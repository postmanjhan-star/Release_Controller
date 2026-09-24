"""修改一個專案，以及封存／解除封存。"""

from abc import ABC, abstractmethod

from app.domain.registry.entities import Project
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


class UpdateProjectUseCase(ABC):
    @abstractmethod
    def execute(
        self,
        project_ref: str,
        *,
        name: str | None = None,
        description: str | None = None,
        default_target: str | None = None,
        drone_connection_id: str | None = None,
        gitea_connection_id: str | None = None,
        is_archived: bool | None = None,
    ) -> Project: ...

    @abstractmethod
    def set_archived(self, project_ref: str, archived: bool) -> Project: ...


class UpdateProjectUseCaseImpl(UpdateProjectUseCase):
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
        *,
        name: str | None = None,
        description: str | None = None,
        default_target: str | None = None,
        drone_connection_id: str | None = None,
        gitea_connection_id: str | None = None,
        is_archived: bool | None = None,
    ) -> Project:
        project = load_project(self.projects, project_ref)
        assert_connection_exists(self.connections, drone_connection_id)
        assert_connection_exists(self.connections, gitea_connection_id)
        project.apply_update(
            at=utc_now(),
            name=name,
            description=description,
            default_target=default_target,
            drone_connection_id=drone_connection_id,
            gitea_connection_id=gitea_connection_id,
            is_archived=is_archived,
        )
        # default_target 會連帶移動每個沒有覆寫的元件，所以位置衝突要重算——
        # 不是只有元件變動時才算。save_project 一律會算。
        return save_project(self.projects, self.resolver, self.uow, project)

    def set_archived(self, project_ref: str, archived: bool) -> Project:
        project = load_project(self.projects, project_ref)
        project.set_archived(archived, at=utc_now())
        self.projects.save(project)
        self.uow.commit()
        return load_project(self.projects, project.id)


def new_update_project_usecase(
    projects: ProjectRepository,
    connections: ConnectionRepository,
    resolver: DroneConnectionResolver,
    uow: UnitOfWork,
) -> UpdateProjectUseCase:
    return UpdateProjectUseCaseImpl(projects, connections, resolver, uow)
