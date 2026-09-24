import re
from datetime import datetime, timezone
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.domain.registry.value_objects import ConnectionKind, ConnectionVerifyStatus

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

Slug = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ShortName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
RepoPart = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Target = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


def _validate_slug(value: str) -> str:
    if not SLUG_PATTERN.match(value):
        raise ValueError(
            "must be lowercase letters, digits, hyphen or underscore, starting with "
            "a letter or digit"
        )
    return value


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


# --------------------------------------------------------------------------- #
# Connections
# --------------------------------------------------------------------------- #


class ConnectionCreate(BaseModel):
    kind: ConnectionKind
    name: ShortName
    base_url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    token: Annotated[str, StringConstraints(min_length=1, max_length=1000)]
    is_default: bool = False


class ConnectionUpdate(BaseModel):
    name: ShortName | None = None
    base_url: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
        | None
    ) = None
    # Omit to keep the stored credential.  There is no way to clear it: a
    # connection without a token cannot do anything, so deleting it is the
    # honest operation.
    token: Annotated[str, StringConstraints(min_length=1, max_length=1000)] | None = None
    is_default: bool | None = None


class ConnectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: ConnectionKind
    name: str
    base_url: str
    # The token itself is never serialised, under any field name.
    token_hint: str
    is_default: bool
    verify_status: ConnectionVerifyStatus | None
    verify_detail: str | None
    verified_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_validator("verified_at", "created_at", "updated_at", mode="after")
    @classmethod
    def ensure_utc(cls, value: datetime | None) -> datetime | None:
        return _utc(value)


class ConnectionListResponse(BaseModel):
    items: list[ConnectionResponse]


class ConnectionTestResponse(BaseModel):
    status: ConnectionVerifyStatus
    detail: str | None = None
    checked_at: datetime


# --------------------------------------------------------------------------- #
# Components
# --------------------------------------------------------------------------- #


class ComponentCreate(BaseModel):
    key: Slug
    display_name: ShortName | None = None
    drone_owner: RepoPart
    drone_repo: RepoPart
    promote_target_override: Target | None = None
    drone_connection_id: str | None = None
    publish_enabled: bool = True
    gitea_owner: RepoPart | None = None
    gitea_repo: RepoPart | None = None
    gitea_connection_id: str | None = None
    tag_prefix: Annotated[str, StringConstraints(strip_whitespace=True, max_length=30)] = ""
    # Omit to append after the existing components.
    position: int | None = Field(default=None, ge=1)
    is_active: bool = True

    _check_key = field_validator("key")(_validate_slug)


class ComponentUpdate(BaseModel):
    display_name: ShortName | None = None
    drone_owner: RepoPart | None = None
    drone_repo: RepoPart | None = None
    promote_target_override: Target | None = None
    clear_promote_target_override: bool = False
    drone_connection_id: str | None = None
    publish_enabled: bool | None = None
    gitea_owner: RepoPart | None = None
    gitea_repo: RepoPart | None = None
    gitea_connection_id: str | None = None
    tag_prefix: Annotated[str, StringConstraints(strip_whitespace=True, max_length=30)] | None = (
        None
    )
    is_active: bool | None = None


class ComponentReorder(BaseModel):
    component_ids: list[str] = Field(min_length=1)


class ComponentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    key: str
    display_name: str
    position: int
    drone_owner: str
    drone_repo: str
    drone_slug: str
    promote_target_override: str | None
    effective_target: str
    drone_connection_id: str | None
    publish_enabled: bool
    gitea_owner: str | None
    gitea_repo: str | None
    gitea_slug: str | None
    gitea_connection_id: str | None
    tag_prefix: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def ensure_utc(cls, value: datetime) -> datetime:
        return _utc(value)


class ComponentListResponse(BaseModel):
    items: list[ComponentResponse]


# --------------------------------------------------------------------------- #
# Projects
# --------------------------------------------------------------------------- #


class ProjectCreate(BaseModel):
    key: Slug
    name: Name
    description: str | None = Field(default=None, max_length=2000)
    default_target: Target = "production"
    drone_connection_id: str | None = None
    gitea_connection_id: str | None = None
    # Components may be supplied inline so a whole project arrives in one call.
    components: list[ComponentCreate] = Field(default_factory=list)

    _check_key = field_validator("key")(_validate_slug)


class ProjectUpdate(BaseModel):
    name: Name | None = None
    description: str | None = Field(default=None, max_length=2000)
    default_target: Target | None = None
    drone_connection_id: str | None = None
    gitea_connection_id: str | None = None
    is_archived: bool | None = None


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    key: str
    name: str
    description: str | None
    default_target: str
    drone_connection_id: str | None
    gitea_connection_id: str | None
    is_archived: bool
    created_at: datetime
    updated_at: datetime
    components: list[ComponentResponse]

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def ensure_utc(cls, value: datetime) -> datetime:
        return _utc(value)


class ProjectListResponse(BaseModel):
    items: list[ProjectResponse]


class ComponentCheck(BaseModel):
    component_id: str
    component_key: str
    status: str  # ok | error
    drone: str | None = None
    gitea: str | None = None
    detail: str | None = None


class ProjectValidateResponse(BaseModel):
    status: str  # ok | error
    checks: list[ComponentCheck]
    conflicts: list[str] = Field(default_factory=list)
