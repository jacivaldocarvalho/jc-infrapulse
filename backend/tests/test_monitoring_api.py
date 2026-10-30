from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.db.check_repository import list_due_targets, save_result
from app.models.check_result import CheckResult, CheckStatus
from app.monitoring.checker import CheckOutcome

pytestmark = pytest.mark.anyio
BASE = "/api/v1/services"


async def register(client: AsyncClient) -> UUID:
    response = await client.post(
        BASE, json={"name": "API", "url": "https://api.example.com/health"}
    )
    assert response.status_code == 201
    return UUID(response.json()["id"])


async def test_history_metrics_status_and_cascade(
    registry_client: AsyncClient, registry_engine: Engine
) -> None:
    identifier = await register(registry_client)
    now = datetime.now(UTC) - timedelta(seconds=1)
    with Session(registry_engine) as session:
        for index in range(20):
            status = CheckStatus.UP if index < 15 else CheckStatus.TIMEOUT
            save_result(
                session,
                CheckOutcome(
                    identifier,
                    now + timedelta(microseconds=index),
                    status,
                    200 if index < 15 else None,
                    float(index + 1),
                    None,
                ),
            )
    path = f"{BASE}/{identifier}"
    history = (await registry_client.get(f"{path}/checks", params={"limit": 3, "offset": 2})).json()
    assert history["total"] == 20 and len(history["items"]) == 3
    assert [item["response_time_ms"] for item in history["items"]] == [18, 17, 16]
    assert all(item["checked_at"].endswith("Z") for item in history["items"])
    metrics = (await registry_client.get(f"{path}/metrics")).json()
    assert metrics["total_checks"] == 20
    assert metrics["up_checks"] == 15 and metrics["down_checks"] == 5
    assert metrics["uptime_percentage"] == 75
    assert metrics["average_response_time_ms"] == 10.5
    assert metrics["p95_response_time_ms"] == pytest.approx(19.05)
    assert metrics["last_status"] == "TIMEOUT"
    status = (await registry_client.get(f"{path}/status")).json()
    assert status["last_status"] == "TIMEOUT"
    assert status["last_checked_at"] == metrics["last_checked_at"]
    assert (await registry_client.delete(path)).status_code == 204
    with Session(registry_engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(CheckResult)
                .where(CheckResult.service_id == identifier)
            )
            == 0
        )


async def test_no_data_and_missing_service(registry_client: AsyncClient) -> None:
    identifier = await register(registry_client)
    path = f"{BASE}/{identifier}"
    metrics = (await registry_client.get(f"{path}/metrics")).json()
    for key in (
        "uptime_percentage",
        "average_response_time_ms",
        "p95_response_time_ms",
        "last_status",
        "last_checked_at",
    ):
        assert metrics[key] is None
    assert metrics["total_checks"] == metrics["up_checks"] == metrics["down_checks"] == 0
    assert (await registry_client.get(f"{path}/checks")).json()["items"] == []
    assert (await registry_client.get(f"{path}/status")).json()["last_status"] is None
    for suffix in ("checks", "metrics", "status"):
        assert (await registry_client.get(f"{BASE}/{uuid4()}/{suffix}")).status_code == 404
        assert (await registry_client.get(f"{BASE}/invalid/{suffix}")).status_code == 422


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=101",
        "offset=-1",
        "offset=10001",
        "start=invalid",
        "start=2026-01-01T00:00:00",
        "start=2026-01-02T00:00:00Z&end=2026-01-01T00:00:00Z",
        "start=2026-01-01T00:00:00Z&end=2026-12-01T00:00:00Z",
    ],
)
async def test_query_limits(registry_client: AsyncClient, query: str) -> None:
    identifier = await register(registry_client)
    assert (await registry_client.get(f"{BASE}/{identifier}/checks?{query}")).status_code == 422


async def test_period_boundaries(registry_client: AsyncClient, registry_engine: Engine) -> None:
    identifier = await register(registry_client)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    with Session(registry_engine) as session:
        for seconds in (-1, 0, 1):
            save_result(
                session,
                CheckOutcome(
                    identifier, start + timedelta(seconds=seconds), CheckStatus.UP, 200, 10, None
                ),
            )
    params = {"start": start.isoformat(), "end": (start + timedelta(seconds=1)).isoformat()}
    for suffix in ("metrics", "checks"):
        response = await registry_client.get(f"{BASE}/{identifier}/{suffix}", params=params)
        assert response.status_code == 200
        assert response.json()["total_checks" if suffix == "metrics" else "total"] == 1


async def test_due_query_respects_activity_interval_and_restart(
    registry_client: AsyncClient, registry_engine: Engine
) -> None:
    identifier = await register(registry_client)
    with Session(registry_engine) as session:
        assert identifier in [item.id for item in list_due_targets(session, 10, set())]
    with Session(registry_engine) as session:
        save_result(
            session, CheckOutcome(identifier, datetime.now(UTC), CheckStatus.UP, 200, 10, None)
        )
    with Session(registry_engine) as session:
        assert identifier not in [item.id for item in list_due_targets(session, 10, set())]
    with Session(registry_engine) as session, session.begin():
        result = session.scalar(select(CheckResult).where(CheckResult.service_id == identifier))
        assert result is not None
        result.checked_at = datetime.now(UTC) - timedelta(seconds=120)
    with Session(registry_engine) as session:
        assert identifier in [item.id for item in list_due_targets(session, 10, set())]
        assert identifier not in [item.id for item in list_due_targets(session, 10, {identifier})]
    await registry_client.patch(f"{BASE}/{identifier}", json={"is_active": False})
    with Session(registry_engine) as session:
        assert identifier not in [item.id for item in list_due_targets(session, 10, set())]


async def test_deleted_service_result_discarded(registry_engine: Engine) -> None:
    with Session(registry_engine) as session:
        assert (
            save_result(
                session, CheckOutcome(uuid4(), datetime.now(UTC), CheckStatus.UP, 200, 10, None)
            )
            is False
        )
