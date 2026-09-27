from unittest.mock import Mock

import pytest
from sqlalchemy import func, select

from app.db.models.mcp import ReleasePlan
from app.db.models.orchestration import ReleaseBundle
from app.services.auth_service import CurrentUser
from app.services.release_operations_errors import MCPToolError
from app.services.release_plan_service import ReleasePlanService
from tests.mcp.conftest import seed_deployment


def ready_plan(operations):
    seed_deployment(operations)
    service = ReleasePlanService(operations)
    return service, service.create_release_plan(frontend=True, backend=False)


def test_plan_never_deploys(operations):
    service, plan = ready_plan(operations)
    assert plan["executable"]
    assert plan["requires_approval"]
    assert plan["approval_status"] == "pending"
    operations.builds.promote.assert_not_called()
    assert operations.db.scalar(select(func.count()).select_from(ReleaseBundle)) == 0
    assert operations.db.scalar(select(func.count()).select_from(ReleasePlan)) == 1
    assert service.get_release_candidates()["frontend"]["currently_in_staging"]


def test_plan_reports_blockers(operations):
    service = ReleasePlanService(operations)
    plan = service.create_release_plan()
    assert not plan["executable"]
    assert plan["blockers"]
    component = operations.component("frontend")
    component.promote_target_override = "production"
    operations.db.commit()
    plan = service.create_release_plan("staging", True, False)
    assert not plan["executable"]
    assert any("override" in b for b in plan["blockers"])


def test_staging_reuses_orchestrator_and_blocks_override(operations):
    operations.settings.mcp_mode = "staging"
    operations.orchestrator.create_bundle = Mock(
        return_value=Mock(id="deployment", status="PENDING")
    )
    service = ReleasePlanService(operations)
    result = service.deploy_staging(True, False, "testing")
    assert result["environment"] == "staging"
    kwargs = operations.orchestrator.create_bundle.call_args.kwargs
    assert kwargs["required_target"] == "staging"
    operations.component("frontend").promote_target_override = "production"
    with pytest.raises(MCPToolError):
        service.deploy_staging(True, False, "testing")
    assert operations.orchestrator.create_bundle.call_count == 1


def test_full_mode_requires_human_approval_and_consumes_once(operations):
    operations.settings.mcp_mode = "full"
    service, plan = ready_plan(operations)
    with pytest.raises(MCPToolError):
        service.promote_to_production(plan["plan_id"], "release")
    with pytest.raises(MCPToolError):
        service.approve(plan["plan_id"], plan["digest"], CurrentUser(login="human", is_admin=True))
    operations.settings.auth_enabled = True
    with pytest.raises(MCPToolError):
        service.approve(plan["plan_id"], plan["digest"], CurrentUser(login="viewer"))
    service.approve(plan["plan_id"], plan["digest"], CurrentUser(login="admin", is_admin=True))
    operations.orchestrator.create_bundle = Mock(return_value=Mock(id="result", status="PENDING"))
    result = service.promote_to_production(plan["plan_id"], "release")
    assert result["plan_id"] == plan["plan_id"]
    with pytest.raises(MCPToolError):
        service.promote_to_production(plan["plan_id"], "retry")
    assert operations.orchestrator.create_bundle.call_count == 1


@pytest.mark.parametrize("change", ["commit", "repository", "expired", "digest"])
def test_stale_or_tampered_plan_is_not_approved(operations, change):
    from datetime import timedelta

    from app.domain.shared.time import utc_now

    service, result = ready_plan(operations)
    operations.settings.auth_enabled = True
    plan = service.get(result["plan_id"])
    if change == "repository":
        operations.component("frontend").drone_repo = "different"
    elif change == "expired":
        plan.expires_at = utc_now() - timedelta(seconds=1)
    elif change == "digest":
        plan.digest = "forged"
    else:
        operations.builds.get_build.side_effect = lambda *args: Mock(
            commit_sha="different", is_promotable=lambda: True
        )
    operations.db.commit()
    with pytest.raises(MCPToolError):
        service.approve(plan.id, plan.digest, CurrentUser(login="admin", is_admin=True))
    operations.builds.promote.assert_not_called()
