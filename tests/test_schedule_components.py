"""Tests for v3.0 step 4: a schedule names components, not a frontend and a backend.

The v2.x schedule tests still pass unchanged -- a schedule created with the old
body still runs, and so does one that was already in the database before the
upgrade.  What is here is the shape the two build-number columns could not hold.
"""

from datetime import datetime, timedelta, timezone

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.core.config import get_settings
from app.db.models.scheduling import DeploymentSchedule, ScheduleComponentBuild
from app.infrastructure.di.injection import get_drone_client, get_drone_clients
from app.services.upstream_clients import FixedDroneClients
from tests.test_n_component_release import ScriptedDrone, add_gateway


@pytest.fixture
def drone(client: TestClient) -> ScriptedDrone:
    scripted = ScriptedDrone()
    client.app.dependency_overrides[get_drone_client] = lambda: scripted
    client.app.dependency_overrides[get_drone_clients] = lambda: FixedDroneClients(scripted)
    return scripted


def due_at() -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()


def schedule_payload(**overrides) -> dict:
    payload = {
        "target": "pre-production",
        "scheduled_for": due_at(),
        "timezone": "Asia/Taipei",
        "requested_by": "release-operator",
        "notification_recipients": [],
    }
    payload.update(overrides)
    return payload


def create_schedule(client: TestClient, **overrides) -> dict:
    response = client.post("/api/v1/schedules", json=schedule_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# creating
# --------------------------------------------------------------------------- #


def test_a_schedule_of_one_component_is_SINGLE(client: TestClient) -> None:
    created = create_schedule(client, components=[{"key": "backend", "build_number": 456}])

    assert created["mode"] == "SINGLE"
    assert created["selected_component_keys"] == "backend"
    assert [(c["component_key"], c["build_number"]) for c in created["component_builds"]] == [
        ("backend", 456)
    ]


def test_a_schedule_of_three_components_is_a_bundle_in_position_order(
    client: TestClient,
) -> None:
    add_gateway(client)

    created = create_schedule(
        client,
        components=[
            {"key": "gateway", "build_number": 3},
            {"key": "backend", "build_number": 456},
            {"key": "frontend", "build_number": 123},
        ],
    )

    assert created["mode"] == "BUNDLE"
    assert created["selected_component_keys"] == "backend,frontend,gateway"


def test_a_component_beyond_the_original_two_can_be_scheduled_alone(
    client: TestClient,
) -> None:
    """The case the two build-number columns had no room for."""
    add_gateway(client)

    created = create_schedule(client, components=[{"key": "gateway", "build_number": 3}])

    assert created["mode"] == "SINGLE"
    # Nothing lands in the legacy columns, because neither of them is a gateway.
    assert created["frontend_build_number"] is None
    assert created["backend_build_number"] is None


def test_the_legacy_columns_are_no_longer_written(client: TestClient) -> None:
    """A new schedule records its selection in one place only.

    The two columns survive as history for schedules written before revision 0016,
    but writing them for a new schedule would create a second, partial answer to
    "what does this release" -- partial because it can only describe frontend and
    backend, which is exactly the limitation v3.0 removed.
    """
    created = create_schedule(
        client,
        components=[
            {"key": "backend", "build_number": 456},
            {"key": "frontend", "build_number": 123},
        ],
    )

    assert created["backend_build_number"] is None
    assert created["frontend_build_number"] is None
    assert [
        (item["component_key"], item["build_number"]) for item in created["component_builds"]
    ] == [("backend", 456), ("frontend", 123)]


def test_the_v2_body_still_creates_a_schedule(client: TestClient) -> None:
    created = create_schedule(client, mode="FRONTEND_ONLY", frontend_build_number=123)

    assert created["mode"] == "FRONTEND_ONLY"
    assert [c["component_key"] for c in created["component_builds"]] == ["frontend"]


def test_a_mode_that_contradicts_the_selection_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/v1/schedules",
        json=schedule_payload(mode="BUNDLE", components=[{"key": "backend", "build_number": 456}]),
    )

    assert response.status_code == 422


