"""Tests for v3.0 step 2: deployments scoped to the registry, guard rekeyed.

The point of the rekey is that the guard now describes the repository that gets
deployed rather than the name we gave it, so the tests that matter most here are
the ones about two components -- or two projects -- pointing at one repository.
"""

from datetime import datetime, timezone

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from alembic import command
from app.core.config import get_settings
from app.db.models.orchestration import Deployment, PublishRecord
from app.db.models.registry import Project, ProjectComponent
from app.domain.orchestration.value_objects import Component
from app.integrations.gitea.schemas import GiteaRelease, GiteaTag
from app.schemas.publish import PublishRequest
from app.services.publish_service import PublishConfigurationError, PublishService
from tests.conftest import registered_component

COMMIT = "a" * 40


# --------------------------------------------------------------------------- #
# deployments carry their registry scope
# --------------------------------------------------------------------------- #


def _promote(client: TestClient, component: str, build: int) -> dict:
    response = client.post(
        f"/api/v1/projects/default/components/{component}/builds/{build}/promote", json={}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_a_promotion_records_the_component_and_connection_it_used(
    client: TestClient, monkeypatch
) -> None:
    from app.infrastructure.di.injection import get_drone_client, get_drone_clients
    from app.services.upstream_clients import FixedDroneClients
    from tests.test_release_worker_v2_2 import FakeDroneClient

    fake = FakeDroneClient()
    client.app.dependency_overrides[get_drone_client] = lambda: fake
    client.app.dependency_overrides[get_drone_clients] = lambda: FixedDroneClients(fake)

    created = _promote(client, "backend", 456)

    with client.session_factory() as db:
        deployment = db.get(Deployment, created["id"])
        component = registered_component(db, "backend")
        assert deployment.component_id == component.id
        assert deployment.project_id == component.project_id
        # Part of the promotion slot, so it has to be recorded, not just resolved.
        assert deployment.drone_connection_id == component.project.drone_connection_id
        assert deployment.drone_repository == component.drone_repo


def test_the_repository_comes_from_the_component_not_the_environment(
    client: TestClient, monkeypatch
) -> None:
    """Editing a component moves where the next promotion goes."""
    from app.infrastructure.di.injection import get_drone_client, get_drone_clients
    from app.services.upstream_clients import FixedDroneClients
    from tests.test_release_worker_v2_2 import FakeDroneClient

    fake = FakeDroneClient()
    client.app.dependency_overrides[get_drone_client] = lambda: fake
    client.app.dependency_overrides[get_drone_clients] = lambda: FixedDroneClients(fake)

    # Point the backend component at the repository the frontend one uses: if the
    # repository still came from DRONE_BACKEND_REPO_NAME this would not move.
    with client.session_factory() as db:
        component = registered_component(db, "backend")
        component.drone_repo = "SMT-Assistant"
        db.commit()

    created = _promote(client, "backend", 123)

    assert created["drone_repository"] == "SMT-Assistant"
    assert ("102573", "SMT-Assistant", 123, "production") in fake.promotions


# --------------------------------------------------------------------------- #
# the rekeyed promotion guard
# --------------------------------------------------------------------------- #


def _raw_deployment(db, component: ProjectComponent, *, status: str, build: int) -> Deployment:
    return Deployment(
        project_id=component.project_id,
        component_id=component.id,
        component=component.key,
        drone_connection_id=component.project.drone_connection_id,
        drone_owner=component.drone_owner,
        drone_repository=component.drone_repo,
        source_build_number=build,
        commit_sha=COMMIT,
        branch="main",
        target="production",
        status=status,
    )


def test_two_projects_sharing_a_repository_cannot_both_promote_one_build(
    client: TestClient,
) -> None:
    """The reason the guard is keyed on the repository and not on component_id.

    Keyed by component these two rows would look like different promotions, and
    the same Drone build would be sent to production twice.
    """
    with client.session_factory() as db:
        original = registered_component(db, "backend")
        other = Project(
            key="other",
            name="Another project",
            default_target="production",
            drone_connection_id=original.project.drone_connection_id,
            gitea_connection_id=original.project.gitea_connection_id,
        )
        other.components.append(
            ProjectComponent(
                key="api",
                display_name="API",
                position=1,
                drone_owner=original.drone_owner,
                drone_repo=original.drone_repo,
                gitea_owner=original.gitea_owner,
                gitea_repo=original.gitea_repo,
            )
        )
        db.add(other)
        db.commit()

        db.add(_raw_deployment(db, original, status="DEPLOYING", build=77))
        db.commit()

        db.add(_raw_deployment(db, other.components[0], status="WAITING", build=77))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_one_repository_promoted_into_two_targets_is_two_slots(client: TestClient) -> None:
    """The monorepo case: same repo, same build, different promote targets."""
    with client.session_factory() as db:
        component = registered_component(db, "backend")
        first = _raw_deployment(db, component, status="DEPLOYING", build=88)
        second = _raw_deployment(db, component, status="DEPLOYING", build=88)
        second.target = "production-web"
        db.add_all([first, second])
        db.commit()

        assert (
            db.scalar(select(Deployment).where(Deployment.source_build_number == 88).limit(1))
            is not None
        )


def test_the_connection_column_cannot_be_left_empty(client: TestClient) -> None:
    """A NULL there would silently disable the guard: SQLite treats NULLs in a
    unique index as distinct, so two NULL-connection rows would never collide."""
    with client.session_factory() as db:
        component = registered_component(db, "backend")
        deployment = _raw_deployment(db, component, status="WAITING", build=99)
        deployment.drone_connection_id = None
        db.add(deployment)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_the_duplicate_pre_check_matches_the_repository_not_the_component(
    client: TestClient,
) -> None:
    """assert_not_duplicate has to agree with the index, or it hands out a 500
    where it meant to hand out a friendly 409."""
    from app.services.deployment_service import DeploymentService, DuplicatePromotionError
    from app.services.drone_build_service import DroneBuildService

    with client.session_factory() as db:
        component = registered_component(db, "backend")
        db.add(_raw_deployment(db, component, status="DEPLOYING", build=101))
        db.commit()

        other = ProjectComponent(
            key="api",
            display_name="API",
            position=3,
            drone_owner=component.drone_owner,
            drone_repo=component.drone_repo,
            gitea_owner=component.gitea_owner,
            gitea_repo=component.gitea_repo,
            promote_target_override="production",
        )
        component.project.components.append(other)
        db.commit()

        service = DeploymentService(db, DroneBuildService(object(), get_settings()), get_settings())
        with pytest.raises(DuplicatePromotionError):
            service.assert_not_duplicate(other, 101, "production")


# --------------------------------------------------------------------------- #
# publishing reads the component
# --------------------------------------------------------------------------- #


class StubGitea:
    def __init__(self) -> None:
        self.created: list[tuple[str, str, str]] = []
        self.tags: dict[str, str] = {}

    def get_repository(self, owner: str, repo: str) -> dict:
        return {"full_name": f"{owner}/{repo}"}

    def get_tag(self, owner: str, repo: str, tag: str) -> GiteaTag:
        from app.integrations.gitea.exceptions import GiteaNotFoundError

        if tag not in self.tags:
            raise GiteaNotFoundError("no such tag")
        return GiteaTag(name=tag, commit_sha=self.tags[tag])

    def get_release_by_tag(self, owner: str, repo: str, tag: str) -> GiteaRelease:
        return GiteaRelease(
            id=1, name=tag, tag_name=tag, html_url=f"http://gitea.test/{owner}/{repo}/{tag}"
        )

    def create_release(self, owner, repo, tag, sha, name, notes, draft, prerelease) -> GiteaRelease:
        self.created.append((owner, repo, tag))
        self.tags[tag] = sha
        return GiteaRelease(
            id=1, name=tag, tag_name=tag, html_url=f"http://gitea.test/{owner}/{repo}/{tag}"
        )


def test_tag_prefix_keeps_two_components_off_each_others_tags(client: TestClient) -> None:
    """Two components in one Gitea repository cannot both own "v1.2.3"."""
    gitea = StubGitea()
    with client.session_factory() as db:
        component = registered_component(db, "backend")
        component.tag_prefix = "be-"
        deployment = _raw_deployment(db, component, status="SUCCESS", build=201)
        db.add(deployment)
        db.commit()

        service = PublishService(db, gitea, get_settings())
        record = service.publish_component(
            deployment, PublishRequest(version="v1.2.3", name="v1.2.3")
        )

        assert record.tag_name == "be-v1.2.3"
        assert gitea.created == [(component.gitea_owner, component.gitea_repo, "be-v1.2.3")]
        # The version stays the version; only the tag is namespaced.
        assert record.version == "v1.2.3"


def test_a_component_that_does_not_publish_is_refused_rather_than_mistagged(
    client: TestClient,
) -> None:
    with client.session_factory() as db:
        component = registered_component(db, "backend")
        component.publish_enabled = False
        deployment = _raw_deployment(db, component, status="SUCCESS", build=202)
        db.add(deployment)
        db.commit()

        service = PublishService(db, StubGitea(), get_settings())
        with pytest.raises(PublishConfigurationError):
            service.publish_component(deployment, PublishRequest(version="v1.2.3", name="v1"))


def test_publish_records_carry_their_component(client: TestClient) -> None:
    gitea = StubGitea()
    with client.session_factory() as db:
        component = registered_component(db, "frontend")
        deployment = _raw_deployment(db, component, status="SUCCESS", build=203)
        db.add(deployment)
        db.commit()

        PublishService(db, gitea, get_settings()).publish_component(
            deployment, PublishRequest(version="v2.0.0", name="v2")
        )
        record = db.scalar(
            select(PublishRecord).where(PublishRecord.deployment_id == deployment.id)
        )

        assert record.component_id == component.id
        assert record.project_id == component.project_id


# --------------------------------------------------------------------------- #
# migration 0015
# --------------------------------------------------------------------------- #


def _v2_row(number: int, component: str, repo: str, status: str = "SUCCESS") -> dict:
    now = datetime.now(timezone.utc)
    return {
        "id": f"dep-{number}",
        "component": component,
        "drone_owner": "102573",
        "drone_repository": repo,
        "source_build_number": number,
        "commit_sha": COMMIT,
        "branch": "main",
        "target": "production",
        "status": status,
        "publish_status": "NOT_PUBLISHED",
        "poll_failure_count": 0,
        "created_at": now,
        "updated_at": now,
    }


def _upgrade_to(revision: str, database_url: str, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("APP_SECRET_KEY", "migration-test-key")
    monkeypatch.setenv("GITEA_BACKEND_REPO_OWNER", "102573")
    monkeypatch.setenv("GITEA_BACKEND_REPO_NAME", "soda")
    monkeypatch.setenv("GITEA_FRONTEND_REPO_OWNER", "102573")
    monkeypatch.setenv("GITEA_FRONTEND_REPO_NAME", "SMT-Assistant")
    get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), revision)


