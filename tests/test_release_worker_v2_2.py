from collections import defaultdict
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from SpiffWorkflow.bpmn.parser.BpmnParser import BpmnParser

from app.infrastructure.di.injection import get_drone_client, get_drone_clients
from app.integrations.drone.exceptions import DronePromoteError
from app.integrations.drone.schemas import DroneBuild, DroneRepositoryInfo
from app.services.upstream_clients import FixedDroneClients


class FakeDroneClient:
    def __init__(self) -> None:
        self.promotions: list[tuple[str, str, int, str]] = []
        self.promotion_numbers = {"SMT-Assistant": 124, "soda": 457}
        self.statuses: dict[tuple[str, int], list[str]] = defaultdict(list)
        self.fail_promote_repo: str | None = None

    def list_builds(self, owner: str, repo: str, limit: int = 20) -> list[DroneBuild]:
        number = 123 if repo == "SMT-Assistant" else 456
        return [self._source(number)][:limit]

    def get_repository(self, owner: str, repo: str) -> DroneRepositoryInfo:
        return DroneRepositoryInfo(
            owner=owner,
            name=repo,
            slug=f"{owner}/{repo}",
            link=f"http://gitea.local/{owner}/{repo}",
            default_branch="main",
        )

    def get_build(self, owner: str, repo: str, build_number: int) -> DroneBuild:
        if build_number in {123, 456}:
            return self._source(build_number)
        values = self.statuses[(repo, build_number)]
        status = values.pop(0) if len(values) > 1 else values[0] if values else "running"
        return DroneBuild(
            number=build_number, status=status, event="promote", branch="main", commit_sha="f" * 40
        )

    def promote_build(self, owner: str, repo: str, build_number: int, target: str) -> DroneBuild:
        self.promotions.append((owner, repo, build_number, target))
        if repo == self.fail_promote_repo:
            raise DronePromoteError("Drone promote failed")
        number = self.promotion_numbers[repo]
        return DroneBuild(
            number=number,
            status="pending",
            event="promote",
            target=target,
            branch="main",
            commit_sha="f" * 40,
        )

    def status(self) -> bool:
        return True

    @staticmethod
    def _source(number: int) -> DroneBuild:
        return DroneBuild(
            number=number,
            status="success",
            event="push",
            branch="main",
            commit_sha=("a" if number == 123 else "b") * 40,
            commit_message="ready",
            author="tester",
        )


@pytest.fixture
def drone(client: TestClient) -> FakeDroneClient:
    fake = FakeDroneClient()
    client.app.dependency_overrides[get_drone_client] = lambda: fake
    client.app.dependency_overrides[get_drone_clients] = lambda: FixedDroneClients(fake)
    return fake


def test_independent_build_lists_and_frontend_only(
    client: TestClient, drone: FakeDroneClient
) -> None:
    base = "/api/v1/projects/default/components"
    frontend = client.get(f"{base}/frontend/builds")
    backend = client.get(f"{base}/backend/builds")
    assert frontend.status_code == backend.status_code == 200
    assert frontend.json()["items"][0]["number"] == 123
    assert backend.json()["items"][0]["number"] == 456
    assert frontend.json()["items"][0]["promotable"] is True
    assert frontend.json()["repository"]["slug"] == "102573/SMT-Assistant"
    assert backend.json()["repository"]["slug"] == "102573/soda"
    assert frontend.json()["branches"] == ["main"]

    # The default target is the project's, not a server-wide setting.
    project = client.get("/api/v1/projects/default")
    assert project.status_code == 200
    assert project.json()["default_target"] == "production"

    response = client.post(f"{base}/frontend/builds/123/promote")
    assert response.status_code == 201
    assert response.json()["component"] == "frontend"
    assert response.json()["source_build_number"] == 123
    assert response.json()["promotion_build_number"] == 124
    assert [call[1] for call in drone.promotions] == ["SMT-Assistant"]
    events = client.get(f"/api/v1/deployments/{response.json()['id']}/events")
    assert events.status_code == 200
    event_types = [item["event_type"] for item in events.json()["items"]]
    assert "STAGE_STARTED" in event_types
    assert "STAGE_SUCCEEDED" in event_types
    history = client.get("/api/v1/deployments").json()
    assert history["total"] == 1
    assert all(item["component"] != "backend" for item in history["items"])


