from typing import Any
from urllib.parse import quote

import httpx

from app.integrations.gitea.exceptions import (
    GiteaAuthenticationError,
    GiteaConflictError,
    GiteaConnectionError,
    GiteaNotFoundError,
    GiteaReleaseError,
    GiteaTimeoutError,
    GiteaUnexpectedResponseError,
)
from app.integrations.gitea.schemas import GiteaRelease, GiteaRepository, GiteaTag
from app.integrations.retry import RetryPolicy, retry_read

# Only failures that carry no information about the resource are retried.  A 404
# is a real answer ("no such tag") that publish_service depends on receiving.
RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    GiteaConnectionError,
    GiteaTimeoutError,
)


class GiteaClient:
    health_path = "/api/healthz"

    def __init__(
        self,
        server: str,
        token: str = "",
        timeout: float = 10.0,
        *,
        connect_timeout: float | None = None,
        write_timeout: float | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.server = server.rstrip("/")
        self.timeout = timeout
        connect = connect_timeout if connect_timeout is not None else min(timeout, 3.0)
        self._read_timeout = httpx.Timeout(timeout, connect=connect)
        self._write_timeout = httpx.Timeout(
            write_timeout if write_timeout is not None else max(timeout, 30.0),
            connect=connect,
        )
        self._retry_policy = retry_policy or RetryPolicy()
        self._headers = {
            "Accept": "application/json",
            "Authorization": f"token {token}",
        }

    @property
    def health_url(self) -> str:
        return f"{self.server}{self.health_path}"

    def health(self) -> dict[str, Any]:
        try:
            response = httpx.get(self.health_url, timeout=self.timeout)
        except httpx.TimeoutException as exc:
            raise GiteaTimeoutError("Gitea health check timed out") from exc
        except httpx.RequestError as exc:
            raise GiteaConnectionError("Gitea is unavailable") from exc

        if response.status_code >= 500:
            raise GiteaConnectionError("Gitea is unavailable")
        if response.status_code >= 400:
            raise GiteaUnexpectedResponseError(
                f"Gitea health endpoint returned HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise GiteaUnexpectedResponseError(
                "Gitea health endpoint returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict) or payload.get("status") != "pass":
            raise GiteaUnexpectedResponseError("Gitea health endpoint did not report pass")
        return payload

    def get_repository(self, owner: str, repo: str) -> GiteaRepository:
        payload = self._read(
            "GET",
            f"/api/v1/repos/{quote(owner, safe='')}/{quote(repo, safe='')}",
            description="get_repository",
        )
        if not isinstance(payload, dict):
            raise GiteaUnexpectedResponseError("Gitea returned invalid repository metadata")
        return GiteaRepository.from_gitea(payload, owner, repo)

    def get_tag(self, owner: str, repo: str, tag_name: str) -> GiteaTag:
        payload = self._read(
            "GET",
            f"/api/v1/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/tags/{quote(tag_name, safe='')}",
            description="get_tag",
        )
        if not isinstance(payload, dict):
            raise GiteaUnexpectedResponseError("Gitea returned invalid tag metadata")
        try:
            return GiteaTag.from_gitea(payload)
        except ValueError as exc:
            raise GiteaUnexpectedResponseError(str(exc)) from exc

    def get_release(self, owner: str, repo: str, release_id: int | str) -> GiteaRelease:
        payload = self._read(
            "GET",
            f"/api/v1/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/releases/{release_id}",
            description="get_release",
        )
        return self._as_release(payload)

    def get_release_by_tag(self, owner: str, repo: str, tag_name: str) -> GiteaRelease:
        payload = self._read(
            "GET",
            f"/api/v1/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/releases/tags/{quote(tag_name, safe='')}",
            description="get_release_by_tag",
        )
        return self._as_release(payload)

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
        try:
            # Never retried: creating a release is not idempotent.  The caller
            # reconciles an uncertain outcome by re-reading the tag and release.
            payload = self._request(
                "POST",
                f"/api/v1/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/releases",
                timeout=self._write_timeout,
                json={
                    "tag_name": tag_name,
                    "target_commitish": target_commitish,
                    "name": name,
                    "body": body,
                    "draft": draft,
                    "prerelease": prerelease,
                },
            )
        except GiteaConflictError:
            raise
        except (
            GiteaAuthenticationError,
            GiteaNotFoundError,
            GiteaConnectionError,
            GiteaTimeoutError,
        ):
            raise
        except GiteaUnexpectedResponseError as exc:
            raise GiteaReleaseError(str(exc)) from exc
        return self._as_release(payload)

    def _read(self, method: str, path: str, *, description: str, **kwargs: Any) -> Any:
        return retry_read(
            lambda: self._request(method, path, **kwargs),
            policy=self._retry_policy,
            retry_on=RETRYABLE_ERRORS,
            description=f"gitea.{description}",
        )

    def _request(self, method: str, path: str, *, timeout: Any = None, **kwargs: Any) -> Any:
        try:
            response = httpx.request(
                method,
                f"{self.server}{path}",
                headers=self._headers,
                timeout=self._read_timeout if timeout is None else timeout,
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            raise GiteaTimeoutError("Gitea request timed out") from exc
        except httpx.RequestError as exc:
            raise GiteaConnectionError("Gitea is unavailable") from exc
        if response.status_code in {401, 403}:
            raise GiteaAuthenticationError("Gitea authentication failed")
        if response.status_code == 404:
            raise GiteaNotFoundError("Gitea resource was not found")
        if response.status_code == 409:
            raise GiteaConflictError("Gitea reported a release conflict")
        if response.status_code >= 500:
            raise GiteaConnectionError("Gitea is unavailable")
        if response.status_code >= 400:
            raise GiteaUnexpectedResponseError(f"Gitea returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise GiteaUnexpectedResponseError("Gitea returned invalid JSON") from exc

    @staticmethod
    def _as_release(payload: Any) -> GiteaRelease:
        if not isinstance(payload, dict):
            raise GiteaUnexpectedResponseError("Gitea returned invalid release metadata")
        try:
            return GiteaRelease.from_gitea(payload)
        except ValueError as exc:
            raise GiteaUnexpectedResponseError(str(exc)) from exc