INSERT_DEPLOYMENT = text(
    """
    INSERT INTO deployments (
        id, component, drone_owner, drone_repository, source_build_number,
        commit_sha, branch, target, status, publish_status, poll_failure_count,
        created_at, updated_at
    ) VALUES (
        :id, :component, :drone_owner, :drone_repository, :source_build_number,
        :commit_sha, :branch, :target, :status, :publish_status, :poll_failure_count,
        :created_at, :updated_at
    )
    """
)


def test_migration_0015_backfills_existing_deployments(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{(tmp_path / 'scope.db').as_posix()}"
    try:
        _upgrade_to("20260902_0014", database_url, monkeypatch)
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(
                INSERT_DEPLOYMENT,
                [
                    _v2_row(456, "backend", "soda"),
                    _v2_row(123, "frontend", "SMT-Assistant"),
                ],
            )

        _upgrade_to("head", database_url, monkeypatch)

        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT d.component, d.project_id, d.component_id, d.drone_connection_id, "
                    "c.key AS component_key, p.key AS project_key "
                    "FROM deployments d "
                    "JOIN project_components c ON c.id = d.component_id "
                    "JOIN projects p ON p.id = d.project_id "
                    "ORDER BY d.component"
                )
            ).all()
            broken = connection.execute(text("PRAGMA foreign_key_check")).all()

        assert [(row.component, row.component_key, row.project_key) for row in rows] == [
            ("backend", "backend", "default"),
            ("frontend", "frontend", "default"),
        ]
        assert all(row.drone_connection_id for row in rows)
        # Rebuilding deployments must not leave anything pointing at nothing.
        assert broken == []
        engine.dispose()
    finally:
        get_settings.cache_clear()


