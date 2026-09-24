"""列出 release。"""

from abc import ABC, abstractmethod

from app.domain.release.entities import Release
from app.domain.release.repositories import ReleaseFilters, ReleaseRepository


class ListReleasesUseCase(ABC):
    @abstractmethod
    def execute(
        self, filters: ReleaseFilters, *, limit: int, offset: int
    ) -> tuple[list[Release], int]: ...


class ListReleasesUseCaseImpl(ListReleasesUseCase):
    def __init__(self, releases: ReleaseRepository) -> None:
        self.releases = releases

    def execute(
        self, filters: ReleaseFilters, *, limit: int, offset: int
    ) -> tuple[list[Release], int]:
        return self.releases.search(filters, limit=limit, offset=offset)


def new_list_releases_usecase(releases: ReleaseRepository) -> ListReleasesUseCase:
    return ListReleasesUseCaseImpl(releases)
