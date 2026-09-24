"""部署的持久化介面。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.domain.orchestration.entities import Deployment
from app.domain.orchestration.value_objects import DeploymentStatus


@dataclass(frozen=True)
class DeploymentFilters:
    # 元件的 key，不是 enum：專案自己命名它的元件。
    component: str | None = None
    status: DeploymentStatus | None = None
    target: str | None = None
    release_bundle_id: str | None = None


class DeploymentRepository(ABC):
    @abstractmethod
    def add(self, deployment: Deployment) -> None:
        """寫入一筆新部署。

        撞到 uq_deployments_active_promotion 時 raise DuplicatePromotionError。
        那個部分唯一索引才是重複促銷真正的保證；應用層的預先檢查只是為了給出
        一句讀得懂的話。
        """

    @abstractmethod
    def save(self, deployment: Deployment) -> None:
        """把部署生命週期的欄位寫回去（不含發布狀態）。"""

    @abstractmethod
    def find_by_id(self, deployment_id: str) -> Deployment | None: ...

    @abstractmethod
    def find_active_promotion(
        self,
        *,
        drone_connection_id: str,
        drone_owner: str,
        drone_repository: str,
        source_build_number: int,
        target: str,
    ) -> str | None:
        """同一個促銷位置上還活著的部署 id，沒有就回 None。

        這只是為了給出一個好讀的 409。真正的保證是那個部分唯一索引——這次
        SELECT 與呼叫端的 INSERT 之間，另一個請求還是可以插進來。
        """

    @abstractmethod
    def claim_for_promotion(self, deployment: Deployment) -> bool:
        """把一個 WAITING 的部署原子地換成 PROMOTING。

        回傳 False 代表別人先認領走了；此時這個 entity 的內容已經不可信，
        呼叫端要重讀。

        認領一定要是一次真正的狀態改變，這樣同時進來的第二個促銷才看得見。
        """

    @abstractmethod
    def search(
        self, filters: DeploymentFilters, *, limit: int, offset: int
    ) -> tuple[list[Deployment], int]: ...
