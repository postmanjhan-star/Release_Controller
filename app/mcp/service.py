"""Composition and audited dispatch. Business decisions live in application services."""

from app.mcp.security import authorize, sanitize_mcp_arguments
from app.mcp.tools import TOOLS
from app.services.component_registry import ComponentRegistry
from app.services.drone_build_service import DroneBuildService
from app.services.mcp_audit_service import MCPAuditService
from app.services.release_diagnostics_service import ReleaseDiagnosticsService
from app.services.release_operations_errors import ErrorCode, MCPToolError, safe_error
from app.services.release_operations_service import ReleaseOperationsService
from app.services.release_plan_service import ReleasePlanService
from app.services.upstream_clients import RegistryDroneClients


def build_operations(db, settings):
    registry = ComponentRegistry(db, settings)
    builds = DroneBuildService(RegistryDroneClients(registry, settings), settings)
    return ReleaseOperationsService(db, builds, settings)


class MCPService:
    def __init__(self, settings, sessions, operations_factory=build_operations):
        self.settings = settings
        self.sessions = sessions
        self.operations_factory = operations_factory
        self.audit = MCPAuditService(sessions)

    def invoke(self, name, arguments, client="stdio"):
        # Redact known configured/registry credentials even if a caller puts a
        # secret in a nominally safe selector, including rejected arguments.
        # This preliminary read never contacts upstream or starts a deployment.
        try:
            with self.sessions() as db:
                operations = self.operations_factory(db, self.settings)
                audit_args = operations.redact_output(sanitize_mcp_arguments(arguments))
            invocation, started = self.audit.record(
                tool=name,
                arguments=audit_args,
                actor=self.settings.mcp_actor,
                client=client,
                status="started",
            )
        except Exception:
            return MCPToolError(
                ErrorCode.PERMISSION_DENIED, "Audit unavailable; operation was not started."
            ).response()
        try:
            authorize(self.settings, name)
            spec = TOOLS.get(name)
            if spec is None:
                raise MCPToolError(ErrorCode.UNSUPPORTED_OPERATION, "Unknown tool.")
            payload = spec.schema.model_validate(arguments).model_dump()
            with self.sessions() as db:
                operations = self.operations_factory(db, self.settings)
                services = {
                    "operations": operations,
                    "plans": ReleasePlanService(operations),
                    "diagnostics": ReleaseDiagnosticsService(operations),
                }
                if spec.service == "unsupported":
                    data = {
                        "supported": False,
                        "reason": "Rollback workflow has not been implemented.",
                    }
                else:
                    data = getattr(services[spec.service], name)(**payload)
                result = {"success": True, "data": operations.redact_output(data)}
            status = "success"
        except Exception as exc:
            error = safe_error(exc)
            result, status = error.response(), error.code.value
        try:
            self.audit.record(
                tool=name,
                arguments=audit_args,
                actor=self.settings.mcp_actor,
                client=client,
                status=status,
                invocation_id=invocation,
                started_at=started,
            )
        except Exception:
            return MCPToolError(
                ErrorCode.DEPLOYMENT_FAILED,
                "Completion audit unavailable; execution may have occurred. Do not retry automatically.",
                {"invocation_id": invocation},
            ).response()
        return result
