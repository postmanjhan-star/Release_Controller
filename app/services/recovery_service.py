"""Drive and repair work that nothing else is watching.

Before this, the server only advanced deployments that a schedule had created.
Anything started from the UI depended on the browser polling /refresh, so closing
the tab left a deployment at DEPLOYING for ever -- the workflow timeout could not
even fire, because it is only evaluated during a refresh.  Restarts were worse:
a publish interrupted between components stayed PUBLISHING, and a schedule
interrupted after claiming stayed RUNNING, both permanently.

Three sweeps, all idempotent and safe to run concurrently with a user's own
refresh, because every state transition they trigger is a conditional UPDATE:

* resume  -- advance every non-terminal deployment, whoever created it
* publish -- rebuild the true publish status of an interrupted publish
* claim   -- release schedules whose runner died mid-flight

The publish and claim sweeps only ever *record* what already happened.  Neither
retries an upstream write, because neither can know whether the original one
landed.
"""

import logging
from datetime import timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.orchestration import Deployment, ReleaseBundle
from app.db.models.scheduling import DeploymentSchedule
from app.domain.orchestration.value_objects import (
    ActorSource,
    DeploymentStatus,
    PublishRecordStatus,
    PublishStatus,
    WorkflowEventType,
    WorkflowStage,
)
from app.domain.scheduling.value_objects import ScheduleStatus
from app.domain.shared.time import utc_now
from app.services.workflow_service import OrchestrationWorkflowService

logger = logging.getLogger(__name__)

RESUMABLE_DEPLOYMENT_STATUSES = (
    DeploymentStatus.WAITING.value,
    DeploymentStatus.PROMOTING.value,
    DeploymentStatus.DEPLOYING.value,
)
PUBLISH_INTERRUPTED_CODE = "PUBLISH_INTERRUPTED"
SCHEDULE_INTERRUPTED_CODE = "SCHEDULE_INTERRUPTED"


