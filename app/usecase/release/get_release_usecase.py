"""讀出一個 release。"""

from abc import ABC, abstractmethod

from app.domain.release.entities import Release
from app.domain.release.exceptions import ReleaseNotFoundError
from app.domain.release.repositories import ReleaseRepository


class GetReleaseUseCase(ABC):
    @abstractmethod
    def execute(self, release_id: str) -> Release: ...


class GetReleaseUseCaseImpl(GetReleaseUseCase):
    def __init__(self, releases: ReleaseRepository) -> None:
        self.releases = releases

    def execute(self, release_id: str) -> Release:
        release = self.releases.find_by_id(release_id)
        if release is None:
            raise ReleaseNotFoundError
        return release


def new_get_release_usecase(releases: ReleaseRepository) -> GetReleaseUseCase:
    return GetReleaseUseCaseImpl(releases)
