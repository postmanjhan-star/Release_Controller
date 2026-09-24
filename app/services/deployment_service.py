import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings
from app.db.models.orchestration import (
    ACTIVE_PROMOTION_STATUSES,
    Deployment,
    ReleaseBundle,
    WorkflowEvent,
)
from app.db.models.registry import ProjectComponent
from app.domain.orchestration.value_objects import (
    ActorSource,
    Component,
    DeploymentStatus,
    WorkflowEventType,
    WorkflowStage,
)
from app.domain.shared.time import utc_now
from app.integrations.drone.exceptions import (
    DroneConnectionError,
    DroneError,
    DroneNotFoundError,
    DroneTimeoutError,
    DroneUnexpectedResponseError,
)
from app.integrations.drone.schemas import DroneBuild
from app.services.component_registry import ComponentRegistry
from app.services.drone_build_service import DroneBuildService
from app.services.workflow_service import OrchestrationWorkflowService

logger = logging.getLogger(__name__)

# Kept as a set for the pre-flight duplicate check.  The authoritative definition
# lives with the model so it stays in step with uq_deployments_active_promotion.
ACTIVE_DUPLICATE_STATUSES = set(ACTIVE_PROMOTION_STATUSES)
TERMINAL_DEPLOYMENT_STATUSES = {
    DeploymentStatus.SUCCESS.value,
    DeploymentStatus.FAILED.value,
    DeploymentStatus.CANCELLED.value,
}
# A deployment may only be claimed for promotion out of WAITING.  Every promotion
# therefore starts with a real state change, which is what makes the claim below
# safe against two callers arriving at once.
CLAIMABLE_FOR_PROMOTION = (DeploymentStatus.WAITING.value,)

# A promote that ended in one of these never got an answer, so Drone may or may
# not have created the build.  Anything else (a 401, a 404, a rejected request)
# is a definite "no promotion exists" and may fail the deployment immediately.
UNCONFIRMED_PROMOTE_ERROR_CODES = frozenset(
    {
        DroneTimeoutError.error_code,
        DroneConnectionError.error_code,
        DroneUnexpectedResponseError.error_code,
    }
)
PROMOTION_UNCONFIRMED_CODE = "DRONE_PROMOTE_UNCONFIRMED"


class DeploymentNotFoundError(Exception):
    pass


class DuplicatePromotionError(Exception):
    pass


@dataclass(frozen=True)
class DeploymentFilters:
    # A component key, not an enum: a project names its own components.
    component: str | None = None
    status: DeploymentStatus | None = None
    target: str | None = None
    release_bundle_id: str | None = None


# frontend and backend keep the stage names they have always written, so an
# existing timeline stays readable and the BPMN diagrams still line up.  Any other
# component gets the component-agnostic stage; which component it was is recorded
# on the event itself.
_PROMOTE_STAGES = {
    Component.FRONTEND.value: WorkflowStage.PROMOTE_FRONTEND,
    Component.BACKEND.value: WorkflowStage.PROMOTE_BACKEND,
}
_WAIT_STAGES = {
    Component.FRONTEND.value: WorkflowStage.WAIT_FRONTEND_DEPLOYMENT,
    Component.BACKEND.value: WorkflowStage.WAIT_BACKEND_DEPLOYMENT,
}


def promote_stage(component_key: str | Component) -> WorkflowStage:
    key = component_key.value if isinstance(component_key, Component) else component_key
    return _PROMOTE_STAGES.get(key, WorkflowStage.PROMOTE_COMPONENT)


def wait_stage(component_key: str | Component) -> WorkflowStage:
    key = component_key.value if isinstance(component_key, Component) else component_key
    return _WAIT_STAGES.get(key, WorkflowStage.WAIT_COMPONENT_DEPLOYMENT)