class RecoveryService:
    """Owns the sweeps.  Constructed per pass; holds no state between them."""

    def __init__(self, session_factory, orchestrator_factory, settings: Settings) -> None:
        self.session_factory = session_factory
        # (db) -> ReleaseOrchestrator, injected so this module does not depend on
        # how a Drone client is built.
        self.orchestrator_factory = orchestrator_factory
        self.settings = settings

    def run(self) -> dict[str, int]:
        return {
            "deployments": self.resume_deployments(),
            "publishes": self.reconcile_publishes(),
            "schedules": self.release_stale_schedule_claims(),
        }

    # -- deployments -------------------------------------------------------

    def resume_deployments(self) -> int:
        """Advance every deployment that is still in flight.

        A deployment is only polled once it has been quiet for
        deployment_poll_interval_seconds, so a UI that is already polling keeps
        ownership and Drone is not asked the same question twice a second.
        """
        quiet_before = utc_now() - timedelta(seconds=self.settings.deployment_poll_interval_seconds)
        with self.session_factory() as db:
            pairs = [
                (row.id, row.release_bundle_id)
                for row in db.execute(
                    select(Deployment.id, Deployment.release_bundle_id)
                    .where(
                        Deployment.status.in_(RESUMABLE_DEPLOYMENT_STATUSES),
                        Deployment.updated_at <= quiet_before,
                    )
                    .order_by(Deployment.updated_at.asc())
                    .limit(self.settings.recovery_batch_size)
                ).all()
            ]

        # A bundle is advanced once, not once per component.
        bundle_ids: list[str] = []
        standalone_ids: list[str] = []
        for deployment_id, bundle_id in pairs:
            if bundle_id:
                if bundle_id not in bundle_ids:
                    bundle_ids.append(bundle_id)
            else:
                standalone_ids.append(deployment_id)

        resumed = 0
        for bundle_id in bundle_ids:
            resumed += self._resume(lambda o, i=bundle_id: o.refresh_bundle(i), "bundle", bundle_id)
        for deployment_id in standalone_ids:
            resumed += self._resume(
                lambda o, i=deployment_id: o.refresh_standalone(i), "deployment", deployment_id
            )
        return resumed

    def _resume(self, action, kind: str, identifier: str) -> int:
        with self.session_factory() as db:
            try:
                action(self.orchestrator_factory(db))
                return 1
            except Exception:
                db.rollback()
                logger.exception("Recovery could not resume %s=%s", kind, identifier)
                return 0

    # -- publishes ---------------------------------------------------------

    def reconcile_publishes(self) -> int:
        """Rebuild the publish status of anything left mid-flight.

        publish_bundle commits after each component so a finished component
        survives a later failure.  The cost is that dying between components
        leaves PUBLISHING behind for ever.  The publish records are the record of
        what actually reached Gitea, so the true status is derived from them --
        never by publishing again.
        """
        stale_before = utc_now() - timedelta(seconds=self.settings.publish_timeout_seconds)
        with self.session_factory() as db:
            bundle_ids = list(
                db.scalars(
                    select(ReleaseBundle.id)
                    .where(
                        ReleaseBundle.publish_status == PublishStatus.PUBLISHING.value,
                        or_(
                            ReleaseBundle.publish_started_at.is_(None),
                            ReleaseBundle.publish_started_at <= stale_before,
                        ),
                    )
                    .limit(self.settings.recovery_batch_size)
                ).all()
            )
            deployment_ids = list(
                db.scalars(
                    select(Deployment.id)
                    .where(
                        Deployment.publish_status == PublishStatus.PUBLISHING.value,
                        Deployment.release_bundle_id.is_(None),
                        or_(
                            Deployment.publish_started_at.is_(None),
                            Deployment.publish_started_at <= stale_before,
                        ),
                    )
                    .limit(self.settings.recovery_batch_size)
                ).all()
            )

        reconciled = 0
        for bundle_id in bundle_ids:
            reconciled += self._reconcile_bundle_publish(bundle_id)
        for deployment_id in deployment_ids:
            reconciled += self._reconcile_deployment_publish(deployment_id)
        return reconciled

    def _reconcile_bundle_publish(self, bundle_id: str) -> int:
        with self.session_factory() as db:
            bundle = db.get(ReleaseBundle, bundle_id)
            if bundle is None or bundle.publish_status != PublishStatus.PUBLISHING.value:
                return 0
            records = [
                record
                for deployment in bundle.deployments
                for record in deployment.publish_records
                if record.version == bundle.version
            ]
            published = sum(
                record.status == PublishRecordStatus.PUBLISHED.value for record in records
            )
            expected = len(bundle.deployments)
            status, message = self._verdict(published, expected)
            self._apply_publish_verdict(db, bundle, status, message, release=bundle)
            db.commit()
            logger.warning(
                "Interrupted bundle publish reconciled release_id=%s version=%s "
                "published=%s/%s status=%s",
                bundle_id,
                bundle.version,
                published,
                expected,
                status,
            )
            return 1

    def _reconcile_deployment_publish(self, deployment_id: str) -> int:
        with self.session_factory() as db:
            deployment = db.get(Deployment, deployment_id)
            if deployment is None or deployment.publish_status != PublishStatus.PUBLISHING.value:
                return 0
            records = [
                record
                for record in deployment.publish_records
                if record.version == deployment.version
            ]
            published = sum(
                record.status == PublishRecordStatus.PUBLISHED.value for record in records
            )
            status, message = self._verdict(published, 1)
            self._apply_publish_verdict(db, deployment, status, message, deployment=deployment)
            db.commit()
            logger.warning(
                "Interrupted publish reconciled deployment_id=%s version=%s status=%s",
                deployment_id,
                deployment.version,
                status,
            )
            return 1

    @staticmethod
    def _verdict(published: int, expected: int) -> tuple[str, str]:
        if expected > 0 and published == expected:
            return (
                PublishStatus.PUBLISHED.value,
                "Publish was interrupted but every component had already reached Gitea",
            )
        if published > 0:
            return (
                PublishStatus.PARTIAL_FAILURE.value,
                f"Publish was interrupted with {published} of {expected} components published",
            )
        return (
            PublishStatus.FAILED.value,
            "Publish was interrupted before any component reached Gitea",
        )

    def _apply_publish_verdict(
        self,
        db: Session,
        subject,
        status: str,
        message: str,
        *,
        release: ReleaseBundle | None = None,
        deployment: Deployment | None = None,
    ) -> None:
        now = utc_now()
        subject.publish_status = status
        subject.updated_at = now
        if status == PublishStatus.PUBLISHED.value:
            subject.published_at = subject.published_at or now
            subject.publish_failed_at = None
            subject.publish_error_code = None
            subject.publish_error_message = None
        else:
            subject.publish_failed_at = now
            subject.publish_error_code = PUBLISH_INTERRUPTED_CODE
            subject.publish_error_message = message
        OrchestrationWorkflowService(
            db, actor=ActorSource.SYSTEM.value, actor_source=ActorSource.SYSTEM.value
        ).append(
            release=release,
            deployment=deployment,
            stage=WorkflowStage.COMPLETE_PUBLISH.value,
            event_type=WorkflowEventType.PUBLISH_RECONCILED.value,
            status=status,
            error_code=None
            if status == PublishStatus.PUBLISHED.value
            else PUBLISH_INTERRUPTED_CODE,
            message=message,
        )

    # -- schedules ---------------------------------------------------------

    def release_stale_schedule_claims(self) -> int:
        """Fail schedules whose runner died after claiming them.

        The claim is not handed back to be retried.  _run_one commits the bundle
        before it records the link, so a dead runner may already have promoted to
        production; re-running would be a second deployment.  The schedule is
        marked failed with a message that says where to look instead.
        """
        stale_before = utc_now() - timedelta(seconds=self.settings.schedule_claim_timeout_seconds)
        with self.session_factory() as db:
            stale_ids = list(
                db.scalars(
                    select(DeploymentSchedule.id)
                    .where(
                        DeploymentSchedule.status == ScheduleStatus.RUNNING.value,
                        DeploymentSchedule.release_bundle_id.is_(None),
                        DeploymentSchedule.deployment_id.is_(None),
                        or_(
                            DeploymentSchedule.started_at.is_(None),
                            DeploymentSchedule.started_at <= stale_before,
                        ),
                    )
                    .limit(self.settings.recovery_batch_size)
                ).all()
            )

        released = 0
        for schedule_id in stale_ids:
            with self.session_factory() as db:
                now = utc_now()
                result = db.execute(
                    update(DeploymentSchedule)
                    .where(
                        DeploymentSchedule.id == schedule_id,
                        DeploymentSchedule.status == ScheduleStatus.RUNNING.value,
                        DeploymentSchedule.release_bundle_id.is_(None),
                        DeploymentSchedule.deployment_id.is_(None),
                    )
                    .values(
                        status=ScheduleStatus.FAILED.value,
                        error_message=(
                            "Interrupted while starting the deployment. A promotion may "
                            "already have been created in Drone; check the deployments "
                            "list for this target before scheduling it again."
                        ),
                        finished_at=now,
                        updated_at=now,
                    )
                    .execution_options(synchronize_session=False)
                )
                db.commit()
                if result.rowcount == 1:
                    released += 1
                    logger.warning(
                        "Released stale schedule claim schedule_id=%s error_code=%s",
                        schedule_id,
                        SCHEDULE_INTERRUPTED_CODE,
                    )
        return released
