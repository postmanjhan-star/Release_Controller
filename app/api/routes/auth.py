import hmac
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.infrastructure.di.injection import optional_current_user
from app.integrations.gitea.oauth import GiteaOAuthClient, GiteaOAuthError
from app.schemas.auth import AuthSessionResponse, AuthUserResponse
from app.services.auth_service import AuthService, CurrentUser

router = APIRouter(prefix="/auth", tags=["auth"])


def _configured(settings: Settings) -> bool:
    return not settings.auth_enabled or bool(settings.gitea_oauth_client_id)


def _callback_url(request: Request, settings: Settings) -> str:
    return settings.gitea_oauth_callback_url or str(request.url_for("auth_callback"))


def _oauth_client(settings: Settings) -> GiteaOAuthClient:
    return GiteaOAuthClient(
        settings.gitea_server,
        settings.gitea_oauth_client_id,
        settings.gitea_oauth_client_secret,
        settings.gitea_timeout_seconds,
    )


@router.get("/session", response_model=AuthSessionResponse)
def auth_session(
    settings: Settings = Depends(get_settings),
    user: CurrentUser | None = Depends(optional_current_user),
) -> AuthSessionResponse:
    return AuthSessionResponse(
        enabled=settings.auth_enabled,
        configured=_configured(settings),
        authenticated=user is not None,
        user=AuthUserResponse(**user.__dict__) if user is not None else None,
    )


@router.get("/login")
def auth_login(
    request: Request,
    return_to: Annotated[str, Query(max_length=2048)] = "/",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    if not settings.auth_enabled:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    if not _configured(settings):
        raise HTTPException(status_code=503, detail="Gitea OAuth is not configured")
    if not return_to.startswith("/") or return_to.startswith("//"):
        return_to = "/"
    state, _verifier, challenge = AuthService(db, settings).create_login_attempt(return_to)
    response = RedirectResponse(
        _oauth_client(settings).authorization_url(
            _callback_url(request, settings), state, challenge
        ),
        status_code=status.HTTP_302_FOUND,
    )
    response.set_cookie(
        "release_controller_oauth_state",
        state,
        max_age=AuthService.oauth_attempt_minutes * 60,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/api/v1/auth/callback",
    )
    return response


@router.get("/callback", name="auth_callback")
def auth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    if error:
        response = RedirectResponse(
            url=f"/?auth_error={quote(error)}", status_code=status.HTTP_303_SEE_OTHER
        )
        response.delete_cookie("release_controller_oauth_state", path="/api/v1/auth/callback")
        return response
    if code is None or state is None:
        raise HTTPException(status_code=400, detail="Missing OAuth callback parameters")
    cookie_state = request.cookies.get("release_controller_oauth_state")
    if cookie_state is None or not hmac.compare_digest(cookie_state, state):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    service = AuthService(db, settings)
    attempt = service.consume_login_attempt(state)
    if attempt is None:
        raise HTTPException(status_code=400, detail="OAuth login attempt expired")
    try:
        user = _oauth_client(settings).authenticate(
            code, _callback_url(request, settings), attempt.code_verifier
        )
    except GiteaOAuthError as exc:
        response = RedirectResponse(
            url=f"/?auth_error={quote(str(exc))}", status_code=status.HTTP_303_SEE_OTHER
        )
        response.delete_cookie("release_controller_oauth_state", path="/api/v1/auth/callback")
        return response
    session_token = service.create_session(user)
    response = RedirectResponse(url=attempt.return_to, status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("release_controller_oauth_state", path="/api/v1/auth/callback")
    response.set_cookie(
        settings.auth_session_cookie_name,
        session_token,
        max_age=settings.auth_session_hours * 3600,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def auth_logout(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    AuthService(db, settings).delete_session(request.cookies.get(settings.auth_session_cookie_name))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(settings.auth_session_cookie_name, path="/")
    return response
