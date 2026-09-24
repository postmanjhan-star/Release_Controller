"""ReleaseBundleRepository 的 SQLAlchemy 實作。"""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.models.orchestration import Deployment as DeploymentRow
from app.db.models.orchestration import ReleaseBundle as BundleRow
from app.db.models.registry import ProjectComponent
from app.domain.orchestration.entities import Deployment, ReleaseBundle
from app.domain.orchestration.repositories import ReleaseBundleRepository
from app.domain.orchestration.value_objects import BundleStatus
from app.infrastructure.sqlite.orchestration.deployment_dto import to_entity as deployment_to_entity
from app.infrastructure.sqlite.orchestration.release_bundle_dto import (
    apply_lifecycle,
    new_row,
    to_entity,
)
from app.services.attachment_service import apply_attachment

# 元件已經不在了的部署排到最後，而不是讓排序爆掉。
POSITION_OF_A_MISSING_COMPONENT = 10**6


class SqlAlchemyReleaseBundleRepository(ReleaseBundleRepository):
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, bundle: ReleaseBundle, *, attachment: Any = None) -> None:
        row = new_row(bundle)
        # attachment 對 domain 而言是不透明的：它是隨通知寄出的附件，不是
        # bundle 的狀態。
        apply_attachment(row, attachment)
        self.session.add(row)
        self.session.flush()

    def save(self, bundle: ReleaseBundle) -> None:
        row = self.session.get(BundleRow, bundle.id)
        if row is None:
            return
        apply_lifecycle(row, bundle)
        self.session.flush()

    def find_by_id(self, release_id: str) -> ReleaseBundle | None:
        row = self.session.scalar(
            select(BundleRow)
            .options(selectinload(BundleRow.deployments))
            .where(BundleRow.id == release_id)
            .execution_options(populate_existing=True)
        )
        return None if row is None else to_entity(row)

    def ordered_deployments(self, bundle: ReleaseBundle) -> list[Deployment]:
        """依元件順序排好的部署，同順位再依建立時間。

        順序來自 project_components.position 而不是部署自己存的副本——一個位置
        之後可能被人編輯，部署不該帶著過期的那一份。
        """
        rows = self.session.scalars(
            select(DeploymentRow).where(DeploymentRow.release_bundle_id == bundle.id)
        ).all()
        component_ids = [row.component_id for row in rows if row.component_id]
        positions: dict[str, int] = {}
        if component_ids:
            positions = dict(
                self.session.execute(
                    select(ProjectComponent.id, ProjectComponent.position).where(
                        ProjectComponent.id.in_(component_ids)
                    )
                ).all()
            )
        ordered = sorted(
            rows,
            key=lambda row: (
                positions.get(row.component_id, POSITION_OF_A_MISSING_COMPONENT),
                row.created_at,
                row.id,
            ),
        )
        return [deployment_to_entity(row) for row in ordered]

    def search(
        self, *, status: BundleStatus | None, target: str | None, limit: int, offset: int
    ) -> tuple[list[ReleaseBundle], int]:
        conditions = []
        if status:
            conditions.append(BundleRow.status == status.value)
        if target:
            conditions.append(BundleRow.target == target)
        total = (
            self.session.scalar(select(func.count()).select_from(BundleRow).where(*conditions)) or 0
        )
        rows = self.session.scalars(
            select(BundleRow)
            .options(selectinload(BundleRow.deployments))
            .where(*conditions)
            .order_by(BundleRow.created_at.desc(), BundleRow.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return [to_entity(row) for row in rows], total
