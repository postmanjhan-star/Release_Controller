import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.db.models.mcp import MCPToolInvocation
from app.mcp.security import redact, sanitize_mcp_arguments
from app.services.mcp_audit_service import MCPAuditService


def test_sanitize():
    result = sanitize_mcp_arguments(
        {
            "reason": "private credential",
            "approval_token": "secret",
            "nested": {"password": "secret"},
            "frontend": True,
        }
    )
    assert "secret" not in str(result)
    assert "private" not in str(result)
    assert result["frontend"] is True
    assert "raw-secret" not in str(
        redact({"logs": "Bearer raw-secret token=raw-secret", "Authorization": "raw-secret"})
    )
    assert redact("known-value", ["known-value"]) == "[REDACTED]"


def test_append_only_audit(client):
    service = MCPAuditService(client.session_factory)
    identifier, started = service.record(
        tool="get_release_status", arguments={}, actor="agent", client="stdio", status="started"
    )
    service.record(
        tool="get_release_status",
        arguments={},
        actor="agent",
        client="stdio",
        status="success",
        invocation_id=identifier,
        started_at=started,
    )
    with client.session_factory() as db:
        rows = list(db.scalars(select(MCPToolInvocation)))
        assert len(rows) == 2
        assert rows[0].invocation_id == rows[1].invocation_id
        for sql in [
            "DELETE FROM mcp_tool_invocations",
            "UPDATE mcp_tool_invocations SET actor = 'forged'",
        ]:
            with pytest.raises(IntegrityError):
                db.execute(text(sql))
            db.rollback()
