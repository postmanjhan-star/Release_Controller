"""上游連線的持久化介面。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

from abc import ABC, abstractmethod

from app.domain.registry.entities import Connection
from app.domain.registry.value_objects import ConnectionKind


class ConnectionRepository(ABC):
    @abstractmethod
    def add(self, connection: Connection) -> None:
        """新增一條連線；名稱撞號時 raise ConnectionNameTakenError。"""

    @abstractmethod
    def save(self, connection: Connection) -> None:
        """寫回既有連線；名稱撞號時 raise ConnectionNameTakenError。"""

    @abstractmethod
    def delete(self, connection: Connection) -> None: ...

    @abstractmethod
    def find_by_id(self, connection_id: str) -> Connection | None: ...

    @abstractmethod
    def list(self, kind: ConnectionKind | None = None) -> list[Connection]: ...

    @abstractmethod
    def find_default(self, kind: ConnectionKind) -> Connection | None: ...

    @abstractmethod
    def exists_for_kind(self, kind: ConnectionKind) -> bool: ...

    @abstractmethod
    def clear_default(self, kind: ConnectionKind, *, except_id: str | None = None) -> None:
        """把這個種類的預設旗標一次清掉。

        一句 UPDATE，而且要在設定新的預設**之前**跑，否則
        uq_upstream_connections_default_per_kind 會看到同一種類有兩個預設。
        逐一讀出來改也做得到，但中間那一刻就會違反索引。
        """
