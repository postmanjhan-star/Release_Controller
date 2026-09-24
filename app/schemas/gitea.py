from typing import Literal

from pydantic import BaseModel


class GiteaHealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    server: str
    endpoint: str
    detail: str | None = None
