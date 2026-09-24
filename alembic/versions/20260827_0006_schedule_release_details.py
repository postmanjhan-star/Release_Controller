"""Add release details to deployment schedules.

Revision ID: 20260827_0006
Revises: 20260826_0005
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260827_0006"
down_revision: str | None = "20260826_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "deployment_schedules",
        sa.Column("release_version", sa.String(100), nullable=True),
    )
    op.add_column(
        "deployment_schedules",
        sa.Column("release_notes", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("deployment_schedules", "release_notes")
    op.drop_column("deployment_schedules", "release_version")
