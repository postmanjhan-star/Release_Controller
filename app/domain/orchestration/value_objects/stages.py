"""元件對應到哪一個 BPMN stage。

frontend 與 backend 保留它們一直以來寫進事件的 stage 名稱，既有的 timeline 才
讀得下去，BPMN 圖也才對得上。其他元件用不分元件的那組 stage，是哪個元件則記在
事件自己身上。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

from app.domain.orchestration.value_objects.component import Component
from app.domain.orchestration.value_objects.workflow_stage import WorkflowStage

_PROMOTE_STAGES = {
    Component.FRONTEND.value: WorkflowStage.PROMOTE_FRONTEND,
    Component.BACKEND.value: WorkflowStage.PROMOTE_BACKEND,
}
_WAIT_STAGES = {
    Component.FRONTEND.value: WorkflowStage.WAIT_FRONTEND_DEPLOYMENT,
    Component.BACKEND.value: WorkflowStage.WAIT_BACKEND_DEPLOYMENT,
}
_VALIDATE_STAGES = {
    Component.FRONTEND.value: WorkflowStage.VALIDATE_FRONTEND,
    Component.BACKEND.value: WorkflowStage.VALIDATE_BACKEND,
}


def _key(component_key: str | Component) -> str:
    return component_key.value if isinstance(component_key, Component) else component_key


def promote_stage(component_key: str | Component) -> WorkflowStage:
    return _PROMOTE_STAGES.get(_key(component_key), WorkflowStage.PROMOTE_COMPONENT)


def wait_stage(component_key: str | Component) -> WorkflowStage:
    return _WAIT_STAGES.get(_key(component_key), WorkflowStage.WAIT_COMPONENT_DEPLOYMENT)


def validate_stage(component_key: str | Component) -> WorkflowStage:
    return _VALIDATE_STAGES.get(_key(component_key), WorkflowStage.VALIDATE_COMPONENT)
