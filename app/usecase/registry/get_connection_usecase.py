"""讀出一條連線。"""

from abc import ABC, abstractmethod

from app.domain.registry.entities import Connection
from app.domain.registry.exceptions import ConnectionNotFoundError
from app.domain.registry.repositories import ConnectionRepository


class GetConnectionUseCase(ABC):
    @abstractmethod
    def execute(self, connection_id: str) -> Connection: ...


class GetConnectionUseCaseImpl(GetConnectionUseCase):
    def __init__(self, connections: ConnectionRepository) -> None:
        self.connections = connections

    def execute(self, connection_id: str) -> Connection:
        connection = self.connections.find_by_id(connection_id)
        if connection is None:
            raise ConnectionNotFoundError
        return connection


def new_get_connection_usecase(connections: ConnectionRepository) -> GetConnectionUseCase:
    return GetConnectionUseCaseImpl(connections)
