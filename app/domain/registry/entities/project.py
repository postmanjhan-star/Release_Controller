"""專案與它的元件。

Project 是 aggregate root，Component 只透過它存取——元件的位置、鍵值唯一性與
促銷位置衝突都是「整個專案」層級的性質，不是單一元件自己能判斷的事。

這個 aggregate 最重要的規則是 slot conflict：兩個元件可以共用同一個 Drone
repository（一個 repo 同時放前後端就是這樣表達的），但它們必須促銷到不同的
target，因為一個促銷位置是由 (連線, owner, repo, build, target) 決定的。兩個元件
落在同一個位置的話，衝突會在有人要發版的那一刻，以一個沒頭沒尾的 409 從
uq_deployments_active_promotion 冒出來。在這裡先擋掉，並且說清楚要改什麼。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

import uuid
from collections import defaultdict
from datetime import datetime

from app.domain.registry.exceptions import (
    ComponentKeyTakenError,
    ComponentNotFoundError,
    ComponentSlotConflictError,
)

# 「還沒有預設連線」的替身。兩個都靠繼承取得連線的元件仍然要比對成相等，
# 這正是位置檢查需要的行為。
UNRESOLVED = "<unresolved>"

SlotKey = tuple[str, str, str, str]


class Component:
    """專案裡的一個可部署元件。"""

    def __init__(
        self,
        *,
        id: str,
        project_id: str,
        key: str,
        display_name: str,
        position: int,
        drone_owner: str,
        drone_repo: str,
        project_default_target: str,
        created_at: datetime,
        updated_at: datetime,
        project_drone_connection_id: str | None = None,
        project_gitea_connection_id: str | None = None,
        promote_target_override: str | None = None,
        drone_connection_id: str | None = None,
        publish_enabled: bool = True,
        gitea_owner: str | None = None,
        gitea_repo: str | None = None,
        gitea_connection_id: str | None = None,
        tag_prefix: str = "",
        is_active: bool = True,
    ) -> None:
        self._id = id
        self._project_id = project_id
        self._key = key
        self._display_name = display_name
        self._position = position
        self._drone_owner = drone_owner
        self._drone_repo = drone_repo
        self._project_default_target = project_default_target
        # 元件實際會用到的連線是 component -> project -> 預設 這條鏈解出來的，
        # 所以中間那一段要跟著元件走，就像 effective_target 一樣。
        self._project_drone_connection_id = project_drone_connection_id
        self._project_gitea_connection_id = project_gitea_connection_id
        self._created_at = created_at
        self._updated_at = updated_at
        self._promote_target_override = promote_target_override
        self._drone_connection_id = drone_connection_id
        self._publish_enabled = publish_enabled
        self._gitea_owner = gitea_owner
        self._gitea_repo = gitea_repo
        self._gitea_connection_id = gitea_connection_id
        self._tag_prefix = tag_prefix
        self._is_active = is_active

    # 唯讀屬性。名稱與 ComponentResponse 的欄位一致。
    @property
    def id(self) -> str:
        return self._id

    @property
    def project_id(self) -> str:
        return self._project_id

    @property
    def key(self) -> str:
        return self._key

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def position(self) -> int:
        return self._position

    @property
    def drone_owner(self) -> str:
        return self._drone_owner

    @property
    def drone_repo(self) -> str:
        return self._drone_repo

    @property
    def drone_slug(self) -> str:
        return f"{self._drone_owner}/{self._drone_repo}"

    @property
    def promote_target_override(self) -> str | None:
        return self._promote_target_override

    @property
    def effective_target(self) -> str:
        return self._promote_target_override or self._project_default_target

    @property
    def drone_connection_id(self) -> str | None:
        return self._drone_connection_id

    @property
    def project_default_target(self) -> str:
        return self._project_default_target

    @property
    def project_drone_connection_id(self) -> str | None:
        return self._project_drone_connection_id

    @property
    def project_gitea_connection_id(self) -> str | None:
        return self._project_gitea_connection_id

    @property
    def publish_enabled(self) -> bool:
        return self._publish_enabled

    @property
    def gitea_owner(self) -> str | None:
        return self._gitea_owner

    @property
    def gitea_repo(self) -> str | None:
        return self._gitea_repo

    @property
    def gitea_slug(self) -> str | None:
        if self._gitea_owner and self._gitea_repo:
            return f"{self._gitea_owner}/{self._gitea_repo}"
        return None

    @property
    def gitea_connection_id(self) -> str | None:
        return self._gitea_connection_id

    @property
    def tag_prefix(self) -> str:
        return self._tag_prefix

    @property
    def is_active(self) -> bool:
        return self._is_active

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Component):
            return self._id == other._id
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._id)

    def __repr__(self) -> str:
        return f"Component(id={self._id!r}, key={self._key!r}, position={self._position})"

    @staticmethod
    def create(
        *,
        project_id: str,
        key: str,
        drone_owner: str,
        drone_repo: str,
        position: int,
        project_default_target: str,
        now: datetime,
        project_drone_connection_id: str | None = None,
        project_gitea_connection_id: str | None = None,
        display_name: str | None = None,
        promote_target_override: str | None = None,
        drone_connection_id: str | None = None,
        publish_enabled: bool = True,
        gitea_owner: str | None = None,
        gitea_repo: str | None = None,
        gitea_connection_id: str | None = None,
        tag_prefix: str = "",
        is_active: bool = True,
    ) -> "Component":
        component = Component(
            id=str(uuid.uuid4()),
            project_id=project_id,
            key=key,
            display_name=display_name or default_display_name(key),
            position=position,
            drone_owner=drone_owner,
            drone_repo=drone_repo,
            project_default_target=project_default_target,
            created_at=now,
            updated_at=now,
            project_drone_connection_id=project_drone_connection_id,
            project_gitea_connection_id=project_gitea_connection_id,
            promote_target_override=promote_target_override,
            drone_connection_id=drone_connection_id,
            publish_enabled=publish_enabled,
            gitea_owner=gitea_owner,
            gitea_repo=gitea_repo,
            gitea_connection_id=gitea_connection_id,
            tag_prefix=tag_prefix,
            is_active=is_active,
        )
        component.assert_publishable()
        return component

    def assert_publishable(self) -> None:
        """開了發布就一定要有 Gitea repository，否則發版當下才會爆。"""
        if self._publish_enabled and not (self._gitea_owner and self._gitea_repo):
            raise ComponentSlotConflictError(
                f"Component {self._key!r} has publishing enabled but no Gitea "
                f"repository. Set gitea_owner and gitea_repo, or turn publish_enabled off."
            )

    def apply_update(
        self,
        *,
        at: datetime,
        display_name: str | None = None,
        drone_owner: str | None = None,
        drone_repo: str | None = None,
        promote_target_override: str | None = None,
        clear_promote_target_override: bool = False,
        drone_connection_id: str | None = None,
        publish_enabled: bool | None = None,
        gitea_owner: str | None = None,
        gitea_repo: str | None = None,
        gitea_connection_id: str | None = None,
        tag_prefix: str | None = None,
        is_active: bool | None = None,
    ) -> None:
        """None 代表「不要動這個欄位」，跟 v1 的 PATCH 語意一樣。"""
        if drone_connection_id is not None:
            self._drone_connection_id = drone_connection_id
        if gitea_connection_id is not None:
            self._gitea_connection_id = gitea_connection_id
        if display_name is not None:
            self._display_name = display_name
        if drone_owner is not None:
            self._drone_owner = drone_owner
        if drone_repo is not None:
            self._drone_repo = drone_repo
        if clear_promote_target_override:
            self._promote_target_override = None
        elif promote_target_override is not None:
            self._promote_target_override = promote_target_override
        if publish_enabled is not None:
            self._publish_enabled = publish_enabled
        if gitea_owner is not None:
            self._gitea_owner = gitea_owner
        if gitea_repo is not None:
            self._gitea_repo = gitea_repo
        if tag_prefix is not None:
            self._tag_prefix = tag_prefix
        if is_active is not None:
            self._is_active = is_active
        self.assert_publishable()
        self._updated_at = at

    def slot_key(self, drone_connection_id: str) -> SlotKey:
        """這個元件會佔用的促銷位置。

        連線 id 由外面解析後傳進來——那條 component -> project -> 預設的繼承鏈
        需要讀資料庫，不屬於 entity。
        """
        return (
            drone_connection_id,
            self._drone_owner.lower(),
            self._drone_repo.lower(),
            self.effective_target,
        )

    def _set_position(self, position: int) -> None:
        self._position = position

    def _inherit_from_project(
        self, default_target: str, drone_connection_id: str | None, gitea_connection_id: str | None
    ) -> None:
        self._project_default_target = default_target
        self._project_drone_connection_id = drone_connection_id
        self._project_gitea_connection_id = gitea_connection_id


class Project:
    """一組一起發布的元件，加上它們共用的預設值。"""

    def __init__(
        self,
        *,
        id: str,
        key: str,
        name: str,
        default_target: str,
        is_archived: bool,
        created_at: datetime,
        updated_at: datetime,
        description: str | None = None,
        drone_connection_id: str | None = None,
        gitea_connection_id: str | None = None,
        components: list[Component] | None = None,
    ) -> None:
        self._id = id
        self._key = key
        self._name = name
        self._default_target = default_target
        self._is_archived = is_archived
        self._created_at = created_at
        self._updated_at = updated_at
        self._description = description
        self._drone_connection_id = drone_connection_id
        self._gitea_connection_id = gitea_connection_id
        self._components = sorted(components or [], key=lambda c: c.position)
        for component in self._components:
            component._inherit_from_project(
                default_target, drone_connection_id, gitea_connection_id
            )

    @property
    def id(self) -> str:
        return self._id

    @property
    def key(self) -> str:
        return self._key

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str | None:
        return self._description

    @property
    def default_target(self) -> str:
        return self._default_target

    @property
    def drone_connection_id(self) -> str | None:
        return self._drone_connection_id

    @property
    def gitea_connection_id(self) -> str | None:
        return self._gitea_connection_id

    @property
    def is_archived(self) -> bool:
        return self._is_archived

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    @property
    def components(self) -> list[Component]:
        return list(self._components)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Project):
            return self._id == other._id
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._id)

    def __repr__(self) -> str:
        return f"Project(id={self._id!r}, key={self._key!r}, components={len(self._components)})"

    @staticmethod
    def create(
        *,
        key: str,
        name: str,
        default_target: str,
        now: datetime,
        description: str | None = None,
        drone_connection_id: str | None = None,
        gitea_connection_id: str | None = None,
    ) -> "Project":
        return Project(
            id=str(uuid.uuid4()),
            key=key,
            name=name,
            default_target=default_target,
            is_archived=False,
            created_at=now,
            updated_at=now,
            description=description,
            drone_connection_id=drone_connection_id,
            gitea_connection_id=gitea_connection_id,
        )

    # ------------------------------------------------------------------ 查詢

    def component(self, component_ref: str) -> Component:
        """依 id 或 key 取得元件——key 是人眼前看到的東西。"""
        for component in self._components:
            if component.id == component_ref or component.key == component_ref:
                return component
        raise ComponentNotFoundError

    # ------------------------------------------------------------------ 修改

    def apply_update(
        self,
        *,
        at: datetime,
        name: str | None = None,
        description: str | None = None,
        default_target: str | None = None,
        drone_connection_id: str | None = None,
        gitea_connection_id: str | None = None,
        is_archived: bool | None = None,
    ) -> None:
        if drone_connection_id is not None:
            self._drone_connection_id = drone_connection_id
        if gitea_connection_id is not None:
            self._gitea_connection_id = gitea_connection_id
        if name is not None:
            self._name = name
        if description is not None:
            self._description = description
        if default_target is not None:
            # 改預設 target 會連帶移動每一個沒有覆寫的元件，所以位置衝突要重算。
            self._default_target = default_target
        for component in self._components:
            component._inherit_from_project(
                self._default_target, self._drone_connection_id, self._gitea_connection_id
            )
        if is_archived is not None:
            self._is_archived = is_archived
        self._updated_at = at

    def set_archived(self, archived: bool, *, at: datetime) -> None:
        self._is_archived = archived
        self._updated_at = at

    def add_component(self, component: Component, *, requested_position: int | None = None) -> None:
        if any(existing.key == component.key for existing in self._components):
            raise ComponentKeyTakenError(
                f"This project already has a component named {component.key!r}"
            )
        size = len(self._components)
        index = clamp_position(requested_position, size) - 1
        # 新元件也要接上這個專案的預設值，否則它的 effective_target 與繼承來的
        # 連線會是建立當下傳進來的那一份，而不是專案現在的樣子。
        component._inherit_from_project(
            self._default_target, self._drone_connection_id, self._gitea_connection_id
        )
        self._components.insert(index, component)
        self._renumber()

    def remove_component(self, component: Component) -> None:
        self._components = [c for c in self._components if c.id != component.id]
        self._renumber()

    def reorder(self, component_ids: list[str]) -> None:
        by_id = {component.id: component for component in self._components}
        if set(component_ids) != set(by_id) or len(component_ids) != len(by_id):
            raise ComponentNotFoundError(
                "Reordering must list every component of this project exactly once"
            )
        self._components = [by_id[component_id] for component_id in component_ids]
        self._renumber()

    def _renumber(self) -> None:
        for index, component in enumerate(self._components, start=1):
            component._set_position(index)

    # ------------------------------------------------------- 促銷位置衝突

    def slot_conflicts(self, drone_connection_ids: dict[str, str]) -> list[str]:
        """哪些啟用中的元件會撞在同一個促銷位置。

        drone_connection_ids 是 component.id -> 已解析的連線 id；解析不出來的
        用 UNRESOLVED，這樣兩個都在等預設連線的元件仍然會被視為相撞。
        """
        grouped: dict[SlotKey, list[str]] = defaultdict(list)
        for component in self._components:
            if component.is_active:
                connection_id = drone_connection_ids.get(component.id, UNRESOLVED)
                grouped[component.slot_key(connection_id)].append(component.key)
        return [
            f"{', '.join(sorted(keys))} all promote {slot[1]}/{slot[2]} into {slot[3]!r}"
            for slot, keys in grouped.items()
            if len(keys) > 1
        ]

    def assert_no_slot_conflict(self, drone_connection_ids: dict[str, str]) -> None:
        conflicts = self.slot_conflicts(drone_connection_ids)
        if conflicts:
            raise ComponentSlotConflictError(
                conflicts[0] + ". Two components may share a repository, but not a promotion "
                "target: give one of them a promote_target_override."
            )


def default_display_name(key: str) -> str:
    return key.replace("-", " ").title()


def clamp_position(requested: int | None, size: int) -> int:
    """把要求的插入位置夾在 1..size+1 之間；沒指定就接在最後。"""
    return max(1, min(requested or size + 1, size + 1))
