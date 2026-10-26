"""CI boundary, durable inbox and shared promotion/recovery integration."""

import json
import threading
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import ci_cli
from app.core.config import get_settings
from app.db.models.ci import CIDeploymentEvent, CIDeploymentRequest, CIPolicy, CIServiceToken
from app.db.models.orchestration import Deployment, PublishRecord, WorkflowEvent
from app.db.models.registry import Project, ProjectComponent
from app.domain.shared.time import utc_now
from app.integrations.drone.exceptions import DroneConnectionError, DroneTimeoutError
from app.integrations.drone.schemas import DroneBuild
from app.schemas.drone import PromoteRequest
from app.services.background_worker import BackgroundWorker
from app.services.ci_deployment_service import CIDeploymentRunner, CIDeploymentService
from app.services.ci_tokens import create_token
from app.services.drone_build_service import DroneBuildService
from app.services.release_orchestrator import ReleaseOrchestrator
from tests.conftest import registered_component
from tests.test_concurrency_guards import run_together

URL = "/api/v1/ci/deployments"
PAYLOAD = dict(
    project_key="wordpress-mcp",
    component_key="server",
    source_build_number=123,
    target="production",
)
SOURCE_STAGES = ["ci", "integration", "publish-image"]


def build(number=123, status="success", event="push", stages=None):
    return DroneBuild(
        number=number,
        status=status,
        event=event,
        branch="main",
        commit_sha="a" * 40,
        parent=123 if event == "promote" else None,
        target="production" if event == "promote" else None,
        stages=stages
        if stages is not None
        else [
            dict(number=i, name=name, status="success") for i, name in enumerate(SOURCE_STAGES, 1)
        ],
    )


def promotion(stages=None, status="success"):
    return build(
        900,
        status,
        "promote",
        stages=stages
        if stages is not None
        else [
            dict(
                number=1,
                name="deploy-production",
                status="success",
                steps=[dict(number=1, name="deploy", status="success")],
            )
        ],
    )


class Drone:
    def __init__(self):
        self.source = build(status="running")
        self.promotion = promotion()
        self.promotes = 0
        self.reads = 0
        self.error = None
        self.promote_error = None
        self.repository = None
        self.history = []
        self.lock = threading.Lock()

    def get_repository(self, owner, repo):
        return self.repository or dict(namespace=owner, name=repo, slug=f"{owner}/{repo}")

    def get_build(self, owner, repo, number):
        self.reads += 1
        if self.error:
            raise self.error
        return self.source if number == 123 else self.promotion

    def promote_build(self, owner, repo, number, target):
        with self.lock:
            self.promotes += 1
        if self.promote_error:
            raise self.promote_error
        return self.promotion

    def list_builds(self, owner, repo, limit):
        return self.history


@pytest.fixture
def ci(client):
    settings = get_settings()
    with client.session_factory() as db:
        project = db.scalar(select(Project).where(Project.key == "default"))
        component = registered_component(db, "backend")
        project.key, component.key = "wordpress-mcp", "server"
        component.drone_owner, component.drone_repo = "jhanzihsian", "wordpress-mcp-server"
        db.add(
            CIPolicy(
                component_id=component.id,
                target="production",
                branch="main",
                source_stages=SOURCE_STAGES,
                promotion_stages=["deploy-production"],
            )
        )
        token, raw = create_token(
            db,
            name="drone",
            project_id=project.id,
            component_id=component.id,
            target="production",
            branch="main",
            expires_at=utc_now() + timedelta(days=1),
        )
        token_id = token.id
    drone = Drone()

    def builds(db):
        return DroneBuildService(drone, settings)

    runner = CIDeploymentRunner(client.session_factory, builds, settings)
    return client, drone, runner, {"Authorization": f"Bearer {raw}"}, token_id, builds


def accept(ci, **changes):
    client, _, _, headers, _, _ = ci
    return client.post(URL, json=PAYLOAD | changes, headers=headers)


def state(ci, request_id):
    return ci[0].get(f"{URL}/{request_id}", headers=ci[3]).json()


