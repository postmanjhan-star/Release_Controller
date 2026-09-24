"""四個狀態轉移 use case 共用的執行順序。

順序本身是有意義的，而且很容易在複製貼上時走樣，所以只寫一次：

1. 讀出 entity；不存在就是 404。
2. **先**確保 workflow instance 存在。舊資料可能沒有 instance，補建時是依
   release 目前的狀態把步驟補完的——如果先改狀態再補建，補出來的 workflow 會
   多走一步。
3. 讓 entity 套用轉移。規則不合就在這裡 raise，不會碰到資料庫。
4. 帶著 entity 回報的前置狀態做 compare-and-swap。寫不到任何一列代表輸掉了
   競賽，要當成衝突。
5. 推進 workflow，然後由 UseCase（不是 repository）結束 transaction。
6. 重新讀一次，回傳資料庫真正的樣子。
"""

import logging
from collections.abc import Callable

from app.domain.release.entities import Release
from app.domain.release.exceptions import (
    InvalidReleaseTransitionError,
    ReleaseNotFoundError,
)
from app.domain.release.repositories import ReleaseRepository, ReleaseWorkflowGateway
from app.domain.release.value_objects import ReleaseStatus
from app.domain.shared.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)


def run_transition(
    *,
    releases: ReleaseRepository,
    workflows: ReleaseWorkflowGateway,
    uow: UnitOfWork,
    release_id: str,
    apply: Callable[[Release], ReleaseStatus],
    element_id: str,
    step_data: dict[str, str] | None = None,
) -> Release:
    release = _load(releases, release_id)
    instance = workflows.ensure_for_transition(release)
    expected = apply(release)
    if not releases.save_transition(release, expected=expected):
        # 另一個請求在這中間完成了轉移。先結束這個已經沒有用的 transaction，
        # 否則接下來重讀到的還是我們自己那份舊快照，錯誤訊息會變成
        # 「status is PENDING; expected PENDING」——v1 就是這樣。
        uow.rollback()
        raise InvalidReleaseTransitionError(_load(releases, release_id).status, expected)
    workflows.complete_step(instance, element_id, **(step_data or {}))
    uow.commit()
    return _load(releases, release_id)


def _load(releases: ReleaseRepository, release_id: str) -> Release:
    release = releases.find_by_id(release_id)
    if release is None:
        raise ReleaseNotFoundError
    return release
