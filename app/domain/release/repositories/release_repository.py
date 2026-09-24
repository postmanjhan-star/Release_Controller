"""v1 release aggregate 的持久化介面。

Domain 只說「需要能做什麼」，怎麼做是 infrastructure 的事。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.domain.release.entities import Release
from app.domain.release.value_objects import ReleaseStatus


@dataclass(frozen=True)
class ReleaseFilters:
    repository: str | None = None
    branch: str | None = None
    environment: str | None = None
    status: ReleaseStatus | None = None


class ReleaseRepository(ABC):
    @abstractmethod
    def add(self, release: Release) -> None:
        """寫入一筆新的 release。

        撞到 repository + commit + environment 的唯一條件時 raise
        DuplicateReleaseError——那個唯一條件才是保證，這裡只負責翻譯。
        """

    @abstractmethod
    def find_by_id(self, release_id: str) -> Release | None: ...

    @abstractmethod
    def search(
        self, filters: ReleaseFilters, *, limit: int, offset: int
    ) -> tuple[list[Release], int]:
        """回傳 (該頁的 release, 符合條件的總數)。"""

    @abstractmethod
    def save_transition(self, release: Release, *, expected: ReleaseStatus) -> bool:
        """把一次狀態轉移寫回去，並且**只在資料庫端的狀態仍是 expected 時**寫。

        回傳 False 代表沒有寫到任何一列：在讀出 entity 到現在之間，有人先完成了
        一次轉移。呼叫端要把它當成衝突處理，不是當成「沒事發生」。

        這個方法存在的理由就是這一點。entity 已經檢查過同一條規則了，但那是在
        記憶體裡；兩個同時進來的請求都會通過記憶體檢查，只有帶著 expected 的
        UPDATE 能讓其中一個的 rowcount 變成 0。
        """
