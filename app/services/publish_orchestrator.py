from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings
from app.db.models.orchestration import Deployment, PublishRecord, ReleaseBundle
from app.domain.orchestration.value_objects import (
    ActorSource,
    BundleStatus,
    Component,
    DeploymentStatus,
    PublishRecordStatus,
    PublishStatus,
    WorkflowEventType,
    WorkflowStage,
)
from app.domain.shared.time import utc_now
from app.integrations.gitea.client import GiteaClient
from app.schemas.publish import PublishRequest
from app.services.component_registry import components_for, ordered_by_component
from app.services.deployment_service import DeploymentNotFoundError
from app.services.publish_service import (
    PublishConfigurationError,
    PublishService,
    TagAlreadyExistsDifferentCommit,
)
from app.services.release_orchestrator import BundleNotFoundError
from app.services.upstream_clients import GiteaClients
from app.services.workflow_service import OrchestrationWorkflowService

_PUBLISH_STAGES = {
    Component.FRONTEND.value: WorkflowStage.PUBLISH_FRONTEND_RELEASE.value,
    Component.BACKEND.value: WorkflowStage.PUBLISH_BACKEND_RELEASE.value,
}


def _publish_stage(component_key: str) -> str:
    """frontend and backend keep their historical stage names; anything else is
    recorded generically, with the component named on the event."""
    return _PUBLISH_STAGES.get(component_key, WorkflowStage.PUBLISH_COMPONENT_RELEASE.value)


class DeploymentNotPublishableError(Exception):
    pass


class PublishStateError(Exception):
    pass