def test_acceptance_never_waits_or_contacts_drone_and_session_is_not_required(ci, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    get_settings.cache_clear()
    response = accept(ci)
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "WAITING_SOURCE" and data["request_id"]
    assert ci[1].reads == ci[1].promotes == 0
    assert ci[0].get(data["status_url"], headers=ci[3]).status_code == 200
    # A CI credential cannot become a browser session or access management routes.
    for path in (
        "/api/v1/projects",
        "/api/v1/deployments",
        "/api/v1/connections",
        "/api/v1/auth/session",
    ):
        response = ci[0].get(path, headers=ci[3])
        assert response.status_code == 401 or response.json().get("authenticated") is False


@pytest.mark.parametrize("credential", [None, "invalid", "expired", "revoked", "session"])
def test_token_rejection(ci, credential):
    client, _, _, headers, token_id, _ = ci
    if credential in {"expired", "revoked"}:
        with client.session_factory() as db:
            token = db.get(CIServiceToken, token_id)
            if credential == "expired":
                token.expires_at = utc_now() - timedelta(seconds=1)
            else:
                token.revoked_at = utc_now()
            db.commit()
    elif credential == "session":
        headers = {"Cookie": "release_controller_session=not-a-ci-token"}
    else:
        headers = {"Authorization": "Bearer invalid"} if credential else {}
    assert client.post(URL, json=PAYLOAD, headers=headers).status_code == 401
    assert client.get(f"{URL}/any", headers=headers).status_code == 401


@pytest.mark.parametrize("field,value", [("target", "staging"), ("component_key", "frontend")])
def test_scope_forbidden(ci, field, value):
    assert accept(ci, **{field: value}).status_code == 403


def test_other_project_and_status_scope_and_rotated_token(ci):
    client = ci[0]
    job = accept(ci).json()
    with client.session_factory() as db:
        project = Project(key="other", name="other")
        db.add(project)
        db.flush()
        other = ProjectComponent(
            project_id=project.id,
            key="server",
            display_name="server",
            position=1,
            drone_owner="x",
            drone_repo="y",
        )
        db.add(other)
        db.commit()
        token = db.get(CIServiceToken, ci[4])
        _, rotated = create_token(
            db,
            name="rotated",
            project_id=token.project_id,
            component_id=token.component_id,
            target=token.target,
            branch=token.branch,
            expires_at=utc_now() + timedelta(days=1),
        )
        _, forbidden = create_token(
            db,
            name="other",
            project_id=project.id,
            component_id=other.id,
            target="production",
            branch="main",
            expires_at=utc_now() + timedelta(days=1),
        )
    assert accept(ci, project_key="other").status_code == 403
    assert (
        client.get(job["status_url"], headers={"Authorization": f"Bearer {forbidden}"}).status_code
        == 403
    )
    assert (
        client.get(job["status_url"], headers={"Authorization": f"Bearer {rotated}"}).status_code
        == 200
    )
    assert client.get(f"{URL}/unknown", headers=ci[3]).status_code == 404


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"project_key": "missing"}, 404),
        ({"component_key": "missing"}, 404),
        ({"repository": "evil"}, 422),
        ({"source_build_number": 0}, 422),
        ({"source_build_number": "123"}, 422),
        ({"upstream_url": "https://evil"}, 422),
        ({"shell": "whoami"}, 422),
    ],
)
def test_invalid_resources_and_contract(ci, change, expected):
    assert accept(ci, **change).status_code == expected
    assert ci[1].promotes == 0


@pytest.mark.parametrize("disabled", ["project", "component", "policy", "branch", "target"])
def test_configuration_rejection(ci, disabled):
    with ci[0].session_factory() as db:
        token = db.get(CIServiceToken, ci[4])
        component = db.get(ProjectComponent, token.component_id)
        policy = db.scalar(select(CIPolicy))
        if disabled == "project":
            component.project.is_archived = True
        elif disabled == "component":
            component.is_active = False
        elif disabled == "policy":
            db.delete(policy)
        elif disabled == "branch":
            policy.branch = "develop"
        else:
            component.promote_target_override = "staging"
        db.commit()
    assert accept(ci).status_code in {403, 409}


@pytest.mark.parametrize(
    "scenario,code",
    [
        ("branch", "SOURCE_IDENTITY_REJECTED"),
        ("event", "SOURCE_IDENTITY_REJECTED"),
        ("number", "SOURCE_IDENTITY_REJECTED"),
        ("failure", "SOURCE_BUILD_FAILED"),
        ("stages", "SOURCE_STAGES_REJECTED"),
        ("skipped", "SOURCE_STAGES_REJECTED"),
        ("commit", "SOURCE_STAGES_REJECTED"),
        ("repository", "SOURCE_REPOSITORY_MISMATCH"),
    ],
)
def test_source_validation(ci, scenario, code):
    drone = ci[1]
    drone.source = build()
    if scenario == "branch":
        drone.source.branch = "develop"
    elif scenario == "event":
        drone.source.event = "pull_request"
    elif scenario == "number":
        drone.source.number = 124
    elif scenario == "failure":
        drone.source.status = "failure"
    elif scenario == "stages":
        drone.source.stages.pop()
    elif scenario == "skipped":
        drone.source.stages[0].status = "skipped"
    elif scenario == "commit":
        drone.source.commit_sha = ""
    else:
        drone.repository = dict(namespace="evil", name="repo", slug="evil/repo")
    job_id = accept(ci).json()["request_id"]
    ci[2].run()
    data = state(ci, job_id)
    assert data["status"] == "FAILED" and data["error_code"] == code
    assert drone.promotes == 0


