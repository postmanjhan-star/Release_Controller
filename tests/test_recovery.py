"""Work in flight must survive the thing that was watching it.

Four ways a release used to get permanently stuck:

* a deployment started from the UI was only advanced by the browser polling
  /refresh, so closing the tab froze it at DEPLOYING -- the workflow timeout
  could not even fire, because it is only evaluated during a refresh;
* a publish interrupted between components stayed PUBLISHING for ever;
* a schedule whose runner died after claiming it stayed RUNNING for ever;
* a bundle that ended PARTIAL_FAILURE was terminal, with one component already in
  production and no way to finish or undo it.
"""

from datetime import timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from app.db.models.orchestration import Deployment, PublishRecord, ReleaseBundle, WorkflowEvent
from app.db.models.scheduling import DeploymentSchedule
from app.db.session import create_database_engine
from app.domain.orchestration.value_objects import (
    ActorSource,
    BundleStatus,
    DeploymentStatus,
    PublishRecordStatus,
    PublishStatus,
    WorkflowEventType,
)
from app.domain.scheduling.value_objects import ScheduleStatus
from app.domain.shared.time import utc_now
from app.integrations.drone.exceptions import DronePromoteError
from app.integrations.drone.schemas import DroneBuild
from app.schemas.drone import PromoteRequest
from app.schemas.orchestration import BundlePromoteRequest
from app.services.drone_build_service import DroneBuildService
from app.services.recovery_service import PUBLISH_INTERRUPTED_CODE, RecoveryService
from app.services.release_orchestrator import BundleNotRetryableError, ReleaseOrchestrator
from tests.conftest import seed_default_project

BACKEND_REPO = "soda"
FRONTEND_REPO = "SMT-Assistant"


