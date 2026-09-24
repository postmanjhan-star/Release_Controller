"""把 service 例外翻成 HTTP 回應的共用對照。

原本住在 `routes/drone.py`，但只有 `routes/projects.py` 在用——route 之間
互相 import 私有 helper 是這裡要消掉的耦合之一。
"""

from fastapi import HTTPException, status

from app.integrations.drone.exceptions import (
    DroneAuthenticationError,
    DroneConnectionError,
    DroneTimeoutError,
)
from app.services.drone_build_service import DroneConfigurationError


def registry_http_error(exc: Exception) -> HTTPException:
    """A component that is not configured is a 404 about configuration, not a 502."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def upstream_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (DroneConnectionError, DroneTimeoutError, DroneConfigurationError)):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, DroneAuthenticationError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Drone authentication failed"
        )
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
