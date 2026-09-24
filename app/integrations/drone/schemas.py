from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _drone_datetime(value: Any) -> datetime | None:
    if value in (None, "", 0):
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, (int, float)):
        result = datetime.fromtimestamp(value, tz=timezone.utc)
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return (
        result.replace(tzinfo=timezone.utc)
        if result.tzinfo is None
        else result.astimezone(timezone.utc)
    )


class DroneBuild(BaseModel):
    model_config = ConfigDict(extra="ignore")

    number: int
    status: str
    event: str = ""
    # Drone sets parent on a promotion build to the build it was promoted from.
    # It is how an uncertain promote is reconciled without creating a second one.
    parent: int | None = None
    target: str | None = None
    branch: str = ""
    commit_sha: str = ""
    commit_message: str = ""
    author: str = ""
    created_at: datetime | None = None

    @classmethod
    def from_drone(cls, payload: dict[str, Any]) -> "DroneBuild":
        return cls(
            number=payload.get("number"),
            status=str(payload.get("status", "")).lower(),
            event=str(payload.get("event", "")),
            parent=payload.get("parent") or None,
            target=payload.get("target"),
            branch=payload.get("branch")
            or payload.get("ref", "").removeprefix("refs/heads/")
            or payload.get("target")
            or "",
            commit_sha=payload.get("after")
            or payload.get("commit")
            or payload.get("commit_sha")
            or "",
            commit_message=payload.get("message") or payload.get("commit_message") or "",
            author=payload.get("author_name") or payload.get("author") or "",
            created_at=_drone_datetime(payload.get("created") or payload.get("created_at")),
        )

    def is_promotable(self) -> bool:
        return self.status == "success" and self.event == "push"

    def is_promotion_of(self, source_build_number: int, target: str) -> bool:
        """True when this build is the promotion created from the given build."""
        if self.event not in {"promote", "promotion"}:
            return False
        if (self.target or "") != target:
            return False
        return self.parent == source_build_number


class DroneRepositoryInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    owner: str
    name: str
    slug: str
    link: str | None = None
    default_branch: str | None = None

    @classmethod
    def from_drone(cls, payload: dict[str, Any], owner: str, name: str) -> "DroneRepositoryInfo":
        return cls(
            owner=payload.get("namespace") or owner,
            name=payload.get("name") or name,
            slug=payload.get("slug") or f"{owner}/{name}",
            link=payload.get("link"),
            default_branch=payload.get("default_branch"),
        )


class DroneBuildResponse(DroneBuild):
    promotable: bool = Field(default=False)

    @classmethod
    def from_build(cls, build: DroneBuild) -> "DroneBuildResponse":
        return cls(**build.model_dump(), promotable=build.is_promotable())
