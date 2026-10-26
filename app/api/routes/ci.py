"""Narrow bearer-only API; deliberately outside the session-protected router."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models.ci import CIDeploymentEvent, CIDeploymentRequest, CIServiceToken
from app.db.models.orchestration import Deployment
from app.db.session import get_db
from app.services.ci_deployment_service import CIDeploymentService, CIError, authorize
from app.services.ci_tokens import authenticate


class SafeCIRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse(status_code=422, content={"detail": "Invalid CI request"})
            except HTTPException:
                raise
            except Exception:
                logging.getLogger(__name__).error("CI request failed")
                return JSONResponse(status_code=500, content={"detail": "Internal server error"})

        return safe_handler


router = APIRouter(prefix="/ci/deployments", tags=["ci"], route_class=SafeCIRoute)


class CIRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_key: str = Field(min_length=1, max_length=50)
    component_key: str = Field(min_length=1, max_length=50)
    source_build_number: int = Field(gt=0, strict=True)
    target: str = Field(min_length=1, max_length=100)


class CIEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    status: str
    error_code: str | None
    created_at: datetime


class CIResponse(BaseModel):
    request_id: str
    status_url: str
    status: str
    service_token_id: str
    source_build_number: int
    commit_sha: str | None
    target: str
    deployment_id: str | None
    promotion_build_number: int | None
    error_code: str | None
    deadline_at: datetime
    events: list[CIEventResponse]


def require_ci_token(request: Request, db: Session = Depends(get_db)) -> CIServiceToken:
    authorization = request.headers.get("authorization", "")
    scheme, _, raw = authorization.partition(" ")
    token = authenticate(db, raw) if scheme.lower() == "bearer" and 0 < len(raw) <= 256 else None
    if token is None:
        raise HTTPException(
            status_code=401,
            detail="CI authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


def response_for(db: Session, job: CIDeploymentRequest) -> CIResponse:
    deployment = db.get(Deployment, job.deployment_id) if job.deployment_id else None
    events = db.scalars(
        select(CIDeploymentEvent)
        .where(CIDeploymentEvent.request_id == job.id)
        .order_by(CIDeploymentEvent.created_at, CIDeploymentEvent.id)
    ).all()
    return CIResponse(
        request_id=job.id,
        status_url=f"/api/v1/ci/deployments/{job.id}",
        status=job.status,
        service_token_id=job.service_token_id,
        source_build_number=job.source_build_number,
        commit_sha=job.commit_sha,
        target=job.target,
        deployment_id=job.deployment_id,
        promotion_build_number=deployment.promotion_build_number if deployment else None,
        error_code=job.error_code,
        deadline_at=job.deadline_at,
        events=events,
    )


@router.post("", status_code=202, response_model=CIResponse)
def accept_deployment(
    payload: CIRequest,
    token: CIServiceToken = Depends(require_ci_token),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CIResponse:
    try:
        job = CIDeploymentService(db, settings).accept(token, **payload.model_dump())
        return response_for(db, job)
    except CIError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from None


@router.get("/{request_id}", response_model=CIResponse)
def deployment_status(
    request_id: str,
    token: CIServiceToken = Depends(require_ci_token),
    db: Session = Depends(get_db),
) -> CIResponse:
    job = db.get(CIDeploymentRequest, request_id)
    if job is None:
        raise HTTPException(status_code=404, detail="CI request not found")
    try:
        authorize(token, job.project_id, job.component_id, job.target, job.branch)
    except CIError:
        raise HTTPException(status_code=403, detail="CI_SCOPE_FORBIDDEN") from None
    return response_for(db, job)
