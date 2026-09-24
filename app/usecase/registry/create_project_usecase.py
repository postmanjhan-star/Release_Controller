"""建立一個專案，可以連同它的元件一起。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.domain.registry.entities import Component, Project
from app.domain.registry.repositories import (
    ConnectionRepository,
    DroneConnectionResolver,
    ProjectRepository,
)
from app.domain.shared.time import utc_now
from app.domain.shared.unit_of_work import UnitOfWork
from app.usecase.registry._project_ops import assert_connection_exists, load_project


@dataclass(frozen=True)
class NewComponent:
    """建立元件所需要的欄位，用 plain values 表示。

    刻意不收 Pydantic model：use case 層不該知道 HTTP 那側長什麼樣子。
    """

    key: str
    drone_owner: str
    drone_repo: str
    display_name: str | None = None
    promote_target_override: str | None = None
    drone_connection_id: str | None = None
    publish_enabled: bool = True
    gitea_owner: str | None = None
    gitea_repo: str | None = None
    gitea_connection_id: str | None = None
    tag_prefix: str = ""
    position: int | None = None
    is_active: bool = True


@dataclass(frozen=True)
class NewProject:
    key: str
    name: str
    default_target: str
    description: str | None = None
    drone_connection_id: str | None = None
    gitea_connection_id: str | None = None
    components: list[NewComponent] = field(default_factory=list)


class CreateProjectUseCase(ABC):
    @abstractmethod
    def execute(self, payload: NewProject) -> Project: ...


class CreateProjectUseCaseImpl(CreateProjectUseCase):
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

    def execute(self, payload: NewProject) -> Project:
        assert_connection_exists(self.connections, payload.drone_connection_id)
        assert_connection_exists(self.connections, payload.gitea_connection_id)
        now = utc_now()
        project = Project.create(
            key=payload.key,
            name=payload.name,
            default_target=payload.default_target,
            now=now,
            description=payload.description,
            drone_connection_id=payload.drone_connection_id,
            gitea_connection_id=payload.gitea_connection_id,
        )
        for index, spec in enumerate(payload.components, start=1):
            assert_connection_exists(self.connections, spec.drone_connection_id)
            assert_connection_exists(self.connections, spec.gitea_connection_id)
            project.add_component(
                build_component(project, spec, position=index, now=now),
                requested_position=index,
            )
        project.assert_no_slot_conflict(self.resolver.drone_connection_ids(project))
        self.projects.add(project)
        self.uow.commit()
        return load_project(self.projects, project.id)


def build_component(project: Project, spec: NewComponent, *, position: int, now) -> Component:
    return Component.create(
        project_id=project.id,
        key=spec.key,
        drone_owner=spec.drone_owner,
        drone_repo=spec.drone_repo,
        position=position,
        project_default_target=project.default_target,
        now=now,
        display_name=spec.display_name,
        promote_target_override=spec.promote_target_override,
        drone_connection_id=spec.drone_connection_id,
        publish_enabled=spec.publish_enabled,
        gitea_owner=spec.gitea_owner,
        gitea_repo=spec.gitea_repo,
        gitea_connection_id=spec.gitea_connection_id,
        tag_prefix=spec.tag_prefix,
        is_active=spec.is_active,
    )


def new_create_project_usecase(
    projects: ProjectRepository,
    connections: ConnectionRepository,
    resolver: DroneConnectionResolver,
    uow: UnitOfWork,
) -> CreateProjectUseCase:
    return CreateProjectUseCaseImpl(projects, connections, resolver, uow)
