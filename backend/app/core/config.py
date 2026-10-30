import ipaddress
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    service_name: str = "infrapulse-api"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    postgres_host: str = "localhost"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = "infrapulse"
    postgres_user: str = "infrapulse"
    postgres_password: SecretStr = Field(min_length=1)
    db_connect_timeout: int = Field(default=3, ge=1, le=30)
    db_statement_timeout_ms: int = Field(default=3000, ge=1, le=30000)
    service_internal_hosts: list[str] = Field(default_factory=list)
    monitoring_internal_networks: list[str] = Field(default_factory=list)
    monitoring_allowed_ports: list[int] = Field(default_factory=lambda: [80, 443])
    monitoring_allowed_hosts: list[str] = Field(default_factory=list)
    monitoring_concurrency: int = Field(default=10, ge=1, le=100)
    monitoring_poll_seconds: float = Field(default=5, ge=1, le=60)
    monitoring_connect_timeout: float = Field(default=3, gt=0, le=30)
    monitoring_response_max_bytes: int = Field(default=1048576, ge=1, le=10485760)
    monitoring_shutdown_seconds: float = Field(default=15, ge=1, le=60)
    monitoring_heartbeat_path: str = "/tmp/infrapulse-worker-heartbeat"

    @field_validator("monitoring_allowed_ports")
    @classmethod
    def validate_ports(cls, value: list[int]) -> list[int]:
        if not value or any(port < 1 or port > 65535 for port in value):
            raise ValueError("Monitoring requires valid allowed ports")
        return value

    @field_validator("monitoring_internal_networks")
    @classmethod
    def validate_networks(cls, value: list[str]) -> list[str]:
        for network in value:
            parsed = ipaddress.ip_network(network)
            if not parsed.is_private or parsed.prefixlen == 0:
                raise ValueError("Internal networks must be explicit private networks")
        return value

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )
