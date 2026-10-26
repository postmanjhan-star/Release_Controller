"""Exercise the shipped migration against a real pre-CI database."""

from datetime import timedelta

import pytest
from alembic.config import Config
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import get_settings
from app.db.models.ci import CIDeploymentRequest, CIPolicy, CIServiceToken
from app.db.models.registry import Project, ProjectComponent
from app.db.session import create_database_engine
from app.domain.shared.time import utc_now
from app.services.ci_tokens import create_token
from tests.conftest import registered_component, seed_default_project


def test_ci_upgrade_preserves_data_constraints_and_downgrade(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'ci-migration.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    config = Config("alembic.ini")
    command.upgrade(config, "20260926_0017")
    engine = create_database_engine(url)
    with Session(engine) as db:
        # Earlier migration can seed registry from the test environment.
        if not db.scalar(select(Project)):
            seed_default_project(db)
        project_id = db.scalar(select(Project.id))
    command.upgrade(config, "head")
    inspector = inspect(engine)
    assert {
        "ci_service_tokens",
        "ci_policies",
        "ci_deployment_requests",
        "ci_deployment_events",
    } <= set(inspector.get_table_names())
    assert "required_promotion_stages" in {c["name"] for c in inspector.get_columns("deployments")}
    with Session(engine) as db:
        assert db.get(Project, project_id)
        component = registered_component(db, "backend")
        token, raw = create_token(
            db,
            name="migration-test",
            project_id=component.project_id,
            component_id=component.id,
            target="production",
            branch="main",
            expires_at=utc_now() + timedelta(days=365),
        )
        db.add(
            CIPolicy(
                component_id=component.id,
                target="production",
                branch="main",
                source_stages=["ci"],
                promotion_stages=["deploy"],
            )
        )
        db.commit()
        scope = dict(
            service_token_id=token.id,
            project_id=component.project_id,
            component_id=component.id,
            source_build_number=123,
            target="production",
            branch="main",
            drone_connection_id=component.project.drone_connection_id,
            drone_owner=component.drone_owner,
            drone_repository=component.drone_repo,
            source_stages=["ci"],
            promotion_stages=["deploy"],
            deadline_at=utc_now(),
        )
        db.add(CIDeploymentRequest(**scope))
        db.commit()
        assert db.scalar(select(CIServiceToken.token_hash)) != raw
        db.add(CIDeploymentRequest(**scope))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        # Hard foreign keys prevent deleting a credential's scoped resource.
        db.delete(db.get(ProjectComponent, component.id))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    command.downgrade(config, "20260926_0017")
    assert "ci_service_tokens" not in inspect(engine).get_table_names()
    with Session(engine) as db:
        assert db.get(Project, project_id)
    command.upgrade(config, "head")
    engine.dispose()
    get_settings.cache_clear()
