import asyncio
import logging
import time
from typing import Protocol
from uuid import UUID

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db import check_repository
from app.monitoring.checker import CheckOutcome, CheckTarget

logger = logging.getLogger(__name__)


class Checker(Protocol):
    async def check(self, target: CheckTarget) -> CheckOutcome: ...


class MonitoringStore(Protocol):
    def due(self, limit: int, excluded: set[UUID]) -> list[CheckTarget]: ...
    def target(self, identifier: UUID) -> CheckTarget | None: ...
    def persist(self, outcome: CheckOutcome) -> bool: ...


class SQLMonitoringStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

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
    def __init__(self, settings: Settings, store: MonitoringStore, checker: Checker) -> None:
        self.settings = settings
        self.store = store
        self.checker = checker
        self.tasks: dict[UUID, asyncio.Task[None]] = {}
        self.next_due: dict[UUID, float] = {}
        self.stop: asyncio.Event | None = None

    async def _execute(self, identifier: UUID) -> None:
        try:
            target = await asyncio.to_thread(self.store.target, identifier)
            if target is None:
                return
            self.next_due[identifier] = time.monotonic() + target.check_interval_seconds
            outcome = await self.checker.check(target)
            persisted = await asyncio.to_thread(self.store.persist, outcome)
            logger.info(
                "check_persisted" if persisted else "check_service_deleted",
                extra={"service_id": str(identifier)},
            )
        except asyncio.CancelledError:
            logger.info("check_cancelled", extra={"service_id": str(identifier)})
            raise
        except Exception:
            # Never log exception strings: they may contain URLs or DB parameters.
            logger.error("check_execution_failed", extra={"service_id": str(identifier)})

    async def tick(self) -> None:
        self.tasks = {key: task for key, task in self.tasks.items() if not task.done()}
        now = time.monotonic()
        self.next_due = {key: due for key, due in self.next_due.items() if due > now}
        capacity = self.settings.monitoring_concurrency - len(self.tasks)
        if capacity <= 0:
            return
        targets = await asyncio.to_thread(
            self.store.due, capacity, set(self.tasks) | set(self.next_due)
        )
        if self.stop is not None and self.stop.is_set():
            return
        for target in targets[:capacity]:
            if target.id in self.tasks:
                continue
            self.next_due[target.id] = now + target.check_interval_seconds
            self.tasks[target.id] = asyncio.create_task(self._execute(target.id))

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
