from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.domain.release.value_objects import ReleaseStatus

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ReleaseCreate(BaseModel):
    repository: NonEmptyString = Field(max_length=255)
    branch: NonEmptyString = Field(max_length=255)
    commit_sha: NonEmptyString = Field(max_length=64)
    environment: NonEmptyString = Field(max_length=100)
    message: str | None = None


class ReleaseApprove(BaseModel):
    approved_by: NonEmptyString | None = Field(default=None, max_length=255)


class ReleaseReject(BaseModel):
    rejected_by: NonEmptyString | None = Field(default=None, max_length=255)
    message: str | None = None


class ReleaseDeploymentFinish(BaseModel):
    status: Literal[ReleaseStatus.SUCCESS, ReleaseStatus.FAILED]
    message: str | None = None


class ReleaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    repository: str
    branch: str
    commit_sha: str
    environment: str
    status: ReleaseStatus
    created_at: datetime
    updated_at: datetime
    approved_by: str | None
    approved_at: datetime | None
    rejected_by: str | None
    rejected_at: datetime | None
    deploy_started_at: datetime | None
    deploy_finished_at: datetime | None
    message: str | None

    @field_validator(
        "created_at",
        "updated_at",
        "approved_at",
        "rejected_at",
        "deploy_started_at",
        "deploy_finished_at",
        mode="after",
    )
    @classmethod
    def ensure_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class ReleaseListResponse(BaseModel):
    items: list[ReleaseResponse]
    total: int
    limit: int
    offset: int
