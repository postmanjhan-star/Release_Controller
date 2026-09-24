"""Add Release Worker v2.3 Gitea publishing state.

Revision ID: 20260828_0008
Revises: 20260827_0007
Create Date: 2026-08-28

Existing deployment history is preserved and starts as NOT_PUBLISHED.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260828_0008"
down_revision: str | None = "20260827_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("release_bundles") as batch_op:
        batch_op.add_column(
            sa.Column("deployment_status", sa.String(30), nullable=False, server_default="PENDING")
        )
        batch_op.add_column(
            sa.Column(
                "publish_status", sa.String(30), nullable=False, server_default="NOT_PUBLISHED"
            )
        )
        batch_op.add_column(sa.Column("version", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("release_name", sa.String(255), nullable=True))
        batch_op.add_column(sa.Column("release_notes", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("publish_started_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("published_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("publish_failed_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("publish_error_code", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("publish_error_message", sa.Text(), nullable=True))
    op.execute(sa.text("UPDATE release_bundles SET deployment_status = status"))

    with op.batch_alter_table("deployments") as batch_op:
        batch_op.add_column(
            sa.Column(
                "publish_status", sa.String(30), nullable=False, server_default="NOT_PUBLISHED"
            )
        )
        batch_op.add_column(sa.Column("version", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("gitea_release_id", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("gitea_release_tag", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("gitea_release_url", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("publish_started_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("published_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("publish_failed_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("publish_error_code", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("publish_error_message", sa.Text(), nullable=True))

    op.create_table(
        "publish_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("release_bundle_id", sa.String(36), nullable=True),
        sa.Column("deployment_id", sa.String(36), nullable=False),
        sa.Column("component", sa.String(20), nullable=False),
        sa.Column("version", sa.String(100), nullable=False),
        sa.Column("repo_owner", sa.String(255), nullable=False),
        sa.Column("repo_name", sa.String(255), nullable=False),
        sa.Column("commit_sha", sa.String(64), nullable=False),
        sa.Column("tag_name", sa.String(100), nullable=False),
        sa.Column("gitea_release_id", sa.String(100), nullable=True),
        sa.Column("gitea_release_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["release_bundle_id"], ["release_bundles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "deployment_id", "version", name="uq_publish_records_deployment_version"
        ),
    )
    for name, columns in (
        ("ix_publish_records_created_at", ["created_at"]),
        ("ix_publish_records_component", ["component"]),
        ("ix_publish_records_status", ["status"]),
        ("ix_publish_records_version", ["version"]),
        ("ix_publish_records_release_bundle_id", ["release_bundle_id"]),
        ("ix_publish_records_deployment_id", ["deployment_id"]),
    ):
        op.create_index(name, "publish_records", columns)


def downgrade() -> None:
    op.drop_table("publish_records")
    with op.batch_alter_table("deployments") as batch_op:
        for column in (
            "publish_error_message",
            "publish_error_code",
            "publish_failed_at",
            "published_at",
            "publish_started_at",
            "gitea_release_url",
            "gitea_release_tag",
            "gitea_release_id",
            "version",
            "publish_status",
        ):
            batch_op.drop_column(column)
    with op.batch_alter_table("release_bundles") as batch_op:
        for column in (
            "publish_error_message",
            "publish_error_code",
            "publish_failed_at",
            "published_at",
            "publish_started_at",
            "release_notes",
            "release_name",
            "version",
            "publish_status",
            "deployment_status",
        ):
            batch_op.drop_column(column)
