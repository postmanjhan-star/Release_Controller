"""單一元件部署的狀態。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

import enum


class DeploymentStatus(str, enum.Enum):
    WAITING = "WAITING"
    PROMOTING = "PROMOTING"
    DEPLOYING = "DEPLOYING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
