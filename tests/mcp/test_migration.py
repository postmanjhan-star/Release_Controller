import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.core.config import get_settings


def test_upgrade_downgrade_and_audit_constraints(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'mcp-migration.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    config = Config("alembic.ini")
    command.upgrade(config, "20260903_0016")
    command.upgrade(config, "head")
    engine = create_engine(url)
    assert {"release_plans", "mcp_tool_invocations"} <= set(inspect(engine).get_table_names())
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO mcp_tool_invocations "
                "(id, invocation_id, tool_name, arguments, result_status, started_at, client, actor) "
                "VALUES ('1', '1', 'read', '{}', 'started', CURRENT_TIMESTAMP, 'stdio', 'agent')"
            )
        )
    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(text("DELETE FROM mcp_tool_invocations"))
    command.downgrade(config, "20260903_0016")
    assert "release_plans" not in inspect(engine).get_table_names()
    assert "deployments" in inspect(engine).get_table_names()
    command.upgrade(config, "head")
    engine.dispose()
    get_settings.cache_clear()
