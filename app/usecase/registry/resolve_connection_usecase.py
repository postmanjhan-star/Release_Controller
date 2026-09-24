"""決定某個 component 該用哪條連線。"""

from abc import ABC, abstractmethod

from app.domain.registry.entities import Connection
from app.domain.registry.exceptions import (
    ConnectionNotFoundError,
    NoDefaultConnectionError,
)
from app.domain.registry.repositories import ConnectionRepository
from app.domain.registry.value_objects import ConnectionKind


class ResolveConnectionUseCase(ABC):
    @abstractmethod
    def execute(self, kind: ConnectionKind, *candidate_ids: str | None) -> Connection: ...


class ResolveConnectionUseCaseImpl(ResolveConnectionUseCase):
    def __init__(self, connections: ConnectionRepository) -> None:
        self.connections = connections

    def execute(self, kind: ConnectionKind, *candidate_ids: str | None) -> Connection:
        """第一個非空的候選，否則該種類的預設連線。

        這就是 component -> project -> 預設 這條繼承鏈，寫在一個地方，
        免得各處各自實作而慢慢走樣。
        """
        for candidate in candidate_ids:
            if candidate:
                connection = self.connections.find_by_id(candidate)
                if connection is None:
                    raise ConnectionNotFoundError
                return connection
        default = self.connections.find_default(kind)
        if default is None:
            raise NoDefaultConnectionError(
                f"No default {kind.value} connection is configured, and nothing "
                f"overrides it. Add one under Connections."
            )
        return default


def new_resolve_connection_usecase(
    connections: ConnectionRepository,
) -> ResolveConnectionUseCase:
    return ResolveConnectionUseCaseImpl(connections)
