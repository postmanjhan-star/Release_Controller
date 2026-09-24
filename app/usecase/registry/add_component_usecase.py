"""在專案裡加一個元件。"""

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
from app.usecase.registry.create_project_usecase import NewComponent, build_component


class AddComponentUseCase(ABC):
    @abstractmethod
    def execute(self, project_ref: str, spec: NewComponent) -> Component: ...


class AddComponentUseCaseImpl(AddComponentUseCase):
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

    def execute(self, project_ref: str, spec: NewComponent) -> Component:
        project = load_project(self.projects, project_ref)
        assert_connection_exists(self.connections, spec.drone_connection_id)
        assert_connection_exists(self.connections, spec.gitea_connection_id)
        component = build_component(
            project, spec, position=len(project.components) + 1, now=utc_now()
        )
        # 位置由 aggregate 決定：要求的位置會被夾在 1..n+1，其餘元件跟著重編。
        project.add_component(component, requested_position=spec.position)
        saved = save_project(self.projects, self.resolver, self.uow, project)
        return saved.component(component.id)


def new_add_component_usecase(
    projects: ProjectRepository,
    connections: ConnectionRepository,
    resolver: DroneConnectionResolver,
    uow: UnitOfWork,
) -> AddComponentUseCase:
    return AddComponentUseCaseImpl(projects, connections, resolver, uow)
