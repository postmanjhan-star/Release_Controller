import asyncio
import logging

from app.core.config import Settings
from app.domain.orchestration.value_objects import ActorSource
from app.services.notification_service import NotificationService
from app.services.recovery_service import RecoveryService
from app.services.release_orchestrator import ReleaseOrchestrator
from app.services.schedule_service import ScheduleRunner

logger = logging.getLogger(__name__)


class BackgroundWorker:
    def __init__(self, session_factory, builds_factory, settings: Settings) -> None:
        self.session_factory = session_factory
        self.schedule_runner = ScheduleRunner(session_factory, builds_factory, settings)
        self.settings = settings
        self.recovery = RecoveryService(
            session_factory,
            lambda db: ReleaseOrchestrator(
                db,
                builds_factory(db),
                settings,
                actor=ActorSource.SYSTEM.value,
                actor_source=ActorSource.SYSTEM.value,
            ),
            settings,
        )

    def reconcile_on_startup(self) -> dict[str, int]:
        """One recovery pass before serving, so a restart resumes rather than strands."""
        if not self.settings.recovery_enabled:
            return {}
        summary = self.recovery.run()
        if any(summary.values()):
            logger.info("Startup recovery %s", summary)
        return summary

    def tick(self) -> None:
        self.schedule_runner.run_due()
        if self.settings.recovery_enabled:
            # Supersedes the old schedule-only refresh: this advances every
            # non-terminal deployment, however it was created.
            self.recovery.run()
        else:
            self.schedule_runner.refresh_scheduled_deployments()
        with self.session_factory() as db:
            notifications = NotificationService(db, self.settings)
            notifications.enqueue_new_events()
            notifications.deliver_pending()

    async def run(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.tick)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Background worker tick failed")
            await asyncio.sleep(self.settings.background_worker_poll_seconds)
