"""UseCase 對 Drone 與 registry 的最小需求。

促銷這件事本來就要打上游、要知道元件對應哪個 repository，但 use case 不該為此
去 import 具體的 client 或 service。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

from typing import Any, Protocol


class PromotionBuild(Protocol):
    """Drone 的一個 build，只取促銷流程真正會讀的兩個欄位。"""

    number: int
    status: str


class DeployableComponent(Protocol):
    """一個可部署的元件。

    過渡期會有兩種形狀進來：orchestration 這側從 ComponentRegistry 拿到的 ORM
    列，以及 registry 那側已經搬好的 domain entity。兩者都滿足這個介面。
    """

    id: str
    key: str
    project_id: str
    is_active: bool


class BuildGateway(Protocol):
    """去 Drone 查 build、送促銷。"""

    def validate_promotable(self, component: Any, build_number: int) -> PromotionBuild: ...

    def promote(self, component: Any, build_number: int, target: str) -> PromotionBuild: ...

    def get_build(self, component: Any, build_number: int) -> PromotionBuild: ...

    def find_promotion_build(
        self, component: Any, source_build_number: int, target: str
    ) -> PromotionBuild | None:
        """找出 Drone 為一個沒有回應的促銷請求建立的 build。

        **絕不送出新的促銷**：找得到就認領它，找不到就維持未確認，交給 timeout
        決定。
        """

    def get_drone_repo(self, component: Any) -> Any: ...


class ComponentLookup(Protocol):
    """從部署或 key 找回它的元件設定。"""

    def for_deployment(self, deployment: Any) -> Any: ...

    def for_key(self, key: str, project: Any = None) -> Any: ...

    def project(self, project_ref: str | None) -> Any: ...

    def drone_connection(self, component: Any) -> Any: ...
