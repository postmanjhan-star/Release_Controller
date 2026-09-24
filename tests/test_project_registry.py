"""Tests for the v3.0 project registry (migration 20260902_0014).

The registry is not on the deployment path yet, so what these assert is that the
configuration it stores is safe to hand to the deployment path later: tokens are
unreadable at rest, a component's promotion slot is unique inside its project,
and deployment order is a real, editable sequence.
"""

from datetime import datetime, timezone

import pytest
from alembic.config import Config
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.core.config import get_settings
from app.core.crypto import TokenCipher, TokenDecryptionError
from app.infrastructure.di.injection import (
    get_component_checker,
    get_connection_probe,
    get_resolve_connection_usecase,
)
from app.infrastructure.upstream.component_checker import UpstreamComponentChecker
from app.infrastructure.upstream.connection_probe import UpstreamConnectionProbe
from app.integrations.drone.exceptions import (
    DroneAuthenticationError,
    DroneNotFoundError,
    DroneTimeoutError,
)
from app.integrations.gitea.exceptions import GiteaNotFoundError
from app.usecase.registry.resolve_connection_usecase import ResolveConnectionUseCase

DRONE_CONNECTION = {
    "kind": "drone",
    "name": "primary-drone",
    "base_url": "http://drone.test/",
    "token": "drone-token-abcd",
}
GITEA_CONNECTION = {
    "kind": "gitea",
    "name": "primary-gitea",
    "base_url": "http://gitea.test",
    "token": "gitea-token-wxyz",
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def make_connection(client: TestClient, payload: dict) -> dict:
    response = client.post("/api/v1/connections", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def component_payload(**overrides) -> dict:
    payload = {
        "key": "backend",
        "drone_owner": "102573",
        "drone_repo": "soda",
        "gitea_owner": "102573",
        "gitea_repo": "soda",
    }
    payload.update(overrides)
    return payload


def make_project(client: TestClient, **overrides) -> dict:
    payload = {
        "key": "smt",
        "name": "SMT Assistant",
        "components": [
            component_payload(),
            component_payload(
                key="frontend", drone_repo="SMT-Assistant", gitea_repo="SMT-Assistant"
            ),
        ],
    }
    payload.update(overrides)
    response = client.post("/api/v1/projects", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


class StubDroneClient:
    """Every repository exists."""

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def status(self) -> dict:
        return {"status": "ok"}

    def get_repository(self, owner: str, repo: str) -> dict:
        return {"namespace": owner, "name": repo}


class StubGiteaClient(StubDroneClient):
    def health(self) -> dict:
        return {"status": "pass"}


# --------------------------------------------------------------------------- #
# connections
# --------------------------------------------------------------------------- #


def test_connection_token_is_never_returned_and_is_encrypted_at_rest(
    bare_registry: TestClient,
) -> None:
    client = bare_registry
    body = make_connection(client, DRONE_CONNECTION)

    assert "token" not in body
    assert "token_encrypted" not in body
    assert body["token_hint"] == "…abcd"
    assert DRONE_CONNECTION["token"] not in str(body)
    # The first connection of a kind becomes its default, so a single-Drone
    # installation never has to know the concept exists.
    assert body["is_default"] is True
    # A trailing slash on the server would produce '//api/...' on every call.
    assert body["base_url"] == "http://drone.test"

    stored = client.get(f"/api/v1/connections/{body['id']}")
    assert DRONE_CONNECTION["token"] not in stored.text


def test_stored_token_round_trips_through_the_cipher() -> None:
    cipher = TokenCipher("a-secret")
    ciphertext = cipher.encrypt("drone-token-abcd")

    assert "drone-token-abcd" not in ciphertext
    assert cipher.decrypt(ciphertext) == "drone-token-abcd"


def test_a_token_encrypted_under_another_key_is_reported_not_raised() -> None:
    ciphertext = TokenCipher("the-old-key").encrypt("drone-token-abcd")

    with pytest.raises(TokenDecryptionError):
        TokenCipher("the-new-key").decrypt(ciphertext)


def test_second_connection_of_a_kind_is_not_default_until_asked(
    bare_registry: TestClient,
) -> None:
    client = bare_registry
    first = make_connection(client, DRONE_CONNECTION)
    second = make_connection(client, {**DRONE_CONNECTION, "name": "backup-drone"})

    assert second["is_default"] is False

    promoted = client.patch(f"/api/v1/connections/{second['id']}", json={"is_default": True})
    assert promoted.status_code == 200
    assert promoted.json()["is_default"] is True
    # At most one default per kind: the old one must have been stood down.
    assert client.get(f"/api/v1/connections/{first['id']}").json()["is_default"] is False


def use_probe(client: TestClient, drone_client) -> None:
    """把「連線測試」指向一個假的 Drone。

    覆寫注入的 probe，而不是 monkeypatch 模組屬性：後者綁死在模組路徑上，
    模組一搬家測試就會安靜地不再測到東西。
    """
    client.app.dependency_overrides[get_connection_probe] = lambda: UpstreamConnectionProbe(
        get_settings(), drone_client=drone_client
    )


def test_replacing_a_token_clears_the_previous_verification_result(
    client: TestClient,
) -> None:
    use_probe(client, StubDroneClient)
    connection = make_connection(client, DRONE_CONNECTION)

    assert client.post(f"/api/v1/connections/{connection['id']}/test").json()["status"] == "ok"
    assert client.get(f"/api/v1/connections/{connection['id']}").json()["verify_status"] == "ok"

    updated = client.patch(f"/api/v1/connections/{connection['id']}", json={"token": "new-token"})

    # Reporting "ok" here would be reporting the previous credential's result.
    assert updated.json()["verify_status"] is None
    assert updated.json()["token_hint"] == "…oken"


def test_connection_test_reports_a_rejected_token_as_an_answer_not_an_error(
    client: TestClient,
) -> None:
    def unauthorized(*_args, **_kwargs):
        raise DroneAuthenticationError("Drone rejected the token")

    use_probe(client, unauthorized)
    connection = make_connection(client, DRONE_CONNECTION)

    response = client.post(f"/api/v1/connections/{connection['id']}/test")

    assert response.status_code == 200
    assert response.json()["status"] == "unauthorized"


def test_connection_test_distinguishes_unreachable_from_rejected(
    client: TestClient,
) -> None:
    def timed_out(*_args, **_kwargs):
        raise DroneTimeoutError("Drone did not respond")

    use_probe(client, timed_out)
    connection = make_connection(client, DRONE_CONNECTION)

    assert client.post(f"/api/v1/connections/{connection['id']}/test").json()["status"] == (
        "unreachable"
    )


def test_a_token_from_an_older_secret_key_reports_key_mismatch(client: TestClient) -> None:
    """Rotating APP_SECRET_KEY must not turn every page into a 500."""
    connection = make_connection(client, DRONE_CONNECTION)

    def stale_key(*_args, **_kwargs):
        raise TokenDecryptionError("cannot decrypt")

    use_probe(client, stale_key)

    response = client.post(f"/api/v1/connections/{connection['id']}/test")

    assert response.status_code == 200
    assert response.json()["status"] == "key_mismatch"


def test_a_connection_a_project_still_uses_cannot_be_deleted(client: TestClient) -> None:
    drone = make_connection(client, DRONE_CONNECTION)
    make_connection(client, GITEA_CONNECTION)
    make_project(client, drone_connection_id=drone["id"])

    response = client.delete(f"/api/v1/connections/{drone['id']}")

    assert response.status_code == 409
    assert "still uses this connection" in response.json()["detail"]


def test_duplicate_connection_name_is_rejected(client: TestClient) -> None:
    make_connection(client, DRONE_CONNECTION)

    response = client.post("/api/v1/connections", json=DRONE_CONNECTION)

    assert response.status_code == 409


# --------------------------------------------------------------------------- #
# projects and components
# --------------------------------------------------------------------------- #


def test_project_is_created_with_its_components_in_deployment_order(client: TestClient) -> None:
    project = make_project(client)

    assert [c["key"] for c in project["components"]] == ["backend", "frontend"]
    assert [c["position"] for c in project["components"]] == [1, 2]
    # Nothing overrides the target, so both inherit the project's.
    assert {c["effective_target"] for c in project["components"]} == {"production"}
    assert project["components"][0]["display_name"] == "Backend"


def test_project_can_be_fetched_by_key_as_well_as_id(client: TestClient) -> None:
    project = make_project(client)

    assert client.get(f"/api/v1/projects/{project['key']}").json()["id"] == project["id"]


def test_duplicate_project_key_is_rejected(client: TestClient) -> None:
    make_project(client)

    response = client.post("/api/v1/projects", json={"key": "smt", "name": "Another"})

    assert response.status_code == 409


def test_project_key_must_be_a_slug(client: TestClient) -> None:
    response = client.post("/api/v1/projects", json={"key": "Not A Slug", "name": "x"})

    assert response.status_code == 422


def test_a_single_repository_may_be_one_component(client: TestClient) -> None:
    """The monorepo case: one build, one promotion, one tag."""
    project = make_project(
        client,
        key="mono",
        components=[component_payload(key="app", drone_repo="mono", gitea_repo="mono")],
    )

    assert len(project["components"]) == 1
    assert project["components"][0]["effective_target"] == "production"


def test_two_components_sharing_a_repository_without_distinct_targets_are_refused(
    client: TestClient,
) -> None:
    """The collision the duplicate-promotion index would otherwise surface much later."""
    response = client.post(
        "/api/v1/projects",
        json={
            "key": "mono",
            "name": "Monorepo",
            "components": [
                component_payload(key="backend", drone_repo="mono", gitea_repo="mono"),
                component_payload(key="frontend", drone_repo="mono", gitea_repo="mono"),
            ],
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "backend, frontend" in detail
    assert "promote_target_override" in detail


def test_two_components_sharing_a_repository_are_allowed_with_distinct_targets(
    client: TestClient,
) -> None:
    project = make_project(
        client,
        key="mono",
        components=[
            component_payload(
                key="backend",
                drone_repo="mono",
                gitea_repo="mono",
                promote_target_override="production-api",
                tag_prefix="be-",
            ),
            component_payload(
                key="frontend",
                drone_repo="mono",
                gitea_repo="mono",
                promote_target_override="production-web",
                tag_prefix="fe-",
            ),
        ],
    )

    targets = {c["key"]: c["effective_target"] for c in project["components"]}
    assert targets == {"backend": "production-api", "frontend": "production-web"}
    assert {c["tag_prefix"] for c in project["components"]} == {"be-", "fe-"}


def test_removing_an_override_that_would_collide_is_refused(client: TestClient) -> None:
    project = make_project(
        client,
        key="mono",
        components=[
            component_payload(key="backend", drone_repo="mono", gitea_repo="mono"),
            component_payload(
                key="frontend",
                drone_repo="mono",
                gitea_repo="mono",
                promote_target_override="production-web",
            ),
        ],
    )

    response = client.patch(
        f"/api/v1/projects/{project['id']}/components/frontend",
        json={"clear_promote_target_override": True},
    )

    assert response.status_code == 422
    assert "promote_target_override" in response.json()["detail"]


def test_changing_the_project_default_target_rechecks_the_slots(client: TestClient) -> None:
    """default_target moves every component that inherits it, so it can collide too."""
    project = make_project(
        client,
        key="mono",
        components=[
            component_payload(key="backend", drone_repo="mono", gitea_repo="mono"),
            component_payload(
                key="frontend",
                drone_repo="mono",
                gitea_repo="mono",
                promote_target_override="staging",
            ),
        ],
    )

    response = client.patch(f"/api/v1/projects/{project['id']}", json={"default_target": "staging"})

    assert response.status_code == 422


def test_inactive_components_do_not_occupy_a_slot(client: TestClient) -> None:
    project = make_project(
        client,
        key="mono",
        components=[
            component_payload(key="backend", drone_repo="mono", gitea_repo="mono"),
            component_payload(
                key="old-backend", drone_repo="mono", gitea_repo="mono", is_active=False
            ),
        ],
    )

    assert len(project["components"]) == 2


def test_publishing_without_a_gitea_repository_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/v1/projects",
        json={
            "key": "x",
            "name": "X",
            "components": [
                {"key": "api", "drone_owner": "102573", "drone_repo": "soda"},
            ],
        },
    )

    assert response.status_code == 422
    assert "publish_enabled" in response.json()["detail"]


def test_a_component_that_never_publishes_needs_no_gitea_repository(client: TestClient) -> None:
    project = make_project(
        client,
        key="x",
        components=[
            {
                "key": "api",
                "drone_owner": "102573",
                "drone_repo": "soda",
                "publish_enabled": False,
            }
        ],
    )

    assert project["components"][0]["publish_enabled"] is False
    assert project["components"][0]["gitea_slug"] is None


def test_duplicate_component_key_within_a_project_is_rejected(client: TestClient) -> None:
    project = make_project(client)

    response = client.post(
        f"/api/v1/projects/{project['id']}/components",
        json=component_payload(key="backend", drone_repo="other"),
    )

    assert response.status_code == 409


def test_component_added_at_a_position_shifts_the_rest_down(client: TestClient) -> None:
    project = make_project(client)

    response = client.post(
        f"/api/v1/projects/{project['id']}/components",
        json=component_payload(
            key="gateway", drone_repo="gateway", gitea_repo="gateway", position=1
        ),
    )

    assert response.status_code == 201
    reloaded = client.get(f"/api/v1/projects/{project['id']}").json()
    assert [c["key"] for c in reloaded["components"]] == ["gateway", "backend", "frontend"]
    assert [c["position"] for c in reloaded["components"]] == [1, 2, 3]


def test_components_can_be_reordered(client: TestClient) -> None:
    """A swap has no valid intermediate state under a unique (project, position)."""
    project = make_project(client)
    backend, frontend = project["components"]

    response = client.post(
        f"/api/v1/projects/{project['id']}/components/reorder",
        json={"component_ids": [frontend["id"], backend["id"]]},
    )

    assert response.status_code == 200
    assert [c["key"] for c in response.json()["components"]] == ["frontend", "backend"]
    assert [c["position"] for c in response.json()["components"]] == [1, 2]


def test_reorder_must_list_every_component(client: TestClient) -> None:
    project = make_project(client)

    response = client.post(
        f"/api/v1/projects/{project['id']}/components/reorder",
        json={"component_ids": [project["components"][0]["id"]]},
    )

    assert response.status_code == 422


def test_deleting_a_component_closes_the_gap_in_the_order(client: TestClient) -> None:
    project = make_project(client)
    project = (
        client.post(
            f"/api/v1/projects/{project['id']}/components",
            json=component_payload(key="gateway", drone_repo="gateway", gitea_repo="gateway"),
        )
        and client.get(f"/api/v1/projects/{project['id']}").json()
    )

    assert client.delete(f"/api/v1/projects/{project['id']}/components/backend").status_code == 204

    reloaded = client.get(f"/api/v1/projects/{project['id']}").json()
    assert [c["key"] for c in reloaded["components"]] == ["frontend", "gateway"]
    assert [c["position"] for c in reloaded["components"]] == [1, 2]


def test_archived_projects_are_hidden_unless_asked_for(bare_registry: TestClient) -> None:
    client = bare_registry
    project = make_project(client)

    assert client.post(f"/api/v1/projects/{project['id']}/archive").status_code == 200
    assert client.get("/api/v1/projects").json()["items"] == []
    assert len(client.get("/api/v1/projects?include_archived=true").json()["items"]) == 1

    client.post(f"/api/v1/projects/{project['id']}/unarchive")
    assert len(client.get("/api/v1/projects").json()["items"]) == 1


# --------------------------------------------------------------------------- #
# validate
# --------------------------------------------------------------------------- #


def use_checker(client: TestClient, *, drone_client, gitea_client) -> None:
    """把專案驗證指向假的 Drone / Gitea。

    跟 use_probe 一樣覆寫注入的相依，而不是 monkeypatch 模組屬性。
    """

    def override(
        resolve_connection: ResolveConnectionUseCase = Depends(get_resolve_connection_usecase),
        settings=Depends(get_settings),
    ) -> UpstreamComponentChecker:
        return UpstreamComponentChecker(
            resolve_connection,
            settings,
            drone_client=drone_client,
            gitea_client=gitea_client,
        )

    client.app.dependency_overrides[get_component_checker] = override


def test_validate_confirms_every_component_against_the_real_upstreams(
    client: TestClient,
) -> None:
    use_checker(client, drone_client=StubDroneClient, gitea_client=StubGiteaClient)
    make_connection(client, DRONE_CONNECTION)
    make_connection(client, GITEA_CONNECTION)
    project = make_project(client)

    body = client.post(f"/api/v1/projects/{project['id']}/validate").json()

    assert body["status"] == "ok"
    assert [check["component_key"] for check in body["checks"]] == ["backend", "frontend"]
    assert all(check["drone"] == "ok" and check["gitea"] == "ok" for check in body["checks"])


def test_validate_names_the_repository_drone_does_not_have(client: TestClient) -> None:
    class MissingRepoDrone(StubDroneClient):
        def get_repository(self, owner: str, repo: str) -> dict:
            raise DroneNotFoundError("no such repo")

    use_checker(client, drone_client=MissingRepoDrone, gitea_client=StubGiteaClient)
    make_connection(client, DRONE_CONNECTION)
    make_connection(client, GITEA_CONNECTION)
    project = make_project(client)

    body = client.post(f"/api/v1/projects/{project['id']}/validate").json()

    assert body["status"] == "error"
    assert body["checks"][0]["detail"] == "Drone has no repository 102573/soda"


def test_validate_reports_a_missing_gitea_repository_separately(client: TestClient) -> None:
    class MissingRepoGitea(StubGiteaClient):
        def get_repository(self, owner: str, repo: str) -> dict:
            raise GiteaNotFoundError("no such repo")

    use_checker(client, drone_client=StubDroneClient, gitea_client=MissingRepoGitea)
    make_connection(client, DRONE_CONNECTION)
    make_connection(client, GITEA_CONNECTION)
    project = make_project(client)

    body = client.post(f"/api/v1/projects/{project['id']}/validate").json()

    assert body["status"] == "error"
    assert body["checks"][0]["drone"] == "ok"
    assert "Gitea has no repository" in body["checks"][0]["detail"]


def test_validate_says_which_connection_is_missing_rather_than_failing(
    bare_registry: TestClient,
) -> None:
    """No connections configured at all is a configuration answer, not a 500."""
    project = make_project(bare_registry)
    client = bare_registry

    body = client.post(f"/api/v1/projects/{project['id']}/validate").json()

    assert body["status"] == "error"
    assert "No default drone connection" in body["checks"][0]["detail"]


# --------------------------------------------------------------------------- #
# authorisation
# --------------------------------------------------------------------------- #


def test_configuration_writes_are_open_by_default(client: TestClient) -> None:
    """PROJECT_ADMIN_WRITES defaults off so an installation cannot lock itself out."""
    assert client.post("/api/v1/connections", json=DRONE_CONNECTION).status_code == 201


def test_configuration_writes_require_an_admin_when_the_gate_is_on(
    client: TestClient, monkeypatch
) -> None:
    from app.infrastructure.di import injection as api_auth
    from app.services.auth_service import CurrentUser

    monkeypatch.setenv("PROJECT_ADMIN_WRITES", "true")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    get_settings.cache_clear()
    client.app.dependency_overrides[api_auth.optional_current_user] = lambda: CurrentUser(
        login="ordinary-user", is_admin=False
    )

    try:
        blocked = client.post("/api/v1/connections", json=DRONE_CONNECTION)
        assert blocked.status_code == 403
        # Reading configuration is not gated: it is how someone works out who to ask.
        assert client.get("/api/v1/connections").status_code == 200

        client.app.dependency_overrides[api_auth.optional_current_user] = lambda: CurrentUser(
            login="an-admin", is_admin=True
        )
        assert client.post("/api/v1/connections", json=DRONE_CONNECTION).status_code == 201
    finally:
        client.app.dependency_overrides.pop(api_auth.optional_current_user, None)
        get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# migration
# --------------------------------------------------------------------------- #


def test_migration_0014_seeds_the_environment_as_the_default_project(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "registry.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("APP_SECRET_KEY", "migration-test-key")
    monkeypatch.setenv("DRONE_TOKEN", "seeded-drone-token")
    monkeypatch.setenv("GITEA_BACKEND_REPO_OWNER", "102573")
    monkeypatch.setenv("GITEA_BACKEND_REPO_NAME", "soda")
    monkeypatch.setenv("GITEA_FRONTEND_REPO_OWNER", "102573")
    monkeypatch.setenv("GITEA_FRONTEND_REPO_NAME", "SMT-Assistant")
    get_settings.cache_clear()

    try:
        command.upgrade(Config("alembic.ini"), "head")
        engine = create_engine(database_url)

        assert {"upstream_connections", "projects", "project_components"}.issubset(
            set(inspect(engine).get_table_names())
        )
        with engine.connect() as connection:
            connections = connection.execute(
                text("SELECT kind, name, is_default, token_encrypted FROM upstream_connections")
            ).all()
            project = connection.execute(text("SELECT key, default_target FROM projects")).one()
            components = connection.execute(
                text(
                    "SELECT key, position, drone_owner, drone_repo, publish_enabled "
                    "FROM project_components ORDER BY position"
                )
            ).all()

        assert {(row.kind, row.name, bool(row.is_default)) for row in connections} == {
            ("drone", "default-drone", True),
            ("gitea", "default-gitea", True),
        }
        # The credential must not be readable in the file the seed just wrote.
        assert all("seeded-drone-token" not in row.token_encrypted for row in connections)
        assert project.key == "default"
        assert project.default_target == "production"
        # Backend first, which is the order ReleaseOrchestrator uses today.
        assert [(row.key, row.position) for row in components] == [
            ("backend", 1),
            ("frontend", 2),
        ]
        assert [row.drone_repo for row in components] == ["soda", "SMT-Assistant"]
        assert all(row.publish_enabled for row in components)
        engine.dispose()
    finally:
        get_settings.cache_clear()


def test_migration_0014_downgrades_cleanly(tmp_path, monkeypatch) -> None:
    """0014 is reversible; only the revision that rekeys deployments is not."""
    database_path = tmp_path / "registry-down.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("APP_SECRET_KEY", "migration-test-key")
    get_settings.cache_clear()

    try:
        config = Config("alembic.ini")
        command.upgrade(config, "head")
        command.downgrade(config, "20260901_0013")
        engine = create_engine(database_url)

        tables = set(inspect(engine).get_table_names())
        assert not {"upstream_connections", "projects", "project_components"} & tables
        # The revision it stands on is untouched.
        assert {"deployments", "workflow_events"}.issubset(tables)
        engine.dispose()
    finally:
        get_settings.cache_clear()


def test_seed_leaves_the_registry_empty_when_no_repositories_are_configured(
    tmp_path, monkeypatch
) -> None:
    """A half-filled environment must not produce a project that looks configured."""
    database_path = tmp_path / "registry-empty.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("APP_SECRET_KEY", "migration-test-key")
    # Empty environment values deliberately override any developer-local .env.
    # Deleting these variables would let BaseSettings read the repositories from
    # .env again, making this isolation test depend on the machine running it.
    monkeypatch.setenv("DRONE_FRONTEND_REPO_OWNER", "")
    monkeypatch.setenv("DRONE_FRONTEND_REPO_NAME", "")
    monkeypatch.setenv("DRONE_BACKEND_REPO_OWNER", "")
    monkeypatch.setenv("DRONE_BACKEND_REPO_NAME", "")
    get_settings.cache_clear()

    try:
        command.upgrade(Config("alembic.ini"), "head")
        engine = create_engine(database_url)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM projects")).scalar_one() == 0
            assert (
                connection.execute(text("SELECT COUNT(*) FROM upstream_connections")).scalar_one()
                == 0
            )
        engine.dispose()
    finally:
        get_settings.cache_clear()


def test_production_refuses_to_start_without_a_secret_key(monkeypatch) -> None:
    """Otherwise the stored deployment credentials would be encrypted with a
    constant published in this repository."""
    from app.core.config import Settings

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("APP_SECRET_KEY", "")

    with pytest.raises(ValueError, match="APP_SECRET_KEY"):
        Settings(_env_file=None, app_env="production", auth_enabled=True, app_secret_key="")

    assert datetime.now(timezone.utc) is not None
