"""建立一個等待核准的 release。"""

import logging
from abc import ABC, abstractmethod

from app.domain.release.entities import Release
from app.domain.release.repositories import ReleaseRepository, ReleaseWorkflowGateway
from app.domain.shared.time import utc_now
from app.domain.shared.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)


class CreateReleaseUseCase(ABC):
    @abstractmethod
    def execute(
        self,
        *,
        repository: str,
        branch: str,
        commit_sha: str,
        environment: str,
        message: str | None,
    ) -> Release: ...


class CreateReleaseUseCaseImpl(CreateReleaseUseCase):
    def __init__(
        self,
        releases: ReleaseRepository,
        workflows: ReleaseWorkflowGateway,
        uow: UnitOfWork,
    ) -> None:
        self.releases = releases
        self.workflows = workflows
        self.uow = uow

    def execute(
        self,
        *,
        repository: str,
        branch: str,
        commit_sha: str,
        environment: str,
        message: str | None,
    ) -> Release:
        release = Release.create(
            repository=repository,
            branch=branch,
            commit_sha=commit_sha,
            environment=environment,
            message=message,
            now=utc_now(),
        )
        # release 與它的 workflow instance 在同一個 transaction 裡建立，否則會
        # 出現一個沒有流程的 release。
        self.releases.add(release)
        self.workflows.create_for_release(release.id)
        self.uow.commit()
        logger.info(
            "Create release id=%s repository=%s environment=%s",
            release.id,
            release.repository,
            release.environment,
        )
        return release


def new_create_release_usecase(
    releases: ReleaseRepository,
    workflows: ReleaseWorkflowGateway,
    uow: UnitOfWork,
) -> CreateReleaseUseCase:
    return CreateReleaseUseCaseImpl(releases, workflows, uow)
