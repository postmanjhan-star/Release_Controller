import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name == "nt", reason="Deployment behavior checks run on Linux CI")
@pytest.mark.parametrize(
    ("file_host", "shell_host", "expected_host"),
    [
        ("127.0.0.2", None, "127.0.0.2"),
        ("127.0.0.2", "127.0.0.3", "127.0.0.3"),
        (None, "127.0.0.3", "127.0.0.3"),
        (None, None, None),
    ],
)
def test_deploy_bind_address_configuration(
    tmp_path: Path, file_host: str | None, shell_host: str | None, expected_host: str | None
) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Bash is required for the deployment script behavior check")

    script = Path("scripts/deploy-release-controller.sh").read_text(encoding="utf-8")
    env_file = tmp_path / "runtime.env"
    required = (
        "DRONE_SERVER",
        "DRONE_TOKEN",
        "GITEA_SERVER",
        "GITEA_TOKEN",
        "APP_SECRET_KEY",
        "AUTH_ENABLED",
        "GITEA_OAUTH_CLIENT_ID",
        "GITEA_OAUTH_CLIENT_SECRET",
        "GITEA_OAUTH_CALLBACK_URL",
        "AUTH_COOKIE_SECURE",
    )
    lines = [f"{key}=test-value" for key in required]
    if file_host is not None:
        lines.append(f"HOST_IP={file_host}")
    # A secrets file is data, even if one value contains shell syntax.
    lines.append("UNRELATED=$(touch env-was-executed)")
    env_file.write_bytes(("\r\n".join(lines) + "\r\n").encode())
    (tmp_path / "Containerfile").touch()
    environment = os.environ.copy()
    environment.pop("HOST_IP", None)
    environment.update(
        ENV_FILE=env_file.as_posix(),
        APP_DIR=tmp_path.as_posix(),
        DATA_DIR=(tmp_path / "data").as_posix(),
        HOST_PORT="3100",
        CONTAINER_PORT="8000",
    )
    if shell_host is not None:
        environment["HOST_IP"] = shell_host

    # Stop at the build boundary: exercise the actual script without running
    # Podman, touching a real service, or waiting for health checks.
    mock_podman = """
podman() {
  case "$1" in
    --version) printf 'podman-test\n' ;;
    network) return 0 ;;
    image) return 1 ;;
    build) return 97 ;;
    *) return 98 ;;
  esac
}
"""
    result = subprocess.run(
        [bash, "-s"],
        input=mock_podman + script,
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert not (tmp_path / "env-was-executed").exists()
    if expected_host is None:
        assert result.returncode == 1, result.stdout + result.stderr
        assert "Set HOST_IP" in result.stderr
        assert not (tmp_path / "data").exists()
    else:
        assert result.returncode == 97, result.stdout + result.stderr
        assert f"Port: {expected_host}:3100 -> 8000" in result.stdout


def test_deploy_script_preflights_gitea_and_passes_env_file() -> None:
    script = Path("scripts/deploy-release-controller.sh").read_text(encoding="utf-8")

    for variable in (
        "GITEA_SERVER",
        "GITEA_TOKEN",
        # Production refuses to start without it, so the deploy has to stop here
        # rather than start a container that exits.
        "APP_SECRET_KEY",
    ):
        assert variable in script
    # Repositories are project_components rows from v3.0; requiring these would
    # block a deploy on configuration the application no longer reads.
    for retired in (
        "DRONE_FRONTEND_REPO_OWNER",
        "GITEA_BACKEND_REPO_NAME",
        "DRONE_DEFAULT_TARGET",
    ):
        assert f"\n  {retired}\n" not in script
    assert '--env-file "$ENV_FILE"' in script
    assert 'chmod 600 "$ENV_FILE"' in script
    assert 'echo "$GITEA_TOKEN"' not in script
    assert 'printf "$GITEA_TOKEN"' not in script


def test_drone_pipeline_gates_main_deployment_on_quality() -> None:
    pipeline = Path(".drone.yml").read_text(encoding="utf-8")

    assert "type: docker\nname: quality" in pipeline
    assert "type: docker\nname: deploy" in pipeline
    assert "depends_on:\n  - quality" in pipeline
    assert "image: appleboy/drone-ssh:1.8.0" in pipeline
    assert "from_secret: production_host" in pipeline
    assert "from_secret: production_user" in pipeline
    assert "from_secret: production_ssh_key" in pipeline
    assert 'cd "$HOME/services/release-controller-src"' in pipeline
    assert "git fetch --prune --tags origin" in pipeline
    assert 'git checkout --detach "${DRONE_COMMIT_SHA}"' in pipeline
    deploy_command = 'bash ./scripts/deploy-release-controller.sh "$PWD"'
    assert deploy_command in pipeline
    assert pipeline.index('git checkout --detach "${DRONE_COMMIT_SHA}"') < pipeline.index(
        deploy_command
    )
    assert "deploy-release-controller-v2.2.sh" not in pipeline
    assert "script_stop: true" in pipeline
    assert 'VERSION="$(sed -n' in pipeline
    assert 'test -n "$${VERSION}"' in pipeline
    assert 'RELEASE_TAG="v$${VERSION}"' in pipeline
    # Reject a reused version before replacing the production container, but
    # publish a new tag only after the deployment health check succeeds.
    assert pipeline.index('test -n "$${VERSION}"') < pipeline.index(deploy_command)
    assert pipeline.index("already points to another commit") < pipeline.index(deploy_command)
    assert pipeline.index(deploy_command) < pipeline.index('git tag "$${RELEASE_TAG}"')
    assert 'git push origin "refs/tags/$${RELEASE_TAG}"' in pipeline
    assert "branch:\n    - main" in pipeline
    assert "type: exec" not in pipeline
    assert "release_controller: production" not in pipeline
    assert not Path(".gitea/workflows/quality.yml").exists()


def test_tag_pipeline_publishes_release_controller_with_official_plugin() -> None:
    pipeline = Path(".drone.yml").read_text(encoding="utf-8")

    assert "name: release" in pipeline
    assert "image: plugins/gitea-release:1" in pipeline
    assert "from_secret: gitea_release_token" in pipeline
    assert "base_url:\n        from_secret: gitea_server" in pipeline
    assert "ref:\n    - refs/tags/v*" in pipeline
    assert "event:\n    - tag" in pipeline