class DeploymentService:
    def __init__(
        self,
        db: Session,
        builds: DroneBuildService,
        settings: Settings,
        *,
        actor: str | None = None,
        actor_source: str = ActorSource.SYSTEM.value,
    ) -> None:
        self.db = db
        self.builds = builds
        self.settings = settings
        self.registry = ComponentRegistry(db, settings)
        self.events = OrchestrationWorkflowService(db, actor=actor, actor_source=actor_source)

    def assert_not_duplicate(
        self, component: ProjectComponent, build_number: int, target: str
    ) -> None:
        """Fast pre-flight check for a readable 409.

        This is advisory only.  Another request can insert between this SELECT and
        the caller's INSERT, so the real guarantee is the uq_deployments_active_promotion
        unique index, surfaced by create() below.

        Matched on the repository the promotion would actually hit, not on the
        component: two components pointing at one repository share a slot, and so
        do two projects that both point at it.
        """
        connection = self.registry.drone_connection(component)
        existing = self.db.scalar(
            select(Deployment.id)
            .where(
                Deployment.drone_connection_id == connection.id,
                Deployment.drone_owner == component.drone_owner,
                Deployment.drone_repository == component.drone_repo,
                Deployment.source_build_number == build_number,
                Deployment.target == target,
                Deployment.status.in_(ACTIVE_DUPLICATE_STATUSES),
            )
            .limit(1)
        )
        if existing is not None:
            raise DuplicatePromotionError("This build has already been promoted to this target")

    def create(
        self,
        component: ProjectComponent,
        build: DroneBuild,
        target: str,
        *,
        bundle: ReleaseBundle | None = None,
        requested_by: str | None = None,
    ) -> Deployment:
        """Record the intent to promote.

        Deployments are always created WAITING and are moved to PROMOTING only by
        promote(), which claims them atomically.  Creating in a pre-claim state is
        what lets the promotion itself be a state transition rather than an
        assignment, and therefore what makes a concurrent second promote visible.
        """
        repo = self.builds.get_drone_repo(component)
        # Recorded, not just resolved: the connection is part of the promotion
        # slot, so a deployment has to remember which one it was created against
        # even if the component is later pointed somewhere else.
        connection = self.registry.drone_connection(component)
        deployment = Deployment(
            release_bundle_id=bundle.id if bundle else None,
            release_bundle=bundle,
            project_id=component.project_id,
            component_id=component.id,
            component=component.key,
            drone_connection_id=connection.id,
            drone_owner=repo.owner,
            drone_repository=repo.name,
            source_build_number=build.number,
            commit_sha=build.commit_sha,
            branch=build.branch,
            target=target,
            status=DeploymentStatus.WAITING.value,
            current_stage=None,
            requested_by=requested_by,
        )
        self.db.add(deployment)
        try:
            self.db.flush()
        except IntegrityError as exc:
            # uq_deployments_active_promotion rejected a concurrent duplicate that
            # slipped past assert_not_duplicate.
            self.db.rollback()
            raise DuplicatePromotionError(
                "This build has already been promoted to this target"
            ) from exc
        return deployment

    def claim_for_promotion(
        self, deployment: Deployment, release: ReleaseBundle | None = None
    ) -> bool:
        """Take exclusive ownership of a WAITING deployment and commit that fact.

        Returns False when another caller got there first, in which case the
        deployment object is refreshed to the winner's state and nothing is sent
        upstream.  Committing here is deliberate: it publishes the claim to other
        sessions and releases the write lock before any network call.
        """
        stage = promote_stage(deployment.component).value
        now = utc_now()
        claimed = self.db.execute(
            update(Deployment)
            .where(
                Deployment.id == deployment.id,
                Deployment.status.in_(CLAIMABLE_FOR_PROMOTION),
            )
            .values(
                status=DeploymentStatus.PROMOTING.value,
                current_stage=stage,
                started_at=func.coalesce(Deployment.started_at, now),
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            # Expire rather than roll back: the caller may hold legitimate pending
            # work in this transaction (a sibling deployment's refresh events).
            self.db.expire(deployment)
            logger.info(
                "Promotion claim skipped deployment_id=%s component=%s status=%s",
                deployment.id,
                deployment.component,
                deployment.status,
            )
            return False

        self.db.expire(deployment)
        if release:
            release.status = "PROMOTING"
            release.deployment_status = "PROMOTING"
            release.current_stage = stage
            release.started_at = release.started_at or now
            release.updated_at = now
        self.events.append(
            release=release,
            deployment=deployment,
            stage=stage,
            event_type=WorkflowEventType.STAGE_STARTED.value,
            status=deployment.status,
        )
        self.db.commit()
        return True

    def promote(self, deployment: Deployment, release: ReleaseBundle | None = None) -> Deployment:
        """Claim, call Drone with no transaction open, then record the outcome.

        The upstream call sits between two transactions rather than inside one.
        Holding a SQLite write lock across it made a slow Drone response fail every
        other write in the process with "database is locked".
        """
        component = self.registry.for_deployment(deployment)
        stage = promote_stage(deployment.component).value
        if not self.claim_for_promotion(deployment, release):
            return deployment

        try:
            promotion = self.builds.promote(
                component, deployment.source_build_number, deployment.target
            )
        except DroneError as exc:
            if exc.error_code in UNCONFIRMED_PROMOTE_ERROR_CODES:
                # The request may well have reached Drone.  Marking this FAILED
                # would both lose a running production deployment and free the
                # duplicate-protection slot, so the next retry would promote a
                # second time.  Stay PROMOTING and reconcile on the next refresh.
                self._mark_promotion_unconfirmed(deployment, exc, stage, release)
            else:
                # Drone rejected the request outright, so no promotion exists.
                self.fail(
                    deployment,
                    failed_stage=stage,
                    error_code=exc.error_code,
                    error_message=str(exc),
                    release=release,
                )
            self.db.commit()
            return deployment

        self._record_promotion(deployment, promotion, stage, release)
        self.db.commit()
        return deployment

    def _mark_promotion_unconfirmed(
        self,
        deployment: Deployment,
        exc: DroneError,
        stage: str,
        release: ReleaseBundle | None,
    ) -> None:
        now = utc_now()
        deployment.poll_error_code = PROMOTION_UNCONFIRMED_CODE
        deployment.poll_error_message = (
            f"Drone did not confirm the promotion request ({exc.error_code}). "
            "Waiting to reconcile rather than promoting again."
        )
        deployment.poll_failure_count = (deployment.poll_failure_count or 0) + 1
        deployment.updated_at = now
        self.events.append(
            release=release,
            deployment=deployment,
            stage=stage,
            event_type=WorkflowEventType.PROMOTION_UNCONFIRMED.value,
            status=deployment.status,
            error_code=exc.error_code,
            message=str(exc),
        )
        logger.warning(
            "Promotion outcome unknown deployment_id=%s component=%s repo=%s/%s "
            "source_build_number=%s target=%s error_code=%s",
            deployment.id,
            deployment.component,
            deployment.drone_owner,
            deployment.drone_repository,
            deployment.source_build_number,
            deployment.target,
            exc.error_code,
        )

    def _reconcile_promotion(
        self, deployment: Deployment, stage: str, release: ReleaseBundle | None
    ) -> bool:
        """Adopt a promotion build Drone created for a request that never returned.

        Never sends a promote.  Either the build is found and adopted, or the
        deployment stays unconfirmed until the workflow timeout decides.
        """
        try:
            promotion = self.builds.find_promotion_build(
                self.registry.for_deployment(deployment),
                deployment.source_build_number,
                deployment.target,
            )
        except Exception as exc:  # noqa: BLE001 - classified below like any poll
            self._record_poll_failure(deployment, exc, stage, release)
            return False

        if promotion is None:
            self._record_poll_failure(
                deployment,
                DroneNotFoundError("No promotion build found for this deployment yet"),
                stage,
                release,
                code=PROMOTION_UNCONFIRMED_CODE,
            )
            return False

        self._clear_poll_failure(deployment, stage, release)
        self.events.append(
            release=release,
            deployment=deployment,
            stage=stage,
            event_type=WorkflowEventType.PROMOTION_RECONCILED.value,
            status=deployment.status,
            message=(
                f"Adopted existing Drone promotion build #{promotion.number} "
                "instead of creating a second one"
            ),
        )
        logger.info(
            "Promotion reconciled deployment_id=%s component=%s source_build_number=%s "
            "promotion_build_number=%s",
            deployment.id,
            deployment.component,
            deployment.source_build_number,
            promotion.number,
        )
        self._record_promotion(deployment, promotion, stage, release)
        return True

    def _record_poll_failure(
        self,
        deployment: Deployment,
        exc: Exception,
        stage: str,
        release: ReleaseBundle | None,
        *,
        code: str | None = None,
    ) -> None:
        """Note that we could not reach Drone, without changing the deployment.

        An event is emitted only when the condition changes, otherwise a UI that
        polls every few seconds would bury the timeline in identical lines.
        """
        error_code = code or getattr(exc, "error_code", "DRONE_UNAVAILABLE")
        first_occurrence = deployment.poll_error_code != error_code
        deployment.poll_error_code = error_code
        deployment.poll_error_message = str(exc) or exc.__class__.__name__
        deployment.poll_failure_count = (deployment.poll_failure_count or 0) + 1
        deployment.updated_at = utc_now()
        if first_occurrence:
            self.events.append(
                release=release,
                deployment=deployment,
                stage=stage,
                event_type=WorkflowEventType.UPSTREAM_UNAVAILABLE.value,
                status=deployment.status,
                error_code=error_code,
                message=deployment.poll_error_message,
            )
        logger.warning(
            "Deployment poll failed, keeping status deployment_id=%s status=%s "
            "error_code=%s consecutive=%s",
            deployment.id,
            deployment.status,
            error_code,
            deployment.poll_failure_count,
        )

    def _clear_poll_failure(
        self, deployment: Deployment, stage: str, release: ReleaseBundle | None
    ) -> None:
        if deployment.poll_error_code is None:
            return
        recovered_from = deployment.poll_error_code
        deployment.poll_error_code = None
        deployment.poll_error_message = None
        deployment.poll_failure_count = 0
        deployment.updated_at = utc_now()
        self.events.append(
            release=release,
            deployment=deployment,
            stage=stage,
            event_type=WorkflowEventType.UPSTREAM_RECOVERED.value,
            status=deployment.status,
            message=f"Drone is reachable again after {recovered_from}",
        )

    def _record_promotion(
        self,
        deployment: Deployment,
        promotion: DroneBuild,
        stage: str,
        release: ReleaseBundle | None,
    ) -> None:
        deployment.promotion_build_number = promotion.number
        deployment.status = DeploymentStatus.DEPLOYING.value
        deployment.current_stage = wait_stage(deployment.component).value
        deployment.updated_at = utc_now()
        if release:
            release.status = "DEPLOYING"
            release.deployment_status = "DEPLOYING"
            release.current_stage = deployment.current_stage
            release.updated_at = deployment.updated_at
        self.events.append(
            release=release,
            deployment=deployment,
            stage=stage,
            event_type=WorkflowEventType.STAGE_SUCCEEDED.value,
            status=deployment.status,
        )
        self.events.append(
            release=release,
            deployment=deployment,
            stage=stage,
            event_type=WorkflowEventType.PROMOTION_CREATED.value,
            status=deployment.status,
            message=f"Drone promotion build #{promotion.number} created",
        )
        self.events.append(
            release=release,
            deployment=deployment,
            stage=deployment.current_stage,
            event_type=WorkflowEventType.STAGE_STARTED.value,
            status=deployment.status,
        )
        logger.info(
            "Promotion created release_id=%s deployment_id=%s component=%s stage=%s repo=%s/%s source_build_number=%s promotion_build_number=%s target=%s",
            deployment.release_bundle_id,
            deployment.id,
            deployment.component,
            deployment.current_stage,
            deployment.drone_owner,
            deployment.drone_repository,
            deployment.source_build_number,
            deployment.promotion_build_number,
            deployment.target,
        )

    def refresh(self, deployment: Deployment, release: ReleaseBundle | None = None) -> Deployment:
        if deployment.status in TERMINAL_DEPLOYMENT_STATUSES:
            return deployment
        stage = deployment.current_stage or wait_stage(deployment.component).value
        if self._timed_out(deployment):
            self.fail(
                deployment,
                failed_stage=stage,
                error_code="WORKFLOW_TIMEOUT",
                error_message="Deployment exceeded the configured workflow timeout",
                release=release,
            )
            return deployment
        if deployment.promotion_build_number is None:
            # Either a promote whose outcome Drone never confirmed, or a claim that
            # was interrupted before the request went out.  Both are resolved by
            # looking for the promotion, never by sending another one.
            self._reconcile_promotion(deployment, stage, release)
            return deployment
        try:
            build = self.builds.get_build(
                self.registry.for_deployment(deployment), deployment.promotion_build_number
            )
        except Exception as exc:  # noqa: BLE001 - classified immediately below
            # Failing to reach Drone is not evidence about the deployment.  Only
            # Drone's own verdict on the build, or the workflow timeout above, may
            # fail it; anything else leaves the state alone and tries again.
            self._record_poll_failure(deployment, exc, stage, release)
            return deployment

        self._clear_poll_failure(deployment, stage, release)
        status = build.status.lower()
        self.events.append(
            release=release,
            deployment=deployment,
            stage=stage,
            event_type=WorkflowEventType.DRONE_STATUS_CHANGED.value,
            status=status,
            message=f"Drone promotion build #{build.number} is {status}",
        )
        if status in {"pending", "running", "blocked"}:
            deployment.status = DeploymentStatus.DEPLOYING.value
            deployment.updated_at = utc_now()
        elif status == "success":
            self.succeed(deployment, release=release)
        else:
            error_code = "DRONE_BUILD_KILLED" if status == "killed" else "DRONE_BUILD_FAILED"
            self.fail(
                deployment,
                failed_stage=stage,
                error_code=error_code,
                error_message=f"Drone promotion build #{build.number} {status}",
                release=release,
            )
        return deployment

    def succeed(self, deployment: Deployment, release: ReleaseBundle | None = None) -> None:
        now = utc_now()
        stage = deployment.current_stage or wait_stage(deployment.component).value
        deployment.status = DeploymentStatus.SUCCESS.value
        deployment.current_stage = None
        deployment.finished_at = now
        deployment.updated_at = now
        self.events.append(
            release=release,
            deployment=deployment,
            stage=stage,
            event_type=WorkflowEventType.STAGE_SUCCEEDED.value,
            status=deployment.status,
        )

    def fail(
        self,
        deployment: Deployment,
        *,
        failed_stage: str,
        error_code: str,
        error_message: str,
        release: ReleaseBundle | None = None,
    ) -> None:
        now = utc_now()
        deployment.status = DeploymentStatus.FAILED.value
        deployment.current_stage = None
        deployment.failed_stage = failed_stage
        deployment.error_code = error_code
        deployment.error_message = error_message
        deployment.failed_at = now
        deployment.finished_at = now
        deployment.updated_at = now
        self.events.append(
            release=release,
            deployment=deployment,
            stage=failed_stage,
            event_type=WorkflowEventType.STAGE_FAILED.value,
            status=deployment.status,
            error_code=error_code,
            message=error_message,
        )
        logger.error(
            "Deployment failed release_id=%s deployment_id=%s component=%s stage=%s status=%s error_code=%s",
            deployment.release_bundle_id,
            deployment.id,
            deployment.component,
            failed_stage,
            deployment.status,
            error_code,
        )

    def cancel(self, deployment: Deployment, reason: str, release: ReleaseBundle) -> None:
        now = utc_now()
        deployment.status = DeploymentStatus.CANCELLED.value
        deployment.current_stage = None
        deployment.cancel_reason = reason
        deployment.cancelled_at = now
        deployment.finished_at = now
        deployment.updated_at = now
        self.events.append(
            release=release,
            deployment=deployment,
            stage=promote_stage(deployment.component).value,
            event_type=WorkflowEventType.DEPLOYMENT_CANCELLED.value,
            status=deployment.status,
            message=reason,
        )

    def reset_for_retry(self, deployment: Deployment, release: ReleaseBundle | None = None) -> None:
        """Return a failed or cancelled deployment to WAITING so it can run again.

        The row is reset rather than replaced so a bundle keeps exactly one
        deployment per component; the attempt that failed survives in the event
        history, which is append-only.
        """
        previous_status = deployment.status
        previous_error = deployment.error_code
        now = utc_now()
        deployment.status = DeploymentStatus.WAITING.value
        deployment.current_stage = None
        deployment.promotion_build_number = None
        deployment.failed_stage = None
        deployment.error_code = None
        deployment.error_message = None
        deployment.cancel_reason = None
        deployment.cancelled_at = None
        deployment.failed_at = None
        deployment.finished_at = None
        deployment.poll_error_code = None
        deployment.poll_error_message = None
        deployment.poll_failure_count = 0
        # started_at is cleared so the workflow timeout measures this attempt.
        deployment.started_at = None
        deployment.updated_at = now
        self.events.append(
            release=release,
            deployment=deployment,
            stage=promote_stage(deployment.component).value,
            event_type=WorkflowEventType.DEPLOYMENT_RETRIED.value,
            status=deployment.status,
            message=(
                f"Retrying after {previous_status}"
                + (f" ({previous_error})" if previous_error else "")
            ),
        )

    def get(self, deployment_id: str) -> Deployment:
        deployment = self.db.scalar(
            select(Deployment)
            .options(selectinload(Deployment.events))
            .where(Deployment.id == deployment_id)
        )
        if deployment is None:
            raise DeploymentNotFoundError
        return deployment

    def list_events(self, deployment_id: str) -> list[WorkflowEvent]:
        self.get(deployment_id)
        return list(
            self.db.scalars(
                select(WorkflowEvent)
                .where(WorkflowEvent.deployment_id == deployment_id)
                .order_by(WorkflowEvent.created_at.asc(), WorkflowEvent.id.asc())
            ).all()
        )

    def list(
        self, filters: DeploymentFilters, *, limit: int, offset: int
    ) -> tuple[list[Deployment], int]:
        conditions = []
        if filters.component:
            conditions.append(Deployment.component == filters.component)
        if filters.status:
            conditions.append(Deployment.status == filters.status.value)
        if filters.target:
            conditions.append(Deployment.target == filters.target)
        if filters.release_bundle_id:
            conditions.append(Deployment.release_bundle_id == filters.release_bundle_id)
        total = self.db.scalar(select(func.count()).select_from(Deployment).where(*conditions)) or 0
        items = list(
            self.db.scalars(
                select(Deployment)
                .where(*conditions)
                .order_by(Deployment.created_at.desc(), Deployment.id.desc())
                .limit(limit)
                .offset(offset)
            ).all()
        )
        return items, total

    def _timed_out(self, deployment: Deployment) -> bool:
        if deployment.started_at is None:
            return False
        started = deployment.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return (
            datetime.now(timezone.utc) - started
        ).total_seconds() > self.settings.deployment_timeout_seconds
