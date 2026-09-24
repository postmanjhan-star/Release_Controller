"""ReleaseRepository 的 SQLAlchemy 實作。"""

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.release import Release as ReleaseRow
from app.domain.release.entities import Release
from app.domain.release.exceptions import DuplicateReleaseError
from app.domain.release.repositories import ReleaseFilters, ReleaseRepository
from app.domain.release.value_objects import ReleaseStatus
from app.infrastructure.sqlite.release.release_dto import (
    from_entity,
    mutable_values,
    to_entity,
)


class SqlAlchemyReleaseRepository(ReleaseRepository):
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, release: Release) -> None:
        self.session.add(from_entity(release))
        try:
            self.session.flush()
        except IntegrityError as exc:
            # uq_releases_repository_commit_environment 拒絕了重複的 release。
            self.session.rollback()
            raise DuplicateReleaseError from exc

    def find_by_id(self, release_id: str) -> Release | None:
        row = self.session.get(ReleaseRow, release_id)
        return None if row is None else to_entity(row)

    def search(
        self, filters: ReleaseFilters, *, limit: int, offset: int
    ) -> tuple[list[Release], int]:
        conditions = []
        if filters.repository is not None:
            conditions.append(ReleaseRow.repository == filters.repository)
        if filters.branch is not None:
            conditions.append(ReleaseRow.branch == filters.branch)
        if filters.environment is not None:
            conditions.append(ReleaseRow.environment == filters.environment)
        if filters.status is not None:
            conditions.append(ReleaseRow.status == filters.status)

        total = self.session.scalar(select(func.count()).select_from(ReleaseRow).where(*conditions))
        rows = self.session.scalars(
            select(ReleaseRow)
            .where(*conditions)
            .order_by(ReleaseRow.created_at.desc(), ReleaseRow.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return [to_entity(row) for row in rows], total or 0

    def save_transition(self, release: Release, *, expected: ReleaseStatus) -> bool:
        """帶著 expected 的 UPDATE——這就是 v1 一直以來的並行防護。

        `WHERE status = expected` 讓這次寫入變成 compare-and-swap：兩個同時
        送進來的核准，只有先到的那個 rowcount 會是 1。改成先讀再無條件寫，
        兩個都會成功，而且不會有任何錯誤浮出來。
        """
        result = self.session.execute(
            update(ReleaseRow)
            .where(ReleaseRow.id == release.id, ReleaseRow.status == expected)
            .values(**mutable_values(release))
        )
        return result.rowcount == 1
