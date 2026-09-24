"""重新排列專案的元件部署順序。"""

from abc import ABC, abstractmethod

from app.domain.registry.entities import Project
from app.domain.registry.repositories import ProjectRepository
from app.domain.shared.unit_of_work import UnitOfWork
from app.usecase.registry._project_ops import load_project


class ReorderComponentsUseCase(ABC):
    @abstractmethod
    def execute(self, project_ref: str, component_ids: list[str]) -> Project: ...


class ReorderComponentsUseCaseImpl(ReorderComponentsUseCase):
    def __init__(self, projects: ProjectRepository, uow: UnitOfWork) -> None:
        self.projects = projects
        self.uow = uow

    def execute(self, project_ref: str, component_ids: list[str]) -> Project:
        project = load_project(self.projects, project_ref)
        # 只換順序不換設定，促銷位置不會因此改變，所以這裡不需要重算衝突。
        project.reorder(component_ids)
        self.projects.save(project)
        self.uow.commit()
        return load_project(self.projects, project.id)


def new_reorder_components_usecase(
    projects: ProjectRepository, uow: UnitOfWork
) -> ReorderComponentsUseCase:
    return ReorderComponentsUseCaseImpl(projects, uow)
