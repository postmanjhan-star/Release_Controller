"""駁回一個 release。"""

import logging
from abc import ABC, abstractmethod

from app.domain.release.entities import Release
from app.domain.release.repositories import ReleaseRepository, ReleaseWorkflowGateway
from app.domain.shared.time import utc_now
from app.domain.shared.unit_of_work import UnitOfWork
from app.usecase.release._transition import run_transition

logger = logging.getLogger(__name__)


class RejectReleaseUseCase(ABC):
    @abstractmethod
    def execute(
        self, release_id: str, *, rejected_by: str | None, message: str | None
    ) -> Release: ...


class RejectReleaseUseCaseImpl(RejectReleaseUseCase):
    def __init__(
        self,
        releases: ReleaseRepository,
        workflows: ReleaseWorkflowGateway,
        uow: UnitOfWork,
    ) -> None:
        self.releases = releases
        self.workflows = workflows
        self.uow = uow

    def execute(self, release_id: str, *, rejected_by: str | None, message: str | None) -> Release:
        now = utc_now()
        release = run_transition(
            releases=self.releases,
            workflows=self.workflows,
            uow=self.uow,
            release_id=release_id,
            apply=lambda entity: entity.reject(by=rejected_by, message=message, at=now),
            element_id="Task_approval_gate",
            step_data={"decision": "rejected"},
        )
        logger.info("Reject release id=%s rejected_by=%s", release.id, release.rejected_by)
        return release


def new_reject_release_usecase(
    releases: ReleaseRepository,
    workflows: ReleaseWorkflowGateway,
    uow: UnitOfWork,
) -> RejectReleaseUseCase:
    return RejectReleaseUseCaseImpl(releases, workflows, uow)
