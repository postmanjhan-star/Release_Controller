from pydantic import BaseModel


class AuthUserResponse(BaseModel):
    login: str
    full_name: str | None = None
    email: str | None = None
    avatar_url: str | None = None
    is_admin: bool = False


class AuthSessionResponse(BaseModel):
    enabled: bool
    configured: bool
    authenticated: bool
    user: AuthUserResponse | None = None
