"""刪除一條上游連線。"""

from abc import ABC, abstractmethod

from app.domain.registry.exceptions import ConnectionInUseError, ConnectionNotFoundError
from app.domain.registry.repositories import ConnectionRepository, ConnectionUsage
from app.domain.shared.unit_of_work import UnitOfWork


class DeleteConnectionUseCase(ABC):
    @abstractmethod
    def execute(self, connection_id: str) -> None: ...


class DeleteConnectionUseCaseImpl(DeleteConnectionUseCase):
    def __init__(
        self,
        connections: ConnectionRepository,
        usage: ConnectionUsage,
        uow: UnitOfWork,
    ) -> None:
        self.connections = connections
        self.usage = usage
        self.uow = uow

    def execute(self, connection_id: str) -> None:
        connection = self.connections.find_by_id(connection_id)
        if connection is None:
            raise ConnectionNotFoundError
        # 外鍵是 RESTRICT，所以資料庫也會擋；先問一次是為了給得出「誰還在用」
        # 這種讀得懂的訊息，而不是一個 IntegrityError。
        if self.usage.is_referenced(connection_id):
            raise ConnectionInUseError(
                "A project or component still uses this connection. Point them "
                "somewhere else first."
            )
        self.connections.delete(connection)
        self.uow.commit()


def new_delete_connection_usecase(
    connections: ConnectionRepository,
    usage: ConnectionUsage,
    uow: UnitOfWork,
) -> DeleteConnectionUseCase:
    return DeleteConnectionUseCaseImpl(connections, usage, uow)
