"""The project registry: connections, projects, components.

Revision ID: 20260902_0014
Revises: 20260901_0013
Create Date: 2026-09-02

First revision of the multi-project work (docs/specifications/MULTI_PROJECT_V3_0_SPEC.md).

This revision adds tables and touches nothing that exists.  No running code reads
these rows yet -- promote, refresh and publish still take their repositories from
Settings -- so it can be deployed and observed on its own, and downgraded cleanly
if the plan changes.

The seed matters.  On an installation that already has DRONE_* / GITEA_* set, the
upgrade materialises exactly what those variables describe: one Drone connection,
one Gitea connection, a project called "default", and its backend and frontend
components in that deployment order (backend first, which is what
ReleaseOrchestrator does today).  The registry therefore starts out agreeing with
the environment rather than empty, and the two can be compared side by side before
anything is switched over.

The seeded tokens are encrypted with APP_SECRET_KEY.  If it is unset, they are
encrypted with the development fallback in app/core/crypto.py -- fine for local
work, refused at startup in production.
"""

import logging
import os
import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.config import get_settings
from app.core.crypto import token_cipher, token_hint

revision: str = "20260902_0014"
down_revision: str | None = "20260901_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    op.create_table(
        "upstream_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("token_encrypted", sa.Text(), nullable=False),
        sa.Column("token_hint", sa.String(16), nullable=False, server_default="—"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("verify_status", sa.String(20), nullable=True),
        sa.Column("verify_detail", sa.Text(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_upstream_connections_kind", "upstream_connections", ["kind"])
    # At most one default per kind, enforced by the database.
    op.create_index(
        "uq_upstream_connections_default_per_kind",
        "upstream_connections",
        ["kind"],
        unique=True,
        sqlite_where=sa.text("is_default = 1"),
        postgresql_where=sa.text("is_default"),
    )

    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("key", sa.String(50), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_target", sa.String(100), nullable=False, server_default="production"),
        sa.Column(
            "drone_connection_id",
            sa.String(36),
            sa.ForeignKey("upstream_connections.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "gitea_connection_id",
            sa.String(36),
            sa.ForeignKey("upstream_connections.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_projects_created_at", "projects", ["created_at"])

    op.create_table(
        "project_components",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(50), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("drone_owner", sa.String(255), nullable=False),
        sa.Column("drone_repo", sa.String(255), nullable=False),
        sa.Column("promote_target_override", sa.String(100), nullable=True),
        sa.Column(
            "drone_connection_id",
            sa.String(36),
            sa.ForeignKey("upstream_connections.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("publish_enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("gitea_owner", sa.String(255), nullable=True),
        sa.Column("gitea_repo", sa.String(255), nullable=True),
        sa.Column(
            "gitea_connection_id",
            sa.String(36),
            sa.ForeignKey("upstream_connections.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("tag_prefix", sa.String(30), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "key", name="uq_project_components_key"),
        sa.UniqueConstraint("project_id", "position", name="uq_project_components_position"),
    )
    op.create_index("ix_project_components_project_id", "project_components", ["project_id"])

    _seed_from_environment()


def _seed_from_environment() -> None:
    """Materialise the current .env configuration as the first project.

    Deliberately conservative: it seeds only when the registry is empty and the
    environment actually describes a repository.  A blank or partly-filled
    environment leaves empty tables rather than a half-project that looks
    configured but is not.
    """
    connection = op.get_bind()
    existing = connection.execute(sa.text("SELECT COUNT(*) FROM projects")).scalar_one()
    if existing:
        logger.info("Project registry already populated; skipping seed")
        return

    settings = get_settings()

    # The per-repository variables are read straight from the environment rather
    # than from Settings.  A migration is frozen history: it has to keep running
    # against a database from before v3.0, and Settings is live code that has
    # since dropped these fields -- repositories are registry rows now.
    def env(name: str) -> str | None:
        value = os.environ.get(name, "").strip()
        return value or None

    components = [
        (
            "backend",
            "Backend",
            env("DRONE_BACKEND_REPO_OWNER"),
            env("DRONE_BACKEND_REPO_NAME"),
            env("GITEA_BACKEND_REPO_OWNER"),
            env("GITEA_BACKEND_REPO_NAME"),
        ),
        (
            "frontend",
            "Frontend",
            env("DRONE_FRONTEND_REPO_OWNER"),
            env("DRONE_FRONTEND_REPO_NAME"),
            env("GITEA_FRONTEND_REPO_OWNER"),
            env("GITEA_FRONTEND_REPO_NAME"),
        ),
    ]
    seedable = [item for item in components if item[2] and item[3]]
    if not seedable:
        logger.info(
            "No DRONE_*_REPO_OWNER/NAME configured; the project registry starts empty. "
            "Add a project under Projects."
        )
        return

    cipher = token_cipher(settings)
    drone_id = str(uuid.uuid4())
    gitea_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    connection.execute(
        sa.text(
            """
            INSERT INTO upstream_connections
                (id, kind, name, base_url, token_encrypted, token_hint, is_default,
                 created_at, updated_at)
            VALUES (:id, :kind, :name, :base_url, :token, :hint, 1,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
        ),
        [
            {
                "id": drone_id,
                "kind": "drone",
                "name": "default-drone",
                "base_url": settings.drone_server.rstrip("/"),
                "token": cipher.encrypt(settings.drone_token or ""),
                "hint": token_hint(settings.drone_token or ""),
            },
            {
                "id": gitea_id,
                "kind": "gitea",
                "name": "default-gitea",
                "base_url": settings.gitea_server.rstrip("/"),
                "token": cipher.encrypt(settings.gitea_token or ""),
                "hint": token_hint(settings.gitea_token or ""),
            },
        ],
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO projects
                (id, key, name, description, default_target, drone_connection_id,
                 gitea_connection_id, is_archived, created_at, updated_at)
            VALUES (:id, 'default', :name, :description, :target, :drone, :gitea, 0,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
        ),
        {
            "id": project_id,
            "name": "Default project",
            "description": (
                "Seeded from the DRONE_* and GITEA_* environment variables by "
                "migration 20260902_0014."
            ),
            "target": env("DRONE_DEFAULT_TARGET") or "production",
            "drone": drone_id,
            "gitea": gitea_id,
        },
    )
    # Backend first: that is the order ReleaseOrchestrator.create_bundle uses
    # today, and position is what will carry it once the orchestrator reads
    # these rows instead of hard-coding the pair.
    connection.execute(
        sa.text(
            """
            INSERT INTO project_components
                (id, project_id, key, display_name, position, drone_owner, drone_repo,
                 promote_target_override, drone_connection_id, publish_enabled,
                 gitea_owner, gitea_repo, gitea_connection_id, tag_prefix, is_active,
                 created_at, updated_at)
            VALUES (:id, :project_id, :key, :display_name, :position, :drone_owner,
                    :drone_repo, NULL, NULL, :publish_enabled, :gitea_owner, :gitea_repo,
                    NULL, '', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
        ),
        [
            {
                "id": str(uuid.uuid4()),
                "project_id": project_id,
                "key": key,
                "display_name": display_name,
                "position": position,
                "drone_owner": drone_owner,
                "drone_repo": drone_repo,
                "publish_enabled": 1 if (gitea_owner and gitea_repo) else 0,
                "gitea_owner": gitea_owner,
                "gitea_repo": gitea_repo,
            }
            for position, (
                key,
                display_name,
                drone_owner,
                drone_repo,
                gitea_owner,
                gitea_repo,
            ) in enumerate(seedable, start=1)
        ],
    )
    logger.info(
        "Seeded project 'default' with %s component(s) from the environment",
        len(seedable),
    )


def downgrade() -> None:
    op.drop_index("ix_project_components_project_id", table_name="project_components")
    op.drop_table("project_components")
    op.drop_index("ix_projects_created_at", table_name="projects")
    op.drop_table("projects")
    op.drop_index("uq_upstream_connections_default_per_kind", table_name="upstream_connections")
    op.drop_index("ix_upstream_connections_kind", table_name="upstream_connections")
    op.drop_table("upstream_connections")
