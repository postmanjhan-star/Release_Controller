"""上游連線的種類與驗證結果。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

import enum


class ConnectionKind(str, enum.Enum):
    DRONE = "drone"
    GITEA = "gitea"


class ConnectionVerifyStatus(str, enum.Enum):
    OK = "ok"
    UNAUTHORIZED = "unauthorized"
    UNREACHABLE = "unreachable"
    # The stored token cannot be decrypted with the current APP_SECRET_KEY.
    # Distinct from UNAUTHORIZED: the credential was never sent.
    KEY_MISMATCH = "key_mismatch"
