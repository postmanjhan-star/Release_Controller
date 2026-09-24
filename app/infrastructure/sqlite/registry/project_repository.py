"""ProjectRepository 的 SQLAlchemy 實作。"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.db.models.registry import Project as ProjectRow
from app.db.models.registry import ProjectComponent as ComponentRow
from app.domain.registry.entities import Project
from app.domain.registry.exceptions import (
    ComponentKeyTakenError,
    ProjectKeyTakenError,
    ProjectNotFoundError,
)
from app.domain.registry.repositories import ProjectRepository
from app.infrastructure.sqlite.registry.project_dto import (
    apply_to_component_row,
    apply_to_project_row,
    component_row,
    project_row,
    to_entity,
)


class SqlAlchemyProjectRepository(ProjectRepository):
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, project: Project) -> None:
        self.session.add(project_row(project))
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise ProjectKeyTakenError(
                f"A project with key {project.key!r} already exists"
            ) from exc
        self._sync_components(project)

    def save(self, project: Project) -> None:
        row = self.session.get(ProjectRow, project.id)
        if row is None:
            raise ProjectNotFoundError
        apply_to_project_row(row, project)
        self._sync_components(project)

    def find(self, project_ref: str) -> Project | None:
        # populate_existing：session 不會在 commit 時 expire，所以一個已經載入過的
        # components 集合會用舊順序回來，重新排序之後看起來就像沒排。
        for column in (ProjectRow.id, ProjectRow.key):
            row = self.session.scalar(
                select(ProjectRow)
                .options(selectinload(ProjectRow.components))
                .where(column == project_ref)
                .execution_options(populate_existing=True)
            )
            if row is not None:
                return to_entity(row)
        return None

    def list(self, *, include_archived: bool = False) -> list[Project]:
        conditions = [] if include_archived else [ProjectRow.is_archived.is_(False)]
        rows = self.session.scalars(
            select(ProjectRow)
            .options(selectinload(ProjectRow.components))
            .where(*conditions)
            .order_by(ProjectRow.name)
            .execution_options(populate_existing=True)
        ).all()
        return [to_entity(row) for row in rows]

    def _sync_components(self, project: Project) -> None:
        """把整組元件寫成 aggregate 現在的樣子：新增、修改、刪除、重新編號。

        位置分兩趟走負數。SQLite 沒有 deferrable constraint，所以直接寫最終順序
        時，只要不是純粹的 append，就會有一瞬間兩個元件位置相同，
        撞上 uq_project_components_position。
        """
        existing = {
            row.id: row
            for row in self.session.scalars(
                select(ComponentRow).where(ComponentRow.project_id == project.id)
            ).all()
        }
        wanted = {component.id: component for component in project.components}

        for component_id, row in existing.items():
            if component_id not in wanted:
                self.session.delete(row)
        self.session.flush()

        rows = {cid: row for cid, row in existing.items() if cid in wanted}
        for component in project.components:
            row = rows.get(component.id)
            if row is None:
                row = component_row(component)
                self.session.add(row)
                rows[component.id] = row
            else:
                apply_to_component_row(row, component)
            row.position = -component.position
        self._flush_components()

        for component in project.components:
            rows[component.id].position = component.position
        self._flush_components()

    def _flush_components(self) -> None:
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise ComponentKeyTakenError(
                "A component key or position collided while saving this project"
            ) from exc
