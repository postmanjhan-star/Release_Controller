"""新增一條上游連線。"""

import logging
from abc import ABC, abstractmethod

from app.domain.registry.entities import Connection
from app.domain.registry.repositories import ConnectionRepository, TokenVault
from app.domain.registry.value_objects import ConnectionKind
from app.domain.shared.time import utc_now
from app.domain.shared.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)


class CreateConnectionUseCase(ABC):
    @abstractmethod
    def execute(
        self,
        *,
        kind: ConnectionKind,
        name: str,
        base_url: str,
        token: str,
        is_default: bool,
    ) -> Connection: ...


class CreateConnectionUseCaseImpl(CreateConnectionUseCase):
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
        *,
        kind: ConnectionKind,
        name: str,
        base_url: str,
        token: str,
        is_default: bool,
    ) -> Connection:
        # 一個種類的第一條連線自動成為預設：只有一台 Drone 的安裝，不應該需要
        # 先知道「預設連線」這個概念存在。
        make_default = is_default or not self.connections.exists_for_kind(kind)
        if make_default:
            # 先清掉舊的預設再寫新的，中間沒有任何一刻同種類有兩個預設——
            # uq_upstream_connections_default_per_kind 不會被違反。
            self.connections.clear_default(kind)
        connection = Connection.create(
            kind=kind,
            name=name,
            base_url=base_url,
            token=self.vault.encrypt(token),
            is_default=make_default,
            now=utc_now(),
        )
        self.connections.add(connection)
        self.uow.commit()
        return connection


def new_create_connection_usecase(
    connections: ConnectionRepository,
    vault: TokenVault,
    uow: UnitOfWork,
) -> CreateConnectionUseCase:
    return CreateConnectionUseCaseImpl(connections, vault, uow)
