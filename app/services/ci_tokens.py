"""Service credentials are intentionally separate from browser sessions."""

import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.ci import CIServiceToken
from app.domain.shared.time import utc_now
from app.services.auth_service import AuthService


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def token_active(token: CIServiceToken | None) -> bool:
    return token is not None and token.revoked_at is None and aware(token.expires_at) > utc_now()


def authenticate(db: Session, raw: str) -> CIServiceToken | None:
    token = db.scalar(
        select(CIServiceToken).where(CIServiceToken.token_hash == AuthService.hash_token(raw))
    )
    return token if token_active(token) else None


def create_token(db: Session, *, expires_at: datetime, **scope) -> tuple[CIServiceToken, str]:
    if aware(expires_at) <= utc_now():
        raise ValueError("Token expiry must be in the future")
    raw = "rc_ci_" + secrets.token_urlsafe(48)
    token = CIServiceToken(token_hash=AuthService.hash_token(raw), expires_at=expires_at, **scope)
    db.add(token)
    db.commit()
    return token, raw
