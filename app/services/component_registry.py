"""Where the deployment path gets its repositories from, as of v3.0.

Until this revision, "which repository is the frontend" was answered by four
environment variables.  It is answered by a project_components row now.  Every
lookup on the deployment path goes through here, so there is one place that
knows how a deployment, or a legacy frontend/backend name, becomes a component.

The legacy lookups exist because the API still speaks in frontend/backend while
the orchestrator is being generalised.  They resolve against the default project
and are the last thing to be removed once routes take a project.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.orchestration import Deployment
from app.db.models.registry import Project, ProjectComponent
from app.domain.registry.entities import Component, Connection
from app.domain.registry.value_objects import ConnectionKind
from app.infrastructure.sqlite.registry.connection_repository import (
    SqlAlchemyConnectionRepository,
)
from app.usecase.registry.resolve_connection_usecase import (
    new_resolve_connection_usecase,
)


class ComponentNotRegisteredError(Exception):
    """The registry has no component for what the caller asked about."""


class NoDefaultProjectError(Exception):
    """A legacy, project-less request arrived and no project obviously owns it."""


class ProjectNotRegisteredError(Exception):
    """A request named a project that does not exist."""


def project_connection_id(component: ProjectComponent | Component, kind: str) -> str | None:
    """繼承鏈中間那一段：這個元件所屬專案設定的連線。

    過渡期兩種形狀都會進來——orchestration 那側還在傳 ORM 列（它有 .project
    relationship），registry 這側已經是 domain entity（它自己帶著繼承來的值）。
    Phase 3c 把 orchestration 也搬過去之後，這個函式就可以刪掉。
    """
    project = getattr(component, "project", None)
    if project is not None:
        return project.drone_connection_id if kind == "drone" else project.gitea_connection_id
    return (
        component.project_drone_connection_id
        if kind == "drone"
        else component.project_gitea_connection_id
    )


def project_default_target(component: ProjectComponent | Component) -> str:
    """同上：ORM 列走 relationship，domain entity 自己帶著。"""
    project = getattr(component, "project", None)
    return project.default_target if project is not None else component.project_default_target


class ComponentRegistry:
    def __init__(self, db: Session, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.resolve_connection = new_resolve_connection_usecase(SqlAlchemyConnectionRepository(db))

    # ----------------------------------------------------------------- lookup

    def default_project(self) -> Project:
        """The project a request that names no project belongs to.

        Prefers the one migration 0014 seeded from the environment.  Falls back
        to "the only project there is", and refuses to guess beyond that -- a
        wrong guess here would deploy the wrong repository.
        """
        seeded = self.db.scalar(select(Project).where(Project.key == "default"))
        if seeded is not None:
            return seeded
        projects = list(
            self.db.scalars(select(Project).where(Project.is_archived.is_(False))).all()
        )
        if len(projects) == 1:
            return projects[0]
        if not projects:
            raise NoDefaultProjectError(
                "No project is configured. Add one under Projects, or set "
                "DRONE_*_REPO_OWNER/NAME and re-run the migration to seed it."
            )
        raise NoDefaultProjectError(
            "Several projects exist and this request names none. Use the "
            "project-scoped endpoints, or keep a project with the key 'default'."
        )

    def project(self, project_ref: str | None) -> Project:
        """The named project, or the one a project-less request belongs to."""
        if not project_ref:
            return self.default_project()
        found = self.db.scalar(
            select(Project).where((Project.id == project_ref) | (Project.key == project_ref))
        )
        if found is None:
            raise ProjectNotRegisteredError(f"No project with id or key {project_ref!r}")
        return found

    def for_key(self, key: str, project: Project | None = None) -> ProjectComponent:
        project = project or self.default_project()
        for component in project.components:
            if component.key == key:
                return component
        raise ComponentNotRegisteredError(
            f"Project {project.key!r} has no component named {key!r}. "
            f"It has: {', '.join(c.key for c in project.components) or 'none'}."
        )

    def for_deployment(self, deployment: Deployment) -> ProjectComponent:
        """The component a deployment was created against.

        Uses component_id rather than the component name snapshot, so a renamed
        component still resolves for work that is already in flight.
        """
        found = self.db.get(ProjectComponent, deployment.component_id)
        if found is None:
            raise ComponentNotRegisteredError(
                f"Deployment {deployment.id} points at component "
                f"{deployment.component_id!r}, which no longer exists"
            )
        return found

    # ------------------------------------------------------------ connections

    def drone_connection(self, component: ProjectComponent | Component) -> Connection:
        return self.resolve_connection.execute(
            ConnectionKind.DRONE,
            component.drone_connection_id,
            project_connection_id(component, "drone"),
        )

    def gitea_connection(self, component: ProjectComponent | Component) -> Connection:
        return self.resolve_connection.execute(
            ConnectionKind.GITEA,
            component.gitea_connection_id,
            project_connection_id(component, "gitea"),
        )


# A release spans several components, and the order they deploy in is the order
# their components sit in.  Kept here rather than on the bundle so there is one
# answer to "what order does this run in", and so a Deployment does not have to
# carry a copy of a position that someone can edit later.
POSITION_OF_A_MISSING_COMPONENT = 10**6


def ordered_by_component(db: Session, deployments: list[Deployment]) -> list[Deployment]:
    """Deployments in component order, ties broken by when they were created."""
    component_ids = [item.component_id for item in deployments if item.component_id]
    positions: dict[str, int] = {}
    if component_ids:
        positions = dict(
            db.execute(
                select(ProjectComponent.id, ProjectComponent.position).where(
                    ProjectComponent.id.in_(component_ids)
                )
            ).all()
        )
    return sorted(
        deployments,
        key=lambda item: (
            positions.get(item.component_id, POSITION_OF_A_MISSING_COMPONENT),
            item.created_at,
            item.id,
        ),
    )


def components_for(db: Session, deployments: list[Deployment]) -> dict[str, ProjectComponent]:
    """deployment id -> its component row, in one query."""
    component_ids = {item.component_id for item in deployments if item.component_id}
    if not component_ids:
        return {}
    by_id = {
        component.id: component
        for component in db.scalars(
            select(ProjectComponent).where(ProjectComponent.id.in_(component_ids))
        ).all()
    }
    return {item.id: by_id[item.component_id] for item in deployments if item.component_id in by_id}
