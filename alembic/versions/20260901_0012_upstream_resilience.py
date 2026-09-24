"""Record why polling is stalled without failing the deployment.

Revision ID: 20260901_0012
Revises: 20260901_0011
Create Date: 2026-09-01

DeploymentService.refresh used to treat any exception from Drone as proof that
the deployment had failed, so one timed-out poll permanently killed a deployment
that was in fact succeeding.  Transient upstream errors now leave the deployment
alone and are recorded here instead, which means "stuck" has to be visible
somewhere the operator can see it.

poll_error_code is deliberately separate from error_code: error_code means the
deployment failed, poll_error_code means we cannot currently tell.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260901_0012"
down_revision: str | None = "20260901_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("deployments", sa.Column("poll_error_code", sa.String(100), nullable=True))
    op.add_column("deployments", sa.Column("poll_error_message", sa.Text(), nullable=True))
    op.add_column(
        "deployments",
        sa.Column("poll_failure_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("deployments", "poll_failure_count")
    op.drop_column("deployments", "poll_error_message")
    op.drop_column("deployments", "poll_error_code")