def test_migration_0015_keeps_the_guard_and_the_append_only_triggers(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{(tmp_path / 'guard.db').as_posix()}"
    try:
        _upgrade_to("head", database_url, monkeypatch)
        engine = create_engine(database_url)
        indexes = {index["name"]: index for index in inspect(engine).get_indexes("deployments")}
        with engine.connect() as connection:
            triggers = (
                connection.execute(text("SELECT name FROM sqlite_master WHERE type = 'trigger'"))
                .scalars()
                .all()
            )

        guard = indexes["uq_deployments_active_promotion"]
        assert bool(guard["unique"]) is True
        assert guard["column_names"] == [
            "drone_connection_id",
            "drone_owner",
            "drone_repository",
            "source_build_number",
            "target",
        ]
        # The rebuild must not have dropped the ordinary indexes either.
        assert {
            "ix_deployments_status",
            "ix_deployments_component",
            "ix_deployments_project_id",
        } <= (set(indexes))
        # workflow_events is a different table, but a batch rebuild in the same
        # migration is exactly how these get lost.
        assert {"workflow_events_block_update", "workflow_events_block_delete"} <= set(triggers)
        engine.dispose()
    finally:
        get_settings.cache_clear()


def test_migration_0015_refuses_a_component_the_registry_does_not_have(
    tmp_path, monkeypatch
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'unmapped.db').as_posix()}"
    try:
        _upgrade_to("20260902_0014", database_url, monkeypatch)
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(INSERT_DEPLOYMENT, [_v2_row(1, "gateway", "gateway")])
        engine.dispose()

        with pytest.raises(RuntimeError, match="gateway"):
            _upgrade_to("head", database_url, monkeypatch)
    finally:
        get_settings.cache_clear()


