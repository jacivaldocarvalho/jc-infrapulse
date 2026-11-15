import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.models.check_result import CheckStatus
from app.monitoring.checker import CheckOutcome, CheckTarget
from app.monitoring.scheduler import MonitoringScheduler
from app.worker import healthy, write_heartbeat

pytestmark = pytest.mark.anyio


class MemoryStore:
    def __init__(self, count: int = 5) -> None:
        self.targets = [
            CheckTarget(uuid4(), "https://api.example.com", 2, 10) for _ in range(count)
        ]
        self.results: list[CheckOutcome] = []
        self.active = True

    def active_count(self) -> int:
        return len(self.targets) if self.active else 0

    def due(self, limit: int, excluded: set[UUID]) -> list[CheckTarget]:
        return [target for target in self.targets if target.id not in excluded][:limit]

    def target(self, identifier: UUID) -> CheckTarget | None:
        return next((t for t in self.targets if t.id == identifier), None) if self.active else None

    def persist(self, outcome: CheckOutcome) -> bool:
        self.results.append(outcome)
        return True


class BlockingChecker:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.running: set[UUID] = set()
        self.calls: list[UUID] = []
        self.maximum = 0
        self.cancelled = 0

    async def check(self, target: CheckTarget) -> CheckOutcome:
        assert target.id not in self.running
        self.running.add(target.id)
        self.calls.append(target.id)
        self.maximum = max(self.maximum, len(self.running))
        self.started.set()
        try:
            await self.release.wait()
            return CheckOutcome(target.id, datetime.now(UTC), CheckStatus.UP, 200, 1, None)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.running.remove(target.id)


def settings() -> Settings:
    return Settings(
        _env_file=None,
        postgres_password=SecretStr("test-only"),
        monitoring_concurrency=2,
        monitoring_shutdown_seconds=1,
    )


async def test_concurrency_backpressure_no_overlap_and_intervals() -> None:
    store, checker = MemoryStore(), BlockingChecker()
    scheduler = MonitoringScheduler(settings(), store, checker)
    await scheduler.tick()
    await asyncio.wait_for(checker.started.wait(), 2)
    async with asyncio.timeout(2):
        while len(checker.running) < 2:
            await asyncio.sleep(0)
    await scheduler.tick()
    assert len(scheduler.tasks) == 2
    checker.release.set()
    await asyncio.gather(*scheduler.tasks.values())
    first_calls = set(checker.calls)
    await scheduler.tick()
    await asyncio.gather(*scheduler.tasks.values())
    assert len(checker.calls) == 4 and len(set(checker.calls)) == 4
    assert checker.maximum == 2
    await scheduler.tick()
    await asyncio.gather(*scheduler.tasks.values())
    await scheduler.tick()
    assert len(checker.calls) == 5
    scheduler.next_due = {key: time.monotonic() - 1 for key in scheduler.next_due}
    await scheduler.tick()
    await asyncio.gather(*scheduler.tasks.values())
    assert len(checker.calls) == 7
    assert first_calls.intersection(checker.calls[5:])


async def test_inactive_service_not_executed() -> None:
    store, checker = MemoryStore(1), BlockingChecker()
    store.active = False
    scheduler = MonitoringScheduler(settings(), store, checker)
    await scheduler.tick()
    await asyncio.gather(*scheduler.tasks.values())
    assert checker.calls == [] and store.results == []


async def test_graceful_shutdown_finishes_and_persists() -> None:
    store, checker = MemoryStore(1), BlockingChecker()
    scheduler = MonitoringScheduler(settings(), store, checker)
    stop = asyncio.Event()
    task = asyncio.create_task(scheduler.run(stop))
    await asyncio.wait_for(checker.started.wait(), 2)
    stop.set()
    checker.release.set()
    await asyncio.wait_for(task, 3)
    assert len(store.results) == 1 and not scheduler.tasks


async def test_shutdown_cancels_after_grace_period() -> None:
    checker = BlockingChecker()
    scheduler = MonitoringScheduler(settings(), MemoryStore(1), checker)
    stop = asyncio.Event()
    task = asyncio.create_task(scheduler.run(stop))
    await asyncio.wait_for(checker.started.wait(), 2)
    stop.set()
    await asyncio.wait_for(task, 3)
    assert checker.cancelled == 1 and not checker.running


async def test_scheduler_recovers_from_store_failure(caplog: pytest.LogCaptureFixture) -> None:
    class FailingStore(MemoryStore):
        def due(self, limit: int, excluded: set[UUID]) -> list[CheckTarget]:
            raise RuntimeError("secret")

    scheduler = MonitoringScheduler(settings(), FailingStore(), BlockingChecker())
    stop = asyncio.Event()
    task = asyncio.create_task(scheduler.run(stop))
    for _ in range(100):
        if "scheduler_iteration_failed" in caplog.text:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, 2)
    assert "scheduler_iteration_failed" in caplog.text and "secret" not in caplog.text


async def test_heartbeat(tmp_path: Path) -> None:
    config = settings()
    config.monitoring_heartbeat_path = str(tmp_path / "heartbeat")
    assert not healthy(config)
    write_heartbeat(Path(config.monitoring_heartbeat_path))
    assert healthy(config)
    Path(config.monitoring_heartbeat_path).write_text('{"pid": 1, "timestamp": 0}')
    assert not healthy(config)
