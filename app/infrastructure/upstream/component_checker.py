"""拿真正的 Drone 與 Gitea 檢查一個元件。

解析連線、打上游、把上游的例外翻成一句話——三件事都在這裡，
所以 use case 只要負責「跑過每個啟用中的元件」。
"""

from collections.abc import Callable

from app.core.config import Settings
from app.core.crypto import TokenDecryptionError
from app.domain.registry.entities import Component, Project
from app.domain.registry.exceptions import (
    ConnectionNotFoundError,
    NoDefaultConnectionError,
)
from app.domain.registry.repositories import ComponentChecker
from app.domain.registry.value_objects import ComponentCheckResult, ConnectionKind
from app.integrations.drone.exceptions import DroneError, DroneNotFoundError
from app.integrations.factory import build_drone_client_for, build_gitea_client_for
from app.integrations.gitea.exceptions import GiteaError, GiteaNotFoundError
from app.usecase.registry.resolve_connection_usecase import ResolveConnectionUseCase


class UpstreamComponentChecker(ComponentChecker):
    def __init__(
        self,
        resolve_connection: ResolveConnectionUseCase,
        settings: Settings,
        *,
        drone_client: Callable[..., object] = build_drone_client_for,
        gitea_client: Callable[..., object] = build_gitea_client_for,
    ) -> None:
        self.resolve_connection = resolve_connection
        self.settings = settings
        # 跟 UpstreamConnectionProbe 同一個理由：要模擬「Drone 沒有這個 repo」時，
        # 換掉的是注入的東西，不是某個模組屬性。
        self.drone_client = drone_client
        self.gitea_client = gitea_client

    def check(self, project: Project, component: Component) -> ComponentCheckResult:
        drone_result: str | None = None
        gitea_result: str | None = None
        try:
            connection = self.resolve_connection.execute(
                ConnectionKind.DRONE,
                component.drone_connection_id,
                project.drone_connection_id,
            )
            self.drone_client(connection, self.settings).get_repository(
                component.drone_owner, component.drone_repo
            )
            drone_result = "ok"
        except DroneNotFoundError:
            return _failed(component, f"Drone has no repository {component.drone_slug}", None)
        except (
            DroneError,
            NoDefaultConnectionError,
            ConnectionNotFoundError,
            TokenDecryptionError,
        ) as exc:
            return _failed(component, str(exc) or exc.__class__.__name__, None)

        if not component.publish_enabled:
            gitea_result = "skipped"
        elif not component.gitea_slug:
            return _failed(
                component, "Publishing is enabled but no Gitea repository is set", drone_result
            )
        else:
            try:
                connection = self.resolve_connection.execute(
                    ConnectionKind.GITEA,
                    component.gitea_connection_id,
                    project.gitea_connection_id,
                )
                self.gitea_client(connection, self.settings).get_repository(
                    component.gitea_owner, component.gitea_repo
                )
                gitea_result = "ok"
            except GiteaNotFoundError:
                return _failed(
                    component, f"Gitea has no repository {component.gitea_slug}", drone_result
                )
            except (
                GiteaError,
                NoDefaultConnectionError,
                ConnectionNotFoundError,
                TokenDecryptionError,
            ) as exc:
                return _failed(component, str(exc) or exc.__class__.__name__, drone_result)

        return ComponentCheckResult(
            component_id=component.id,
            component_key=component.key,
            status="ok",
            drone=drone_result,
            gitea=gitea_result,
        )


def _failed(component: Component, detail: str, drone_result: str | None) -> ComponentCheckResult:
    return ComponentCheckResult(
        component_id=component.id,
        component_key=component.key,
        status="error",
        drone=drone_result,
        gitea=None,
        detail=detail,
    )
