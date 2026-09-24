"""Release entity 與 releases 資料列之間的轉換。

ORM model 留在 app/db/models/release.py（alembic 由那裡取得 metadata，
orchestration 那側也還在用它），這裡只放映射，讓 domain 不必知道它存在。
"""

from typing import Any

from app.db.models.release import Release as ReleaseRow
from app.domain.release.entities import Release

# 一次狀態轉移可能寫到的欄位。全部一起寫回去是刻意的：entity 帶著沒被這次轉移
# 動到的舊值，所以寫回去是 no-op，換來的是每個轉移都走同一條路徑。
MUTABLE_FIELDS = (
    "status",
    "updated_at",
    "approved_by",
    "approved_at",
    "rejected_by",
    "rejected_at",
    "deploy_started_at",
    "deploy_finished_at",
    "message",
)


def to_entity(row: ReleaseRow) -> Release:
    return Release(
        id=row.id,
        repository=row.repository,
        branch=row.branch,
        commit_sha=row.commit_sha,
        environment=row.environment,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        rejected_by=row.rejected_by,
        rejected_at=row.rejected_at,
        deploy_started_at=row.deploy_started_at,
        deploy_finished_at=row.deploy_finished_at,
        message=row.message,
    )


def from_entity(release: Release) -> ReleaseRow:
    return ReleaseRow(
        id=release.id,
        repository=release.repository,
        branch=release.branch,
        commit_sha=release.commit_sha,
        environment=release.environment,
        status=release.status,
        created_at=release.created_at,
        updated_at=release.updated_at,
        approved_by=release.approved_by,
        approved_at=release.approved_at,
        rejected_by=release.rejected_by,
        rejected_at=release.rejected_at,
        deploy_started_at=release.deploy_started_at,
        deploy_finished_at=release.deploy_finished_at,
        message=release.message,
    )


def mutable_values(release: Release) -> dict[str, Any]:
    return {field: getattr(release, field) for field in MUTABLE_FIELDS}
