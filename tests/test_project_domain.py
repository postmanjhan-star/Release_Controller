"""Project aggregate 的規則測試——不建資料庫、不打上游。

這裡最重要的是位置衝突（slot conflict）。它原本是 `ProjectService._slot_key` +
`_slot_conflicts` + `_assert_no_slot_conflict` 三個私有方法，要測它得起一個
SQLite、建連線、建專案、發 HTTP。現在是 `Project.slot_conflicts()` 的純函式行為。

（結構不變式在 test_release_domain.py，掃的是整個 app/domain。）
"""

from datetime import datetime, timezone

import pytest

from app.domain.registry.entities import (
    UNRESOLVED,
    Component,
    Project,
    clamp_position,
    default_display_name,
)
from app.domain.registry.exceptions import (
    ComponentKeyTakenError,
    ComponentNotFoundError,
    ComponentSlotConflictError,
)

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 9, 5, 13, 0, tzinfo=timezone.utc)


def make_project(default_target: str = "production", **kwargs) -> Project:
    return Project.create(
        key="default", name="Default project", default_target=default_target, now=NOW, **kwargs
    )


def make_component(project: Project, key: str, *, position: int = 1, **kwargs) -> Component:
    kwargs.setdefault("drone_owner", "102573")
    kwargs.setdefault("drone_repo", "soda")
    kwargs.setdefault("publish_enabled", False)
    return Component.create(
        project_id=project.id,
        key=key,
        position=position,
        project_default_target=project.default_target,
        now=NOW,
        **kwargs,
    )


def with_components(project: Project, *components: Component) -> Project:
    for index, component in enumerate(components, start=1):
        project.add_component(component, requested_position=index)
    return project


# --- 位置衝突 -----------------------------------------------------------


def test_two_components_on_one_repo_and_one_target_collide() -> None:
    project = make_project()
    with_components(
        project, make_component(project, "backend"), make_component(project, "frontend")
    )

    conflicts = project.slot_conflicts({})

    assert conflicts == ["backend, frontend all promote 102573/soda into 'production'"]


def test_sharing_a_repository_is_fine_with_different_targets() -> None:
    project = make_project()
    with_components(
        project,
        make_component(project, "backend"),
        make_component(project, "frontend", promote_target_override="staging"),
    )

    assert project.slot_conflicts({}) == []


def test_inactive_components_cannot_collide() -> None:
    project = make_project()
    with_components(
        project,
        make_component(project, "backend"),
        make_component(project, "frontend", is_active=False),
    )

    assert project.slot_conflicts({}) == []


def test_components_waiting_on_the_same_missing_default_still_collide() -> None:
    """兩個都解析不到連線的元件必須被視為相撞，不能各自算成不同的位置。"""
    project = make_project()
    with_components(
        project, make_component(project, "backend"), make_component(project, "frontend")
    )

    # 空的對照表 = 兩個都是 UNRESOLVED。
    assert len(project.slot_conflicts({})) == 1
    assert UNRESOLVED == "<unresolved>"


def test_different_connections_are_different_slots() -> None:
    project = make_project()
    backend = make_component(project, "backend")
    frontend = make_component(project, "frontend")
    with_components(project, backend, frontend)

    assert project.slot_conflicts({backend.id: "conn-a", frontend.id: "conn-b"}) == []


def test_repository_names_compare_case_insensitively() -> None:
    project = make_project()
    with_components(
        project,
        make_component(project, "backend", drone_owner="102573", drone_repo="soda"),
        make_component(project, "frontend", drone_owner="102573", drone_repo="SODA"),
    )

    assert len(project.slot_conflicts({})) == 1


def test_changing_the_default_target_can_create_a_collision() -> None:
    """改專案的 default_target 會連帶移動每個沒有覆寫的元件。"""
    project = make_project()
    with_components(
        project,
        make_component(project, "backend", promote_target_override="staging"),
        make_component(project, "frontend"),
    )
    assert project.slot_conflicts({}) == []

    project.apply_update(at=LATER, default_target="staging")

    assert len(project.slot_conflicts({})) == 1