def test_running_success_deploys_and_records_actor_history_without_publishing(ci):
    job_id = accept(ci).json()["request_id"]
    ci[2].run()
    assert state(ci, job_id)["status"] == "WAITING_SOURCE" and ci[1].promotes == 0
    assert state(ci, job_id)["commit_sha"] == "a" * 40
    ci[1].source = build()
    ci[2].run()
    # promotion itself still goes through the existing tracker
    ci[2].run()
    data = state(ci, job_id)
    assert data["status"] == "SUCCESS" and ci[1].promotes == 1
    assert data["commit_sha"] == "a" * 40 and data["promotion_build_number"] == 900
    assert [e["status"] for e in data["events"]] == ["WAITING_SOURCE", "DEPLOYING", "SUCCESS"]
    with ci[0].session_factory() as db:
        events = db.scalars(
            select(WorkflowEvent).where(WorkflowEvent.deployment_id == data["deployment_id"])
        ).all()
        assert any(e.actor == f"ci-token:{ci[4]}" and e.actor_source == "ci" for e in events)
        assert db.get(Deployment, data["deployment_id"]).publish_status == "NOT_PUBLISHED"
        assert db.scalar(select(func.count()).select_from(PublishRecord)) == 0


@pytest.mark.parametrize(
    "outcome", ["failure", "timeout", "upstream-timeout", "revoked", "disabled", "config"]
)
def test_waiting_failures(ci, outcome):
    job_id = accept(ci).json()["request_id"]
    ci[2].run()
    with ci[0].session_factory() as db:
        job = db.get(CIDeploymentRequest, job_id)
        if outcome in {"timeout", "upstream-timeout"}:
            job.deadline_at = utc_now() - timedelta(seconds=1)
        elif outcome == "revoked":
            db.get(CIServiceToken, ci[4]).revoked_at = utc_now()
        elif outcome == "disabled":
            db.get(ProjectComponent, job.component_id).is_active = False
        elif outcome == "config":
            db.get(ProjectComponent, job.component_id).drone_repo = "changed"
        db.commit()
    ci[1].source.status = "failure" if outcome == "failure" else "success"
    if outcome == "upstream-timeout":
        ci[1].error = DroneConnectionError("secret-in-error")
    ci[2].run()
    with ci[0].session_factory() as db:
        job = db.get(CIDeploymentRequest, job_id)
        assert job.status == "FAILED" and job.error_code
    assert ci[1].promotes == 0


def test_upstream_retry_and_worker_restart_resume_waiting(ci):
    job_id = accept(ci).json()["request_id"]
    ci[1].error = DroneConnectionError("Bearer hidden-upstream-secret")
    ci[2].run()
    assert state(ci, job_id)["error_code"] == "SOURCE_UPSTREAM_UNAVAILABLE"
    ci[1].error, ci[1].source = None, build()
    worker = BackgroundWorker(ci[0].session_factory, ci[5], get_settings())
    worker.reconcile_on_startup()
    worker.tick()
    assert state(ci, job_id)["status"] == "SUCCESS" and ci[1].promotes == 1


def test_duplicate_and_concurrent_notifications_and_workers(ci):
    ids = []

    def notify(barrier):
        with ci[0].session_factory() as db:
            token = db.get(CIServiceToken, ci[4])
            barrier.wait(timeout=10)
            job = CIDeploymentService(db, get_settings()).accept(token, **PAYLOAD)
            ids.append(job.id)

    assert run_together(notify, count=4) == []
    assert len(set(ids)) == 1
    assert accept(ci).json()["request_id"] == ids[0]
    ci[1].source = build()

    def run(barrier):
        barrier.wait(timeout=10)
        CIDeploymentRunner(ci[0].session_factory, ci[5], get_settings()).run()

    assert run_together(run, count=3) == []
    ci[2].run()
    assert ci[1].promotes == 1
    assert state(ci, ids[0])["status"] == "SUCCESS"
    assert accept(ci).json()["request_id"] == ids[0]


