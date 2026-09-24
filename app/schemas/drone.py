from pydantic import BaseModel, Field

from app.integrations.drone.schemas import DroneBuildResponse, DroneRepositoryInfo


class DroneBuildListResponse(BaseModel):
    component: str
    repository: DroneRepositoryInfo
    branches: list[str]
    items: list[DroneBuildResponse]


class PromoteRequest(BaseModel):
    target: str | None = Field(default=None, min_length=1, max_length=100)
    requested_by: str | None = Field(default=None, max_length=255)


class DroneStatusResponse(BaseModel):
    status: str
