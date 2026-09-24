"""Upgrade v1 directly to Release Worker v2.2.

Revision ID: 20260821_0003
Revises: 20260820_0002
Create Date: 2026-08-21

The v1 releases and release_workflows tables are intentionally untouched.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260821_0003"
down_revision: str | None = "20260820_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "release_bundles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("mode", sa.String(30), nullable=False),
        sa.Column("target", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("workflow_instance_id", sa.String(36), nullable=True),
        sa.Column("current_stage", sa.String(100), nullable=True),
        sa.Column("failed_stage", sa.String(100), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_release_bundles_created_at", "release_bundles", ["created_at"])
    op.create_index("ix_release_bundles_status", "release_bundles", ["status"])
    op.create_index("ix_release_bundles_target", "release_bundles", ["target"])

    op.create_table(
        "deployments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("release_bundle_id", sa.String(36), nullable=True),
        sa.Column("component", sa.String(20), nullable=False),
        sa.Column("drone_owner", sa.String(255), nullable=False),
        sa.Column("drone_repository", sa.String(255), nullable=False),
        sa.Column("source_build_number", sa.Integer(), nullable=False),
        sa.Column("promotion_build_number", sa.Integer(), nullable=True),
        sa.Column("commit_sha", sa.String(64), nullable=False),
        sa.Column("branch", sa.String(255), nullable=False),
        sa.Column("target", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("current_stage", sa.String(100), nullable=True),
        sa.Column("failed_stage", sa.String(100), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["release_bundle_id"], ["release_bundles.id"], ondelete="CASCADE"),
    )
    for name, columns in (
        ("ix_deployments_created_at", ["created_at"]),
        ("ix_deployments_component", ["component"]),
        ("ix_deployments_status", ["status"]),
        ("ix_deployments_target", ["target"]),
        ("ix_deployments_release_bundle_id", ["release_bundle_id"]),
        ("ix_deployments_duplicate_lookup", ["component", "source_build_number", "target"]),
    ):
        op.create_index(name, "deployments", columns)

    op.create_table(
        "workflow_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("release_bundle_id", sa.String(36), nullable=True),
        sa.Column("deployment_id", sa.String(36), nullable=True),
        sa.Column("workflow_instance_id", sa.String(36), nullable=True),
        sa.Column("stage", sa.String(100), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(30), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["release_bundle_id"], ["release_bundles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_workflow_events_release_bundle_id", "workflow_events", ["release_bundle_id"]
    )
    op.create_index("ix_workflow_events_deployment_id", "workflow_events", ["deployment_id"])
    op.create_index("ix_workflow_events_created_at", "workflow_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("workflow_events")
    op.drop_table("deployments")
    op.drop_table("release_bundles")
