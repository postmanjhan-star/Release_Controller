"""Project / Component entity 與 registry 資料列之間的轉換。"""

from app.db.models.registry import Project as ProjectRow
from app.db.models.registry import ProjectComponent as ComponentRow
from app.domain.registry.entities import Component, Project


def to_entity(row: ProjectRow) -> Project:
    return Project(
        id=row.id,
        key=row.key,
        name=row.name,
        default_target=row.default_target,
        is_archived=row.is_archived,
        created_at=row.created_at,
        updated_at=row.updated_at,
        description=row.description,
        drone_connection_id=row.drone_connection_id,
        gitea_connection_id=row.gitea_connection_id,
        components=[component_to_entity(c, row) for c in row.components],
    )


def component_to_entity(row: ComponentRow, project: ProjectRow) -> Component:
    return Component(
        id=row.id,
        project_id=row.project_id,
        key=row.key,
        display_name=row.display_name,
        position=row.position,
        drone_owner=row.drone_owner,
        drone_repo=row.drone_repo,
        project_default_target=project.default_target,
        project_drone_connection_id=project.drone_connection_id,
        project_gitea_connection_id=project.gitea_connection_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        promote_target_override=row.promote_target_override,
        drone_connection_id=row.drone_connection_id,
        publish_enabled=row.publish_enabled,
        gitea_owner=row.gitea_owner,
        gitea_repo=row.gitea_repo,
        gitea_connection_id=row.gitea_connection_id,
        tag_prefix=row.tag_prefix,
        is_active=row.is_active,
    )


def project_row(project: Project) -> ProjectRow:
    row = ProjectRow(id=project.id, key=project.key, created_at=project.created_at)
    apply_to_project_row(row, project)
    return row


def apply_to_project_row(row: ProjectRow, project: Project) -> None:
    row.name = project.name
    row.description = project.description
    row.default_target = project.default_target
    row.drone_connection_id = project.drone_connection_id
    row.gitea_connection_id = project.gitea_connection_id
    row.is_archived = project.is_archived
    row.updated_at = project.updated_at


def component_row(component: Component) -> ComponentRow:
    row = ComponentRow(
        id=component.id,
        project_id=component.project_id,
        key=component.key,
        created_at=component.created_at,
    )
    apply_to_component_row(row, component)
    return row


def apply_to_component_row(row: ComponentRow, component: Component) -> None:
    """位置刻意不在這裡寫——它要分兩趟走負數，見 repository 的 _sync_components。"""
    row.display_name = component.display_name
    row.drone_owner = component.drone_owner
    row.drone_repo = component.drone_repo
    row.promote_target_override = component.promote_target_override
    row.drone_connection_id = component.drone_connection_id
    row.publish_enabled = component.publish_enabled
    row.gitea_owner = component.gitea_owner
    row.gitea_repo = component.gitea_repo
    row.gitea_connection_id = component.gitea_connection_id
    row.tag_prefix = component.tag_prefix
    row.is_active = component.is_active
    row.updated_at = component.updated_at
