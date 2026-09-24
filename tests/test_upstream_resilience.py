"""Upstream failures must not be mistaken for deployment failures.

The rule these tests encode: reaching Drone is not evidence about the deployment.
Only Drone's own verdict on the promotion build, or the workflow timeout, may fail
one.  Everything else -- a timeout, a dropped connection, a 502 from a proxy --
leaves the deployment alone and is retried.

Before this change set, one timed-out poll permanently failed a deployment that
was in fact succeeding, and a timed-out promote failed the deployment while Drone
went on running the build, so the operator's retry produced a second production
deployment.
"""

import threading

import pytest
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from app.db.models.orchestration import Deployment, WorkflowEvent
from app.db.session import create_database_engine
from app.domain.orchestration.value_objects import DeploymentStatus, WorkflowEventType
from app.integrations.drone.exceptions import (
    DroneAuthenticationError,
    DroneConnectionError,
    DroneTimeoutError,
)
from app.integrations.drone.schemas import DroneBuild
from app.integrations.retry import RetryPolicy, retry_read
from app.schemas.drone import PromoteRequest
from app.services.deployment_service import PROMOTION_UNCONFIRMED_CODE
from app.services.drone_build_service import DroneBuildService
from app.services.release_orchestrator import ReleaseOrchestrator
from tests.conftest import seed_default_project

BACKEND_REPO = "soda"


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
    """Drone stand-in whose failures are scripted per call."""

    def __init__(self) -> None:
        self.promotes: list[tuple[str, int, str]] = []
        self.get_build_error: Exception | None = None
        self.promote_error: Exception | None = None
        self.list_builds_error: Exception | None = None
        self.builds: dict[tuple[str, int], DroneBuild] = {}
        self.history: list[DroneBuild] = []
        self.get_build_calls = 0

    def get_build(self, owner: str, repo: str, number: int) -> DroneBuild:
        self.get_build_calls += 1
        if self.get_build_error:
            raise self.get_build_error
        return self.builds.get((repo, number)) or make_build(number)

    def promote_build(self, owner: str, repo: str, number: int, target: str) -> DroneBuild:
        self.promotes.append((repo, number, target))
        if self.promote_error:
            raise self.promote_error
        return make_build(901, "running", event="promote", parent=number, target=target)

    def list_builds(self, owner: str, repo: str, limit: int = 20) -> list[DroneBuild]:
        if self.list_builds_error:
            raise self.list_builds_error
        return self.history or [make_build(1)]

    def get_repository(self, owner: str, repo: str) -> dict:
        return {"name": repo, "namespace": owner, "default_branch": "main"}


@pytest.fixture
def rig(tmp_path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'resilience.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with session_factory() as setup:
        seed_default_project(setup)
    settings = get_settings()
    drone = ScriptedDrone()

    def orchestrator():
        db = session_factory()
        return db, ReleaseOrchestrator(db, DroneBuildService(drone, settings), settings)

    yield drone, orchestrator, session_factory
    engine.dispose()


def events_for(session_factory, deployment_id: str) -> list[str]:
    with session_factory() as db:
        return [
            event.event_type
            for event in db.query(WorkflowEvent)
            .filter(WorkflowEvent.deployment_id == deployment_id)
            .order_by(WorkflowEvent.created_at)
            .all()
        ]


# --------------------------------------------------------------------------
# P1: a poll that cannot reach Drone must not fail a running deployment
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        DroneTimeoutError("Drone request timed out"),
        DroneConnectionError("Drone is unavailable"),
        DroneAuthenticationError("Drone authentication failed"),
    ],
)
def test_transient_poll_failure_keeps_the_deployment_running(rig, error):
    drone, orchestrator, session_factory = rig
    db, orch = orchestrator()
    deployment = orch.promote_standalone(orch.registry.for_key("backend"), 5, PromoteRequest())
    deployment_id = deployment.id
    assert deployment.status == DeploymentStatus.DEPLOYING.value
    db.close()

    drone.get_build_error = error
    db, orch = orchestrator()
    refreshed = orch.refresh_standalone(deployment_id)
    db.close()

    assert refreshed.status == DeploymentStatus.DEPLOYING.value
    assert refreshed.error_code is None
    assert refreshed.poll_error_code == error.error_code
    assert refreshed.poll_failure_count == 1
    assert WorkflowEventType.UPSTREAM_UNAVAILABLE.value in events_for(
        session_factory, deployment_id
    )


