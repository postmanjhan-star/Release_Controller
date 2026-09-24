"""Make the audit trail answer "who", and survive what it describes.

Revision ID: 20260901_0013
Revises: 20260901_0012
Create Date: 2026-09-01

Three defects in one table:

* workflow_events had ON DELETE CASCADE to both release_bundles and deployments,
  so deleting a release erased the evidence about it.  The columns keep their
  values but are no longer foreign keys: an audit row is a statement about
  something that happened and must outlive the thing it describes.
* No column recorded who caused an event, which is the one question the audit
  trail exists to answer.  Publishing recorded no operator at all.
* "Append-only" was a docstring.  It is now two triggers.

Existing rows are preserved.  They get actor='system' because the operator was
never captured for them, and that is the honest value -- not a guess.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260901_0013"
down_revision: str | None = "20260901_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BLOCK_UPDATE = """
CREATE TRIGGER workflow_events_block_update
BEFORE UPDATE ON workflow_events
BEGIN
    SELECT RAISE(ABORT, 'workflow_events is append-only: rows cannot be updated');
END
"""

BLOCK_DELETE = """
CREATE TRIGGER workflow_events_block_delete
BEFORE DELETE ON workflow_events
BEGIN
    SELECT RAISE(ABORT, 'workflow_events is append-only: rows cannot be deleted');
END
"""

# The shape workflow_events must have after the rebuild.  Passing this as
# copy_from is how the SQLite batch rebuild is told to drop the foreign keys;
# reflection alone would faithfully recreate them.
WORKFLOW_EVENTS = sa.Table(
    "workflow_events",
    sa.MetaData(),
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("release_bundle_id", sa.String(36), nullable=True),
    sa.Column("deployment_id", sa.String(36), nullable=True),
    sa.Column("workflow_instance_id", sa.String(36), nullable=True),
    sa.Column("stage", sa.String(100), nullable=False),
    sa.Column("event_type", sa.String(50), nullable=False),
    sa.Column("status", sa.String(30), nullable=True),
    sa.Column("error_code", sa.String(100), nullable=True),
    sa.Column("message", sa.Text(), nullable=True),
    sa.Column("actor", sa.String(255), nullable=False, server_default="system"),
    sa.Column("actor_source", sa.String(20), nullable=False, server_default="system"),
    sa.Column("component", sa.String(20), nullable=True),
    sa.Column("target", sa.String(100), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)


def _drop_triggers() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    op.execute("DROP TRIGGER IF EXISTS workflow_events_block_update")
    op.execute("DROP TRIGGER IF EXISTS workflow_events_block_delete")


def upgrade() -> None:
    _drop_triggers()

    op.add_column(
        "workflow_events",
        sa.Column("actor", sa.String(255), nullable=False, server_default="system"),
    )
    op.add_column(
        "workflow_events",
        sa.Column("actor_source", sa.String(20), nullable=False, server_default="system"),
    )
    op.add_column("workflow_events", sa.Column("component", sa.String(20), nullable=True))
    op.add_column("workflow_events", sa.Column("target", sa.String(100), nullable=True))

    # Backfill the subject snapshot from the rows that still exist, so historical
    # events stay readable if their release is later removed.
    op.execute(
        """
        UPDATE workflow_events
        SET component = (
            SELECT d.component FROM deployments d WHERE d.id = workflow_events.deployment_id
        )
        WHERE deployment_id IS NOT NULL AND component IS NULL
        """
    )
    op.execute(
        """
        UPDATE workflow_events
        SET target = COALESCE(
            (SELECT b.target FROM release_bundles b WHERE b.id = workflow_events.release_bundle_id),
            (SELECT d.target FROM deployments d WHERE d.id = workflow_events.deployment_id)
        )
        WHERE target IS NULL
        """
    )

    # Drop the cascading foreign keys by rebuilding the table without them.
    with op.batch_alter_table(
        "workflow_events", copy_from=WORKFLOW_EVENTS, recreate="always"
    ) as batch:
        batch.create_index("ix_workflow_events_release_bundle_id", ["release_bundle_id"])
        batch.create_index("ix_workflow_events_deployment_id", ["deployment_id"])
        batch.create_index("ix_workflow_events_created_at", ["created_at"])
        batch.create_index("ix_workflow_events_actor", ["actor"])

    op.add_column("publish_records", sa.Column("requested_by", sa.String(255), nullable=True))

    if op.get_bind().dialect.name == "sqlite":
        op.execute(BLOCK_UPDATE)
        op.execute(BLOCK_DELETE)


def downgrade() -> None:
    _drop_triggers()
    op.drop_column("publish_records", "requested_by")
    with op.batch_alter_table("workflow_events", copy_from=WORKFLOW_EVENTS) as batch:
        batch.drop_column("target")
        batch.drop_column("component")
        batch.drop_column("actor_source")
        batch.drop_column("actor")
    # The cascading foreign keys are deliberately not restored: re-creating them
    # would arm the data-loss behaviour this revision exists to remove.
