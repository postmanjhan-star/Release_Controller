"""The small, credential-free part of repository metadata used by the picker."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RepositoryMatch:
    owner: str
    name: str
    description: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @classmethod
    def from_payload(cls, payload: Any) -> "RepositoryMatch":
        if not isinstance(payload, dict):
            raise ValueError("Invalid repository metadata")
        full_name = payload.get("full_name") or payload.get("slug")
        if isinstance(full_name, str) and full_name.count("/") == 1:
            owner, name = full_name.split("/")
        else:
            owner = payload.get("namespace") or payload.get("owner")
            if isinstance(owner, dict):
                owner = owner.get("login")
            name = payload.get("name")
        if not all(isinstance(part, str) and part.strip() for part in (owner, name)):
            raise ValueError("Invalid repository metadata")
        description = payload.get("description")
        if description is None:
            description = ""
        if not isinstance(description, str):
            raise ValueError("Invalid repository description")
        return cls(owner=owner, name=name, description=description)
