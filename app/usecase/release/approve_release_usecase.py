"""核准一個 release。"""

import logging
from abc import ABC, abstractmethod

from app.domain.release.entities import Release
from app.domain.release.repositories import ReleaseRepository, ReleaseWorkflowGateway
from app.domain.shared.time import utc_now
from app.domain.shared.unit_of_work import UnitOfWork
from app.usecase.release._transition import run_transition

logger = logging.getLogger(__name__)


class ApproveReleaseUseCase(ABC):
    @abstractmethod
    def execute(self, release_id: str, *, approved_by: str | None) -> Release: ...


class ApproveReleaseUseCaseImpl(ApproveReleaseUseCase):
    def __init__(
        self,
        releases: ReleaseRepository,
        workflows: ReleaseWorkflowGateway,
        uow: UnitOfWork,
    ) -> None:
        self.releases = releases
        self.workflows = workflows
        self.uow = uow

    def execute(self, release_id: str, *, approved_by: str | None) -> Release:
        now = utc_now()
        release = run_transition(
            releases=self.releases,
            workflows=self.workflows,
            uow=self.uow,
            release_id=release_id,
            apply=lambda entity: entity.approve(by=approved_by, at=now),
            element_id="Task_approval_gate",
            step_data={"decision": "approved"},
        )
        logger.info("Approve release id=%s approved_by=%s", release.id, release.approved_by)
        return release


def new_approve_release_usecase(
    releases: ReleaseRepository,
    workflows: ReleaseWorkflowGateway,
    uow: UnitOfWork,
) -> ApproveReleaseUseCase:
    return ApproveReleaseUseCaseImpl(releases, workflows, uow)
