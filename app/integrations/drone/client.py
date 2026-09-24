from typing import Any

import httpx

from app.integrations.drone.exceptions import (
    DroneAuthenticationError,
    DroneConnectionError,
    DroneNotFoundError,
    DronePromoteError,
    DroneTimeoutError,
    DroneUnexpectedResponseError,
)
from app.integrations.drone.schemas import DroneBuild, DroneRepositoryInfo
from app.integrations.retry import RetryPolicy, retry_read

# Reaching Drone can fail for reasons that say nothing about the repository or the
# build.  Those are worth another attempt; a 401 or a 404 is not.
RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    DroneConnectionError,
    DroneTimeoutError,
    DroneUnexpectedResponseError,
)


class DroneClient:
    def __init__(
        self,
        server: str,
        token: str,
        timeout: float = 10.0,
        *,
        connect_timeout: float | None = None,
        write_timeout: float | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.server = server.rstrip("/")
        self.timeout = timeout
        # One number for connect, read and write had to be short enough for a poll
        # and long enough for a promote at the same time.  They are separate
        # budgets now: connecting should be quick, promoting may not be.
        connect = connect_timeout if connect_timeout is not None else min(timeout, 3.0)
        self._read_timeout = httpx.Timeout(timeout, connect=connect)
        self._write_timeout = httpx.Timeout(
            write_timeout if write_timeout is not None else max(timeout, 30.0),
            connect=connect,
        )
        self._retry_policy = retry_policy or RetryPolicy()
        self._headers = {"Authorization": f"Bearer {token}"}

    def list_builds(self, owner: str, repo: str, limit: int = 20) -> list[DroneBuild]:
        payload = self._read(
            "GET",
            f"/api/repos/{owner}/{repo}/builds",
            description="list_builds",
            params={"limit": limit},
        )
        if not isinstance(payload, list):
            raise DroneUnexpectedResponseError("Drone returned an invalid build list")
        return [DroneBuild.from_drone(item) for item in payload]

    def get_repository(self, owner: str, repo: str) -> DroneRepositoryInfo:
        payload = self._read("GET", f"/api/repos/{owner}/{repo}", description="get_repository")
        if not isinstance(payload, dict):
            raise DroneUnexpectedResponseError("Drone returned invalid repository metadata")
        return DroneRepositoryInfo.from_drone(payload, owner, repo)

    def get_build(self, owner: str, repo: str, build_number: int) -> DroneBuild:
        payload = self._read(
            "GET",
            f"/api/repos/{owner}/{repo}/builds/{build_number}",
            description="get_build",
        )
        if not isinstance(payload, dict):
            raise DroneUnexpectedResponseError("Drone returned an invalid build")
        return DroneBuild.from_drone(payload)

    def promote_build(self, owner: str, repo: str, build_number: int, target: str) -> DroneBuild:
        """Create a promotion build.  Never retried: this call is not idempotent.

        A timeout here means the outcome is unknown, not that nothing happened, so
        recovery is the caller's reconciliation step rather than a second POST.
        """
        try:
            payload = self._request(
                "POST",
                f"/api/repos/{owner}/{repo}/builds/{build_number}/promote",
                timeout=self._write_timeout,
                params={"target": target},
            )
        except DroneNotFoundError as exc:
            raise DronePromoteError("Drone could not create the promotion build") from exc
        if not isinstance(payload, dict) or payload.get("number") is None:
            raise DronePromoteError("Drone returned an invalid promotion build")
        return DroneBuild.from_drone(payload)

    def status(self) -> bool:
        self._read("GET", "/api/user", description="status")
        return True

    def _read(self, method: str, path: str, *, description: str, **kwargs: Any) -> Any:
        return retry_read(
            lambda: self._request(method, path, **kwargs),
            policy=self._retry_policy,
            retry_on=RETRYABLE_ERRORS,
            description=f"drone.{description}",
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
            raise DroneTimeoutError("Drone request timed out") from exc
        except httpx.RequestError as exc:
            raise DroneConnectionError("Drone is unavailable") from exc
        if response.status_code in {401, 403}:
            raise DroneAuthenticationError("Drone authentication failed")
        if response.status_code == 404:
            raise DroneNotFoundError("Drone build was not found")
        if response.status_code >= 500:
            raise DroneConnectionError("Drone is unavailable")
        if response.status_code >= 400:
            raise DroneUnexpectedResponseError(f"Drone returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise DroneUnexpectedResponseError("Drone returned invalid JSON") from exc
