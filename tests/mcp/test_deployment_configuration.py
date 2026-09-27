from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_mcp_compose_process_shares_database_without_running_migrations_or_worker():
    for name in ["docker-compose.yml", "compose.portainer.yml"]:
        services = yaml.safe_load((ROOT / name).read_text())["services"]
        api, mcp = services["release-controller"], services["release-controller-mcp"]
        assert api["image"] == mcp["image"]
        assert api["volumes"] == mcp["volumes"]
        assert mcp["command"] == ["python", "-m", "app.mcp.server"]
        assert mcp["profiles"] == ["mcp"]
        assert mcp["environment"]["BACKGROUND_WORKER_ENABLED"] == "false"
        assert mcp["environment"]["MCP_MODE"] == "${MCP_MODE:-readonly}"
        assert mcp["environment"]["DATABASE_URL"] == "sqlite:////data/release.db"
        assert mcp["depends_on"]["release-controller"]["condition"] == "service_healthy"
        assert mcp["ports"][0].startswith("127.0.0.1:")
