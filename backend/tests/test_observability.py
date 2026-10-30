import asyncio
import json
import logging
import socket
import threading
import time
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import generate_latest
from pydantic import SecretStr
from test_scheduler import BlockingChecker, MemoryStore

from app.core.config import Settings
from app.core.logging import JsonFormatter
from app.main import create_app
from app.models.check_result import CheckStatus
from app.monitoring.checker import CheckOutcome, CheckTarget, HTTPChecker
from app.monitoring.scheduler import MonitoringScheduler
from app.observability.metrics import Metrics, MetricsServer
from app.observability.processor import BoundedSpanProcessor
from app.observability.tracing import Telemetry, operation

pytestmark = pytest.mark.anyio


def config(**values: object) -> Settings:
    return Settings.model_validate(
        {"postgres_password": SecretStr("test-only"), "telemetry_sample_ratio": 1, **values}
    )


def value(metrics: Metrics, name: str, labels: dict[str, str] | None = None) -> float:
    sample = metrics.registry.get_sample_value(name, labels or {})
    assert sample is not None
    return sample


async def test_listener_lifecycle_and_event_loop() -> None:
    metrics = Metrics()
    listener = MetricsServer(metrics, "127.0.0.1", 0)
    await listener.start()
    pulse = asyncio.create_task(metrics.pulse())
    async with httpx.AsyncClient(trust_env=False) as client:
        response = await client.get(f"http://127.0.0.1:{listener.port}/metrics")
    assert response.status_code == 200
    assert "infrapulse_checks_total" in response.text
    assert value(metrics, "infrapulse_event_loop_last_tick_timestamp_seconds") > 0
    pulse.cancel()
    await asyncio.gather(pulse, return_exceptions=True)
    await listener.close()
    assert listener.thread is not None and not listener.thread.is_alive()
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", listener.port))


async def test_api_red_routes_privacy_and_external_context() -> None:
    application = create_app(config(metrics_enabled=True, api_metrics_port=0))
    exporter = InMemorySpanExporter()
    async with application.router.lifespan_context(application):
        original = application.state.telemetry
        telemetry = Telemetry(config(), "api", exporter)
        application.state.telemetry = telemetry
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application), base_url="http://test"
            ) as client:
                assert (await client.get("/health?token=secret")).status_code == 200
                assert (await client.get("/metrics")).status_code == 404
                response = await client.get(
                    "/api/v1/services/not-a-uuid?token=secret",
                    headers={
                        "traceparent": "00-11111111111111111111111111111111-2222222222222222-01",
                        "baggage": "token=secret",
                        "authorization": "Bearer secret",
                        "tracestate": "secret=value",
                    },
                )
                assert response.status_code == 422
                assert (await client.get("/unknown-sensitive-token")).status_code == 404
            assert telemetry.provider is not None and telemetry.provider.force_flush(2000)
            spans = exporter.get_finished_spans()
            server = next(s for s in spans if s.name == "GET /api/v1/services/{service_id}")
            assert server.parent is None
            assert len(server.links) == 1 and not server.links[0].context.trace_state
            assert server.context is not None and server.context.trace_id != int("1" * 32, 16)
            assert set(server.attributes or {}) == {
                "http.request.method",
                "http.route",
                "http.response.status_code",
            }
            assert all(not s.events for s in spans)
            text = generate_latest(application.state.metrics.registry).decode()
            assert 'route="/api/v1/services/{service_id}"' in text
            for secret in ("not-a-uuid", "secret", "unknown-sensitive-token", "token="):
                assert secret not in text and secret not in str(
                    [(s.name, s.attributes, s.events) for s in spans]
                )
            assert (
                value(
                    application.state.metrics,
                    "http_server_requests_total",
                    {
                        "method": "GET",
                        "route": "/api/v1/services/{service_id}",
                        "status_code": "422",
                    },
                )
                == 1
            )
            assert (
                value(
                    application.state.metrics,
                    "http_server_request_duration_seconds_count",
                    {"method": "GET", "route": "unmatched", "status_code": "404"},
                )
                == 1
            )
        finally:
            await telemetry.close()
            application.state.telemetry = original


