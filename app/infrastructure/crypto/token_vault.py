"""TokenVault 的 Fernet 實作。"""

from app.core.config import Settings
from app.core.crypto import token_cipher, token_hint
from app.domain.registry.repositories import TokenVault
from app.domain.registry.value_objects import EncryptedToken


class FernetTokenVault(TokenVault):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def encrypt(self, plaintext: str) -> EncryptedToken:
        return EncryptedToken(
            cipher_text=token_cipher(self.settings).encrypt(plaintext),
            hint=token_hint(plaintext),
        )
