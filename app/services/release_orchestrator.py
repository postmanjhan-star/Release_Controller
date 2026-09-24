import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings
from app.db.models.orchestration import Deployment, ReleaseBundle, WorkflowEvent
from app.db.models.registry import Project, ProjectComponent
from app.domain.orchestration.value_objects import (
    ActorSource,
    BundleStatus,
    Component,
    DeploymentStatus,
    PublishStatus,
    ReleaseMode,
    WorkflowEventType,
    WorkflowStage,
)
from app.domain.shared.time import utc_now
from app.integrations.drone.schemas import DroneBuild
from app.schemas.drone import PromoteRequest
from app.schemas.orchestration import BundlePromoteRequest
from app.services.attachment_service import UploadedAttachment, apply_attachment
from app.services.component_registry import (
    ComponentRegistry,
    ordered_by_component,
    project_default_target,
)
from app.services.deployment_service import (
    DeploymentService,
    DuplicatePromotionError,
)
from app.services.drone_build_service import DroneBuildService
from app.services.workflow_service import OrchestrationWorkflowService

logger = logging.getLogger(__name__)

_VALIDATE_STAGES = {
    Component.FRONTEND.value: WorkflowStage.VALIDATE_FRONTEND.value,
    Component.BACKEND.value: WorkflowStage.VALIDATE_BACKEND.value,
}


def _target_for(component: ProjectComponent, release_target: str) -> str:
    """Where this component actually promotes to.

    A component that declares its own promote target keeps it whatever the
    release asks for: that declaration is what lets two components share one
    repository, and honouring the release target instead would put them both in
    the same promotion slot.  The release target is the fallback for everyone else.
    """
    return component.promote_target_override or release_target


def _validate_stage(component_key: str) -> str:
    """frontend and backend keep their historical stage names; anything else is
    recorded generically, with the component named on the event."""
    return _VALIDATE_STAGES.get(component_key, WorkflowStage.VALIDATE_COMPONENT.value)


class BundleNotFoundError(Exception):
    pass


class BundleNotRetryableError(Exception):
    pass


class ComponentNotSelectableError(Exception):
    """A release named a component that exists but must not be deployed."""