def test_asserting_says_what_to_change() -> None:
    project = make_project()
    with_components(
        project, make_component(project, "backend"), make_component(project, "frontend")
    )

    with pytest.raises(ComponentSlotConflictError) as raised:
        project.assert_no_slot_conflict({})

    assert "give one of them a promote_target_override" in str(raised.value)


# --- 位置與順序 ---------------------------------------------------------


def test_components_are_numbered_from_one_in_order() -> None:
    project = make_project()
    with_components(
        project,
        make_component(project, "a"),
        make_component(project, "b"),
        make_component(project, "c"),
    )

    assert [c.key for c in project.components] == ["a", "b", "c"]
    assert [c.position for c in project.components] == [1, 2, 3]


def test_an_out_of_range_position_is_clamped_not_refused() -> None:
    assert clamp_position(None, 3) == 4  # 沒指定就接在最後
    assert clamp_position(1, 3) == 1
    assert clamp_position(2, 3) == 2
    assert clamp_position(99, 3) == 4  # 太大就夾到最後，不是拒絕


def test_inserting_in_the_middle_pushes_the_rest_down() -> None:
    project = make_project()
    with_components(project, make_component(project, "a"), make_component(project, "c"))

    project.add_component(make_component(project, "b"), requested_position=2)

    assert [c.key for c in project.components] == ["a", "b", "c"]
    assert [c.position for c in project.components] == [1, 2, 3]


def test_removing_a_component_closes_the_gap() -> None:
    project = make_project()
    a, b, c = (make_component(project, k) for k in ("a", "b", "c"))
    with_components(project, a, b, c)

    project.remove_component(b)

    assert [x.key for x in project.components] == ["a", "c"]
    assert [x.position for x in project.components] == [1, 2]


def test_reordering_must_list_every_component_exactly_once() -> None:
    project = make_project()
    a, b = make_component(project, "a"), make_component(project, "b")
    with_components(project, a, b)

    with pytest.raises(ComponentNotFoundError):
        project.reorder([a.id])
    with pytest.raises(ComponentNotFoundError):
        project.reorder([a.id, a.id])

    project.reorder([b.id, a.id])
    assert [c.key for c in project.components] == ["b", "a"]
    assert [c.position for c in project.components] == [1, 2]


def test_a_duplicate_component_key_is_refused() -> None:
    project = make_project()
    with_components(project, make_component(project, "backend"))

    with pytest.raises(ComponentKeyTakenError):
        project.add_component(make_component(project, "backend", drone_repo="other"))


# --- 元件本身 -----------------------------------------------------------


def test_publishing_without_a_gitea_repository_is_refused() -> None:
    project = make_project()

    with pytest.raises(ComponentSlotConflictError) as raised:
        make_component(project, "backend", publish_enabled=True)

    assert "publishing enabled but no Gitea repository" in str(raised.value)


def test_turning_publishing_on_later_is_refused_the_same_way() -> None:
    project = make_project()
    component = make_component(project, "backend")

    with pytest.raises(ComponentSlotConflictError):
        component.apply_update(at=LATER, publish_enabled=True)


def test_a_display_name_is_derived_from_the_key() -> None:
    assert default_display_name("release-controller") == "Release Controller"
    project = make_project()
    assert make_component(project, "my-service").display_name == "My Service"


def test_the_effective_target_falls_back_to_the_project() -> None:
    project = make_project("production")
    plain = make_component(project, "a")
    overridden = make_component(project, "b", promote_target_override="staging")
    with_components(project, plain, overridden)

    assert plain.effective_target == "production"
    assert overridden.effective_target == "staging"


def test_a_component_is_reachable_by_id_or_by_key() -> None:
    project = make_project()
    component = make_component(project, "backend")
    with_components(project, component)

    assert project.component(component.id) is component
    assert project.component("backend") is component
    with pytest.raises(ComponentNotFoundError):
        project.component("nope")


def test_components_inherit_the_projects_connections() -> None:
    project = make_project(drone_connection_id="conn-drone", gitea_connection_id="conn-gitea")
    component = make_component(project, "backend")
    with_components(project, component)

    assert component.project_drone_connection_id == "conn-drone"
    assert component.project_gitea_connection_id == "conn-gitea"
