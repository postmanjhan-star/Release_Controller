import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.scheduling.value_objects import EmailDeliveryStatus, ScheduleStatus
from app.domain.shared.time import utc_now


class DeploymentSchedule(Base):
    __tablename__ = "deployment_schedules"
    __table_args__ = (
        Index("ix_deployment_schedules_due", "status", "scheduled_for_utc"),
        Index("ix_deployment_schedules_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=True
    )
    mode: Mapped[str] = mapped_column(String(30), nullable=False)
    # A snapshot of what was scheduled, so the row still reads correctly after a
    # component is renamed.  The authoritative selection is component_builds.
    selected_component_keys: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The v2.x shape.  Kept until revision 0017 so a schedule created before the
    # upgrade still runs, and read only when component_builds is empty.
    # Read-only history, no longer written.  Revision 0016 copied the selection of
    # every PENDING and RUNNING schedule into schedule_component_builds, but not
    # that of the schedules that had already run -- for those, these two columns
    # are the only surviving record of what was scheduled, so dropping them would
    # erase audit history rather than tidy it.
    frontend_build_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    backend_build_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target: Mapped[str] = mapped_column(String(100), nullable=False)
    scheduled_for_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=ScheduleStatus.PENDING.value
    )
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notification_recipients: Mapped[str | None] = mapped_column(Text, nullable=True)
    release_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    release_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachment_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attachment_content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attachment_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attachment_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    deployment_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("deployments.id", ondelete="SET NULL"), nullable=True
    )
    release_bundle_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("release_bundles.id", ondelete="SET NULL"), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    schedule_notice: Mapped["EmailOutbox | None"] = relationship(
        back_populates="schedule",
        foreign_keys="EmailOutbox.schedule_id",
        uselist=False,
    )
    component_builds: Mapped[list["ScheduleComponentBuild"]] = relationship(
        back_populates="schedule",
        cascade="all, delete-orphan",
        order_by="ScheduleComponentBuild.component_key",
    )

    @property
    def notification_status(self) -> str | None:
        return self.schedule_notice.status if self.schedule_notice else None

    @property
    def notification_sent_at(self) -> datetime | None:
        return self.schedule_notice.sent_at if self.schedule_notice else None


class ScheduleComponentBuild(Base):
    """Which build of which component a schedule will release.

    A child table rather than a widening set of columns: the two build-number
    columns it replaces were the shape of the problem when a project had exactly
    a frontend and a backend.  ondelete=RESTRICT on the component is deliberate --
    a component with a pending schedule pointing at it must not vanish underneath
    it.
    """

    __tablename__ = "schedule_component_builds"

    schedule_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("deployment_schedules.id", ondelete="CASCADE"),
        primary_key=True,
    )
    component_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("project_components.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    component_key: Mapped[str] = mapped_column(String(50), nullable=False)
    build_number: Mapped[int] = mapped_column(Integer, nullable=False)

    schedule: Mapped[DeploymentSchedule] = relationship(back_populates="component_builds")


class EmailOutbox(Base):
    __tablename__ = "email_outbox"
    __table_args__ = (
        UniqueConstraint("workflow_event_id", name="uq_email_outbox_workflow_event_id"),
        Index("ix_email_outbox_delivery", "status", "next_attempt_at"),
        Index("ix_email_outbox_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    workflow_event_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("workflow_events.id", ondelete="SET NULL"), nullable=True
    )
    schedule_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("deployment_schedules.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    recipients: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    attachment_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attachment_content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attachment_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attachment_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=EmailDeliveryStatus.PENDING.value
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    schedule: Mapped[DeploymentSchedule | None] = relationship(
        back_populates="schedule_notice",
        foreign_keys=[schedule_id],
    )


class EmailNotificationCursor(Base):
    __tablename__ = "email_notification_cursors"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    after_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    after_event_id: Mapped[str] = mapped_column(String(36), nullable=False)


class NotificationRecipient(Base):
    __tablename__ = "notification_recipients"
    __table_args__ = (Index("ix_notification_recipients_created_at", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
