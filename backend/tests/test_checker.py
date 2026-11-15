import asyncio
import logging
import socket
import ssl
from uuid import uuid4

import httpcore
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.url_policy import validate_resolved_address
from app.models.check_result import CheckStatus
from app.monitoring.checker import CheckTarget, HTTPChecker

pytestmark = pytest.mark.anyio
PUBLIC = "93.184.216.34"


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, postgres_password=SecretStr("test-only"))


def target(url: str = "https://api.example.com/health?token=secret") -> CheckTarget:
    return CheckTarget(uuid4(), url, 1, 10)


async def public_resolver(host: str, port: int) -> list[str]:
    return [PUBLIC]


def checker_for(settings: Settings, data: bytes) -> HTTPChecker:
    return HTTPChecker(
        settings,
        resolver=public_resolver,
        backend_factory=lambda host, port, address: httpcore.AsyncMockBackend([data]),
    )


@pytest.mark.parametrize(
    "code,status", [(200, "UP"), (204, "UP"), (302, "DOWN"), (404, "DOWN"), (500, "DOWN")]
)
async def test_http_status(settings: Settings, code: int, status: str) -> None:
    checker = checker_for(settings, f"HTTP/1.1 {code} Test\r\nContent-Length: 0\r\n\r\n".encode())
    result = await checker.check(target())
    assert result.status == status
    assert result.http_status_code == code
    assert result.response_time_ms >= 0
    assert result.checked_at.utcoffset() is not None


@pytest.mark.parametrize(
    "error,status",
    [
        (socket.gaierror("secret"), CheckStatus.DNS_ERROR),
        (ssl.SSLCertVerificationError("secret"), CheckStatus.TLS_ERROR),
        (ConnectionRefusedError("secret"), CheckStatus.CONNECTION_ERROR),
        (httpcore.ConnectError("secret"), CheckStatus.CONNECTION_ERROR),
        (httpcore.ReadTimeout("secret"), CheckStatus.TIMEOUT),
        (httpcore.RemoteProtocolError("secret"), CheckStatus.HTTP_ERROR),
    ],
)
async def test_failure_causes(settings: Settings, error: Exception, status: CheckStatus) -> None:
    async def fail(host: str, port: int) -> list[str]:
        raise error

    result = await HTTPChecker(settings, resolver=fail).check(target())
    assert result.status == status
    assert result.error_message is not None and "secret" not in result.error_message


async def test_total_timeout_includes_dns(settings: Settings) -> None:
    async def slow(host: str, port: int) -> list[str]:
        await asyncio.Event().wait()
        return []

    result = await HTTPChecker(settings, resolver=slow).check(target())
    assert result.status == CheckStatus.TIMEOUT


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost",
        "http://127.0.0.1",
        "http://10.1.2.3",
        "http://169.254.169.254",
        "http://[::1]",
        "http://[fe80::1]",
        "http://[fd00:ec2::254]",
        "http://user:password@api.example.com",
        "http://api.example.com:8080",
        "ftp://example.com",
    ],
)
async def test_forbidden_urls_never_resolve(settings: Settings, url: str) -> None:
    async def forbidden(host: str, port: int) -> list[str]:
        pytest.fail("Forbidden URL reached DNS")

    assert (
        await HTTPChecker(settings, resolver=forbidden).check(target(url))
    ).status == CheckStatus.HTTP_ERROR


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "::1",
        "fe80::1",
        "fc00::1",
        "224.0.0.1",
        "0.0.0.0",
        "100.100.100.200",
        "168.63.129.16",
        "192.0.2.1",
        "::ffff:127.0.0.1",
        "2002:7f00:1::",
        "fd00:ec2::254",
        "64:ff9b::a00:1",
        "64:ff9b:1::a00:1",
    ],
)
async def test_all_dns_addresses_validated(settings: Settings, address: str) -> None:
    async def mixed(host: str, port: int) -> list[str]:
        return [PUBLIC, address]

    def forbidden(host: str, port: int, address: str) -> httpcore.AsyncNetworkBackend:
        pytest.fail("Rejected DNS answer reached transport")

    result = await HTTPChecker(settings, resolver=mixed, backend_factory=forbidden).check(target())
    assert result.status == CheckStatus.HTTP_ERROR


async def test_rebinding_no_second_resolution(settings: Settings) -> None:
    calls = 0
    connections: list[tuple[str, int, str]] = []

    async def rebinding(host: str, port: int) -> list[str]:
        nonlocal calls
        calls += 1
        return [PUBLIC] if calls == 1 else ["127.0.0.1"]

    def backend(host: str, port: int, address: str) -> httpcore.AsyncNetworkBackend:
        connections.append((host, port, address))
        return httpcore.AsyncMockBackend([b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"])

    checker = HTTPChecker(settings, resolver=rebinding, backend_factory=backend)
    assert (await checker.check(target())).status == CheckStatus.UP
    assert calls == 1 and connections == [("api.example.com", 443, PUBLIC)]
    assert (await checker.check(target())).status == CheckStatus.HTTP_ERROR
    assert calls == 2 and len(connections) == 1


async def test_redirect_not_followed(settings: Settings) -> None:
    checker = checker_for(
        settings,
        b"HTTP/1.1 302 Found\r\nLocation: http://169.254.169.254/\r\nContent-Length: 0\r\n\r\n",
    )
    result = await checker.check(target())
    assert result.status == CheckStatus.DOWN and result.http_status_code == 302


@pytest.mark.parametrize(
    "body",
    [
        b"Content-Length: 100\r\n\r\n",
        b"Transfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n",
    ],
)
async def test_response_limit(settings: Settings, body: bytes) -> None:
    settings.monitoring_response_max_bytes = 4
    result = await checker_for(settings, b"HTTP/1.1 200 OK\r\n" + body).check(target())
    assert result.status == CheckStatus.HTTP_ERROR
    assert result.error_message == "Response size limit exceeded"


async def test_tls_verification_cannot_be_disabled(settings: Settings) -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    with pytest.raises(ValueError, match="mandatory"):
        HTTPChecker(settings, ssl_context=context)


async def test_internal_exception_requires_host_and_network(settings: Settings) -> None:
    settings.service_internal_hosts = ["api.internal"]
    with pytest.raises(ValueError):
        validate_resolved_address("api.internal", "10.1.2.3", settings)
    settings.monitoring_internal_networks = ["10.1.2.0/24"]
    validate_resolved_address("api.internal", "10.1.2.3", settings)
    for host, address in [
        ("other.internal", "10.1.2.3"),
        ("api.internal", "10.2.0.1"),
        ("api.internal", "169.254.169.254"),
        ("api.internal", "127.0.0.1"),
    ]:
        with pytest.raises(ValueError):
            validate_resolved_address(host, address, settings)


async def test_logs_are_correlated_and_sanitized(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        await checker_for(settings, b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n").check(target())
    records = [r for r in caplog.records if r.name == "app.monitoring.checker"]
    assert [r.message for r in records] == ["check_started", "check_finished"]
    assert records[0].__dict__["run_id"] == records[1].__dict__["run_id"]
    assert "secret" not in caplog.text