def make_build(number: int, status: str = "success", **extra) -> DroneBuild:
    payload = {
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
    payload.update(extra)
    return DroneBuild.from_drone(payload)


class ScriptedDrone:
    def __init__(self) -> None:
        self.promotes: list[tuple[str, int, str]] = []
        self.builds: dict[tuple[str, int], DroneBuild] = {}
        self.fail_promote_for: set[str] = set()
        self._next = 900

    def get_build(self, owner: str, repo: str, number: int) -> DroneBuild:
        return self.builds.get((repo, number)) or make_build(number)

    def promote_build(self, owner: str, repo: str, number: int, target: str) -> DroneBuild:
        self.promotes.append((repo, number, target))
        if repo in self.fail_promote_for:
            raise DronePromoteError("Drone could not create the promotion build")
        self._next += 1
        return make_build(self._next, "running", event="promote", parent=number, target=target)

    def list_builds(self, owner: str, repo: str, limit: int = 20) -> list[DroneBuild]:
        return [make_build(1)]

    def get_repository(self, owner: str, repo: str) -> dict:
        return {"name": repo, "namespace": owner, "default_branch": "main"}


@pytest.fixture
def rig(tmp_path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'recovery.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with session_factory() as setup:
        seed_default_project(setup)
    settings = get_settings()
    drone = ScriptedDrone()

    def orchestrator_for(db):
        return ReleaseOrchestrator(
            db,
            DroneBuildService(drone, settings),
            settings,
            actor=ActorSource.SYSTEM.value,
            actor_source=ActorSource.SYSTEM.value,
        )

    def session_orchestrator(actor: str = "alice"):
        db = session_factory()
        return db, ReleaseOrchestrator(
            db,
            DroneBuildService(drone, settings),
            settings,
            actor=actor,
            actor_source=ActorSource.SESSION.value,
        )

    recovery = RecoveryService(session_factory, orchestrator_for, settings)
    yield drone, session_orchestrator, session_factory, recovery
    engine.dispose()


def age(session_factory, model, identifier: str, **fields: int) -> None:
    """Backdate timestamps so a sweep considers the row quiet enough to act on.

    Written as a single UPDATE because updated_at carries onupdate=utc_now: a
    second ORM edit in the same session would silently undo the backdating.
    """
    with session_factory() as db:
        db.execute(
            update(model)
            .where(model.id == identifier)
            .values(
                **{
                    field: utc_now() - timedelta(seconds=seconds)
                    for field, seconds in fields.items()
                }
            )
            .execution_options(synchronize_session=False)
        )
        db.commit()


def event_types(session_factory, deployment_id: str | None = None) -> list[str]:
    with session_factory() as db:
        query = db.query(WorkflowEvent)
        if deployment_id:
            query = query.filter(WorkflowEvent.deployment_id == deployment_id)
        return [event.event_type for event in query.order_by(WorkflowEvent.id).all()]


# --------------------------------------------------------------------------
# G3: the server drives deployments nobody is watching
# --------------------------------------------------------------------------


def test_a_deployment_nobody_is_polling_still_reaches_success(rig):
    """The browser tab is closed; the deployment must still finish."""
    drone, session_orchestrator, session_factory, recovery = rig
    db, orch = session_orchestrator()
    deployment_id = orch.promote_standalone(
        orch.registry.for_key("backend"), 5, PromoteRequest()
    ).id
    db.close()

    drone.builds[(BACKEND_REPO, 901)] = make_build(901, "success")
    age(session_factory, Deployment, deployment_id, updated_at=60)

    assert recovery.resume_deployments() == 1

    with session_factory() as db:
        assert db.get(Deployment, deployment_id).status == DeploymentStatus.SUCCESS.value


def test_a_bundle_nobody_is_polling_still_advances_to_the_frontend(rig):
    drone, session_orchestrator, session_factory, recovery = rig
    db, orch = session_orchestrator()
    orch.create_bundle(BundlePromoteRequest(frontend_build_number=11, backend_build_number=22))
    db.close()

    drone.builds[(BACKEND_REPO, 901)] = make_build(901, "success")
    with session_factory() as db:
        for deployment in db.query(Deployment).all():
            deployment.updated_at = utc_now() - timedelta(seconds=60)
        db.commit()

    recovery.resume_deployments()

    assert [p for p in drone.promotes if p[0] == FRONTEND_REPO] == [
        (FRONTEND_REPO, 11, "production")
    ]


def test_a_bundle_is_advanced_once_not_once_per_component(rig):
    """Two deployments in one bundle must not mean two refreshes."""
    drone, session_orchestrator, session_factory, recovery = rig
    db, orch = session_orchestrator()
    orch.create_bundle(BundlePromoteRequest(frontend_build_number=11, backend_build_number=22))
    db.close()

    with session_factory() as db:
        for deployment in db.query(Deployment).all():
            deployment.updated_at = utc_now() - timedelta(seconds=60)
        db.commit()

    assert recovery.resume_deployments() == 1


def test_recently_updated_deployments_are_left_to_their_current_poller(rig):
    """A UI already polling keeps ownership; Drone is not asked twice a second."""
    _, session_orchestrator, session_factory, recovery = rig
    db, orch = session_orchestrator()
    orch.promote_standalone(orch.registry.for_key("backend"), 6, PromoteRequest())
    db.close()

    assert recovery.resume_deployments() == 0


def test_a_stalled_deployment_eventually_times_out_without_any_poller(rig):
    """The workflow timeout could not fire at all when nothing was refreshing."""
    drone, session_orchestrator, session_factory, recovery = rig
    db, orch = session_orchestrator()
    deployment_id = orch.promote_standalone(
        orch.registry.for_key("backend"), 7, PromoteRequest()
    ).id
    db.close()

    drone.builds[(BACKEND_REPO, 901)] = make_build(901, "running")
    age(
        session_factory,
        Deployment,
        deployment_id,
        updated_at=60,
        started_at=get_settings().deployment_timeout_seconds + 60,
    )

    recovery.resume_deployments()

    with session_factory() as db:
        deployment = db.get(Deployment, deployment_id)
        assert deployment.status == DeploymentStatus.FAILED.value
        assert deployment.error_code == "WORKFLOW_TIMEOUT"


# --------------------------------------------------------------------------
# T3: an interrupted publish is resolved from its records, never republished
# --------------------------------------------------------------------------


def _stranded_publish(session_factory, bundle_id: str, version: str, published: int) -> None:
    with session_factory() as db:
        bundle = db.get(ReleaseBundle, bundle_id)
        bundle.publish_status = PublishStatus.PUBLISHING.value
        bundle.version = version
        bundle.publish_started_at = utc_now() - timedelta(seconds=3600)
        for index, deployment in enumerate(bundle.deployments):
            deployment.version = version
            db.add(
                PublishRecord(
                    release_bundle_id=bundle.id,
                    deployment_id=deployment.id,
                    component=deployment.component,
                    version=version,
                    repo_owner="102573",
                    repo_name=deployment.drone_repository,
                    commit_sha=deployment.commit_sha,
                    tag_name=version,
                    status=(
                        PublishRecordStatus.PUBLISHED.value
                        if index < published
                        else PublishRecordStatus.PUBLISHING.value
                    ),
                )
            )
        db.commit()


def _succeeded_bundle(drone, session_orchestrator, session_factory) -> str:
    db, orch = session_orchestrator()
    bundle_id = orch.create_bundle(
        BundlePromoteRequest(frontend_build_number=1, backend_build_number=2)
    ).id
    db.close()
    drone.builds[(BACKEND_REPO, 901)] = make_build(901, "success")
    db, orch = session_orchestrator()
    orch.refresh_bundle(bundle_id)
    db.close()
    drone.builds[(FRONTEND_REPO, 902)] = make_build(902, "success")
    db, orch = session_orchestrator()
    orch.refresh_bundle(bundle_id)
    db.close()
    return bundle_id


def test_a_publish_interrupted_after_every_component_is_recorded_as_published(rig):
    drone, session_orchestrator, session_factory, recovery = rig
    bundle_id = _succeeded_bundle(drone, session_orchestrator, session_factory)
    _stranded_publish(session_factory, bundle_id, "v1.0.0", published=2)

    assert recovery.reconcile_publishes() == 1

    with session_factory() as db:
        bundle = db.get(ReleaseBundle, bundle_id)
        assert bundle.publish_status == PublishStatus.PUBLISHED.value
        assert bundle.publish_error_code is None
    assert WorkflowEventType.PUBLISH_RECONCILED.value in event_types(session_factory)


def test_a_publish_interrupted_midway_is_recorded_as_partial(rig):
    drone, session_orchestrator, session_factory, recovery = rig
    bundle_id = _succeeded_bundle(drone, session_orchestrator, session_factory)
    _stranded_publish(session_factory, bundle_id, "v1.0.0", published=1)

    recovery.reconcile_publishes()

    with session_factory() as db:
        bundle = db.get(ReleaseBundle, bundle_id)
        assert bundle.publish_status == PublishStatus.PARTIAL_FAILURE.value
        assert bundle.publish_error_code == PUBLISH_INTERRUPTED_CODE


def test_a_publish_interrupted_before_anything_landed_is_recorded_as_failed(rig):
    drone, session_orchestrator, session_factory, recovery = rig
    bundle_id = _succeeded_bundle(drone, session_orchestrator, session_factory)
    _stranded_publish(session_factory, bundle_id, "v1.0.0", published=0)

    recovery.reconcile_publishes()

    with session_factory() as db:
        assert db.get(ReleaseBundle, bundle_id).publish_status == PublishStatus.FAILED.value


def test_a_publish_still_within_its_timeout_is_left_alone(rig):
    drone, session_orchestrator, session_factory, recovery = rig
    bundle_id = _succeeded_bundle(drone, session_orchestrator, session_factory)
    _stranded_publish(session_factory, bundle_id, "v1.0.0", published=1)
    with session_factory() as db:
        db.get(ReleaseBundle, bundle_id).publish_started_at = utc_now()
        db.commit()

    assert recovery.reconcile_publishes() == 0


# --------------------------------------------------------------------------
# F1: a schedule whose runner died is released, not left RUNNING for ever
# --------------------------------------------------------------------------


def test_a_schedule_stuck_running_is_failed_with_a_pointer_to_check_drone(rig):
    _, _, session_factory, recovery = rig
    with session_factory() as db:
        schedule = DeploymentSchedule(
            mode="BUNDLE",
            frontend_build_number=1,
            backend_build_number=2,
            target="production",
            scheduled_for_utc=utc_now() - timedelta(hours=2),
            timezone="Asia/Taipei",
            status=ScheduleStatus.RUNNING.value,
            started_at=utc_now() - timedelta(hours=2),
        )
        db.add(schedule)
        db.commit()
        schedule_id = schedule.id

    assert recovery.release_stale_schedule_claims() == 1

    with session_factory() as db:
        schedule = db.get(DeploymentSchedule, schedule_id)
        assert schedule.status == ScheduleStatus.FAILED.value
        assert "check the deployments list" in schedule.error_message


def test_a_schedule_that_did_produce_a_deployment_is_not_touched(rig):
    """It got far enough to record the link, so it is not stranded."""
    drone, session_orchestrator, session_factory, recovery = rig
    db, orch = session_orchestrator()
    real_bundle_id = orch.create_bundle(
        BundlePromoteRequest(frontend_build_number=1, backend_build_number=2)
    ).id
    db.close()
    with session_factory() as db:
        schedule = DeploymentSchedule(
            mode="BUNDLE",
            frontend_build_number=1,
            backend_build_number=2,
            target="production",
            scheduled_for_utc=utc_now() - timedelta(hours=2),
            timezone="Asia/Taipei",
            status=ScheduleStatus.RUNNING.value,
            started_at=utc_now() - timedelta(hours=2),
            release_bundle_id=real_bundle_id,
        )
        db.add(schedule)
        db.commit()

    assert recovery.release_stale_schedule_claims() == 0


def test_a_recently_claimed_schedule_is_left_running(rig):
    _, _, session_factory, recovery = rig
    with session_factory() as db:
        db.add(
            DeploymentSchedule(
                mode="BUNDLE",
                frontend_build_number=1,
                backend_build_number=2,
                target="production",
                scheduled_for_utc=utc_now(),
                timezone="Asia/Taipei",
                status=ScheduleStatus.RUNNING.value,
                started_at=utc_now(),
            )
        )
        db.commit()

    assert recovery.release_stale_schedule_claims() == 0


# --------------------------------------------------------------------------
# G4: a partly-failed bundle can be driven forward
# --------------------------------------------------------------------------


def _partially_failed_bundle(drone, session_orchestrator, session_factory) -> str:
    """Backend reaches production, the frontend promote is rejected."""
    db, orch = session_orchestrator()
    bundle_id = orch.create_bundle(
        BundlePromoteRequest(frontend_build_number=1, backend_build_number=2)
    ).id
    db.close()
    drone.builds[(BACKEND_REPO, 901)] = make_build(901, "success")
    drone.fail_promote_for.add(FRONTEND_REPO)
    db, orch = session_orchestrator()
    orch.refresh_bundle(bundle_id)
    db.close()
    with session_factory() as db:
        assert db.get(ReleaseBundle, bundle_id).status == BundleStatus.PARTIAL_FAILURE.value
    return bundle_id


def test_a_partly_failed_bundle_can_be_retried_and_only_reruns_what_failed(rig):
    drone, session_orchestrator, session_factory, _ = rig
    bundle_id = _partially_failed_bundle(drone, session_orchestrator, session_factory)
    backend_promotes = len([p for p in drone.promotes if p[0] == BACKEND_REPO])

    drone.fail_promote_for.clear()
    db, orch = session_orchestrator()
    retried = orch.retry_bundle(bundle_id)
    db.close()

    assert len([p for p in drone.promotes if p[0] == BACKEND_REPO]) == backend_promotes, (
        "the component that already succeeded must not be promoted again"
    )
    assert len([p for p in drone.promotes if p[0] == FRONTEND_REPO]) == 2

    by_component = {d.component: d for d in retried.deployments}
    assert by_component["backend"].status == DeploymentStatus.SUCCESS.value
    assert by_component["frontend"].status == DeploymentStatus.DEPLOYING.value
    assert retried.error_code is None


def test_retry_keeps_one_deployment_per_component_and_records_the_attempt(rig):
    drone, session_orchestrator, session_factory, _ = rig
    bundle_id = _partially_failed_bundle(drone, session_orchestrator, session_factory)
    drone.fail_promote_for.clear()

    db, orch = session_orchestrator()
    orch.retry_bundle(bundle_id)
    db.close()

    with session_factory() as db:
        deployments = db.query(Deployment).filter(Deployment.release_bundle_id == bundle_id).all()
        assert len(deployments) == 2, "retry must reset in place, not add a third row"
    assert WorkflowEventType.DEPLOYMENT_RETRIED.value in event_types(session_factory)


def test_a_healthy_bundle_cannot_be_retried(rig):
    drone, session_orchestrator, session_factory, _ = rig
    bundle_id = _succeeded_bundle(drone, session_orchestrator, session_factory)

    db, orch = session_orchestrator()
    with pytest.raises(BundleNotRetryableError):
        orch.retry_bundle(bundle_id)
    db.close()


def test_a_published_bundle_cannot_be_redeployed(rig):
    """Retrying would change what an already-published version refers to."""
    drone, session_orchestrator, session_factory, _ = rig
    bundle_id = _partially_failed_bundle(drone, session_orchestrator, session_factory)
    with session_factory() as db:
        bundle = db.get(ReleaseBundle, bundle_id)
        bundle.publish_status = PublishStatus.PUBLISHED.value
        db.commit()

    db, orch = session_orchestrator()
    with pytest.raises(BundleNotRetryableError, match="already been published"):
        orch.retry_bundle(bundle_id)
    db.close()


# --------------------------------------------------------------------------
# C4: a failed publish no longer pins the version that failed
# --------------------------------------------------------------------------


def test_a_failed_publish_can_be_retried_under_a_new_version(rig):
    from app.services.publish_orchestrator import PublishOrchestrator

    assert (
        PublishOrchestrator._assert_version("v1.0.0", PublishStatus.FAILED.value, "v1.0.1") is None
    )


def test_a_publish_in_flight_still_pins_its_version(rig):
    from app.services.publish_orchestrator import PublishOrchestrator, PublishStateError

    for pinned in (
        PublishStatus.PUBLISHING.value,
        PublishStatus.PUBLISHED.value,
        PublishStatus.PARTIAL_FAILURE.value,
    ):
        with pytest.raises(PublishStateError):
            PublishOrchestrator._assert_version("v1.0.0", pinned, "v1.0.1")


# --------------------------------------------------------------------------
# Wiring: the worker and startup path actually run the sweeps
# --------------------------------------------------------------------------


def test_the_background_worker_resumes_deployments_on_tick(rig, monkeypatch):
    """RecoveryService working in isolation is no use if the worker never calls it."""
    from app.services.background_worker import BackgroundWorker

    drone, session_orchestrator, session_factory, _ = rig
    db, orch = session_orchestrator()
    deployment_id = orch.promote_standalone(
        orch.registry.for_key("backend"), 31, PromoteRequest()
    ).id
    db.close()

    drone.builds[(BACKEND_REPO, 901)] = make_build(901, "success")
    age(session_factory, Deployment, deployment_id, updated_at=60)

    settings = get_settings()
    worker = BackgroundWorker(
        session_factory, lambda _db: DroneBuildService(drone, settings), settings
    )
    monkeypatch.setattr(worker.schedule_runner, "run_due", lambda: 0)
    worker.tick()

    with session_factory() as db:
        assert db.get(Deployment, deployment_id).status == DeploymentStatus.SUCCESS.value


def test_startup_reconciliation_picks_up_work_stranded_by_a_restart(rig):
    from app.services.background_worker import BackgroundWorker

    drone, session_orchestrator, session_factory, _ = rig
    db, orch = session_orchestrator()
    deployment_id = orch.promote_standalone(
        orch.registry.for_key("backend"), 32, PromoteRequest()
    ).id
    db.close()

    drone.builds[(BACKEND_REPO, 901)] = make_build(901, "success")
    age(session_factory, Deployment, deployment_id, updated_at=60)

    settings = get_settings()
    worker = BackgroundWorker(
        session_factory, lambda _db: DroneBuildService(drone, settings), settings
    )
    summary = worker.reconcile_on_startup()

    assert summary["deployments"] == 1
    with session_factory() as db:
        assert db.get(Deployment, deployment_id).status == DeploymentStatus.SUCCESS.value
