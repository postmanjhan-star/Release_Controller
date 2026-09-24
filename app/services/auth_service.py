import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.auth import AuthSession, OAuthLoginAttempt
from app.integrations.gitea.oauth import GiteaOAuthUser


@dataclass(frozen=True)
class CurrentUser:
    login: str
    full_name: str | None = None
    email: str | None = None
    avatar_url: str | None = None
    is_admin: bool = False


def verified_actor(user: CurrentUser, supplied: str | None = None) -> str:
    """Use client identity only when authentication is explicitly disabled."""
    if user.login == "authentication-disabled":
        return supplied or "local-user"
    return user.login


class AuthService:
    oauth_attempt_minutes = 10

    def __init__(self, db: Session, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create_login_attempt(self, return_to: str) -> tuple[str, str, str]:
        now = datetime.now(timezone.utc)
        self.db.execute(delete(OAuthLoginAttempt).where(OAuthLoginAttempt.expires_at <= now))
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        self.db.add(
            OAuthLoginAttempt(
                state_hash=self.hash_token(state),
                code_verifier=verifier,
                return_to=return_to,
                expires_at=now + timedelta(minutes=self.oauth_attempt_minutes),
            )
        )
        self.db.commit()
        return state, verifier, challenge

    def consume_login_attempt(self, state: str) -> OAuthLoginAttempt | None:
        now = datetime.now(timezone.utc)
        attempt = self.db.scalar(
            select(OAuthLoginAttempt).where(
                OAuthLoginAttempt.state_hash == self.hash_token(state),
                OAuthLoginAttempt.expires_at > now,
            )
        )
        if attempt is None:
            return None
        self.db.delete(attempt)
        self.db.commit()
        return attempt

    def create_session(self, user: GiteaOAuthUser) -> str:
        now = datetime.now(timezone.utc)
        raw_token = secrets.token_urlsafe(32)
        self.db.execute(delete(AuthSession).where(AuthSession.expires_at <= now))
        self.db.add(
            AuthSession(
                token_hash=self.hash_token(raw_token),
                login=user.login,
                full_name=user.full_name,
                email=user.email,
                avatar_url=user.avatar_url,
                is_admin=user.is_admin,
                expires_at=now + timedelta(hours=self.settings.auth_session_hours),
            )
        )
        self.db.commit()
        return raw_token

    def get_user(self, raw_token: str | None) -> CurrentUser | None:
        if not raw_token:
            return None
        now = datetime.now(timezone.utc)
        session = self.db.scalar(
            select(AuthSession).where(
                AuthSession.token_hash == self.hash_token(raw_token),
                AuthSession.expires_at > now,
            )
        )
        if session is None:
            return None
        return CurrentUser(
            login=session.login,
            full_name=session.full_name,
            email=session.email,
            avatar_url=session.avatar_url,
            is_admin=session.is_admin,
        )

    def delete_session(self, raw_token: str | None) -> None:
        if raw_token:
            self.db.execute(
                delete(AuthSession).where(AuthSession.token_hash == self.hash_token(raw_token))
            )
            self.db.commit()
