import httpx
from fastapi.testclient import TestClient

from app.infrastructure.di.injection import get_gitea_client
from app.integrations.gitea.client import GiteaClient
from app.integrations.gitea.exceptions import GiteaTimeoutError


class HealthyGiteaClient:
    server = "http://gitea.test:3000"
    health_url = "http://gitea.test:3000/api/healthz"

    def health(self) -> dict:
        return {"status": "pass"}


class UnavailableGiteaClient(HealthyGiteaClient):
    def health(self) -> dict:
        raise GiteaTimeoutError("Gitea health check timed out")


def test_gitea_health_returns_upstream_endpoint(client: TestClient) -> None:
    client.app.dependency_overrides[get_gitea_client] = HealthyGiteaClient

    response = client.get("/api/v1/gitea/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "server": "http://gitea.test:3000",
        "endpoint": "http://gitea.test:3000/api/healthz",
        "detail": None,
    }


def test_gitea_health_returns_503_when_upstream_is_unavailable(
    client: TestClient,
) -> None:
    client.app.dependency_overrides[get_gitea_client] = UnavailableGiteaClient

    response = client.get("/api/v1/gitea/health")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "server": "http://gitea.test:3000",
        "endpoint": "http://gitea.test:3000/api/healthz",
        "detail": "Gitea health check timed out",
    }


def test_gitea_status_is_compact_and_does_not_affect_service_health(
    client: TestClient,
) -> None:
    client.app.dependency_overrides[get_gitea_client] = UnavailableGiteaClient

    status_response = client.get("/api/v1/gitea/status")
    health_response = client.get("/health")

    assert status_response.status_code == 200
    assert status_response.json() == {"status": "unavailable"}
    assert health_response.status_code == 200
    assert health_response.json()["status"] == "ok"


def test_gitea_client_checks_official_healthz_path(monkeypatch) -> None:
    request: dict[str, object] = {}

    def fake_get(url: str, timeout: float) -> httpx.Response:
        request.update(url=url, timeout=timeout)
        return httpx.Response(200, json={"status": "pass", "checks": {}})

    monkeypatch.setattr(httpx, "get", fake_get)
    gitea = GiteaClient("http://gitea.test:3000/", timeout=2.5)

    assert gitea.health()["status"] == "pass"
    assert request == {
        "url": "http://gitea.test:3000/api/healthz",
        "timeout": 2.5,
    }
