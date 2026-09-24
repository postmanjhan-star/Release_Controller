import logging
from datetime import timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.registry import ProjectComponent
from app.db.models.scheduling import DeploymentSchedule, ScheduleComponentBuild
from app.domain.orchestration.value_objects import ActorSource, Component, ReleaseMode
from app.domain.scheduling.value_objects import ScheduleStatus
from app.domain.shared.time import utc_now
from app.schemas.drone import PromoteRequest
from app.schemas.orchestration import BundlePromoteRequest, ComponentBuildSelection
from app.schemas.scheduling import ScheduleCreate
from app.services.attachment_service import UploadedAttachment, apply_attachment
from app.services.component_registry import ComponentRegistry
from app.services.notification_service import NotificationService
from app.services.release_orchestrator import ReleaseOrchestrator

logger = logging.getLogger(__name__)


class ScheduleNotFoundError(Exception):
    pass


class InvalidScheduleTransitionError(Exception):
    pass


class ScheduleService:
    def __init__(self, db: Session, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.registry = ComponentRegistry(db, settings)

    def create(
        self, payload: ScheduleCreate, *, attachment: UploadedAttachment | None = None
    ) -> DeploymentSchedule:
        project = self.registry.project(payload.project_id)
        selection = self._selected_components(project, payload)
        scheduled_for_utc = payload.scheduled_for.astimezone(timezone.utc)
        schedule = DeploymentSchedule(
            project_id=project.id,
            mode=payload.mode.value,
            selected_component_keys=",".join(component.key for component, _ in selection),
            target=payload.target or project.default_target,
            scheduled_for_utc=scheduled_for_utc,
            timezone=payload.timezone,
            requested_by=payload.requested_by,
            notification_recipients=",".join(payload.notification_recipients) or None,
            release_version=payload.release_version,
            release_notes=payload.release_notes,
        )
        apply_attachment(schedule, attachment)
        self.db.add(schedule)
        self.db.flush()
        for component, build_number in selection:
            schedule.component_builds.append(
                ScheduleComponentBuild(
                    component_id=component.id,
                    component_key=component.key,
                    build_number=build_number,
                )
            )
        self.db.flush()
        NotificationService(self.db, self.settings).enqueue_schedule_created(schedule)
        self.db.commit()
        self.db.refresh(schedule)
        return schedule

    def _selected_components(
        self, project, payload: ScheduleCreate
    ) -> list[tuple[ProjectComponent, int]]:
        """Resolve the request to (component, build number) pairs, in deploy order."""
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
        selection = [
            (self.registry.for_key(key, project), build_number) for key, build_number in requested
        ]
        selection.sort(key=lambda item: item[0].position)
        return selection

    def get(self, schedule_id: str) -> DeploymentSchedule:
        schedule = self.db.get(DeploymentSchedule, schedule_id)
        if schedule is None:
            raise ScheduleNotFoundError
        return schedule

    def list(
        self, *, status: ScheduleStatus | None, limit: int, offset: int
    ) -> tuple[list[DeploymentSchedule], int]:
        conditions = [DeploymentSchedule.status == status.value] if status else []
        total = (
            self.db.scalar(select(func.count()).select_from(DeploymentSchedule).where(*conditions))
            or 0
        )
        items = list(
            self.db.scalars(
                select(DeploymentSchedule)
                .where(*conditions)
                .order_by(DeploymentSchedule.scheduled_for_utc.desc(), DeploymentSchedule.id.desc())
                .limit(limit)
                .offset(offset)
            ).all()
        )
        return items, total

    def cancel(self, schedule_id: str) -> DeploymentSchedule:
        schedule = self.get(schedule_id)
        if schedule.status != ScheduleStatus.PENDING.value:
            raise InvalidScheduleTransitionError("Only pending schedules can be cancelled")
        now = utc_now()
        schedule.status = ScheduleStatus.CANCELLED.value
        schedule.updated_at = now
        schedule.finished_at = now
        self.db.commit()
        self.db.refresh(schedule)
        return schedule


def _selection_for(schedule: DeploymentSchedule) -> list[tuple[str, int]]:
    """What this schedule releases, as (component key, build number).

    Reads the child rows, and falls back to the two build-number columns for a
    schedule created before revision 0016 -- those rows have no child rows and
    must still run.
    """
    if schedule.component_builds:
        return [(item.component_key, item.build_number) for item in schedule.component_builds]
    legacy = [
        (Component.BACKEND.value, schedule.backend_build_number),
        (Component.FRONTEND.value, schedule.frontend_build_number),
    ]
    if schedule.mode == ReleaseMode.FRONTEND_ONLY.value:
        legacy = [(Component.FRONTEND.value, schedule.frontend_build_number)]
    elif schedule.mode == ReleaseMode.BACKEND_ONLY.value:
        legacy = [(Component.BACKEND.value, schedule.backend_build_number)]
    return [(key, number) for key, number in legacy if number is not None]


class ScheduleRunner:
    """Claims persisted due schedules and invokes the normal release orchestrator.

    builds_factory takes the session the schedule is running in: the build service
    resolves each component's repository and connection from the registry, so it
    cannot be built once and shared across sessions.
    """

    def __init__(self, session_factory, builds_factory, settings: Settings) -> None:
        self.session_factory = session_factory
        self.builds_factory = builds_factory
        self.settings = settings

    def run_due(self) -> int:
        with self.session_factory() as db:
            due_ids = list(
                db.scalars(
                    select(DeploymentSchedule.id)
                    .where(
                        DeploymentSchedule.status == ScheduleStatus.PENDING.value,
                        DeploymentSchedule.scheduled_for_utc <= utc_now(),
                    )
                    .order_by(DeploymentSchedule.scheduled_for_utc.asc())
                    .limit(self.settings.scheduler_batch_size)
                ).all()
            )
        for schedule_id in due_ids:
            self._run_one(schedule_id)
        return len(due_ids)

    def _run_one(self, schedule_id: str) -> None:
        with self.session_factory() as db:
            now = utc_now()
            claimed = db.execute(
                update(DeploymentSchedule)
                .where(
                    DeploymentSchedule.id == schedule_id,
                    DeploymentSchedule.status == ScheduleStatus.PENDING.value,
                )
                .values(status=ScheduleStatus.RUNNING.value, started_at=now, updated_at=now)
            )
            db.commit()
            if claimed.rowcount != 1:
                return
            schedule = db.get(DeploymentSchedule, schedule_id)

            try:
                orchestrator = ReleaseOrchestrator(
                    db,
                    self.builds_factory(db),
                    self.settings,
                    actor=schedule.requested_by,
                    actor_source=ActorSource.SCHEDULE.value,
                )
                selection = _selection_for(schedule)
                if len(selection) > 1:
                    result = orchestrator.create_bundle(
                        BundlePromoteRequest(
                            project_id=schedule.project_id,
                            components=[
                                ComponentBuildSelection(key=key, build_number=number)
                                for key, number in selection
                            ],
                            target=schedule.target,
                            requested_by=schedule.requested_by,
                        )
                    )
                    schedule.release_bundle_id = result.id
                else:
                    key, build_number = selection[0]
                    component = orchestrator.registry.for_key(
                        key, orchestrator.registry.project(schedule.project_id)
                    )
                    result = orchestrator.promote_standalone(
                        component,
                        build_number,
                        PromoteRequest(target=schedule.target, requested_by=schedule.requested_by),
                    )
                    schedule.deployment_id = result.id
                schedule.status = ScheduleStatus.SUCCEEDED.value
                schedule.error_message = None
            except Exception as exc:
                db.rollback()
                schedule = db.get(DeploymentSchedule, schedule_id)
                schedule.status = ScheduleStatus.FAILED.value
                schedule.error_message = str(exc)[:2000] or exc.__class__.__name__
                logger.exception("Scheduled deployment failed schedule_id=%s", schedule_id)
            schedule.finished_at = utc_now()
            schedule.updated_at = schedule.finished_at
            db.commit()

    def refresh_scheduled_deployments(self) -> int:
        with self.session_factory() as db:
            schedule_ids = list(
                db.scalars(
                    select(DeploymentSchedule.id)
                    .where(
                        DeploymentSchedule.status == ScheduleStatus.SUCCEEDED.value,
                        or_(
                            DeploymentSchedule.deployment_id.is_not(None),
                            DeploymentSchedule.release_bundle_id.is_not(None),
                        ),
                    )
                    .order_by(DeploymentSchedule.finished_at.asc())
                    .limit(self.settings.scheduler_batch_size)
                ).all()
            )
        refreshed = 0
        for schedule_id in schedule_ids:
            with self.session_factory() as db:
                schedule = db.get(DeploymentSchedule, schedule_id)
                try:
                    # Polling on the worker's own initiative, not the requester's.
                    orchestrator = ReleaseOrchestrator(
                        db,
                        self.builds_factory(db),
                        self.settings,
                        actor=schedule.requested_by,
                        actor_source=ActorSource.SYSTEM.value,
                    )
                    if schedule.release_bundle_id:
                        bundle = orchestrator.get_bundle(schedule.release_bundle_id)
                        if bundle.status not in {"SUCCESS", "FAILED", "PARTIAL_FAILURE"}:
                            orchestrator.refresh_bundle(bundle.id)
                            refreshed += 1
                    elif schedule.deployment_id:
                        deployment = orchestrator.deployments.get(schedule.deployment_id)
                        if deployment.status not in {"SUCCESS", "FAILED", "CANCELLED"}:
                            orchestrator.refresh_standalone(deployment.id)
                            refreshed += 1
                except Exception:
                    db.rollback()
                    logger.exception(
                        "Scheduled deployment refresh failed schedule_id=%s", schedule_id
                    )
        return refreshed
