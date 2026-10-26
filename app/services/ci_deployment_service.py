"""Durable CI inbox. HTTP acceptance never contacts Drone or promotes a build."""

import logging
import re
import uuid
from datetime import timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.ci import CIDeploymentEvent, CIDeploymentRequest, CIPolicy, CIServiceToken
from app.db.models.orchestration import ACTIVE_PROMOTION_STATUSES, Deployment
from app.db.models.registry import Project, ProjectComponent
from app.domain.shared.time import utc_now
from app.integrations.drone.exceptions import DroneError
from app.services.ci_policy import stages_succeeded
from app.services.ci_tokens import aware, token_active
from app.services.component_registry import ComponentRegistry
from app.services.deployment_service import DuplicatePromotionError
from app.services.drone_build_service import BuildNotFoundError
from app.services.release_orchestrator import ReleaseOrchestrator

logger = logging.getLogger(__name__)
TERMINAL = {"SUCCESS", "FAILED"}


class CIError(Exception):
    def __init__(self, code: str, status: int = 409):
        self.code = code
        self.status = status
        super().__init__(code)


def resolve_component(db: Session, project_key: str, component_key: str) -> ProjectComponent:
    project = db.scalar(select(Project).where(Project.key == project_key))
    if project is None:
        raise CIError("PROJECT_NOT_FOUND", 404)
    component = db.scalar(
        select(ProjectComponent).where(
            ProjectComponent.project_id == project.id, ProjectComponent.key == component_key
        )
    )
    if component is None:
        raise CIError("COMPONENT_NOT_FOUND", 404)
    if project.is_archived or not component.is_active:
        raise CIError("PROJECT_OR_COMPONENT_DISABLED")
    return component


def policy_for(db: Session, component_id: str, target: str) -> CIPolicy:
    policy = db.scalar(
        select(CIPolicy).where(CIPolicy.component_id == component_id, CIPolicy.target == target)
    )
    if policy is None or not policy.source_stages or not policy.promotion_stages:
        raise CIError("CI_POLICY_NOT_CONFIGURED")
    return policy


def authorize(
    token: CIServiceToken, project_id: str, component_id: str, target: str, branch: str
) -> None:
    if (token.project_id, token.component_id, token.target, token.branch) != (
        project_id,
        component_id,
        target,
        branch,
    ):
        raise CIError("CI_SCOPE_FORBIDDEN", 403)


def transition(db: Session, job: CIDeploymentRequest, status: str, code: str | None = None) -> None:
    if job.status == status and job.error_code == code:
        return
    job.status, job.error_code, job.updated_at = status, code, utc_now()
    if status in TERMINAL:
        job.finished_at = utc_now()
    db.add(CIDeploymentEvent(request_id=job.id, status=status, error_code=code))


class CIDeploymentService:
    def __init__(self, db: Session, settings: Settings):
        self.db, self.settings = db, settings
        self.registry = ComponentRegistry(db, settings)

    def accept(
        self,
        token: CIServiceToken,
        *,
        project_key: str,
        component_key: str,
        source_build_number: int,
        target: str,
    ) -> CIDeploymentRequest:
        component = resolve_component(self.db, project_key, component_key)
        # Check the resource scope before revealing its CI policy.
        authorize(token, component.project_id, component.id, target, token.branch)
        policy = policy_for(self.db, component.id, target)
        authorize(token, component.project_id, component.id, target, policy.branch)
        existing = self.db.scalar(
            select(CIDeploymentRequest).where(
                CIDeploymentRequest.component_id == component.id,
                CIDeploymentRequest.source_build_number == source_build_number,
                CIDeploymentRequest.target == target,
            )
        )
        if existing:
            authorize(
                token, existing.project_id, existing.component_id, existing.target, existing.branch
            )
            return existing
        try:
            connection = self.registry.drone_connection(component)
        except Exception:
            raise CIError("DRONE_CONNECTION_NOT_CONFIGURED") from None
        if component.promote_target_override and component.promote_target_override != target:
            raise CIError("TARGET_CONFIGURATION_MISMATCH")
        job = CIDeploymentRequest(
            service_token_id=token.id,
            project_id=component.project_id,
            component_id=component.id,
            source_build_number=source_build_number,
            target=target,
            branch=policy.branch,
            drone_connection_id=connection.id,
            drone_owner=component.drone_owner,
            drone_repository=component.drone_repo,
            source_stages=list(policy.source_stages),
            promotion_stages=list(policy.promotion_stages),
            status="WAITING_SOURCE",
            deadline_at=utc_now() + timedelta(seconds=self.settings.ci_source_wait_seconds),
        )
        self.db.add(job)
        try:
            self.db.flush()
            self.db.add(CIDeploymentEvent(request_id=job.id, status="WAITING_SOURCE"))
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            job = self.db.scalar(
                select(CIDeploymentRequest).where(
                    CIDeploymentRequest.component_id == component.id,
                    CIDeploymentRequest.source_build_number == source_build_number,
                    CIDeploymentRequest.target == target,
                )
            )
            if job is None:
                raise CIError("CI_ACCEPT_CONFLICT") from None
        authorize(token, job.project_id, job.component_id, job.target, job.branch)
        return job


