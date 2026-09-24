"""Link schedule-created email notifications to their schedule.

Revision ID: 20260827_0007
Revises: 20260827_0006
Create Date: 2026-08-27
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa

from alembic import op

revision: str = "20260827_0007"
down_revision: str | None = "20260827_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("email_outbox") as batch_op:
        batch_op.add_column(sa.Column("schedule_id", sa.String(36), nullable=True))
        batch_op.create_foreign_key(
            "fk_email_outbox_schedule_id",
            "deployment_schedules",
            ["schedule_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_email_outbox_schedule_id", ["schedule_id"])

    # Existing schedule notices predate the explicit relationship. They are
    # created in the same transaction, so their timestamps differ by only a
    # few milliseconds. Link only a single nearest schedule within one second.
    bind = op.get_bind()
    schedules = list(
        bind.execute(sa.text("SELECT id, created_at FROM deployment_schedules")).mappings()
    )
    notices = list(
        bind.execute(
            sa.text(
                "SELECT id, created_at FROM email_outbox "
                "WHERE workflow_event_id IS NULL AND schedule_id IS NULL"
            )
        ).mappings()
    )
    for notice in notices:
        notice_time = _as_datetime(notice["created_at"])
        nearby = [
            schedule
            for schedule in schedules
            if abs((_as_datetime(schedule["created_at"]) - notice_time).total_seconds()) < 1
        ]
        if len(nearby) == 1:
            bind.execute(
                sa.text("UPDATE email_outbox SET schedule_id = :schedule_id WHERE id = :notice_id"),
                {"schedule_id": nearby[0]["id"], "notice_id": notice["id"]},
            )


def downgrade() -> None:
    with op.batch_alter_table("email_outbox") as batch_op:
        batch_op.drop_index("ix_email_outbox_schedule_id")
        batch_op.drop_constraint("fk_email_outbox_schedule_id", type_="foreignkey")
        batch_op.drop_column("schedule_id")


def _as_datetime(value: datetime | str) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)
