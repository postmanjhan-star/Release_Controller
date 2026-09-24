"""元件識別。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

import enum


class Component(str, enum.Enum):
    """The two component keys this application still knows by name.

    A project names its own components, so this is no longer *the* set of
    components -- nothing resolves a repository through it any more.  What is
    left is the two names that carry meaning outside the registry: they have
    their own WorkflowStage members and their own bundled BPMN diagrams, and
    they are the keys the pre-v3.0 request bodies and schedule columns speak in.
    A component key is a plain string everywhere else.
    """

    FRONTEND = "frontend"
    BACKEND = "backend"
