from app.mcp import schemas as s
from app.mcp.tools.base import ToolSpec

TOOLS = [
    ToolSpec(
        "get_repository_state",
        s.RepositoryInput,
        "operations",
        "Read repository metadata and latest commit observed by Drone; not a Git HEAD guarantee.",
    )
]
