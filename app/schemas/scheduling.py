from datetime import datetime, timezone
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.domain.orchestration.value_objects import ReleaseMode
from app.domain.scheduling.value_objects import EmailDeliveryStatus, ScheduleStatus
from app.schemas.orchestration import ComponentBuildSelection

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ScheduleCreate(BaseModel):
    """What to release, when.

    `components` is the shape going forward; `mode` follows from how many were
    selected.  The v2.x body -- a mode plus up to two build numbers named after
    the two components there used to be -- is still accepted unchanged.
    """

    project_id: str | None = Field(default=None, max_length=100)
    components: list[ComponentBuildSelection] = Field(default_factory=list)
    mode: ReleaseMode | None = None
    frontend_build_number: int | None = Field(default=None, gt=0)
    backend_build_number: int | None = Field(default=None, gt=0)
    target: NonEmptyString | None = Field(default=None, max_length=100)
    scheduled_for: datetime
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    requested_by: str | None = Field(default=None, max_length=255)
    notification_recipients: list[str] = Field(default_factory=list, max_length=50)
    release_version: str | None = Field(default=None, max_length=100)
    release_notes: str | None = Field(default=None, max_length=4000)

    @field_validator("release_version", "release_notes")
    @classmethod
    def strip_release_details(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if info.field_name == "release_version" and any(char in value for char in "\r\n"):
            raise ValueError("Release version must be a single line")
        return value

    @field_validator("notification_recipients")
    @classmethod
    def validate_recipients(cls, values: list[str]) -> list[str]:
        cleaned = []
        for value in values:
            value = value.strip()
            if not value or "@" not in value or any(char in value for char in "\r\n,"):
                raise ValueError("Invalid email recipient")
            cleaned.append(value)
        return list(dict.fromkeys(cleaned))

    @model_validator(mode="after")
    def validate_builds_and_time(self) -> "ScheduleCreate":
        if self.target and self.target.casefold() == "production":
            if self.release_version is None or self.release_notes is None:
                raise ValueError(
                    "Production schedules require a release version and update details"
                )
        if self.components:
            keys = [item.key for item in self.components]
            if len(set(keys)) != len(keys):
                raise ValueError("Each component may appear only once in a schedule")
            derived = ReleaseMode.SINGLE if len(keys) == 1 else ReleaseMode.BUNDLE
            if self.mode is not None and self.mode != derived:
                raise ValueError(
                    f"{len(keys)} component(s) selected, which is {derived.value}, "
                    f"not {self.mode.value}"
                )
            self.mode = derived
        else:
            if self.mode is None:
                raise ValueError(
                    "A schedule needs at least one component: send "
                    'components: [{"key": ..., "build_number": ...}]'
                )
            expected = {
                ReleaseMode.FRONTEND_ONLY: (self.frontend_build_number, self.backend_build_number),
                ReleaseMode.BACKEND_ONLY: (self.backend_build_number, self.frontend_build_number),
            }
            if self.mode in expected:
                required, forbidden = expected[self.mode]
                if required is None or forbidden is not None:
                    raise ValueError(
                        f"{self.mode.value} requires exactly its matching build number"
                    )
            elif self.frontend_build_number is None or self.backend_build_number is None:
                raise ValueError("BUNDLE requires both frontend and backend build numbers")
        try:
            zone = ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        if self.scheduled_for.tzinfo is None:
            self.scheduled_for = self.scheduled_for.replace(tzinfo=zone)
        return self


class ScheduleComponentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    component_id: str
    component_key: str
    build_number: int


class ScheduleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str | None
    mode: ReleaseMode
    selected_component_keys: str | None
    component_builds: list[ScheduleComponentResponse]
    frontend_build_number: int | None
    backend_build_number: int | None
    target: str
    scheduled_for_utc: datetime
    timezone: str
    status: ScheduleStatus
    requested_by: str | None
    notification_recipients: list[str]
    release_version: str | None
    release_notes: str | None
    attachment_filename: str | None
    attachment_content_type: str | None
    attachment_size: int | None
    deployment_id: str | None
    release_bundle_id: str | None
    error_message: str | None
    notification_status: EmailDeliveryStatus | None
    notification_sent_at: datetime | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @field_validator("notification_recipients", mode="before")
    @classmethod
    def split_recipients(cls, value: str | list[str] | None) -> list[str]:
        return (
            [item for item in (value or "").split(",") if item]
            if isinstance(value, str)
            else value or []
        )

    @field_validator(
        "scheduled_for_utc",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "notification_sent_at",
        mode="after",
    )
    @classmethod
    def ensure_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )


class ScheduleListResponse(BaseModel):
    items: list[ScheduleResponse]
    total: int
    limit: int
    offset: int


class RunDueResponse(BaseModel):
    processed: int


class EmailOutboxResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow_event_id: str | None
    schedule_id: str | None
    recipients: list[str]
    subject: str
    attachment_filename: str | None
    attachment_content_type: str | None
    attachment_size: int | None
    status: EmailDeliveryStatus
    attempts: int
    next_attempt_at: datetime
    claimed_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None

    @field_validator("recipients", mode="before")
    @classmethod
    def split_recipients(cls, value: str | list[str]) -> list[str]:
        return value.split(",") if isinstance(value, str) else value


class EmailOutboxListResponse(BaseModel):
    items: list[EmailOutboxResponse]
    total: int
    limit: int
    offset: int


class NotificationDispatchResponse(BaseModel):
    queued: int
    sent: int


def normalize_email(value: str) -> str:
    value = value.strip().lower()
    if (
        len(value) > 320
        or any(character.isspace() for character in value)
        or any(character in value for character in "\r\n,;")
        or value.count("@") != 1
    ):
        raise ValueError("Invalid email address")
    local_part, domain = value.split("@")
    if not local_part or len(local_part) > 64 or not domain or len(domain) > 255:
        raise ValueError("Invalid email address")
    return value


class NotificationRecipientCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class NotificationRecipientResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    created_at: datetime


class NotificationRecipientListResponse(BaseModel):
    items: list[NotificationRecipientResponse]
    total: int
