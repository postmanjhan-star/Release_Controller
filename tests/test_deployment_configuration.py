"""Contracts for the current Drone -> deploy branch -> Portainer deployment."""

import os
import re
import subprocess
import textwrap
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
    assert "trigger:\n  branch:\n    - main\n" in quality
    assert "- push\n    - pull_request" in quality
    assert "npm run quality" in _step(quality, "frontend-quality")
    python = _step(quality, "python-quality")
    assert "depends_on:\n      - frontend-quality" in python
    assert "python -m pytest --cov=app" in python
    assert "depends_on:\n      - python-quality" in _step(quality, "browser-e2e")
    assert "appleboy/drone-ssh" not in deploy
    assert "bash ./scripts/deploy-release-controller.sh" not in deploy
    assert not (ROOT / ".gitea/workflows/quality.yml").exists()


def test_fast_ci_keeps_all_python_tests_but_cannot_deploy() -> None:
    fast = _pipeline("fast-quality")
    assert "trigger:\n  branch:\n    exclude:\n      - main\n      - deploy\n" in fast
    assert fast.split("\ntrigger:\n")[1].strip().endswith("event:\n    - push")
    assert "npm ci" in _step(fast, "frontend-quality")
    assert "npm run quality" in _step(fast, "frontend-quality")
    python = _step(fast, "python-quality")
    assert "depends_on:\n      - frontend-quality" in python
    assert "python -m pytest -vv" in python
    assert "--cov" not in python
    assert "browser-e2e" not in fast
    assert "- fast-quality" not in _pipeline("deploy")


def test_full_ci_covers_main_prs_and_explicit_runs_before_deployment() -> None:
    quality = _pipeline("quality")
    assert "event:\n    - push\n    - pull_request\n    - cron\n    - custom" in quality
    python = _step(quality, "python-quality")
    assert "--cov=app --cov-report=term-missing --cov-report=xml" in python
    browser = _step(quality, "browser-e2e")
    assert 'CI: "true"' in browser
    assert "npm run test:e2e -- --workers=1" in browser
    for step in (python, browser):
        assert "when:" not in step
        assert "failure: ignore" not in step
    # A scheduled or custom quality run must never trigger a deployment.
    assert _pipeline("deploy").split("\ntrigger:\n")[1].strip() == (
        "branch:\n    - main\n  event:\n    - push"
    )


@pytest.mark.parametrize("pipeline_name", ["fast-quality", "quality"])
def test_python_ci_supports_untrusted_repositories_and_reports_stalls(pipeline_name: str) -> None:
    pipeline = _pipeline(pipeline_name)
    python = _step(pipeline, "python-quality")
    # All temporary data stays in the container; no trusted mount is required.
    assert "volumes:" not in pipeline
    assert "privileged:" not in pipeline
    assert 'PYTHONUNBUFFERED: "1"' in python
    command = next(line for line in python.splitlines() if "python -m pytest " in line).split(
        "python -m pytest ", 1
    )[1]
    for option in (
        "-vv",
        "--sqlite-no-sync",
        "--basetemp=/tmp/pytest-run",
        "--durations=30",
        "-o faulthandler_timeout=120",
        "--junitxml=test-results/pytest.xml",
    ):
        assert option in command
    for exclusion in ("--ignore", "--deselect", " -k ", " -m ", "--cov-fail-under"):
        assert exclusion not in command
    assert " tests/" not in command


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
    assert "image: python:3.10-slim-bookworm" in redeploy
    assert "python -m devtools.portainer_redeploy --insecure" in redeploy


def test_health_check_must_succeed_before_release_tagging() -> None:
    deploy = _pipeline("deploy")
    health = _step(deploy, "verify-deployment")
    assert "for i in $(seq 1 45); do" in health
    assert "if curl -fsS" in health
    assert "--max-time 5" in health
    assert "from_secret: deployment_health_url" in health
    assert '"$DEPLOYMENT_HEALTH_URL" >/dev/null' in health
    assert '[ "$SERVED_VERSION" = "$EXPECTED_VERSION" ]' in health
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


