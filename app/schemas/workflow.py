from datetime import datetime, timezone

from pydantic import BaseModel, field_validator


class WorkflowStepResponse(BaseModel):
    element_id: str
    name: str
    state: str


class WorkflowStateResponse(BaseModel):
    release_id: str
    definition_id: str
    is_complete: bool
    current_element_ids: list[str]
    completed_element_ids: list[str]
    steps: list[WorkflowStepResponse]
    updated_at: datetime

    @field_validator("updated_at", mode="after")
    @classmethod
    def ensure_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
