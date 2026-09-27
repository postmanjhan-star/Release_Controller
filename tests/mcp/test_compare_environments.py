from tests.mcp.conftest import seed_deployment


def test_comparison_uses_commit_not_tag_or_ancestry_guess(operations):
    a = seed_deployment(operations)
    a.gitea_release_tag = "v1"
    b = seed_deployment(operations, environment="production")
    b.gitea_release_tag = "different-tag-same-commit"
    operations.db.commit()
    result = operations.compare_environments()
    assert result["frontend"]["same"] is True
    assert result["backend"]["same"] is None
    assert result["production_behind"] is None
