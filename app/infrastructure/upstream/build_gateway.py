"""BuildGateway 的實作：包住 DroneBuildService，並且分類上游的失敗。

「哪些 Drone 錯誤代表促銷可能已經送達」是這裡的知識——它跟具體的例外類別綁在
一起。「未確認的促銷要怎麼處理」則是 use case 的規則。
"""

from typing import Any

from app.domain.orchestration.exceptions import UpstreamPromotionError, UpstreamUnavailable
from app.domain.orchestration.repositories import BuildGateway, PromotionBuild
from app.integrations.drone.exceptions import (
    DroneConnectionError,
    DroneError,
    DroneTimeoutError,
    DroneUnexpectedResponseError,
)
from app.services.drone_build_service import DroneBuildService

# 以這些錯誤結束的促銷從來沒有拿到答案，Drone 可能已經建了 build 也可能沒有。
# 其他任何錯誤都是明確的「沒有促銷發生」。
UNCONFIRMED_PROMOTE_ERROR_CODES = frozenset(
    {
        DroneTimeoutError.error_code,
        DroneConnectionError.error_code,
        DroneUnexpectedResponseError.error_code,
    }
)


class DroneBuildGateway(BuildGateway):
    def __init__(self, builds: DroneBuildService) -> None:
        self.builds = builds

    # 這兩個的例外原樣往外傳：呼叫端（route）要靠它們決定 HTTP 狀態碼。
    def validate_promotable(self, component: Any, build_number: int) -> PromotionBuild:
        return self.builds.validate_promotable(component, build_number)

    def get_drone_repo(self, component: Any) -> Any:
        return self.builds.get_drone_repo(component)

    def promote(self, component: Any, build_number: int, target: str) -> PromotionBuild:
        try:
            return self.builds.promote(component, build_number, target)
        except DroneError as exc:
            raise UpstreamPromotionError(
                exc.error_code,
                str(exc),
                confirmed=exc.error_code not in UNCONFIRMED_PROMOTE_ERROR_CODES,
            ) from exc

    def get_build(self, component: Any, build_number: int) -> PromotionBuild:
        try:
            return self.builds.get_build(component, build_number)
        except Exception as exc:  # noqa: BLE001 - 任何取不到 build 的原因都一樣處理
            raise _unavailable(exc) from exc

    def find_promotion_build(
        self, component: Any, source_build_number: int, target: str
    ) -> PromotionBuild | None:
        try:
            return self.builds.find_promotion_build(component, source_build_number, target)
        except Exception as exc:  # noqa: BLE001 - 同上
            raise _unavailable(exc) from exc


def _unavailable(exc: Exception) -> UpstreamUnavailable:
    return UpstreamUnavailable(
        getattr(exc, "error_code", "DRONE_UNAVAILABLE"),
        str(exc) or exc.__class__.__name__,
    )
