"""v1 核准流程的 Release entity。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

import uuid
from datetime import datetime

from app.domain.release.exceptions import InvalidReleaseTransitionError
from app.domain.release.value_objects import ReleaseStatus

FINISHED_STATUSES = (ReleaseStatus.SUCCESS, ReleaseStatus.FAILED)


class Release:
    """一次發布的核准與部署歷程。

    狀態機就是這個 aggregate 的全部業務規則：

        PENDING --approve--> APPROVED --start--> DEPLOYING --finish--> SUCCESS
             \\                                                    \\-> FAILED
              \\--reject--> REJECTED

    每個轉移方法**回傳它的前置狀態**，由 repository 拿去當 compare-and-swap
    的 WHERE 條件。這個看起來多餘的回傳值是刻意的：規則寫在這裡是為了不碰
    資料庫就能測，但兩個同時進來的核准請求只有資料庫擋得住，所以前置狀態必須
    一路帶到 UPDATE 上，不能只在記憶體裡檢查完就算數。
    （背景見 docs/backend-ddd-review.md 的「落差 2」。）

    身分是 id；兩個 Release 相不相等跟欄位值無關。
    """

    def __init__(
        self,
        *,
        id: str,
        repository: str,
        branch: str,
        commit_sha: str,
        environment: str,
        status: ReleaseStatus,
        created_at: datetime,
        updated_at: datetime,
        approved_by: str | None = None,
        approved_at: datetime | None = None,
        rejected_by: str | None = None,
        rejected_at: datetime | None = None,
        deploy_started_at: datetime | None = None,
        deploy_finished_at: datetime | None = None,
        message: str | None = None,
    ) -> None:
        self._id = id
        self._repository = repository
        self._branch = branch
        self._commit_sha = commit_sha
        self._environment = environment
        self._status = status
        self._created_at = created_at
        self._updated_at = updated_at
        self._approved_by = approved_by
        self._approved_at = approved_at
        self._rejected_by = rejected_by
        self._rejected_at = rejected_at
        self._deploy_started_at = deploy_started_at
        self._deploy_finished_at = deploy_finished_at
        self._message = message

    # 唯讀屬性。名稱與 ReleaseResponse 的欄位一致，所以 presentation 可以直接
    # 從 entity 讀，不需要先繞回 ORM 物件。
    @property
    def id(self) -> str:
        return self._id

    @property
    def repository(self) -> str:
        return self._repository

    @property
    def branch(self) -> str:
        return self._branch

    @property
    def commit_sha(self) -> str:
        return self._commit_sha

    @property
    def environment(self) -> str:
        return self._environment

    @property
    def status(self) -> ReleaseStatus:
        return self._status

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    @property
    def approved_by(self) -> str | None:
        return self._approved_by

    @property
    def approved_at(self) -> datetime | None:
        return self._approved_at

    @property
    def rejected_by(self) -> str | None:
        return self._rejected_by

    @property
    def rejected_at(self) -> datetime | None:
        return self._rejected_at

    @property
    def deploy_started_at(self) -> datetime | None:
        return self._deploy_started_at

    @property
    def deploy_finished_at(self) -> datetime | None:
        return self._deploy_finished_at

    @property
    def message(self) -> str | None:
        return self._message

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Release):
            return self._id == other._id
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._id)

    def __repr__(self) -> str:
        return f"Release(id={self._id!r}, status={self._status.value})"

    @staticmethod
    def create(
        *,
        repository: str,
        branch: str,
        commit_sha: str,
        environment: str,
        message: str | None,
        now: datetime,
    ) -> "Release":
        return Release(
            id=str(uuid.uuid4()),
            repository=repository,
            branch=branch,
            commit_sha=commit_sha,
            environment=environment,
            status=ReleaseStatus.PENDING,
            created_at=now,
            updated_at=now,
            message=message,
        )

    def approve(self, *, by: str | None, at: datetime) -> ReleaseStatus:
        expected = self._require(ReleaseStatus.PENDING)
        self._status = ReleaseStatus.APPROVED
        self._approved_by = by
        self._approved_at = at
        self._updated_at = at
        return expected

    def reject(self, *, by: str | None, message: str | None, at: datetime) -> ReleaseStatus:
        expected = self._require(ReleaseStatus.PENDING)
        self._status = ReleaseStatus.REJECTED
        self._rejected_by = by
        self._rejected_at = at
        self._updated_at = at
        self._message = message
        return expected

    def start_deployment(self, *, at: datetime) -> ReleaseStatus:
        expected = self._require(ReleaseStatus.APPROVED)
        self._status = ReleaseStatus.DEPLOYING
        self._deploy_started_at = at
        self._updated_at = at
        return expected

    def finish_deployment(
        self, *, status: ReleaseStatus, message: str | None, at: datetime
    ) -> ReleaseStatus:
        if status not in FINISHED_STATUSES:
            raise ValueError(f"A deployment finishes in SUCCESS or FAILED, not {status.value}")
        expected = self._require(ReleaseStatus.DEPLOYING)
        self._status = status
        self._deploy_finished_at = at
        self._updated_at = at
        self._message = message
        return expected

    def _require(self, expected: ReleaseStatus) -> ReleaseStatus:
        if self._status is not expected:
            raise InvalidReleaseTransitionError(self._status, expected)
        return expected