def test_migration_0015_refuses_existing_duplicates_under_the_new_key(
    tmp_path, monkeypatch
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'dupes.db').as_posix()}"
    try:
        _upgrade_to("20260902_0014", database_url, monkeypatch)
        engine = create_engine(database_url)
        with engine.begin() as connection:
            # Two components, one repository, one build, one target.  The old key
            # includes the component name so it lets this through; the new key is
            # about the repository, and this is exactly the double deployment it
            # exists to make impossible.
            connection.execute(
                INSERT_DEPLOYMENT,
                [
                    _v2_row(456, "backend", "soda", status="DEPLOYING"),
                    {**_v2_row(456, "frontend", "soda"), "id": "dep-456b"},
                ],
            )
        engine.dispose()

        with pytest.raises(RuntimeError, match="more than one active promotion"):
            _upgrade_to("head", database_url, monkeypatch)
    finally:
        get_settings.cache_clear()


def test_migration_0015_refuses_to_downgrade_once_a_second_project_exists(
    tmp_path, monkeypatch
) -> None:
    """The old guard cannot tell two projects' components apart, so going back
    would report the second project's deployments as duplicates of the first's."""
    database_url = f"sqlite:///{(tmp_path / 'oneway.db').as_posix()}"
    try:
        _upgrade_to("head", database_url, monkeypatch)
        engine = create_engine(database_url)
        session = sessionmaker(bind=engine)()
        session.add(Project(key="second", name="Second", default_target="production"))
        session.commit()
        session.close()
        engine.dispose()

        with pytest.raises(RuntimeError, match="Refusing to downgrade"):
            command.downgrade(Config("alembic.ini"), "20260902_0014")
    finally:
        get_settings.cache_clear()


def test_migration_0015_downgrades_while_one_project_exists(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{(tmp_path / 'back.db').as_posix()}"
    try:
        _upgrade_to("head", database_url, monkeypatch)
        command.downgrade(Config("alembic.ini"), "20260902_0014")
        engine = create_engine(database_url)
        columns = {c["name"] for c in inspect(engine).get_columns("deployments")}
        indexes = {index["name"]: index for index in inspect(engine).get_indexes("deployments")}

        assert not {"project_id", "component_id", "drone_connection_id"} & columns
        assert indexes["uq_deployments_active_promotion"]["column_names"] == [
            "component",
            "source_build_number",
            "target",
        ]
        engine.dispose()
    finally:
        get_settings.cache_clear()


def test_legacy_component_names_keep_their_stage_names(client: TestClient) -> None:
    """Existing timelines and the BPMN diagrams still line up; a component that is
    neither frontend nor backend gets the generic stage instead."""
    from app.domain.orchestration.value_objects import WorkflowStage
    from app.services.deployment_service import promote_stage, wait_stage

    assert promote_stage(Component.BACKEND.value) is WorkflowStage.PROMOTE_BACKEND
    assert wait_stage(Component.FRONTEND.value) is WorkflowStage.WAIT_FRONTEND_DEPLOYMENT
    assert promote_stage("gateway") is WorkflowStage.PROMOTE_COMPONENT
    assert wait_stage("gateway") is WorkflowStage.WAIT_COMPONENT_DEPLOYMENT


def test_a_component_with_deployment_history_cannot_be_deleted(client: TestClient) -> None:
    """Deleting it would leave real deployments pointing at nothing."""
    with client.session_factory() as db:
        component = registered_component(db, "backend")
        db.add(_raw_deployment(db, component, status="SUCCESS", build=301))
        db.commit()
        project_id, component_id = component.project_id, component.id

    response = client.delete(f"/api/v1/projects/{project_id}/components/{component_id}")

    assert response.status_code == 409
    assert "Set it inactive instead" in response.json()["detail"]

    # Deactivating is the operation that stays available.
    assert (
        client.patch(
            f"/api/v1/projects/{project_id}/components/{component_id}",
            json={"is_active": False},
        ).status_code
        == 200
    )


def test_a_component_that_never_deployed_can_still_be_deleted(client: TestClient) -> None:
    response = client.post(
        "/api/v1/projects/default/components",
        json={
            "key": "gateway",
            "drone_owner": "102573",
            "drone_repo": "gateway",
            "publish_enabled": False,
        },
    )
    assert response.status_code == 201

    assert client.delete("/api/v1/projects/default/components/gateway").status_code == 204
