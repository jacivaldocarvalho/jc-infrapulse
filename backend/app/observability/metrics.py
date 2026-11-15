"""Bounded-cardinality registries and explicitly owned HTTP listeners."""

import asyncio
import socket
import time
from socketserver import ThreadingMixIn
from threading import Thread
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, make_wsgi_app

BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.started = Gauge(
            "infrapulse_process_start_timestamp_seconds",
            "Process telemetry start UTC epoch",
            registry=self.registry,
        )
        self.started.set(time.time())
        self.heartbeat = Gauge(
            "infrapulse_event_loop_last_tick_timestamp_seconds",
            "Last event loop tick UTC epoch",
            registry=self.registry,
        )
        self.requests = Counter(
            "http_server_requests_total",
            "Completed API requests",
            ("method", "route", "status_code"),
            registry=self.registry,
        )
        self.request_duration = Histogram(
            "http_server_request_duration_seconds",
            "API request duration seconds",
            ("method", "route", "status_code"),
            buckets=BUCKETS,
            registry=self.registry,
        )
        self.checks = Counter(
            "infrapulse_checks_total",
            "Completed checker outcomes before persistence",
            ("status",),
            registry=self.registry,
        )
        self.check_duration = Histogram(
            "infrapulse_check_duration_seconds",
            "Checker execution duration seconds",
            ("status",),
            buckets=BUCKETS,
            registry=self.registry,
        )
        self.in_flight = Gauge(
            "infrapulse_checks_in_flight",
            "Tasks occupying worker capacity including database work",
            registry=self.registry,
        )
        self.capacity = Gauge(
            "infrapulse_worker_capacity",
            "Configured concurrent task capacity",
            registry=self.registry,
        )
        self.cycles = Counter(
            "infrapulse_scheduler_cycles_total",
            "Scheduler cycles",
            ("outcome",),
            registry=self.registry,
        )
        self.errors = Counter(
            "infrapulse_scheduler_errors_total",
            "Internal failures, never monitored service DOWN",
            ("stage",),
            registry=self.registry,
        )
        self.cycle_duration = Histogram(
            "infrapulse_scheduler_cycle_duration_seconds",
            "Scheduler cycle seconds",
            buckets=BUCKETS,
            registry=self.registry,
        )
        self.last_cycle = Gauge(
            "infrapulse_scheduler_last_healthy_cycle_timestamp_seconds",
            "Last successful scheduling cycle UTC epoch",
            registry=self.registry,
        )
        self.last_up = Gauge(
            "infrapulse_last_up_check_timestamp_seconds",
            "Last UP result committed by this worker UTC epoch",
            registry=self.registry,
        )
        self.persisted = Counter(
            "infrapulse_check_results_persisted_total",
            "Successfully committed check results",
            ("status",),
            registry=self.registry,
        )
        self.discarded = Counter(
            "infrapulse_check_results_discarded_total",
            "Results discarded because service was deleted",
            registry=self.registry,
        )
        self.cancelled = Counter(
            "infrapulse_check_tasks_cancelled_total",
            "Cancelled worker tasks",
            registry=self.registry,
        )
        self.active = Gauge(
            "infrapulse_active_services",
            "Last sampled active service count; -1 before first sample",
            registry=self.registry,
        )
        self.active.set(-1)
        for status in (
            "UP",
            "DOWN",
            "TIMEOUT",
            "DNS_ERROR",
            "TLS_ERROR",
            "CONNECTION_ERROR",
            "HTTP_ERROR",
        ):
            self.checks.labels(status)
            self.check_duration.labels(status)
            self.persisted.labels(status)
        for stage in ("selection", "target", "checker", "persist", "heartbeat"):
            self.errors.labels(stage)
        for outcome in ("success", "saturated", "error", "cancelled"):
            self.cycles.labels(outcome)

    async def pulse(self) -> None:
        while True:
            self.heartbeat.set(time.time())
            await asyncio.sleep(1)


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        # Never log raw scrape paths, query strings or client addresses.
        pass


class OwnedHTTPServer(ThreadingMixIn, WSGIServer):
    daemon_threads = False
    block_on_close = True

    def process_request(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: tuple[str, int]
    ) -> None:
        assert isinstance(request, socket.socket)
        request.settimeout(1)
        super().process_request(request, client_address)


class MetricsServer:
    def __init__(self, metrics: Metrics, host: str, port: int) -> None:
        self.metrics, self.host, self.port = metrics, host, port
        self.server: WSGIServer | None = None
        self.thread: Thread | None = None

    def _start(self) -> None:
        self.server = make_server(
            self.host,
            self.port,
            make_wsgi_app(self.metrics.registry),
            server_class=OwnedHTTPServer,
            handler_class=QuietHandler,
        )
        self.port = self.server.server_port
        self.thread = Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.1},
            name="infrapulse-metrics",
        )
        self.thread.start()

    async def start(self) -> None:
        await asyncio.to_thread(self._start)

    async def close(self) -> None:
        if self.server is not None:
            await asyncio.to_thread(self.server.shutdown)
            # Wait for active handlers too; socket reads have a one-second timeout.
            await asyncio.to_thread(self.server.server_close)
        if self.thread is not None:
            await asyncio.to_thread(self.thread.join, 2)
