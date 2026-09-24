"""把 stage timeline 寫進 append-only 的稽核紀錄。

實作會設定 actor——誰造成這件事在建構 recorder 時就決定，不是每次呼叫再傳，
所以新的事件種類不可能不小心變成匿名的。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

from typing import Protocol

from app.domain.orchestration.entities import Deployment, ReleaseBundle


class WorkflowEventRecorder(Protocol):
    def append(
        self,
        *,
        stage: str,
        event_type: str,
        release: ReleaseBundle | None = None,
        deployment: Deployment | None = None,
        status: str | None = None,
        error_code: str | None = None,
        message: str | None = None,
    ) -> None: ...
