"""修改專案裡的一個元件。"""

from abc import ABC, abstractmethod

from app.domain.registry.entities import Component
from app.domain.registry.repositories import (
    ConnectionRepository,
    DroneConnectionResolver,
    ProjectRepository,
)
from app.domain.shared.time import utc_now
from app.domain.shared.unit_of_work import UnitOfWork
from app.usecase.registry._project_ops import (
    assert_connection_exists,
    load_project,
    save_project,
)


class UpdateComponentUseCase(ABC):
    @abstractmethod
    def execute(
        self,
        project_ref: str,
        component_ref: str,
        *,
        display_name: str | None = None,
        drone_owner: str | None = None,
        drone_repo: str | None = None,
        promote_target_override: str | None = None,
        clear_promote_target_override: bool = False,
        drone_connection_id: str | None = None,
        publish_enabled: bool | None = None,
        gitea_owner: str | None = None,
        gitea_repo: str | None = None,
        gitea_connection_id: str | None = None,
        tag_prefix: str | None = None,
        is_active: bool | None = None,
    ) -> Component: ...


class UpdateComponentUseCaseImpl(UpdateComponentUseCase):
    def __init__(
        self,
        projects: ProjectRepository,
        connections: ConnectionRepository,
        resolver: DroneConnectionResolver,
        uow: UnitOfWork,
    ) -> None:
        self.projects = projects
        self.connections = connections
        self.resolver = resolver
        self.uow = uow

    def execute(
        self,
        project_ref: str,
        component_ref: str,
        *,
        display_name: str | None = None,
        drone_owner: str | None = None,
        drone_repo: str | None = None,
        promote_target_override: str | None = None,
        clear_promote_target_override: bool = False,
        drone_connection_id: str | None = None,
        publish_enabled: bool | None = None,
        gitea_owner: str | None = None,
        gitea_repo: str | None = None,
        gitea_connection_id: str | None = None,
        tag_prefix: str | None = None,
        is_active: bool | None = None,
    ) -> Component:
        project = load_project(self.projects, project_ref)
        component = project.component(component_ref)
        assert_connection_exists(self.connections, drone_connection_id)
        assert_connection_exists(self.connections, gitea_connection_id)
        component.apply_update(
            at=utc_now(),
            display_name=display_name,
            drone_owner=drone_owner,
            drone_repo=drone_repo,
            promote_target_override=promote_target_override,
            clear_promote_target_override=clear_promote_target_override,
            drone_connection_id=drone_connection_id,
            publish_enabled=publish_enabled,
            gitea_owner=gitea_owner,
            gitea_repo=gitea_repo,
            gitea_connection_id=gitea_connection_id,
            tag_prefix=tag_prefix,
            is_active=is_active,
        )
        saved = save_project(self.projects, self.resolver, self.uow, project)
        return saved.component(component.id)


def new_update_component_usecase(
    projects: ProjectRepository,
    connections: ConnectionRepository,
    resolver: DroneConnectionResolver,
    uow: UnitOfWork,
) -> UpdateComponentUseCase:
    return UpdateComponentUseCaseImpl(projects, connections, resolver, uow)
