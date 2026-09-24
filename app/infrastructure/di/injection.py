"""FastAPI 的相依組裝，全專案唯一的一處。

session -> registry -> upstream client -> service -> orchestrator -> handler
在這裡串起來。Route 只從這個模組取得組好的物件，因此不再需要互相 import
（在此之前 `releases.py` 要跟 `deployments.py` 借 publish orchestrator，
`projects.py`／`schedules.py`／`deployments.py` 要跟 `drone.py` 借 build
service，`drone.py` 等於身兼 DI 容器與 API 模組）。

換掉任何一層的實作只需要改這個檔案。
"""

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.domain.orchestration.value_objects import ActorSource
from app.domain.registry.repositories import (
    ComponentChecker,
    ComponentUsage,
    ConnectionProbe,
    ConnectionRepository,
    ConnectionUsage,
    DroneConnectionResolver,
    ProjectRepository,
    TokenVault,
)
from app.domain.release.repositories import ReleaseRepository, ReleaseWorkflowGateway
from app.domain.shared.unit_of_work import UnitOfWork
from app.infrastructure.crypto.token_vault import FernetTokenVault
from app.infrastructure.sqlite.registry.component_usage import SqlAlchemyComponentUsage
from app.infrastructure.sqlite.registry.connection_repository import (
    SqlAlchemyConnectionRepository,
)
from app.infrastructure.sqlite.registry.connection_usage import SqlAlchemyConnectionUsage
from app.infrastructure.sqlite.registry.drone_connection_resolver import (
    ConnectionRegistryResolver,
)
from app.infrastructure.sqlite.registry.project_repository import SqlAlchemyProjectRepository
from app.infrastructure.sqlite.release.release_repository import SqlAlchemyReleaseRepository
from app.infrastructure.sqlite.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.upstream.component_checker import UpstreamComponentChecker
from app.infrastructure.upstream.connection_probe import UpstreamConnectionProbe
from app.integrations.drone.client import DroneClient
from app.integrations.factory import build_drone_client, build_gitea_client
from app.integrations.gitea.client import GiteaClient
from app.services.auth_service import AuthService, CurrentUser, verified_actor
from app.services.component_registry import ComponentRegistry
from app.services.deployment_service import DeploymentService
from app.services.drone_build_service import DroneBuildService
from app.services.publish_orchestrator import PublishOrchestrator
from app.services.publish_service import PublishService
from app.services.release_orchestrator import ReleaseOrchestrator
from app.services.schedule_service import ScheduleService
from app.services.upstream_clients import (
    DroneClients,
    GiteaClients,
    RegistryDroneClients,
    RegistryGiteaClients,
)
from app.services.workflow_service import WorkflowService
from app.usecase.registry.add_component_usecase import (
    AddComponentUseCase,
    new_add_component_usecase,
)
from app.usecase.registry.create_connection_usecase import (
    CreateConnectionUseCase,
    new_create_connection_usecase,
)
from app.usecase.registry.create_project_usecase import (
    CreateProjectUseCase,
    new_create_project_usecase,
)
from app.usecase.registry.delete_component_usecase import (
    DeleteComponentUseCase,
    new_delete_component_usecase,
)
from app.usecase.registry.delete_connection_usecase import (
    DeleteConnectionUseCase,
    new_delete_connection_usecase,
)
from app.usecase.registry.get_connection_usecase import (
    GetConnectionUseCase,
    new_get_connection_usecase,
)
from app.usecase.registry.get_project_usecase import GetProjectUseCase, new_get_project_usecase
from app.usecase.registry.list_connections_usecase import (
    ListConnectionsUseCase,
    new_list_connections_usecase,
)
from app.usecase.registry.list_projects_usecase import (
    ListProjectsUseCase,
    new_list_projects_usecase,
)
from app.usecase.registry.reorder_components_usecase import (
    ReorderComponentsUseCase,
    new_reorder_components_usecase,
)
from app.usecase.registry.resolve_connection_usecase import (
    ResolveConnectionUseCase,
    new_resolve_connection_usecase,
)
from app.usecase.registry.test_connection_usecase import (
    TestConnectionUseCase,
    new_test_connection_usecase,
)
from app.usecase.registry.update_component_usecase import (
    UpdateComponentUseCase,
    new_update_component_usecase,
)
from app.usecase.registry.update_connection_usecase import (
    UpdateConnectionUseCase,
    new_update_connection_usecase,
)
from app.usecase.registry.update_project_usecase import (
    UpdateProjectUseCase,
    new_update_project_usecase,
)
from app.usecase.registry.validate_project_usecase import (
    ValidateProjectUseCase,
    new_validate_project_usecase,
)
from app.usecase.release.approve_release_usecase import (
    ApproveReleaseUseCase,
    new_approve_release_usecase,
)
from app.usecase.release.create_release_usecase import (
    CreateReleaseUseCase,
    new_create_release_usecase,
)
from app.usecase.release.finish_deployment_usecase import (
    FinishDeploymentUseCase,
    new_finish_deployment_usecase,
)
from app.usecase.release.get_release_usecase import GetReleaseUseCase, new_get_release_usecase
from app.usecase.release.list_releases_usecase import (
    ListReleasesUseCase,
    new_list_releases_usecase,
)
from app.usecase.release.reject_release_usecase import (
    RejectReleaseUseCase,
    new_reject_release_usecase,
)
from app.usecase.release.start_deployment_usecase import (
    StartDeploymentUseCase,
    new_start_deployment_usecase,
)

