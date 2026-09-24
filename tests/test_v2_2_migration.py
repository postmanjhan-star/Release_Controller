from datetime import datetime, timezone

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.core.config import get_settings


def test_v1_data_is_preserved_during_direct_v2_2_upgrade(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "upgrade.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config("alembic.ini")

    try:
        command.upgrade(config, "20260820_0002")
        engine = create_engine(database_url)
        now = datetime.now(timezone.utc)
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO releases (
                        id, repository, branch, commit_sha, environment,
                        status, created_at, updated_at, message
                    ) VALUES (
                        :id, :repository, :branch, :commit_sha, :environment,
                        :status, :created_at, :updated_at, :message
                    )
                    """
                ),
                {
                    "id": "00000000-0000-0000-0000-000000000022",
                    "repository": "preserved-v1-repository",
                    "branch": "main",
                    "commit_sha": "2" * 40,
                    "environment": "pre-production",
                    "status": "PENDING",
                    "created_at": now,
                    "updated_at": now,
                    "message": "must survive v2.2 migration",
                },
            )

        command.upgrade(config, "head")
        tables = set(inspect(engine).get_table_names())
        assert {
            "releases",
            "release_workflows",
            "release_bundles",
            "deployments",
            "workflow_events",
            "deployment_schedules",
            "email_outbox",
            "email_notification_cursors",
            "notification_recipients",
            "publish_records",
        }.issubset(tables)
        bundle_columns = {
            column["name"] for column in inspect(engine).get_columns("release_bundles")
        }
        deployment_columns = {
            column["name"] for column in inspect(engine).get_columns("deployments")
        }
        assert {"deployment_status", "publish_status", "version"}.issubset(bundle_columns)
        assert {
            "publish_status",
            "version",
            "gitea_release_id",
            "gitea_release_tag",
            "gitea_release_url",
        }.issubset(deployment_columns)
        schedule_columns = {
            column["name"] for column in inspect(engine).get_columns("deployment_schedules")
        }
        assert {"release_version", "release_notes"}.issubset(schedule_columns)
        outbox_columns = {column["name"] for column in inspect(engine).get_columns("email_outbox")}
        assert "schedule_id" in outbox_columns
        with engine.connect() as connection:
            preserved = connection.execute(
                text("SELECT repository, message FROM releases WHERE id = :id"),
                {"id": "00000000-0000-0000-0000-000000000022"},
            ).one()
        assert preserved == ("preserved-v1-repository", "must survive v2.2 migration")
        engine.dispose()
    finally:
        get_settings.cache_clear()


def test_v2_3_upgrade_preserves_successful_deployments_as_not_published(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "v22-to-v23.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config("alembic.ini")

    try:
        command.upgrade(config, "20260827_0007")
        engine = create_engine(database_url)
        now = datetime.now(timezone.utc)
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO release_bundles (
                        id, mode, target, status, created_at, updated_at
                    ) VALUES (:id, 'BUNDLE', 'production', 'SUCCESS', :now, :now)
                    """
                ),
                {"id": "00000000-0000-0000-0000-000000000023", "now": now},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO deployments (
                        id, release_bundle_id, component, drone_owner,
                        drone_repository, source_build_number, commit_sha,
                        branch, target, status, created_at, updated_at
                    ) VALUES (
                        :id, :bundle_id, 'backend', '102573', 'soda', 456,
                        :commit_sha, 'main', 'production', 'SUCCESS', :now, :now
                    )
                    """
                ),
                {
                    "id": "00000000-0000-0000-0000-000000000024",
                    "bundle_id": "00000000-0000-0000-0000-000000000023",
                    "commit_sha": "b" * 40,
                    "now": now,
                },
            )

        command.upgrade(config, "head")
        with engine.connect() as connection:
            bundle = connection.execute(
                text(
                    "SELECT status, deployment_status, publish_status "
                    "FROM release_bundles WHERE id = :id"
                ),
                {"id": "00000000-0000-0000-0000-000000000023"},
            ).one()
            deployment = connection.execute(
                text("SELECT status, publish_status FROM deployments WHERE id = :id"),
                {"id": "00000000-0000-0000-0000-000000000024"},
            ).one()
        assert bundle == ("SUCCESS", "SUCCESS", "NOT_PUBLISHED")
        assert deployment == ("SUCCESS", "NOT_PUBLISHED")
        engine.dispose()
    finally:
        get_settings.cache_clear()
