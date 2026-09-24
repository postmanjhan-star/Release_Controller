"""The audit trail must answer "who did what, when" and survive deletion.

README states the project exists to record "誰在何時核准並提交哪一個版本".  These
tests hold the schema to that claim:

* deleting a release used to erase every event about it (ON DELETE CASCADE), so
  the evidence could be removed by the thing it was evidence about;
* no column recorded the operator, and publishing recorded none at all;
* "append-only" was a docstring, not a constraint;
* events tie-broke on a random uuid4, so same-instant events displayed in an
  arbitrary order.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.models.orchestration import (
    Deployment,
    PublishRecord,
    ReleaseBundle,
    WorkflowEvent,
    event_id,
)
from app.db.session import create_database_engine
from app.domain.orchestration.value_objects import ActorSource, Component
from app.integrations.drone.schemas import DroneBuild
from app.schemas.drone import PromoteRequest
from app.schemas.orchestration import BundlePromoteRequest
from app.services.drone_build_service import DroneBuildService
from app.services.release_orchestrator import ReleaseOrchestrator
from tests.conftest import seed_default_project


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


class StubDrone:
    def __init__(self) -> None:
        self.promotes: list[tuple[str, int, str]] = []

    def get_build(self, owner: str, repo: str, number: int) -> DroneBuild:
        return make_build(number)

    def promote_build(self, owner: str, repo: str, number: int, target: str) -> DroneBuild:
        self.promotes.append((repo, number, target))
        return make_build(900 + number, "running", event="promote", parent=number, target=target)

    def list_builds(self, owner: str, repo: str, limit: int = 20) -> list[DroneBuild]:
        return [make_build(1)]

    def get_repository(self, owner: str, repo: str) -> dict:
        return {"name": repo, "namespace": owner, "default_branch": "main"}


@pytest.fixture
def audit(tmp_path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'audit.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with session_factory() as setup:
        seed_default_project(setup)
    settings = get_settings()
    drone = StubDrone()

    def orchestrator(actor: str | None = None, source: str = ActorSource.SESSION.value):
        db = session_factory()
        return db, ReleaseOrchestrator(
            db,
            DroneBuildService(drone, settings),
            settings,
            actor=actor,
            actor_source=source,
        )

    yield drone, orchestrator, session_factory
    engine.dispose()


# --------------------------------------------------------------------------
# C5: audit rows outlive what they describe
# --------------------------------------------------------------------------


def test_deleting_a_release_leaves_its_audit_trail_intact(audit):
    _, orchestrator, session_factory = audit
    db, orch = orchestrator(actor="alice")
    bundle = orch.create_bundle(
        BundlePromoteRequest(frontend_build_number=1, backend_build_number=2)
    )
    release_id = bundle.id
    db.close()

    with session_factory() as db:
        before = db.query(WorkflowEvent).count()
        assert before > 0

    with session_factory() as db:
        db.delete(db.get(ReleaseBundle, release_id))
        db.commit()

    with session_factory() as db:
        surviving = db.query(WorkflowEvent).all()
        assert len(surviving) == before, "audit rows must not be deleted with the release"
        # The linkage survives too: the id still names the release that is gone.
        assert all(event.release_bundle_id == release_id for event in surviving)
        # And each row still reads on its own.
        assert {event.target for event in surviving} == {"production"}
        assert db.get(ReleaseBundle, release_id) is None


def test_deleting_a_deployment_leaves_its_audit_trail_intact(audit):
    _, orchestrator, session_factory = audit
    db, orch = orchestrator(actor="bob")
    deployment_id = orch.promote_standalone(
        orch.registry.for_key("backend"), 5, PromoteRequest()
    ).id
    db.close()

    with session_factory() as db:
        before = db.query(WorkflowEvent).count()
        db.delete(db.get(Deployment, deployment_id))
        db.commit()

    with session_factory() as db:
        surviving = db.query(WorkflowEvent).all()
        assert len(surviving) == before
        assert all(event.deployment_id == deployment_id for event in surviving)
        assert {event.component for event in surviving} == {"backend"}


def test_workflow_events_has_no_foreign_keys(audit):
    """A foreign key here is what armed the cascade; there must not be one."""
    _, _, session_factory = audit
    with session_factory() as db:
        schema = db.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='workflow_events'")
        ).scalar_one()
    assert "FOREIGN KEY" not in schema.upper()


# --------------------------------------------------------------------------
# C6: every event names an actor; publishing names one too
# --------------------------------------------------------------------------


def test_every_event_records_who_caused_it(audit):
    _, orchestrator, session_factory = audit
    db, orch = orchestrator(actor="alice")
    orch.promote_standalone(orch.registry.for_key("backend"), 7, PromoteRequest())
    db.close()

    with session_factory() as db:
        events = db.query(WorkflowEvent).all()
        assert events
        assert {event.actor for event in events} == {"alice"}
        assert {event.actor_source for event in events} == {ActorSource.SESSION.value}


def test_a_scheduled_run_is_attributed_to_whoever_scheduled_it(audit):
    _, orchestrator, session_factory = audit
    db, orch = orchestrator(actor="carol", source=ActorSource.SCHEDULE.value)
    orch.promote_standalone(orch.registry.for_key("backend"), 8, PromoteRequest())
    db.close()

    with session_factory() as db:
        events = db.query(WorkflowEvent).all()
        assert {event.actor for event in events} == {"carol"}
        assert {event.actor_source for event in events} == {ActorSource.SCHEDULE.value}


def test_an_unattributed_caller_is_recorded_as_system_not_left_blank(audit):
    """Unknown must be stated, not implied by a null."""
    _, orchestrator, session_factory = audit
    db, orch = orchestrator(actor=None, source=ActorSource.SYSTEM.value)
    orch.promote_standalone(orch.registry.for_key("backend"), 9, PromoteRequest())
    db.close()

    with session_factory() as db:
        events = db.query(WorkflowEvent).all()
        assert {event.actor for event in events} == {"system"}


def test_publish_records_carry_the_operator(audit):
    _, orchestrator, session_factory = audit
    db, orch = orchestrator(actor="dave")
    deployment_id = orch.promote_standalone(
        orch.registry.for_key("backend"), 21, PromoteRequest()
    ).id
    db.close()

    with session_factory() as db:
        db.add(
            PublishRecord(
                deployment_id=deployment_id,
                component=Component.BACKEND.value,
                version="v1.8.0",
                repo_owner="102573",
                repo_name="soda",
                commit_sha="a" * 40,
                tag_name="v1.8.0",
                status="PUBLISHED",
                requested_by="dave",
            )
        )
        db.commit()

    with session_factory() as db:
        record = db.query(PublishRecord).one()
        assert record.requested_by == "dave", "who published a version must be answerable"


# --------------------------------------------------------------------------
# U1: append-only is enforced, not asserted
# --------------------------------------------------------------------------


def test_an_event_cannot_be_edited(audit):
    _, orchestrator, session_factory = audit
    db, orch = orchestrator(actor="alice")
    orch.promote_standalone(orch.registry.for_key("backend"), 11, PromoteRequest())
    db.close()

    with session_factory() as db:
        # SQLite surfaces RAISE(ABORT) as an integrity failure.
        with pytest.raises(IntegrityError, match="append-only"):
            db.execute(text("UPDATE workflow_events SET message = 'tampered'"))
        db.rollback()

    with session_factory() as db:
        assert db.query(WorkflowEvent).filter(WorkflowEvent.message == "tampered").count() == 0


def test_an_event_cannot_be_deleted(audit):
    _, orchestrator, session_factory = audit
    db, orch = orchestrator(actor="alice")
    orch.promote_standalone(orch.registry.for_key("backend"), 12, PromoteRequest())
    db.close()

    with session_factory() as db:
        before = db.query(WorkflowEvent).count()
        with pytest.raises(IntegrityError, match="append-only"):
            db.execute(text("DELETE FROM workflow_events"))
        db.rollback()

    with session_factory() as db:
        assert db.query(WorkflowEvent).count() == before


# --------------------------------------------------------------------------
# U2: the timeline orders correctly, including within one instant
# --------------------------------------------------------------------------


def test_event_ids_sort_chronologically():
    """promote() writes three events at once; (created_at, id) must order them."""
    ids = [event_id() for _ in range(200)]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


def test_same_instant_events_keep_their_written_order(audit):
    _, orchestrator, session_factory = audit
    db, orch = orchestrator(actor="alice")
    orch.promote_standalone(orch.registry.for_key("backend"), 13, PromoteRequest())
    db.close()

    with session_factory() as db:
        written = [
            event.event_type
            for event in db.query(WorkflowEvent).order_by(WorkflowEvent.id.asc()).all()
        ]
        ordered = [
            event.event_type
            for event in db.query(WorkflowEvent)
            .order_by(WorkflowEvent.created_at.asc(), WorkflowEvent.id.asc())
            .all()
        ]
    assert written == ordered
    # The promote sequence is the case that used to shuffle.
    assert "PROMOTION_CREATED" in written
    assert written.index("STAGE_STARTED") < written.index("PROMOTION_CREATED")


# --------------------------------------------------------------------------
# U3: production refuses to run with a forgeable operator
# --------------------------------------------------------------------------


def test_production_refuses_to_start_with_authentication_disabled():
    with pytest.raises(ValueError, match="AUTH_ENABLED"):
        Settings(app_env="production", auth_enabled=False)


def test_production_starts_normally_with_authentication_enabled():
    assert Settings(app_env="production", auth_enabled=True).auth_enabled is True


def test_non_production_may_disable_authentication_for_local_work():
    assert Settings(app_env="test", auth_enabled=False).auth_enabled is False
