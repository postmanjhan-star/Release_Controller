"""Durable plans and append-only invocation evidence, independent of deployments."""

import uuid
from datetime import datetime

from sqlalchemy import DDL, JSON, DateTime, String, event
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.shared.time import utc_now


class ReleasePlan(Base):
    __tablename__ = "release_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_id: Mapped[str | None] = mapped_column(String(36), unique=True)


class MCPToolInvocation(Base):
    __tablename__ = "mcp_tool_invocations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    invocation_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    arguments: Mapped[dict] = mapped_column(JSON, nullable=False)
    result_status: Mapped[str] = mapped_column(String(50), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    client: Mapped[str] = mapped_column(String(100), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)


AUDIT_TRIGGERS = tuple(
    f"CREATE TRIGGER IF NOT EXISTS mcp_audit_block_{operation.lower()} "
    f"BEFORE {operation} ON mcp_tool_invocations BEGIN "
    "SELECT RAISE(ABORT, 'mcp audit is append-only'); END"
    for operation in ("UPDATE", "DELETE")
)
for statement in AUDIT_TRIGGERS:
    event.listen(
        MCPToolInvocation.__table__, "after_create", DDL(statement).execute_if(dialect="sqlite")
    )
