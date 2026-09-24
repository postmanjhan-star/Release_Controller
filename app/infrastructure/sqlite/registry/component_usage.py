"""「還有東西指著這個元件嗎」——跨 aggregate 的唯讀查詢。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.orchestration import Deployment
from app.db.models.scheduling import ScheduleComponentBuild
from app.domain.registry.repositories import ComponentUsage


class SqlAlchemyComponentUsage(ComponentUsage):
    def __init__(self, session: Session) -> None:
        self.session = session

    def has_deployments(self, component_id: str) -> bool:
        return (
            self.session.scalar(
                select(Deployment.id).where(Deployment.component_id == component_id).limit(1)
            )
            is not None
        )

    def is_scheduled(self, component_id: str) -> bool:
        return (
            self.session.scalar(
                select(ScheduleComponentBuild.schedule_id)
                .where(ScheduleComponentBuild.component_id == component_id)
                .limit(1)
            )
            is not None
        )
