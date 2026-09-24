from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.integrations.gitea.oauth import (
    GiteaOAuthClient,
    GiteaOAuthError,
    GiteaOAuthUser,
)


def oauth_settings() -> Settings:
    return Settings(
        app_env="test",
        auth_enabled=True,
        auth_cookie_secure=False,
        gitea_server="http://gitea.test:3000",
        gitea_oauth_client_id="release-controller-client",
        gitea_oauth_client_secret="secret",
    )


def complete_login(client: TestClient, monkeypatch) -> None:
    client.app.dependency_overrides[get_settings] = oauth_settings
    monkeypatch.setattr(
        GiteaOAuthClient,
        "authenticate",
        lambda _self, _code, _callback, _verifier: GiteaOAuthUser(
            login="verified-user",
            full_name="Verified User",
            email="verified@example.com",
            avatar_url="http://gitea.test/avatar.png",
            is_admin=False,
        ),
    )
    login = client.get("/api/v1/auth/login", follow_redirects=False)
    assert login.status_code == 302
    authorize_query = parse_qs(urlparse(login.headers["location"]).query)
    state = authorize_query["state"][0]
    assert authorize_query["code_challenge_method"] == ["S256"]

    callback = client.get(
        f"/api/v1/auth/callback?code=test-code&state={state}",
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == "/"


def test_protected_api_requires_login(client: TestClient) -> None:
    client.app.dependency_overrides[get_settings] = oauth_settings

    session = client.get("/api/v1/auth/session")
    protected = client.get("/api/v1/releases")

    assert session.status_code == 200
    assert session.json() == {
        "enabled": True,
        "configured": True,
        "authenticated": False,
        "user": None,
    }
    assert protected.status_code == 401
    assert protected.json() == {"detail": "Authentication required"}
    assert client.get("/health").status_code == 200


def test_gitea_oauth_login_session_and_logout(client: TestClient, monkeypatch) -> None:
    complete_login(client, monkeypatch)

    session = client.get("/api/v1/auth/session")
    assert session.status_code == 200
    assert session.json()["authenticated"] is True
    assert session.json()["user"] == {
        "login": "verified-user",
        "full_name": "Verified User",
        "email": "verified@example.com",
        "avatar_url": "http://gitea.test/avatar.png",
        "is_admin": False,
    }
    assert client.get("/api/v1/releases").status_code == 200

    logout = client.post("/api/v1/auth/logout")
    assert logout.status_code == 204
    assert client.get("/api/v1/releases").status_code == 401


def test_verified_identity_overrides_approval_actor(
    client: TestClient, monkeypatch, release_payload: dict[str, str]
) -> None:
    complete_login(client, monkeypatch)
    created = client.post("/api/v1/releases", json=release_payload)
    assert created.status_code == 201

    approved = client.post(
        f"/api/v1/releases/{created.json()['id']}/approve",
        json={"approved_by": "forged-user"},
    )

    assert approved.status_code == 200
    assert approved.json()["approved_by"] == "verified-user"


def test_oauth_callback_rejects_state_without_matching_browser_cookie(
    client: TestClient,
) -> None:
    client.app.dependency_overrides[get_settings] = oauth_settings

    response = client.get(
        "/api/v1/auth/callback?code=test-code&state=forged",
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid OAuth state"}


def test_oauth_callback_redirects_gitea_denial_to_login(client: TestClient) -> None:
    client.app.dependency_overrides[get_settings] = oauth_settings

    response = client.get(
        "/api/v1/auth/callback?error=access_denied",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/?auth_error=access_denied"


def test_gitea_oauth_client_exchanges_code_and_loads_user(monkeypatch) -> None:
    requests: dict[str, object] = {}

    def fake_post(url: str, **kwargs) -> httpx.Response:
        requests["token_url"] = url
        requests["token_data"] = kwargs["data"]
        return httpx.Response(200, json={"access_token": "oauth-token"})

    def fake_get(url: str, **kwargs) -> httpx.Response:
        requests["user_url"] = url
        requests["user_headers"] = kwargs["headers"]
        return httpx.Response(
            200,
            json={
                "login": "jhan",
                "full_name": "Jhan",
                "email": "",
                "avatar_url": "http://gitea.test/avatar.png",
                "is_admin": True,
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)
    oauth = GiteaOAuthClient("http://gitea.test/", "client", "secret", timeout=2)

    user = oauth.authenticate("code", "http://controller/callback", "verifier")

    assert user == GiteaOAuthUser(
        login="jhan",
        full_name="Jhan",
        email=None,
        avatar_url="http://gitea.test/avatar.png",
        is_admin=True,
    )
    assert requests["token_data"] == {
        "client_id": "client",
        "client_secret": "secret",
        "code": "code",
        "grant_type": "authorization_code",
        "redirect_uri": "http://controller/callback",
        "code_verifier": "verifier",
    }
    assert requests["user_headers"] == {
        "Accept": "application/json",
        "Authorization": "Bearer oauth-token",
    }


def test_gitea_oauth_client_rejects_invalid_token_response(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_args, **_kwargs: httpx.Response(200, json={}),
    )
    oauth = GiteaOAuthClient("http://gitea.test", "client")

    with pytest.raises(GiteaOAuthError, match="did not return an access token"):
        oauth.authenticate("code", "http://controller/callback", "verifier")
