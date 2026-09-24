"""部署編排的業務例外。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""


class DeploymentNotFoundError(Exception):
    pass


class DuplicatePromotionError(Exception):
    """同一個 build 已經被促銷到同一個 target。

    真正的保證是 uq_deployments_active_promotion；應用層的預先檢查只是為了給出
    一句讀得懂的話。
    """


class BundleNotFoundError(Exception):
    pass


class BundleNotRetryableError(Exception):
    pass


class ComponentNotSelectableError(Exception):
    pass


class UpstreamPromotionError(Exception):
    """促銷請求沒有成功。

    `confirmed` 是這個例外存在的理由：Drone 明確拒絕（401、404、被駁回的請求）
    時是 True，代表沒有任何促銷發生，可以直接把部署標成失敗；逾時、連線中斷、
    看不懂的回應則是 False，代表**請求可能已經抵達 Drone**，這時候標成失敗會
    同時弄丟一個可能正在跑的正式部署，並且釋放重複保護的位置。

    哪些上游錯誤算哪一種，由 infrastructure 的 gateway 決定；「未確認要怎麼辦」
    是這一層的規則。
    """

    def __init__(self, error_code: str, message: str, *, confirmed: bool) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.confirmed = confirmed


class UpstreamUnavailable(Exception):
    """連不上 Drone。

    這不是關於部署本身的證據——只有 Drone 對 build 的判決，或 workflow timeout，
    才可以讓一個部署失敗。
    """

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
