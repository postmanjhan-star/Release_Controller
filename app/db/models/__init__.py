"""SQLAlchemy ORM models.

Alembic 透過 `from app.db import models` 匯入這裡註冊 Base.metadata，
所以每個 model 類別都必須在這份清單裡。
領域 enum 已搬到 app/domain/*/value_objects/，不再由這裡轉出。
"""

from app.db.models.auth import AuthSession, OAuthLoginAttempt
from app.db.models.orchestration import (
    Deployment,
    PublishRecord,
    ReleaseBundle,
    WorkflowEvent,
)
from app.db.models.registry import Project, ProjectComponent, UpstreamConnection
from app.db.models.release import Release
from app.db.models.scheduling import (
    DeploymentSchedule,
    EmailNotificationCursor,
    EmailOutbox,
    NotificationRecipient,
    ScheduleComponentBuild,
)
from app.db.models.workflow import ReleaseWorkflow

__all__ = [
    "AuthSession",
    "Deployment",
    "DeploymentSchedule",
    "EmailNotificationCursor",
    "EmailOutbox",
    "NotificationRecipient",
    "OAuthLoginAttempt",
    "Project",
    "ProjectComponent",
    "PublishRecord",
    "Release",
    "ReleaseBundle",
    "ReleaseWorkflow",
    "ScheduleComponentBuild",
    "UpstreamConnection",
    "WorkflowEvent",
]