# --- 認證守衛 --------------------------------------------------------------


def optional_current_user(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CurrentUser | None:
    if not settings.auth_enabled:
        return CurrentUser(login="authentication-disabled", full_name="Local development")
    return AuthService(db, settings).get_user(
        request.cookies.get(settings.auth_session_cookie_name)
    )


def require_user(
    user: CurrentUser | None = Depends(optional_current_user),
) -> CurrentUser:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Session"},
        )
    return user


def require_admin(
    user: CurrentUser = Depends(require_user),
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    """Guards the endpoints that decide where production gets deployed.

    Editing a project or a connection changes which repository the controller
    promotes and tags, so it is a different kind of act from starting a release.
    The gate is off by default (PROJECT_ADMIN_WRITES) because turning it on when
    no Gitea account carries the admin flag would lock an installation out of its
    own settings.  With authentication disabled there is no identity to check, so
    there is nothing this could enforce.
    """
    if not settings.project_admin_writes or not settings.auth_enabled:
        return user
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Changing project or connection configuration requires a Gitea "
                "administrator account"
            ),
        )
    return user


# --- 上游 client（ambient：只回答「服務活著嗎」） -------------------------------------


def get_drone_client(settings: Settings = Depends(get_settings)) -> DroneClient:
    """The ambient client, for the endpoints that ask whether Drone is up at all.

    Not used on the deployment path: there, the client depends on which
    connection the component resolves to.
    """
    return build_drone_client(settings)


def get_gitea_client(settings: Settings = Depends(get_settings)) -> GiteaClient:
    return build_gitea_client(settings)


# --- Registry 與依 component 解析的 client ----------------------------------


