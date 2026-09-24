"""操作來源：人、排程或系統。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

import enum


class ActorSource(str, enum.Enum):
    SESSION = "session"
    SCHEDULE = "schedule"
    SYSTEM = "system"
