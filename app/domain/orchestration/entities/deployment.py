"""一次元件促銷的生命週期。

狀態機：

    WAITING --claim--> PROMOTING --record_promotion--> DEPLOYING --> SUCCESS
                            |                              |
                            +---------- fail / cancel ------+--> FAILED / CANCELLED
    FAILED / CANCELLED --reset_for_retry--> WAITING

這個 entity 只負責「發生了這件事，狀態應該變成什麼」。**它不負責防止兩個人同時
促銷**——那要靠 repository 帶著 expected 狀態的 UPDATE，以及
uq_deployments_active_promotion 這個部分唯一索引。詳見
docs/backend-ddd-review.md 的「落差 2」。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

from datetime import datetime, timezone

from app.domain.orchestration.value_objects import (
    DeploymentStatus,
    PublishStatus,
    promote_stage,
    wait_stage,
)

TERMINAL_STATUSES = frozenset(
    {DeploymentStatus.SUCCESS, DeploymentStatus.FAILED, DeploymentStatus.CANCELLED}
)

# 只有 WAITING 的部署可以被認領去促銷。每一次促銷因此都從一個真正的狀態改變開始，
# 這正是「兩個人同時進來只有一個成功」得以成立的原因。
CLAIMABLE_FOR_PROMOTION = frozenset({DeploymentStatus.WAITING})

# 促銷請求以這些錯誤結束時，Drone 可能已經建了 build 也可能沒有。其他錯誤
# （401、404、被拒絕的請求）都是明確的「沒有促銷發生」，可以直接失敗。
PROMOTION_UNCONFIRMED_CODE = "DRONE_PROMOTE_UNCONFIRMED"


class Deployment:
    def __init__(
        self,
        *,
        id: str,
        project_id: str,
        component_id: str,
        component: str,
        drone_connection_id: str,
        drone_owner: str,
        drone_repository: str,
        source_build_number: int,
        commit_sha: str,
        branch: str,
        target: str,
        status: DeploymentStatus,
        created_at: datetime,
        updated_at: datetime,
        release_bundle_id: str | None = None,
        promotion_build_number: int | None = None,
        publish_status: PublishStatus = PublishStatus.NOT_PUBLISHED,
        version: str | None = None,
        gitea_release_id: str | None = None,
        gitea_release_tag: str | None = None,
        gitea_release_url: str | None = None,
        current_stage: str | None = None,
        failed_stage: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        cancel_reason: str | None = None,
        poll_error_code: str | None = None,
        poll_error_message: str | None = None,
        poll_failure_count: int = 0,
        requested_by: str | None = None,
        started_at: datetime | None = None,
        failed_at: datetime | None = None,
        cancelled_at: datetime | None = None,
        finished_at: datetime | None = None,
        publish_started_at: datetime | None = None,
        published_at: datetime | None = None,
        publish_failed_at: datetime | None = None,
        publish_error_code: str | None = None,
        publish_error_message: str | None = None,
    ) -> None:
        self._id = id
        self._project_id = project_id
        self._component_id = component_id
        self._component = component
        self._drone_connection_id = drone_connection_id
        self._drone_owner = drone_owner
        self._drone_repository = drone_repository
        self._source_build_number = source_build_number
        self._commit_sha = commit_sha
        self._branch = branch
        self._target = target
        self._status = status
        self._created_at = created_at
        self._updated_at = updated_at
        self._release_bundle_id = release_bundle_id
        self._promotion_build_number = promotion_build_number
        self._publish_status = publish_status
        self._version = version
        self._gitea_release_id = gitea_release_id
        self._gitea_release_tag = gitea_release_tag
        self._gitea_release_url = gitea_release_url
        self._current_stage = current_stage
        self._failed_stage = failed_stage
        self._error_code = error_code
        self._error_message = error_message
        self._cancel_reason = cancel_reason
        self._poll_error_code = poll_error_code
        self._poll_error_message = poll_error_message
        self._poll_failure_count = poll_failure_count
        self._requested_by = requested_by
        self._started_at = started_at
        self._failed_at = failed_at
        self._cancelled_at = cancelled_at
        self._finished_at = finished_at
        self._publish_started_at = publish_started_at
        self._published_at = published_at
        self._publish_failed_at = publish_failed_at
        self._publish_error_code = publish_error_code
        self._publish_error_message = publish_error_message

    # --- 唯讀屬性（名稱對齊 DeploymentResponse）---------------------------
    id = property(lambda self: self._id)
    project_id = property(lambda self: self._project_id)
    component_id = property(lambda self: self._component_id)
    component = property(lambda self: self._component)
    drone_connection_id = property(lambda self: self._drone_connection_id)
    drone_owner = property(lambda self: self._drone_owner)
    drone_repository = property(lambda self: self._drone_repository)
    source_build_number = property(lambda self: self._source_build_number)
    promotion_build_number = property(lambda self: self._promotion_build_number)
    commit_sha = property(lambda self: self._commit_sha)
    branch = property(lambda self: self._branch)
    target = property(lambda self: self._target)
    status = property(lambda self: self._status)
    release_bundle_id = property(lambda self: self._release_bundle_id)
    publish_status = property(lambda self: self._publish_status)
    version = property(lambda self: self._version)
    gitea_release_id = property(lambda self: self._gitea_release_id)
    gitea_release_tag = property(lambda self: self._gitea_release_tag)
    gitea_release_url = property(lambda self: self._gitea_release_url)
    current_stage = property(lambda self: self._current_stage)
    failed_stage = property(lambda self: self._failed_stage)
    error_code = property(lambda self: self._error_code)
    error_message = property(lambda self: self._error_message)
    cancel_reason = property(lambda self: self._cancel_reason)
    poll_error_code = property(lambda self: self._poll_error_code)
    poll_error_message = property(lambda self: self._poll_error_message)
    poll_failure_count = property(lambda self: self._poll_failure_count)
    requested_by = property(lambda self: self._requested_by)
    started_at = property(lambda self: self._started_at)
    failed_at = property(lambda self: self._failed_at)
    cancelled_at = property(lambda self: self._cancelled_at)
    finished_at = property(lambda self: self._finished_at)
    publish_started_at = property(lambda self: self._publish_started_at)
    published_at = property(lambda self: self._published_at)
    publish_failed_at = property(lambda self: self._publish_failed_at)
    publish_error_code = property(lambda self: self._publish_error_code)
    publish_error_message = property(lambda self: self._publish_error_message)
    created_at = property(lambda self: self._created_at)
    updated_at = property(lambda self: self._updated_at)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Deployment):
            return self._id == other._id
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._id)

    def __repr__(self) -> str:
        return f"Deployment(id={self._id!r}, component={self._component!r}, status={self._status.value})"

    # --- 判斷 ------------------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        return self._status in TERMINAL_STATUSES

    @property
    def is_claimable(self) -> bool:
        return self._status in CLAIMABLE_FOR_PROMOTION

    @property
    def promote_stage(self) -> str:
        return promote_stage(self._component).value

    @property
    def wait_stage(self) -> str:
        return wait_stage(self._component).value

    @property
    def active_stage(self) -> str:
        """目前這一步的 stage；沒有記錄就用等待部署那一段。"""
        return self._current_stage or self.wait_stage

    @property
    def awaits_promotion_outcome(self) -> bool:
        """促銷送出去了但沒拿到 build 編號。

        可能是 Drone 沒回應的促銷，也可能是認領之後、請求送出去之前就被中斷。
        兩種都只能靠「去找找看那個 build」解決，絕不能再送一次促銷。
        """
        return self._promotion_build_number is None

    def has_timed_out(self, *, now: datetime, timeout_seconds: float) -> bool:
        if self._started_at is None:
            return False
        started = self._started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return (now - started).total_seconds() > timeout_seconds

    # --- 狀態轉移 --------------------------------------------------------

    def claim_for_promotion(self, *, at: datetime) -> str:
        """記憶體端的認領。真正決定誰贏的是 repository 帶 expected 的 UPDATE。"""
        self._status = DeploymentStatus.PROMOTING
        self._current_stage = self.promote_stage
        self._started_at = self._started_at or at
        self._updated_at = at
        return self._current_stage

    def record_promotion(self, promotion_build_number: int, *, at: datetime) -> None:
        self._promotion_build_number = promotion_build_number
        self._status = DeploymentStatus.DEPLOYING
        self._current_stage = self.wait_stage
        self._updated_at = at

    def mark_promotion_unconfirmed(self, error_code: str, *, at: datetime) -> None:
        """Drone 沒有確認這次促銷。

        **不能標成 FAILED。** 那會同時弄丟一個可能正在跑的正式部署，並且釋放
        重複保護的位置——下一次重試就會促銷第二次。維持 PROMOTING，等下一次
        refresh 去對帳。
        """
        self._poll_error_code = PROMOTION_UNCONFIRMED_CODE
        self._poll_error_message = (
            f"Drone did not confirm the promotion request ({error_code}). "
            "Waiting to reconcile rather than promoting again."
        )
        self._poll_failure_count = (self._poll_failure_count or 0) + 1
        self._updated_at = at

    def record_poll_failure(self, error_code: str, message: str, *, at: datetime) -> bool:
        """記下「連不上 Drone」，但不改變部署狀態。

        回傳這是不是一個新出現的狀況——只有新狀況才該寫事件，否則一個每幾秒
        輪詢一次的介面會把 timeline 埋在一模一樣的行裡。
        """
        first_occurrence = self._poll_error_code != error_code
        self._poll_error_code = error_code
        self._poll_error_message = message
        self._poll_failure_count = (self._poll_failure_count or 0) + 1
        self._updated_at = at
        return first_occurrence

    def clear_poll_failure(self, *, at: datetime) -> str | None:
        """回傳剛剛從哪個錯誤恢復；本來就沒有錯誤則回傳 None。"""
        if self._poll_error_code is None:
            return None
        recovered_from = self._poll_error_code
        self._poll_error_code = None
        self._poll_error_message = None
        self._poll_failure_count = 0
        self._updated_at = at
        return recovered_from

    def keep_deploying(self, *, at: datetime) -> None:
        """Drone 的 build 還在跑。"""
        self._status = DeploymentStatus.DEPLOYING
        self._updated_at = at

    def succeed(self, *, at: datetime) -> str:
        stage = self.active_stage
        self._status = DeploymentStatus.SUCCESS
        self._current_stage = None
        self._finished_at = at
        self._updated_at = at
        return stage

    def fail(self, *, failed_stage: str, error_code: str, error_message: str, at: datetime) -> None:
        self._status = DeploymentStatus.FAILED
        self._current_stage = None
        self._failed_stage = failed_stage
        self._error_code = error_code
        self._error_message = error_message
        self._failed_at = at
        self._finished_at = at
        self._updated_at = at

    def cancel(self, reason: str, *, at: datetime) -> None:
        self._status = DeploymentStatus.CANCELLED
        self._current_stage = None
        self._cancel_reason = reason
        self._cancelled_at = at
        self._finished_at = at
        self._updated_at = at

    def reset_for_retry(self, *, at: datetime) -> tuple[DeploymentStatus, str | None]:
        """把失敗或取消的部署放回 WAITING，回傳它先前的狀態與錯誤碼。

        重設而不是換一列，是為了讓一個 bundle 對每個元件永遠只有一個部署；
        失敗的那次留在 append-only 的事件歷史裡。
        """
        previous = (self._status, self._error_code)
        self._status = DeploymentStatus.WAITING
        self._current_stage = None
        self._promotion_build_number = None
        self._failed_stage = None
        self._error_code = None
        self._error_message = None
        self._cancel_reason = None
        self._cancelled_at = None
        self._failed_at = None
        self._finished_at = None
        self._poll_error_code = None
        self._poll_error_message = None
        self._poll_failure_count = 0
        # started_at 清掉，workflow timeout 才是量這一次嘗試。
        self._started_at = None
        self._updated_at = at
        return previous