async def test_unsampled_remote_context_cannot_force_sampling() -> None:
    exporter = InMemorySpanExporter()
    application = create_app(config())
    async with application.router.lifespan_context(application):
        telemetry = Telemetry(config(telemetry_sample_ratio=0), "api", exporter)
        application.state.telemetry = telemetry
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client:
            await client.get(
                "/api/v1",
                headers={"traceparent": "00-11111111111111111111111111111111-2222222222222222-01"},
            )
        assert telemetry.provider is not None
        telemetry.provider.force_flush(2000)
        assert not exporter.get_finished_spans()
        await telemetry.close()


@pytest.mark.parametrize("status", list(CheckStatus))
async def test_outcomes_and_persistence_are_distinct(status: CheckStatus) -> None:
    class Checker:
        async def check(self, target: CheckTarget) -> CheckOutcome:
            from datetime import UTC, datetime

            return CheckOutcome(
                target.id,
                datetime.now(UTC),
                status,
                200 if status == CheckStatus.UP else None,
                125,
                None,
            )

    metrics, store = Metrics(), MemoryStore(1)
    scheduler = MonitoringScheduler(config(), store, Checker(), metrics)
    await scheduler.tick()
    await asyncio.gather(*scheduler.tasks.values())
    assert value(metrics, "infrapulse_checks_total", {"status": status.value}) == 1
    assert (
        value(metrics, "infrapulse_check_duration_seconds_sum", {"status": status.value}) == 0.125
    )
    assert value(metrics, "infrapulse_check_duration_seconds_count", {"status": status.value}) == 1
    assert value(metrics, "infrapulse_check_results_persisted_total", {"status": status.value}) == 1
    assert value(metrics, "infrapulse_checks_in_flight") == 0
    assert (value(metrics, "infrapulse_last_up_check_timestamp_seconds") > 0) == (
        status == CheckStatus.UP
    )
    assert value(metrics, "infrapulse_scheduler_last_healthy_cycle_timestamp_seconds") > 0
    for family in metrics.registry.collect():
        for sample in family.samples:
            assert set(sample.labels) <= {"status", "stage", "outcome", "le"}


async def test_persistence_failure_is_internal_and_not_down() -> None:
    class FailingStore(MemoryStore):
        def persist(self, outcome: CheckOutcome) -> bool:
            raise RuntimeError("secret postgres://password")

    store, checker, metrics = FailingStore(1), BlockingChecker(), Metrics()
    checker.release.set()
    scheduler = MonitoringScheduler(config(), store, checker, metrics)
    await scheduler.tick()
    await asyncio.gather(*scheduler.tasks.values())
    assert value(metrics, "infrapulse_checks_total", {"status": "UP"}) == 1
    assert value(metrics, "infrapulse_check_results_persisted_total", {"status": "UP"}) == 0
    assert value(metrics, "infrapulse_last_up_check_timestamp_seconds") == 0
    assert value(metrics, "infrapulse_checks_total", {"status": "DOWN"}) == 0
    assert value(metrics, "infrapulse_scheduler_errors_total", {"stage": "persist"}) == 1
    assert value(metrics, "infrapulse_checks_in_flight") == 0


async def test_internal_checker_exception_does_not_create_outcome() -> None:
    class Checker:
        async def check(self, target: CheckTarget) -> CheckOutcome:
            raise RuntimeError("https://secret/?token=secret")

    metrics = Metrics()
    scheduler = MonitoringScheduler(config(), MemoryStore(1), Checker(), metrics)
    await scheduler.tick()
    await asyncio.gather(*scheduler.tasks.values())
    assert value(metrics, "infrapulse_scheduler_errors_total", {"stage": "checker"}) == 1
    assert (
        sum(
            s.value
            for f in metrics.registry.collect()
            for s in f.samples
            if s.name == "infrapulse_checks_total"
        )
        == 0
    )
    assert value(metrics, "infrapulse_checks_in_flight") == 0


