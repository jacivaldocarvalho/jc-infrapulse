"""Pinned sockets: DNS is resolved only by the resolver, never by the transport."""

import asyncio
import ipaddress
import socket
import ssl
from collections.abc import Iterable
from typing import Any

import httpcore

SocketOption = tuple[int, int, int | bytes | bytearray] | tuple[int, int, None, int]


async def resolve_host(host: str, port: int) -> list[str]:
    try:
        return [str(ipaddress.ip_address(host))]
    except ValueError:
        records = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
        )
        return list(dict.fromkeys(str(record[4][0]) for record in records))


class PinnedStream(httpcore.AsyncNetworkStream):
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        try:
            async with asyncio.timeout(timeout):
                return await self.reader.read(max_bytes)
        except TimeoutError as exc:
            raise httpcore.ReadTimeout from exc
        except OSError as exc:
            raise httpcore.ReadError from exc

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        try:
            async with asyncio.timeout(timeout):
                self.writer.write(buffer)
                await self.writer.drain()
        except TimeoutError as exc:
            raise httpcore.WriteTimeout from exc
        except OSError as exc:
            raise httpcore.WriteError from exc

    async def aclose(self) -> None:
        self.writer.close()
        try:
            async with asyncio.timeout(1):
                await self.writer.wait_closed()
        except (OSError, TimeoutError):
            pass

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            async with asyncio.timeout(timeout):
                await self.writer.start_tls(ssl_context, server_hostname=server_hostname)
        except BaseException:
            self.writer.close()
            raise
        return self

    def get_extra_info(self, info: str) -> Any:
        if info == "is_readable":
            return self.reader.at_eof()
        return self.writer.get_extra_info(info)


class PinnedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, host: str, port: int, address: str) -> None:
        self.host = host
        self.port = port
        self.address = ipaddress.ip_address(address)

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if host.lower().rstrip(".") != self.host or port != self.port or local_address is not None:
            raise httpcore.ConnectError("Unexpected destination")
        family = socket.AF_INET6 if self.address.version == 6 else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.setblocking(False)
        try:
            async with asyncio.timeout(timeout):
                # sock_connect recognizes numeric addresses with inet_pton; no DNS call.
                await asyncio.get_running_loop().sock_connect(sock, (str(self.address), port))
                reader, writer = await asyncio.open_connection(sock=sock, limit=65536)
            return PinnedStream(reader, writer)
        except TimeoutError as exc:
            sock.close()
            raise httpcore.ConnectTimeout from exc
        except OSError as exc:
            sock.close()
            raise httpcore.ConnectError from exc
        except BaseException:
            sock.close()
            raise
