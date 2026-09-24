from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.domain.orchestration.value_objects import ActorSource, DeploymentStatus
from app.infrastructure.di.injection import (
    get_build_service,
    get_deployment_service,
    get_publish_orchestrator,
    require_user,
)
from app.schemas.orchestration import (
    DeploymentListResponse,
    DeploymentResponse,
    WorkflowEventListResponse,
)
from app.schemas.publish import PublishRequest
from app.services.auth_service import CurrentUser, verified_actor
from app.services.deployment_service import (
    DeploymentFilters,
    DeploymentNotFoundError,
    DeploymentService,
    DuplicatePromotionError,
)
from app.services.drone_build_service import DroneBuildService
from app.services.publish_orchestrator import (
    DeploymentNotPublishableError,
    PublishOrchestrator,
    PublishStateError,
)
from app.services.publish_service import TagAlreadyExistsDifferentCommit
from app.services.release_orchestrator import ReleaseOrchestrator

router = APIRouter(prefix="/deployments", tags=["deployments"])


@router.get("", response_model=DeploymentListResponse)
def list_deployments(
    service: DeploymentService = Depends(get_deployment_service),
    component: Annotated[str | None, Query(max_length=50)] = None,
    deployment_status: Annotated[DeploymentStatus | None, Query(alias="status")] = None,
    target: str | None = None,
    release_bundle_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DeploymentListResponse:
    items, total = service.list(
        DeploymentFilters(
            component=component,
            status=deployment_status,
            target=target,
            release_bundle_id=release_bundle_id,
        ),
        limit=limit,
        offset=offset,
    )
    return DeploymentListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{deployment_id}", response_model=DeploymentResponse)
def get_deployment(
    deployment_id: str,
    service: DeploymentService = Depends(get_deployment_service),
) -> DeploymentResponse:
    try:
        return service.get(deployment_id)
    except DeploymentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Deployment not found") from exc


@router.get("/{deployment_id}/events", response_model=WorkflowEventListResponse)
def deployment_events(
    deployment_id: str,
    service: DeploymentService = Depends(get_deployment_service),
) -> WorkflowEventListResponse:
    try:
        return WorkflowEventListResponse(items=service.list_events(deployment_id))
    except DeploymentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Deployment not found") from exc


@router.post("/{deployment_id}/publish", response_model=DeploymentResponse)
def publish_deployment(
    deployment_id: str,
    payload: PublishRequest,
    orchestrator: PublishOrchestrator = Depends(get_publish_orchestrator),
) -> DeploymentResponse:
    try:
        return orchestrator.publish_standalone(deployment_id, payload)
    except DeploymentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Deployment not found") from exc
    except (DeploymentNotPublishableError, PublishStateError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TagAlreadyExistsDifferentCommit as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{deployment_id}/refresh", response_model=DeploymentResponse)
def refresh_deployment(
    deployment_id: str,
    db: Session = Depends(get_db),
    builds: DroneBuildService = Depends(get_build_service),
    settings: Settings = Depends(get_settings),
    user: CurrentUser = Depends(require_user),
) -> DeploymentResponse:
    try:
        return ReleaseOrchestrator(
            db,
            builds,
            settings,
            actor=verified_actor(user),
            actor_source=ActorSource.SESSION.value,
        ).refresh_standalone(deployment_id)
    except DeploymentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Deployment not found") from exc
    except DuplicatePromotionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
