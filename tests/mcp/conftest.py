from unittest.mock import Mock

import pytest

from app.core.config import Settings
from app.integrations.drone.schemas import DroneBuild, DroneRepositoryInfo
from app.services.drone_build_service import DroneBuildService
from app.services.release_operations_service import ReleaseOperationsService


@pytest.fixture
def operations(client):
    settings = Settings(_env_file=None, mcp_enabled=True)
    builds = Mock(spec=DroneBuildService)
    builds.get_drone_repo.side_effect = DroneBuildService.get_drone_repo
    builds.list_builds.return_value = [
        DroneBuild(number=42, status="success", event="push", commit_sha="a" * 40, branch="main")
    ]
    builds.get_build.side_effect = lambda component, number: DroneBuild(
        number=number, status="success", event="push", commit_sha="a" * 40, branch="main"
    )
    builds.validate_promotable.side_effect = builds.get_build.side_effect
    builds.get_repository_info.return_value = DroneRepositoryInfo(
        owner="team", name="app", slug="team/app", default_branch="main"
    )
    with client.session_factory() as db:
        yield ReleaseOperationsService(db, builds, settings)


def seed_deployment(operations, key="frontend", environment="staging", status="SUCCESS", sha=None):
    component = operations.component(key)
    build = DroneBuild(
        number=42, status="success", event="push", commit_sha=sha or "a" * 40, branch="main"
    )
    deployment = operations.orchestrator.deployments.create(
        component, build, operations.target(environment), requested_by="human"
    )
    deployment.status = status
    operations.db.commit()
    return deployment
