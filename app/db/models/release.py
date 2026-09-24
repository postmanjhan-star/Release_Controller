import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.release.value_objects import ReleaseStatus
from app.domain.shared.time import utc_now


class Release(Base):
    __tablename__ = "releases"
    __table_args__ = (
        UniqueConstraint(
            "repository",
            "commit_sha",
            "environment",
            name="uq_releases_repository_commit_environment",
        ),
        Index("ix_releases_created_at", "created_at"),
        Index("ix_releases_repository", "repository"),
        Index("ix_releases_environment", "environment"),
        Index("ix_releases_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    repository: Mapped[str] = mapped_column(String(255), nullable=False)
    branch: Mapped[str] = mapped_column(String(255), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    environment: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[ReleaseStatus] = mapped_column(
        Enum(
            ReleaseStatus,
            name="release_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=False,
        default=ReleaseStatus.PENDING,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deploy_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deploy_finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    workflow: Mapped["ReleaseWorkflow | None"] = relationship(
        back_populates="release",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )


from app.db.models.workflow import ReleaseWorkflow  # noqa: E402
