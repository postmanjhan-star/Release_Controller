"""Make duplicate promotion impossible at the database level.

Revision ID: 20260831_0010
Revises: 20260828_0009
Create Date: 2026-08-31

DeploymentService.assert_not_duplicate was a bare SELECT, so two concurrent
promote requests could both pass it and both reach Drone.  A partial unique
index over the statuses that occupy a promotion slot closes that window.

The predicate matches ACTIVE_PROMOTION_STATUSES in app/db/models/orchestration.py.

Existing rows are not rewritten.  If historical data already contains duplicate
active promotions the index cannot be created; upgrade() reports them by
(component, source_build_number, target) so they can be resolved deliberately
rather than silently collapsed.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260831_0010"
down_revision: str | None = "20260828_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIVE_STATUSES = ("WAITING", "PROMOTING", "DEPLOYING", "SUCCESS")
PREDICATE = "status IN ({})".format(", ".join(f"'{status}'" for status in ACTIVE_STATUSES))


def upgrade() -> None:
    connection = op.get_bind()
    duplicates = connection.execute(
        sa.text(
            f"""
            SELECT component, source_build_number, target, COUNT(*) AS total
            FROM deployments
            WHERE {PREDICATE}
            GROUP BY component, source_build_number, target
            HAVING COUNT(*) > 1
            """
        )
    ).all()
    if duplicates:
        detail = ", ".join(
            f"{row.component}/build#{row.source_build_number}->{row.target} x{row.total}"
            for row in duplicates
        )
        raise RuntimeError(
            "Cannot create uq_deployments_active_promotion: existing duplicate active "
            f"promotions must be resolved first ({detail}). Cancel or fail the "
            "superseded deployments, then re-run this migration."
        )

    op.create_index(
        "uq_deployments_active_promotion",
        "deployments",
        ["component", "source_build_number", "target"],
        unique=True,
        sqlite_where=sa.text(PREDICATE),
        postgresql_where=sa.text(PREDICATE),
    )


def downgrade() -> None:
    op.drop_index("uq_deployments_active_promotion", table_name="deployments")
