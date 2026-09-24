from collections import defaultdict

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.infrastructure.di.injection import (
    get_drone_client,
    get_drone_clients,
    get_gitea_clients,
)
from app.integrations.drone.schemas import DroneBuild, DroneRepositoryInfo
from app.integrations.gitea.exceptions import (
    GiteaAuthenticationError,
    GiteaNotFoundError,
)
from app.integrations.gitea.schemas import GiteaRelease, GiteaRepository, GiteaTag
from app.services.upstream_clients import FixedDroneClients, FixedGiteaClients


class PublishDroneClient:
    def __init__(self) -> None:
        self.statuses: dict[tuple[str, int], list[str]] = defaultdict(list)

    def get_build(self, owner: str, repo: str, build_number: int) -> DroneBuild:
        if build_number in {123, 456}:
            return DroneBuild(
                number=build_number,
                status="success",
                event="push",
                branch="main",
                commit_sha=("a" if build_number == 123 else "b") * 40,
            )
        values = self.statuses[(repo, build_number)]
        status = values.pop(0) if len(values) > 1 else values[0] if values else "running"
        return DroneBuild(
            number=build_number,
            status=status,
            event="promote",
            branch="main",
            commit_sha="f" * 40,
        )

    def promote_build(self, owner: str, repo: str, build_number: int, target: str) -> DroneBuild:
        return DroneBuild(
            number=124 if repo == "SMT-Assistant" else 457,
            status="pending",
            event="promote",
            branch="main",
            commit_sha="f" * 40,
        )

    def get_repository(self, owner: str, repo: str) -> DroneRepositoryInfo:
        return DroneRepositoryInfo(owner, repo, f"{owner}/{repo}")

    def list_builds(self, owner: str, repo: str, limit: int = 20) -> list[DroneBuild]:
        return []


class PublishGiteaClient:
    def __init__(self) -> None:
        self.tags: dict[tuple[str, str, str], str] = {}
        self.releases: dict[tuple[str, str, str], GiteaRelease] = {}
        self.create_calls: list[tuple[str, str, str, str]] = []
        self.fail_once_repo: str | None = None
        self.auth_failure_repo: str | None = None

    def health(self) -> dict:
        return {"status": "pass"}

    def get_repository(self, owner: str, repo: str) -> GiteaRepository:
        return GiteaRepository(owner, repo, f"{owner}/{repo}")

    def get_tag(self, owner: str, repo: str, tag_name: str) -> GiteaTag:
        try:
            commit = self.tags[(owner, repo, tag_name)]
        except KeyError as exc:
            raise GiteaNotFoundError("Tag not found") from exc
        return GiteaTag(tag_name, commit)

    def get_release_by_tag(self, owner: str, repo: str, tag_name: str) -> GiteaRelease:
        try:
            return self.releases[(owner, repo, tag_name)]
        except KeyError as exc:
            raise GiteaNotFoundError("Release not found") from exc

    def create_release(
        self,
        owner: str,
        repo: str,
        tag_name: str,
        target_commitish: str,
        name: str,
        body: str,
        draft: bool = False,
        prerelease: bool = False,
    ) -> GiteaRelease:
        self.create_calls.append((owner, repo, tag_name, target_commitish))
        if repo == self.auth_failure_repo:
            raise GiteaAuthenticationError("Gitea authentication failed")
        if repo == self.fail_once_repo:
            self.fail_once_repo = None
            raise GiteaAuthenticationError("Gitea authentication failed")
        key = (owner, repo, tag_name)
        self.tags[key] = target_commitish
        release = GiteaRelease(
            id=len(self.releases) + 1,
            tag_name=tag_name,
            name=name,
            html_url=f"http://gitea.test/{owner}/{repo}/releases/tag/{tag_name}",
            target_commitish=target_commitish,
        )
        self.releases[key] = release
        return release


@pytest.fixture
def publish_dependencies(client: TestClient) -> tuple[PublishDroneClient, PublishGiteaClient]:
    drone = PublishDroneClient()
    gitea = PublishGiteaClient()
    # The repositories come from the seeded project's components, not from here.
    settings = Settings(database_url="sqlite:///unused.db")
    client.app.dependency_overrides[get_drone_client] = lambda: drone
    client.app.dependency_overrides[get_drone_clients] = lambda: FixedDroneClients(drone)
    client.app.dependency_overrides[get_gitea_clients] = lambda: FixedGiteaClients(gitea)
    client.app.dependency_overrides[get_settings] = lambda: settings
    return drone, gitea


