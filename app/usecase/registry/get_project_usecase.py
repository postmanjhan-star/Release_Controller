"""讀出一個專案，或它底下的一個元件。"""

from abc import ABC, abstractmethod

from app.domain.registry.entities import Component, Project
from app.domain.registry.repositories import ProjectRepository
from app.usecase.registry._project_ops import load_project


class GetProjectUseCase(ABC):
    @abstractmethod
    def execute(self, project_ref: str) -> Project: ...

    @abstractmethod
    def component(self, project_ref: str, component_ref: str) -> Component: ...


class GetProjectUseCaseImpl(GetProjectUseCase):
    def __init__(self, projects: ProjectRepository) -> None:
        self.projects = projects

    def execute(self, project_ref: str) -> Project:
        return load_project(self.projects, project_ref)

    def component(self, project_ref: str, component_ref: str) -> Component:
        return load_project(self.projects, project_ref).component(component_ref)


def new_get_project_usecase(projects: ProjectRepository) -> GetProjectUseCase:
    return GetProjectUseCaseImpl(projects)
