import os
from collections.abc import Generator

TEST_ENVIRONMENT = {
    "APP_ENV": "test",
    "BACKGROUND_WORKER_ENABLED": "false",
    "AUTH_ENABLED": "false",
    "DRONE_FRONTEND_REPO_OWNER": "102573",
    "DRONE_FRONTEND_REPO_NAME": "SMT-Assistant",
    "DRONE_BACKEND_REPO_OWNER": "102573",
    "DRONE_BACKEND_REPO_NAME": "soda",
    "DRONE_DEFAULT_TARGET": "production",
    "GITEA_FRONTEND_REPO_OWNER": "102573",
    "GITEA_FRONTEND_REPO_NAME": "SMT-Assistant",
    "GITEA_BACKEND_REPO_OWNER": "102573",
    "GITEA_BACKEND_REPO_NAME": "soda",
    # Fixed so encrypted connection tokens round-trip within a test run and
    # nothing falls back to the development key.
    "APP_SECRET_KEY": "test-secret-key-for-connection-tokens",
}

# Applied before the app is imported, not only in the fixture below.  Settings()
# refuses to build under APP_ENV=production without APP_SECRET_KEY -- deliberately,
# because that key encrypts the stored upstream tokens -- and app.db.session reads
# the settings at import time.  Without this the suite would only run in a shell
# that already had the variables exported, which a clean CI container does not.
# ruff: noqa: E402
os.environ.update(TEST_ENVIRONMENT)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.crypto import token_cipher, token_hint
from app.db.base import Base
from app.db.models.registry import Project, ProjectComponent, UpstreamConnection
from app.db.session import create_database_engine, get_db
from app.main import app


@pytest.fixture(autouse=True)
def isolated_test_settings(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Re-assert the test environment per test, so one that edits it cannot leak."""
    for name, value in TEST_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client(tmp_path) -> Generator[TestClient, None, None]:
    database_path = tmp_path / "test.db"
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    Base.metadata.create_all(bind=engine)
    with testing_session() as setup:
        seed_default_project(setup)

    def override_get_db() -> Generator[Session, None, None]:
        db = testing_session()
        try:
            yield db
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        # Exposed so a test can reach the same database outside a request.
        test_client.session_factory = testing_session
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def seed_default_project(db: Session) -> Project:
    """The registry state migration 20260902_0014 produces from the test environment.

    Base.metadata.create_all builds empty tables, but the deployment path reads its
    repositories from project_components now, so every test needs the same rows a
    real installation gets from the migration -- otherwise each one would be
    exercising an unconfigured controller.
    """
    settings = get_settings()
    cipher = token_cipher(settings)
    # Read from the same variables migration 20260902_0014 seeds from, so the
    # fixture and a real first upgrade produce the same two components.
    env = TEST_ENVIRONMENT
    drone = UpstreamConnection(
        kind="drone",
        name="default-drone",
        base_url=settings.drone_server.rstrip("/"),
        token_encrypted=cipher.encrypt(settings.drone_token or "test-token"),
        token_hint=token_hint(settings.drone_token or "test-token"),
        is_default=True,
    )
    gitea = UpstreamConnection(
        kind="gitea",
        name="default-gitea",
        base_url=settings.gitea_server.rstrip("/"),
        token_encrypted=cipher.encrypt(settings.gitea_token or "test-token"),
        token_hint=token_hint(settings.gitea_token or "test-token"),
        is_default=True,
    )
    db.add_all([drone, gitea])
    db.flush()
    project = Project(
        key="default",
        name="Default project",
        default_target=env["DRONE_DEFAULT_TARGET"],
        drone_connection_id=drone.id,
        gitea_connection_id=gitea.id,
    )
    db.add(project)
    db.flush()
    # Backend first, matching the order ReleaseOrchestrator promotes in.
    project.components.append(
        ProjectComponent(
            key="backend",
            display_name="Backend",
            position=1,
            drone_owner=env["DRONE_BACKEND_REPO_OWNER"],
            drone_repo=env["DRONE_BACKEND_REPO_NAME"],
            gitea_owner=env["GITEA_BACKEND_REPO_OWNER"],
            gitea_repo=env["GITEA_BACKEND_REPO_NAME"],
        )
    )
    project.components.append(
        ProjectComponent(
            key="frontend",
            display_name="Frontend",
            position=2,
            drone_owner=env["DRONE_FRONTEND_REPO_OWNER"],
            drone_repo=env["DRONE_FRONTEND_REPO_NAME"],
            gitea_owner=env["GITEA_FRONTEND_REPO_OWNER"],
            gitea_repo=env["GITEA_FRONTEND_REPO_NAME"],
        )
    )
    db.commit()
    return project


@pytest.fixture
def bare_registry(client: TestClient) -> TestClient:
    """A controller with nothing configured yet.

    Every other test wants the seeded project, because that is what a real
    installation has after migration 0014. These few are about what happens
    before anyone has configured anything.
    """
    with client.session_factory() as db:
        db.execute(delete(ProjectComponent))
        db.execute(delete(Project))
        db.execute(delete(UpstreamConnection))
        db.commit()
    return client


def registered_component(db: Session, key: str) -> ProjectComponent:
    """The seeded component row for a key, for tests that build deployments by hand."""
    return db.scalars(
        select(ProjectComponent)
        .join(Project, Project.id == ProjectComponent.project_id)
        .where(Project.key == "default", ProjectComponent.key == key)
    ).one()


@pytest.fixture
def release_payload() -> dict[str, str]:
    return {
        "repository": "smt-assistant-backend",
        "branch": "pre-production",
        "commit_sha": "7c1fe1ac1d4ef9b5c46279f39db965a8c73389ae",
        "environment": "pre-production",
        "message": "Drone build passed",
    }


@pytest.fixture
def created_release(client: TestClient, release_payload: dict[str, str]) -> dict:
    response = client.post("/api/v1/releases", json=release_payload)
    assert response.status_code == 201
    return response.json()