def test_restart_with_expired_lease_and_durable_prepared_intent(ci, monkeypatch):
    job_id = accept(ci).json()["request_id"]
    ci[1].source = build()

    def crash(*args):
        raise RuntimeError("sensitive error")

    monkeypatch.setattr(ci[2], "_track", crash)
    ci[2].run()
    with ci[0].session_factory() as db:
        job = db.get(CIDeploymentRequest, job_id)
        assert job.deployment_id and db.get(Deployment, job.deployment_id).status == "WAITING"
        job.lease_id, job.lease_until = "dead-worker", utc_now() - timedelta(seconds=1)
        db.commit()
    resumed = CIDeploymentRunner(ci[0].session_factory, ci[5], get_settings())
    resumed.run()
    resumed.run()
    assert ci[1].promotes == 1 and state(ci, job_id)["status"] == "SUCCESS"


def test_unknown_promotion_reconciles_without_resending_even_after_restart(ci):
    ci[1].source = build()
    ci[1].promote_error = DroneTimeoutError("Bearer upstream-secret")
    job_id = accept(ci).json()["request_id"]
    ci[2].run()
    for _ in range(3):
        CIDeploymentRunner(ci[0].session_factory, ci[5], get_settings()).run()
    assert ci[1].promotes == 1 and state(ci, job_id)["status"] == "DEPLOYING"
    ci[1].history = [promotion()]
    ci[2].run()
    ci[2].run()
    assert ci[1].promotes == 1 and state(ci, job_id)["status"] == "SUCCESS"


@pytest.mark.parametrize(
    "stages",
    [
        [],
        [dict(number=1, name="deploy-production", status="skipped")],
        [dict(number=1, name="other", status="success", steps=[dict(number=1, status="success")])],
        [dict(number=1, name="deploy-production", status="success")],
        [
            dict(
                number=1,
                name="deploy-production",
                status="success",
                steps=[dict(number=1, status="skipped")],
            )
        ],
    ],
)
def test_empty_or_skipped_promotion_never_succeeds(ci, stages):
    ci[1].source, ci[1].promotion = build(), promotion(stages)
    job_id = accept(ci).json()["request_id"]
    ci[2].run()
    ci[2].run()
    data = state(ci, job_id)
    assert data["status"] == "FAILED"
    with ci[0].session_factory() as db:
        assert db.get(Deployment, data["deployment_id"]).status == "FAILED"


def test_manual_deployment_is_adopted_and_stages_still_verified(ci):
    ci[1].source = build()
    with ci[0].session_factory() as db:
        component = db.get(ProjectComponent, db.get(CIServiceToken, ci[4]).component_id)
        orchestrator = ReleaseOrchestrator(db, ci[5](db), get_settings())
        deployment = orchestrator.promote_standalone(component, 123, PromoteRequest())
        deployment_id = deployment.id
    job_id = accept(ci).json()["request_id"]
    ci[2].run()
    assert state(ci, job_id)["deployment_id"] == deployment_id
    assert state(ci, job_id)["status"] == "SUCCESS" and ci[1].promotes == 1


def test_cli_policy_token_hash_once_and_revoke(ci, monkeypatch, capsys):
    monkeypatch.setattr(ci_cli, "SessionLocal", ci[0].session_factory)
    scope = [
        "--project",
        "wordpress-mcp",
        "--component",
        "server",
        "--target",
        "production",
        "--branch",
        "main",
    ]
    ci_cli.main(
        ["set-policy", *scope, "--source-stage", "ci", "--promotion-stage", "deploy-production"]
    )
    assert "policy_id" in capsys.readouterr().out
    ci_cli.main(["create-token", *scope, "--name", "new-token", "--expires-in-days", "2"])
    output = json.loads(capsys.readouterr().out)
    with ci[0].session_factory() as db:
        token = db.get(CIServiceToken, output["id"])
        assert len(output["token"]) >= 64 and output["token"] != token.token_hash
        assert output["token"] not in repr(token.__dict__)
    ci_cli.main(["revoke-token", "--id", output["id"]])
    assert output["token"] not in capsys.readouterr().out
    assert (
        ci[0]
        .post(URL, json=PAYLOAD, headers={"Authorization": f"Bearer {output['token']}"})
        .status_code
        == 401
    )


