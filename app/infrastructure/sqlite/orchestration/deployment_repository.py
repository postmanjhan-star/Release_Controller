"""DeploymentRepository 的 SQLAlchemy 實作。"""

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.orchestration import ACTIVE_PROMOTION_STATUSES
from app.db.models.orchestration import Deployment as DeploymentRow
from app.domain.orchestration.entities import CLAIMABLE_FOR_PROMOTION, Deployment
from app.domain.orchestration.exceptions import DuplicatePromotionError
from app.domain.orchestration.repositories import DeploymentFilters, DeploymentRepository
from app.infrastructure.sqlite.orchestration.deployment_dto import (
    apply_lifecycle,
    new_row,
    to_entity,
)

CLAIMABLE_VALUES = tuple(status.value for status in CLAIMABLE_FOR_PROMOTION)


class SqlAlchemyDeploymentRepository(DeploymentRepository):
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, deployment: Deployment) -> None:
        self.session.add(new_row(deployment))
        try:
            self.session.flush()
        except IntegrityError as exc:
            # uq_deployments_active_promotion 擋掉了一個溜過預先檢查的重複促銷。
            self.session.rollback()
            raise DuplicatePromotionError(
                "This build has already been promoted to this target"
            ) from exc

    def save(self, deployment: Deployment) -> None:
        row = self.session.get(DeploymentRow, deployment.id)
        if row is None:
            return
        apply_lifecycle(row, deployment)
        self.session.flush()

    def find_by_id(self, deployment_id: str) -> Deployment | None:
        row = self.session.get(DeploymentRow, deployment_id)
        return None if row is None else to_entity(row)

    def find_active_promotion(
        self,
        *,
        drone_connection_id: str,
        drone_owner: str,
        drone_repository: str,
        source_build_number: int,
        target: str,
    ) -> str | None:
        return self.session.scalar(
            select(DeploymentRow.id)
            .where(
                DeploymentRow.drone_connection_id == drone_connection_id,
                DeploymentRow.drone_owner == drone_owner,
                DeploymentRow.drone_repository == drone_repository,
                DeploymentRow.source_build_number == source_build_number,
                DeploymentRow.target == target,
                DeploymentRow.status.in_(ACTIVE_PROMOTION_STATUSES),
            )
            .limit(1)
        )

    def claim_for_promotion(self, deployment: Deployment) -> bool:
        """帶 expected 狀態的 UPDATE——決定誰贏的就是這一句。

        entity 已經把自己改成 PROMOTING 了，但那只是記憶體裡的事；兩個同時
        進來的呼叫都會通過那一步，只有這裡的 rowcount 能分出勝負。
        """
        claimed = self.session.execute(
            update(DeploymentRow)
            .where(
                DeploymentRow.id == deployment.id,
                DeploymentRow.status.in_(CLAIMABLE_VALUES),
            )
            .values(
                status=deployment.status.value,
                current_stage=deployment.current_stage,
                started_at=func.coalesce(DeploymentRow.started_at, deployment.started_at),
                updated_at=deployment.updated_at,
            )
            .execution_options(synchronize_session=False)
        )
        # 兩條路都要 expire：synchronize_session=False 表示 session 裡那一列
        # 沒有被同步。這裡刻意用 expire 而不是 rollback——呼叫端可能還握著同一個
        # transaction 裡其他部署的待寫事件。
        row = self.session.get(DeploymentRow, deployment.id)
        if row is not None:
            self.session.expire(row)
        return claimed.rowcount == 1

    def search(
        self, filters: DeploymentFilters, *, limit: int, offset: int
    ) -> tuple[list[Deployment], int]:
        conditions = []
        if filters.component:
            conditions.append(DeploymentRow.component == filters.component)
        if filters.status:
            conditions.append(DeploymentRow.status == filters.status.value)
        if filters.target:
            conditions.append(DeploymentRow.target == filters.target)
        if filters.release_bundle_id:
            conditions.append(DeploymentRow.release_bundle_id == filters.release_bundle_id)
        total = (
            self.session.scalar(select(func.count()).select_from(DeploymentRow).where(*conditions))
            or 0
        )
        rows = self.session.scalars(
            select(DeploymentRow)
            .where(*conditions)
            .order_by(DeploymentRow.created_at.desc(), DeploymentRow.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return [to_entity(row) for row in rows], total
