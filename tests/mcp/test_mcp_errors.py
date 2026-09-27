from unittest.mock import Mock

from sqlalchemy import select

from app.db.models.mcp import MCPToolInvocation
from app.integrations.drone.exceptions import DroneConnectionError
from app.mcp.service import MCPService
from app.services.release_operations_service import ReleaseOperationsService


def dispatcher(client, operations):
    return MCPService(
        operations.settings,
        client.session_factory,
        lambda db, settings: ReleaseOperationsService(db, operations.builds, settings),
    )


def test_invalid_unknown_denied_and_upstream_errors_are_audited(client, operations):
    service = dispatcher(client, operations)
    cases = [
        ("list_releases", {"limit": 101}, "INVALID_ARGUMENT"),
        ("unknown", {"password": "private"}, "UNSUPPORTED_OPERATION"),
        ("deploy_staging", {"reason": "private"}, "PERMISSION_DENIED"),
    ]
    for tool, arguments, code in cases:
        result = service.invoke(tool, arguments)
        assert result["error"]["code"] == code
        assert "private" not in str(result)
    operations.builds.get_build.side_effect = DroneConnectionError("Bearer private")
    assert (
        service.invoke("get_pipeline_status", {"repository": "backend", "build_number": 42})[
            "error"
        ]["code"]
        == "EXTERNAL_SERVICE_ERROR"
    )
    with client.session_factory() as db:
        rows = list(db.scalars(select(MCPToolInvocation)))
        assert len(rows) == 8
        assert "private" not in str([r.arguments for r in rows])


def test_audit_failure_never_starts_operation(client, operations):
    service = dispatcher(client, operations)
    service.audit = Mock()
    service.audit.record.side_effect = RuntimeError("private")
    assert service.invoke("get_release_status", {})["error"]["code"] == "PERMISSION_DENIED"
    operations.builds.list_builds.assert_not_called()


def test_output_redacts_registered_and_configured_secrets(client, operations):
    service = dispatcher(client, operations)
    operations.settings.drone_token = "never-leak-this"
    operations.builds.list_builds.return_value[0].commit_sha = "never-leak-this"
    assert "never-leak-this" not in str(service.invoke("get_release_status", {}))


def test_unexpected_error_does_not_leak_exception(client, operations):
    service = dispatcher(client, operations)
    operations.builds.list_builds.side_effect = RuntimeError("private traceback")
    result = service.invoke("get_release_status", {})
    assert result["error"]["code"] == "DEPLOYMENT_FAILED"
    assert "private" not in str(result)


def test_audit_redacts_registry_secret_even_in_invalid_selector(client, operations):
    from app.core.crypto import token_cipher
    from app.db.models.registry import UpstreamConnection

    connection = operations.db.scalar(
        select(UpstreamConnection).where(UpstreamConnection.kind == "drone")
    )
    connection.token_encrypted = token_cipher(operations.settings).encrypt("audit-private-value")
    operations.db.commit()
    service = dispatcher(client, operations)
    service.invoke("get_pipeline_status", {"repository": "audit-private-value", "build_number": 42})
    with client.session_factory() as db:
        rows = list(db.scalars(select(MCPToolInvocation)))
        assert len(rows) == 2
        assert all(row.arguments["repository"] == "[REDACTED]" for row in rows)
