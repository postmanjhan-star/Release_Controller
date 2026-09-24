"""促銷／輪詢的執行順序，用假的 repository 與上游測。

這裡驗的不是「狀態變成什麼」（那在 test_orchestration_domain.py），而是**順序**：
認領有沒有在打 Drone 之前 commit、未確認的促銷會不會被誤判成失敗、推進迴圈會不會
停下來。這些以前只能靠讀程式碼相信。
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.orchestration.entities import Deployment, ReleaseBundle
from app.domain.orchestration.exceptions import UpstreamPromotionError, UpstreamUnavailable
from app.domain.orchestration.value_objects import (
    BundleStatus,
    DeploymentStatus,
    ReleaseMode,
)
from app.usecase.orchestration._promotion import Orchestration, advance, promote, refresh

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


class Build:
    def __init__(self, number: int, status: str = "running") -> None:
        self.number = number
        self.status = status


class FakeDeployments:
    def __init__(self, *deployments: Deployment) -> None:
        self.items = {d.id: d for d in deployments}
        self.claims: list[str] = []
        self.claim_wins = True

    def find_by_id(self, deployment_id):
        return self.items.get(deployment_id)

    def save(self, deployment):
        self.items[deployment.id] = deployment

    def claim_for_promotion(self, deployment):
        self.claims.append(deployment.id)
        if self.claim_wins:
            self.items[deployment.id] = deployment
        return self.claim_wins

    def add(self, deployment):
        self.items[deployment.id] = deployment

    def find_active_promotion(self, **_kwargs):
        return None

    def search(self, *_a, **_k):
        return list(self.items.values()), len(self.items)


class FakeBundles:
    def __init__(self, ordered: list[Deployment] | None = None) -> None:
        self.ordered = ordered or []
        self.saved = 0

    def add(self, bundle, **_kwargs):
        pass

    def save(self, _bundle):
        self.saved += 1

    def find_by_id(self, _release_id):
        return None

    def ordered_deployments(self, _bundle):
        return list(self.ordered)

    def search(self, **_kwargs):
        return [], 0


class FakeEvents:
    def __init__(self, log: list) -> None:
        self.log = log

    def append(self, *, stage, event_type, **_kwargs):
        self.log.append(("event", event_type, stage))


class FakeUow:
    def __init__(self, log: list) -> None:
        self.log = log

    def commit(self):
        self.log.append(("commit", None, None))

    def rollback(self):
        self.log.append(("rollback", None, None))


class FakeBuilds:
    def __init__(
        self,
        log: list,
        *,
        promote_result=None,
        promote_error=None,
        build=None,
        get_error=None,
        found=None,
    ) -> None:
        self.log = log
        self.promote_result = promote_result
        self.promote_error = promote_error
        self.build = build
        self.get_error = get_error
        self.found = found

    def promote(self, _component, _number, _target):
        self.log.append(("drone", "promote", None))
        if self.promote_error:
            raise self.promote_error
        return self.promote_result

    def get_build(self, _component, _number):
        self.log.append(("drone", "get_build", None))
        if self.get_error:
            raise self.get_error
        return self.build

    def find_promotion_build(self, _component, _number, _target):
        self.log.append(("drone", "find_promotion_build", None))
        if self.get_error:
            raise self.get_error
        return self.found

    def validate_promotable(self, _c, _n):
        return self.build

    def get_drone_repo(self, _c):
        return None


class FakeRegistry:
    def for_deployment(self, _deployment):
        return object()

    def for_key(self, _key, _project=None):
        return object()

    def project(self, _ref):
        return object()

    def drone_connection(self, _component):
        return object()


def make_deployment(key="backend", status=DeploymentStatus.WAITING, **kwargs) -> Deployment:
    kwargs.setdefault("id", f"dep-{key}")
    return Deployment(
        project_id="p",
        component_id=f"c-{key}",
        component=key,
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


def make_bundle() -> ReleaseBundle:
    return ReleaseBundle(
        id="bundle",
        mode=ReleaseMode.BUNDLE,
        target="production",
        status=BundleStatus.PENDING,
        deployment_status=BundleStatus.PENDING,
        created_at=NOW,
        updated_at=NOW,
    )


def make_context(log, deployments, *, bundles=None, builds=None, timeout=600.0) -> Orchestration:
    return Orchestration(
        deployments=deployments,
        bundles=bundles or FakeBundles(),
        events=FakeEvents(log),
        uow=FakeUow(log),
        builds=builds or FakeBuilds(log),
        registry=FakeRegistry(),
        timeout_seconds=timeout,
    )


# --- 促銷的順序 ---------------------------------------------------------


def test_the_claim_is_committed_before_drone_is_called() -> None:
    """SQLite 的寫鎖不可以跨過一次 Drone 呼叫。"""
    log: list = []
    deployment = make_deployment()
    ctx = make_context(
        log, FakeDeployments(deployment), builds=FakeBuilds(log, promote_result=Build(901))
    )

    promote(ctx, deployment)

    kinds = [entry[0] for entry in log]
    assert kinds.index("commit") < kinds.index("drone"), log


def test_losing_the_claim_sends_nothing_upstream() -> None:
    log: list = []
    deployment = make_deployment()
    deployments = FakeDeployments(deployment)
    deployments.claim_wins = False
    ctx = make_context(log, deployments, builds=FakeBuilds(log, promote_result=Build(901)))

    promote(ctx, deployment)

    assert not any(entry[0] == "drone" for entry in log)
    assert deployments.claims == [deployment.id]


def test_an_unconfirmed_promotion_does_not_fail_the_deployment() -> None:
    log: list = []
    deployment = make_deployment()
    ctx = make_context(
        log,
        FakeDeployments(deployment),
        builds=FakeBuilds(
            log, promote_error=UpstreamPromotionError("DRONE_TIMEOUT", "timed out", confirmed=False)
        ),
    )

    promote(ctx, deployment)

    assert deployment.status is DeploymentStatus.PROMOTING
    assert deployment.awaits_promotion_outcome
    assert ("event", "PROMOTION_UNCONFIRMED", "PROMOTE_BACKEND") in log


def test_a_rejected_promotion_fails_immediately() -> None:
    log: list = []
    deployment = make_deployment()
    ctx = make_context(
        log,
        FakeDeployments(deployment),
        builds=FakeBuilds(
            log, promote_error=UpstreamPromotionError("DRONE_AUTH", "rejected", confirmed=True)
        ),
    )

    promote(ctx, deployment)

    assert deployment.status is DeploymentStatus.FAILED
    assert deployment.error_code == "DRONE_AUTH"


# --- 輪詢的順序 ---------------------------------------------------------


def test_a_timed_out_deployment_fails_without_asking_drone() -> None:
    log: list = []
    # 相對於**真實**時鐘往前兩小時：choreography 內部用 utc_now()，
    # 就跟它取代的那段程式碼一樣。
    deployment = make_deployment(
        status=DeploymentStatus.DEPLOYING,
        promotion_build_number=901,
        started_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    ctx = make_context(log, FakeDeployments(deployment), timeout=60.0)

    refresh(ctx, deployment)

    assert deployment.status is DeploymentStatus.FAILED
    assert deployment.error_code == "WORKFLOW_TIMEOUT"
    assert not any(entry[0] == "drone" for entry in log)


def test_a_deployment_without_a_build_number_reconciles_instead_of_promoting() -> None:
    log: list = []
    deployment = make_deployment(
        status=DeploymentStatus.PROMOTING, started_at=datetime.now(timezone.utc)
    )
    ctx = make_context(
        log, FakeDeployments(deployment), builds=FakeBuilds(log, found=Build(901, "running"))
    )

    refresh(ctx, deployment)

    calls = [entry[1] for entry in log if entry[0] == "drone"]
    assert calls == ["find_promotion_build"], "絕不可以在對帳時送出第二次促銷"
    assert deployment.promotion_build_number == 901
    assert deployment.status is DeploymentStatus.DEPLOYING


def test_an_unreachable_drone_leaves_the_deployment_alone() -> None:
    log: list = []
    deployment = make_deployment(
        status=DeploymentStatus.DEPLOYING,
        promotion_build_number=901,
        started_at=datetime.now(timezone.utc),
    )
    ctx = make_context(
        log,
        FakeDeployments(deployment),
        builds=FakeBuilds(log, get_error=UpstreamUnavailable("DRONE_TIMEOUT", "no answer")),
    )

    refresh(ctx, deployment)

    assert deployment.status is DeploymentStatus.DEPLOYING
    assert deployment.poll_error_code == "DRONE_TIMEOUT"


@pytest.mark.parametrize(
    ("drone_status", "expected"),
    [
        ("running", DeploymentStatus.DEPLOYING),
        ("success", DeploymentStatus.SUCCESS),
        ("failure", DeploymentStatus.FAILED),
        ("killed", DeploymentStatus.FAILED),
    ],
)
def test_the_drone_verdict_decides_the_outcome(drone_status, expected) -> None:
    log: list = []
    deployment = make_deployment(
        status=DeploymentStatus.DEPLOYING,
        promotion_build_number=901,
        started_at=datetime.now(timezone.utc),
    )
    ctx = make_context(
        log, FakeDeployments(deployment), builds=FakeBuilds(log, build=Build(901, drone_status))
    )

    refresh(ctx, deployment)

    assert deployment.status is expected


# --- Bundle 推進 --------------------------------------------------------


def test_advancing_stops_at_a_step_that_is_still_running() -> None:
    """迴圈必須停下來——同一步一直回報 REFRESH 會轉不完。"""
    log: list = []
    backend = make_deployment(
        "backend",
        status=DeploymentStatus.DEPLOYING,
        promotion_build_number=901,
        started_at=datetime.now(timezone.utc),
    )
    deployments = FakeDeployments(backend)
    bundles = FakeBundles([backend])
    ctx = make_context(
        log, deployments, bundles=bundles, builds=FakeBuilds(log, build=Build(901, "running"))
    )

    advance(ctx, make_bundle())

    assert backend.status is DeploymentStatus.DEPLOYING
    assert [entry[1] for entry in log if entry[0] == "drone"] == ["get_build"]


def test_a_step_that_succeeds_lets_the_next_one_start() -> None:
    log: list = []
    backend = make_deployment("backend", status=DeploymentStatus.SUCCESS)
    frontend = make_deployment("frontend")
    deployments = FakeDeployments(backend, frontend)
    bundles = FakeBundles([backend, frontend])
    ctx = make_context(
        log, deployments, bundles=bundles, builds=FakeBuilds(log, promote_result=Build(902))
    )

    advance(ctx, make_bundle())

    assert frontend.status is DeploymentStatus.DEPLOYING
    assert frontend.promotion_build_number == 902


def test_a_failed_step_cancels_the_ones_still_waiting() -> None:
    log: list = []
    backend = make_deployment("backend", status=DeploymentStatus.FAILED)
    frontend = make_deployment("frontend")
    bundles = FakeBundles([backend, frontend])
    bundle = make_bundle()
    ctx = make_context(log, FakeDeployments(backend, frontend), bundles=bundles)

    advance(ctx, bundle)

    assert frontend.status is DeploymentStatus.CANCELLED
    assert frontend.cancel_reason == "Backend deployment failed"
    assert bundle.status is BundleStatus.FAILED


def test_every_step_succeeding_finishes_the_bundle() -> None:
    log: list = []
    steps = [
        make_deployment("backend", status=DeploymentStatus.SUCCESS),
        make_deployment("frontend", status=DeploymentStatus.SUCCESS),
    ]
    bundle = make_bundle()
    ctx = make_context(log, FakeDeployments(*steps), bundles=FakeBundles(steps))

    advance(ctx, bundle)

    assert bundle.status is BundleStatus.SUCCESS
    assert ("event", "RELEASE_COMPLETED", "COMPLETE_DEPLOYMENT") in log
