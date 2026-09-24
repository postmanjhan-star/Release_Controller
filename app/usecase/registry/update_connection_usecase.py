"""修改一條上游連線。"""

from abc import ABC, abstractmethod

from app.domain.registry.entities import Connection
from app.domain.registry.exceptions import ConnectionNotFoundError
from app.domain.registry.repositories import ConnectionRepository, TokenVault
from app.domain.shared.time import utc_now
from app.domain.shared.unit_of_work import UnitOfWork


class UpdateConnectionUseCase(ABC):
    @abstractmethod
    def execute(
        self,
        connection_id: str,
        *,
        name: str | None = None,
        base_url: str | None = None,
        token: str | None = None,
        is_default: bool | None = None,
    ) -> Connection: ...


class UpdateConnectionUseCaseImpl(UpdateConnectionUseCase):
    def __init__(
        self,
        connections: ConnectionRepository,
        vault: TokenVault,
        uow: UnitOfWork,
    ) -> None:
        self.connections = connections
        self.vault = vault
        self.uow = uow

    def execute(
        self,
        connection_id: str,
        *,
        name: str | None = None,
        base_url: str | None = None,
        token: str | None = None,
        is_default: bool | None = None,
    ) -> Connection:
        connection = self.connections.find_by_id(connection_id)
        if connection is None:
            raise ConnectionNotFoundError
        now = utc_now()
        if name is not None:
            connection.rename(name, at=now)
        if base_url is not None:
            connection.point_at(base_url, at=now)
        if token is not None:
            # entity 會順手丟掉上一把 token 的驗證結果。
            connection.replace_token(self.vault.encrypt(token), at=now)
        if is_default is True:
            self.connections.clear_default(connection.kind, except_id=connection.id)
            connection.make_default(at=now)
        elif is_default is False:
            connection.clear_default(at=now)
        connection.touch(at=now)
        self.connections.save(connection)
        self.uow.commit()
        return connection


def new_update_connection_usecase(
    connections: ConnectionRepository,
    vault: TokenVault,
    uow: UnitOfWork,
) -> UpdateConnectionUseCase:
    return UpdateConnectionUseCaseImpl(connections, vault, uow)
