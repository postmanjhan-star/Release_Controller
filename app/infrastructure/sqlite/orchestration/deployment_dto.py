"""Deployment entity 與 deployments 資料列之間的轉換。"""

from app.db.models.orchestration import Deployment as DeploymentRow
from app.domain.orchestration.entities import Deployment
from app.domain.orchestration.value_objects import DeploymentStatus, PublishStatus

# 部署生命週期會改到的欄位。
#
# 發布相關的欄位（publish_status、version、gitea_release_*、publish_*）**刻意
# 不在這裡**：publish_service 與 publish_orchestrator 還是直接寫那些欄位。
# 兩條寫入路徑碰的是不相交的欄位集合，所以不會互相蓋掉；等它們也搬過來時，
# 這份清單再擴充。
LIFECYCLE_FIELDS = (
    "status",
    "promotion_build_number",
    "current_stage",
    "failed_stage",
    "error_code",
    "error_message",
    "cancel_reason",
    "poll_error_code",
    "poll_error_message",
    "poll_failure_count",
    "started_at",
    "failed_at",
    "cancelled_at",
    "finished_at",
    "updated_at",
)


def to_entity(row: DeploymentRow) -> Deployment:
    return Deployment(
        id=row.id,
        project_id=row.project_id,
        component_id=row.component_id,
        component=row.component,
        drone_connection_id=row.drone_connection_id,
        drone_owner=row.drone_owner,
        drone_repository=row.drone_repository,
        source_build_number=row.source_build_number,
        commit_sha=row.commit_sha,
        branch=row.branch,
        target=row.target,
        status=DeploymentStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
        release_bundle_id=row.release_bundle_id,
        promotion_build_number=row.promotion_build_number,
        publish_status=PublishStatus(row.publish_status),
        version=row.version,
        gitea_release_id=row.gitea_release_id,
        gitea_release_tag=row.gitea_release_tag,
        gitea_release_url=row.gitea_release_url,
        current_stage=row.current_stage,
        failed_stage=row.failed_stage,
        error_code=row.error_code,
        error_message=row.error_message,
        cancel_reason=row.cancel_reason,
        poll_error_code=row.poll_error_code,
        poll_error_message=row.poll_error_message,
        poll_failure_count=row.poll_failure_count,
        requested_by=row.requested_by,
        started_at=row.started_at,
        failed_at=row.failed_at,
        cancelled_at=row.cancelled_at,
        finished_at=row.finished_at,
        publish_started_at=row.publish_started_at,
        published_at=row.published_at,
        publish_failed_at=row.publish_failed_at,
        publish_error_code=row.publish_error_code,
        publish_error_message=row.publish_error_message,
    )


def new_row(deployment: Deployment) -> DeploymentRow:
    row = DeploymentRow(
        id=deployment.id,
        release_bundle_id=deployment.release_bundle_id,
        project_id=deployment.project_id,
        component_id=deployment.component_id,
        component=deployment.component,
        drone_connection_id=deployment.drone_connection_id,
        drone_owner=deployment.drone_owner,
        drone_repository=deployment.drone_repository,
        source_build_number=deployment.source_build_number,
        commit_sha=deployment.commit_sha,
        branch=deployment.branch,
        target=deployment.target,
        publish_status=deployment.publish_status.value,
        requested_by=deployment.requested_by,
        created_at=deployment.created_at,
    )
    apply_lifecycle(row, deployment)
    return row


def apply_lifecycle(row: DeploymentRow, deployment: Deployment) -> None:
    for field in LIFECYCLE_FIELDS:
        value = getattr(deployment, field)
        setattr(row, field, value.value if field == "status" else value)
