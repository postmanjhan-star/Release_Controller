"""促銷與輪詢的執行順序，四個 use case 共用。

順序本身就是這一叢集最重要的東西，而且很容易在複製貼上時走樣：

* 認領是一次 compare-and-swap，而且**在任何網路呼叫之前就 commit**。SQLite 的
  寫鎖如果跨過一次 Drone 呼叫，一個慢的上游會讓這個 process 的其他寫入全部變成
  "database is locked"。
* 上游呼叫因此夾在兩個 transaction 之間，不是包在一個裡面。
* 促銷沒有拿到答案時維持 PROMOTING。標成 FAILED 會同時弄丟一個可能正在跑的
  正式部署，並且釋放 uq_deployments_active_promotion 的位置——下次重試會促銷
  第二次。
* 連不上 Drone 不是關於部署的證據。只有 Drone 對 build 的判決，或 workflow
  timeout，才可以讓一個部署失敗。
"""

import logging
from dataclasses import dataclass

from app.domain.orchestration.entities import (
    FAIL,
    FINISH,
    PROMOTE,
    REFRESH,
    Deployment,
    ReleaseBundle,
)
from app.domain.orchestration.exceptions import UpstreamPromotionError, UpstreamUnavailable
from app.domain.orchestration.repositories import (
    BuildGateway,
    ComponentLookup,
    DeploymentRepository,
    ReleaseBundleRepository,
    WorkflowEventRecorder,
)
from app.domain.orchestration.value_objects import (
    BundleStatus,
    DeploymentStatus,
    WorkflowEventType,
    WorkflowStage,
)
from app.domain.shared.time import utc_now
from app.domain.shared.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)

RUNNING_BUILD_STATUSES = frozenset({"pending", "running", "blocked"})


@dataclass(frozen=True)
class Orchestration:
    """驅動一次促銷所需要的全部東西。"""

    deployments: DeploymentRepository
    bundles: ReleaseBundleRepository
    events: WorkflowEventRecorder
    uow: UnitOfWork
    builds: BuildGateway
    registry: ComponentLookup
    timeout_seconds: float


# --- 促銷 ---------------------------------------------------------------


def promote(ctx: Orchestration, deployment: Deployment, bundle: ReleaseBundle | None = None):
    component = ctx.registry.for_deployment(deployment)
    stage = deployment.promote_stage
    if not _claim(ctx, deployment, bundle):
        return ctx.deployments.find_by_id(deployment.id)

    try:
        promotion = ctx.builds.promote(component, deployment.source_build_number, deployment.target)
    except UpstreamPromotionError as exc:
        if exc.confirmed:
            # Drone 直接拒絕了，所以沒有任何促銷存在。
            _fail(ctx, deployment, stage, exc.error_code, exc.message, bundle)
        else:
            _mark_unconfirmed(ctx, deployment, exc, stage, bundle)
        ctx.deployments.save(deployment)
        ctx.uow.commit()
        return ctx.deployments.find_by_id(deployment.id)

    _record_promotion(ctx, deployment, promotion, stage, bundle)
    ctx.deployments.save(deployment)
    ctx.uow.commit()
    return ctx.deployments.find_by_id(deployment.id)


def _claim(ctx: Orchestration, deployment: Deployment, bundle: ReleaseBundle | None) -> bool:
    now = utc_now()
    stage = deployment.claim_for_promotion(at=now)
    if not ctx.deployments.claim_for_promotion(deployment):
        logger.info(
            "Promotion claim skipped deployment_id=%s component=%s",
            deployment.id,
            deployment.component,
        )
        return False
    if bundle is not None:
        bundle.mirror(BundleStatus.PROMOTING, stage, at=now)
        ctx.bundles.save(bundle)
    ctx.events.append(
        release=bundle,
        deployment=deployment,
        stage=stage,
        event_type=WorkflowEventType.STAGE_STARTED.value,
        status=deployment.status.value,
    )
    # 刻意在這裡 commit：把認領公開給其他 session，並且在任何 HTTP 之前放掉寫鎖。
    ctx.uow.commit()
    return True


