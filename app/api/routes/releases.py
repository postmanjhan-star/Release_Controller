from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core.config import Settings, get_settings
from app.domain.orchestration.value_objects import BundleStatus
from app.domain.release.exceptions import (
    DuplicateReleaseError,
    InvalidReleaseTransitionError,
    ReleaseNotFoundError,
)
from app.domain.release.repositories import ReleaseFilters
from app.domain.release.value_objects import ReleaseStatus
from app.infrastructure.di.injection import (
    get_approve_release_usecase,
    get_create_release_usecase,
    get_finish_deployment_usecase,
    get_get_release_usecase,
    get_list_releases_usecase,
    get_publish_orchestrator,
    get_reject_release_usecase,
    get_release_orchestrator,
    get_start_deployment_usecase,
    require_user,
)
from app.integrations.drone.exceptions import (
    DroneAuthenticationError,
    DroneConnectionError,
    DroneError,
    DroneTimeoutError,
)
from app.schemas.orchestration import (
    BundlePromoteRequest,
    BundleResponse,
    UnifiedReleaseListResponse,
    WorkflowEventListResponse,
)
from app.schemas.publish import PublishRequest
from app.schemas.release import (
    ReleaseApprove,
    ReleaseCreate,
    ReleaseDeploymentFinish,
    ReleaseReject,
    ReleaseResponse,
)
from app.services.attachment_service import parse_payload_with_optional_attachment
from app.services.auth_service import CurrentUser, verified_actor
from app.services.component_registry import (
    ComponentNotRegisteredError,
    NoDefaultProjectError,
    ProjectNotRegisteredError,
)
from app.services.deployment_service import DuplicatePromotionError
from app.services.drone_build_service import (
    BuildNotFoundError,
    BuildNotPromotableError,
    DroneConfigurationError,
)
from app.services.publish_orchestrator import (
    DeploymentNotPublishableError,
    PublishOrchestrator,
    PublishStateError,
)
from app.services.publish_service import TagAlreadyExistsDifferentCommit
from app.services.release_orchestrator import (
    BundleNotFoundError,
    BundleNotRetryableError,
    ComponentNotSelectableError,
    ReleaseOrchestrator,
)
from app.usecase.release.approve_release_usecase import ApproveReleaseUseCase
from app.usecase.release.create_release_usecase import CreateReleaseUseCase
from app.usecase.release.finish_deployment_usecase import FinishDeploymentUseCase
from app.usecase.release.get_release_usecase import GetReleaseUseCase
from app.usecase.release.list_releases_usecase import ListReleasesUseCase
from app.usecase.release.reject_release_usecase import RejectReleaseUseCase
from app.usecase.release.start_deployment_usecase import StartDeploymentUseCase

router = APIRouter()


CreateRelease = Annotated[CreateReleaseUseCase, Depends(get_create_release_usecase)]
GetRelease = Annotated[GetReleaseUseCase, Depends(get_get_release_usecase)]
ListReleases = Annotated[ListReleasesUseCase, Depends(get_list_releases_usecase)]
ApproveRelease = Annotated[ApproveReleaseUseCase, Depends(get_approve_release_usecase)]
RejectRelease = Annotated[RejectReleaseUseCase, Depends(get_reject_release_usecase)]
StartDeployment = Annotated[StartDeploymentUseCase, Depends(get_start_deployment_usecase)]
FinishDeployment = Annotated[FinishDeploymentUseCase, Depends(get_finish_deployment_usecase)]


@router.post("", response_model=ReleaseResponse, status_code=status.HTTP_201_CREATED)
def create_release(payload: ReleaseCreate, usecase: CreateRelease) -> ReleaseResponse:
    try:
        return usecase.execute(
            repository=payload.repository,
            branch=payload.branch,
            commit_sha=payload.commit_sha,
            environment=payload.environment,
            message=payload.message,
        )
    except DuplicateReleaseError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Release already exists for this repository, commit, and environment",
        ) from exc