async def test_selection_error_instrumented() -> None:
    class Store(MemoryStore):
        def due(self, limit: int, excluded: set[UUID]) -> list[CheckTarget]:
            raise RuntimeError("secret")

    metrics = Metrics()
    scheduler = MonitoringScheduler(config(), Store(), BlockingChecker(), metrics)
    with pytest.raises(RuntimeError):
        await scheduler.tick()
    assert value(metrics, "infrapulse_scheduler_errors_total", {"stage": "selection"}) == 1
    assert value(metrics, "infrapulse_scheduler_cycles_total", {"outcome": "error"}) == 1
    assert value(metrics, "infrapulse_scheduler_last_healthy_cycle_timestamp_seconds") == 0


async def test_saturation_and_cancel_finally() -> None:
    metrics, checker = Metrics(), BlockingChecker()
    scheduler = MonitoringScheduler(
        config(monitoring_concurrency=1), MemoryStore(2), checker, metrics
    )
    await scheduler.tick()
    await checker.started.wait()
    assert value(metrics, "infrapulse_checks_in_flight") == 1
    await scheduler.tick()
    assert value(metrics, "infrapulse_scheduler_cycles_total", {"outcome": "saturated"}) == 1
    task = next(iter(scheduler.tasks.values()))
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert value(metrics, "infrapulse_checks_in_flight") == 0
    assert value(metrics, "infrapulse_check_tasks_cancelled_total") == 1


async def test_real_dns_timeout_metric() -> None:
    async def resolver(host: str, port: int) -> list[str]:
        await asyncio.Event().wait()
        return []

    metrics, store = Metrics(), MemoryStore(1)
    first = store.targets[0]
    store.targets[0] = CheckTarget(first.id, first.url, 1, 10)
    scheduler = MonitoringScheduler(
        config(), store, HTTPChecker(config(), resolver=resolver), metrics
    )
    await scheduler.tick()
    await asyncio.gather(*scheduler.tasks.values())
    assert value(metrics, "infrapulse_checks_total", {"status": "TIMEOUT"}) == 1
    assert value(metrics, "infrapulse_check_duration_seconds_sum", {"status": "TIMEOUT"}) >= 1


async def test_trace_log_correlation_and_safe_error() -> None:
    exporter = InMemorySpanExporter()
    telemetry = Telemetry(config(), "worker", exporter)
    with pytest.raises(RuntimeError), operation(telemetry.tracer, "monitoring.check") as span:
        record = logging.LogRecord("app", logging.INFO, "", 0, "check_started", (), None)
        data = json.loads(JsonFormatter().format(record))
        assert data["trace_id"] == format(span.get_span_context().trace_id, "032x")
        assert data["span_id"] == format(span.get_span_context().span_id, "016x")
        raise RuntimeError("https://secret:password@host/?token=secret")
    assert telemetry.provider is not None
    telemetry.provider.force_flush(2000)
    finished = exporter.get_finished_spans()
    assert finished[0].status.status_code.name == "ERROR"
    assert not finished[0].events and "secret" not in str(finished[0].attributes)
    await telemetry.close()


async def test_collector_failure_keeps_api_working_and_stops_exporter() -> None:
    application = create_app(
        config(
            telemetry_enabled=True,
            telemetry_endpoint="http://127.0.0.1:1/v1/traces",
            telemetry_timeout_seconds=0.1,
        )
    )
    started = time.monotonic()
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client:
            assert (await client.get("/api/v1")).status_code == 200
    assert time.monotonic() - started < 5
    assert not any(t.name == "infrapulse-traces" and t.is_alive() for t in threading.enumerate())


async def test_bounded_flush_and_thread_shutdown() -> None:
    class SlowExporter(SpanExporter):
        def export(self, spans: object) -> SpanExportResult:
            time.sleep(0.05)
            return SpanExportResult.SUCCESS

    processor = BoundedSpanProcessor(SlowExporter())
    telemetry = Telemetry(config(), "worker", InMemorySpanExporter())
    assert telemetry.provider is not None
    telemetry.provider.add_span_processor(processor)
    with operation(telemetry.tracer, "test"):
        pass
    assert not processor.force_flush(1)
    await telemetry.close()
    assert not processor.thread.is_alive()


async def test_registries_are_independent() -> None:
    first, second = Metrics(), Metrics()
    first.checks.labels("UP").inc()
    assert value(second, "infrapulse_checks_total", {"status": "UP"}) == 0


async def test_worker_listener_signals_and_collector_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from unittest.mock import Mock

    from sqlalchemy import Engine

    import app.worker as worker

    checker, store = BlockingChecker(), MemoryStore(1)
    engine = Mock(spec=Engine)
    listeners: list[MetricsServer] = []
    callbacks: list[object] = []
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop, "add_signal_handler", lambda sig, callback: callbacks.append(callback)
    )
    monkeypatch.setattr(loop, "remove_signal_handler", lambda sig: True)
    monkeypatch.setattr(worker, "create_database_engine", lambda settings: engine)
    monkeypatch.setattr(worker, "SQLMonitoringStore", lambda engine: store)
    monkeypatch.setattr(worker, "HTTPChecker", lambda settings: checker)

    class Listener(MetricsServer):
        async def start(self) -> None:
            await super().start()
            listeners.append(self)

    monkeypatch.setattr(worker, "MetricsServer", Listener)
    task = asyncio.create_task(
        worker.run_worker(
            config(
                metrics_enabled=True,
                worker_metrics_port=0,
                telemetry_enabled=True,
                telemetry_endpoint="http://127.0.0.1:1/v1/traces",
                telemetry_timeout_seconds=0.1,
                monitoring_shutdown_seconds=1,
                monitoring_heartbeat_path=str(tmp_path / "heartbeat"),
            )
        )
    )
    try:
        await asyncio.wait_for(checker.started.wait(), 3)
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(f"http://127.0.0.1:{listeners[0].port}/metrics")
            assert response.status_code == 200
            assert "infrapulse_checks_in_flight 1.0" in response.text
        assert callbacks and callable(callbacks[0])
        callbacks[0]()
        checker.release.set()
        await asyncio.wait_for(task, 5)
        assert store.results
        engine.dispose.assert_called_once()
        assert not (tmp_path / "heartbeat").exists()
        assert listeners[0].thread is not None and not listeners[0].thread.is_alive()
        assert not any(t.name == "infrapulse-traces" for t in threading.enumerate())
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_api_listener_start_failure_cleans_resources() -> None:
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        application = create_app(
            config(
                metrics_enabled=True,
                api_metrics_port=occupied.getsockname()[1],
                telemetry_enabled=True,
                telemetry_endpoint="http://127.0.0.1:1/v1/traces",
                telemetry_timeout_seconds=0.1,
            )
        )
        with pytest.raises(OSError):
            async with application.router.lifespan_context(application):
                pytest.fail("Listener unexpectedly started on occupied port")
    assert not any(t.name == "infrapulse-traces" for t in threading.enumerate())


async def test_flush_deadline_drops_backlog_and_joins() -> None:
    class DelayedExporter(SpanExporter):
        def export(self, spans: object) -> SpanExportResult:
            time.sleep(0.15)
            return SpanExportResult.SUCCESS

    processor = BoundedSpanProcessor(DelayedExporter())
    telemetry = Telemetry(config(), "worker", InMemorySpanExporter())
    assert telemetry.provider is not None
    telemetry.provider.add_span_processor(processor)
    for _ in range(300):
        with operation(telemetry.tracer, "check"):
            pass
    assert len(processor.queue) <= 256
    assert not processor.force_flush(1)
    await telemetry.close()
    assert not processor.thread.is_alive() and not processor.queue


