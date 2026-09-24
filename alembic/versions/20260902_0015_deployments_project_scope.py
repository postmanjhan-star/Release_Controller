"""Attach deployments to the project registry, and rekey the promotion guard.

Revision ID: 20260902_0015
Revises: 20260902_0014
Create Date: 2026-09-02

Second revision of the multi-project work (docs/specifications/MULTI_PROJECT_V3_0_SPEC.md).

**This one is a one-way door.**  Downgrading restores an index keyed on
(component, source_build_number, target), which cannot tell two projects'
components apart -- so once a second project exists, going back would report its
deployments as duplicates of the first one's.  Take the backup before, not after.

What changes:

* deployments gains project_id, component_id and drone_connection_id, all NOT NULL,
  and the promotion guard is rebuilt on
  (drone_connection_id, drone_owner, drone_repository, source_build_number, target).
  The old key described *the name we gave the thing*; the new one describes *the
  repository that actually gets deployed*, which is the fact worth protecting --
  and which two different projects pointing at one repository still cannot
  violate.
* drone_connection_id is NOT NULL for a specific reason: SQLite treats NULLs in a
  unique index as distinct, so a nullable column in the key would silently switch
  the guard off for any row that left it empty.
* release_bundles and publish_records gain the same registry columns, nullable.
  They are for scoping and filtering rather than for a constraint, and making them
  NOT NULL would have meant rebuilding two more tables that other tables hold
  foreign keys into, for no safety gained.

On the index swap: the spec calls for creating the new index before dropping the
old one, so the table is never left unguarded.  A table rebuild reaches the same
end differently -- the whole revision runs in one transaction, so there is no
committed moment where deployments exists without a guard, and the rebuild is the
only way to add a NOT NULL column in SQLite.

Refusals before anything is written.  The migration stops, with the offending rows
named, if the registry cannot account for an existing deployment, or if the data
already contains duplicates under the new key.  Neither is something to resolve by
guessing.
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260902_0015"
down_revision: str | None = "20260902_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

ACTIVE_STATUSES = ("WAITING", "PROMOTING", "DEPLOYING", "SUCCESS")
PREDICATE = "status IN ({})".format(", ".join(f"'{status}'" for status in ACTIVE_STATUSES))

NEW_SLOT = (
    "drone_connection_id",
    "drone_owner",
    "drone_repository",
    "source_build_number",
    "target",
)
OLD_SLOT = ("component", "source_build_number", "target")

# The shape deployments must have after the rebuild.  Passed as copy_from so the
# rebuild produces exactly this rather than whatever reflection infers.
DEPLOYMENTS = sa.Table(
    "deployments",
    sa.MetaData(),
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column(
        "release_bundle_id",
        sa.String(36),
        sa.ForeignKey("release_bundles.id", ondelete="CASCADE"),
        nullable=True,
    ),
    sa.Column(
        "project_id",
        sa.String(36),
        sa.ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column(
        "component_id",
        sa.String(36),
        sa.ForeignKey("project_components.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("component", sa.String(50), nullable=False),
    sa.Column(
        "drone_connection_id",
        sa.String(36),
        sa.ForeignKey("upstream_connections.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("drone_owner", sa.String(255), nullable=False),
    sa.Column("drone_repository", sa.String(255), nullable=False),
    sa.Column("source_build_number", sa.Integer(), nullable=False),
    sa.Column("promotion_build_number", sa.Integer(), nullable=True),
    sa.Column("commit_sha", sa.String(64), nullable=False),
    sa.Column("branch", sa.String(255), nullable=False),
    sa.Column("target", sa.String(100), nullable=False),
    sa.Column("status", sa.String(30), nullable=False),
    sa.Column("publish_status", sa.String(30), nullable=False),
    sa.Column("version", sa.String(100), nullable=True),
    sa.Column("gitea_release_id", sa.String(100), nullable=True),
    sa.Column("gitea_release_tag", sa.String(100), nullable=True),
    sa.Column("gitea_release_url", sa.Text(), nullable=True),
    sa.Column("current_stage", sa.String(100), nullable=True),
    sa.Column("failed_stage", sa.String(100), nullable=True),
    sa.Column("error_code", sa.String(100), nullable=True),
    sa.Column("error_message", sa.Text(), nullable=True),
    sa.Column("cancel_reason", sa.Text(), nullable=True),
    sa.Column("poll_error_code", sa.String(100), nullable=True),
    sa.Column("poll_error_message", sa.Text(), nullable=True),
    sa.Column("poll_failure_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("requested_by", sa.String(255), nullable=True),
    sa.Column("attachment_filename", sa.String(255), nullable=True),
    sa.Column("attachment_content_type", sa.String(255), nullable=True),
    sa.Column("attachment_size", sa.Integer(), nullable=True),
    sa.Column("attachment_data", sa.LargeBinary(), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("publish_started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("publish_failed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("publish_error_code", sa.String(100), nullable=True),
    sa.Column("publish_error_message", sa.Text(), nullable=True),
)

DEPLOYMENT_INDEXES = (
    ("ix_deployments_created_at", ["created_at"]),
    ("ix_deployments_component", ["component"]),
    ("ix_deployments_status", ["status"]),
    ("ix_deployments_target", ["target"]),
    ("ix_deployments_release_bundle_id", ["release_bundle_id"]),
    ("ix_deployments_project_id", ["project_id"]),
    ("ix_deployments_component_id", ["component_id"]),
    ("ix_deployments_duplicate_lookup", list(NEW_SLOT)),
)


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def _assert_no_duplicates_under_new_key(connection) -> None:
    """Refuse rather than let the unique index fail halfway through the rebuild.

    Checked on the repository coordinates alone: every existing row is about to
    be given the connection its component resolves to, so a collision on
    (owner, repo, build, target) is a collision under the full key too.
    """
    duplicates = connection.execute(
        sa.text(
            f"""
            SELECT drone_owner, drone_repository, source_build_number, target,
                   COUNT(*) AS total
            FROM deployments
            WHERE {PREDICATE}
            GROUP BY drone_owner, drone_repository, source_build_number, target
            HAVING COUNT(*) > 1
            """
        )
    ).all()
    if duplicates:
        listing = ", ".join(
            f"{row.drone_owner}/{row.drone_repository} build#{row.source_build_number}"
            f"->{row.target} ({row.total}x)"
            for row in duplicates
        )
        raise RuntimeError(
            "Cannot rekey the promotion guard: these repositories already hold more "
            f"than one active promotion of the same build: {listing}. Resolve them "
            "deliberately (cancel the ones that did not deploy) and run the upgrade "
            "again."
        )


def _component_map(connection) -> dict[str, tuple[str, str, str]]:
    """component key -> (project_id, component_id, resolved drone connection id).

    Uses the project migration 0014 seeded, or the only project there is.  A
    deployment naming a component that no project offers is a refusal, not a
    guess: filling it in wrongly would attach real deployment history to the
    wrong repository.
    """
    used = [
        row.component
        for row in connection.execute(sa.text("SELECT DISTINCT component FROM deployments")).all()
    ]
    if not used:
        return {}

    projects = connection.execute(
        sa.text("SELECT id, key, drone_connection_id FROM projects ORDER BY key")
    ).all()
    seeded = [row for row in projects if row.key == "default"]
    candidates = seeded or projects
    if len(candidates) != 1:
        raise RuntimeError(
            "Existing deployments have to be attached to a project, and this "
            f"database has {len(projects)} of them with none keyed 'default'. "
            "Keep exactly one, or add a project with the key 'default', then run "
            "the upgrade again."
        )
    project = candidates[0]

    default_drone = connection.execute(
        sa.text("SELECT id FROM upstream_connections WHERE kind = 'drone' AND is_default = 1")
    ).scalar()
    components = connection.execute(
        sa.text(
            "SELECT id, key, drone_connection_id FROM project_components "
            "WHERE project_id = :project_id"
        ),
        {"project_id": project.id},
    ).all()
    by_key = {row.key: row for row in components}

    missing = sorted(set(used) - set(by_key))
    if missing:
        raise RuntimeError(
            f"Deployments exist for component(s) {', '.join(missing)}, which project "
            f"{project.key!r} does not have. Add them under Projects (with the same "
            "Drone repository those deployments used) and run the upgrade again."
        )

    mapping: dict[str, tuple[str, str, str]] = {}
    for key in used:
        component = by_key[key]
        connection_id = (
            component.drone_connection_id or project.drone_connection_id or default_drone
        )
        if not connection_id:
            raise RuntimeError(
                f"Component {key!r} resolves to no Drone connection, and the promotion "
                "guard cannot be keyed without one. Add a default Drone connection "
                "under Connections and run the upgrade again."
            )
        mapping[key] = (project.id, component.id, connection_id)
    return mapping


def upgrade() -> None:
    connection = op.get_bind()
    _assert_no_duplicates_under_new_key(connection)
    mapping = _component_map(connection)

    op.add_column("deployments", sa.Column("project_id", sa.String(36), nullable=True))
    op.add_column("deployments", sa.Column("component_id", sa.String(36), nullable=True))
    op.add_column("deployments", sa.Column("drone_connection_id", sa.String(36), nullable=True))
    op.add_column("release_bundles", sa.Column("project_id", sa.String(36), nullable=True))
    op.add_column("release_bundles", sa.Column("selected_component_keys", sa.Text(), nullable=True))
    op.add_column("publish_records", sa.Column("project_id", sa.String(36), nullable=True))
    op.add_column("publish_records", sa.Column("component_id", sa.String(36), nullable=True))

    for key, (project_id, component_id, drone_connection_id) in mapping.items():
        connection.execute(
            sa.text(
                """
                UPDATE deployments
                SET project_id = :project_id,
                    component_id = :component_id,
                    drone_connection_id = :drone_connection_id
                WHERE component = :key
                """
            ),
            {
                "project_id": project_id,
                "component_id": component_id,
                "drone_connection_id": drone_connection_id,
                "key": key,
            },
        )
        connection.execute(
            sa.text(
                "UPDATE publish_records SET project_id = :project_id, "
                "component_id = :component_id WHERE component = :key"
            ),
            {"project_id": project_id, "component_id": component_id, "key": key},
        )

    # A bundle belongs to the project its deployments do, and lists the
    # components it covered.  Written per bundle rather than with group_concat,
    # which does not order its values in SQLite, so the snapshot reads in the
    # order the components actually deployed in.
    bundle_rows = connection.execute(
        sa.text(
            "SELECT release_bundle_id, component, project_id FROM deployments "
            "WHERE release_bundle_id IS NOT NULL ORDER BY created_at, id"
        )
    ).all()
    grouped: dict[str, list[tuple[str, str]]] = {}
    for row in bundle_rows:
        grouped.setdefault(row.release_bundle_id, []).append((row.component, row.project_id))
    for bundle_id, entries in grouped.items():
        connection.execute(
            sa.text(
                "UPDATE release_bundles SET project_id = :project_id, "
                "selected_component_keys = :keys WHERE id = :id"
            ),
            {
                "id": bundle_id,
                "project_id": entries[0][1],
                "keys": ",".join(component for component, _project in entries),
            },
        )

    # Foreign keys off for the rebuild: dropping and recreating deployments while
    # publish_records and deployment_schedules point at it trips enforcement
    # mid-flight.  Integrity is re-checked below rather than assumed.
    if _is_sqlite():
        op.execute("PRAGMA foreign_keys = OFF")
    with op.batch_alter_table("deployments", copy_from=DEPLOYMENTS, recreate="always") as batch:
        for name, columns in DEPLOYMENT_INDEXES:
            batch.create_index(name, columns)
        batch.create_index(
            "uq_deployments_active_promotion",
            list(NEW_SLOT),
            unique=True,
            sqlite_where=sa.text(PREDICATE),
            postgresql_where=sa.text(PREDICATE),
        )
    if _is_sqlite():
        op.execute("PRAGMA foreign_keys = ON")
        violations = connection.execute(sa.text("PRAGMA foreign_key_check")).all()
        if violations:
            raise RuntimeError(
                f"Rebuilding deployments left {len(violations)} broken foreign key "
                f"reference(s): {violations[:5]}"
            )

    op.create_index("ix_release_bundles_project_id", "release_bundles", ["project_id"])
    op.create_index("ix_publish_records_project_id", "publish_records", ["project_id"])
    logger.info(
        "Promotion guard rekeyed to %s; %s component(s) mapped",
        ", ".join(NEW_SLOT),
        len(mapping),
    )


def downgrade() -> None:
    """Restores the v2.x guard.  Only safe while exactly one project exists."""
    connection = op.get_bind()
    projects = connection.execute(sa.text("SELECT COUNT(*) FROM projects")).scalar_one()
    if projects > 1:
        raise RuntimeError(
            f"Refusing to downgrade: {projects} projects exist and the old promotion "
            "guard is keyed on the component name alone, so two projects' components "
            "would be reported as duplicates of one another. Archive or remove the "
            "extra projects first."
        )

    op.drop_index("ix_publish_records_project_id", table_name="publish_records")
    op.drop_index("ix_release_bundles_project_id", table_name="release_bundles")

    if _is_sqlite():
        op.execute("PRAGMA foreign_keys = OFF")
    with op.batch_alter_table("deployments", copy_from=DEPLOYMENTS, recreate="always") as batch:
        batch.drop_column("project_id")
        batch.drop_column("component_id")
        batch.drop_column("drone_connection_id")
        batch.create_index("ix_deployments_created_at", ["created_at"])
        batch.create_index("ix_deployments_component", ["component"])
        batch.create_index("ix_deployments_status", ["status"])
        batch.create_index("ix_deployments_target", ["target"])
        batch.create_index("ix_deployments_release_bundle_id", ["release_bundle_id"])
        batch.create_index("ix_deployments_duplicate_lookup", list(OLD_SLOT))
        batch.create_index(
            "uq_deployments_active_promotion",
            list(OLD_SLOT),
            unique=True,
            sqlite_where=sa.text(PREDICATE),
            postgresql_where=sa.text(PREDICATE),
        )
    if _is_sqlite():
        op.execute("PRAGMA foreign_keys = ON")

    op.drop_column("publish_records", "component_id")
    op.drop_column("publish_records", "project_id")
    op.drop_column("release_bundles", "selected_component_keys")
    op.drop_column("release_bundles", "project_id")