def _record_promotion(ctx, deployment: Deployment, promotion, stage: str, bundle) -> None:
    now = utc_now()
    deployment.record_promotion(promotion.number, at=now)
    if bundle is not None:
        bundle.mirror(BundleStatus.DEPLOYING, deployment.current_stage, at=now)
        ctx.bundles.save(bundle)
    ctx.events.append(
        release=bundle,
        deployment=deployment,
        stage=stage,
        event_type=WorkflowEventType.STAGE_SUCCEEDED.value,
        status=deployment.status.value,
    )
    ctx.events.append(
        release=bundle,
        deployment=deployment,
        stage=stage,
        event_type=WorkflowEventType.PROMOTION_CREATED.value,
        status=deployment.status.value,
        message=f"Drone promotion build #{promotion.number} created",
    )
    ctx.events.append(
        release=bundle,
        deployment=deployment,
        stage=deployment.current_stage,
        event_type=WorkflowEventType.STAGE_STARTED.value,
        status=deployment.status.value,
    )
    logger.info(
        "Promotion created release_id=%s deployment_id=%s component=%s stage=%s "
        "repo=%s/%s source_build_number=%s promotion_build_number=%s target=%s",
        deployment.release_bundle_id,
        deployment.id,
        deployment.component,
        deployment.current_stage,
        deployment.drone_owner,
        deployment.drone_repository,
        deployment.source_build_number,
        deployment.promotion_build_number,
        deployment.target,
    )


def _mark_unconfirmed(ctx, deployment: Deployment, exc, stage: str, bundle) -> None:
    deployment.mark_promotion_unconfirmed(exc.error_code, at=utc_now())
    ctx.events.append(
        release=bundle,
        deployment=deployment,
        stage=stage,
        event_type=WorkflowEventType.PROMOTION_UNCONFIRMED.value,
        status=deployment.status.value,
        error_code=exc.error_code,
        message=exc.message,
    )
    logger.warning(
        "Promotion outcome unknown deployment_id=%s component=%s repo=%s/%s "
        "source_build_number=%s target=%s error_code=%s",
        deployment.id,
        deployment.component,
        deployment.drone_owner,
        deployment.drone_repository,
        deployment.source_build_number,
        deployment.target,
        exc.error_code,
    )


# --- 輪詢 ---------------------------------------------------------------


def refresh(ctx: Orchestration, deployment: Deployment, bundle: ReleaseBundle | None = None):
    """更新一個進行中的部署。**不 commit**：由呼叫端界定 transaction。"""
    if deployment.is_terminal:
        return deployment
    stage = deployment.active_stage

    if deployment.has_timed_out(now=utc_now(), timeout_seconds=ctx.timeout_seconds):
        _fail(
            ctx,
            deployment,
            stage,
            "WORKFLOW_TIMEOUT",
            "Deployment exceeded the configured workflow timeout",
            bundle,
        )
        ctx.deployments.save(deployment)
        return deployment

    if deployment.awaits_promotion_outcome:
        _reconcile(ctx, deployment, stage, bundle)
        ctx.deployments.save(deployment)
        return deployment

    component = ctx.registry.for_deployment(deployment)
    try:
        build = ctx.builds.get_build(component, deployment.promotion_build_number)
    except UpstreamUnavailable as exc:
        _record_poll_failure(ctx, deployment, exc.error_code, exc.message, stage, bundle)
        ctx.deployments.save(deployment)
        return deployment

    _clear_poll_failure(ctx, deployment, stage, bundle)
    status = build.status.lower()
    ctx.events.append(
        release=bundle,
        deployment=deployment,
        stage=stage,
        event_type=WorkflowEventType.DRONE_STATUS_CHANGED.value,
        status=status,
        message=f"Drone promotion build #{build.number} is {status}",
    )
    if status in RUNNING_BUILD_STATUSES:
        deployment.keep_deploying(at=utc_now())
    elif status == "success":
        _succeed(ctx, deployment, bundle)
    else:
        _fail(
            ctx,
            deployment,
            stage,
            "DRONE_BUILD_KILLED" if status == "killed" else "DRONE_BUILD_FAILED",
            f"Drone promotion build #{build.number} {status}",
            bundle,
        )
    ctx.deployments.save(deployment)
    return deployment