async def test_listener_shutdown_waits_for_incomplete_scrape() -> None:
    listener = MetricsServer(Metrics(), "127.0.0.1", 0)
    await listener.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", listener.port)
    try:
        writer.write(b"GET /metrics HTTP/1.1\r\nHost: localhost\r\n")
        await writer.drain()
        await asyncio.sleep(0.05)
        await asyncio.wait_for(listener.close(), 2)
        assert await asyncio.wait_for(reader.read(), 1) == b""
        assert not any(t.name == "infrapulse-metrics" for t in threading.enumerate())
    finally:
        writer.close()
        await writer.wait_closed()
        await listener.close()


async def test_api_500_counted_once_without_exception_details() -> None:
    application = create_app(config())

    @application.get("/failure")
    async def failure() -> None:
        raise RuntimeError("sensitive-token https://password@host/")

    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            assert (await client.get("/failure?token=secret")).status_code == 500
        labels = {"method": "GET", "route": "/failure", "status_code": "500"}
        assert value(application.state.metrics, "http_server_requests_total", labels) == 1
        assert (
            value(application.state.metrics, "http_server_request_duration_seconds_count", labels)
            == 1
        )


async def test_readiness_excluded_from_database_spans() -> None:
    from unittest.mock import MagicMock, Mock, patch

    from sqlalchemy.orm import Session
    from starlette.requests import Request

    from app.db.session import get_session

    application = create_app(config())
    exporter = InMemorySpanExporter()
    async with application.router.lifespan_context(application):
        original = application.state.telemetry
        telemetry = Telemetry(config(), "api", exporter)
        application.state.telemetry = telemetry
        try:
            # Use a fake engine session but exercise the real dependency generator.
            application.state.engine = Mock()
            session = MagicMock(spec=Session)
            session.__enter__.return_value = session
            session.__exit__.return_value = False

            with patch("app.db.session.Session", return_value=session):
                request = Request({"type": "http", "path": "/ready", "app": application})
                dependency = get_session(request)
                next(dependency)
                with pytest.raises(StopIteration):
                    next(dependency)
            assert telemetry.provider is not None and telemetry.provider.force_flush(2000)
            assert not exporter.get_finished_spans()
        finally:
            await telemetry.close()
            application.state.telemetry = original


async def test_deleted_service_outcome_is_discarded() -> None:
    class Store(MemoryStore):
        def persist(self, outcome: CheckOutcome) -> bool:
            return False

    metrics, checker = Metrics(), BlockingChecker()
    checker.release.set()
    scheduler = MonitoringScheduler(config(), Store(1), checker, metrics)
    await scheduler.tick()
    await asyncio.gather(*scheduler.tasks.values())
    assert value(metrics, "infrapulse_checks_total", {"status": "UP"}) == 1
    assert value(metrics, "infrapulse_check_results_discarded_total") == 1
    assert value(metrics, "infrapulse_check_results_persisted_total", {"status": "UP"}) == 0
    assert value(metrics, "infrapulse_last_up_check_timestamp_seconds") == 0


async def test_file_heartbeat_failure_is_one_unhealthy_cycle(tmp_path: Path) -> None:
    from app.worker import WorkerScheduler

    metrics = Metrics()
    scheduler = WorkerScheduler(
        config(monitoring_heartbeat_path=str(tmp_path / "missing" / "file")),
        MemoryStore(0),
        BlockingChecker(),
        metrics,
    )
    with pytest.raises(OSError):
        await scheduler.tick()
    assert value(metrics, "infrapulse_scheduler_errors_total", {"stage": "heartbeat"}) == 1
    assert value(metrics, "infrapulse_scheduler_errors_total", {"stage": "selection"}) == 0
    assert value(metrics, "infrapulse_scheduler_cycles_total", {"outcome": "error"}) == 1
    assert value(metrics, "infrapulse_scheduler_last_healthy_cycle_timestamp_seconds") == 0
