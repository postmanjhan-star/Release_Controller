import pytest

from app.db.models.orchestration import ReleaseBundle
from app.services.release_operations_errors import MCPToolError


def test_scoped_history_filters_and_pagination(operations):
    for target in ["staging", "production"]:
        operations.db.add(
            ReleaseBundle(
                project_id=operations.project().id,
                mode="BUNDLE",
                target=target,
                status="SUCCESS",
                deployment_status="SUCCESS",
            )
        )
    operations.db.commit()
    result = operations.list_releases(environment="staging", status="SUCCESS", limit=1)
    assert result["total"] == 1
    assert len(result["items"]) == 1
    release_id = result["items"][0]["id"]
    assert operations.get_release(release_id)["environment"] == "staging"
    assert operations.list_releases(offset=2)["items"] == []
    row = operations.orchestrator.get_bundle(release_id)
    row.project_id = None
    operations.db.commit()
    with pytest.raises(MCPToolError):
        operations.get_release(release_id)
