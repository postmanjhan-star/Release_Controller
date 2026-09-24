import itertools
import os
import secrets
import time
import uuid
from datetime import datetime

from sqlalchemy import (
    DDL,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.orchestration.value_objects import (
    BundleStatus,
    DeploymentStatus,
    PublishStatus,
    ReleaseMode,
)
from app.domain.shared.time import utc_now

_event_counter = itertools.count()
_process_salt = secrets.token_hex(3)


def event_id() -> str:
    """A workflow event id that sorts chronologically.

    Event queries order by (created_at, id).  With a random uuid4 the tie-break
    was random, so events written in the same instant -- promote() writes three --
    could be displayed out of order.  Milliseconds give ordering between
    instants, a per-process counter gives it within one, and the salt keeps ids
    from colliding across processes.
    """
    millis = int(time.time() * 1000)
    return f"{millis:013d}-{next(_event_counter):06d}-{_process_salt}-{os.getpid():06d}"


# A deployment occupies its promotion slot from the moment it is created until it
# reaches a terminal, non-successful state.  This is the single definition behind
# both the application-level duplicate check and the uq_deployments_active_promotion
# partial unique index, so the two cannot drift.
ACTIVE_PROMOTION_STATUSES: tuple[str, ...] = (
    DeploymentStatus.WAITING.value,
    DeploymentStatus.PROMOTING.value,
    DeploymentStatus.DEPLOYING.value,
    DeploymentStatus.SUCCESS.value,
)

# What identifies "the same promotion".  Deliberately the repository's real-world
# coordinates rather than component_id: the fact being protected is that one Drone
# build cannot be promoted into one target twice, which is true regardless of how
# the UI groups repositories into projects.  Keyed this way, two projects that
# both point at the same repository still cannot double-deploy it; keyed by
# component_id they could.
#
# drone_connection_id is part of the key and must never be NULL: SQLite treats
# NULLs in a unique index as distinct, so a NULL here would silently switch the
# guard off for that row.
ACTIVE_PROMOTION_SLOT: tuple[str, ...] = (
    "drone_connection_id",
    "drone_owner",
    "drone_repository",
    "source_build_number",
    "target",
)

ACTIVE_PROMOTION_PREDICATE = "status IN ({})".format(
    ", ".join(f"'{status}'" for status in ACTIVE_PROMOTION_STATUSES)
)


class ReleaseBundle(Base):
    __tablename__ = "release_bundles"
    __table_args__ = (
        Index("ix_release_bundles_created_at", "created_at"),
        Index("ix_release_bundles_status", "status"),
        Index("ix_release_bundles_target", "target"),
        Index("ix_release_bundles_project_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Nullable in the database on purpose.  Only deployments carry a hard NOT NULL
    # for their registry columns, because theirs are what the duplicate-promotion
    # index is built on; here the column is for scoping and filtering, and adding
    # it did not justify rebuilding a table three others hold foreign keys into.
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=True
    )
    mode: Mapped[str] = mapped_column(String(30), nullable=False, default=ReleaseMode.BUNDLE.value)
    # Which components this release covers, as a comma-separated snapshot of their
    # keys.  A snapshot rather than a join because it must still read correctly
    # once a component is renamed or deactivated.
    selected_component_keys: Mapped[str | None] = mapped_column(Text, nullable=True)
    target: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=BundleStatus.PENDING.value
    )
    deployment_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=BundleStatus.PENDING.value
    )
    publish_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=PublishStatus.NOT_PUBLISHED.value
    )
    version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    release_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    release_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    workflow_instance_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    current_stage: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failed_stage: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attachment_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attachment_content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attachment_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attachment_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    publish_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    publish_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    publish_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    publish_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    deployments: Mapped[list["Deployment"]] = relationship(
        back_populates="release_bundle",
        cascade="all, delete-orphan",
        order_by="Deployment.created_at",
    )
    # viewonly: deleting a bundle must never delete its audit trail.
    events: Mapped[list["WorkflowEvent"]] = relationship(
        primaryjoin="foreign(WorkflowEvent.release_bundle_id) == ReleaseBundle.id",
        order_by="WorkflowEvent.created_at, WorkflowEvent.id",
        viewonly=True,
    )
    publish_records: Mapped[list["PublishRecord"]] = relationship(
        back_populates="release_bundle",
        cascade="all, delete-orphan",
        order_by="PublishRecord.created_at",
    )


