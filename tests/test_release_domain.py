"""Release entity 的規則測試——不建資料庫、不進 HTTP。

在 Phase 2 之前這些規則寫在 `ReleaseService` 的 SQL `WHERE` 子句裡，要測
「REJECTED 不能再被核准」得先起一個 SQLite。這個檔案是那次搬移換到的東西。

最後兩個測試不是行為測試而是結構不變式：domain 層一旦開始 import
SQLAlchemy 或外層模組，整個分層就退化成註解，而那件事不會有任何行為測試發現。
"""

import ast
import os
from datetime import datetime, timezone

import pytest

from app.domain.release.entities import Release
from app.domain.release.exceptions import InvalidReleaseTransitionError
from app.domain.release.value_objects import ReleaseStatus

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 9, 4, 13, 0, tzinfo=timezone.utc)


def make_release(status: ReleaseStatus = ReleaseStatus.PENDING) -> Release:
    release = Release.create(
        repository="release-controller",
        branch="main",
        commit_sha="0" * 40,
        environment="production",
        message="first",
        now=NOW,
    )
    if status is ReleaseStatus.PENDING:
        return release
    if status is ReleaseStatus.REJECTED:
        release.reject(by="operator", message="no", at=LATER)
        return release
    release.approve(by="operator", at=LATER)
    if status is ReleaseStatus.APPROVED:
        return release
    release.start_deployment(at=LATER)
    if status is ReleaseStatus.DEPLOYING:
        return release
    release.finish_deployment(status=status, message=None, at=LATER)
    return release


def test_a_new_release_waits_for_approval() -> None:
    release = make_release()

    assert release.status is ReleaseStatus.PENDING
    assert release.approved_at is None
    assert release.rejected_at is None
    assert release.created_at == NOW == release.updated_at
    assert release.id


def test_approving_records_who_and_when() -> None:
    release = make_release()

    expected = release.approve(by="operator", at=LATER)

    assert release.status is ReleaseStatus.APPROVED
    assert release.approved_by == "operator"
    assert release.approved_at == LATER
    assert release.updated_at == LATER
    # 轉移回報自己的前置狀態，repository 拿它當 compare-and-swap 的條件。
    assert expected is ReleaseStatus.PENDING


def test_rejecting_replaces_the_message() -> None:
    release = make_release()

    expected = release.reject(by="operator", message="not this commit", at=LATER)

    assert release.status is ReleaseStatus.REJECTED
    assert release.rejected_by == "operator"
    assert release.message == "not this commit"
    assert expected is ReleaseStatus.PENDING


def test_the_whole_happy_path() -> None:
    release = make_release()

    assert release.approve(by="operator", at=LATER) is ReleaseStatus.PENDING
    assert release.start_deployment(at=LATER) is ReleaseStatus.APPROVED
    assert (
        release.finish_deployment(status=ReleaseStatus.SUCCESS, message="done", at=LATER)
        is ReleaseStatus.DEPLOYING
    )
    assert release.status is ReleaseStatus.SUCCESS
    assert release.deploy_started_at == LATER
    assert release.deploy_finished_at == LATER


@pytest.mark.parametrize(
    ("status", "action"),
    [
        (ReleaseStatus.APPROVED, "approve"),
        (ReleaseStatus.REJECTED, "approve"),
        (ReleaseStatus.SUCCESS, "approve"),
        (ReleaseStatus.APPROVED, "reject"),
        (ReleaseStatus.DEPLOYING, "reject"),
        (ReleaseStatus.PENDING, "start"),
        (ReleaseStatus.DEPLOYING, "start"),
        (ReleaseStatus.PENDING, "finish"),
        (ReleaseStatus.APPROVED, "finish"),
        (ReleaseStatus.SUCCESS, "finish"),
    ],
)
def test_illegal_transitions_are_refused(status: ReleaseStatus, action: str) -> None:
    release = make_release(status)
    call = {
        "approve": lambda: release.approve(by="operator", at=LATER),
        "reject": lambda: release.reject(by="operator", message=None, at=LATER),
        "start": lambda: release.start_deployment(at=LATER),
        "finish": lambda: release.finish_deployment(
            status=ReleaseStatus.SUCCESS, message=None, at=LATER
        ),
    }[action]

    with pytest.raises(InvalidReleaseTransitionError):
        call()

    assert release.status is status, "被拒絕的轉移不可以留下半套狀態"


def test_the_refusal_message_is_the_one_the_v1_api_promises() -> None:
    release = make_release(ReleaseStatus.APPROVED)

    with pytest.raises(InvalidReleaseTransitionError) as raised:
        release.approve(by="operator", at=LATER)

    # tests/test_releases.py 與 test_workflows.py 斷言在這個字串上。
    assert str(raised.value) == "Release status is APPROVED; expected PENDING"


def test_a_deployment_cannot_finish_in_a_non_terminal_status() -> None:
    release = make_release(ReleaseStatus.DEPLOYING)

    with pytest.raises(ValueError):
        release.finish_deployment(status=ReleaseStatus.PENDING, message=None, at=LATER)


def test_identity_is_the_id_not_the_fields() -> None:
    one = make_release()
    same_id = Release(
        id=one.id,
        repository="somewhere-else",
        branch="other",
        commit_sha="1" * 40,
        environment="staging",
        status=ReleaseStatus.FAILED,
        created_at=NOW,
        updated_at=NOW,
    )

    assert one == same_id
    assert one != make_release()
    assert len({one, same_id}) == 1


# --- 結構不變式 ---------------------------------------------------------


def domain_modules() -> list[str]:
    paths = []
    for root, dirs, files in os.walk("app/domain"):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        paths += [os.path.join(root, f) for f in files if f.endswith(".py")]
    return sorted(paths)


def imported_modules(path: str) -> list[str]:
    tree = ast.parse(open(path, encoding="utf-8").read())
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
    return names


def test_the_domain_layer_imports_no_framework() -> None:
    forbidden = {"sqlalchemy", "fastapi", "pydantic", "alembic", "SpiffWorkflow"}
    offenders = [
        (path, module)
        for path in domain_modules()
        for module in imported_modules(path)
        if module.split(".")[0] in forbidden
    ]

    assert offenders == []


def test_the_domain_layer_imports_nothing_from_the_outer_layers() -> None:
    offenders = [
        (path, module)
        for path in domain_modules()
        for module in imported_modules(path)
        if module.startswith("app.") and not module.startswith("app.domain")
    ]

    assert offenders == []
