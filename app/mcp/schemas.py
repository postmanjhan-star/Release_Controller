from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Environment = Literal["staging", "production"]
Identifier = Annotated[str, StringConstraints(min_length=1, max_length=100, pattern=r"^[\w.-]+$")]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EnvironmentInput(Empty):
    environment: Environment


class ListInput(Empty):
    limit: int = Field(default=10, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)
    environment: Environment | None = None
    status: (
        Literal["PENDING", "PROMOTING", "DEPLOYING", "SUCCESS", "FAILED", "PARTIAL_FAILURE"] | None
    ) = None


class ReleaseInput(Empty):
    release_id: Identifier


class RepositoryInput(Empty):
    repository: Identifier


class PipelineInput(RepositoryInput):
    build_number: int = Field(gt=0)


class Selection(Empty):
    frontend: bool = True
    backend: bool = True

    @model_validator(mode="after")
    def at_least_one(self):
        if not (self.frontend or self.backend):
            raise ValueError("Select at least one component")
        return self


class PlanInput(Selection):
    target_environment: Environment = "production"


class DeployInput(Selection):
    reason: Reason


class PlanReference(Empty):
    plan_id: Identifier


class PromoteInput(PlanReference):
    # Component selection and versions come exclusively from the approved plan.
    plan_id: Identifier
    reason: Reason


class RollbackInput(EnvironmentInput):
    target_release_id: Identifier
