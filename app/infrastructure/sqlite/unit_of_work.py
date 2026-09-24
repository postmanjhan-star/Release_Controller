"""UnitOfWork 的 SQLAlchemy 實作。"""

from sqlalchemy.orm import Session

from app.domain.shared.unit_of_work import UnitOfWork


class SqlAlchemyUnitOfWork(UnitOfWork):
    """把 request 綁定的 Session 當成 transaction 邊界交給 UseCase。

    Repository 只 add / flush / execute，不 commit：一個 UseCase 可能要動兩個
    aggregate（建立 release 的同一個 transaction 也要建立它的 workflow
    instance），由誰結束 transaction 必須只有一個答案。
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()
