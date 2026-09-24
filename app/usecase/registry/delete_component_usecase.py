"""刪掉專案裡的一個元件。"""

from abc import ABC, abstractmethod

from app.domain.registry.exceptions import ComponentInUseError
from app.domain.registry.repositories import ComponentUsage, ProjectRepository
from app.domain.shared.unit_of_work import UnitOfWork
from app.usecase.registry._project_ops import load_project


class DeleteComponentUseCase(ABC):
    @abstractmethod
    def execute(self, project_ref: str, component_ref: str) -> None: ...


class DeleteComponentUseCaseImpl(DeleteComponentUseCase):
    def __init__(
        self,
        projects: ProjectRepository,
        usage: ComponentUsage,
        uow: UnitOfWork,
    ) -> None:
        self.projects = projects
        self.usage = usage
        self.uow = uow

    def execute(self, project_ref: str, component_ref: str) -> None:
        project = load_project(self.projects, project_ref)
        component = project.component(component_ref)
        self._assert_deletable(component.id, component.key)
        # 刪掉之後其餘元件重新編號，位置永遠是 1..n。
        project.remove_component(component)
        self.projects.save(project)
        self.uow.commit()

    def _assert_deletable(self, component_id: str, component_key: str) -> None:
        """部署過的元件不刪。

        部署歷史是證據，把元件刪掉會讓那段歷史指向不存在的東西而變得讀不懂——
        跟 workflow_events 完全不設外鍵是同一個理由。停用（is_active=false）
        永遠可用，而且不會動到已經發生的事。
        """
        if self.usage.has_deployments(component_id):
            raise ComponentInUseError(
                f"Component {component_key!r} has deployment history and cannot be "
                "deleted. Set it inactive instead -- that stops new releases using "
                "it while keeping what it already deployed readable."
            )
        if self.usage.is_scheduled(component_id):
            raise ComponentInUseError(
                f"Component {component_key!r} is part of a schedule and cannot be "
                "deleted. Cancel the schedule first, or set the component inactive."
            )


def new_delete_component_usecase(
    projects: ProjectRepository,
    usage: ComponentUsage,
    uow: UnitOfWork,
) -> DeleteComponentUseCase:
    return DeleteComponentUseCaseImpl(projects, usage, uow)
