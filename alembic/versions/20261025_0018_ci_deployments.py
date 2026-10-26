"""Scoped CI service credentials and persistent deployment inbox.

Revision ID: 20261025_0018
Revises: 20260926_0017
"""

import sqlalchemy as sa

from alembic import op

revision = "20261025_0018"
down_revision = "20260926_0017"
branch_labels = None
depends_on = None


def fk(name, table):
    return sa.Column(
        name, sa.String(36), sa.ForeignKey(f"{table}.id", ondelete="RESTRICT"), nullable=False
    )


def stamp(name, nullable=False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade():
    op.add_column("deployments", sa.Column("required_promotion_stages", sa.JSON(), nullable=True))
    op.create_table(
        "ci_service_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        fk("project_id", "projects"),
        fk("component_id", "project_components"),
        sa.Column("target", sa.String(100), nullable=False),
        sa.Column("branch", sa.String(255), nullable=False),
        stamp("created_at"),
        stamp("expires_at"),
        stamp("revoked_at", True),
    )
    op.create_table(
        "ci_policies",
        sa.Column("id", sa.String(36), primary_key=True),
        fk("component_id", "project_components"),
        sa.Column("target", sa.String(100), nullable=False),
        sa.Column("branch", sa.String(255), nullable=False),
        sa.Column("source_stages", sa.JSON(), nullable=False),
        sa.Column("promotion_stages", sa.JSON(), nullable=False),
        sa.UniqueConstraint("component_id", "target", name="uq_ci_policy_scope"),
    )
    op.create_table(
        "ci_deployment_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        fk("service_token_id", "ci_service_tokens"),
        fk("project_id", "projects"),
        fk("component_id", "project_components"),
        fk("drone_connection_id", "upstream_connections"),
        sa.Column("target", sa.String(100), nullable=False),
        sa.Column("branch", sa.String(255), nullable=False),
        sa.Column("source_build_number", sa.Integer(), nullable=False),
        sa.Column("drone_owner", sa.String(255), nullable=False),
        sa.Column("drone_repository", sa.String(255), nullable=False),
        sa.Column("source_stages", sa.JSON(), nullable=False),
        sa.Column("promotion_stages", sa.JSON(), nullable=False),
        sa.Column("commit_sha", sa.String(64)),
        sa.Column(
            "deployment_id", sa.String(36), sa.ForeignKey("deployments.id", ondelete="RESTRICT")
        ),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("error_code", sa.String(100)),
        stamp("created_at"),
        stamp("updated_at"),
        stamp("deadline_at"),
        stamp("finished_at", True),
        sa.Column("lease_id", sa.String(36)),
        stamp("lease_until", True),
        sa.UniqueConstraint("component_id", "source_build_number", "target", name="uq_ci_intent"),
    )
    op.create_index("ix_ci_deployment_requests_status", "ci_deployment_requests", ["status"])
    op.create_table(
        "ci_deployment_events",
        sa.Column("id", sa.String(36), primary_key=True),
        fk("request_id", "ci_deployment_requests"),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("error_code", sa.String(100)),
        stamp("created_at"),
    )
    op.create_index("ix_ci_deployment_events_request_id", "ci_deployment_events", ["request_id"])


def downgrade():
    op.drop_table("ci_deployment_events")
    op.drop_table("ci_deployment_requests")
    op.drop_table("ci_policies")
    op.drop_table("ci_service_tokens")
    op.drop_column("deployments", "required_promotion_stages")
