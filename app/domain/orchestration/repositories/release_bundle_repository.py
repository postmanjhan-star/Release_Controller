"""Release bundle 的持久化介面。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

from abc import ABC, abstractmethod

from app.domain.orchestration.entities import Deployment, ReleaseBundle
from app.domain.orchestration.value_objects import BundleStatus


class ReleaseBundleRepository(ABC):
    @abstractmethod
    def add(self, bundle: ReleaseBundle) -> None: ...

    @abstractmethod
    def save(self, bundle: ReleaseBundle) -> None:
        """寫回 bundle 自己的欄位；底下的部署由 DeploymentRepository 負責。"""

    @abstractmethod
    def find_by_id(self, release_id: str) -> ReleaseBundle | None: ...

    @abstractmethod
    def ordered_deployments(self, bundle: ReleaseBundle) -> list[Deployment]:
        """依元件在專案裡的順序排好的部署。

        順序來自 project_components.position，不是 bundle 自己存的——一個部署
        不需要帶著一份之後可能被人編輯的位置副本。
        """

    @abstractmethod
    def search(
        self, *, status: BundleStatus | None, target: str | None, limit: int, offset: int
    ) -> tuple[list[ReleaseBundle], int]: ...