def test_a_schedule_of_nothing_is_refused(client: TestClient) -> None:
    response = client.post("/api/v1/schedules", json=schedule_payload())

    assert response.status_code == 422


def test_the_same_component_twice_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/v1/schedules",
        json=schedule_payload(
            components=[
                {"key": "backend", "build_number": 1},
                {"key": "backend", "build_number": 2},
            ]
        ),
    )

    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# running
# --------------------------------------------------------------------------- #


def run_due(client: TestClient) -> int:
    response = client.post("/api/v1/schedules/run-due")
    assert response.status_code == 200, response.text
    return response.json()["processed"]


def test_a_single_component_schedule_promotes_that_component(
    client: TestClient, drone: ScriptedDrone
) -> None:
    add_gateway(client)
    created = create_schedule(client, components=[{"key": "gateway", "build_number": 3}])

    assert run_due(client) == 1

    schedule = client.get(f"/api/v1/schedules/{created['id']}").json()
    assert schedule["status"] == "SUCCEEDED"
    # One component means one deployment, not a bundle.
    assert schedule["deployment_id"] is not None
    assert schedule["release_bundle_id"] is None
    assert drone.promotions == [("gateway", 3, "pre-production")]


def test_a_multi_component_schedule_creates_a_bundle(
    client: TestClient, drone: ScriptedDrone
) -> None:
    add_gateway(client)
    created = create_schedule(
        client,
        components=[
            {"key": "backend", "build_number": 456},
            {"key": "gateway", "build_number": 3},
        ],
    )

    assert run_due(client) == 1

    schedule = client.get(f"/api/v1/schedules/{created['id']}").json()
    assert schedule["status"] == "SUCCEEDED"
    assert schedule["release_bundle_id"] is not None
    assert schedule["deployment_id"] is None
    bundle = client.get(f"/api/v1/releases/{schedule['release_bundle_id']}").json()
    assert bundle["selected_component_keys"] == "backend,gateway"
    # Still one at a time, in position order.
    assert drone.promotions == [("soda", 456, "pre-production")]


def test_a_schedule_written_before_the_upgrade_still_runs(
    client: TestClient, drone: ScriptedDrone
) -> None:
    """Rows created before revision 0016 have no component_builds at all.

    Current code cannot produce such a row -- it writes the child rows and leaves
    the two columns alone -- so the shape is reconstructed here: the selection in
    the legacy columns and nowhere else.
    """
    created = create_schedule(
        client,
        components=[
            {"key": "backend", "build_number": 456},
            {"key": "frontend", "build_number": 123},
        ],
    )
    with client.session_factory() as db:
        db.query(ScheduleComponentBuild).filter(
            ScheduleComponentBuild.schedule_id == created["id"]
        ).delete()
        schedule = db.get(DeploymentSchedule, created["id"])
        schedule.backend_build_number = 456
        schedule.frontend_build_number = 123
        db.commit()

    assert run_due(client) == 1

    schedule = client.get(f"/api/v1/schedules/{created['id']}").json()
    assert schedule["status"] == "SUCCEEDED", schedule["error_message"]
    assert schedule["release_bundle_id"] is not None


def test_a_component_a_schedule_points_at_cannot_be_deleted(client: TestClient) -> None:
    add_gateway(client)
    create_schedule(client, components=[{"key": "gateway", "build_number": 3}])

    response = client.delete("/api/v1/projects/default/components/gateway")

    assert response.status_code == 409
    assert "part of a schedule" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# migration 0016
# --------------------------------------------------------------------------- #


INSERT_SCHEDULE = text(
    """
    INSERT INTO deployment_schedules (
        id, mode, frontend_build_number, backend_build_number, target,
        scheduled_for_utc, timezone, status, created_at, updated_at
    ) VALUES (
        :id, :mode, :frontend, :backend, :target,
        :scheduled_for_utc, 'UTC', :status, :now, :now
    )
    """
)


