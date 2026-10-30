import asyncio
import logging
import socket
import ssl
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpcore
from pydantic import HttpUrl, ValidationError

from app.core.config import Settings
from app.core.url_policy import validate_execution_url, validate_resolved_address
from app.models.check_result import CheckStatus
from app.monitoring.transport import PinnedBackend, resolve_host

logger = logging.getLogger(__name__)
Resolver = Callable[[str, int], Awaitable[list[str]]]
BackendFactory = Callable[[str, int, str], httpcore.AsyncNetworkBackend]


@dataclass(frozen=True)
class CheckTarget:
    id: UUID
    url: str
    timeout_seconds: int
    check_interval_seconds: int


@dataclass(frozen=True)
class CheckOutcome:
    service_id: UUID
    checked_at: datetime
    status: CheckStatus
    http_status_code: int | None
    response_time_ms: float
    error_message: str | None


class ResponseLimitError(Exception):
    pass


class HTTPChecker:
    def __init__(
        self,
        settings: Settings,
        *,
        resolver: Resolver = resolve_host,
        backend_factory: BackendFactory = PinnedBackend,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self.settings = settings
        self.resolver = resolver
        self.backend_factory = backend_factory
        self.ssl_context = ssl_context or ssl.create_default_context()
        if not self.ssl_context.check_hostname or self.ssl_context.verify_mode != ssl.CERT_REQUIRED:
            raise ValueError("TLS certificate and hostname verification are mandatory")

    async def check(self, target: CheckTarget) -> CheckOutcome:
        run_id = str(uuid4())
        fields = {"run_id": run_id, "service_id": str(target.id)}
        logger.info("check_started", extra=fields)
        started = time.monotonic()
        checked_at = datetime.now(UTC)
        code: int | None = None
        error: str | None = None
        try:
            async with asyncio.timeout(target.timeout_seconds):
                url = HttpUrl(target.url)
                host = validate_execution_url(url, self.settings)
                port = url.port or (443 if url.scheme == "https" else 80)
                addresses = await self.resolver(host, port)
                if not addresses:
                    raise socket.gaierror
                for address in addresses:
                    validate_resolved_address(host, address, self.settings)
                backend = self.backend_factory(host, port, addresses[0])
                async with httpcore.AsyncConnectionPool(
                    ssl_context=self.ssl_context,
                    network_backend=backend,
                    max_connections=1,
                    max_keepalive_connections=0,
                    retries=0,
                    http2=False,
                ) as pool:
                    # Original URL retains Host and SNI; only connect_tcp is replaced.
                    timeout = min(self.settings.monitoring_connect_timeout, target.timeout_seconds)
                    async with pool.stream(
                        "GET",
                        str(url),
                        headers={
                            "User-Agent": "InfraPulse/0.1",
                            "Accept-Encoding": "identity",
                            "Connection": "close",
                        },
                        extensions={"timeout": {"connect": timeout}},
                    ) as response:
                        code = response.status
                        if not 100 <= code <= 599:
                            code = None
                            raise httpcore.RemoteProtocolError
                        size = 0
                        for name, value in response.headers:
                            if name.lower() == b"content-length" and int(value) > (
                                self.settings.monitoring_response_max_bytes
                            ):
                                raise ResponseLimitError
                        async for chunk in response.aiter_stream():
                            size += len(chunk)
                            if size > self.settings.monitoring_response_max_bytes:
                                raise ResponseLimitError
                        status = CheckStatus.UP if 200 <= code < 300 else CheckStatus.DOWN
        except (TimeoutError, httpcore.TimeoutException):
            status, error = CheckStatus.TIMEOUT, "Check deadline exceeded"
        except socket.gaierror:
            status, error = CheckStatus.DNS_ERROR, "DNS resolution failed"
        except ssl.SSLError:
            status, error = CheckStatus.TLS_ERROR, "TLS verification or handshake failed"
        except (httpcore.NetworkError, OSError):
            status, error = CheckStatus.CONNECTION_ERROR, "Connection failed"
        except ResponseLimitError:
            status, error = CheckStatus.HTTP_ERROR, "Response size limit exceeded"
        except (ValueError, ValidationError):
            status, error = CheckStatus.HTTP_ERROR, "Destination rejected by execution policy"
        except (httpcore.ProtocolError, httpcore.UnsupportedProtocol):
            status, error = CheckStatus.HTTP_ERROR, "Invalid HTTP response"
        outcome = CheckOutcome(
            target.id, checked_at, status, code, (time.monotonic() - started) * 1000, error
        )
        if error is not None:
            logger.warning("check_failed", extra=fields | {"check_status": status.value})
        logger.info(
            "check_finished",
            extra=fields
            | {"check_status": status.value, "response_time_ms": outcome.response_time_ms},
        )
        return outcome
