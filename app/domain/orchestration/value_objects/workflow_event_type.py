"""Append-only 稽核事件的種類。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

import enum


class WorkflowEventType(str, enum.Enum):
    STAGE_STARTED = "STAGE_STARTED"
    STAGE_SUCCEEDED = "STAGE_SUCCEEDED"
    STAGE_FAILED = "STAGE_FAILED"
    DEPLOYMENT_CANCELLED = "DEPLOYMENT_CANCELLED"
    PROMOTION_CREATED = "PROMOTION_CREATED"
    DRONE_STATUS_CHANGED = "DRONE_STATUS_CHANGED"
    RELEASE_COMPLETED = "RELEASE_COMPLETED"
    PUBLISH_REQUESTED = "PUBLISH_REQUESTED"
    PUBLISH_STAGE_STARTED = "PUBLISH_STAGE_STARTED"
    PUBLISH_STAGE_SUCCEEDED = "PUBLISH_STAGE_SUCCEEDED"
    PUBLISH_STAGE_FAILED = "PUBLISH_STAGE_FAILED"
    GITEA_RELEASE_CREATED = "GITEA_RELEASE_CREATED"
    GITEA_RELEASE_ALREADY_EXISTS = "GITEA_RELEASE_ALREADY_EXISTS"
    PUBLISH_COMPLETED = "PUBLISH_COMPLETED"
    PUBLISH_PARTIAL_FAILURE = "PUBLISH_PARTIAL_FAILURE"
    # An upstream call failed in a way that says nothing about the deployment.
    # Recorded once per new condition so the timeline shows why a deployment is
    # sitting still, without one line per poll.
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    UPSTREAM_RECOVERED = "UPSTREAM_RECOVERED"
    # A promote request whose outcome Drone never confirmed, and its resolution.
    PROMOTION_UNCONFIRMED = "PROMOTION_UNCONFIRMED"
    PROMOTION_RECONCILED = "PROMOTION_RECONCILED"
    # A partly-failed bundle being driven forward again by an operator.
    DEPLOYMENT_RETRIED = "DEPLOYMENT_RETRIED"
    # Interrupted work picked back up: a publish whose real outcome was recovered
    # from its records, or a deployment the worker resumed after a restart.
    PUBLISH_RECONCILED = "PUBLISH_RECONCILED"
    DEPLOYMENT_RESUMED = "DEPLOYMENT_RESUMED"
