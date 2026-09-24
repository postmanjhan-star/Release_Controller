"""把一個已核准的 release 推進到部署中。"""

import logging
from abc import ABC, abstractmethod

from app.domain.release.entities import Release
from app.domain.release.repositories import ReleaseRepository, ReleaseWorkflowGateway
from app.domain.shared.time import utc_now
from app.domain.shared.unit_of_work import UnitOfWork
from app.usecase.release._transition import run_transition

logger = logging.getLogger(__name__)


class StartDeploymentUseCase(ABC):
    @abstractmethod
    def execute(self, release_id: str) -> Release: ...


class StartDeploymentUseCaseImpl(StartDeploymentUseCase):
    def __init__(
        self,
        releases: ReleaseRepository,
        workflows: ReleaseWorkflowGateway,
        uow: UnitOfWork,
    ) -> None:
        self.releases = releases
        self.workflows = workflows
        self.uow = uow

    def execute(self, release_id: str) -> Release:
        now = utc_now()
        release = run_transition(
            releases=self.releases,
            workflows=self.workflows,
            uow=self.uow,
            release_id=release_id,
            apply=lambda entity: entity.start_deployment(at=now),
            element_id="Task_start_deployment",
        )
        logger.info("Start deployment release id=%s", release.id)
        return release


def new_start_deployment_usecase(
    releases: ReleaseRepository,
    workflows: ReleaseWorkflowGateway,
    uow: UnitOfWork,
) -> StartDeploymentUseCase:
    return StartDeploymentUseCaseImpl(releases, workflows, uow)
