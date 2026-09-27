import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.mcp.schemas import DeployInput, PlanInput, PromoteInput
from app.mcp.security import authorize
from app.services.release_operations_errors import MCPToolError


@pytest.mark.parametrize(
    "mode,tool,allowed",
    [
        ("readonly", "get_release_status", True),
        ("readonly", "list_releases", True),
        ("readonly", "create_release_plan", True),
        ("readonly", "deploy_staging", False),
        ("readonly", "promote_to_production", False),
        ("staging", "deploy_staging", True),
        ("staging", "promote_to_production", False),
        ("full", "promote_to_production", True),
    ],
)
def test_modes(mode, tool, allowed):
    settings = Settings(_env_file=None, mcp_enabled=True, mcp_mode=mode)
    if allowed:
        authorize(settings, tool)
    else:
        with pytest.raises(MCPToolError):
            authorize(settings, tool)


@pytest.mark.parametrize(
    "values",
    [
        {"mcp_mode": "all"},
        {"mcp_audit_enabled": False},
        {"mcp_transport": "streamable-http"},
        {"mcp_staging_target": "production"},
        {"mcp_staging_target": "same", "mcp_production_target": "same"},
    ],
)
def test_fail_closed_config(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, mcp_enabled=True, **values)


def test_strict_inputs():
    for schema, args in [
        (DeployInput, {"reason": " "}),
        (PlanInput, {"frontend": False, "backend": False}),
        (PlanInput, {"frontend": "false"}),
        (PromoteInput, {"plan_id": "plan", "reason": "release", "confirmed": True}),
    ]:
        with pytest.raises(ValidationError):
            schema.model_validate(args)
