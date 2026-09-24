"""Bundle 部署的聚合狀態。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

import enum


class BundleStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROMOTING = "PROMOTING"
    DEPLOYING = "DEPLOYING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
