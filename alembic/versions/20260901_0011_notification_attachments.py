"""Add persisted notification attachments.

Revision ID: 20260901_0011
Revises: 20260831_0010
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260901_0011"
down_revision: str | None = "20260831_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("deployment_schedules", "release_bundles", "deployments", "email_outbox")


def upgrade() -> None:
    for table_name in TABLES:
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.add_column(sa.Column("attachment_filename", sa.String(length=255)))
            batch_op.add_column(sa.Column("attachment_content_type", sa.String(length=255)))
            batch_op.add_column(sa.Column("attachment_size", sa.Integer()))
            batch_op.add_column(sa.Column("attachment_data", sa.LargeBinary()))


def downgrade() -> None:
    for table_name in reversed(TABLES):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_column("attachment_data")
            batch_op.drop_column("attachment_size")
            batch_op.drop_column("attachment_content_type")
            batch_op.drop_column("attachment_filename")