class ReleaseOrchestrator:
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
        self.deployments = DeploymentService(
            db, builds, settings, actor=actor, actor_source=actor_source
        )
        self.events = OrchestrationWorkflowService(db, actor=actor, actor_source=actor_source)

    def promote_standalone(
        self,
        component: ProjectComponent,
        build_number: int,
        payload: PromoteRequest,
        *,
        attachment: UploadedAttachment | None = None,
    ) -> Deployment:
        registered = component
        target = _target_for(registered, payload.target or project_default_target(registered))
        build = self.builds.validate_promotable(registered, build_number)
        self.deployments.assert_not_duplicate(registered, build_number, target)
        deployment = self.deployments.create(
            registered,
            build,
            target,
            requested_by=payload.requested_by,
        )
        apply_attachment(deployment, attachment)
        validate_stage = _validate_stage(registered.key)
        self.events.append(
            deployment=deployment,
            stage=validate_stage,
            event_type=WorkflowEventType.STAGE_STARTED.value,
        )
        self.events.append(
            deployment=deployment,
            stage=validate_stage,
            event_type=WorkflowEventType.STAGE_SUCCEEDED.value,
            status=DeploymentStatus.PROMOTING.value,
        )
        # Persist the intent before going upstream.  promote() claims the
        # deployment and commits that claim before it calls Drone, so no write
        # lock is held across the network round trip.
        self.db.commit()
        self.deployments.promote(deployment)
        return self.deployments.get(deployment.id)

    def create_bundle(
        self, payload: BundlePromoteRequest, *, attachment: UploadedAttachment | None = None
    ) -> ReleaseBundle:
        """Start a release covering the components the caller selected.

        Any subset of a project's components, in the order their positions put
        them.  Nothing is promoted until every source build and every duplicate
        guard has passed -- the v2.2 atomic pre-validation, now over N components
        instead of exactly two.
        """
        project = self.registry.project(payload.project_id)
        selection = self._selected_components(project, payload)
        target = payload.target or project.default_target

        validated: list[tuple[ProjectComponent, DroneBuild]] = []
        for component, build_number in selection:
            build = self.builds.validate_promotable(component, build_number)
            self.deployments.assert_not_duplicate(
                component, build.number, _target_for(component, target)
            )
            validated.append((component, build))

        now = utc_now()
        bundle = ReleaseBundle(
            project_id=project.id,
            mode=ReleaseMode.BUNDLE.value,
            selected_component_keys=",".join(component.key for component, _ in validated),
            target=target,
            status=BundleStatus.PENDING.value,
            deployment_status=BundleStatus.PENDING.value,
            workflow_instance_id=str(uuid.uuid4()),
            current_stage=WorkflowStage.CREATE_RELEASE_BUNDLE.value,
            requested_by=payload.requested_by,
            started_at=now,
        )
        apply_attachment(bundle, attachment)
        self.db.add(bundle)
        self.db.flush()

        created: list[Deployment] = []
        for component, build in validated:
            deployment = self.deployments.create(
                component,
                build,
                _target_for(component, target),
                bundle=bundle,
                requested_by=payload.requested_by,
            )
            created.append(deployment)
            stage = _validate_stage(component.key)
            self.events.append(
                release=bundle,
                deployment=deployment,
                stage=stage,
                event_type=WorkflowEventType.STAGE_STARTED.value,
            )
            self.events.append(
                release=bundle,
                deployment=deployment,
                stage=stage,
                event_type=WorkflowEventType.STAGE_SUCCEEDED.value,
            )
        self.events.append(
            release=bundle,
            stage=WorkflowStage.CREATE_RELEASE_BUNDLE.value,
            event_type=WorkflowEventType.STAGE_STARTED.value,
        )
        self.events.append(
            release=bundle,
            stage=WorkflowStage.CREATE_RELEASE_BUNDLE.value,
            event_type=WorkflowEventType.STAGE_SUCCEEDED.value,
        )
        # The whole bundle is durable before Drone is touched.  A crash between
        # here and the promotion leaves every deployment WAITING, which is a
        # resumable state rather than a lost transaction.
        self.db.commit()
        self._advance(bundle)
        self.db.commit()
        logger.info(
            "Bundle started release_id=%s components=%s target=%s status=%s",
            bundle.id,
            [(item.component, item.source_build_number) for item in created],
            target,
            bundle.status,
        )
        return self.get_bundle(bundle.id)

    def _selected_components(
        self, project: Project, payload: BundlePromoteRequest
    ) -> list[tuple[ProjectComponent, int]]:
        """Resolve the request to (component, build number) pairs, in deploy order.

        Accepts the v2.2 body as well, where the two build numbers were named
        after the two components that used to be the only ones there were.
        """
        if payload.components:
            requested = [(item.key, item.build_number) for item in payload.components]
        else:
            requested = [
                (key, build_number)
                for key, build_number in (
                    (Component.BACKEND.value, payload.backend_build_number),
                    (Component.FRONTEND.value, payload.frontend_build_number),
                )
                if build_number is not None
            ]

        selection: list[tuple[ProjectComponent, int]] = []
        for key, build_number in requested:
            component = self.registry.for_key(key, project)
            if not component.is_active:
                raise ComponentNotSelectableError(
                    f"Component {key!r} is inactive and cannot be released. Reactivate "
                    "it under Projects first."
                )
            selection.append((component, build_number))
        # Position is the deployment order, whatever order the caller listed them in.
        selection.sort(key=lambda item: item[0].position)
        return selection

    def retry_bundle(self, release_id: str) -> ReleaseBundle:
        """Drive a partly-failed bundle forward by re-running only what failed.

        A bundle that ends PARTIAL_FAILURE has already put a component into
        production, so it had no way out: refresh_bundle treats it as terminal and
        there was no other entry point.  Rolling the successful part back is the
        other defensible answer, but nothing in the system can roll a component
        back yet, so forward is the only move that exists.

        Failed deployments are reset in place rather than replaced, so the bundle
        keeps exactly one deployment per component.  Nothing is lost by that: the
        attempt that failed is in the (append-only) event history.
        """
        bundle = self.get_bundle(release_id)
        deployment_status = bundle.deployment_status or bundle.status
        if deployment_status not in {
            BundleStatus.FAILED.value,
            BundleStatus.PARTIAL_FAILURE.value,
        }:
            raise BundleNotRetryableError(
                f"Only a failed or partly failed bundle can be retried; this one is "
                f"{deployment_status}"
            )
        if bundle.publish_status != PublishStatus.NOT_PUBLISHED.value:
            raise BundleNotRetryableError(
                "This bundle has already been published; retrying the deployment "
                "would change what the published version refers to"
            )

        retried = [
            deployment
            for deployment in bundle.deployments
            if deployment.status != DeploymentStatus.SUCCESS.value
        ]
        if not retried:
            raise BundleNotRetryableError("Every component already succeeded")

        now = utc_now()
        for deployment in retried:
            self.deployments.reset_for_retry(deployment, bundle)
        bundle.status = BundleStatus.PENDING.value
        bundle.deployment_status = BundleStatus.PENDING.value
        bundle.current_stage = WorkflowStage.CREATE_RELEASE_BUNDLE.value
        bundle.failed_stage = None
        bundle.error_code = None
        bundle.error_message = None
        bundle.failed_at = None
        bundle.finished_at = None
        bundle.updated_at = now
        self.events.append(
            release=bundle,
            stage=WorkflowStage.CREATE_RELEASE_BUNDLE.value,
            event_type=WorkflowEventType.DEPLOYMENT_RETRIED.value,
            status=bundle.status,
            message="Retrying " + ", ".join(sorted(d.component for d in retried)),
        )
        self.db.commit()

        # Order still holds on a retry: a later component may depend on an
        # earlier one, so the reset deployments resume from the front.
        self._advance(bundle)
        self.db.commit()
        logger.info(
            "Bundle retry started release_id=%s components=%s",
            bundle.id,
            [d.component for d in retried],
        )
        return self.get_bundle(bundle.id)

    def refresh_bundle(self, release_id: str) -> ReleaseBundle:
        bundle = self.get_bundle(release_id)
        if bundle.status in {
            BundleStatus.SUCCESS.value,
            BundleStatus.FAILED.value,
            BundleStatus.PARTIAL_FAILURE.value,
        }:
            return bundle
        self._advance(bundle)
        self.db.commit()
        return self.get_bundle(bundle.id)

    def _advance(self, bundle: ReleaseBundle) -> None:
        """Move the release forward by whatever its current step allows.

        One pass walks the components in order and stops at the first one that is
        not finished: polling it if it is running, promoting it if it is waiting.
        A step that succeeds lets the walk continue to the next one in the same
        pass, which is how a backend finishing immediately starts the frontend.

        This replaces a hand-written two-step machine, and is behaviourally
        identical for the two-component case.  What it does not change is *how* a
        promotion happens: promote() still claims the deployment and commits that
        claim before any HTTP, so no write lock spans a Drone call.
        """
        steps = self._ordered(bundle)
        for step in steps:
            if step.status in {DeploymentStatus.PROMOTING.value, DeploymentStatus.DEPLOYING.value}:
                self.deployments.refresh(step, bundle)
            elif step.status == DeploymentStatus.WAITING.value:
                self.deployments.promote(step, bundle)

            if step.status == DeploymentStatus.FAILED.value:
                self._fail_bundle(bundle, step, steps)
                return
            if step.status != DeploymentStatus.SUCCESS.value:
                # Still promoting, deploying, or cancelled: nothing further to do
                # in this pass.
                return
        self._finish_success(bundle)

    def _ordered(self, bundle: ReleaseBundle) -> list[Deployment]:
        return ordered_by_component(self.db, list(bundle.deployments))

    def get_bundle(self, release_id: str) -> ReleaseBundle:
        bundle = self.db.scalar(
            select(ReleaseBundle)
            .options(selectinload(ReleaseBundle.deployments))
            .where(ReleaseBundle.id == release_id)
        )
        if bundle is None:
            raise BundleNotFoundError
        return bundle

    def refresh_standalone(self, deployment_id: str) -> Deployment:
        deployment = self.deployments.get(deployment_id)
        if deployment.release_bundle_id is not None:
            raise DuplicatePromotionError("Refresh the parent release bundle instead")
        self.deployments.refresh(deployment)
        if deployment.status == DeploymentStatus.SUCCESS.value:
            self.events.append(
                deployment=deployment,
                stage=WorkflowStage.COMPLETE_DEPLOYMENT.value,
                event_type=WorkflowEventType.RELEASE_COMPLETED.value,
                status=deployment.status,
            )
            self.events.append(
                deployment=deployment,
                stage=WorkflowStage.WAIT_PUBLISH_REQUEST.value,
                event_type=WorkflowEventType.STAGE_STARTED.value,
                status=deployment.status,
            )
        self.db.commit()
        return self.deployments.get(deployment.id)

    def list_bundles(
        self,
        *,
        status: BundleStatus | None,
        target: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[ReleaseBundle], int]:
        conditions = []
        if status:
            conditions.append(ReleaseBundle.status == status.value)
        if target:
            conditions.append(ReleaseBundle.target == target)
        total = (
            self.db.scalar(select(func.count()).select_from(ReleaseBundle).where(*conditions)) or 0
        )
        bundles = list(
            self.db.scalars(
                select(ReleaseBundle)
                .options(selectinload(ReleaseBundle.deployments))
                .where(*conditions)
                .order_by(ReleaseBundle.created_at.desc(), ReleaseBundle.id.desc())
                .limit(limit)
                .offset(offset)
            ).all()
        )
        return bundles, total

    def list_events(self, release_id: str) -> list[WorkflowEvent]:
        self.get_bundle(release_id)
        return list(
            self.db.scalars(
                select(WorkflowEvent)
                .where(WorkflowEvent.release_bundle_id == release_id)
                .order_by(WorkflowEvent.created_at.asc(), WorkflowEvent.id.asc())
            ).all()
        )

    def _fail_bundle(
        self, bundle: ReleaseBundle, failed: Deployment, steps: list[Deployment]
    ) -> None:
        """Stop the release, and say whether anything of it reached production.

        FAILED and PARTIAL_FAILURE differ by exactly one thing -- whether some
        component already succeeded -- so they are one function.  With two
        components that reduces to the old pair: a backend failure leaves nothing
        succeeded (FAILED), a frontend failure follows a successful backend
        (PARTIAL_FAILURE).
        """
        for later in steps:
            if later.status == DeploymentStatus.WAITING.value:
                # Capitalised so the message reads the same as it always has
                # ("Backend deployment failed") for the components that existed
                # before components were configurable.
                reason = f"{failed.component.capitalize()} deployment failed"
                self.deployments.cancel(later, reason, bundle)
        succeeded = any(step.status == DeploymentStatus.SUCCESS.value for step in steps)
        status = BundleStatus.PARTIAL_FAILURE.value if succeeded else BundleStatus.FAILED.value
        now = utc_now()
        bundle.status = status
        bundle.deployment_status = status
        bundle.current_stage = None
        bundle.failed_stage = failed.failed_stage
        bundle.error_code = failed.error_code
        bundle.error_message = failed.error_message
        bundle.failed_at = now
        bundle.finished_at = now
        bundle.updated_at = now

    def _finish_success(self, bundle: ReleaseBundle) -> None:
        now = utc_now()
        bundle.status = BundleStatus.SUCCESS.value
        bundle.deployment_status = BundleStatus.SUCCESS.value
        bundle.current_stage = None
        bundle.finished_at = now
        bundle.updated_at = now
        self.events.append(
            release=bundle,
            stage=WorkflowStage.COMPLETE_DEPLOYMENT.value,
            event_type=WorkflowEventType.RELEASE_COMPLETED.value,
            status=bundle.status,
        )
        self.events.append(
            release=bundle,
            stage=WorkflowStage.WAIT_PUBLISH_REQUEST.value,
            event_type=WorkflowEventType.STAGE_STARTED.value,
            status=bundle.status,
        )