def _reconcile(ctx, deployment: Deployment, stage: str, bundle) -> None:
    """認領 Drone 為一個沒有回應的請求建立的 build。絕不送出新的促銷。"""
    component = ctx.registry.for_deployment(deployment)
    try:
        promotion = ctx.builds.find_promotion_build(
            component, deployment.source_build_number, deployment.target
        )
    except UpstreamUnavailable as exc:
        _record_poll_failure(ctx, deployment, exc.error_code, exc.message, stage, bundle)
        return

    if promotion is None:
        from app.domain.orchestration.entities import PROMOTION_UNCONFIRMED_CODE

        _record_poll_failure(
            ctx,
            deployment,
            PROMOTION_UNCONFIRMED_CODE,
            "No promotion build found for this deployment yet",
            stage,
            bundle,
        )
        return

    _clear_poll_failure(ctx, deployment, stage, bundle)
    ctx.events.append(
        release=bundle,
        deployment=deployment,
        stage=stage,
        event_type=WorkflowEventType.PROMOTION_RECONCILED.value,
        status=deployment.status.value,
        message=(
            f"Adopted existing Drone promotion build #{promotion.number} "
            "instead of creating a second one"
        ),
    )
    logger.info(
        "Promotion reconciled deployment_id=%s component=%s source_build_number=%s "
        "promotion_build_number=%s",
        deployment.id,
        deployment.component,
        deployment.source_build_number,
        promotion.number,
    )
    _record_promotion(ctx, deployment, promotion, stage, bundle)


def _record_poll_failure(ctx, deployment: Deployment, code: str, message: str, stage, bundle):
    first_occurrence = deployment.record_poll_failure(code, message, at=utc_now())
    if first_occurrence:
        ctx.events.append(
            release=bundle,
            deployment=deployment,
            stage=stage,
            event_type=WorkflowEventType.UPSTREAM_UNAVAILABLE.value,
            status=deployment.status.value,
            error_code=code,
            message=message,
        )
    logger.warning(
        "Deployment poll failed, keeping status deployment_id=%s status=%s "
        "error_code=%s consecutive=%s",
        deployment.id,
        deployment.status.value,
        code,
        deployment.poll_failure_count,
    )


def _clear_poll_failure(ctx, deployment: Deployment, stage: str, bundle) -> None:
    recovered_from = deployment.clear_poll_failure(at=utc_now())
    if recovered_from is None:
        return
    ctx.events.append(
        release=bundle,
        deployment=deployment,
        stage=stage,
        event_type=WorkflowEventType.UPSTREAM_RECOVERED.value,
        status=deployment.status.value,
        message=f"Drone is reachable again after {recovered_from}",
    )


# --- 終態 ---------------------------------------------------------------


def _succeed(ctx, deployment: Deployment, bundle) -> None:
    stage = deployment.succeed(at=utc_now())
    ctx.events.append(
        release=bundle,
        deployment=deployment,
        stage=stage,
        event_type=WorkflowEventType.STAGE_SUCCEEDED.value,
        status=deployment.status.value,
    )


def _fail(ctx, deployment: Deployment, stage: str, code: str, message: str, bundle) -> None:
    deployment.fail(failed_stage=stage, error_code=code, error_message=message, at=utc_now())
    ctx.events.append(
        release=bundle,
        deployment=deployment,
        stage=stage,
        event_type=WorkflowEventType.STAGE_FAILED.value,
        status=deployment.status.value,
        error_code=code,
        message=message,
    )
    logger.error(
        "Deployment failed release_id=%s deployment_id=%s component=%s stage=%s "
        "status=%s error_code=%s",
        deployment.release_bundle_id,
        deployment.id,
        deployment.component,
        stage,
        deployment.status.value,
        code,
    )


