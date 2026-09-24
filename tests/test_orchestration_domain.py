"""Deployment 與 ReleaseBundle 的規則測試——不建資料庫、不打 Drone。

這一叢集是整個專案最不能弄壞的部分，而在此之前它的狀態機完全沒有單元測試：
要驗「Drone 沒回應時不可以標成 FAILED」得起一個真的 SQLite、mock httpx、
再走一次 orchestrator。

（結構不變式在 test_release_domain.py，掃的是整個 app/domain。）
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.orchestration.entities import (
    FAIL,
    FINISH,
    PROMOTE,
    PROMOTION_UNCONFIRMED_CODE,
    REFRESH,
    WAIT,
    Deployment,
    ReleaseBundle,
)
from app.domain.orchestration.value_objects import (
    BundleStatus,
    DeploymentStatus,
    ReleaseMode,
    promote_stage,
    validate_stage,
    wait_stage,
)

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=30)


def make_deployment(
    component: str = "backend", status: DeploymentStatus = DeploymentStatus.WAITING, **kwargs
) -> Deployment:
    kwargs.setdefault("id", f"dep-{component}")
    return Deployment(
        project_id="proj",
        component_id=f"comp-{component}",
        component=component,
        drone_connection_id="conn",
        drone_owner="102573",
        drone_repository="soda",
        source_build_number=77,
        commit_sha="a" * 40,
        branch="main",
        target="production",
        status=status,
        created_at=NOW,
        updated_at=NOW,
        **kwargs,
    )


def make_bundle(*deployments: Deployment, status: BundleStatus = BundleStatus.PENDING):
    return ReleaseBundle(
        id="bundle",
        mode=ReleaseMode.BUNDLE,
        target="production",
        status=status,
        deployment_status=status,
        created_at=NOW,
        updated_at=NOW,
        deployments=list(deployments),
    )


# --- stage 命名 ---------------------------------------------------------


def test_frontend_and_backend_keep_their_historical_stage_names() -> None:
    assert promote_stage("backend").value == "PROMOTE_BACKEND"
    assert wait_stage("frontend").value == "WAIT_FRONTEND_DEPLOYMENT"
    assert validate_stage("backend").value == "VALIDATE_BACKEND"


def test_any_other_component_uses_the_component_agnostic_stages() -> None:
    assert promote_stage("worker").value == "PROMOTE_COMPONENT"
    assert wait_stage("worker").value == "WAIT_COMPONENT_DEPLOYMENT"
    assert validate_stage("worker").value == "VALIDATE_COMPONENT"


# --- Deployment 生命週期 ------------------------------------------------


def test_a_new_deployment_waits_and_can_be_claimed() -> None:
    deployment = make_deployment()

    assert deployment.status is DeploymentStatus.WAITING
    assert deployment.is_claimable
    assert not deployment.is_terminal


def test_claiming_starts_the_clock_once_only() -> None:
    deployment = make_deployment()

    stage = deployment.claim_for_promotion(at=NOW)

    assert deployment.status is DeploymentStatus.PROMOTING
    assert stage == "PROMOTE_BACKEND" == deployment.current_stage
    assert deployment.started_at == NOW

    # 重試同一個部署時 started_at 不能被往後推，否則 timeout 永遠不會到。
    deployment.claim_for_promotion(at=LATER)
    assert deployment.started_at == NOW


def test_recording_the_promotion_moves_to_deploying() -> None:
    deployment = make_deployment()
    deployment.claim_for_promotion(at=NOW)

    deployment.record_promotion(901, at=LATER)

    assert deployment.status is DeploymentStatus.DEPLOYING
    assert deployment.promotion_build_number == 901
    assert deployment.current_stage == "WAIT_BACKEND_DEPLOYMENT"
    assert not deployment.awaits_promotion_outcome


def test_an_unconfirmed_promotion_stays_promoting() -> None:
    """這是整個叢集最重要的一條規則。

    標成 FAILED 會同時弄丟一個可能正在跑的正式部署，並且釋放
    uq_deployments_active_promotion 的位置——下一次重試就會促銷第二次。
    """
    deployment = make_deployment()
    deployment.claim_for_promotion(at=NOW)

    deployment.mark_promotion_unconfirmed("DRONE_TIMEOUT", at=LATER)

    assert deployment.status is DeploymentStatus.PROMOTING
    assert deployment.poll_error_code == PROMOTION_UNCONFIRMED_CODE
    assert "rather than promoting again" in deployment.poll_error_message
    assert deployment.poll_failure_count == 1
    # 還沒有 build 編號，所以下一次 refresh 會去對帳而不是再促銷一次。
    assert deployment.awaits_promotion_outcome


def test_a_poll_failure_is_only_news_the_first_time() -> None:
    deployment = make_deployment(status=DeploymentStatus.DEPLOYING)

    assert deployment.record_poll_failure("DRONE_TIMEOUT", "timed out", at=NOW) is True
    assert deployment.record_poll_failure("DRONE_TIMEOUT", "timed out", at=NOW) is False
    assert deployment.record_poll_failure("DRONE_UNAVAILABLE", "down", at=NOW) is True
    assert deployment.poll_failure_count == 3
    # 輪詢失敗不是關於部署本身的證據，狀態不能動。
    assert deployment.status is DeploymentStatus.DEPLOYING


def test_recovering_reports_what_it_recovered_from() -> None:
    deployment = make_deployment(status=DeploymentStatus.DEPLOYING)
    deployment.record_poll_failure("DRONE_TIMEOUT", "timed out", at=NOW)

    assert deployment.clear_poll_failure(at=LATER) == "DRONE_TIMEOUT"
    assert deployment.poll_error_code is None
    assert deployment.poll_failure_count == 0
    # 本來就沒壞掉的話不該冒出一則「恢復了」的事件。
    assert deployment.clear_poll_failure(at=LATER) is None


def test_succeeding_reports_the_stage_it_was_on() -> None:
    deployment = make_deployment(status=DeploymentStatus.DEPLOYING, current_stage="WAIT_X")

    assert deployment.succeed(at=LATER) == "WAIT_X"
    assert deployment.status is DeploymentStatus.SUCCESS
    assert deployment.current_stage is None
    assert deployment.finished_at == LATER
    assert deployment.is_terminal


def test_a_deployment_with_no_stage_falls_back_to_waiting_for_deployment() -> None:
    assert make_deployment().active_stage == "WAIT_BACKEND_DEPLOYMENT"
    assert make_deployment(current_stage="PROMOTE_BACKEND").active_stage == "PROMOTE_BACKEND"


def test_failing_records_where_and_why() -> None:
    deployment = make_deployment(status=DeploymentStatus.DEPLOYING)

    deployment.fail(
        failed_stage="WAIT_BACKEND_DEPLOYMENT",
        error_code="DRONE_BUILD_FAILED",
        error_message="build 901 failure",
        at=LATER,
    )

    assert deployment.status is DeploymentStatus.FAILED
    assert deployment.failed_stage == "WAIT_BACKEND_DEPLOYMENT"
    assert deployment.error_code == "DRONE_BUILD_FAILED"
    assert deployment.failed_at == LATER == deployment.finished_at
    assert deployment.current_stage is None


def test_cancelling_keeps_the_reason() -> None:
    deployment = make_deployment()

    deployment.cancel("Backend deployment failed", at=LATER)

    assert deployment.status is DeploymentStatus.CANCELLED
    assert deployment.cancel_reason == "Backend deployment failed"
    assert deployment.is_terminal


def test_a_retry_clears_the_previous_attempt_including_the_clock() -> None:
    deployment = make_deployment(status=DeploymentStatus.DEPLOYING, promotion_build_number=901)
    deployment.claim_for_promotion(at=NOW)
    deployment.fail(failed_stage="S", error_code="E", error_message="m", at=NOW)

    previous = deployment.reset_for_retry(at=LATER)

    assert previous == (DeploymentStatus.FAILED, "E")
    assert deployment.status is DeploymentStatus.WAITING
    assert deployment.promotion_build_number is None
    assert deployment.error_code is None
    assert deployment.failed_stage is None
    assert deployment.finished_at is None
    # started_at 一定要清掉，否則 timeout 會拿上一次的時間來算這一次。
    assert deployment.started_at is None


# --- timeout ------------------------------------------------------------


def test_a_deployment_that_never_started_cannot_time_out() -> None:
    assert make_deployment().has_timed_out(now=LATER, timeout_seconds=1) is False


def test_the_timeout_is_measured_from_the_start_of_this_attempt() -> None:
    deployment = make_deployment()
    deployment.claim_for_promotion(at=NOW)

    assert deployment.has_timed_out(now=NOW + timedelta(seconds=59), timeout_seconds=60) is False
    assert deployment.has_timed_out(now=NOW + timedelta(seconds=61), timeout_seconds=60) is True


def test_a_naive_timestamp_from_sqlite_is_read_as_utc() -> None:
    """SQLite 拿回來的欄位沒有 tzinfo，直接相減會 TypeError。"""
    deployment = make_deployment(started_at=datetime(2026, 9, 5, 12, 0))

    assert deployment.has_timed_out(now=NOW + timedelta(seconds=61), timeout_seconds=60) is True


# --- ReleaseBundle 的推進決定 -------------------------------------------


def test_a_bundle_promotes_the_first_waiting_component() -> None:
    backend, frontend = make_deployment("backend"), make_deployment("frontend")
    bundle = make_bundle(backend, frontend)

    decision = bundle.next_decision([backend, frontend])

    assert decision.action == PROMOTE
    assert decision.deployment is backend


def test_a_bundle_refreshes_a_component_that_is_in_flight() -> None:
    backend = make_deployment("backend", status=DeploymentStatus.DEPLOYING)
    frontend = make_deployment("frontend")
    bundle = make_bundle(backend, frontend)

    assert bundle.next_decision([backend, frontend]).action == REFRESH


def test_a_finished_component_lets_the_walk_continue() -> None:
    backend = make_deployment("backend", status=DeploymentStatus.SUCCESS)
    frontend = make_deployment("frontend")
    bundle = make_bundle(backend, frontend)

    decision = bundle.next_decision([backend, frontend])

    assert decision.action == PROMOTE
    assert decision.deployment is frontend


def test_every_component_succeeding_finishes_the_bundle() -> None:
    steps = [
        make_deployment("backend", status=DeploymentStatus.SUCCESS),
        make_deployment("frontend", status=DeploymentStatus.SUCCESS),
    ]

    assert make_bundle(*steps).next_decision(steps).action == FINISH


def test_a_failed_component_stops_the_bundle() -> None:
    backend = make_deployment("backend", status=DeploymentStatus.FAILED)
    steps = [backend, make_deployment("frontend")]

    decision = make_bundle(*steps).next_decision(steps)

    assert decision.action == FAIL
    assert decision.deployment is backend


def test_a_cancelled_component_ends_the_pass_without_finishing() -> None:
    steps = [make_deployment("backend", status=DeploymentStatus.CANCELLED)]

    assert make_bundle(*steps).next_decision(steps).action == WAIT


# --- ReleaseBundle 的狀態 -----------------------------------------------


@pytest.mark.parametrize(
    ("any_succeeded", "expected"),
    [(False, BundleStatus.FAILED), (True, BundleStatus.PARTIAL_FAILURE)],
)
def test_partial_failure_is_failure_with_something_already_live(
    any_succeeded: bool, expected: BundleStatus
) -> None:
    failed = make_deployment("frontend", status=DeploymentStatus.DEPLOYING)
    failed.fail(failed_stage="WAIT_X", error_code="E", error_message="m", at=LATER)
    bundle = make_bundle(failed)

    status = bundle.fail(failed, any_succeeded=any_succeeded, at=LATER)

    assert status is expected is bundle.status
    assert bundle.deployment_status is expected
    # 失敗的原因原封不動從那個部署身上抄過來。
    assert (bundle.failed_stage, bundle.error_code, bundle.error_message) == ("WAIT_X", "E", "m")
    assert bundle.finished_at == LATER


def test_the_cancellation_reason_reads_like_it_always_has() -> None:
    assert ReleaseBundle.cancellation_reason(make_deployment("backend")) == (
        "Backend deployment failed"
    )


def test_mirroring_starts_the_bundle_clock_once() -> None:
    bundle = make_bundle()

    bundle.mirror(BundleStatus.PROMOTING, "PROMOTE_BACKEND", at=NOW)
    bundle.mirror(BundleStatus.DEPLOYING, "WAIT_BACKEND_DEPLOYMENT", at=LATER)

    assert bundle.status is BundleStatus.DEPLOYING
    assert bundle.deployment_status is BundleStatus.DEPLOYING
    assert bundle.current_stage == "WAIT_BACKEND_DEPLOYMENT"
    assert bundle.started_at == NOW


def test_finishing_clears_the_stage() -> None:
    bundle = make_bundle()

    bundle.finish_success(at=LATER)

    assert bundle.status is BundleStatus.SUCCESS
    assert bundle.current_stage is None
    assert bundle.finished_at == LATER
