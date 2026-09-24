"""用真正的 Drone 與 Gitea 檢查一個專案部署得起來嗎。"""

from abc import ABC, abstractmethod

from app.domain.registry.repositories import (
    ComponentChecker,
    DroneConnectionResolver,
    ProjectRepository,
)
from app.domain.registry.value_objects import ComponentCheckResult, ProjectValidation
from app.usecase.registry._project_ops import load_project


class ValidateProjectUseCase(ABC):
    @abstractmethod
    def execute(self, project_ref: str) -> ProjectValidation: ...


class ValidateProjectUseCaseImpl(ValidateProjectUseCase):
    def __init__(
        self,
        projects: ProjectRepository,
        checker: ComponentChecker,
        resolver: DroneConnectionResolver,
    ) -> None:
        self.projects = projects
        self.checker = checker
        self.resolver = resolver

    def execute(self, project_ref: str) -> ProjectValidation:
        """回答「這個專案現在發得出去嗎」，在有人真的去發之前。

        位置衝突理論上不會在這裡出現——存檔時就擋掉了——但還是照樣回報：
        從舊環境變數 seed 出來的專案從來沒有經過存檔時的檢查。
        """
        project = load_project(self.projects, project_ref)
        checks: list[ComponentCheckResult] = [
            self.checker.check(project, component)
            for component in project.components
            if component.is_active
        ]
        conflicts = project.slot_conflicts(self.resolver.drone_connection_ids(project))
        status = "ok" if all(c.status == "ok" for c in checks) and not conflicts else "error"
        return ProjectValidation(status=status, checks=checks, conflicts=conflicts)


def new_validate_project_usecase(
    projects: ProjectRepository,
    checker: ComponentChecker,
    resolver: DroneConnectionResolver,
) -> ValidateProjectUseCase:
    return ValidateProjectUseCaseImpl(projects, checker, resolver)
