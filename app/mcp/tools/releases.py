from app.mcp import schemas as s
from app.mcp.tools.base import ToolSpec

TOOLS = [
    ToolSpec(
        "get_release_plan",
        s.PlanReference,
        "plans",
        "Read the saved plan, approval state and execution release ID.",
    ),
    ToolSpec(
        "get_release_status",
        s.Empty,
        "operations",
        "Inspect configured project release state. No deployment.",
    ),
    ToolSpec(
        "list_releases",
        s.ListInput,
        "operations",
        "List project release bundles, newest first. Status uses uppercase controller states.",
    ),
    ToolSpec(
        "get_release",
        s.ReleaseInput,
        "operations",
        "Read a project release bundle by its exact ID.",
    ),
    ToolSpec(
        "compare_environments",
        s.Empty,
        "operations",
        "Compare recorded staging and production commits. Does not infer Git ancestry.",
    ),
    ToolSpec(
        "get_release_candidates",
        s.Empty,
        "plans",
        "Read facts about staging versions and their source build outcomes.",
    ),
    ToolSpec(
        "create_release_plan",
        s.PlanInput,
        "plans",
        "Persist a reviewable release plan. NEVER deploys. Production requires separate human admin approval.",
        False,
    ),
]
