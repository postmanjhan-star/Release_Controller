"""Transaction 邊界。

UseCase 是唯一決定「這次操作在哪裡結束」的地方，所以它需要一個能 commit 的
東西，而不需要知道那是 SQLAlchemy 的 Session。Repository 只寫入、不 commit。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

from abc import ABC, abstractmethod


class UnitOfWork(ABC):
    @abstractmethod
    def commit(self) -> None: ...

    @abstractmethod
    def rollback(self) -> None: ...
