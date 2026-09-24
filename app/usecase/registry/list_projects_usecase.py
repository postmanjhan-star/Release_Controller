"""列出所有專案。"""

from abc import ABC, abstractmethod

from app.domain.registry.entities import Project
from app.domain.registry.repositories import ProjectRepository


class ListProjectsUseCase(ABC):
    @abstractmethod
    def execute(self, *, include_archived: bool = False) -> list[Project]: ...


class ListProjectsUseCaseImpl(ListProjectsUseCase):
    def __init__(self, projects: ProjectRepository) -> None:
        self.projects = projects

    def execute(self, *, include_archived: bool = False) -> list[Project]:
        return self.projects.list(include_archived=include_archived)


def new_list_projects_usecase(projects: ProjectRepository) -> ListProjectsUseCase:
    return ListProjectsUseCaseImpl(projects)
