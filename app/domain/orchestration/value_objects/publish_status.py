"""Gitea Release 發布狀態。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

import enum


class PublishStatus(str, enum.Enum):
    NOT_PUBLISHED = "NOT_PUBLISHED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"


class PublishRecordStatus(str, enum.Enum):
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
