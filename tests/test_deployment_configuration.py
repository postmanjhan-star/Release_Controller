"""Contracts for the current Drone -> deploy branch -> Portainer deployment."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _pipeline(name: str) -> str:
    documents = re.split(r"(?m)^---\s*$", (ROOT / ".drone.yml").read_text(encoding="utf-8"))
    matches = [document for document in documents if f"\nname: {name}\n" in document]
    assert len(matches) == 1, f"Expected exactly one {name!r} pipeline"
    # Comments describing the retired deployment must not satisfy a contract.
    return re.sub(r"(?m)^\s*#[^\n]*\n", "", matches[0])


def _step(pipeline: str, name: str) -> str:
    steps = re.split(r"(?m)^  - name: ", pipeline.split("\ntrigger:")[0])[1:]
    matches = [step for step in steps if step.startswith(f"{name}\n")]
    assert len(matches) == 1, f"Expected exactly one {name!r} step"
    return matches[0]


def test_drone_pipeline_gates_main_deployment_on_quality() -> None:
    quality = _pipeline("quality")
    deploy = _pipeline("deploy")

    assert "type: docker\n" in quality
    assert "type: docker\n" in deploy
    assert "depends_on:\n  - quality" in deploy
    assert "concurrency:\n  limit: 1" in deploy
    assert "trigger:\n  branch:\n    - main\n  event:\n    - push" in deploy
    # Promoting deploy must not recursively trigger another quality/deploy run.
    assert "branch:\n    exclude:\n      - deploy" in quality
    assert "- push\n    - pull_request" in quality
    assert "npm run quality" in _step(quality, "frontend-quality")
    python = _step(quality, "python-quality")
    assert "depends_on:\n      - frontend-quality" in python
    assert "python -m pytest --cov=app" in python
    assert "depends_on:\n      - python-quality" in _step(quality, "browser-e2e")
    assert "appleboy/drone-ssh" not in deploy
    assert "bash ./scripts/deploy-release-controller.sh" not in deploy
    assert not (ROOT / ".gitea/workflows/quality.yml").exists()


@pytest.mark.parametrize(
    ("name", "dependency"),
    [
        ("promote-deploy-ref", "release-preflight"),
        ("portainer-redeploy", "promote-deploy-ref"),
        ("verify-deployment", "portainer-redeploy"),
        ("tag-release", "verify-deployment"),
    ],
)
def test_deployment_steps_wait_for_success(name: str, dependency: str) -> None:
    step = _step(_pipeline("deploy"), name)
    assert f"depends_on:\n      - {dependency}\n" in step
    assert "failure: ignore" not in step
    assert "- failure" not in step


def test_release_preflight_rejects_missing_or_reused_version() -> None:
    preflight = _step(_pipeline("deploy"), "release-preflight")
    assert "git fetch --tags origin" in preflight
    assert 'VERSION="$(sed -n' in preflight
    assert "pyproject.toml" in preflight
    assert 'if [ -z "$VERSION" ]; then' in preflight
    assert 'RELEASE_TAG="v$VERSION"' in preflight
    assert 'git rev-parse -q --verify "refs/tags/$RELEASE_TAG^{commit}"' in preflight
    assert '[ "$EXISTING_TAG_COMMIT" != "${DRONE_COMMIT_SHA}" ]' in preflight
    assert "already points to another commit" in preflight
    assert preflight.count("exit 1") == 2
    assert preflight.index("already points to another commit") < preflight.index("> .release-tag")


def test_deploy_promotes_tested_commit_and_uses_portainer_secrets() -> None:
    deploy = _pipeline("deploy")
    promote = _step(deploy, "promote-deploy-ref")
    assert '"${DRONE_COMMIT_SHA}:refs/heads/deploy"' in promote
    assert "git push" in promote
    for name in ("release-preflight", "promote-deploy-ref", "tag-release"):
        step = _step(deploy, name)
        assert "from_secret: gitea_deploy_user" in step
        assert "from_secret: gitea_deploy_token" in step
        assert 'export GIT_ASKPASS="$PWD/.drone-git-askpass.sh"' in step
        assert "export GIT_TERMINAL_PROMPT=0" in step

    redeploy = _step(deploy, "portainer-redeploy")
    for secret in (
        "portainer_url",
        "portainer_api_token",
        "portainer_stack_id",
        "portainer_endpoint_id",
    ):
        assert f"from_secret: {secret}" in redeploy
    assert "curl -kfsS" in redeploy
    assert "-X PUT" in redeploy
    assert '-H "X-API-Key: $PORTAINER_API_TOKEN"' in redeploy
    assert (
        "$PORTAINER_URL/api/stacks/$PORTAINER_STACK_ID/git/redeploy"
        "?endpointId=$PORTAINER_ENDPOINT_ID"
    ) in redeploy


def test_health_check_must_succeed_before_release_tagging() -> None:
    deploy = _pipeline("deploy")
    health = _step(deploy, "verify-deployment")
    assert "for i in $(seq 1 45); do" in health
    assert "if curl -fsS" in health
    assert "--max-time 5" in health
    assert "/health; then" in health
    assert "exit 0" in health
    assert health.rstrip().endswith("exit 1")

    tag = _step(deploy, "tag-release")
    assert 'RELEASE_TAG="$(cat .release-tag)"' in tag
    assert '[ "$EXISTING_TAG_COMMIT" = "${DRONE_COMMIT_SHA}" ]' in tag
    assert "unexpectedly points to another commit" in tag
    assert tag.index("exit 1") < tag.index('git tag "$RELEASE_TAG"')
    assert 'git tag "$RELEASE_TAG" "${DRONE_COMMIT_SHA}"' in tag
    assert "git push" in tag
    assert '"refs/tags/$RELEASE_TAG"' in tag


def test_portainer_compose_preserves_runtime_configuration() -> None:
    compose = (ROOT / "compose.portainer.yml").read_text(encoding="utf-8")
    assert "dockerfile: Containerfile" in compose
    assert (ROOT / "Containerfile").is_file()
    assert "${HOST_IP:-127.0.0.1}:${HOST_PORT:-3100}:8000" in compose
    assert "${DATA_DIR:-./data}:/data" in compose
    assert "env_file:\n      - stack.env" in compose
    assert "DATABASE_URL: sqlite:////data/release.db" in compose
    assert "healthcheck:" in compose
    assert "http://127.0.0.1:8000/health" in compose


def test_tag_pipeline_publishes_release_controller_with_official_plugin() -> None:
    release = _pipeline("release")
    assert "image: plugins/gitea-release:1" in release
    assert "from_secret: gitea_release_token" in release
    assert "base_url:\n        from_secret: gitea_server" in release
    assert "ref:\n    - refs/tags/v*" in release
    assert "event:\n    - tag" in release
