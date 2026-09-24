"""列出存起來的連線。"""

from abc import ABC, abstractmethod

from app.domain.registry.entities import Connection
from app.domain.registry.repositories import ConnectionRepository
from app.domain.registry.value_objects import ConnectionKind


class ListConnectionsUseCase(ABC):
    @abstractmethod
    def execute(self, kind: ConnectionKind | None = None) -> list[Connection]: ...


class ListConnectionsUseCaseImpl(ListConnectionsUseCase):
    def __init__(self, connections: ConnectionRepository) -> None:
        self.connections = connections

    def execute(self, kind: ConnectionKind | None = None) -> list[Connection]:
        return self.connections.list(kind)


def new_list_connections_usecase(connections: ConnectionRepository) -> ListConnectionsUseCase:
    return ListConnectionsUseCaseImpl(connections)