class CIDeploymentRunner:
    """Per-job leases bound concurrent polling; deployment claims guard upstream writes.

    A lease may be reclaimed after five minutes if a worker dies. The deployment
    link is committed with its intent BEFORE any promotion. After a crash, an
    existing PROMOTING intent is reconciled, never sent a second time.
    """

    def __init__(self, session_factory, builds_factory, settings: Settings):
        self.session_factory, self.builds_factory, self.settings = (
            session_factory,
            builds_factory,
            settings,
        )

    def run(self) -> int:
        now = utc_now()
        with self.session_factory() as db:
            ids = list(
                db.scalars(
                    select(CIDeploymentRequest.id)
                    .where(
                        CIDeploymentRequest.status.not_in(TERMINAL),
                        or_(
                            CIDeploymentRequest.lease_until.is_(None),
                            CIDeploymentRequest.lease_until <= now,
                        ),
                    )
                    .order_by(CIDeploymentRequest.updated_at)
                    .limit(self.settings.recovery_batch_size)
                )
            )
        for job_id in ids:
            self.run_one(job_id)
        return len(ids)

    def run_one(self, job_id: str) -> None:
        lease_id, now = str(uuid.uuid4()), utc_now()
        with self.session_factory() as db:
            claim = db.execute(
                update(CIDeploymentRequest)
                .where(
                    CIDeploymentRequest.id == job_id,
                    CIDeploymentRequest.status.not_in(TERMINAL),
                    or_(
                        CIDeploymentRequest.lease_until.is_(None),
                        CIDeploymentRequest.lease_until <= now,
                    ),
                )
                .values(lease_id=lease_id, lease_until=now + timedelta(minutes=5))
            )
            db.commit()
            if claim.rowcount != 1:
                return
            try:
                self._advance(db, db.get(CIDeploymentRequest, job_id))
                db.commit()
            except Exception:
                # Never log exception text/tracebacks: upstream transports can
                # carry credentials. Only fixed error codes enter CI history.
                db.rollback()
                job = db.get(CIDeploymentRequest, job_id)
                transition(db, job, job.status, "CI_WORKER_RETRY")
                db.commit()
                logger.warning("CI job retry request_id=%s", job_id)
            finally:
                db.execute(
                    update(CIDeploymentRequest)
                    .where(
                        CIDeploymentRequest.id == job_id,
                        CIDeploymentRequest.lease_id == lease_id,
                    )
                    .values(lease_id=None, lease_until=None, updated_at=utc_now())
                )
                db.commit()

    def _advance(self, db: Session, job: CIDeploymentRequest) -> None:
        builds = self.builds_factory(db)
        orchestrator = ReleaseOrchestrator(
            db, builds, self.settings, actor=f"ci-token:{job.service_token_id}", actor_source="ci"
        )
        if job.deployment_id:
            self._track(db, job, orchestrator)
            return
        if aware(job.deadline_at) <= utc_now():
            transition(db, job, "FAILED", "SOURCE_WAIT_TIMEOUT")
            return
        if not token_active(db.get(CIServiceToken, job.service_token_id)):
            transition(db, job, "FAILED", "CI_TOKEN_INACTIVE")
            return
        component = db.get(ProjectComponent, job.component_id)
        if component.project.is_archived or not component.is_active:
            transition(db, job, "FAILED", "PROJECT_OR_COMPONENT_DISABLED")
            return
        registry = orchestrator.registry
        if (
            component.drone_owner,
            component.drone_repo,
            registry.drone_connection(component).id,
        ) != (job.drone_owner, job.drone_repository, job.drone_connection_id) or (
            component.promote_target_override and component.promote_target_override != job.target
        ):
            transition(db, job, "FAILED", "COMPONENT_CONFIGURATION_CHANGED")
            return
        try:
            repo = builds.get_repository_info(component)
            if (repo.owner, repo.name, repo.slug) != (
                job.drone_owner,
                job.drone_repository,
                f"{job.drone_owner}/{job.drone_repository}",
            ):
                transition(db, job, "FAILED", "SOURCE_REPOSITORY_MISMATCH")
                return
            build = builds.get_build(component, job.source_build_number)
        except BuildNotFoundError:
            transition(db, job, "FAILED", "SOURCE_BUILD_NOT_FOUND")
            return
        except DroneError:
            transition(db, job, "WAITING_SOURCE", "SOURCE_UPSTREAM_UNAVAILABLE")
            return
        # Preserve the upstream commit for waiting/rejected builds as well.
        # Only a syntactically valid SHA can enter the audit/API snapshot.
        if re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", build.commit_sha):
            job.commit_sha = build.commit_sha
        if (
            build.number != job.source_build_number
            or build.event != "push"
            or build.branch != job.branch
        ):
            transition(db, job, "FAILED", "SOURCE_IDENTITY_REJECTED")
            return
        if build.status in {"pending", "running", "blocked", "waiting"}:
            transition(db, job, "WAITING_SOURCE")
            return
        if build.status != "success":
            transition(db, job, "FAILED", "SOURCE_BUILD_FAILED")
            return
        if not re.fullmatch(
            r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", build.commit_sha
        ) or not stages_succeeded(build, job.source_stages):
            transition(db, job, "FAILED", "SOURCE_STAGES_REJECTED")
            return
        # Reads may have taken time; never dispatch after the waiting deadline.
        if aware(job.deadline_at) <= utc_now():
            transition(db, job, "FAILED", "SOURCE_WAIT_TIMEOUT")
            return
        job.commit_sha = build.commit_sha
        try:
            deployment = orchestrator.prepare_standalone(
                component, build, job.target, requested_by=f"ci-token:{job.service_token_id}"
            )
            deployment.required_promotion_stages = list(job.promotion_stages)
            job.deployment_id = deployment.id
            transition(db, job, "DEPLOYING")
            db.commit()
        except DuplicatePromotionError:
            # create() may have rolled back when a human or another inbox won.
            db.rollback()
            job = db.get(CIDeploymentRequest, job.id)
            deployment = db.scalar(
                select(Deployment).where(
                    Deployment.drone_connection_id == job.drone_connection_id,
                    Deployment.drone_owner == job.drone_owner,
                    Deployment.drone_repository == job.drone_repository,
                    Deployment.source_build_number == job.source_build_number,
                    Deployment.target == job.target,
                    Deployment.status.in_(ACTIVE_PROMOTION_STATUSES),
                )
            )
            if deployment is None:
                return
            if deployment.commit_sha != build.commit_sha or deployment.branch != job.branch:
                transition(db, job, "FAILED", "EXISTING_DEPLOYMENT_MISMATCH")
                return
            job.commit_sha, job.deployment_id = build.commit_sha, deployment.id
            transition(db, job, "DEPLOYING")
            db.commit()
        self._track(db, job, orchestrator)

    def _track(
        self, db: Session, job: CIDeploymentRequest, orchestrator: ReleaseOrchestrator
    ) -> None:
        deployment = db.get(Deployment, job.deployment_id)
        if deployment.release_bundle_id:
            orchestrator.refresh_bundle(deployment.release_bundle_id)
        else:
            orchestrator.refresh_standalone(deployment.id)
        if deployment.status == "SUCCESS":
            # Also validate adopted historical/manual deployments whose snapshot
            # may predate CI policy. No success is inferred from a build summary.
            try:
                build = orchestrator.builds.get_build(
                    orchestrator.registry.for_deployment(deployment),
                    deployment.promotion_build_number,
                )
            except (DroneError, BuildNotFoundError):
                started = aware(deployment.started_at or deployment.created_at)
                if utc_now() >= started + timedelta(
                    seconds=self.settings.deployment_timeout_seconds
                ):
                    transition(db, job, "FAILED", "PROMOTION_VERIFICATION_TIMEOUT")
                else:
                    transition(db, job, "DEPLOYING", "PROMOTION_UPSTREAM_UNAVAILABLE")
                return
            if not (
                build.is_promotion_of(job.source_build_number, job.target)
                and build.commit_sha == job.commit_sha
                and stages_succeeded(build, job.promotion_stages, deployment=True)
            ):
                transition(db, job, "FAILED", "PROMOTION_STAGES_REJECTED")
                return
            transition(db, job, "SUCCESS")
        elif deployment.status in {"FAILED", "CANCELLED"}:
            code = (
                deployment.error_code
                if deployment.error_code
                in {"PROMOTION_STAGES_REJECTED", "CI_DISPATCH_REJECTED", "WORKFLOW_TIMEOUT"}
                else "DEPLOYMENT_FAILED"
            )
            transition(db, job, "FAILED", code)
        else:
            transition(db, job, "DEPLOYING")
