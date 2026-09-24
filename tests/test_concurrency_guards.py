"""Concurrency guards for the promotion path.

Every test here failed before the 20260831_0010 change set.  They exist so that
a future refactor cannot quietly reintroduce any of the three faults:

* a Drone call made while a SQLite write transaction is open, which turned a slow
  upstream into "database is locked" for every other request;
* a duplicate promotion slipping between assert_not_duplicate's SELECT and the
  INSERT;
* two callers both moving a WAITING deployment to PROMOTING and both reaching
  Drone, leaving one promotion build untracked.

The v1 approval race at the bottom was added with the Phase 2 refactor: the
`WHERE status = ...` on an approval has always been a compare-and-swap as well
as a business rule, and nothing proved it.

They use real threads against a file-backed SQLite database because none of these
faults reproduce on a single connection.
"""

import threading
import time

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from app.db.models.orchestration import Deployment, ReleaseBundle
from app.db.session import create_database_engine
from app.domain.orchestration.value_objects import Component, DeploymentStatus
from app.domain.release.exceptions import InvalidReleaseTransitionError
from app.domain.release.value_objects import ReleaseStatus
from app.infrastructure.sqlite.release.release_repository import SqlAlchemyReleaseRepository
from app.infrastructure.sqlite.unit_of_work import SqlAlchemyUnitOfWork
from app.integrations.drone.schemas import DroneBuild
from app.schemas.drone import PromoteRequest
from app.schemas.orchestration import BundlePromoteRequest
from app.services.deployment_service import DuplicatePromotionError
from app.services.drone_build_service import DroneBuildService
from app.services.release_orchestrator import ReleaseOrchestrator
from app.services.workflow_service import WorkflowService
from app.usecase.release.approve_release_usecase import new_approve_release_usecase
from app.usecase.release.create_release_usecase import new_create_release_usecase
from tests.conftest import registered_component, seed_default_project

BACKEND_REPO = "soda"
FRONTEND_REPO = "SMT-Assistant"


def make_build(number: int, status: str = "success") -> DroneBuild:
    return DroneBuild.from_drone(
        {
            "number": number,
            "status": status,
            "event": "push",
            "after": f"{number:040x}",
            "target": "",
            "source": "main",
            "message": "commit message",
            "author_login": "operator",
            "created": 0,
            "finished": 0,
        }
    )


class RecordingDrone:
    """Drone stand-in that records promotions and can be made deliberately slow."""

    def __init__(self, promote_latency: float = 0.0) -> None:
        self.promotes: list[tuple[str, int, str]] = []
        self.promote_latency = promote_latency
        self.overrides: dict[tuple[str, int], DroneBuild] = {}
        self._lock = threading.Lock()
        self._next_promotion = 900

    def get_build(self, owner: str, repo: str, number: int) -> DroneBuild:
        return self.overrides.get((repo, number)) or make_build(number)

    def promote_build(self, owner: str, repo: str, number: int, target: str) -> DroneBuild:
        time.sleep(self.promote_latency)
        with self._lock:
            self.promotes.append((repo, number, target))
            self._next_promotion += 1
            promotion_number = self._next_promotion
        return make_build(promotion_number, "running")

    def list_builds(self, owner: str, repo: str, limit: int = 20) -> list[DroneBuild]:
        return [make_build(1)]

    def get_repository(self, owner: str, repo: str) -> dict:
        return {"name": repo, "namespace": owner, "default_branch": "main"}

    def promotions_for(self, repo: str) -> list[tuple[str, int, str]]:
        return [item for item in self.promotes if item[0] == repo]


@pytest.fixture
def orchestration(tmp_path):
    """A real on-disk database plus a session factory usable from several threads."""
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'guards.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with session_factory() as setup:
        seed_default_project(setup)
    settings = get_settings()

    def orchestrator_for(drone: RecordingDrone):
        db = session_factory()
        return db, ReleaseOrchestrator(db, DroneBuildService(drone, settings), settings)

    yield session_factory, orchestrator_for
    engine.dispose()


