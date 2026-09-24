"""UseCase 需要、但不屬於持久化的三個出口。

跟 Phase 2 的 workflow gateway 放在同一個位置：這些都是 domain 定義、
infrastructure 實作的出口，即使它們談的不是資料庫。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

from typing import Protocol

from app.domain.registry.entities import Connection
from app.domain.registry.value_objects import ConnectionVerifyStatus, EncryptedToken


class TokenVault(Protocol):
    """把明文憑證變成可以存起來的密文。

    加密需要金鑰，金鑰來自設定，所以這件事不能發生在 domain 裡。
    """

    def encrypt(self, plaintext: str) -> EncryptedToken: ...


class ConnectionProbe(Protocol):
    """去問上游：這把憑證還能用嗎。

    回答本身沒有失敗的概念——「token 被拒絕」是一個成功的答案。
    """

    def probe(self, connection: Connection) -> tuple[ConnectionVerifyStatus, str | None]: ...


class ConnectionUsage(Protocol):
    """有沒有 project 或 component 還指著這條連線。

    跨 aggregate 的唯讀查詢：刪除的安全性由 registry 那側回答，
    但 connection 這側需要知道答案。
    """

    def is_referenced(self, connection_id: str) -> bool: ...
