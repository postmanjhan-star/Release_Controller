from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GiteaRepository:
    owner: str
    name: str
    full_name: str
    html_url: str | None = None

    @classmethod
    def from_gitea(cls, payload: dict[str, Any], owner: str, name: str) -> GiteaRepository:
        return cls(
            owner=owner,
            name=name,
            full_name=str(payload.get("full_name") or f"{owner}/{name}"),
            html_url=payload.get("html_url"),
        )


@dataclass(frozen=True)
class GiteaTag:
    name: str
    commit_sha: str

    @classmethod
    def from_gitea(cls, payload: dict[str, Any]) -> GiteaTag:
        commit = payload.get("commit") or {}
        sha = commit.get("sha") or commit.get("id")
        if not payload.get("name") or not sha:
            raise ValueError("Gitea returned invalid tag metadata")
        return cls(name=str(payload["name"]), commit_sha=str(sha))


@dataclass(frozen=True)
class GiteaRelease:
    id: int | str
    tag_name: str
    name: str
    html_url: str | None
    target_commitish: str | None = None

    @classmethod
    def from_gitea(cls, payload: dict[str, Any]) -> GiteaRelease:
        release_id = payload.get("id")
        tag_name = payload.get("tag_name")
        if release_id is None or not tag_name:
            raise ValueError("Gitea returned invalid release metadata")
        return cls(
            id=release_id,
            tag_name=str(tag_name),
            name=str(payload.get("name") or tag_name),
            html_url=payload.get("html_url") or payload.get("url"),
            target_commitish=payload.get("target_commitish"),
        )
