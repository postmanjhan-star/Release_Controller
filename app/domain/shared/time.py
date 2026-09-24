"""共用時間工具。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