PUBLISH = {
    "version": "v1.8.0",
    "name": "v1.8.0",
    "release_notes": "Release notes",
    "draft": False,
    "prerelease": False,
}


def complete_standalone(client: TestClient, drone: PublishDroneClient, component: str) -> dict:
    number = 123 if component == "frontend" else 456
    repo = "SMT-Assistant" if component == "frontend" else "soda"
    promotion = 124 if component == "frontend" else 457
    deployment = client.post(
        f"/api/v1/projects/default/components/{component}/builds/{number}/promote"
    ).json()
    drone.statuses[(repo, promotion)] = ["success"]
    return client.post(f"/api/v1/deployments/{deployment['id']}/refresh").json()


def complete_bundle(client: TestClient, drone: PublishDroneClient) -> dict:
    drone.statuses[("soda", 457)] = ["success"]
    drone.statuses[("SMT-Assistant", 124)] = ["success"]
    bundle = client.post(
        "/api/v1/releases/promote",
        json={"frontend_build_number": 123, "backend_build_number": 456},
    ).json()
    client.post(f"/api/v1/releases/{bundle['id']}/refresh")
    return client.post(f"/api/v1/releases/{bundle['id']}/refresh").json()


@pytest.mark.parametrize(
    ("component", "expected_repo", "expected_commit"),
    [
        ("frontend", "SMT-Assistant", "a" * 40),
        ("backend", "soda", "b" * 40),
    ],
)
def test_standalone_publish_only_calls_participating_repository(
    client: TestClient,
    publish_dependencies: tuple[PublishDroneClient, PublishGiteaClient],
    component: str,
    expected_repo: str,
    expected_commit: str,
) -> None:
    drone, gitea = publish_dependencies
    deployment = complete_standalone(client, drone, component)

    response = client.post(f"/api/v1/deployments/{deployment['id']}/publish", json=PUBLISH)

    assert response.status_code == 200
    assert response.json()["status"] == "SUCCESS"
    assert response.json()["publish_status"] == "PUBLISHED"
    assert gitea.create_calls == [("102573", expected_repo, "v1.8.0", expected_commit)]


def test_publish_before_deployment_success_returns_conflict(
    client: TestClient,
    publish_dependencies: tuple[PublishDroneClient, PublishGiteaClient],
) -> None:
    deployment = client.post(
        "/api/v1/projects/default/components/frontend/builds/123/promote"
    ).json()

    response = client.post(f"/api/v1/deployments/{deployment['id']}/publish", json=PUBLISH)

    assert response.status_code == 409
    assert response.json()["detail"] == "Deployment is not publishable"


def test_bundle_publish_uses_backend_first_and_independent_commits(
    client: TestClient,
    publish_dependencies: tuple[PublishDroneClient, PublishGiteaClient],
) -> None:
    drone, gitea = publish_dependencies
    bundle = complete_bundle(client, drone)

    response = client.post(f"/api/v1/releases/{bundle['id']}/publish", json=PUBLISH)

    assert response.status_code == 200
    body = response.json()
    assert body["deployment_status"] == "SUCCESS"
    assert body["publish_status"] == "PUBLISHED"
    assert gitea.create_calls == [
        ("102573", "soda", "v1.8.0", "b" * 40),
        ("102573", "SMT-Assistant", "v1.8.0", "a" * 40),
    ]


def test_gitea_auth_failure_does_not_change_deployment_success(
    client: TestClient,
    publish_dependencies: tuple[PublishDroneClient, PublishGiteaClient],
) -> None:
    drone, gitea = publish_dependencies
    deployment = complete_standalone(client, drone, "backend")
    gitea.auth_failure_repo = "soda"

    response = client.post(f"/api/v1/deployments/{deployment['id']}/publish", json=PUBLISH)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "SUCCESS"
    assert body["publish_status"] == "FAILED"
    assert body["publish_error_code"] == "GITEA_AUTH_FAILED"
    assert "token" not in response.text.lower()


