"""Let a schedule name any components, not just a frontend and a backend.

Revision ID: 20260903_0016
Revises: 20260902_0015
Create Date: 2026-09-03

Fourth revision of the multi-project work (docs/specifications/MULTI_PROJECT_V3_0_SPEC.md).

deployment_schedules carried two build-number columns because a project had
exactly two components.  They become rows in schedule_component_builds, which a
project with one component or four fits just as well.

Purely additive: the old columns keep their values and the runner still reads
them for any schedule that has no rows in the new table, so a schedule created
before the upgrade runs afterwards without being touched.  Revision 0017 removes
them once nothing pending predates this one.
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260903_0016"
down_revision: str | None = "20260902_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

# Only these are worth carrying over.  A schedule that has already run has its
# result recorded on the row (deployment_id / release_bundle_id), and a cancelled
# or failed one will not run again -- backfilling those would invent a selection
# for something that is never going to use it.
PENDING = "status IN ('PENDING', 'RUNNING')"


def upgrade() -> None:
    op.create_table(
        "schedule_component_builds",
        sa.Column(
            "schedule_id",
            sa.String(36),
            sa.ForeignKey("deployment_schedules.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "component_id",
            sa.String(36),
            sa.ForeignKey("project_components.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("component_key", sa.String(50), nullable=False),
        sa.Column("build_number", sa.Integer(), nullable=False),
    )
    op.add_column("deployment_schedules", sa.Column("project_id", sa.String(36), nullable=True))
    op.add_column(
        "deployment_schedules", sa.Column("selected_component_keys", sa.Text(), nullable=True)
    )
    op.create_index("ix_deployment_schedules_project_id", "deployment_schedules", ["project_id"])

    connection = op.get_bind()
    components = connection.execute(
        sa.text(
            "SELECT c.id, c.key, c.project_id FROM project_components c "
            "JOIN projects p ON p.id = c.project_id "
            "WHERE p.key = 'default' OR (SELECT COUNT(*) FROM projects) = 1"
        )
    ).all()
    by_key = {row.key: row for row in components}
    if not by_key:
        logger.info("No project components to attach schedules to; nothing to backfill")
        return

    migrated = 0
    for column, key in (
        ("backend_build_number", "backend"),
        ("frontend_build_number", "frontend"),
    ):
        component = by_key.get(key)
        if component is None:
            continue
        result = connection.execute(
            sa.text(
                f"""
                INSERT INTO schedule_component_builds
                    (schedule_id, component_id, component_key, build_number)
                SELECT id, :component_id, :component_key, {column}
                FROM deployment_schedules
                WHERE {column} IS NOT NULL AND {PENDING}
                """
            ),
            {"component_id": component.id, "component_key": component.key},
        )
        migrated += result.rowcount or 0

    # Written per schedule rather than with group_concat: SQLite does not order
    # the values inside it, and this snapshot should read in the order the
    # components deploy in.
    schedules = connection.execute(
        sa.text(
            "SELECT b.schedule_id, b.component_key, c.project_id, c.position "
            "FROM schedule_component_builds b "
            "JOIN project_components c ON c.id = b.component_id"
        )
    ).all()
    grouped: dict[str, list[tuple[int, str, str]]] = {}
    for row in schedules:
        grouped.setdefault(row.schedule_id, []).append(
            (row.position, row.component_key, row.project_id)
        )
    for schedule_id, entries in grouped.items():
        entries.sort()
        connection.execute(
            sa.text(
                "UPDATE deployment_schedules SET project_id = :project_id, "
                "selected_component_keys = :keys WHERE id = :id"
            ),
            {
                "id": schedule_id,
                "project_id": entries[0][2],
                "keys": ",".join(key for _position, key, _project in entries),
            },
        )
    logger.info("Backfilled %s pending schedule component selection(s)", migrated)


def downgrade() -> None:
    op.drop_index("ix_deployment_schedules_project_id", table_name="deployment_schedules")
    op.drop_column("deployment_schedules", "selected_component_keys")
    op.drop_column("deployment_schedules", "project_id")
    op.drop_table("schedule_component_builds")
