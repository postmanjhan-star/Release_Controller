import asyncio
import json

from fastapi.testclient import TestClient
from mcp.shared.memory import create_connected_server_and_client_session

from app.mcp.server import create_http_app, create_server
from tests.mcp.test_mcp_errors import dispatcher


def test_real_mcp_protocol_tools_resources_prompts(client, operations):
    async def run():
        server = create_server(dispatcher(client, operations))
        async with create_connected_server_and_client_session(server) as session:
            tools = (await session.list_tools()).tools
            assert len(tools) == 15
            assert not any("approve" in tool.name for tool in tools)
            response = await session.call_tool("list_releases", {"limit": 1})
            assert response.structuredContent["success"]
            assert not response.isError
            invalid = await session.call_tool("list_releases", {"limit": -1})
            assert invalid.isError
            assert invalid.structuredContent["error"]["code"] == "INVALID_ARGUMENT"
            denied = await session.call_tool(
                "promote_to_production", {"plan_id": "x", "reason": "release"}
            )
            assert denied.isError
            assert len((await session.list_resources()).resources) == 5
            data = await session.read_resource("release-controller://environment/staging")
            assert json.loads(data.contents[0].text)["data"]["healthy"] is None
            config = await session.read_resource("release-controller://config")
            assert "token" not in config.contents[0].text
            assert len((await session.list_prompts()).prompts) == 3
            prompt = await session.get_prompt("prepare-production-release")
            assert "DO NOT promote" in prompt.messages[0].content.text

    asyncio.run(run())


def test_http_authentication_initialize_and_call(client, operations):
    settings = operations.settings
    settings.mcp_api_token = "x" * 40
    settings.mcp_transport = "streamable-http"
    settings.mcp_allowed_hosts = "testserver"
    application = create_http_app(create_server(dispatcher(client, operations)), settings)
    with TestClient(application) as http:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        }
        assert http.post("/mcp/", json=payload).status_code == 401
        assert http.get("/mcp/", headers={"Authorization": "Bearer invalid"}).status_code == 401
        headers = {
            "Authorization": f"Bearer {settings.mcp_api_token}",
            "Accept": "application/json, text/event-stream",
        }
        response = http.post("/mcp/", headers=headers, json=payload)
        assert response.status_code == 200
        assert response.json()["result"]["serverInfo"]["name"] == "release-controller"
        response = http.post(
            "/mcp/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "list_releases", "arguments": {}},
            },
        )
        assert response.status_code == 200
        assert response.json()["result"]["structuredContent"]["success"]
        assert (
            http.post(
                "/mcp/", headers={**headers, "Host": "attacker.test"}, json=payload
            ).status_code
            == 421
        )


def test_stdio_process_initializes_without_stdout_logging(client):
    import os
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def run():
        env = dict(os.environ)
        env.update(
            DATABASE_URL=str(client.session_factory.kw["bind"].url),
            MCP_ENABLED="true",
            MCP_TRANSPORT="stdio",
            MCP_MODE="readonly",
        )
        parameters = StdioServerParameters(
            command=sys.executable, args=["-m", "app.mcp.server"], env=env
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                assert len((await session.list_tools()).tools) == 15
                response = await session.call_tool("list_releases", {})
                assert response.structuredContent["success"]

    asyncio.run(run())
