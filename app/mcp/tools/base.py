from dataclasses import dataclass

from pydantic import BaseModel


@dataclass(frozen=True)
class ToolSpec:
    name: str
    schema: type[BaseModel]
    service: str
    description: str
    read_only: bool = True