class Deployment(Base):
    __tablename__ = "deployments"
    __table_args__ = (
        Index("ix_deployments_created_at", "created_at"),
        Index("ix_deployments_component", "component"),
        Index("ix_deployments_status", "status"),
        Index("ix_deployments_target", "target"),
        Index("ix_deployments_release_bundle_id", "release_bundle_id"),
        Index("ix_deployments_project_id", "project_id"),
        Index("ix_deployments_component_id", "component_id"),
        Index("ix_deployments_duplicate_lookup", *ACTIVE_PROMOTION_SLOT),
        # The database, not the application, is what makes duplicate promotion
        # impossible.  DeploymentService.assert_not_duplicate is only a fast path
        # for a friendly error message; between its SELECT and the INSERT another
        # request can slip through, so this index is the actual guarantee.
        Index(
            "uq_deployments_active_promotion",
            *ACTIVE_PROMOTION_SLOT,
            unique=True,
            sqlite_where=text(ACTIVE_PROMOTION_PREDICATE),
            postgresql_where=text(ACTIVE_PROMOTION_PREDICATE),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    release_bundle_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("release_bundles.id", ondelete="CASCADE"), nullable=True
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    component_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("project_components.id", ondelete="RESTRICT"), nullable=False
    )
    # A snapshot of the component's key, kept alongside component_id for the same
    # reason workflow_events keeps one: a deployment has to stay readable if the
    # component is later renamed.
    component: Mapped[str] = mapped_column(String(50), nullable=False)
    drone_connection_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("upstream_connections.id", ondelete="RESTRICT"), nullable=False
    )
    drone_owner: Mapped[str] = mapped_column(String(255), nullable=False)
    drone_repository: Mapped[str] = mapped_column(String(255), nullable=False)
    source_build_number: Mapped[int] = mapped_column(Integer, nullable=False)
    promotion_build_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    branch: Mapped[str] = mapped_column(String(255), nullable=False)
    target: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    publish_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=PublishStatus.NOT_PUBLISHED.value
    )
    version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    gitea_release_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    gitea_release_tag: Mapped[str | None] = mapped_column(String(100), nullable=True)
    gitea_release_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_stage: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failed_stage: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Why polling is currently getting nowhere.  Distinct from error_code, which
    # means the deployment itself failed; these clear as soon as a poll succeeds.
    poll_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    poll_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    poll_failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attachment_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attachment_content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attachment_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attachment_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    publish_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    publish_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    publish_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    publish_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    release_bundle: Mapped[ReleaseBundle | None] = relationship(back_populates="deployments")
    # viewonly: deleting a deployment must never delete its audit trail.
    events: Mapped[list["WorkflowEvent"]] = relationship(
        primaryjoin="foreign(WorkflowEvent.deployment_id) == Deployment.id",
        order_by="WorkflowEvent.created_at, WorkflowEvent.id",
        viewonly=True,
    )
    publish_records: Mapped[list["PublishRecord"]] = relationship(
        back_populates="deployment",
        cascade="all, delete-orphan",
        order_by="PublishRecord.created_at",
    )


class PublishRecord(Base):
    __tablename__ = "publish_records"
    __table_args__ = (
        Index("ix_publish_records_created_at", "created_at"),
        Index("ix_publish_records_component", "component"),
        Index("ix_publish_records_status", "status"),
        Index("ix_publish_records_version", "version"),
        Index("ix_publish_records_release_bundle_id", "release_bundle_id"),
        Index("ix_publish_records_deployment_id", "deployment_id"),
        Index("ix_publish_records_project_id", "project_id"),
        UniqueConstraint("deployment_id", "version", name="uq_publish_records_deployment_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    release_bundle_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("release_bundles.id", ondelete="CASCADE"), nullable=True
    )
    deployment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("deployments.id", ondelete="CASCADE"), nullable=False
    )
    # Nullable for the same reason as ReleaseBundle.project_id above.
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=True
    )
    component_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("project_components.id", ondelete="RESTRICT"), nullable=True
    )
    component: Mapped[str] = mapped_column(String(50), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    repo_owner: Mapped[str] = mapped_column(String(255), nullable=False)
    repo_name: Mapped[str] = mapped_column(String(255), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    tag_name: Mapped[str] = mapped_column(String(100), nullable=False)
    gitea_release_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    gitea_release_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    release_bundle: Mapped[ReleaseBundle | None] = relationship(back_populates="publish_records")
    deployment: Mapped[Deployment] = relationship(back_populates="publish_records")


class WorkflowEvent(Base):
    __tablename__ = "workflow_events"
    __table_args__ = (
        Index("ix_workflow_events_release_bundle_id", "release_bundle_id"),
        Index("ix_workflow_events_deployment_id", "deployment_id"),
        Index("ix_workflow_events_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=event_id)
    # Deliberately NOT foreign keys.  An audit row is evidence about something
    # that happened; it must survive the deletion of whatever it describes, and
    # keep pointing at it.  ON DELETE CASCADE used to erase the entire trail
    # along with the release, and ON DELETE SET NULL would erase the linkage.
    release_bundle_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    deployment_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    workflow_instance_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    stage: Mapped[str] = mapped_column(String(100), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Who did this.  actor_source says how we know: an authenticated session, a
    # schedule acting for whoever created it, or the system acting on its own.
    actor: Mapped[str] = mapped_column(String(255), nullable=False, default="system")
    actor_source: Mapped[str] = mapped_column(String(20), nullable=False, default="system")
    # Snapshot of the subject, so an event still reads correctly once the
    # release or deployment it points at is gone.
    component: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    release_bundle: Mapped[ReleaseBundle | None] = relationship(
        primaryjoin="foreign(WorkflowEvent.release_bundle_id) == ReleaseBundle.id",
        viewonly=True,
    )
    deployment: Mapped[Deployment | None] = relationship(
        primaryjoin="foreign(WorkflowEvent.deployment_id) == Deployment.id",
        viewonly=True,
    )


# The audit trail is append-only, and this is what enforces it rather than a
# comment.  Retention work has to drop these triggers deliberately.
_APPEND_ONLY_TRIGGERS = (
    """
    CREATE TRIGGER IF NOT EXISTS workflow_events_block_update
    BEFORE UPDATE ON workflow_events
    BEGIN
        SELECT RAISE(ABORT, 'workflow_events is append-only: rows cannot be updated');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS workflow_events_block_delete
    BEFORE DELETE ON workflow_events
    BEGIN
        SELECT RAISE(ABORT, 'workflow_events is append-only: rows cannot be deleted');
    END
    """,
)

for _statement in _APPEND_ONLY_TRIGGERS:
    event.listen(
        WorkflowEvent.__table__,
        "after_create",
        DDL(_statement).execute_if(dialect="sqlite"),
    )
