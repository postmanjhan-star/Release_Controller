"""Add managed notification recipients.

Revision ID: 20260826_0005
Revises: 20260825_0004
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260826_0005"
down_revision: str | None = "20260825_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_recipients",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email", name="uq_notification_recipients_email"),
    )
    op.create_index(
        "ix_notification_recipients_created_at",
        "notification_recipients",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_table("notification_recipients")
