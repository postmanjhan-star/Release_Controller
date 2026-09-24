from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status

from app.api.errors import registry_http_error, upstream_http_error
from app.core.config import Settings, get_settings
from app.domain.registry.exceptions import (
    ComponentInUseError,
    ComponentKeyTakenError,
    ComponentNotFoundError,
    ComponentSlotConflictError,
    ConnectionNotFoundError,
    ProjectKeyTakenError,
    ProjectNotFoundError,
)
from app.infrastructure.di.injection import (
    get_add_component_usecase,
    get_build_service,
    get_create_project_usecase,
    get_delete_component_usecase,
    get_get_project_usecase,
    get_list_projects_usecase,
    get_release_orchestrator,
    get_reorder_components_usecase,
    get_update_component_usecase,
    get_update_project_usecase,
    get_validate_project_usecase,
    require_admin,
    require_user,
)
from app.integrations.drone.exceptions import DroneError
from app.integrations.drone.schemas import DroneBuildResponse
from app.schemas.drone import DroneBuildListResponse, PromoteRequest
from app.schemas.orchestration import DeploymentResponse
from app.schemas.registry import (
    ComponentCreate,
    ComponentListResponse,
    ComponentReorder,
    ComponentResponse,
    ComponentUpdate,
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdate,
    ProjectValidateResponse,
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
    DroneBuildService,
    DroneConfigurationError,
)
from app.services.release_orchestrator import ComponentNotSelectableError, ReleaseOrchestrator
from app.usecase.registry.add_component_usecase import AddComponentUseCase
from app.usecase.registry.create_project_usecase import (
    CreateProjectUseCase,
    NewComponent,
    NewProject,
)
from app.usecase.registry.delete_component_usecase import DeleteComponentUseCase
from app.usecase.registry.get_project_usecase import GetProjectUseCase
from app.usecase.registry.list_projects_usecase import ListProjectsUseCase
from app.usecase.registry.reorder_components_usecase import ReorderComponentsUseCase
from app.usecase.registry.update_component_usecase import UpdateComponentUseCase
from app.usecase.registry.update_project_usecase import UpdateProjectUseCase
from app.usecase.registry.validate_project_usecase import ValidateProjectUseCase

router = APIRouter(prefix="/projects", tags=["projects"])


ListProjects = Annotated[ListProjectsUseCase, Depends(get_list_projects_usecase)]
GetProject = Annotated[GetProjectUseCase, Depends(get_get_project_usecase)]
CreateProject = Annotated[CreateProjectUseCase, Depends(get_create_project_usecase)]
UpdateProject = Annotated[UpdateProjectUseCase, Depends(get_update_project_usecase)]
AddComponent = Annotated[AddComponentUseCase, Depends(get_add_component_usecase)]
UpdateComponent = Annotated[UpdateComponentUseCase, Depends(get_update_component_usecase)]
DeleteComponent = Annotated[DeleteComponentUseCase, Depends(get_delete_component_usecase)]
ReorderComponents = Annotated[ReorderComponentsUseCase, Depends(get_reorder_components_usecase)]
ValidateProject = Annotated[ValidateProjectUseCase, Depends(get_validate_project_usecase)]


def _new_component(payload: ComponentCreate) -> NewComponent:
    """HTTP 的形狀翻成 use case 的形狀。翻譯是 presentation 的工作。"""
    return NewComponent(**payload.model_dump())


_NOT_FOUND = "Project not found"
_COMPONENT_NOT_FOUND = "Component not found"


def _conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.get("", response_model=ProjectListResponse)
def list_projects(
    usecase: ListProjects,
    include_archived: Annotated[bool, Query()] = False,
) -> ProjectListResponse:
    return ProjectListResponse(items=usecase.execute(include_archived=include_archived))


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
def create_project(payload: ProjectCreate, usecase: CreateProject) -> ProjectResponse:
    try:
        return usecase.execute(
            NewProject(
                key=payload.key,
                name=payload.name,
                default_target=payload.default_target,
                description=payload.description,
                drone_connection_id=payload.drone_connection_id,
                gitea_connection_id=payload.gitea_connection_id,
                components=[_new_component(c) for c in payload.components],
            )
        )
    except ProjectKeyTakenError as exc:
        raise _conflict(exc) from exc
    except ComponentSlotConflictError as exc:
        raise _unprocessable(exc) from exc
    except ConnectionNotFoundError as exc:
        raise _unprocessable(exc) from exc


@router.get("/{project_ref}", response_model=ProjectResponse)
def get_project(project_ref: str, usecase: GetProject) -> ProjectResponse:
    try:
        return usecase.execute(project_ref)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc


@router.patch(
    "/{project_ref}", response_model=ProjectResponse, dependencies=[Depends(require_admin)]
)
def update_project(
    project_ref: str, payload: ProjectUpdate, usecase: UpdateProject
) -> ProjectResponse:
    try:
        return usecase.execute(project_ref, **payload.model_dump())
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc
    except ComponentSlotConflictError as exc:
        raise _unprocessable(exc) from exc
    except ConnectionNotFoundError as exc:
        raise _unprocessable(exc) from exc


@router.post(
    "/{project_ref}/archive", response_model=ProjectResponse, dependencies=[Depends(require_admin)]
)
def archive_project(project_ref: str, usecase: UpdateProject) -> ProjectResponse:
    """Archive rather than delete.

    A project that has deployed is part of the deployment history, and that
    history has to keep making sense.
    """
    try:
        return usecase.set_archived(project_ref, True)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc


