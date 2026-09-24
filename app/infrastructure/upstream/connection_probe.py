"""ConnectionProbe 的實作：真的去打一次上游。

client builder 是建構參數而不是模組層的名字，所以測試要模擬「Drone 拒絕了
token」時，是換掉一個注入的東西，而不是 monkeypatch 某個模組屬性——那種寫法會
把測試綁死在模組路徑上，重構時會安靜地失效。
"""

from collections.abc import Callable

from app.core.config import Settings
from app.core.crypto import TokenDecryptionError
from app.domain.registry.entities import Connection
from app.domain.registry.repositories import ConnectionProbe
from app.domain.registry.value_objects import ConnectionKind, ConnectionVerifyStatus
from app.integrations.drone.exceptions import DroneAuthenticationError, DroneError
from app.integrations.factory import build_drone_client_for, build_gitea_client_for
from app.integrations.gitea.exceptions import GiteaAuthenticationError, GiteaError


class UpstreamConnectionProbe(ConnectionProbe):
    def __init__(
        self,
        settings: Settings,
        *,
        drone_client: Callable[..., object] = build_drone_client_for,
        gitea_client: Callable[..., object] = build_gitea_client_for,
    ) -> None:
        self.settings = settings
        self.drone_client = drone_client
        self.gitea_client = gitea_client

    def probe(self, connection: Connection) -> tuple[ConnectionVerifyStatus, str | None]:
        try:
            if connection.kind is ConnectionKind.DRONE:
                self.drone_client(connection, self.settings).status()
            else:
                self.gitea_client(connection, self.settings).health()
        except TokenDecryptionError as exc:
            return ConnectionVerifyStatus.KEY_MISMATCH, str(exc)
        except (DroneAuthenticationError, GiteaAuthenticationError) as exc:
            return ConnectionVerifyStatus.UNAUTHORIZED, str(exc) or "The token was rejected"
        except (DroneError, GiteaError) as exc:
            return ConnectionVerifyStatus.UNREACHABLE, str(exc) or exc.__class__.__name__
        return ConnectionVerifyStatus.OK, None