def test_partial_failure_retry_skips_already_published_backend(
    client: TestClient,
    publish_dependencies: tuple[PublishDroneClient, PublishGiteaClient],
) -> None:
    drone, gitea = publish_dependencies
    bundle = complete_bundle(client, drone)
    gitea.fail_once_repo = "SMT-Assistant"

    first = client.post(f"/api/v1/releases/{bundle['id']}/publish", json=PUBLISH)

    assert first.status_code == 200
    assert first.json()["deployment_status"] == "SUCCESS"
    assert first.json()["publish_status"] == "PARTIAL_FAILURE"
    assert [item[1] for item in gitea.create_calls] == ["soda", "SMT-Assistant"]

    second = client.post(f"/api/v1/releases/{bundle['id']}/publish", json=PUBLISH)

    assert second.status_code == 200
    assert second.json()["publish_status"] == "PUBLISHED"
    assert [item[1] for item in gitea.create_calls] == [
        "soda",
        "SMT-Assistant",
        "SMT-Assistant",
    ]


def test_existing_same_tag_and_commit_is_idempotent_success(
    client: TestClient,
    publish_dependencies: tuple[PublishDroneClient, PublishGiteaClient],
) -> None:
    drone, gitea = publish_dependencies
    deployment = complete_standalone(client, drone, "frontend")
    key = ("102573", "SMT-Assistant", "v1.8.0")
    gitea.tags[key] = "a" * 40
    gitea.releases[key] = GiteaRelease(88, "v1.8.0", "v1.8.0", "http://existing")

    response = client.post(f"/api/v1/deployments/{deployment['id']}/publish", json=PUBLISH)

    assert response.status_code == 200
    assert response.json()["gitea_release_id"] == "88"
    assert gitea.create_calls == []


def test_existing_tag_on_different_commit_returns_409_and_preserves_deployment(
    client: TestClient,
    publish_dependencies: tuple[PublishDroneClient, PublishGiteaClient],
) -> None:
    drone, gitea = publish_dependencies
    deployment = complete_standalone(client, drone, "frontend")
    gitea.tags[("102573", "SMT-Assistant", "v1.8.0")] = "c" * 40

    response = client.post(f"/api/v1/deployments/{deployment['id']}/publish", json=PUBLISH)

    assert response.status_code == 409
    assert response.json()["detail"] == "Tag v1.8.0 already exists on a different commit"
    detail = client.get(f"/api/v1/deployments/{deployment['id']}").json()
    assert detail["status"] == "SUCCESS"
    assert detail["publish_status"] == "FAILED"
    assert detail["publish_error_code"] == "TAG_ALREADY_EXISTS_DIFFERENT_COMMIT"


def test_publish_history_and_workflow_events_are_available(
    client: TestClient,
    publish_dependencies: tuple[PublishDroneClient, PublishGiteaClient],
) -> None:
    drone, _gitea = publish_dependencies
    deployment = complete_standalone(client, drone, "backend")
    client.post(f"/api/v1/deployments/{deployment['id']}/publish", json=PUBLISH)

    history = client.get("/api/v1/publishes?component=backend&status=PUBLISHED&version=v1.8.0")
    events = client.get(f"/api/v1/deployments/{deployment['id']}/events")

    assert history.status_code == 200
    assert history.json()["total"] == 1
    publish_id = history.json()["items"][0]["id"]
    assert client.get(f"/api/v1/publishes/{publish_id}").status_code == 200
    assert {
        "PUBLISH_REQUESTED",
        "PUBLISH_STAGE_SUCCEEDED",
        "GITEA_RELEASE_CREATED",
        "PUBLISH_COMPLETED",
    }.issubset({item["event_type"] for item in events.json()["items"]})


def test_version_validation_rejects_non_semver(
    client: TestClient,
    publish_dependencies: tuple[PublishDroneClient, PublishGiteaClient],
) -> None:
    drone, _gitea = publish_dependencies
    deployment = complete_standalone(client, drone, "frontend")

    response = client.post(
        f"/api/v1/deployments/{deployment['id']}/publish",
        json={**PUBLISH, "version": "release-latest"},
    )

    assert response.status_code == 422
