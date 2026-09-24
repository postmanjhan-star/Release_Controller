"""UseCase 對 BPMN workflow 的最小需求。

實作是 SpiffWorkflow + 序列化狀態（`app/services/workflow_service.py`），
但 usecase 不需要知道，也不該為了推進一個 workflow 而 import SpiffWorkflow。

handle 對 domain 而言是不透明的：gateway 給什麼，usecase 就原樣傳回去。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

from typing import Any, Protocol

from app.domain.release.entities import Release


class ReleaseWorkflowGateway(Protocol):
    def create_for_release(self, release_id: str) -> Any: ...

    def ensure_for_transition(self, release: Release) -> Any:
        """取得這個 release 的 workflow instance，舊資料沒有就依現況補建。"""
        ...

    def complete_step(self, instance: Any, element_id: str, **data: str) -> None: ...
