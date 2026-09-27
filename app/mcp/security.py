import hmac

from app.core.config import Settings
from app.core.redaction import redact
from app.services.release_operations_errors import ErrorCode, MCPToolError

WRITE_TOOLS = {"deploy_staging", "promote_to_production", "rollback"}


def sanitize_mcp_arguments(arguments: dict) -> dict:
    # Keep typed operational selectors, never arbitrary free text, including
    # rejected/unknown arguments. Validation errors must not echo their inputs.
    safe = {
        "environment",
        "target_environment",
        "frontend",
        "backend",
        "limit",
        "offset",
        "status",
        "repository",
        "build_number",
        "release_id",
        "target_release_id",
        "plan_id",
    }
    return {
        key: redact(value) if key in safe and not isinstance(value, (dict, list)) else "[REDACTED]"
        for key, value in arguments.items()
    }


def authorize(settings: Settings, tool: str) -> None:
    if not settings.mcp_enabled or not settings.mcp_audit_enabled:
        raise MCPToolError(ErrorCode.PERMISSION_DENIED, "MCP is disabled or audit is unavailable.")
    if tool in WRITE_TOOLS:
        allowed = settings.mcp_mode == "full" or (
            settings.mcp_mode == "staging" and tool == "deploy_staging"
        )
        if not allowed:
            raise MCPToolError(ErrorCode.PERMISSION_DENIED, "This tool is forbidden in MCP_MODE.")


class BearerAuthMiddleware:
    """Authenticate every HTTP request, including initialize and resource reads."""

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            supplied = headers.get(b"authorization", b"").decode("latin-1")
            expected = f"Bearer {self.token}"
            if not self.token or not hmac.compare_digest(supplied.encode(), expected.encode()):
                from starlette.responses import JSONResponse

                response = JSONResponse(
                    {"error": "Authentication required"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
