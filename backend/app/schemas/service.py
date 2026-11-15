from datetime import UTC, datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_serializer, model_validator

Name = Annotated[str, Field(min_length=1, max_length=120, strict=True)]
Interval = Annotated[int, Field(ge=10, le=2147483647, strict=True)]
Timeout = Annotated[int, Field(ge=1, le=2147483647, strict=True)]


class ServiceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: Name
    url: HttpUrl
    check_interval_seconds: Interval = 60
    timeout_seconds: Timeout = 5
    is_active: Annotated[bool, Field(strict=True)] = True

    @model_validator(mode="after")
    def validate_timing(self) -> Self:
        if self.timeout_seconds >= self.check_interval_seconds:
            raise ValueError("timeout_seconds must be less than check_interval_seconds")
        return self


class ServiceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: Name | None = None
    url: HttpUrl | None = None
    check_interval_seconds: Interval | None = None
    timeout_seconds: Timeout | None = None
    is_active: Annotated[bool, Field(strict=True)] | None = None

    @model_validator(mode="after")
    def reject_null(self) -> Self:
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("Service fields cannot be null")
        return self


class ServiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    url: str
    check_interval_seconds: int
    timeout_seconds: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def serialize_utc(self, value: datetime) -> str:
        # SQLite unit fixtures return naive UTC; PostgreSQL returns aware timestamps.
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class ServicePage(BaseModel):
    items: list[ServiceResponse]
    total: int
    limit: int
    offset: int
