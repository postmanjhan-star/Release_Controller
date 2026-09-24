"""專案 use case 需要、但不屬於這個 aggregate 的出口。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

from typing import Protocol

from app.domain.registry.entities import Component, Project
from app.domain.registry.value_objects import ComponentCheckResult


class ComponentUsage(Protocol):
    """部署歷史或排程還指著這個元件嗎。

    跨 aggregate 的唯讀查詢：答案在 orchestration 那一側，但刪除的判斷在這裡。
    """

    def has_deployments(self, component_id: str) -> bool: ...

    def is_scheduled(self, component_id: str) -> bool: ...


class ComponentChecker(Protocol):
    """拿真正的 Drone 與 Gitea 檢查一個元件設定得對不對。

    要解析連線、要打上游、要把上游的例外翻成人看得懂的一句話——三件事都在
    infrastructure。
    """

    def check(self, project: Project, component: Component) -> ComponentCheckResult: ...


class DroneConnectionResolver(Protocol):
    """每個元件實際會用到的 Drone 連線 id，解析不出來的就不放進去。

    位置衝突要靠它才算得出來，而那條 component -> project -> 預設的繼承鏈
    需要讀資料庫。
    """

    def drone_connection_ids(self, project: Project) -> dict[str, str]: ...
