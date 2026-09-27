from tests.mcp.conftest import seed_deployment


def test_status_does_not_invent_health(operations):
    empty = operations.get_environment_status("staging")
    assert empty["healthy"] is None
    assert empty["frontend"]["version"] is None
    deployment = seed_deployment(operations)
    deployment.gitea_release_tag = "v1.2.3"
    operations.db.commit()
    result = operations.get_release_status()
    assert result["frontend"]["staging_version"] == "v1.2.3"
    assert result["frontend"]["latest_commit"] == "a" * 40
    assert result["backend"]["production_version"] is None


def test_repository_and_pipeline(operations):
    assert operations.get_repository_state("backend")["default_branch"] == "main"
    assert operations.get_pipeline_status("backend", 42)["commit"] == "a" * 40