def test_deployment_recovers_once_drone_answers_again(rig):
    drone, orchestrator, session_factory = rig
    db, orch = orchestrator()
    deployment_id = orch.promote_standalone(
        orch.registry.for_key("backend"), 6, PromoteRequest()
    ).id
    db.close()

    drone.get_build_error = DroneTimeoutError("Drone request timed out")
    for _ in range(3):
        db, orch = orchestrator()
        orch.refresh_standalone(deployment_id)
        db.close()

    with session_factory() as check:
        stalled = check.get(Deployment, deployment_id)
        assert stalled.status == DeploymentStatus.DEPLOYING.value
        assert stalled.poll_failure_count == 3

    # Drone comes back and reports the promotion build succeeded.
    drone.get_build_error = None
    drone.builds[(BACKEND_REPO, 901)] = make_build(901, "success")
    db, orch = orchestrator()
    recovered = orch.refresh_standalone(deployment_id)
    db.close()

    assert recovered.status == DeploymentStatus.SUCCESS.value
    assert recovered.poll_error_code is None
    assert recovered.poll_failure_count == 0
    assert WorkflowEventType.UPSTREAM_RECOVERED.value in events_for(session_factory, deployment_id)


def test_repeated_identical_poll_failures_do_not_flood_the_timeline(rig):
    drone, orchestrator, session_factory = rig
    db, orch = orchestrator()
    deployment_id = orch.promote_standalone(
        orch.registry.for_key("backend"), 8, PromoteRequest()
    ).id
    db.close()

    drone.get_build_error = DroneTimeoutError("Drone request timed out")
    for _ in range(5):
        db, orch = orchestrator()
        orch.refresh_standalone(deployment_id)
        db.close()

    unavailable = [
        event
        for event in events_for(session_factory, deployment_id)
        if event == WorkflowEventType.UPSTREAM_UNAVAILABLE.value
    ]
    assert len(unavailable) == 1


def test_drone_reporting_a_failed_build_still_fails_the_deployment(rig):
    """The one thing that may fail a deployment is Drone's own verdict."""
    drone, orchestrator, _ = rig
    db, orch = orchestrator()
    deployment_id = orch.promote_standalone(
        orch.registry.for_key("backend"), 9, PromoteRequest()
    ).id
    db.close()

    drone.builds[(BACKEND_REPO, 901)] = make_build(901, "failure")
    db, orch = orchestrator()
    failed = orch.refresh_standalone(deployment_id)
    db.close()

    assert failed.status == DeploymentStatus.FAILED.value
    assert failed.error_code == "DRONE_BUILD_FAILED"


def test_workflow_timeout_still_fails_a_stalled_deployment(rig, monkeypatch):
    """Patience is bounded: the timeout is the backstop that replaces failing fast."""
    drone, orchestrator, _ = rig
    db, orch = orchestrator()
    deployment_id = orch.promote_standalone(
        orch.registry.for_key("backend"), 10, PromoteRequest()
    ).id
    db.close()

    drone.get_build_error = DroneTimeoutError("Drone request timed out")
    monkeypatch.setattr(
        "app.services.deployment_service.DeploymentService._timed_out", lambda self, d: True
    )
    db, orch = orchestrator()
    timed_out = orch.refresh_standalone(deployment_id)
    db.close()

    assert timed_out.status == DeploymentStatus.FAILED.value
    assert timed_out.error_code == "WORKFLOW_TIMEOUT"


