"""已加密的上游憑證。

Domain 只搬運密文與提示，不做加解密——那需要金鑰，是 infrastructure 的事。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class EncryptedToken:
    """Fernet 密文，加上足以辨認是哪一把 token 的提示。

    hint 是明文的最後四個字元：兩個存起來的憑證要能被人分辨，但不能被讀出來。
    """

    cipher_text: str
    hint: str