def test_bundle_is_backend_first_and_uses_independent_numbers(
    client: TestClient, drone: FakeDroneClient
) -> None:
    response = client.post(
        "/api/v1/releases/promote",
        json={
            "frontend_build_number": 123,
            "backend_build_number": 456,
            "target": "pre-production",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["mode"] == "BUNDLE"
    by_component = {item["component"]: item for item in body["deployments"]}
    assert by_component["backend"]["status"] == "DEPLOYING"
    assert by_component["backend"]["source_build_number"] == 456
    assert by_component["frontend"]["status"] == "WAITING"
    assert by_component["frontend"]["source_build_number"] == 123
    assert [call[1] for call in drone.promotions] == ["soda"]


def test_backend_failure_cancels_frontend_and_records_events(
    client: TestClient, drone: FakeDroneClient
) -> None:
    drone.statuses[("soda", 457)] = ["failure"]
    bundle = client.post(
        "/api/v1/releases/promote",
        json={"frontend_build_number": 123, "backend_build_number": 456},
    ).json()
    refreshed = client.post(f"/api/v1/releases/{bundle['id']}/refresh")
    assert refreshed.status_code == 200
    body = refreshed.json()
    assert body["status"] == "FAILED"
    assert body["failed_stage"] == "WAIT_BACKEND_DEPLOYMENT"
    by_component = {item["component"]: item for item in body["deployments"]}
    assert by_component["backend"]["error_code"] == "DRONE_BUILD_FAILED"
    assert by_component["frontend"]["status"] == "CANCELLED"
    assert by_component["frontend"]["cancel_reason"] == "Backend deployment failed"
    assert [call[1] for call in drone.promotions] == ["soda"]
    events = client.get(f"/api/v1/releases/{bundle['id']}/events").json()["items"]
    event_types = [event["event_type"] for event in events]
    assert "STAGE_STARTED" in event_types
    assert "STAGE_SUCCEEDED" in event_types
    assert "STAGE_FAILED" in event_types
    assert "DEPLOYMENT_CANCELLED" in event_types


def test_backend_success_then_frontend_failure_is_partial(
    client: TestClient, drone: FakeDroneClient
) -> None:
    drone.statuses[("soda", 457)] = ["success"]
    drone.statuses[("SMT-Assistant", 124)] = ["failure"]
    bundle = client.post(
        "/api/v1/releases/promote",
        json={"frontend_build_number": 123, "backend_build_number": 456},
    ).json()
    first = client.post(f"/api/v1/releases/{bundle['id']}/refresh").json()
    assert {item["component"]: item["status"] for item in first["deployments"]} == {
        "backend": "SUCCESS",
        "frontend": "DEPLOYING",
    }
    assert [call[1] for call in drone.promotions] == ["soda", "SMT-Assistant"]
    second = client.post(f"/api/v1/releases/{bundle['id']}/refresh").json()
    assert second["status"] == "PARTIAL_FAILURE"
    assert second["failed_stage"] == "WAIT_FRONTEND_DEPLOYMENT"


def test_both_success_and_duplicate_protection(client: TestClient, drone: FakeDroneClient) -> None:
    drone.statuses[("soda", 457)] = ["success"]
    drone.statuses[("SMT-Assistant", 124)] = ["success"]
    bundle = client.post(
        "/api/v1/releases/promote",
        json={"frontend_build_number": 123, "backend_build_number": 456},
    ).json()
    client.post(f"/api/v1/releases/{bundle['id']}/refresh")
    completed = client.post(f"/api/v1/releases/{bundle['id']}/refresh").json()
    assert completed["status"] == "SUCCESS"
    assert completed["finished_at"] is not None
    duplicate = client.post(
        "/api/v1/releases/promote",
        json={"frontend_build_number": 123, "backend_build_number": 456},
    )
    assert duplicate.status_code == 409


def test_backend_promote_api_failure_is_persisted_and_cancels_frontend(
    client: TestClient, drone: FakeDroneClient
) -> None:
    drone.fail_promote_repo = "soda"
    response = client.post(
        "/api/v1/releases/promote",
        json={"frontend_build_number": 123, "backend_build_number": 456},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "FAILED"
    by_component = {item["component"]: item for item in body["deployments"]}
    assert by_component["backend"]["promotion_build_number"] is None
    assert by_component["backend"]["failed_stage"] == "PROMOTE_BACKEND"
    assert by_component["frontend"]["status"] == "CANCELLED"


def test_workflow_definitions_are_machine_readable(client: TestClient) -> None:
    for mode, definition_id, filename, expected_stage in (
        ("FRONTEND_ONLY", "frontend_only", "frontend_only.bpmn", "VALIDATE_FRONTEND"),
        ("BACKEND_ONLY", "backend_only", "backend_only.bpmn", "VALIDATE_BACKEND"),
        ("BUNDLE", "release_bundle", "release_bundle.bpmn", "CREATE_RELEASE_BUNDLE"),
    ):
        response = client.get(f"/api/v1/workflows/{mode}/definition")
        assert response.status_code == 200
        assert 'isExecutable="true"' in response.text
        assert "bpmndi:BPMNDiagram" in response.text
        assert expected_stage in response.text
        assert "COMPLETE_DEPLOYMENT" in response.text
        assert "WAIT_PUBLISH_REQUEST" in response.text
        assert "PREPARE_RELEASE_VERSION" in response.text
        assert "COMPLETE_PUBLISH" in response.text
        parser = BpmnParser()
        parser.add_bpmn_file(str(Path("app/workflows/bpmn") / filename))
        assert parser.get_spec(definition_id) is not None