# --------------------------------------------------------------------------
# P2: a promote whose outcome is unknown must be reconciled, never repeated
# --------------------------------------------------------------------------


def test_promote_timeout_leaves_the_deployment_unconfirmed_not_failed(rig):
    drone, orchestrator, session_factory = rig
    drone.promote_error = DroneTimeoutError("Drone request timed out")

    db, orch = orchestrator()
    deployment = orch.promote_standalone(orch.registry.for_key("backend"), 11, PromoteRequest())
    db.close()

    assert deployment.status == DeploymentStatus.PROMOTING.value
    assert deployment.error_code is None
    assert deployment.poll_error_code == PROMOTION_UNCONFIRMED_CODE
    assert deployment.promotion_build_number is None
    assert WorkflowEventType.PROMOTION_UNCONFIRMED.value in events_for(
        session_factory, deployment.id
    )


def test_unconfirmed_promotion_is_adopted_instead_of_promoting_again(rig):
    """Drone did create the build; reconciliation must find it, not make another."""
    drone, orchestrator, session_factory = rig
    drone.promote_error = DroneTimeoutError("Drone request timed out")

    db, orch = orchestrator()
    deployment_id = orch.promote_standalone(
        orch.registry.for_key("backend"), 12, PromoteRequest()
    ).id
    db.close()
    assert len(drone.promotes) == 1

    # The promote actually landed: the build exists in Drone's history.
    drone.promote_error = None
    drone.history = [
        make_build(950, "running", event="promote", parent=12, target="production"),
        make_build(12),
    ]
    drone.builds[(BACKEND_REPO, 950)] = make_build(950, "running", event="promote", parent=12)

    db, orch = orchestrator()
    reconciled = orch.refresh_standalone(deployment_id)
    db.close()

    assert len(drone.promotes) == 1, "reconciliation must never send a second promote"
    assert reconciled.promotion_build_number == 950
    assert reconciled.status == DeploymentStatus.DEPLOYING.value
    assert reconciled.poll_error_code is None
    assert WorkflowEventType.PROMOTION_RECONCILED.value in events_for(
        session_factory, deployment_id
    )


def test_unconfirmed_promotion_stays_unconfirmed_when_no_build_is_found(rig):
    drone, orchestrator, _ = rig
    drone.promote_error = DroneConnectionError("Drone is unavailable")

    db, orch = orchestrator()
    deployment_id = orch.promote_standalone(
        orch.registry.for_key("backend"), 13, PromoteRequest()
    ).id
    db.close()

    drone.promote_error = None
    drone.history = [make_build(13)]  # no promotion build for build 13

    db, orch = orchestrator()
    still_unknown = orch.refresh_standalone(deployment_id)
    db.close()

    assert len(drone.promotes) == 1
    assert still_unknown.status == DeploymentStatus.PROMOTING.value
    assert still_unknown.poll_error_code == PROMOTION_UNCONFIRMED_CODE
    assert still_unknown.promotion_build_number is None


def test_unconfirmed_promotion_keeps_the_duplicate_slot_taken(rig):
    """The slot must stay occupied, or a retry would deploy to production twice."""
    from app.services.deployment_service import DuplicatePromotionError

    drone, orchestrator, _ = rig
    drone.promote_error = DroneTimeoutError("Drone request timed out")

    db, orch = orchestrator()
    orch.promote_standalone(orch.registry.for_key("backend"), 14, PromoteRequest())
    db.close()

    db, orch = orchestrator()
    with pytest.raises(DuplicatePromotionError):
        orch.promote_standalone(orch.registry.for_key("backend"), 14, PromoteRequest())
    db.close()

    assert len(drone.promotes) == 1


