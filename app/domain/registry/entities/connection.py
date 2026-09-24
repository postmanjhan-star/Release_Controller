"""一條存起來的 Drone / Gitea 連線。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

import uuid
from datetime import datetime

from app.domain.registry.value_objects import (
    ConnectionKind,
    ConnectionVerifyStatus,
    EncryptedToken,
)


class Connection:
    """一個上游的位址加上一把憑證，外加它上次被驗證的結果。

    這裡沒有狀態機，規則只有兩條，而且兩條原本都散在 service 裡：

    * base_url 尾端的斜線不算差異——`https://drone/` 和 `https://drone` 是同一台。
    * **換掉 token 就要丟掉驗證結果。** 留著等於拿上一把 token 的結論去說新的
      那把可以用。

    「同一種類最多一個預設」不在這裡，那是 uq_upstream_connections_default_per_kind
    在擋的——兩個同時進來的請求，記憶體裡的檢查看不到彼此。
    """

    def __init__(
        self,
        *,
        id: str,
        kind: ConnectionKind,
        name: str,
        base_url: str,
        token: EncryptedToken,
        is_default: bool,
        created_at: datetime,
        updated_at: datetime,
        verify_status: ConnectionVerifyStatus | None = None,
        verify_detail: str | None = None,
        verified_at: datetime | None = None,
    ) -> None:
        self._id = id
        self._kind = kind
        self._name = name
        self._base_url = normalise_base_url(base_url)
        self._token = token
        self._is_default = is_default
        self._created_at = created_at
        self._updated_at = updated_at
        self._verify_status = verify_status
        self._verify_detail = verify_detail
        self._verified_at = verified_at

    # 唯讀屬性。名稱與 ConnectionResponse 的欄位一致。
    @property
    def id(self) -> str:
        return self._id

    @property
    def kind(self) -> ConnectionKind:
        return self._kind

    @property
    def name(self) -> str:
        return self._name

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def token(self) -> EncryptedToken:
        return self._token

    @property
    def token_encrypted(self) -> str:
        return self._token.cipher_text

    @property
    def token_hint(self) -> str:
        return self._token.hint

    @property
    def is_default(self) -> bool:
        return self._is_default

    @property
    def verify_status(self) -> ConnectionVerifyStatus | None:
        return self._verify_status

    @property
    def verify_detail(self) -> str | None:
        return self._verify_detail

    @property
    def verified_at(self) -> datetime | None:
        return self._verified_at

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Connection):
            return self._id == other._id
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._id)

    def __repr__(self) -> str:
        return f"Connection(id={self._id!r}, kind={self._kind.value}, name={self._name!r})"

    @staticmethod
    def create(
        *,
        kind: ConnectionKind,
        name: str,
        base_url: str,
        token: EncryptedToken,
        is_default: bool,
        now: datetime,
    ) -> "Connection":
        return Connection(
            id=str(uuid.uuid4()),
            kind=kind,
            name=name,
            base_url=base_url,
            token=token,
            is_default=is_default,
            created_at=now,
            updated_at=now,
        )

    def rename(self, name: str, *, at: datetime) -> None:
        self._name = name
        self._updated_at = at

    def point_at(self, base_url: str, *, at: datetime) -> None:
        self._base_url = normalise_base_url(base_url)
        self._updated_at = at

    def replace_token(self, token: EncryptedToken, *, at: datetime) -> None:
        """換憑證，並且丟掉上一把的驗證結果。

        沒有丟的話，列表會拿舊 token 的「ok」去描述一把還沒被試過的新 token。
        """
        self._token = token
        self._verify_status = None
        self._verify_detail = None
        self._verified_at = None
        self._updated_at = at

    def make_default(self, *, at: datetime) -> None:
        self._is_default = True
        self._updated_at = at

    def clear_default(self, *, at: datetime) -> None:
        self._is_default = False
        self._updated_at = at

    def touch(self, *, at: datetime) -> None:
        """記下這條連線被編輯過。

        即使這次編輯沒有實際改到任何欄位也會蓋上時間，跟 v1 的行為一樣。
        """
        self._updated_at = at

    def record_probe(
        self, status: ConnectionVerifyStatus, detail: str | None, *, at: datetime
    ) -> None:
        """記下上游怎麼回答，好讓列表不必每次載入都去打 Drone 和 Gitea。"""
        self._verify_status = status
        self._verify_detail = detail
        self._verified_at = at
        self._updated_at = at


def normalise_base_url(base_url: str) -> str:
    return base_url.rstrip("/")
