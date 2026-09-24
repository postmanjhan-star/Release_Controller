"""Add persisted deployment schedules and SMTP email outbox.

Revision ID: 20260825_0004
Revises: 20260821_0003
Create Date: 2026-08-25
"""

from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa

from alembic import op

revision: str = "20260825_0004"
down_revision: str | None = "20260821_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deployment_schedules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("mode", sa.String(30), nullable=False),
        sa.Column("frontend_build_number", sa.Integer(), nullable=True),
        sa.Column("backend_build_number", sa.Integer(), nullable=True),
        sa.Column("target", sa.String(100), nullable=False),
        sa.Column("scheduled_for_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("requested_by", sa.String(255), nullable=True),
        sa.Column("notification_recipients", sa.Text(), nullable=True),
        sa.Column("deployment_id", sa.String(36), nullable=True),
        sa.Column("release_bundle_id", sa.String(36), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["release_bundle_id"], ["release_bundles.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_deployment_schedules_due",
        "deployment_schedules",
        ["status", "scheduled_for_utc"],
    )
    op.create_index("ix_deployment_schedules_created_at", "deployment_schedules", ["created_at"])

    op.create_table(
        "email_outbox",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workflow_event_id", sa.String(36), nullable=True),
        sa.Column("recipients", sa.Text(), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workflow_event_id"], ["workflow_events.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("workflow_event_id", name="uq_email_outbox_workflow_event_id"),
    )
    op.create_index("ix_email_outbox_delivery", "email_outbox", ["status", "next_attempt_at"])
    op.create_index("ix_email_outbox_created_at", "email_outbox", ["created_at"])

    op.create_table(
        "email_notification_cursors",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("after_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("after_event_id", sa.String(36), nullable=False),
    )
    op.bulk_insert(
        sa.table(
            "email_notification_cursors",
            sa.column("id", sa.String(50)),
            sa.column("after_created_at", sa.DateTime(timezone=True)),
            sa.column("after_event_id", sa.String(36)),
        ),
        [
            {
                "id": "workflow_events",
                "after_created_at": datetime.now(timezone.utc),
                "after_event_id": "",
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("email_notification_cursors")
    op.drop_table("email_outbox")
    op.drop_table("deployment_schedules")
