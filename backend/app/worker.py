"""Standalone monitoring process and its local heartbeat health check."""

import asyncio
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.session import create_database_engine
from app.monitoring.checker import HTTPChecker
from app.monitoring.scheduler import MonitoringScheduler, SQLMonitoringStore
from app.observability.metrics import Metrics, MetricsServer
from app.observability.tracing import Telemetry

logger = logging.getLogger(__name__)


def write_heartbeat(path: Path) -> None:
    path.write_text(json.dumps({"pid": os.getpid(), "timestamp": time.time()}))


class WorkerScheduler(MonitoringScheduler):
    async def _tick(self) -> str:
        outcome = await super()._tick()
        self.cycle_stage = "heartbeat"
        await asyncio.to_thread(write_heartbeat, Path(self.settings.monitoring_heartbeat_path))
        return outcome


def healthy(settings: Settings) -> bool:
    try:
        record = json.loads(Path(settings.monitoring_heartbeat_path).read_text())
        age = time.time() - float(record["timestamp"])
        os.kill(int(record["pid"]), 0)
        return 0 <= age <= max(30, settings.monitoring_poll_seconds * 3)
    except (OSError, ValueError, KeyError, TypeError):
        return False


async def run_worker(settings: Settings) -> None:
    configure_logging(settings.log_level)
    engine = create_database_engine(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    metrics, telemetry = Metrics(), Telemetry(settings, "worker")
    listener = MetricsServer(metrics, settings.metrics_host, settings.worker_metrics_port)
    pulse = asyncio.create_task(metrics.pulse())
    logger.info("worker_started")
    try:
        if settings.metrics_enabled:
            await listener.start()
        await WorkerScheduler(
            settings, SQLMonitoringStore(engine), HTTPChecker(settings), metrics, telemetry
        ).run(stop)
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)
        pulse.cancel()
        await asyncio.gather(pulse, return_exceptions=True)
        await listener.close()
        await telemetry.close()
        await asyncio.to_thread(engine.dispose)
        Path(settings.monitoring_heartbeat_path).unlink(missing_ok=True)
        logger.info("worker_stopped")


def main() -> None:
    settings = Settings()
    if sys.argv[1:] == ["healthcheck"]:
        raise SystemExit(0 if healthy(settings) else 1)
    if sys.argv[1:]:
        raise SystemExit("Usage: python -m app.worker [healthcheck]")
    asyncio.run(run_worker(settings))


if __name__ == "__main__":
    main()
