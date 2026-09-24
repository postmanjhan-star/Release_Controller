"""Create releases table.

Revision ID: 20260820_0001
Revises:
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260820_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_status = sa.Enum(
    "PENDING",
    "APPROVED",
    "REJECTED",
    "DEPLOYING",
    "SUCCESS",
    "FAILED",
    name="release_status",
    native_enum=False,
    create_constraint=True,
)


def upgrade() -> None:
    op.create_table(
        "releases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("repository", sa.String(length=255), nullable=False),
        sa.Column("branch", sa.String(length=255), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=False),
        sa.Column("environment", sa.String(length=100), nullable=False),
        sa.Column("status", release_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by", sa.String(length=255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_by", sa.String(length=255), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deploy_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deploy_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "repository",
            "commit_sha",
            "environment",
            name="uq_releases_repository_commit_environment",
        ),
    )
    op.create_index("ix_releases_created_at", "releases", ["created_at"])
    op.create_index("ix_releases_environment", "releases", ["environment"])
    op.create_index("ix_releases_repository", "releases", ["repository"])
    op.create_index("ix_releases_status", "releases", ["status"])


def downgrade() -> None:
    op.drop_index("ix_releases_status", table_name="releases")
    op.drop_index("ix_releases_repository", table_name="releases")
    op.drop_index("ix_releases_environment", table_name="releases")
    op.drop_index("ix_releases_created_at", table_name="releases")
    op.drop_table("releases")
