from datetime import datetime, timezone
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.domain.orchestration.value_objects import (
    BundleStatus,
    DeploymentStatus,
    PublishStatus,
    ReleaseMode,
)

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ComponentBuildSelection(BaseModel):
    key: NonEmptyString = Field(max_length=50)
    build_number: int = Field(gt=0)


class BundlePromoteRequest(BaseModel):
    """Which components of which project to release, and from which builds.

    The v2.2 body -- two build numbers named after the two components that used
    to be the only ones -- is still accepted and read as
    [{backend}, {frontend}] of the default project.
    """

    project_id: str | None = Field(default=None, max_length=100)
    components: list[ComponentBuildSelection] = Field(default_factory=list)
    frontend_build_number: int | None = Field(default=None, gt=0)
    backend_build_number: int | None = Field(default=None, gt=0)
    target: NonEmptyString | None = Field(default=None, max_length=100)
    requested_by: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def require_something_to_release(self) -> "BundlePromoteRequest":
        if self.components:
            keys = [item.key for item in self.components]
            if len(set(keys)) != len(keys):
                raise ValueError("Each component may appear only once in a release")
            return self
        if self.frontend_build_number is None and self.backend_build_number is None:
            raise ValueError(
                "A release needs at least one component: send "
                'components: [{"key": ..., "build_number": ...}]'
            )
        return self


class DeploymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    release_bundle_id: str | None
    project_id: str
    component_id: str
    component: str
    drone_owner: str
    drone_repository: str
    source_build_number: int
    promotion_build_number: int | None
    commit_sha: str
    branch: str
    target: str
    status: DeploymentStatus
    publish_status: PublishStatus
    version: str | None
    gitea_release_id: str | None
    gitea_release_tag: str | None
    gitea_release_url: str | None
    current_stage: str | None
    failed_stage: str | None
    error_code: str | None
    error_message: str | None
    cancel_reason: str | None
    poll_error_code: str | None
    poll_error_message: str | None
    poll_failure_count: int
    requested_by: str | None
    attachment_filename: str | None
    attachment_content_type: str | None
    attachment_size: int | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    failed_at: datetime | None
    cancelled_at: datetime | None
    finished_at: datetime | None
    publish_started_at: datetime | None
    published_at: datetime | None
    publish_failed_at: datetime | None
    publish_error_code: str | None
    publish_error_message: str | None

    @field_validator(
        "created_at",
        "updated_at",
        "started_at",
        "failed_at",
        "cancelled_at",
        "finished_at",
        "publish_started_at",
        "published_at",
        "publish_failed_at",
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


class BundleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str | None
    mode: ReleaseMode
    selected_component_keys: str | None
    target: str
    status: BundleStatus
    deployment_status: BundleStatus
    publish_status: PublishStatus
    version: str | None
    release_name: str | None
    release_notes: str | None
    workflow_instance_id: str | None
    current_stage: str | None
    failed_stage: str | None
    error_code: str | None
    error_message: str | None
    requested_by: str | None
    attachment_filename: str | None
    attachment_content_type: str | None
    attachment_size: int | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    failed_at: datetime | None
    finished_at: datetime | None
    publish_started_at: datetime | None
    published_at: datetime | None
    publish_failed_at: datetime | None
    publish_error_code: str | None
    publish_error_message: str | None
    deployments: list[DeploymentResponse]
    publish_records: list["PublishRecordResponse"]

    @field_validator(
        "created_at",
        "updated_at",
        "started_at",
        "failed_at",
        "finished_at",
        "publish_started_at",
        "published_at",
        "publish_failed_at",
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


class BundleListResponse(BaseModel):
    items: list[BundleResponse]
    total: int
    limit: int
    offset: int


class DeploymentListResponse(BaseModel):
    items: list[DeploymentResponse]
    total: int
    limit: int
    offset: int


class WorkflowEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    release_bundle_id: str | None
    deployment_id: str | None
    workflow_instance_id: str | None
    stage: str
    event_type: str
    status: str | None
    error_code: str | None
    message: str | None
    actor: str
    actor_source: str
    component: str | None
    target: str | None
    created_at: datetime

    @field_validator("created_at", mode="after")
    @classmethod
    def ensure_utc(cls, value: datetime) -> datetime:
        return (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )


class WorkflowEventListResponse(BaseModel):
    items: list[WorkflowEventResponse]


class PublishRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    release_bundle_id: str | None
    deployment_id: str
    project_id: str | None
    component_id: str | None
    component: str
    version: str
    repo_owner: str
    repo_name: str
    commit_sha: str
    tag_name: str
    gitea_release_id: str | None
    gitea_release_url: str | None
    status: str
    error_code: str | None
    error_message: str | None
    requested_by: str | None
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None

    @field_validator("created_at", "updated_at", "published_at", mode="after")
    @classmethod
    def ensure_publish_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )


class PublishRecordListResponse(BaseModel):
    items: list[PublishRecordResponse]
    total: int
    limit: int
    offset: int


class UnifiedReleaseListResponse(BaseModel):
    items: list["ReleaseResponse | BundleResponse"]
    total: int
    limit: int
    offset: int


from app.schemas.release import ReleaseResponse  # noqa: E402

UnifiedReleaseListResponse.model_rebuild()
BundleResponse.model_rebuild()
