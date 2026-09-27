"""Safe application errors shared by the MCP adapter and approval API."""

import logging
from enum import Enum

from pydantic import ValidationError

logger = logging.getLogger(__name__)


class ErrorCode(str, Enum):
    NOT_FOUND = "NOT_FOUND"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    CONFLICT = "CONFLICT"
    PIPELINE_FAILED = "PIPELINE_FAILED"
    DEPLOYMENT_FAILED = "DEPLOYMENT_FAILED"
    EXTERNAL_SERVICE_ERROR = "EXTERNAL_SERVICE_ERROR"
    UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"


class MCPToolError(Exception):
    def __init__(self, code: ErrorCode, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def response(self) -> dict:
        return {
            "success": False,
            "error": {
                "code": self.code.value,
                "message": self.message,
                "details": self.details,
            },
        }


def safe_error(exc):
    from app.integrations.drone.exceptions import DroneError
    from app.integrations.gitea.exceptions import GiteaError
    from app.services.component_registry import (
        ComponentNotRegisteredError,
        NoDefaultProjectError,
        ProjectNotRegisteredError,
    )
    from app.services.deployment_service import DeploymentNotFoundError, DuplicatePromotionError
    from app.services.drone_build_service import BuildNotFoundError, BuildNotPromotableError
    from app.services.release_orchestrator import BundleNotFoundError, ComponentNotSelectableError

    if isinstance(exc, MCPToolError):
        return exc
    if isinstance(exc, ValidationError):
        return MCPToolError(ErrorCode.INVALID_ARGUMENT, "Invalid tool arguments.")
    if isinstance(
        exc,
        (
            BuildNotFoundError,
            BundleNotFoundError,
            DeploymentNotFoundError,
            ComponentNotRegisteredError,
            NoDefaultProjectError,
            ProjectNotRegisteredError,
        ),
    ):
        return MCPToolError(
            ErrorCode.NOT_FOUND, "Requested record or configured component was not found."
        )
    if isinstance(exc, BuildNotPromotableError):
        return MCPToolError(ErrorCode.PIPELINE_FAILED, "Source build is not promotable.")
    if isinstance(exc, (DuplicatePromotionError, ComponentNotSelectableError)):
        return MCPToolError(
            ErrorCode.CONFLICT, "Deployment conflicts with current state or configuration."
        )
    if isinstance(exc, (DroneError, GiteaError)):
        return MCPToolError(
            ErrorCode.EXTERNAL_SERVICE_ERROR, "An upstream service could not complete the request."
        )
    # Exception messages can contain credentials or SQL parameters. Log type only.
    logger.error("MCP operation failed: %s", type(exc).__name__)
    return MCPToolError(
        ErrorCode.DEPLOYMENT_FAILED, "Operation could not complete; inspect server audit."
    )