def cancel(ctx, deployment: Deployment, reason: str, bundle) -> None:
    deployment.cancel(reason, at=utc_now())
    ctx.events.append(
        release=bundle,
        deployment=deployment,
        stage=deployment.promote_stage,
        event_type=WorkflowEventType.DEPLOYMENT_CANCELLED.value,
        status=deployment.status.value,
        message=reason,
    )
    ctx.deployments.save(deployment)


def reset_for_retry(ctx, deployment: Deployment, bundle) -> None:
    previous_status, previous_error = deployment.reset_for_retry(at=utc_now())
    ctx.events.append(
        release=bundle,
        deployment=deployment,
        stage=deployment.promote_stage,
        event_type=WorkflowEventType.DEPLOYMENT_RETRIED.value,
        status=deployment.status.value,
        message=(
            f"Retrying after {previous_status.value}"
            + (f" ({previous_error})" if previous_error else "")
        ),
    )
    ctx.deployments.save(deployment)


# --- Bundle 推進 --------------------------------------------------------


def advance(ctx: Orchestration, bundle: ReleaseBundle) -> None:
    """一輪一輪把 bundle 往前推，直到它停在某一步或走完。

    一步成功之後會繼續走到下一步，這就是 backend 立刻完成時 frontend 會馬上
    被啟動的原因。停在還沒結束的那一步上，這一輪就結束。
    """
    # 每一輪最多只能往前跨過一個步驟，所以圈數有上界。加這個上界是因為漏掉
    # 下面那個「動作之後再檢查一次」的判斷會讓迴圈轉不完——那時候寧可留下一行
    # 錯誤紀錄然後退出，也不要把一個 worker 卡死在這裡。
    for _ in range(len(ctx.bundles.ordered_deployments(bundle)) + 1):
        ordered = ctx.bundles.ordered_deployments(bundle)
        decision = bundle.next_decision(ordered)
        if decision.action == FINISH:
            _finish_bundle(ctx, bundle)
            return
        if decision.action == FAIL:
            _fail_bundle(ctx, bundle, decision.deployment, ordered)
            return
        if decision.action not in (REFRESH, PROMOTE):
            return

        acted_on = decision.deployment.id
        if decision.action == REFRESH:
            refresh(ctx, decision.deployment, bundle)
        else:
            promote(ctx, decision.deployment, bundle)

        after = ctx.deployments.find_by_id(acted_on)
        if after is None:
            return
        if after.status is DeploymentStatus.FAILED:
            _fail_bundle(ctx, bundle, after, ctx.bundles.ordered_deployments(bundle))
            return
        if after.status is not DeploymentStatus.SUCCESS:
            # 還在促銷、部署中或已取消：這一輪沒有別的事可做。
            return
    else:
        logger.error("Bundle advance did not settle release_id=%s; giving up this pass", bundle.id)


def _fail_bundle(ctx, bundle: ReleaseBundle, failed: Deployment, steps) -> None:
    reason = ReleaseBundle.cancellation_reason(failed)
    for later in steps:
        if later.status is DeploymentStatus.WAITING:
            cancel(ctx, later, reason, bundle)
    any_succeeded = any(step.status is DeploymentStatus.SUCCESS for step in steps)
    bundle.fail(failed, any_succeeded=any_succeeded, at=utc_now())
    ctx.bundles.save(bundle)


def _finish_bundle(ctx, bundle: ReleaseBundle) -> None:
    bundle.finish_success(at=utc_now())
    ctx.bundles.save(bundle)
    ctx.events.append(
        release=bundle,
        stage=WorkflowStage.COMPLETE_DEPLOYMENT.value,
        event_type=WorkflowEventType.RELEASE_COMPLETED.value,
        status=bundle.status.value,
    )
    ctx.events.append(
        release=bundle,
        stage=WorkflowStage.WAIT_PUBLISH_REQUEST.value,
        event_type=WorkflowEventType.STAGE_STARTED.value,
        status=bundle.status.value,
    )
