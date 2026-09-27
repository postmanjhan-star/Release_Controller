"""Add controlled release plans and MCP invocation evidence.

Revision ID: 20260926_0017
Revises: 20260903_0016
"""

import sqlalchemy as sa

from alembic import op

revision = "20260926_0017"
down_revision = "20260903_0016"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "release_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("approved_by", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("release_id", sa.String(36), unique=True),
    )
    op.create_table(
        "mcp_tool_invocations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("invocation_id", sa.String(36), nullable=False),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("result_status", sa.String(50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("client", sa.String(100), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
    )
    op.create_index(
        "ix_mcp_tool_invocations_invocation_id", "mcp_tool_invocations", ["invocation_id"]
    )
    if op.get_bind().dialect.name == "sqlite":
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER mcp_audit_block_{operation.lower()} "
                f"BEFORE {operation} ON mcp_tool_invocations BEGIN "
                "SELECT RAISE(ABORT, 'mcp audit is append-only'); END"
            )


def downgrade():
    op.drop_table("mcp_tool_invocations")
    op.drop_table("release_plans")
