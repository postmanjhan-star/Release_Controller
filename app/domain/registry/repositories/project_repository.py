"""專案 aggregate 的持久化介面。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

from abc import ABC, abstractmethod

from app.domain.registry.entities import Project


class ProjectRepository(ABC):
    @abstractmethod
    def add(self, project: Project) -> None:
        """寫入新專案與它的元件；key 撞號時 raise ProjectKeyTakenError。"""

    @abstractmethod
    def save(self, project: Project) -> None:
        """把整個 aggregate 寫回去——新增、修改、刪除與重新編號都在這一步。

        位置的寫法是實作細節而且不好省：SQLite 沒有 deferrable constraint，
        直接寫最終順序時，只要不是純粹的 append，就會有一瞬間兩個元件位置相同，
        撞上 uq_project_components_position。
        """

    @abstractmethod
    def find(self, project_ref: str) -> Project | None:
        """依 id 或 key 取得專案；key 是人眼前看到的東西，而且也是唯一的。"""

    @abstractmethod
    def list(self, *, include_archived: bool = False) -> list[Project]: ...
