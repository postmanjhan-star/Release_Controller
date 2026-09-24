"""Encryption for the upstream credentials this service stores.

A Drone or Gitea token is a live credential: whoever holds one can deploy.  Once
those tokens move out of the environment and into the database, a copy of
release.db would otherwise be a copy of the credentials -- including every backup
and every developer laptop the file is ever copied to.  They are therefore stored
encrypted under a key that lives outside the database.

The key is APP_SECRET_KEY.  Losing it does not lose the database, only the stored
tokens: re-enter them and everything else is intact.  Rotating it has the same
effect, which is why a token that cannot be decrypted is reported as
KEY_MISMATCH rather than surfacing as a 500.
"""

import base64
import hashlib
import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings

logger = logging.getLogger(__name__)

# Used only when APP_ENV is not production and no key was configured, so local
# work needs no setup.  It is a constant in the source and therefore no secret at
# all -- which is exactly why config.py refuses to start production without a
# real key.
DEVELOPMENT_FALLBACK_KEY = "release-controller-development-key"


class TokenDecryptionError(RuntimeError):
    """Stored ciphertext cannot be read with the configured APP_SECRET_KEY.

    Means the key changed (or was never the one that encrypted this row), not
    that the data is corrupt.  The fix is to re-enter the token.
    """


def _fernet_key(secret: str) -> bytes:
    # Fernet wants 32 url-safe base64 bytes; APP_SECRET_KEY is free-form.  A
    # single SHA-256 is the right derivation here: the input is a high-entropy
    # secret chosen by an operator, not a human-memorable password, so a KDF's
    # work factor would cost startup time and buy nothing.
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())


class TokenCipher:
    def __init__(self, secret: str) -> None:
        self._fernet = Fernet(_fernet_key(secret))

    def encrypt(self, token: str) -> str:
        return self._fernet.encrypt(token.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            raise TokenDecryptionError(
                "This connection's token cannot be decrypted with the current "
                "APP_SECRET_KEY. Re-enter the token to store it under the new key."
            ) from exc


@lru_cache(maxsize=4)
def _cipher_for(secret: str) -> TokenCipher:
    return TokenCipher(secret)


def token_cipher(settings: Settings) -> TokenCipher:
    """The one place a TokenCipher is constructed.

    Takes Settings rather than a raw string so the development fallback lives
    here instead of at every call site.
    """
    secret = (settings.app_secret_key or "").strip()
    if not secret:
        # config.py has already refused to start production in this state.
        logger.warning(
            "APP_SECRET_KEY is not set; upstream tokens are encrypted with the "
            "development fallback key and are not protected."
        )
        secret = DEVELOPMENT_FALLBACK_KEY
    return _cipher_for(secret)


def token_hint(token: str) -> str:
    """The tail of a token, for telling two stored credentials apart in the UI."""
    tail = token.strip()[-4:]
    return f"…{tail}" if tail else "—"
