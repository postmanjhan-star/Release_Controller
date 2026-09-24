"""Release 的形狀：單一元件或 bundle。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

import enum


class ReleaseMode(str, enum.Enum):
    # SINGLE and BUNDLE are the two shapes a release actually has: one component,
    # or several.  FRONTEND_ONLY and BACKEND_ONLY are what SINGLE used to be
    # called when frontend and backend were the only components there were; they
    # stay in the enum because existing rows carry them, and nothing new writes
    # them.
    SINGLE = "SINGLE"
    BUNDLE = "BUNDLE"
    FRONTEND_ONLY = "FRONTEND_ONLY"
    BACKEND_ONLY = "BACKEND_ONLY"
