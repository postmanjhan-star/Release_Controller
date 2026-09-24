import re

from pydantic import BaseModel, Field, field_validator

VERSION_PATTERN = re.compile(r"^v?[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")


class PublishRequest(BaseModel):
    version: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    release_notes: str = Field(default="", max_length=20000)
    draft: bool = False
    prerelease: bool = False

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        value = value.strip()
        if not VERSION_PATTERN.fullmatch(value):
            raise ValueError("Version must use semantic version format, for example v1.8.0")
        return value

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()
