from app.mcp.tools import deployments, diagnostics, pipelines, releases, repositories

TOOLS = {
    tool.name: tool
    for module in (releases, deployments, pipelines, repositories, diagnostics)
    for tool in module.TOOLS
}
