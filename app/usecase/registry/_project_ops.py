"""專案 use case 共用的三個小動作。

抽出來不是為了少打字，是因為「存檔前要重算位置衝突」這件事有六個 use case 都
要做，而漏掉其中一個不會有任何測試發現——衝突會延後到有人發版時才以一個
409 冒出來。
"""

from app.domain.registry.entities import Project
from app.domain.registry.exceptions import (
    ConnectionNotFoundError,
    ProjectNotFoundError,
)
from app.domain.registry.repositories import (
    ConnectionRepository,
    DroneConnectionResolver,
    ProjectRepository,
)
from app.domain.shared.unit_of_work import UnitOfWork


def load_project(projects: ProjectRepository, project_ref: str) -> Project:
    project = projects.find(project_ref)
    if project is None:
        raise ProjectNotFoundError
    return project


def assert_connection_exists(connections: ConnectionRepository, connection_id: str | None) -> None:
    if connection_id is None:
        return
    if connections.find_by_id(connection_id) is None:
        raise ConnectionNotFoundError(f"No connection with id {connection_id!r}")


def save_project(
    projects: ProjectRepository,
    resolver: DroneConnectionResolver,
    uow: UnitOfWork,
    project: Project,
) -> Project:
    """先算衝突再寫入。

    v1 是先 flush 再檢查、發現衝突才 rollback。改成先檢查之後，被拒絕的請求
    根本不會碰到資料庫——對呼叫端來說結果一樣，少了一次沒有必要的寫入。
    """
    project.assert_no_slot_conflict(resolver.drone_connection_ids(project))
    projects.save(project)
    uow.commit()
    return load_project(projects, project.id)
