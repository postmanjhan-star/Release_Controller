"""v1 release aggregate 的業務例外。

放在 domain 而不是 service：這些是「規則被違反」的事實，跟它會被翻成哪個
HTTP 狀態碼無關。翻譯是 presentation 的事。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

from app.domain.release.value_objects import ReleaseStatus


class ReleaseNotFoundError(Exception):
    pass


class DuplicateReleaseError(Exception):
    """同一個 repository + commit + environment 已經有 release。

    真正的保證是 uq_releases_repository_commit_environment；repository 實作
    把資料庫拒絕的結果翻成這個例外。
    """


class InvalidReleaseTransitionError(Exception):
    """狀態機不允許這次轉移。

    訊息格式是既有 v1 API 的一部分（測試斷言 "expected PENDING"），不要改。
    """

    def __init__(self, current: ReleaseStatus, expected: ReleaseStatus) -> None:
        super().__init__(f"Release status is {current.value}; expected {expected.value}")
        self.current = current
        self.expected = expected
