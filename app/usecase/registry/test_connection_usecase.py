"""問上游：這條連線存的憑證還能用嗎。"""

from abc import ABC, abstractmethod
from datetime import datetime

from app.domain.registry.exceptions import ConnectionNotFoundError
from app.domain.registry.repositories import ConnectionProbe, ConnectionRepository
from app.domain.registry.value_objects import ConnectionVerifyStatus
from app.domain.shared.time import utc_now
from app.domain.shared.unit_of_work import UnitOfWork


class TestConnectionUseCase(ABC):
    @abstractmethod
    def execute(
        self, connection_id: str
    ) -> tuple[ConnectionVerifyStatus, str | None, datetime]: ...


class TestConnectionUseCaseImpl(TestConnectionUseCase):
    def __init__(
        self,
        connections: ConnectionRepository,
        probe: ConnectionProbe,
        uow: UnitOfWork,
    ) -> None:
        self.connections = connections
        self.probe = probe
        self.uow = uow

    def execute(self, connection_id: str) -> tuple[ConnectionVerifyStatus, str | None, datetime]:
        connection = self.connections.find_by_id(connection_id)
        if connection is None:
            raise ConnectionNotFoundError
        status, detail = self.probe.probe(connection)
        checked_at = utc_now()
        # 把答案記在那一列上，列表檢視才不用每次載入都去打 Drone 和 Gitea。
        connection.record_probe(status, detail, at=checked_at)
        self.connections.save(connection)
        self.uow.commit()
        return status, detail, checked_at


def new_test_connection_usecase(
    connections: ConnectionRepository,
    probe: ConnectionProbe,
    uow: UnitOfWork,
) -> TestConnectionUseCase:
    return TestConnectionUseCaseImpl(connections, probe, uow)
