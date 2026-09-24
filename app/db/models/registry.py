"""The project registry: what this controller can deploy, and where.

Before v3.0 the answer lived in eight environment variables that described one
project with exactly two components.  It lives here instead, so a person can add
a project without a redeploy, and so a project can have one component (a repo
holding both halves) or three (frontend, backend, gateway) rather than exactly
two.

Nothing in this module is on the deployment path yet.  The promote, refresh and
publish services still read their repositories from Settings; wiring them to
these rows is the next revision's work.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.shared.time import utc_now


class UpstreamConnection(Base):
    """One Drone or Gitea endpoint, with its credential.

    Drone and Gitea share a table because they are the same thing from the
    operator's side -- a server plus a token -- and because the encryption and
    "which one is the default" rules should exist once, not twice.
    """

    __tablename__ = "upstream_connections"
    __table_args__ = (
        Index("ix_upstream_connections_kind", "kind"),
        # At most one default per kind, enforced by the database rather than by
        # the service that sets it.
        Index(
            "uq_upstream_connections_default_per_kind",
            "kind",
            unique=True,
            sqlite_where=text("is_default = 1"),
            postgresql_where=text("is_default"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    # Fernet ciphertext.  Never returned by the API, in any form.
    token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    # The last four characters of the plaintext, so two stored credentials can be
    # told apart without revealing either.
    token_hint: Mapped[str] = mapped_column(String(16), nullable=False, default="—")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verify_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    verify_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (Index("ix_projects_created_at", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    key: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_target: Mapped[str] = mapped_column(String(100), nullable=False, default="production")
    # NULL means "the default connection of that kind".  Most installations have
    # one Drone and one Gitea; per-project overrides exist for the ones that do not.
    drone_connection_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("upstream_connections.id", ondelete="RESTRICT"), nullable=True
    )
    gitea_connection_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("upstream_connections.id", ondelete="RESTRICT"), nullable=True
    )
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    components: Mapped[list["ProjectComponent"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectComponent.position",
    )


class ProjectComponent(Base):
    """One deployable unit: a Drone repository, promoted to a target.

    Two components may share a repository -- that is how a single repo holding
    both a frontend and a backend is expressed.  What must differ is the slot
    they promote into, which is why promote_target_override exists and why
    ProjectService refuses to save two components that resolve to the same
    (connection, owner, repo, target).  Without that check the collision would
    only surface much later, as an unexplained 409 from the duplicate-promotion
    index.
    """

    __tablename__ = "project_components"
    __table_args__ = (
        UniqueConstraint("project_id", "key", name="uq_project_components_key"),
        # Position is the deployment order.  Unique because the first version
        # deploys strictly in sequence; equal positions are how parallel
        # deployment would later be expressed, and the column is shaped for it.
        UniqueConstraint("project_id", "position", name="uq_project_components_position"),
        Index("ix_project_components_project_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(50), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    drone_owner: Mapped[str] = mapped_column(String(255), nullable=False)
    drone_repo: Mapped[str] = mapped_column(String(255), nullable=False)
    # NULL means "the project's default_target".
    promote_target_override: Mapped[str | None] = mapped_column(String(100), nullable=True)
    drone_connection_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("upstream_connections.id", ondelete="RESTRICT"), nullable=True
    )

    publish_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    gitea_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gitea_repo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gitea_connection_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("upstream_connections.id", ondelete="RESTRICT"), nullable=True
    )
    # Two components publishing into one Gitea repository cannot both own the tag
    # "v1.2.3".  A prefix per component ("fe-", "be-") is what keeps them apart.
    tag_prefix: Mapped[str] = mapped_column(String(30), nullable=False, default="")

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    project: Mapped[Project] = relationship(back_populates="components")

    @property
    def effective_target(self) -> str:
        return self.promote_target_override or self.project.default_target

    @property
    def drone_slug(self) -> str:
        return f"{self.drone_owner}/{self.drone_repo}"

    @property
    def gitea_slug(self) -> str | None:
        if self.gitea_owner and self.gitea_repo:
            return f"{self.gitea_owner}/{self.gitea_repo}"
        return None