def test_ci_errors_logs_and_responses_do_not_expose_credentials(ci, caplog, monkeypatch):
    raw = ci[3]["Authorization"].removeprefix("Bearer ")
    response = accept(ci, repository=raw)
    assert response.status_code == 422 and raw not in response.text
    ci[1].source = build()
    ci[1].promote_error = DroneTimeoutError("Authorization: Bearer upstream-secret")
    job_id = accept(ci).json()["request_id"]
    ci[2].run()
    data = state(ci, job_id)
    assert raw not in json.dumps(data) and "upstream-secret" not in json.dumps(data)
    assert raw not in caplog.text and "upstream-secret" not in caplog.text
    with ci[0].session_factory() as db:
        assert "upstream-secret" not in " ".join(
            e.message or "" for e in db.scalars(select(WorkflowEvent))
        )
        assert raw not in " ".join(
            e.error_code or "" for e in db.scalars(select(CIDeploymentEvent))
        )


def test_manual_and_ci_race_share_existing_promotion_guard(ci):
    from app.services.deployment_service import DuplicatePromotionError

    ci[1].source = build()
    job_id = accept(ci).json()["request_id"]

    def race(barrier):
        if threading.current_thread().name.endswith("manual"):
            with ci[0].session_factory() as db:
                component = db.get(ProjectComponent, db.get(CIServiceToken, ci[4]).component_id)
                orchestrator = ReleaseOrchestrator(db, ci[5](db), get_settings())
                barrier.wait(timeout=10)
                try:
                    orchestrator.promote_standalone(component, 123, PromoteRequest())
                except DuplicatePromotionError:
                    pass
        else:
            barrier.wait(timeout=10)
            ci[2].run()

    barrier = threading.Barrier(2)
    errors = []

    def invoke():
        try:
            race(barrier)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=invoke, name=name) for name in ("manual", "ci")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert errors == []
    ci[2].run()
    ci[2].run()
    assert state(ci, job_id)["status"] == "SUCCESS" and ci[1].promotes == 1
    with ci[0].session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Deployment)) == 1


def test_recovery_cannot_dispatch_prepared_ci_job_after_revocation(ci, monkeypatch):
    ci[1].source = build()
    job_id = accept(ci).json()["request_id"]

    def crash(*args):
        raise RuntimeError()

    monkeypatch.setattr(ci[2], "_track", crash)
    ci[2].run()
    with ci[0].session_factory() as db:
        job = db.get(CIDeploymentRequest, job_id)
        deployment_id = job.deployment_id
        db.get(CIServiceToken, ci[4]).revoked_at = utc_now()
        db.commit()
    # A generic/manual refresh must enforce CI authorization too.
    with ci[0].session_factory() as db:
        ReleaseOrchestrator(db, ci[5](db), get_settings()).refresh_standalone(deployment_id)
        assert db.get(Deployment, deployment_id).error_code == "CI_DISPATCH_REJECTED"
    assert ci[1].promotes == 0


def test_live_worker_lease_prevents_second_worker_and_expired_lease_recovers(ci):
    job_id = accept(ci).json()["request_id"]
    ci[1].source = build()
    with ci[0].session_factory() as db:
        job = db.get(CIDeploymentRequest, job_id)
        job.lease_id, job.lease_until = "other", utc_now() + timedelta(minutes=1)
        db.commit()
    assert ci[2].run() == 0 and ci[1].promotes == 0
    with ci[0].session_factory() as db:
        db.get(CIDeploymentRequest, job_id).lease_until = utc_now() - timedelta(seconds=1)
        db.commit()
    ci[2].run()
    assert ci[1].promotes == 1


def test_component_policy_is_configurable(ci):
    with ci[0].session_factory() as db:
        policy = db.scalar(select(CIPolicy))
        policy.source_stages = ["test-and-publish"]
        policy.promotion_stages = ["custom-deploy"]
        db.commit()
    ci[1].source = build(stages=[dict(number=1, name="test-and-publish", status="success")])
    ci[1].promotion = promotion(
        stages=[
            dict(
                number=1,
                name="custom-deploy",
                status="success",
                steps=[dict(number=1, status="success")],
            )
        ]
    )
    job_id = accept(ci).json()["request_id"]
    ci[2].run()
    ci[2].run()
    assert state(ci, job_id)["status"] == "SUCCESS"


