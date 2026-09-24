from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx


class GiteaOAuthError(Exception):
    pass


@dataclass(frozen=True)
class GiteaOAuthUser:
    login: str
    full_name: str | None
    email: str | None
    avatar_url: str | None
    is_admin: bool


class GiteaOAuthClient:
    def __init__(
        self,
        server: str,
        client_id: str,
        client_secret: str = "",
        timeout: float = 10.0,
    ) -> None:
        self.server = server.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout

    def authorization_url(self, callback_url: str, state: str, code_challenge: str) -> str:
        query = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": callback_url,
                "response_type": "code",
                "scope": "read:user",
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self.server}/login/oauth/authorize?{query}"

    def authenticate(self, code: str, callback_url: str, code_verifier: str) -> GiteaOAuthUser:
        data = {
            "client_id": self.client_id,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": callback_url,
            "code_verifier": code_verifier,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        try:
            token_response = httpx.post(
                f"{self.server}/login/oauth/access_token",
                data=data,
                headers={"Accept": "application/json"},
                timeout=self.timeout,
            )
            if token_response.status_code >= 400:
                raise GiteaOAuthError("Gitea rejected the OAuth authorization code")
            token_payload: Any = token_response.json()
            access_token = (
                token_payload.get("access_token") if isinstance(token_payload, dict) else None
            )
            if not isinstance(access_token, str) or not access_token:
                raise GiteaOAuthError("Gitea did not return an access token")

            user_response = httpx.get(
                f"{self.server}/api/v1/user",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {access_token}",
                },
                timeout=self.timeout,
            )
            if user_response.status_code >= 400:
                raise GiteaOAuthError("Gitea rejected the OAuth access token")
            payload: Any = user_response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise GiteaOAuthError("Unable to authenticate with Gitea") from exc

        login = payload.get("login") if isinstance(payload, dict) else None
        if not isinstance(login, str) or not login:
            raise GiteaOAuthError("Gitea returned invalid user information")
        return GiteaOAuthUser(
            login=login,
            full_name=_optional_string(payload.get("full_name")),
            email=_optional_string(payload.get("email")),
            avatar_url=_optional_string(payload.get("avatar_url")),
            is_admin=payload.get("is_admin") is True,
        )


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
