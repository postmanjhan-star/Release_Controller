"""Add persisted SpiffWorkflow instances.

Revision ID: 20260820_0002
Revises: 20260820_0001
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260820_0002"
down_revision: str | None = "20260820_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "release_workflows",
        sa.Column("release_id", sa.String(length=36), nullable=False),
        sa.Column("definition_id", sa.String(length=100), nullable=False),
        sa.Column("state_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["release_id"], ["releases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("release_id"),
    )


def downgrade() -> None:
    op.drop_table("release_workflows")