@router.post(
    "/{project_ref}/unarchive",
    response_model=ProjectResponse,
    dependencies=[Depends(require_admin)],
)
def unarchive_project(project_ref: str, usecase: UpdateProject) -> ProjectResponse:
    try:
        return usecase.set_archived(project_ref, False)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc


@router.post("/{project_ref}/validate", response_model=ProjectValidateResponse)
def validate_project(project_ref: str, usecase: ValidateProject) -> ProjectValidateResponse:
    """Check every active component against the real Drone and Gitea."""
    try:
        return usecase.execute(project_ref)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc


@router.get("/{project_ref}/components", response_model=ComponentListResponse)
def list_components(project_ref: str, usecase: GetProject) -> ComponentListResponse:
    try:
        return ComponentListResponse(items=usecase.execute(project_ref).components)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc


@router.post(
    "/{project_ref}/components",
    response_model=ComponentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
def add_component(
    project_ref: str, payload: ComponentCreate, usecase: AddComponent
) -> ComponentResponse:
    try:
        return usecase.execute(project_ref, _new_component(payload))
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc
    except ComponentKeyTakenError as exc:
        raise _conflict(exc) from exc
    except ComponentSlotConflictError as exc:
        raise _unprocessable(exc) from exc
    except ConnectionNotFoundError as exc:
        raise _unprocessable(exc) from exc


@router.patch(
    "/{project_ref}/components/{component_ref}",
    response_model=ComponentResponse,
    dependencies=[Depends(require_admin)],
)
def update_component(
    project_ref: str, component_ref: str, payload: ComponentUpdate, usecase: UpdateComponent
) -> ComponentResponse:
    try:
        return usecase.execute(project_ref, component_ref, **payload.model_dump())
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc
    except ComponentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_COMPONENT_NOT_FOUND) from exc
    except ComponentSlotConflictError as exc:
        raise _unprocessable(exc) from exc
    except ConnectionNotFoundError as exc:
        raise _unprocessable(exc) from exc


@router.delete(
    "/{project_ref}/components/{component_ref}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
def delete_component(project_ref: str, component_ref: str, usecase: DeleteComponent) -> None:
    try:
        usecase.execute(project_ref, component_ref)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc
    except ComponentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_COMPONENT_NOT_FOUND) from exc
    except ComponentInUseError as exc:
        raise _conflict(exc) from exc


@router.post(
    "/{project_ref}/components/reorder",
    response_model=ProjectResponse,
    dependencies=[Depends(require_admin)],
)
def reorder_components(
    project_ref: str, payload: ComponentReorder, usecase: ReorderComponents
) -> ProjectResponse:
    """Set the deployment order. The body must list every component exactly once."""
    try:
        return usecase.execute(project_ref, payload.component_ids)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc
    except ComponentNotFoundError as exc:
        raise _unprocessable(exc) from exc


# --------------------------------------------------------------------------- #
# Builds, by project and component
# --------------------------------------------------------------------------- #
#
# The /drone/{component}/builds pair these replace can only name a frontend or a
# backend, because its path parameter is the old two-value enum.  These take a
# component key, so they reach every component a project has.


def _component_for(usecase: GetProjectUseCase, project_ref: str, component_ref: str):
    try:
        return usecase.component(project_ref, component_ref)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc
    except ComponentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_COMPONENT_NOT_FOUND) from exc


@router.get(
    "/{project_ref}/components/{component_ref}/builds",
    response_model=DroneBuildListResponse,
)
def component_builds(
    project_ref: str,
    component_ref: str,
    usecase: GetProject,
    builds: DroneBuildService = Depends(get_build_service),
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DroneBuildListResponse:
    component = _component_for(usecase, project_ref, component_ref)
    try:
        repository = builds.get_repository_info(component)
        found = builds.list_builds(component, limit)
    except (DroneError, DroneConfigurationError) as exc:
        raise upstream_http_error(exc) from exc
    return DroneBuildListResponse(
        component=component.key,
        repository=repository,
        branches=list(
            dict.fromkeys(
                [build.branch for build in found if build.branch]
                + ([repository.default_branch] if repository.default_branch else [])
            )
        ),
        items=[DroneBuildResponse.from_build(build) for build in found],
    )


@router.post(
    "/{project_ref}/components/{component_ref}/builds/{build_number}/promote",
    response_model=DeploymentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def promote_component_build(
    request: Request,
    project_ref: str,
    component_ref: str,
    usecase: GetProject,
    build_number: Annotated[int, Path(gt=0)],
    orchestrator: ReleaseOrchestrator = Depends(get_release_orchestrator),
    settings: Settings = Depends(get_settings),
    user: CurrentUser = Depends(require_user),
) -> DeploymentResponse:
    """Deploy one component on its own, without creating a release bundle."""
    component = _component_for(usecase, project_ref, component_ref)
    payload, attachment = await parse_payload_with_optional_attachment(
        request, PromoteRequest, settings, allow_empty_payload=True
    )
    if not component.is_active:
        raise _unprocessable(
            ComponentNotSelectableError(
                f"Component {component.key!r} is inactive and cannot be deployed. "
                "Reactivate it under Projects first."
            )
        )
    try:
        return orchestrator.promote_standalone(
            component,
            build_number,
            payload.model_copy(update={"requested_by": verified_actor(user, payload.requested_by)}),
            attachment=attachment,
        )
    except BuildNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Build not found") from exc
    except BuildNotPromotableError as exc:
        raise _conflict(exc) from exc
    except DuplicatePromotionError as exc:
        raise _conflict(exc) from exc
    except (ComponentNotRegisteredError, NoDefaultProjectError, ProjectNotRegisteredError) as exc:
        raise registry_http_error(exc) from exc
    except (DroneError, DroneConfigurationError) as exc:
        raise upstream_http_error(exc) from exc