@pytest.fixture
def run_health_check(tmp_path: Path):
    """Run the actual CI shell command without network calls or retry delays."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$@" >> "$HEALTH_ARGS"\n'
        'for arg do url="$arg"; done\n'
        'case "$url" in\n'
        "  */version.json)\n"
        '    count=$(wc -l < "$HEALTH_CALLS")\n'
        '    if [ "$count" -lt "$VERSION_SUCCEED_ON" ]; then\n'
        '      printf "%s" "$STALE_VERSION"\n'
        '    else printf \'{"version":"3.1.3"}\'; fi\n'
        "    exit 0 ;;\n"
        "esac\n"
        'echo called >> "$HEALTH_CALLS"\n'
        'count=$(wc -l < "$HEALTH_CALLS")\n'
        '[ "$count" -ge "$HEALTH_SUCCEED_ON" ] && exit 0\nexit 22\n',
        encoding="utf-8",
    )
    curl.chmod(0o755)
    sleep = bin_dir / "sleep"
    sleep.write_text('#!/bin/sh\necho waiting >> "$HEALTH_SLEEPS"\n', encoding="utf-8")
    sleep.chmod(0o755)
    health = _step(_pipeline("deploy"), "verify-deployment")
    # Drone unescapes parameter expressions before starting the container shell.
    script = textwrap.dedent(health.split("      - |\n", 1)[1]).replace("$$", "$")

    def run(
        url: str | None,
        succeed_on: int,
        *,
        version_succeed_on: int = 1,
        stale_version: str = '{"version":"3.1.1"}',
    ):
        (tmp_path / ".release-tag").write_text("v3.1.3\n", encoding="utf-8")
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.defpath}",
            "HEALTH_CALLS": str(tmp_path / "calls"),
            "HEALTH_ARGS": str(tmp_path / "args"),
            "HEALTH_SLEEPS": str(tmp_path / "sleeps"),
            "HEALTH_SUCCEED_ON": str(succeed_on),
            "VERSION_SUCCEED_ON": str(version_succeed_on),
            "STALE_VERSION": stale_version,
        }
        env.pop("DEPLOYMENT_HEALTH_URL", None)
        if url is not None:
            env["DEPLOYMENT_HEALTH_URL"] = url
        return subprocess.run(
            ["sh", "-eu", "-c", script],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )

    return run


@pytest.mark.parametrize("url", [None, ""])
def test_health_check_rejects_missing_url_without_network_calls(run_health_check, tmp_path, url):
    result = run_health_check(url, succeed_on=1)
    assert result.returncode != 0
    assert "deployment_health_url" in result.stderr
    assert not (tmp_path / "calls").exists()


def test_health_check_retries_configured_home_url_until_success(run_health_check, tmp_path):
    url = "http://release-controller:8000/health?check=1&source=ci"
    result = run_health_check(url, succeed_on=3)
    assert result.returncode == 0, result.stderr
    assert "Deployment healthy." in result.stdout
    assert (tmp_path / "args").read_text().splitlines() == ["-fsS", "--max-time", "5", url] * 3 + [
        "-fsS",
        "--max-time",
        "5",
        "-H",
        "Cache-Control: no-cache",
        "http://release-controller:8000/version.json",
    ]
    assert len((tmp_path / "sleeps").read_text().splitlines()) == 2


def test_health_check_failure_blocks_release_after_all_attempts(run_health_check, tmp_path):
    result = run_health_check("http://nas.example:3100/health", succeed_on=46)
    assert result.returncode == 1
    assert "Deployment health or frontend version check failed" in result.stderr
    assert "Deployment healthy." not in result.stdout
    assert len((tmp_path / "calls").read_text().splitlines()) == 45


@pytest.mark.parametrize("body", ['{"version":"3.1.1"}', "", "<html>proxy page</html>"])
def test_healthy_old_or_invalid_frontend_cannot_pass_deployment(run_health_check, tmp_path, body):
    result = run_health_check(
        "http://nas.example:3100/health",
        succeed_on=1,
        version_succeed_on=46,
        stale_version=body,
    )
    assert result.returncode == 1
    assert "expected v3.1.3" in result.stderr
    assert "Deployment healthy." not in result.stdout
    assert len((tmp_path / "calls").read_text().splitlines()) == 45


def test_deployment_waits_for_new_frontend_on_same_service_path(run_health_check, tmp_path):
    result = run_health_check(
        "http://nas.example:3100/release/health?source=ci",
        succeed_on=1,
        version_succeed_on=3,
    )
    assert result.returncode == 0, result.stderr
    assert "Frontend version matches v3.1.3" in result.stdout
    assert len((tmp_path / "calls").read_text().splitlines()) == 3
    assert "http://nas.example:3100/release/version.json" in (tmp_path / "args").read_text()


def test_deployment_rejects_url_without_health_path(run_health_check, tmp_path):
    result = run_health_check("http://nas.example:3100/", succeed_on=1)
    assert result.returncode == 1
    assert "must end in /health" in result.stderr
    assert not (tmp_path / "calls").exists()


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


def test_portainer_compose_keeps_application_on_shared_ci_network_after_recreation() -> None:
    compose = (ROOT / "compose.portainer.yml").read_text(encoding="utf-8")
    services = compose.split("\nnetworks:\n", 1)[0]
    application, mcp = services.split("\n  release-controller-mcp:", 1)
    # Keep the private Stack network while allowing CI to resolve the API by name.
    assert "    networks:\n      - default\n      - ci-portainer\n" in application
    assert (
        "\nnetworks:\n  default: {}\n  ci-portainer:\n    external: true\n    name: ci-portainer"
        in compose
    )
    # The optional MCP service must not join the shared CI network.
    assert "ci-portainer" not in mcp


def test_tag_pipeline_publishes_release_controller_with_official_plugin() -> None:
    release = _pipeline("release")
    assert "image: plugins/gitea-release:1" in release
    assert "from_secret: gitea_release_token" in release
    assert "base_url:\n        from_secret: gitea_server" in release
    assert "ref:\n    - refs/tags/v*" in release
    assert "event:\n    - tag" in release
