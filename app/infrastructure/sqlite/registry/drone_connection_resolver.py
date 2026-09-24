"""每個元件實際會用到的 Drone 連線 id。

位置衝突要靠這張表才算得出來，而 component -> project -> 預設 的繼承鏈
要讀資料庫，所以不能放在 entity 裡。
"""

from app.domain.registry.entities import Project
from app.domain.registry.exceptions import (
    ConnectionNotFoundError,
    NoDefaultConnectionError,
)
from app.domain.registry.repositories import DroneConnectionResolver
from app.domain.registry.value_objects import ConnectionKind
from app.usecase.registry.resolve_connection_usecase import ResolveConnectionUseCase


class ConnectionRegistryResolver(DroneConnectionResolver):
    def __init__(self, resolve_connection: ResolveConnectionUseCase) -> None:
        self.resolve_connection = resolve_connection

    def drone_connection_ids(self, project: Project) -> dict[str, str]:
        resolved: dict[str, str] = {}
        for component in project.components:
            try:
                resolved[component.id] = self.resolve_connection.execute(
                    ConnectionKind.DRONE,
                    component.drone_connection_id,
                    project.drone_connection_id,
                ).id
            except (NoDefaultConnectionError, ConnectionNotFoundError):
                # 解析不出來的就留白，Project.slot_conflicts 會用 UNRESOLVED 代入，
                # 兩個都在等預設連線的元件因此仍然會被視為相撞。
                continue
        return resolved
