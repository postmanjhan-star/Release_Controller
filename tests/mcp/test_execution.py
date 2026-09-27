import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.db.models.mcp import ReleasePlan
from app.db.models.orchestration import Deployment
from app.integrations.drone.schemas import DroneBuild
from app.services.auth_service import CurrentUser
from app.services.drone_build_service import BuildNotPromotableError
from app.services.planned_deployment_guard import verify_planned_deployment
from app.services.release_operations_errors import MCPToolError
from app.services.release_operations_service import ReleaseOperationsService
from app.services.release_plan_service import ReleasePlanService
from tests.mcp.test_release_plan import ready_plan


def approved(operations):
    service, plan = ready_plan(operations)
    operations.settings.auth_enabled = True
    operations.settings.mcp_mode = "full"
    service.approve(plan["plan_id"], plan["digest"], CurrentUser(login="admin", is_admin=True))
    return service, plan


def test_real_orchestrator_executes_exact_approved_build(operations):
    service, plan = approved(operations)
    operations.builds.promote.return_value = DroneBuild(
        number=100, status="running", event="promote"
    )
    result = service.promote_to_production(plan["plan_id"], "release")
    bundle = operations.orchestrator.get_bundle(result["release_id"])
    assert bundle.target == "production"
    assert bundle.deployments[0].commit_sha == "a" * 40
    assert bundle.deployments[0].source_build_number == 42
    assert operations.builds.promote.call_count == 1
    operations.db.refresh(service.get(plan["plan_id"]))
    assert service.get(plan["plan_id"]).release_id == bundle.id


def test_source_change_between_validation_and_orchestrator_is_rejected(operations):
    service, plan = approved(operations)
    operations.builds.validate_promotable.side_effect = lambda *args: DroneBuild(
        number=42, status="success", event="push", commit_sha="different"
    )
    with pytest.raises(BuildNotPromotableError):
        service.promote_to_production(plan["plan_id"], "release")
    assert service.get(plan["plan_id"]).status == "execution_failed"
    operations.builds.promote.assert_not_called()


def test_worker_rechecks_approved_routes(operations):
    service, plan = approved(operations)
    operations.orchestrator._advance = Mock()
    result = service.promote_to_production(plan["plan_id"], "release")
    deployment = operations.db.scalar(
        select(Deployment).where(Deployment.release_bundle_id == result["release_id"])
    )
    component = operations.component("frontend")
    component.drone_repo = "redirected"
    operations.db.commit()
    with pytest.raises(MCPToolError):
        verify_planned_deployment(
            operations.db, operations.settings, operations.builds, deployment, component
        )
    operations.orchestrator.deployments.promote(deployment)
    assert deployment.status == "FAILED"
    assert deployment.error_code == "APPROVED_PLAN_CHANGED"
    operations.builds.promote.assert_not_called()


def test_simultaneous_execution_claims_approval_only_once(client, operations):
    service, plan = approved(operations)
    operations.db.rollback()
    barrier = threading.Barrier(2)
    executed = []

    def run():
        with client.session_factory() as db:
            ops = ReleaseOperationsService(db, operations.builds, operations.settings)
            plans = ReleasePlanService(ops)
            original = plans.validate_current

            def validate(row):
                original(row)
                barrier.wait(timeout=10)

            plans.validate_current = validate

            def execute(snapshot, release_id=None):
                executed.append(release_id)
                return {"release_id": release_id}

            plans.execute = execute
            try:
                plans.promote_to_production(plan["plan_id"], "release")
                return "executed"
            except MCPToolError:
                return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: run(), range(2)))
    assert sorted(outcomes) == ["denied", "executed"]
    assert len(executed) == 1
    with client.session_factory() as db:
        assert db.get(ReleasePlan, plan["plan_id"]).status == "executed"
