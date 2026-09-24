"""ReleaseBundle entity 與 release_bundles 資料列之間的轉換。"""

from app.db.models.orchestration import ReleaseBundle as BundleRow
from app.domain.orchestration.entities import ReleaseBundle
from app.domain.orchestration.value_objects import BundleStatus, PublishStatus, ReleaseMode
from app.infrastructure.sqlite.orchestration.deployment_dto import to_entity as deployment_to_entity

# 發布相關欄位刻意不在這裡，理由同 deployment_dto。
LIFECYCLE_FIELDS = (
    "status",
    "deployment_status",
    "current_stage",
    "failed_stage",
    "error_code",
    "error_message",
    "started_at",
    "failed_at",
    "finished_at",
    "updated_at",
)
_ENUM_FIELDS = frozenset({"status", "deployment_status"})


def to_entity(row: BundleRow, *, with_deployments: bool = True) -> ReleaseBundle:
    return ReleaseBundle(
        id=row.id,
        mode=ReleaseMode(row.mode),
        target=row.target,
        status=BundleStatus(row.status),
        deployment_status=BundleStatus(row.deployment_status),
        created_at=row.created_at,
        updated_at=row.updated_at,
        project_id=row.project_id,
        selected_component_keys=row.selected_component_keys,
        publish_status=PublishStatus(row.publish_status),
        version=row.version,
        release_name=row.release_name,
        release_notes=row.release_notes,
        workflow_instance_id=row.workflow_instance_id,
        current_stage=row.current_stage,
        failed_stage=row.failed_stage,
        error_code=row.error_code,
        error_message=row.error_message,
        requested_by=row.requested_by,
        started_at=row.started_at,
        failed_at=row.failed_at,
        finished_at=row.finished_at,
        publish_started_at=row.publish_started_at,
        published_at=row.published_at,
        publish_failed_at=row.publish_failed_at,
        publish_error_code=row.publish_error_code,
        publish_error_message=row.publish_error_message,
        deployments=(
            [deployment_to_entity(item) for item in row.deployments] if with_deployments else []
        ),
    )


def new_row(bundle: ReleaseBundle) -> BundleRow:
    row = BundleRow(
        id=bundle.id,
        project_id=bundle.project_id,
        mode=bundle.mode.value,
        selected_component_keys=bundle.selected_component_keys,
        target=bundle.target,
        publish_status=bundle.publish_status.value,
        workflow_instance_id=bundle.workflow_instance_id,
        requested_by=bundle.requested_by,
        created_at=bundle.created_at,
    )
    apply_lifecycle(row, bundle)
    return row


def apply_lifecycle(row: BundleRow, bundle: ReleaseBundle) -> None:
    for field in LIFECYCLE_FIELDS:
        value = getattr(bundle, field)
        setattr(row, field, value.value if field in _ENUM_FIELDS else value)
