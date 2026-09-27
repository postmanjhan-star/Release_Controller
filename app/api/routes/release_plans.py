"""Human-only review and approval; deliberately absent from the MCP tool catalog."""

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.infrastructure.di.injection import get_build_service, require_user
from app.services.auth_service import CurrentUser
from app.services.drone_build_service import DroneBuildService
from app.services.mcp_audit_service import MCPAuditService
from app.services.release_operations_errors import ErrorCode, MCPToolError, safe_error
from app.services.release_operations_service import ReleaseOperationsService
from app.services.release_plan_service import ReleasePlanService

router = APIRouter(prefix="/release-plans", tags=["release plans"])


def plan_service(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    builds: DroneBuildService = Depends(get_build_service),
):
    return ReleasePlanService(ReleaseOperationsService(db, builds, settings))


def require_approver(
    user: CurrentUser = Depends(require_user), settings: Settings = Depends(get_settings)
):
    if not settings.auth_enabled or not user.is_admin or user.login == "authentication-disabled":
        raise HTTPException(
            403, "An authenticated administrator must review and approve release plans."
        )
    return user


def http_error(exc):
    codes = {
        ErrorCode.NOT_FOUND: 404,
        ErrorCode.PERMISSION_DENIED: 403,
        ErrorCode.INVALID_ARGUMENT: 422,
        ErrorCode.CONFLICT: 409,
        ErrorCode.PIPELINE_FAILED: 409,
    }
    return HTTPException(codes.get(exc.code, 503), detail=exc.response()["error"])


@router.get("/{plan_id}")
def review_plan(plan_id: str, service=Depends(plan_service), user=Depends(require_approver)):
    try:
        return service.ops.redact_output(service.view(service.get(plan_id)))
    except MCPToolError as exc:
        raise http_error(exc) from exc


@router.post("/{plan_id}/approve")
def approve_plan(
    plan_id: str,
    x_release_plan_digest: str = Header(pattern=r"^[a-f0-9]{64}$"),
    service=Depends(plan_service),
    user=Depends(require_approver),
):
    # A custom header is required so cross-origin HTML forms cannot approve using
    # a browser's session cookie. No wildcard CORS is enabled for this endpoint.
    audit = MCPAuditService(sessionmaker(bind=service.db.get_bind()))
    invocation, started = audit.record(
        tool="human_approve_release_plan",
        arguments={"plan_id": plan_id, "digest": x_release_plan_digest},
        actor=user.login,
        client="authenticated-session",
        status="started",
    )
    status = "success"
    try:
        result = service.approve(plan_id, x_release_plan_digest, user)
    except Exception as exc:
        error = safe_error(exc)
        status = error.code.value
        raise http_error(error) from exc
    finally:
        service.db.rollback()
        audit.record(
            tool="human_approve_release_plan",
            arguments={"plan_id": plan_id, "digest": x_release_plan_digest},
            actor=user.login,
            client="authenticated-session",
            status=status,
            invocation_id=invocation,
            started_at=started,
        )
    return service.ops.redact_output(result)
