"""「還有沒有人在用這條連線」——跨 aggregate 的唯讀查詢。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.registry import Project, ProjectComponent
from app.domain.registry.repositories import ConnectionUsage


class SqlAlchemyConnectionUsage(ConnectionUsage):
    def __init__(self, session: Session) -> None:
        self.session = session

    def is_referenced(self, connection_id: str) -> bool:
        project = self.session.scalar(
            select(Project.id)
            .where(
                (Project.drone_connection_id == connection_id)
                | (Project.gitea_connection_id == connection_id)
            )
            .limit(1)
        )
        if project is not None:
            return True
        component = self.session.scalar(
            select(ProjectComponent.id)
            .where(
                (ProjectComponent.drone_connection_id == connection_id)
                | (ProjectComponent.gitea_connection_id == connection_id)
            )
            .limit(1)
        )
        return component is not None