def run_together(target, count: int = 2) -> list:
    """Run target(barrier) in `count` threads that start at the same instant."""
    barrier = threading.Barrier(count)
    errors: list = []

    def wrapper() -> None:
        try:
            target(barrier)
        except Exception as exc:  # noqa: BLE001 - the test asserts on what was raised
            errors.append(exc)

    threads = [threading.Thread(target=wrapper) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return errors


def test_concurrent_standalone_promote_reaches_drone_once(orchestration):
    """Two operators promoting the same build must produce one production deploy."""
    _, orchestrator_for = orchestration
    drone = RecordingDrone(promote_latency=0.15)

    def promote(barrier: threading.Barrier) -> None:
        db, orchestrator = orchestrator_for(drone)
        try:
            barrier.wait(timeout=10)
            orchestrator.promote_standalone(
                orchestrator.registry.for_key("backend"), 42, PromoteRequest()
            )
        finally:
            db.close()

    errors = run_together(promote)

    assert drone.promotions_for(BACKEND_REPO) == [(BACKEND_REPO, 42, "production")]
    assert [type(error) for error in errors] == [DuplicatePromotionError]


def test_concurrent_bundle_refresh_promotes_frontend_once(orchestration):
    """A UI poll and the background worker landing together must not double-promote."""
    session_factory, orchestrator_for = orchestration
    drone = RecordingDrone(promote_latency=0.15)

    db, orchestrator = orchestrator_for(drone)
    bundle = orchestrator.create_bundle(
        BundlePromoteRequest(frontend_build_number=11, backend_build_number=22)
    )
    release_id = bundle.id
    backend_promotion = drone.promotions_for(BACKEND_REPO)
    db.close()
    assert backend_promotion == [(BACKEND_REPO, 22, "production")]

    # The backend promotion build reports success, so the frontend becomes eligible.
    drone.overrides[(BACKEND_REPO, 901)] = make_build(901, "success")

    def refresh(barrier: threading.Barrier) -> None:
        db, orchestrator = orchestrator_for(drone)
        try:
            barrier.wait(timeout=10)
            orchestrator.refresh_bundle(release_id)
        finally:
            db.close()

    errors = run_together(refresh)
    assert errors == []

    frontend_promotions = drone.promotions_for(FRONTEND_REPO)
    assert frontend_promotions == [(FRONTEND_REPO, 11, "production")]

    # The recorded promotion build must be the one Drone actually created; an
    # unrecorded second promotion is the failure mode this test exists for.
    with session_factory() as db:
        frontend = (
            db.query(Deployment)
            .filter(
                Deployment.release_bundle_id == release_id,
                Deployment.component == Component.FRONTEND.value,
            )
            .one()
        )
        assert frontend.status == DeploymentStatus.DEPLOYING.value
        assert frontend.promotion_build_number == 902


def test_slow_drone_does_not_block_unrelated_writes(orchestration):
    """No SQLite write lock may be held across an upstream call."""
    session_factory, orchestrator_for = orchestration
    drone = RecordingDrone(promote_latency=3.0)
    outcome: dict[str, object] = {}

    def promoter() -> None:
        db, orchestrator = orchestrator_for(drone)
        try:
            orchestrator.promote_standalone(
                orchestrator.registry.for_key("backend"), 7, PromoteRequest()
            )
        finally:
            db.close()

    def unrelated_writer() -> None:
        time.sleep(0.5)  # arrive while the Drone call is in flight
        started = time.monotonic()
        with session_factory() as db:
            try:
                db.add(ReleaseBundle(target="production", status="PENDING"))
                db.commit()
                outcome["elapsed"] = time.monotonic() - started
            except Exception as exc:  # noqa: BLE001
                outcome["error"] = exc

    threads = [threading.Thread(target=promoter), threading.Thread(target=unrelated_writer)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert "error" not in outcome, f"unrelated write failed: {outcome.get('error')}"
    # SQLite's busy_timeout is 5s; anything close to it means the lock was held
    # across the 3s Drone call again.
    assert outcome["elapsed"] < 1.0


def test_active_promotion_slot_is_unique_in_the_database(orchestration):
    """The guarantee is the index, not the application-level pre-check."""
    session_factory, _ = orchestration

    def deployment(db, status: str) -> Deployment:
        component = registered_component(db, Component.BACKEND.value)
        return Deployment(
            project_id=component.project_id,
            component_id=component.id,
            component=component.key,
            drone_connection_id=component.project.drone_connection_id,
            drone_owner="102573",
            drone_repository=BACKEND_REPO,
            source_build_number=55,
            commit_sha="a" * 40,
            branch="main",
            target="production",
            status=status,
        )

    with session_factory() as db:
        db.add(deployment(db, DeploymentStatus.DEPLOYING.value))
        db.commit()

    with session_factory() as db:
        db.add(deployment(db, DeploymentStatus.WAITING.value))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_terminal_deployments_free_the_slot_for_a_retry(orchestration):
    """A failed or cancelled promotion must not block promoting that build again."""
    session_factory, _ = orchestration

    def deployment(db, status: str) -> Deployment:
        component = registered_component(db, Component.FRONTEND.value)
        return Deployment(
            project_id=component.project_id,
            component_id=component.id,
            component=component.key,
            drone_connection_id=component.project.drone_connection_id,
            drone_owner="102573",
            drone_repository=FRONTEND_REPO,
            source_build_number=66,
            commit_sha="b" * 40,
            branch="main",
            target="production",
            status=status,
        )

    with session_factory() as db:
        db.add(deployment(db, DeploymentStatus.FAILED.value))
        db.add(deployment(db, DeploymentStatus.CANCELLED.value))
        db.add(deployment(db, DeploymentStatus.WAITING.value))
        db.commit()
        assert db.query(Deployment).filter(Deployment.source_build_number == 66).count() == 3


def test_promotion_claim_is_won_by_exactly_one_caller(orchestration):
    """claim_for_promotion is the primitive the refresh race relies on."""
    session_factory, orchestrator_for = orchestration
    drone = RecordingDrone()

    db, orchestrator = orchestrator_for(drone)
    build = make_build(77)
    created = orchestrator.deployments.create(
        registered_component(db, Component.BACKEND.value), build, "production"
    )
    deployment_id = created.id
    db.commit()
    db.close()

    results: list[bool] = []
    results_lock = threading.Lock()

    def claim(barrier: threading.Barrier) -> None:
        db, orchestrator = orchestrator_for(drone)
        try:
            deployment = db.get(Deployment, deployment_id)
            barrier.wait(timeout=10)
            won = orchestrator.deployments.claim_for_promotion(deployment)
            with results_lock:
                results.append(won)
        finally:
            db.close()

    errors = run_together(claim, count=3)
    assert errors == []
    assert sorted(results) == [False, False, True]


@pytest.fixture
def approvals(tmp_path):
    """A real on-disk database plus a factory for one request's worth of wiring."""
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'approvals.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def wiring():
        db = session_factory()
        return db, SqlAlchemyReleaseRepository(db), WorkflowService(db), SqlAlchemyUnitOfWork(db)

    yield wiring
    engine.dispose()


def test_release_approval_is_won_by_exactly_one_caller(approvals):
    """Two approvals arriving together must not both succeed.

    The rule lives in the entity now, but an in-memory check cannot see the other
    request: both callers read PENDING and both would pass it.  What separates
    them is that the repository writes with `WHERE status = PENDING`, so exactly
    one UPDATE matches a row.
    """
    db, releases, workflows, uow = approvals()
    release = new_create_release_usecase(releases, workflows, uow).execute(
        repository="release-controller",
        branch="main",
        commit_sha="a" * 40,
        environment="production",
        message=None,
    )
    release_id = release.id
    db.close()

    outcomes: list[bool] = []
    refusals: list[str] = []
    outcomes_lock = threading.Lock()

    def approve(barrier: threading.Barrier) -> None:
        db, releases, workflows, uow = approvals()
        try:
            usecase = new_approve_release_usecase(releases, workflows, uow)
            barrier.wait(timeout=10)
            try:
                usecase.execute(release_id, approved_by="operator")
                won = True
            except InvalidReleaseTransitionError as exc:
                won = False
                with outcomes_lock:
                    refusals.append(str(exc))
            with outcomes_lock:
                outcomes.append(won)
        finally:
            db.close()

    errors = run_together(approve, count=2)

    assert errors == []
    assert sorted(outcomes) == [False, True]
    # 輸的那個要看到資料庫真正的狀態，不是自己讀進來的舊快照。
    assert refusals == ["Release status is APPROVED; expected PENDING"]

    db, releases, _workflows, _uow = approvals()
    try:
        assert releases.find_by_id(release_id).status is ReleaseStatus.APPROVED
    finally:
        db.close()