def test_a_rejected_promote_fails_immediately(rig):
    """A 401 means the request never ran, so there is nothing to reconcile."""
    drone, orchestrator, _ = rig
    drone.promote_error = DroneAuthenticationError("Drone authentication failed")

    db, orch = orchestrator()
    deployment = orch.promote_standalone(orch.registry.for_key("backend"), 15, PromoteRequest())
    db.close()

    assert deployment.status == DeploymentStatus.FAILED.value
    assert deployment.error_code == "DRONE_AUTH_FAILED"


# --------------------------------------------------------------------------
# R1/R2: reads are retried with jitter, writes never are
# --------------------------------------------------------------------------


def test_retry_read_gives_up_with_the_original_error():
    attempts = []

    def always_times_out():
        attempts.append(1)
        raise DroneTimeoutError("Drone request timed out")

    with pytest.raises(DroneTimeoutError):
        retry_read(
            always_times_out,
            policy=RetryPolicy(attempts=3, base_seconds=0, max_seconds=0),
            retry_on=(DroneTimeoutError,),
            description="test",
            sleep=lambda _: None,
        )
    assert len(attempts) == 3


def test_retry_read_returns_as_soon_as_a_call_succeeds():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise DroneConnectionError("Drone is unavailable")
        return "ok"

    result = retry_read(
        flaky,
        policy=RetryPolicy(attempts=5, base_seconds=0, max_seconds=0),
        retry_on=(DroneConnectionError,),
        description="test",
        sleep=lambda _: None,
    )
    assert result == "ok"
    assert len(calls) == 3


def test_retry_read_does_not_retry_unlisted_errors():
    calls = []

    def unauthorised():
        calls.append(1)
        raise DroneAuthenticationError("Drone authentication failed")

    with pytest.raises(DroneAuthenticationError):
        retry_read(
            unauthorised,
            policy=RetryPolicy(attempts=5, base_seconds=0, max_seconds=0),
            retry_on=(DroneTimeoutError, DroneConnectionError),
            description="test",
            sleep=lambda _: None,
        )
    assert len(calls) == 1


def test_backoff_is_jittered_so_pollers_do_not_retry_in_lockstep():
    policy = RetryPolicy(attempts=4, base_seconds=1.0, max_seconds=8.0)
    samples = {round(policy.delay_for(2), 6) for _ in range(50)}
    assert len(samples) > 1, "delays must not be identical across callers"
    assert all(0 <= sample <= 4.0 for sample in samples)


def test_promote_is_never_retried_by_the_client(monkeypatch):
    """A retried promote is a second production deployment."""
    import httpx

    from app.integrations.drone.client import DroneClient

    calls: list[str] = []

    def always_times_out(method: str, url: str, **kwargs):
        calls.append(method)
        raise httpx.ConnectTimeout("boom")

    monkeypatch.setattr(httpx, "request", always_times_out)
    client = DroneClient("http://drone.test", "token", timeout=1)

    with pytest.raises(DroneTimeoutError):
        client.promote_build("team", "api", 5, "production")
    assert calls == ["POST"], "promote must be attempted exactly once"

    calls.clear()
    with pytest.raises(DroneTimeoutError):
        client.get_build("team", "api", 5)
    assert len(calls) == 3, "reads are retried"


def test_concurrent_pollers_do_not_corrupt_poll_state(rig):
    """Several refreshes landing together must not double-count or crash."""
    drone, orchestrator, session_factory = rig
    db, orch = orchestrator()
    deployment_id = orch.promote_standalone(
        orch.registry.for_key("backend"), 16, PromoteRequest()
    ).id
    db.close()

    drone.get_build_error = DroneTimeoutError("Drone request timed out")
    barrier = threading.Barrier(3)
    errors: list[Exception] = []

    def poll() -> None:
        db, orch = orchestrator()
        try:
            barrier.wait(timeout=10)
            orch.refresh_standalone(deployment_id)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            db.close()

    threads = [threading.Thread(target=poll) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    with session_factory() as check:
        deployment = check.get(Deployment, deployment_id)
        assert deployment.status == DeploymentStatus.DEPLOYING.value
        assert deployment.poll_failure_count >= 1
