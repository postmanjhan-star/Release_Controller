"""通知信件的投遞狀態。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

import enum


class EmailDeliveryStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SENT = "SENT"
    FAILED = "FAILED"