class PublishOrchestrator:
    def __init__(
        self,
        db: Session,
        gitea: GiteaClients | GiteaClient,
        settings: Settings,
        *,
        actor: str | None = None,
        actor_source: str = ActorSource.SYSTEM.value,
    ) -> None:
        self.db = db
        self.actor = actor
        self.publish = PublishService(db, gitea, settings, actor=actor)
        self.events = OrchestrationWorkflowService(db, actor=actor, actor_source=actor_source)

    def publish_standalone(self, deployment_id: str, payload: PublishRequest) -> Deployment:
        deployment = self._get_deployment(deployment_id)
        if deployment.release_bundle_id is not None:
            raise PublishStateError("Publish the parent release bundle")
        if deployment.status != DeploymentStatus.SUCCESS.value:
            raise DeploymentNotPublishableError("Deployment is not publishable")
        self._assert_version(deployment.version, deployment.publish_status, payload.version)
        self._prepare_deployment(deployment, payload)
        conflict: TagAlreadyExistsDifferentCommit | None = None
        try:
            record = self.publish.publish_component(deployment, payload)
            self._record_component_result(deployment, record)
        except TagAlreadyExistsDifferentCommit as exc:
            conflict = exc
            record = self._record_for(deployment, payload.version)
            self._record_component_result(deployment, record)
        except PublishConfigurationError as exc:
            self._fail_without_record(deployment, exc.error_code, str(exc))
        self._finish_deployment_publish(deployment)
        self.db.commit()
        result = self._get_deployment(deployment.id)
        if conflict:
            raise conflict
        return result

    def publish_bundle(self, release_id: str, payload: PublishRequest) -> ReleaseBundle:
        bundle = self._get_bundle(release_id)
        deployment_status = bundle.deployment_status or bundle.status
        if deployment_status != BundleStatus.SUCCESS.value:
            raise DeploymentNotPublishableError("Deployment is not publishable")
        self._assert_version(bundle.version, bundle.publish_status, payload.version)
        now = utc_now()
        bundle.publish_status = PublishStatus.PUBLISHING.value
        bundle.version = payload.version
        bundle.release_name = payload.name
        bundle.release_notes = payload.release_notes
        bundle.publish_started_at = bundle.publish_started_at or now
        bundle.published_at = None
        bundle.publish_failed_at = None
        bundle.publish_error_code = None
        bundle.publish_error_message = None
        bundle.updated_at = now
        self.events.append(
            release=bundle,
            stage=WorkflowStage.WAIT_PUBLISH_REQUEST.value,
            event_type=WorkflowEventType.PUBLISH_REQUESTED.value,
            status=bundle.publish_status,
            message=f"Publish {payload.version} requested",
        )
        self._prepare_version_event(bundle)

        # Same order as the deployment ran in, so the timeline reads the same way
        # twice.  Already-published records are idempotently skipped by
        # PublishService.
        ordered = ordered_by_component(self.db, list(bundle.deployments))
        components = components_for(self.db, ordered)
        publishing = [
            deployment for deployment in ordered if components[deployment.id].publish_enabled
        ]
        if not publishing:
            raise PublishStateError(
                "No component in this release publishes a Gitea release. Turn "
                "publishing on for at least one of them, or there is nothing to do."
            )
        conflicts: list[TagAlreadyExistsDifferentCommit] = []
        for deployment in publishing:
            self._prepare_deployment(deployment, payload, emit_prepare=False)
            try:
                record = self.publish.publish_component(deployment, payload)
                self._record_component_result(deployment, record, bundle)
            except TagAlreadyExistsDifferentCommit as exc:
                conflicts.append(exc)
                record = self._record_for(deployment, payload.version)
                self._record_component_result(deployment, record, bundle)
            except PublishConfigurationError as exc:
                self._fail_without_record(deployment, exc.error_code, str(exc), bundle)
            # Preserve each repository outcome independently. In particular,
            # a completed backend publish must survive a later frontend failure.
            self.db.commit()

        self._aggregate_bundle(bundle, payload.version, expected=len(publishing))
        self.db.commit()
        result = self._get_bundle(bundle.id)
        if conflicts:
            raise conflicts[0]
        return result

    def _prepare_deployment(
        self, deployment: Deployment, payload: PublishRequest, *, emit_prepare: bool = True
    ) -> None:
        now = utc_now()
        deployment.version = payload.version
        deployment.publish_status = PublishStatus.PUBLISHING.value
        deployment.publish_started_at = deployment.publish_started_at or now
        deployment.updated_at = now
        self.events.append(
            deployment=deployment,
            stage=WorkflowStage.WAIT_PUBLISH_REQUEST.value,
            event_type=WorkflowEventType.PUBLISH_REQUESTED.value,
            status=deployment.publish_status,
            message=f"Publish {payload.version} requested",
        )
        if emit_prepare:
            self.events.append(
                deployment=deployment,
                stage=WorkflowStage.PREPARE_RELEASE_VERSION.value,
                event_type=WorkflowEventType.PUBLISH_STAGE_STARTED.value,
                status=deployment.publish_status,
            )
            self.events.append(
                deployment=deployment,
                stage=WorkflowStage.PREPARE_RELEASE_VERSION.value,
                event_type=WorkflowEventType.PUBLISH_STAGE_SUCCEEDED.value,
                status=deployment.publish_status,
                message=f"Version {payload.version} validated",
            )
        publish_stage = _publish_stage(deployment.component)
        self.events.append(
            deployment=deployment,
            stage=publish_stage,
            event_type=WorkflowEventType.PUBLISH_STAGE_STARTED.value,
            status=deployment.publish_status,
        )

    def _prepare_version_event(self, bundle: ReleaseBundle) -> None:
        self.events.append(
            release=bundle,
            stage=WorkflowStage.PREPARE_RELEASE_VERSION.value,
            event_type=WorkflowEventType.PUBLISH_STAGE_STARTED.value,
            status=bundle.publish_status,
        )
        self.events.append(
            release=bundle,
            stage=WorkflowStage.PREPARE_RELEASE_VERSION.value,
            event_type=WorkflowEventType.PUBLISH_STAGE_SUCCEEDED.value,
            status=bundle.publish_status,
            message=f"Version {bundle.version} validated",
        )

    def _record_component_result(
        self,
        deployment: Deployment,
        record: PublishRecord,
        bundle: ReleaseBundle | None = None,
    ) -> None:
        stage = _publish_stage(deployment.component)
        if record.status == PublishRecordStatus.PUBLISHED.value:
            already_exists = bool(getattr(record, "_already_exists", False))
            self.events.append(
                release=bundle,
                deployment=deployment,
                stage=stage,
                event_type=WorkflowEventType.PUBLISH_STAGE_SUCCEEDED.value,
                status=record.status,
                message=f"Gitea release {record.tag_name} is ready",
            )
            self.events.append(
                release=bundle,
                deployment=deployment,
                stage=stage,
                event_type=(
                    WorkflowEventType.GITEA_RELEASE_ALREADY_EXISTS.value
                    if already_exists
                    else WorkflowEventType.GITEA_RELEASE_CREATED.value
                ),
                status=record.status,
                message=record.gitea_release_url,
            )
        else:
            self.events.append(
                release=bundle,
                deployment=deployment,
                stage=stage,
                event_type=WorkflowEventType.PUBLISH_STAGE_FAILED.value,
                status=record.status,
                error_code=record.error_code,
                message=record.error_message,
            )

    def _finish_deployment_publish(self, deployment: Deployment) -> None:
        now = utc_now()
        if deployment.publish_status == PublishStatus.PUBLISHED.value:
            self.events.append(
                deployment=deployment,
                stage=WorkflowStage.COMPLETE_PUBLISH.value,
                event_type=WorkflowEventType.PUBLISH_COMPLETED.value,
                status=deployment.publish_status,
            )
        else:
            deployment.publish_failed_at = deployment.publish_failed_at or now
        deployment.updated_at = now

    def _aggregate_bundle(self, bundle: ReleaseBundle, version: str, *, expected: int) -> None:
        """Roll the per-repository outcomes up into one status.

        `expected` counts only the components that publish.  Using the number of
        deployments instead would leave any release containing a component with
        publishing turned off permanently PARTIAL_FAILURE, however well it went.
        """
        records = [
            item
            for deployment in bundle.deployments
            for item in deployment.publish_records
            if item.version == version
        ]
        published = sum(item.status == PublishRecordStatus.PUBLISHED.value for item in records)
        failed = sum(item.status == PublishRecordStatus.FAILED.value for item in records)
        now = utc_now()
        if published == expected and published > 0:
            bundle.publish_status = PublishStatus.PUBLISHED.value
            bundle.published_at = now
            bundle.publish_failed_at = None
            event_type = WorkflowEventType.PUBLISH_COMPLETED.value
            stage = WorkflowStage.COMPLETE_PUBLISH.value
        elif published > 0:
            bundle.publish_status = PublishStatus.PARTIAL_FAILURE.value
            bundle.publish_failed_at = now
            event_type = WorkflowEventType.PUBLISH_PARTIAL_FAILURE.value
            stage = WorkflowStage.COMPLETE_PUBLISH.value
        else:
            bundle.publish_status = PublishStatus.FAILED.value
            bundle.publish_failed_at = now
            event_type = WorkflowEventType.PUBLISH_STAGE_FAILED.value
            stage = WorkflowStage.COMPLETE_PUBLISH.value
        failures = [item for item in records if item.status == PublishRecordStatus.FAILED.value]
        if failures:
            bundle.publish_error_code = failures[0].error_code
            bundle.publish_error_message = failures[0].error_message
        else:
            deployment_failures = [
                item for item in bundle.deployments if item.publish_error_code is not None
            ]
            if deployment_failures:
                bundle.publish_error_code = deployment_failures[0].publish_error_code
                bundle.publish_error_message = deployment_failures[0].publish_error_message
        if not failures and failed == 0 and not bundle.publish_error_code:
            bundle.publish_error_code = None
            bundle.publish_error_message = None
        bundle.updated_at = now
        self.events.append(
            release=bundle,
            stage=stage,
            event_type=event_type,
            status=bundle.publish_status,
            error_code=bundle.publish_error_code,
            message=bundle.publish_error_message,
        )

    def _fail_without_record(
        self,
        deployment: Deployment,
        error_code: str,
        error_message: str,
        bundle: ReleaseBundle | None = None,
    ) -> None:
        now = utc_now()
        deployment.publish_status = PublishStatus.FAILED.value
        deployment.publish_failed_at = now
        deployment.publish_error_code = error_code
        deployment.publish_error_message = error_message
        deployment.updated_at = now
        stage = _publish_stage(deployment.component)
        self.events.append(
            release=bundle,
            deployment=deployment,
            stage=stage,
            event_type=WorkflowEventType.PUBLISH_STAGE_FAILED.value,
            status=deployment.publish_status,
            error_code=error_code,
            message=error_message,
        )

    def _record_for(self, deployment: Deployment, version: str) -> PublishRecord:
        return self.db.scalar(
            select(PublishRecord).where(
                PublishRecord.deployment_id == deployment.id,
                PublishRecord.version == version,
            )
        )  # type: ignore[return-value]

    # A publish attempt pins its version while it is in flight or partly done, so
    # a retry cannot straddle two versions.  A wholly failed attempt pins nothing:
    # the commonest reason to fail is TAG_ALREADY_EXISTS_DIFFERENT_COMMIT, where
    # choosing a new version is the only correct move, and the old rule refused
    # exactly that.
    VERSION_IS_PINNED_WHILE = frozenset(
        {
            PublishStatus.PUBLISHING.value,
            PublishStatus.PUBLISHED.value,
            PublishStatus.PARTIAL_FAILURE.value,
        }
    )

    @classmethod
    def _assert_version(cls, current: str | None, status: str, requested: str) -> None:
        if current and current != requested and status in cls.VERSION_IS_PINNED_WHILE:
            raise PublishStateError(
                f"Publish {current} is still in progress or partly published; "
                f"retry it with version {current} before starting a different one"
            )

    def _get_deployment(self, deployment_id: str) -> Deployment:
        deployment = self.db.scalar(
            select(Deployment)
            .options(selectinload(Deployment.publish_records))
            .where(Deployment.id == deployment_id)
        )
        if deployment is None:
            raise DeploymentNotFoundError
        return deployment

    def _get_bundle(self, release_id: str) -> ReleaseBundle:
        bundle = self.db.scalar(
            select(ReleaseBundle)
            .options(
                selectinload(ReleaseBundle.deployments).selectinload(Deployment.publish_records),
                selectinload(ReleaseBundle.publish_records),
            )
            .where(ReleaseBundle.id == release_id)
        )
        if bundle is None:
            raise BundleNotFoundError
        return bundle
