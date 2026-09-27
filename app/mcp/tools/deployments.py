from app.mcp import schemas as s
from app.mcp.tools.base import ToolSpec

TOOLS = [
    ToolSpec(
        "get_environment_status",
        s.EnvironmentInput,
        "operations",
        "Read controller deployment state; live health checks are unavailable.",
    ),
    ToolSpec(
        "deploy_staging",
        s.DeployInput,
        "plans",
        "Deploy latest observed default-branch push builds to staging. Requires staging/full mode.",
        False,
    ),
    ToolSpec(
        "promote_to_production",
        s.PromoteInput,
        "plans",
        "Execute the exact server-side human-approved plan once. Requires full mode. Cannot approve or change versions.",
        False,
    ),
    ToolSpec(
        "rollback",
        s.RollbackInput,
        "unsupported",
        "Report rollback capability for an explicit target release. Currently unsupported.",
        False,
    ),
]
