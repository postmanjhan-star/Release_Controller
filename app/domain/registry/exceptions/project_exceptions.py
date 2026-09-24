"""專案與元件 aggregate 的業務例外。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""


class ProjectNotFoundError(Exception):
    pass


class ComponentNotFoundError(Exception):
    pass


class ProjectKeyTakenError(Exception):
    pass


class ComponentKeyTakenError(Exception):
    pass


class ComponentSlotConflictError(Exception):
    """兩個元件會促銷到同一個位置，或是設定本身就不可能成立。"""


class ComponentInUseError(Exception):
    """部署歷史或排程還指著這個元件。"""
