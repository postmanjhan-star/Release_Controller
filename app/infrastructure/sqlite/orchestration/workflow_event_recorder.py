"""WorkflowEventRecorder 的實作：把事件寫進 append-only 的 workflow_events。

包在既有的 OrchestrationWorkflowService 外面，那裡已經處理了 actor 與欄位快照；
這一層只負責把 domain entity 翻成它要的形狀。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.orchestration import Deployment as DeploymentRow
from app.db.models.orchestration import ReleaseBundle as BundleRow
from app.domain.orchestration.entities import Deployment, ReleaseBundle
from app.domain.orchestration.repositories import WorkflowEventRecorder
from app.services.workflow_service import OrchestrationWorkflowService


class SqlAlchemyWorkflowEventRecorder(WorkflowEventRecorder):
    def __init__(self, session: Session, events: OrchestrationWorkflowService) -> None:
        self.session = session
        self.events = events

    def append(
        self,
        *,
        stage: str,
        event_type: str,
        release: ReleaseBundle | None = None,
        deployment: Deployment | None = None,
        status: str | None = None,
        error_code: str | None = None,
        message: str | None = None,
    ) -> None:
        release_row = self.session.get(BundleRow, release.id) if release else None
        deployment_row = self.session.get(DeploymentRow, deployment.id) if deployment else None
        if release_row is None and deployment_row is not None and deployment.release_bundle_id:
            # 只給了部署、但它屬於某個 bundle：事件仍要帶上那個 workflow instance。
            release_row = self.session.scalar(
                select(BundleRow).where(BundleRow.id == deployment.release_bundle_id)
            )
        self.events.append(
            stage=stage,
            event_type=event_type,
            release=release_row,
            deployment=deployment_row,
            status=status,
            error_code=error_code,
            message=message,
        )
