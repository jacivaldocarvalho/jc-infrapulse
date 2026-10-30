import asyncio
import ssl
import subprocess
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.models.check_result import CheckStatus
from app.monitoring.checker import CheckTarget, HTTPChecker
from app.monitoring.transport import PinnedBackend

pytestmark = pytest.mark.anyio


@pytest.fixture
def tls_files(tmp_path: Path) -> tuple[Path, Path]:
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-subj",
            "/CN=api.example.com",
            "-addext",
            "subjectAltName=DNS:api.example.com",
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


async def test_real_pinned_socket_preserves_host_sni_and_ignores_proxies(
    tls_files: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cert, key = tls_files
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert, key)
    names: list[str | None] = []
    server_context.set_servername_callback(lambda sock, name, context: names.append(name))
    request: list[bytes] = []

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(b"HTTP/1.1 200 OK\r\nSet-Cookie: session=secret\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0, ssl=server_context)
    port = server.sockets[0].getsockname()[1]
    settings = Settings(
        _env_file=None, postgres_password=SecretStr("test-only"), monitoring_allowed_ports=[port]
    )
    addresses: list[str] = []

    async def resolver(host: str, requested_port: int) -> list[str]:
        return ["93.184.216.34"]

    def controlled_backend(host: str, requested_port: int, address: str) -> PinnedBackend:
        addresses.append(address)
        # Test-only substitution AFTER policy validation, never available via settings.
        return PinnedBackend(host, requested_port, "127.0.0.1")

    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    context = ssl.create_default_context(cafile=str(cert))
    checker = HTTPChecker(
        settings, resolver=resolver, backend_factory=controlled_backend, ssl_context=context
    )
    target = CheckTarget(uuid4(), f"https://api.example.com:{port}/health", 3, 10)
    try:
        # Even numeric getaddrinfo is prohibited here: sockets must bypass it entirely.
        with patch.object(
            asyncio.get_running_loop(), "getaddrinfo", side_effect=AssertionError("DNS repeated")
        ):
            assert (await checker.check(target)).status == CheckStatus.UP
            assert (await checker.check(target)).status == CheckStatus.UP
        assert names == ["api.example.com", "api.example.com"]
        assert addresses == ["93.184.216.34"] * 2
        assert len(request) == 2
        for data in request:
            assert f"Host: api.example.com:{port}\r\n".encode() in data
            assert b"User-Agent: InfraPulse/" in data
            assert b"Cookie:" not in data and b"Authorization:" not in data
            for header in (b"traceparent:", b"tracestate:", b"baggage:"):
                assert header not in data.lower()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize("trusted", [False, True])
async def test_real_tls_rejects_untrusted_certificate_and_wrong_hostname(
    tls_files: tuple[Path, Path], trusted: bool
) -> None:
    cert, key = tls_files
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0, ssl=context)
    port = server.sockets[0].getsockname()[1]
    settings = Settings(
        _env_file=None, postgres_password=SecretStr("test-only"), monitoring_allowed_ports=[port]
    )

    async def resolver(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    checker = HTTPChecker(
        settings,
        resolver=resolver,
        backend_factory=lambda host, port, ip: PinnedBackend(host, port, "127.0.0.1"),
        ssl_context=ssl.create_default_context(cafile=str(cert)) if trusted else None,
    )
    try:
        result = await checker.check(
            CheckTarget(uuid4(), f"https://wrong.example.com:{port}", 3, 10)
        )
        assert result.status == CheckStatus.TLS_ERROR
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize("requested_host", ["api.example.com", "api.example.com."])
async def test_pinned_backend_connects_only_to_configured_address(requested_host: str) -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    backend = PinnedBackend("api.example.com", port, "127.0.0.1")
    try:
        with patch.object(
            asyncio.get_running_loop(), "getaddrinfo", side_effect=AssertionError("DNS repeated")
        ):
            stream = await backend.connect_tcp(requested_host, port, timeout=1)
            assert stream.get_extra_info("peername") == ("127.0.0.1", port)
            await stream.aclose()
        import httpcore

        with pytest.raises(httpcore.ConnectError):
            await backend.connect_tcp("other.example.com", port, timeout=1)
    finally:
        server.close()
        await server.wait_closed()
