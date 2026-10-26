"""CI credentials, per-component policy and durable deployment requests."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.shared.time import utc_now


def new_id() -> str:
    return str(uuid.uuid4())


class CIServiceToken(Base):
    __tablename__ = "ci_service_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"))
    component_id: Mapped[str] = mapped_column(
        ForeignKey("project_components.id", ondelete="RESTRICT")
    )
    target: Mapped[str] = mapped_column(String(100))
    branch: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CIPolicy(Base):
    __tablename__ = "ci_policies"
    __table_args__ = (UniqueConstraint("component_id", "target", name="uq_ci_policy_scope"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    component_id: Mapped[str] = mapped_column(
        ForeignKey("project_components.id", ondelete="RESTRICT")
    )
    target: Mapped[str] = mapped_column(String(100))
    branch: Mapped[str] = mapped_column(String(255))
    source_stages: Mapped[list[str]] = mapped_column(JSON)
    promotion_stages: Mapped[list[str]] = mapped_column(JSON)


class CIDeploymentRequest(Base):
    __tablename__ = "ci_deployment_requests"
    __table_args__ = (
        UniqueConstraint("component_id", "source_build_number", "target", name="uq_ci_intent"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    service_token_id: Mapped[str] = mapped_column(
        ForeignKey("ci_service_tokens.id", ondelete="RESTRICT")
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"))
    component_id: Mapped[str] = mapped_column(
        ForeignKey("project_components.id", ondelete="RESTRICT")
    )
    target: Mapped[str] = mapped_column(String(100))
    branch: Mapped[str] = mapped_column(String(255))
    source_build_number: Mapped[int]
    drone_connection_id: Mapped[str] = mapped_column(
        ForeignKey("upstream_connections.id", ondelete="RESTRICT")
    )
    drone_owner: Mapped[str] = mapped_column(String(255))
    drone_repository: Mapped[str] = mapped_column(String(255))
    source_stages: Mapped[list[str]] = mapped_column(JSON)
    promotion_stages: Mapped[list[str]] = mapped_column(JSON)
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    deployment_id: Mapped[str | None] = mapped_column(
        ForeignKey("deployments.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(30), default="WAITING_SOURCE", index=True)
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_id: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CIDeploymentEvent(Base):
    __tablename__ = "ci_deployment_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    request_id: Mapped[str] = mapped_column(
        ForeignKey("ci_deployment_requests.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(30))
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