def get_component_registry(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ComponentRegistry:
    return ComponentRegistry(db, settings)


def get_drone_clients(
    registry: ComponentRegistry = Depends(get_component_registry),
    settings: Settings = Depends(get_settings),
) -> DroneClients:
    return RegistryDroneClients(registry, settings)


def get_gitea_clients(
    registry: ComponentRegistry = Depends(get_component_registry),
    settings: Settings = Depends(get_settings),
) -> GiteaClients:
    return RegistryGiteaClients(registry, settings)


# --- Service -----------------------------------------------------------


def get_build_service(
    clients: DroneClients = Depends(get_drone_clients),
    settings: Settings = Depends(get_settings),
) -> DroneBuildService:
    return DroneBuildService(clients, settings)


def get_schedule_service(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> ScheduleService:
    return ScheduleService(db, settings)


def get_publish_service(
    db: Session = Depends(get_db),
    gitea: GiteaClient = Depends(get_gitea_client),
    settings: Settings = Depends(get_settings),
) -> PublishService:
    return PublishService(db, gitea, settings)


def get_deployment_service(
    db: Session = Depends(get_db),
    builds: DroneBuildService = Depends(get_build_service),
    settings: Settings = Depends(get_settings),
    user: CurrentUser = Depends(require_user),
) -> DeploymentService:
    return DeploymentService(
        db,
        builds,
        settings,
        actor=verified_actor(user),
        actor_source=ActorSource.SESSION.value,
    )


# --- Orchestrator（帶已驗證的 actor，寫進稽核事件） ----------------------------------


def get_release_orchestrator(
    db: Session = Depends(get_db),
    builds: DroneBuildService = Depends(get_build_service),
    settings: Settings = Depends(get_settings),
    user: CurrentUser = Depends(require_user),
) -> ReleaseOrchestrator:
    return ReleaseOrchestrator(
        db,
        builds,
        settings,
        actor=verified_actor(user),
        actor_source=ActorSource.SESSION.value,
    )


def get_publish_orchestrator(
    db: Session = Depends(get_db),
    gitea: GiteaClients = Depends(get_gitea_clients),
    settings: Settings = Depends(get_settings),
    user: CurrentUser = Depends(require_user),
) -> PublishOrchestrator:
    return PublishOrchestrator(
        db,
        gitea,
        settings,
        actor=verified_actor(user),
        actor_source=ActorSource.SESSION.value,
    )


# --- Release aggregate（Phase 2 的四層垂直切片）---------------------------
#
# 同一個 request 裡 Depends(get_db) 只會解析一次，所以 repository、unit of work
# 與 workflow gateway 共用同一個 Session——UseCase 結束 transaction 時，三者的
# 寫入會一起進去。


def get_workflow_service(db: Session = Depends(get_db)) -> WorkflowService:
    return WorkflowService(db)


def get_release_workflows(
    workflows: WorkflowService = Depends(get_workflow_service),
) -> ReleaseWorkflowGateway:
    """同一個物件的另一個視角：usecase 只看得到 gateway 那幾個方法。"""
    return workflows


def get_release_repository(db: Session = Depends(get_db)) -> ReleaseRepository:
    return SqlAlchemyReleaseRepository(db)


def get_unit_of_work(db: Session = Depends(get_db)) -> UnitOfWork:
    return SqlAlchemyUnitOfWork(db)


def get_create_release_usecase(
    releases: ReleaseRepository = Depends(get_release_repository),
    workflows: ReleaseWorkflowGateway = Depends(get_release_workflows),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> CreateReleaseUseCase:
    return new_create_release_usecase(releases, workflows, uow)


def get_get_release_usecase(
    releases: ReleaseRepository = Depends(get_release_repository),
) -> GetReleaseUseCase:
    return new_get_release_usecase(releases)


def get_list_releases_usecase(
    releases: ReleaseRepository = Depends(get_release_repository),
) -> ListReleasesUseCase:
    return new_list_releases_usecase(releases)


def get_approve_release_usecase(
    releases: ReleaseRepository = Depends(get_release_repository),
    workflows: ReleaseWorkflowGateway = Depends(get_release_workflows),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> ApproveReleaseUseCase:
    return new_approve_release_usecase(releases, workflows, uow)


def get_reject_release_usecase(
    releases: ReleaseRepository = Depends(get_release_repository),
    workflows: ReleaseWorkflowGateway = Depends(get_release_workflows),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> RejectReleaseUseCase:
    return new_reject_release_usecase(releases, workflows, uow)


def get_start_deployment_usecase(
    releases: ReleaseRepository = Depends(get_release_repository),
    workflows: ReleaseWorkflowGateway = Depends(get_release_workflows),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> StartDeploymentUseCase:
    return new_start_deployment_usecase(releases, workflows, uow)


def get_finish_deployment_usecase(
    releases: ReleaseRepository = Depends(get_release_repository),
    workflows: ReleaseWorkflowGateway = Depends(get_release_workflows),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> FinishDeploymentUseCase:
    return new_finish_deployment_usecase(releases, workflows, uow)


# --- Connection aggregate（Phase 3a）--------------------------------------
#
# 三個出口是分開注入的，所以測試可以只換掉「去打上游」那一個，不必 monkeypatch
# 模組屬性——舊的做法把測試綁在 connection_service 這個模組路徑上。


def get_connection_repository(db: Session = Depends(get_db)) -> ConnectionRepository:
    return SqlAlchemyConnectionRepository(db)


def get_connection_usage(db: Session = Depends(get_db)) -> ConnectionUsage:
    return SqlAlchemyConnectionUsage(db)


def get_token_vault(settings: Settings = Depends(get_settings)) -> TokenVault:
    return FernetTokenVault(settings)


def get_connection_probe(settings: Settings = Depends(get_settings)) -> ConnectionProbe:
    return UpstreamConnectionProbe(settings)


def get_list_connections_usecase(
    connections: ConnectionRepository = Depends(get_connection_repository),
) -> ListConnectionsUseCase:
    return new_list_connections_usecase(connections)


def get_get_connection_usecase(
    connections: ConnectionRepository = Depends(get_connection_repository),
) -> GetConnectionUseCase:
    return new_get_connection_usecase(connections)


def get_create_connection_usecase(
    connections: ConnectionRepository = Depends(get_connection_repository),
    vault: TokenVault = Depends(get_token_vault),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> CreateConnectionUseCase:
    return new_create_connection_usecase(connections, vault, uow)


def get_update_connection_usecase(
    connections: ConnectionRepository = Depends(get_connection_repository),
    vault: TokenVault = Depends(get_token_vault),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> UpdateConnectionUseCase:
    return new_update_connection_usecase(connections, vault, uow)


def get_delete_connection_usecase(
    connections: ConnectionRepository = Depends(get_connection_repository),
    usage: ConnectionUsage = Depends(get_connection_usage),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> DeleteConnectionUseCase:
    return new_delete_connection_usecase(connections, usage, uow)


def get_test_connection_usecase(
    connections: ConnectionRepository = Depends(get_connection_repository),
    probe: ConnectionProbe = Depends(get_connection_probe),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> TestConnectionUseCase:
    return new_test_connection_usecase(connections, probe, uow)


# --- Project aggregate（Phase 3b）-----------------------------------------


def get_project_repository(db: Session = Depends(get_db)) -> ProjectRepository:
    return SqlAlchemyProjectRepository(db)


def get_component_usage(db: Session = Depends(get_db)) -> ComponentUsage:
    return SqlAlchemyComponentUsage(db)


def get_resolve_connection_usecase(
    connections: ConnectionRepository = Depends(get_connection_repository),
) -> ResolveConnectionUseCase:
    return new_resolve_connection_usecase(connections)


def get_drone_connection_resolver(
    resolve_connection: ResolveConnectionUseCase = Depends(get_resolve_connection_usecase),
) -> DroneConnectionResolver:
    return ConnectionRegistryResolver(resolve_connection)


def get_component_checker(
    resolve_connection: ResolveConnectionUseCase = Depends(get_resolve_connection_usecase),
    settings: Settings = Depends(get_settings),
) -> ComponentChecker:
    return UpstreamComponentChecker(resolve_connection, settings)


def get_list_projects_usecase(
    projects: ProjectRepository = Depends(get_project_repository),
) -> ListProjectsUseCase:
    return new_list_projects_usecase(projects)


def get_get_project_usecase(
    projects: ProjectRepository = Depends(get_project_repository),
) -> GetProjectUseCase:
    return new_get_project_usecase(projects)


def get_create_project_usecase(
    projects: ProjectRepository = Depends(get_project_repository),
    connections: ConnectionRepository = Depends(get_connection_repository),
    resolver: DroneConnectionResolver = Depends(get_drone_connection_resolver),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> CreateProjectUseCase:
    return new_create_project_usecase(projects, connections, resolver, uow)


def get_update_project_usecase(
    projects: ProjectRepository = Depends(get_project_repository),
    connections: ConnectionRepository = Depends(get_connection_repository),
    resolver: DroneConnectionResolver = Depends(get_drone_connection_resolver),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> UpdateProjectUseCase:
    return new_update_project_usecase(projects, connections, resolver, uow)


def get_add_component_usecase(
    projects: ProjectRepository = Depends(get_project_repository),
    connections: ConnectionRepository = Depends(get_connection_repository),
    resolver: DroneConnectionResolver = Depends(get_drone_connection_resolver),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> AddComponentUseCase:
    return new_add_component_usecase(projects, connections, resolver, uow)


def get_update_component_usecase(
    projects: ProjectRepository = Depends(get_project_repository),
    connections: ConnectionRepository = Depends(get_connection_repository),
    resolver: DroneConnectionResolver = Depends(get_drone_connection_resolver),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> UpdateComponentUseCase:
    return new_update_component_usecase(projects, connections, resolver, uow)


def get_delete_component_usecase(
    projects: ProjectRepository = Depends(get_project_repository),
    usage: ComponentUsage = Depends(get_component_usage),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> DeleteComponentUseCase:
    return new_delete_component_usecase(projects, usage, uow)


def get_reorder_components_usecase(
    projects: ProjectRepository = Depends(get_project_repository),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> ReorderComponentsUseCase:
    return new_reorder_components_usecase(projects, uow)


def get_validate_project_usecase(
    projects: ProjectRepository = Depends(get_project_repository),
    checker: ComponentChecker = Depends(get_component_checker),
    resolver: DroneConnectionResolver = Depends(get_drone_connection_resolver),
) -> ValidateProjectUseCase:
    return new_validate_project_usecase(projects, checker, resolver)
