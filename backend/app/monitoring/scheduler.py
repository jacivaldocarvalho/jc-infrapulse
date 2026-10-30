import asyncio
import logging
import time
from typing import Protocol
from uuid import UUID

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db import check_repository
from app.models.check_result import CheckStatus
from app.models.service import Service
from app.monitoring.checker import CheckOutcome, CheckTarget
from app.observability.metrics import Metrics
from app.observability.tracing import Telemetry, operation

logger = logging.getLogger(__name__)


class Checker(Protocol):
    async def check(self, target: CheckTarget) -> CheckOutcome: ...


class MonitoringStore(Protocol):
    def due(self, limit: int, excluded: set[UUID]) -> list[CheckTarget]: ...
    def target(self, identifier: UUID) -> CheckTarget | None: ...
    def persist(self, outcome: CheckOutcome) -> bool: ...
    def active_count(self) -> int: ...


class SQLMonitoringStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def active_count(self) -> int:
        with Session(self.engine) as session:
            return (
                session.scalar(
                    select(func.count()).select_from(Service).where(Service.is_active.is_(True))
                )
                or 0
            )

    def due(self, limit: int, excluded: set[UUID]) -> list[CheckTarget]:
        with Session(self.engine) as session:
            return check_repository.list_due_targets(session, limit, excluded)

    def target(self, identifier: UUID) -> CheckTarget | None:
        with Session(self.engine) as session:
            return check_repository.active_target(session, identifier)

    def persist(self, outcome: CheckOutcome) -> bool:
        with Session(self.engine) as session:
            return check_repository.save_result(session, outcome)


class MonitoringScheduler:
    def __init__(
        self,
        settings: Settings,
        store: MonitoringStore,
        checker: Checker,
        metrics: Metrics | None = None,
        telemetry: Telemetry | None = None,
    ) -> None:
        self.metrics = metrics or Metrics()
        self.telemetry = telemetry or Telemetry(settings, "worker")
        self.metrics.capacity.set(settings.monitoring_concurrency)
        self.cycle_stage = "selection"
        self.settings = settings
        self.store = store
        self.checker = checker
        self.tasks: dict[UUID, asyncio.Task[None]] = {}
        self.next_due: dict[UUID, float] = {}
        self.stop: asyncio.Event | None = None

    async def _execute(self, identifier: UUID) -> None:
        stage = "target"
        self.metrics.in_flight.inc()
        try:
            with operation(self.telemetry.tracer, "worker.execute"):
                with operation(self.telemetry.tracer, "db.target"):
                    target = await asyncio.to_thread(self.store.target, identifier)
                if target is None:
                    return
                self.next_due[identifier] = time.monotonic() + target.check_interval_seconds
                stage = "checker"
                with operation(self.telemetry.tracer, "monitoring.check") as span:
                    outcome = await self.checker.check(target)
                    span.set_attribute("check.status", outcome.status.value)
                self.metrics.checks.labels(outcome.status.value).inc()
                self.metrics.check_duration.labels(outcome.status.value).observe(
                    outcome.response_time_ms / 1000
                )
                stage = "persist"
                with operation(self.telemetry.tracer, "db.persist"):
                    persisted = await asyncio.to_thread(self.store.persist, outcome)
                if persisted:
                    self.metrics.persisted.labels(outcome.status.value).inc()
                    if outcome.status == CheckStatus.UP:
                        self.metrics.last_up.set(time.time())
                else:
                    self.metrics.discarded.inc()
                logger.info(
                    "check_persisted" if persisted else "check_service_deleted",
                    extra={"service_id": str(identifier)},
                )
        except asyncio.CancelledError:
            self.metrics.cancelled.inc()
            logger.info("check_cancelled", extra={"service_id": str(identifier)})
            raise
        except Exception:
            self.metrics.errors.labels(stage).inc()
            logger.error("check_execution_failed", extra={"service_id": str(identifier)})
        finally:
            self.metrics.in_flight.dec()

    async def tick(self) -> None:
        started = time.monotonic()
        outcome = "success"
        self.cycle_stage = "selection"
        try:
            with operation(self.telemetry.tracer, "scheduler.cycle"):
                outcome = await self._tick()
            self.metrics.last_cycle.set(time.time())
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception:
            outcome = "error"
            self.metrics.errors.labels(self.cycle_stage).inc()
            raise
        finally:
            self.metrics.cycles.labels(outcome).inc()
            self.metrics.cycle_duration.observe(time.monotonic() - started)

    async def _tick(self) -> str:
        self.tasks = {key: task for key, task in self.tasks.items() if not task.done()}
        now = time.monotonic()
        self.next_due = {key: due for key, due in self.next_due.items() if due > now}
        capacity = self.settings.monitoring_concurrency - len(self.tasks)
        with operation(self.telemetry.tracer, "db.selection"):
            self.metrics.active.set(await asyncio.to_thread(self.store.active_count))
        if capacity <= 0:
            return "saturated"
        with operation(self.telemetry.tracer, "db.due"):
            targets = await asyncio.to_thread(
                self.store.due, capacity, set(self.tasks) | set(self.next_due)
            )
        if self.stop is not None and self.stop.is_set():
            return "success"
        for target in targets[:capacity]:
            if target.id in self.tasks:
                continue
            self.next_due[target.id] = now + target.check_interval_seconds
            self.tasks[target.id] = asyncio.create_task(self._execute(target.id))

        return "success"

    async def run(self, stop: asyncio.Event) -> None:
        self.stop = stop
        logger.info("scheduler_started")
        try:
            while not stop.is_set():
                try:
                    await self.tick()
                except Exception:
                    logger.error("scheduler_iteration_failed")
                try:
                    await asyncio.wait_for(
                        stop.wait(), timeout=self.settings.monitoring_poll_seconds
                    )
                except TimeoutError:
                    pass
        finally:
            if self.tasks:
                _, pending = await asyncio.wait(
                    self.tasks.values(), timeout=self.settings.monitoring_shutdown_seconds
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*self.tasks.values(), return_exceptions=True)
            self.tasks.clear()
            logger.info("scheduler_stopped")