@router.get("", response_model=UnifiedReleaseListResponse)
def list_releases(
    usecase: ListReleases,
    orchestrator: ReleaseOrchestrator = Depends(get_release_orchestrator),
    repository: str | None = None,
    branch: str | None = None,
    environment: str | None = None,
    target: str | None = None,
    release_status: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UnifiedReleaseListResponse:
    legacy_status = None
    bundle_status = None
    if release_status is not None:
        try:
            legacy_status = ReleaseStatus(release_status)
        except ValueError:
            pass
        try:
            bundle_status = BundleStatus(release_status)
        except ValueError:
            pass
        if legacy_status is None and bundle_status is None:
            raise HTTPException(status_code=422, detail="Invalid release status")

    legacy_items, _legacy_total = usecase.execute(
        ReleaseFilters(
            repository=repository,
            branch=branch,
            environment=environment,
            status=legacy_status,
        ),
        limit=10000,
        offset=0,
    )
    bundle_items, _bundle_total = orchestrator.list_bundles(
        status=bundle_status,
        target=target,
        limit=10000,
        offset=0,
    )
    # Repository/branch/environment are legacy filters. Target is the v2.2
    # equivalent, so avoid returning records that could not have matched the
    # caller's chosen model.
    if target is not None or bundle_status is not None and legacy_status is None:
        legacy_items = []
    if (
        repository is not None
        or branch is not None
        or environment is not None
        or legacy_status is not None
        and bundle_status is None
    ):
        bundle_items = []
    items = [*legacy_items, *bundle_items]
    items.sort(key=lambda item: (item.created_at, item.id), reverse=True)
    total = len(items)
    return UnifiedReleaseListResponse(
        items=items[offset : offset + limit],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/promote", response_model=BundleResponse, status_code=status.HTTP_201_CREATED)
async def promote_bundle(
    request: Request,
    orchestrator: ReleaseOrchestrator = Depends(get_release_orchestrator),
    settings: Settings = Depends(get_settings),
    user: CurrentUser = Depends(require_user),
) -> BundleResponse:
    payload, attachment = await parse_payload_with_optional_attachment(
        request, BundlePromoteRequest, settings
    )
    try:
        return orchestrator.create_bundle(
            payload.model_copy(update={"requested_by": verified_actor(user, payload.requested_by)}),
            attachment=attachment,
        )
    except BuildNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Build not found") from exc
    except BuildNotPromotableError as exc:
        raise HTTPException(status_code=409, detail="Build is not promotable") from exc
    except DuplicatePromotionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (
        ComponentNotRegisteredError,
        ProjectNotRegisteredError,
        NoDefaultProjectError,
    ) as exc:
        # Naming something the registry does not have is a configuration answer,
        # not an upstream failure.
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ComponentNotSelectableError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (DroneConnectionError, DroneTimeoutError, DroneConfigurationError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except DroneAuthenticationError as exc:
        raise HTTPException(status_code=502, detail="Drone authentication failed") from exc
    except DroneError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/{release_id}/refresh", response_model=BundleResponse)
def refresh_bundle(
    release_id: str,
    orchestrator: ReleaseOrchestrator = Depends(get_release_orchestrator),
) -> BundleResponse:
    try:
        return orchestrator.refresh_bundle(release_id)
    except BundleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Release not found") from exc


@router.post("/{release_id}/retry", response_model=BundleResponse)
def retry_bundle(
    release_id: str,
    orchestrator: ReleaseOrchestrator = Depends(get_release_orchestrator),
) -> BundleResponse:
    """Re-run only the components of a partly-failed bundle that did not succeed."""
    try:
        return orchestrator.retry_bundle(release_id)
    except BundleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Release not found") from exc
    except BundleNotRetryableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BuildNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Build not found") from exc
    except (DroneConnectionError, DroneTimeoutError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except DroneAuthenticationError as exc:
        raise HTTPException(status_code=502, detail="Drone authentication failed") from exc
    except DroneError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/{release_id}/publish", response_model=BundleResponse)
def publish_bundle(
    release_id: str,
    payload: PublishRequest,
    orchestrator: PublishOrchestrator = Depends(get_publish_orchestrator),
) -> BundleResponse:
    try:
        return orchestrator.publish_bundle(release_id, payload)
    except BundleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Release not found") from exc
    except (DeploymentNotPublishableError, PublishStateError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TagAlreadyExistsDifferentCommit as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{release_id}/events", response_model=WorkflowEventListResponse)
def release_events(
    release_id: str,
    orchestrator: ReleaseOrchestrator = Depends(get_release_orchestrator),
) -> WorkflowEventListResponse:
    try:
        return WorkflowEventListResponse(items=orchestrator.list_events(release_id))
    except BundleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Release not found") from exc


@router.get("/{release_id}", response_model=ReleaseResponse | BundleResponse)
def get_release(
    release_id: str,
    usecase: GetRelease,
    orchestrator: ReleaseOrchestrator = Depends(get_release_orchestrator),
) -> ReleaseResponse | BundleResponse:
    try:
        return usecase.execute(release_id)
    except ReleaseNotFoundError:
        try:
            return orchestrator.get_bundle(release_id)
        except BundleNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Release not found") from exc


@router.post("/{release_id}/approve", response_model=ReleaseResponse)
def approve_release(
    release_id: str,
    payload: ReleaseApprove,
    usecase: ApproveRelease,
    user: CurrentUser = Depends(require_user),
) -> ReleaseResponse:
    try:
        return usecase.execute(
            release_id,
            approved_by=verified_actor(user, payload.approved_by),
        )
    except ReleaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Release not found") from exc
    except InvalidReleaseTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{release_id}/reject", response_model=ReleaseResponse)
def reject_release(
    release_id: str,
    payload: ReleaseReject,
    usecase: RejectRelease,
    user: CurrentUser = Depends(require_user),
) -> ReleaseResponse:
    try:
        return usecase.execute(
            release_id,
            rejected_by=verified_actor(user, payload.rejected_by),
            message=payload.message,
        )
    except ReleaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Release not found") from exc
    except InvalidReleaseTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{release_id}/deployment/start", response_model=ReleaseResponse)
def start_deployment(release_id: str, usecase: StartDeployment) -> ReleaseResponse:
    try:
        return usecase.execute(release_id)
    except ReleaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Release not found") from exc
    except InvalidReleaseTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{release_id}/deployment/finish", response_model=ReleaseResponse)
def finish_deployment(
    release_id: str,
    payload: ReleaseDeploymentFinish,
    usecase: FinishDeployment,
) -> ReleaseResponse:
    try:
        return usecase.execute(release_id, status=payload.status, message=payload.message)
    except ReleaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Release not found") from exc
    except InvalidReleaseTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