def _upgrade(revision: str, database_url: str, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("APP_SECRET_KEY", "migration-test-key")
    monkeypatch.setenv("GITEA_BACKEND_REPO_OWNER", "102573")
    monkeypatch.setenv("GITEA_BACKEND_REPO_NAME", "soda")
    monkeypatch.setenv("GITEA_FRONTEND_REPO_OWNER", "102573")
    monkeypatch.setenv("GITEA_FRONTEND_REPO_NAME", "SMT-Assistant")
    get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), revision)


def test_migration_0016_backfills_pending_schedules_only(tmp_path, monkeypatch) -> None:
    """A schedule that has already run gets nothing: its outcome is already
    recorded, and inventing a selection for it would be inventing history."""
    database_url = f"sqlite:///{(tmp_path / 'schedules.db').as_posix()}"
    now = datetime.now(timezone.utc)
    try:
        _upgrade("20260902_0015", database_url, monkeypatch)
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(
                INSERT_SCHEDULE,
                [
                    {
                        "id": "pending-bundle",
                        "mode": "BUNDLE",
                        "frontend": 123,
                        "backend": 456,
                        "target": "production",
                        "scheduled_for_utc": now,
                        "status": "PENDING",
                        "now": now,
                    },
                    {
                        "id": "pending-frontend",
                        "mode": "FRONTEND_ONLY",
                        "frontend": 124,
                        "backend": None,
                        "target": "production",
                        "scheduled_for_utc": now,
                        "status": "PENDING",
                        "now": now,
                    },
                    {
                        "id": "already-run",
                        "mode": "BUNDLE",
                        "frontend": 1,
                        "backend": 2,
                        "target": "production",
                        "scheduled_for_utc": now,
                        "status": "SUCCEEDED",
                        "now": now,
                    },
                ],
            )

        _upgrade("head", database_url, monkeypatch)

        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT schedule_id, component_key, build_number "
                    "FROM schedule_component_builds ORDER BY schedule_id, component_key"
                )
            ).all()
            keys = connection.execute(
                text(
                    "SELECT id, selected_component_keys, project_id FROM deployment_schedules "
                    "ORDER BY id"
                )
            ).all()
            broken = connection.execute(text("PRAGMA foreign_key_check")).all()

        assert [(r.schedule_id, r.component_key, r.build_number) for r in rows] == [
            ("pending-bundle", "backend", 456),
            ("pending-bundle", "frontend", 123),
            ("pending-frontend", "frontend", 124),
        ]
        by_id = {row.id: row for row in keys}
        assert by_id["pending-bundle"].selected_component_keys == "backend,frontend"
        assert by_id["pending-bundle"].project_id is not None
        assert by_id["already-run"].selected_component_keys is None
        assert broken == []
        engine.dispose()
    finally:
        get_settings.cache_clear()


def test_migration_0016_downgrades_cleanly(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{(tmp_path / 'schedules-down.db').as_posix()}"
    try:
        _upgrade("head", database_url, monkeypatch)
        command.downgrade(Config("alembic.ini"), "20260902_0015")
        engine = create_engine(database_url)
        tables = set(inspect(engine).get_table_names())
        columns = {c["name"] for c in inspect(engine).get_columns("deployment_schedules")}

        assert "schedule_component_builds" not in tables
        assert not {"project_id", "selected_component_keys"} & columns
        # The columns it replaces are untouched, so nothing pending is lost.
        assert {"frontend_build_number", "backend_build_number"} <= columns
        engine.dispose()
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("mode", ["SINGLE", "BUNDLE"])
def test_the_new_modes_round_trip_through_the_api(client: TestClient, mode: str) -> None:
    components = [{"key": "backend", "build_number": 456}]
    if mode == "BUNDLE":
        components.append({"key": "frontend", "build_number": 123})

    created = create_schedule(client, components=components)

    assert created["mode"] == mode
    listed = client.get("/api/v1/schedules").json()["items"]
    assert {item["mode"] for item in listed} == {mode}
