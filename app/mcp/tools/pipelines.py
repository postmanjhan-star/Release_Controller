from app.mcp import schemas as s
from app.mcp.tools.base import ToolSpec

TOOLS = [
    ToolSpec(
        "get_pipeline_status",
        s.PipelineInput,
        "operations",
        "Read a Drone build for a component key registered in the configured project.",
    )
]
