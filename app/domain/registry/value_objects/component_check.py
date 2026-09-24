"""驗證一個專案是否部署得起來的結果。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ComponentCheckResult:
    component_id: str
    component_key: str
    status: str  # ok | error
    drone: str | None = None
    gitea: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class ProjectValidation:
    status: str  # ok | error
    checks: list[ComponentCheckResult] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
