from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["healthy"] = "healthy"
    service: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]


class ApiInfo(BaseModel):
    name: str
    version: str
