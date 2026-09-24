"""一次涵蓋多個元件的發布。

Bundle 自己沒有狀態機——它的狀態是它底下那些部署的狀態聚合出來的。這裡的規則
只有兩條，而且兩條原本都埋在 `ReleaseOrchestrator` 的私有方法裡：

* **下一步要做什麼**：依元件順序走，停在第一個還沒結束的部署上。
* **失敗要記成哪一種**：`FAILED` 與 `PARTIAL_FAILURE` 只差一件事——有沒有元件
  已經成功上線了。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

from dataclasses import dataclass
from datetime import datetime

from app.domain.orchestration.entities.deployment import Deployment
from app.domain.orchestration.value_objects import (
    BundleStatus,
    DeploymentStatus,
    PublishStatus,
    ReleaseMode,
)

REFRESH = "refresh"
PROMOTE = "promote"
FAIL = "fail"
FINISH = "finish"
WAIT = "wait"

IN_FLIGHT_STATUSES = frozenset({DeploymentStatus.PROMOTING, DeploymentStatus.DEPLOYING})


@dataclass(frozen=True)
class BundleDecision:
    """依現況該做的下一件事。

    決定與執行分開：決定是純的（好測），執行要打 Drone（不好測）。
    """

    action: str
    deployment: Deployment | None = None


class ReleaseBundle:
    def __init__(
        self,
        *,
        id: str,
        mode: ReleaseMode,
        target: str,
        status: BundleStatus,
        deployment_status: BundleStatus,
        created_at: datetime,
        updated_at: datetime,
        project_id: str | None = None,
        selected_component_keys: str | None = None,
        publish_status: PublishStatus = PublishStatus.NOT_PUBLISHED,
        version: str | None = None,
        release_name: str | None = None,
        release_notes: str | None = None,
        workflow_instance_id: str | None = None,
        current_stage: str | None = None,
        failed_stage: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        requested_by: str | None = None,
        started_at: datetime | None = None,
        failed_at: datetime | None = None,
        finished_at: datetime | None = None,
        publish_started_at: datetime | None = None,
        published_at: datetime | None = None,
        publish_failed_at: datetime | None = None,
        publish_error_code: str | None = None,
        publish_error_message: str | None = None,
        deployments: list[Deployment] | None = None,
    ) -> None:
        self._id = id
        self._mode = mode
        self._target = target
        self._status = status
        self._deployment_status = deployment_status
        self._created_at = created_at
        self._updated_at = updated_at
        self._project_id = project_id
        self._selected_component_keys = selected_component_keys
        self._publish_status = publish_status
        self._version = version
        self._release_name = release_name
        self._release_notes = release_notes
        self._workflow_instance_id = workflow_instance_id
        self._current_stage = current_stage
        self._failed_stage = failed_stage
        self._error_code = error_code
        self._error_message = error_message
        self._requested_by = requested_by
        self._started_at = started_at
        self._failed_at = failed_at
        self._finished_at = finished_at
        self._publish_started_at = publish_started_at
        self._published_at = published_at
        self._publish_failed_at = publish_failed_at
        self._publish_error_code = publish_error_code
        self._publish_error_message = publish_error_message
        self._deployments = list(deployments or [])

    id = property(lambda self: self._id)
    mode = property(lambda self: self._mode)
    target = property(lambda self: self._target)
    status = property(lambda self: self._status)
    deployment_status = property(lambda self: self._deployment_status)
    project_id = property(lambda self: self._project_id)
    selected_component_keys = property(lambda self: self._selected_component_keys)
    publish_status = property(lambda self: self._publish_status)
    version = property(lambda self: self._version)
    release_name = property(lambda self: self._release_name)
    release_notes = property(lambda self: self._release_notes)
    workflow_instance_id = property(lambda self: self._workflow_instance_id)
    current_stage = property(lambda self: self._current_stage)
    failed_stage = property(lambda self: self._failed_stage)
    error_code = property(lambda self: self._error_code)
    error_message = property(lambda self: self._error_message)
    requested_by = property(lambda self: self._requested_by)
    started_at = property(lambda self: self._started_at)
    failed_at = property(lambda self: self._failed_at)
    finished_at = property(lambda self: self._finished_at)
    publish_started_at = property(lambda self: self._publish_started_at)
    published_at = property(lambda self: self._published_at)
    publish_failed_at = property(lambda self: self._publish_failed_at)
    publish_error_code = property(lambda self: self._publish_error_code)
    publish_error_message = property(lambda self: self._publish_error_message)
    created_at = property(lambda self: self._created_at)
    updated_at = property(lambda self: self._updated_at)
    deployments = property(lambda self: list(self._deployments))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ReleaseBundle):
            return self._id == other._id
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._id)

    def __repr__(self) -> str:
        return f"ReleaseBundle(id={self._id!r}, status={self._status.value})"

    # --- 決定下一步 ------------------------------------------------------

    def next_decision(self, ordered: list[Deployment]) -> BundleDecision:
        """依元件順序走一遍，回報停在哪裡、該做什麼。

        一步成功之後這一輪會繼續走到下一步——這就是 backend 立刻完成時
        frontend 會馬上被啟動的原因。
        """
        for step in ordered:
            if step.status in IN_FLIGHT_STATUSES:
                return BundleDecision(REFRESH, step)
            if step.status is DeploymentStatus.WAITING:
                return BundleDecision(PROMOTE, step)
            if step.status is DeploymentStatus.FAILED:
                return BundleDecision(FAIL, step)
            if step.status is not DeploymentStatus.SUCCESS:
                # 取消掉的：這一輪沒有別的事可做。
                return BundleDecision(WAIT, step)
        return BundleDecision(FINISH)

    @staticmethod
    def cancellation_reason(failed: Deployment) -> str:
        # 首字大寫，讓訊息跟元件還不能設定的年代一樣讀成 "Backend deployment failed"。
        return f"{failed.component.capitalize()} deployment failed"

    # --- 狀態轉移 --------------------------------------------------------

    def mirror(self, status: BundleStatus, stage: str | None, *, at: datetime) -> None:
        """跟著底下那個正在跑的部署一起走。"""
        self._status = status
        self._deployment_status = status
        self._current_stage = stage
        self._started_at = self._started_at or at
        self._updated_at = at

    def fail(self, failed: Deployment, *, any_succeeded: bool, at: datetime) -> BundleStatus:
        """停下這次發布，並且說清楚有沒有東西已經上了正式站。

        兩個元件的情況會退化成舊的那一對：backend 失敗時什麼都沒成功（FAILED），
        frontend 失敗時前面的 backend 已經成功（PARTIAL_FAILURE）。
        """
        status = BundleStatus.PARTIAL_FAILURE if any_succeeded else BundleStatus.FAILED
        self._status = status
        self._deployment_status = status
        self._current_stage = None
        self._failed_stage = failed.failed_stage
        self._error_code = failed.error_code
        self._error_message = failed.error_message
        self._failed_at = at
        self._finished_at = at
        self._updated_at = at
        return status

    def finish_success(self, *, at: datetime) -> None:
        self._status = BundleStatus.SUCCESS
        self._deployment_status = BundleStatus.SUCCESS
        self._current_stage = None
        self._finished_at = at
        self._updated_at = at
