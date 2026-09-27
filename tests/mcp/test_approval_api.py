from sqlalchemy import select

from app.core.config import get_settings
from app.db.models.mcp import MCPToolInvocation
from app.infrastructure.di.injection import get_build_service, require_user
from app.main import app
from app.services.auth_service import CurrentUser
from tests.mcp.test_release_plan import ready_plan


def test_human_review_approval_digest_and_audit(client, operations, monkeypatch):
    service, plan = ready_plan(operations)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    get_settings.cache_clear()
    app.dependency_overrides[get_build_service] = lambda: operations.builds
    app.dependency_overrides[require_user] = lambda: CurrentUser(login="reviewer", is_admin=True)
    url = f"/api/v1/release-plans/{plan['plan_id']}"
    review = client.get(url)
    assert review.status_code == 200
    assert review.json()["actions"][0]["commit_sha"] == "a" * 40
    assert client.post(url + "/approve").status_code == 422
    assert (
        client.post(url + "/approve", headers={"X-Release-Plan-Digest": "0" * 64}).status_code
        == 409
    )
    response = client.post(url + "/approve", headers={"X-Release-Plan-Digest": plan["digest"]})
    assert response.status_code == 200
    assert response.json()["approved_by"] == "reviewer"
    assert (
        client.post(url + "/approve", headers={"X-Release-Plan-Digest": plan["digest"]}).status_code
        == 409
    )
    with client.session_factory() as db:
        rows = list(db.scalars(select(MCPToolInvocation)))
        assert len(rows) == 6
        assert all(row.actor == "reviewer" for row in rows)
        assert {r.result_status for r in rows} == {"started", "success", "CONFLICT"}


def test_agent_bearer_cannot_approve(client, operations, monkeypatch):
    service, plan = ready_plan(operations)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    get_settings.cache_clear()
    app.dependency_overrides[get_build_service] = lambda: operations.builds
    url = f"/api/v1/release-plans/{plan['plan_id']}/approve"
    response = client.post(
        url,
        headers={"Authorization": "Bearer agent-token", "X-Release-Plan-Digest": plan["digest"]},
    )
    assert response.status_code == 401
    app.dependency_overrides[require_user] = lambda: CurrentUser(login="viewer", is_admin=False)
    assert client.post(url, headers={"X-Release-Plan-Digest": plan["digest"]}).status_code == 403