def test_unexpected_api_and_worker_errors_are_sanitized(ci, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise RuntimeError("Authorization: Bearer do-not-expose")

    with monkeypatch.context() as patch:
        patch.setattr(CIDeploymentService, "accept", broken)
        response = accept(ci)
        assert response.status_code == 500 and "do-not-expose" not in response.text
    job_id = accept(ci).json()["request_id"]
    monkeypatch.setattr(ci[1], "get_build", broken)
    ci[2].run()
    assert state(ci, job_id)["error_code"] == "CI_WORKER_RETRY"
    assert "do-not-expose" not in caplog.text


def test_cli_refuses_missing_resources_bad_policy_and_unknown_token(ci, monkeypatch, capsys):
    monkeypatch.setattr(ci_cli, "SessionLocal", ci[0].session_factory)
    for args, code in [
        (["revoke-token", "--id", "missing"], "CI_TOKEN_NOT_FOUND"),
        (
            [
                "create-token",
                "--project",
                "missing",
                "--component",
                "server",
                "--target",
                "production",
                "--branch",
                "main",
                "--name",
                "test",
            ],
            "PROJECT_NOT_FOUND",
        ),
        (
            [
                "create-token",
                "--project",
                "wordpress-mcp",
                "--component",
                "server",
                "--target",
                "production",
                "--branch",
                "other",
                "--name",
                "test",
            ],
            "TOKEN_SCOPE_OR_NAME_INVALID",
        ),
    ]:
        with pytest.raises(SystemExit) as exc:
            ci_cli.main(args)
        assert exc.value.code == 1 and code in capsys.readouterr().err


def test_adopting_manual_intent_does_not_replace_human_authorization(ci, monkeypatch):
    ci[1].source = build()
    with ci[0].session_factory() as db:
        component = db.get(ProjectComponent, db.get(CIServiceToken, ci[4]).component_id)
        orchestrator = ReleaseOrchestrator(db, ci[5](db), get_settings())
        deployment = orchestrator.prepare_standalone(
            component, build(), "production", requested_by="human-operator"
        )
        deployment_id = deployment.id
        db.commit()
    job_id = accept(ci).json()["request_id"]

    def interrupted(*args):
        raise RuntimeError()

    monkeypatch.setattr(ci[2], "_track", interrupted)
    ci[2].run()
    with ci[0].session_factory() as db:
        assert db.get(CIDeploymentRequest, job_id).deployment_id == deployment_id
        db.get(CIServiceToken, ci[4]).revoked_at = utc_now()
        db.commit()
    with ci[0].session_factory() as db:
        ReleaseOrchestrator(db, ci[5](db), get_settings()).refresh_standalone(deployment_id)
    assert ci[1].promotes == 1


def test_adopted_success_verification_has_deadline(ci):
    ci[1].source = build()
    with ci[0].session_factory() as db:
        component = db.get(ProjectComponent, db.get(CIServiceToken, ci[4]).component_id)
        orchestrator = ReleaseOrchestrator(db, ci[5](db), get_settings())
        deployment = orchestrator.promote_standalone(component, 123, PromoteRequest())
        orchestrator.refresh_standalone(deployment.id)
        deployment.started_at = utc_now() - timedelta(days=1)
        db.commit()
    original = ci[1].get_build

    def source_only(owner, repo, number):
        if number != 123:
            raise DroneTimeoutError("never-expose")
        return original(owner, repo, number)

    ci[1].get_build = source_only
    job_id = accept(ci).json()["request_id"]
    ci[2].run()
    assert state(ci, job_id)["error_code"] == "PROMOTION_VERIFICATION_TIMEOUT"
    assert ci[1].promotes == 1


def test_failed_intent_repeated_notification_never_retries_promotion(ci):
    ci[1].source = build(status="failure")
    job_id = accept(ci).json()["request_id"]
    ci[2].run()
    ci[1].source = build()
    assert accept(ci).json()["request_id"] == job_id
    ci[2].run()
    assert state(ci, job_id)["status"] == "FAILED" and ci[1].promotes == 0


def test_clone_only_promotion_is_not_an_actual_deployment(ci):
    ci[1].source = build()
    ci[1].promotion = promotion(
        stages=[
            dict(
                number=1,
                name="deploy-production",
                status="success",
                steps=[
                    dict(number=1, name="clone", status="success"),
                    dict(number=2, name="deploy", status="skipped"),
                ],
            )
        ]
    )
    job_id = accept(ci).json()["request_id"]
    ci[2].run()
    ci[2].run()
    assert state(ci, job_id)["error_code"] == "PROMOTION_STAGES_REJECTED"
