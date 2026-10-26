"""Repository discovery uses stored credentials, never browser-supplied servers."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.integrations.drone.client import DroneClient
from app.integrations.drone.exceptions import DroneUnexpectedResponseError
from app.integrations.gitea.client import GiteaClient
from app.integrations.gitea.exceptions import GiteaUnexpectedResponseError

ENDPOINT = "/api/v1/connections/repositories"


def connection(client: TestClient, kind: str, name: str = "primary") -> dict:
    response = client.post(
        "/api/v1/connections",
        json={
            "kind": kind,
            "name": name,
            "base_url": f"https://{name}.{kind}.test",
            "token": f"{name}-test-token",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_drone_search_filters_full_names_case_insensitively_and_pages(
    bare_registry: TestClient, monkeypatch
) -> None:
    client = bare_registry
    chosen = connection(client, "drone")

    def request(method, url, **kwargs):
        assert method == "GET"
        assert url == "https://primary.drone.test/api/user/repos"
        assert kwargs["headers"]["Authorization"] == "Bearer primary-test-token"
        return httpx.Response(
            200,
            json=[
                {"slug": "team/Alpha.Worker", "description": "Worker"},
                {"namespace": "team", "name": "alpha-api"},
                {"owner": "other", "name": "unrelated"},
            ],
        )

    monkeypatch.setattr(httpx, "request", request)
    first = client.get(ENDPOINT, params={"kind": "drone", "q": "TEAM/ALPHA", "limit": 1})
    assert first.status_code == 200
    assert first.json() == {
        "connection_id": chosen["id"],
        "items": [
            {"owner": "team", "name": "alpha-api", "full_name": "team/alpha-api", "description": ""}
        ],
        "has_more": True,
    }
    second = client.get(
        ENDPOINT, params={"kind": "drone", "q": "TEAM/ALPHA", "limit": 1, "page": 2}
    ).json()
    assert second["items"][0]["name"] == "Alpha.Worker"
    assert second["has_more"] is False
    assert client.get(ENDPOINT, params={"kind": "drone", "q": "absent"}).json()["items"] == []


def test_gitea_search_uses_explicit_connection_and_encodes_query_parameters(
    bare_registry: TestClient, monkeypatch
) -> None:
    client = bare_registry
    connection(client, "gitea")
    chosen = connection(client, "gitea", "secondary")

    def request(method, url, **kwargs):
        assert method == "GET"
        assert url == "https://secondary.gitea.test/api/v1/repos/search"
        assert kwargs["headers"]["Authorization"] == "token secondary-test-token"
        assert kwargs["params"] == {"q": "engine & api", "page": 3, "limit": 1, "private": True}
        return httpx.Response(
            200,
            json={
                "ok": True,
                "data": [
                    {
                        "owner": {"login": "team"},
                        "name": "engine",
                        "description": "Private engine",
                        "internal_token": "must-not-leak",
                    }
                ],
            },
        )

    monkeypatch.setattr(httpx, "request", request)
    response = client.get(
        ENDPOINT,
        params={
            "kind": "gitea",
            "connection_id": chosen["id"],
            "q": " engine & api ",
            "page": 3,
            "limit": 1,
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "connection_id": chosen["id"],
        "items": [
            {
                "owner": "team",
                "name": "engine",
                "full_name": "team/engine",
                "description": "Private engine",
            }
        ],
        "has_more": True,
    }
    assert "token" not in response.text


def test_search_reports_missing_default_unknown_connection_and_wrong_kind(
    bare_registry: TestClient, monkeypatch
) -> None:
    client = bare_registry

    def unexpected_request(*args, **kwargs):
        pytest.fail("Invalid configuration must not make an upstream request")

    monkeypatch.setattr(httpx, "request", unexpected_request)
    assert client.get(ENDPOINT, params={"kind": "drone"}).status_code == 422
    assert (
        client.get(ENDPOINT, params={"kind": "drone", "connection_id": "missing"}).status_code
        == 404
    )
    gitea = connection(client, "gitea")
    response = client.get(ENDPOINT, params={"kind": "drone", "connection_id": gitea["id"]})
    assert response.status_code == 422
    assert "kind" in response.json()["detail"]


@pytest.mark.parametrize("kind", ["drone", "gitea"])
def test_search_hides_upstream_errors(bare_registry: TestClient, monkeypatch, kind) -> None:
    client = bare_registry
    connection(client, kind)
    monkeypatch.setattr(
        httpx,
        "request",
        lambda *args, **kwargs: httpx.Response(401, text="private-upstream-response"),
    )
    response = client.get(ENDPOINT, params={"kind": kind})
    assert response.status_code == 502
    assert "Check the connection and token" in response.json()["detail"]
    assert "private-upstream-response" not in response.text
    assert "primary-test-token" not in response.text


def test_search_reports_unreadable_stored_credential(
    bare_registry: TestClient, monkeypatch
) -> None:
    client = bare_registry
    connection(client, "drone")
    monkeypatch.setenv("APP_SECRET_KEY", "a-different-test-key")
    get_settings.cache_clear()
    response = client.get(ENDPOINT, params={"kind": "drone"})
    assert response.status_code == 503
    assert response.json()["detail"] == "Cannot decrypt the connection token"


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"kind": "other"},
        {"kind": "drone", "page": 0},
        {"kind": "gitea", "limit": 51},
        {"kind": "drone", "q": "a" * 201},
    ],
)
def test_search_validates_parameters(client: TestClient, params) -> None:
    assert client.get(ENDPOINT, params=params).status_code == 422


@pytest.mark.parametrize("payload", [{}, [None], [{"name": "missing-owner"}]])
def test_drone_rejects_malformed_repository_lists(monkeypatch, payload) -> None:
    client = DroneClient("https://drone.test", "test-token")
    monkeypatch.setattr(client, "_read", lambda *args, **kwargs: payload)
    with pytest.raises(DroneUnexpectedResponseError):
        client.list_repositories()


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"data": {}},
        {"ok": False, "data": []},
        {"data": [None]},
        {"data": [{"full_name": "team/api", "description": []}]},
        {"data": [{"full_name": "team/api", "description": 123}]},
    ],
)
def test_gitea_rejects_malformed_repository_lists(monkeypatch, payload) -> None:
    client = GiteaClient("https://gitea.test", "test-token")
    monkeypatch.setattr(client, "_read", lambda *args, **kwargs: payload)
    with pytest.raises(GiteaUnexpectedResponseError):
        client.search_repositories("api")
