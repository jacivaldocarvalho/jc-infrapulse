import asyncio
import json
import os
import signal
import socket
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.check_result import CheckResult, CheckStatus
from app.models.service import Service

pytestmark = pytest.mark.integration


def test_check_result_persistence_timezone_and_indexes(postgres_engine: Engine) -> None:
    identifier, check_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    with Session(postgres_engine) as session, session.begin():
        session.add(Service(id=identifier, name="Persisted check", url="https://example.com"))
        session.flush()
        session.add(
            CheckResult(
                id=check_id,
                service_id=identifier,
                checked_at=now,
                status=CheckStatus.UP,
                http_status_code=200,
                response_time_ms=12.5,
            )
        )
    with Session(postgres_engine) as session:
        result = session.get(CheckResult, check_id)
        assert result is not None
        assert result.status == CheckStatus.UP and result.response_time_ms == 12.5
        assert result.checked_at == now
        assert result.created_at.utcoffset() == timedelta(0)
    indexes = inspect(postgres_engine).get_indexes("check_results")
    assert any(index["column_names"] == ["service_id", "checked_at", "id"] for index in indexes)


@pytest.mark.parametrize(
    "fields", [{"response_time_ms": -1}, {"http_status_code": 600}, {"status": "UNKNOWN"}]
)
def test_check_database_constraints(postgres_engine: Engine, fields: dict[str, object]) -> None:
    identifier = uuid4()
    with Session(postgres_engine) as session, session.begin():
        session.add(Service(id=identifier, name="Constraints", url="https://example.com"))
    with pytest.raises(IntegrityError), Session(postgres_engine) as session, session.begin():
        session.add(
            CheckResult(
                **(
                    {
                        "service_id": identifier,
                        "checked_at": datetime.now(UTC),
                        "status": CheckStatus.UP,
                        "response_time_ms": 1,
                    }
                    | fields
                )
            )
        )


def result_count(engine: Engine, identifier: UUID) -> int:
    with Session(engine) as session:
        return (
            session.scalar(
                select(func.count())
                .select_from(CheckResult)
                .where(CheckResult.service_id == identifier)
            )
            or 0
        )


@pytest.mark.anyio
async def test_standalone_worker_real_http_and_graceful_signal(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    # Only runs in compose.test.yaml; traffic remains within its isolated network.
    address = socket.gethostbyname(socket.gethostname())
    requests: list[bytes] = []

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        requests.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handler, "0.0.0.0", 0)
    port = server.sockets[0].getsockname()[1]
    identifier = uuid4()
    with Session(postgres_engine) as session, session.begin():
        # Other tests may have left active services; isolate the worker's selection.
        from sqlalchemy import update

        session.execute(update(Service).values(is_active=False))
        session.add(
            Service(
                id=identifier,
                name="Controlled local HTTP",
                url=f"http://{address}:{port}/health",
                timeout_seconds=2,
                check_interval_seconds=10,
            )
        )
    env = os.environ | {
        "MONITORING_ALLOWED_PORTS": json.dumps([port]),
        "MONITORING_ALLOWED_HOSTS": json.dumps([address]),
        "SERVICE_INTERNAL_HOSTS": json.dumps([address]),
        "MONITORING_INTERNAL_NETWORKS": json.dumps([f"{address}/32"]),
        "MONITORING_POLL_SECONDS": "1",
        "MONITORING_HEARTBEAT_PATH": str(tmp_path / "worker-heartbeat"),
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.worker",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        async with asyncio.timeout(15):
            while not await asyncio.to_thread(result_count, postgres_engine, identifier):
                assert process.returncode is None
                await asyncio.sleep(0.1)
        health = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.worker", "healthcheck", env=env
        )
        assert await health.wait() == 0
        await asyncio.sleep(1.1)
        assert await asyncio.to_thread(result_count, postgres_engine, identifier) == 1
        assert len(requests) == 1
        process.send_signal(signal.SIGTERM)
        output, _ = await asyncio.wait_for(process.communicate(), 10)
        assert process.returncode == 0
        assert b"worker_started" in output and b"worker_stopped" in output
        with Session(postgres_engine) as session:
            result = session.scalar(select(CheckResult).where(CheckResult.service_id == identifier))
            assert result is not None and result.status == CheckStatus.UP
    finally:
        if process.returncode is None:
            process.terminate()
            await asyncio.wait_for(process.communicate(), 10)
        server.close()
        await server.wait_closed()
