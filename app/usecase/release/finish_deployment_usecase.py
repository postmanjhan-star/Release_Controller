"""記錄部署的結果。"""

import logging
from abc import ABC, abstractmethod

from app.domain.release.entities import Release
from app.domain.release.repositories import ReleaseRepository, ReleaseWorkflowGateway
from app.domain.release.value_objects import ReleaseStatus
from app.domain.shared.time import utc_now
from app.domain.shared.unit_of_work import UnitOfWork
from app.usecase.release._transition import run_transition

logger = logging.getLogger(__name__)


class FinishDeploymentUseCase(ABC):
    @abstractmethod
    def execute(
        self, release_id: str, *, status: ReleaseStatus, message: str | None
    ) -> Release: ...


class FinishDeploymentUseCaseImpl(FinishDeploymentUseCase):
    def __init__(
        self,
        releases: ReleaseRepository,
        workflows: ReleaseWorkflowGateway,
        uow: UnitOfWork,
    ) -> None:
        self.releases = releases
        self.workflows = workflows
        self.uow = uow

    def execute(self, release_id: str, *, status: ReleaseStatus, message: str | None) -> Release:
        now = utc_now()
        release = run_transition(
            releases=self.releases,
            workflows=self.workflows,
            uow=self.uow,
            release_id=release_id,
            apply=lambda entity: entity.finish_deployment(status=status, message=message, at=now),
            element_id="Task_execute_deployment",
            step_data={"deployment_result": status.value.lower()},
        )
        logger.info("Finish deployment release id=%s status=%s", release.id, release.status)
        return release


def new_finish_deployment_usecase(
    releases: ReleaseRepository,
    workflows: ReleaseWorkflowGateway,
    uow: UnitOfWork,
) -> FinishDeploymentUseCase:
    return FinishDeploymentUseCaseImpl(releases, workflows, uow)
