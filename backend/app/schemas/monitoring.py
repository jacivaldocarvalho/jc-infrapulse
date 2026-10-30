from datetime import UTC, datetime, timedelta
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_serializer, model_validator

from app.models.check_result import CheckStatus


class CheckWindow(BaseModel):
    start: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC) - timedelta(days=1))
    end: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.end <= self.start or self.end - self.start > timedelta(days=90):
            raise ValueError("Period must be positive and no greater than 90 days")
        self.start = self.start.astimezone(UTC)
        self.end = self.end.astimezone(UTC)
        return self


class UTCResponse(BaseModel):
    @field_serializer(
        "checked_at", "created_at", "last_checked_at", "start", "end", check_fields=False
    )
    def serialize_utc(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class CheckResponse(UTCResponse):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    service_id: UUID
    checked_at: datetime
    status: CheckStatus
    http_status_code: int | None
    response_time_ms: float | None
    error_message: str | None
    created_at: datetime


class CheckPage(BaseModel):
    items: list[CheckResponse]
    total: int
    limit: int
    offset: int


class ServiceStatus(UTCResponse):
    service_id: UUID
    last_status: CheckStatus | None
    last_checked_at: datetime | None


class ServiceMetrics(ServiceStatus):
    start: datetime
    end: datetime
    total_checks: int
    up_checks: int
    down_checks: int
    uptime_percentage: float | None
    average_response_time_ms: float | None
    p95_response_time_ms: float | None
