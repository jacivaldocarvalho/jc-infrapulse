from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.models.service import Service

pytestmark = pytest.mark.anyio
BASE = "/api/v1/services"
VALID = {"name": "Public API", "url": "https://api.example.com/health"}


async def test_create_and_persist(registry_client: AsyncClient, registry_engine: Engine) -> None:
    response = await registry_client.post(BASE, json=VALID)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == VALID["name"]
    assert data["url"] == VALID["url"]
    assert data["check_interval_seconds"] == 60
    assert data["timeout_seconds"] == 5
    assert data["is_active"] is True
    assert data["created_at"].endswith("Z")
    assert data["updated_at"] == data["created_at"]
    with Session(registry_engine) as session:
        record = session.get(Service, UUID(data["id"]))
        assert record is not None
        assert record.name == VALID["name"]


@pytest.mark.parametrize(
    "patch",
    [
        {"name": ""},
        {"name": "  "},
        {"name": "n" * 121},
        {"name": None},
        {"url": "invalid"},
        {"url": "ftp://example.com"},
        {"url": "https://user:secret@example.com"},
        {"url": "http://localhost"},
        {"url": "http://127.0.0.1"},
        {"url": "http://10.0.0.1"},
        {"url": "http://169.254.169.254"},
        {"url": "http://[::1]"},
        {"check_interval_seconds": 9},
        {"timeout_seconds": 0},
        {"check_interval_seconds": 10, "timeout_seconds": 10},
        {"timeout_seconds": 60},
        {"check_interval_seconds": True},
        {"timeout_seconds": 1.5},
        {"timeout_seconds": 2147483648},
        {"is_active": "false"},
        {"unexpected": True},
    ],
)
async def test_invalid_create(registry_client: AsyncClient, patch: dict[str, object]) -> None:
    response = await registry_client.post(BASE, json=VALID | patch)
    assert response.status_code == 422
    assert (await registry_client.get(BASE)).json()["total"] == 0


@pytest.mark.parametrize("data", [{}, {"name": "API"}, {"url": "https://example.com"}])
async def test_missing_fields(registry_client: AsyncClient, data: dict[str, object]) -> None:
    assert (await registry_client.post(BASE, json=data)).status_code == 422


async def test_pagination_and_filter(registry_client: AsyncClient) -> None:
    records = []
    for index in range(5):
        response = await registry_client.post(
            BASE, json=VALID | {"name": f"Service {index}", "is_active": index % 2 == 0}
        )
        assert response.status_code == 201
        records.append(response.json())
    ids = [
        item["id"] for item in sorted(records, key=lambda item: (item["created_at"], item["id"]))
    ]
    page = (await registry_client.get(BASE, params={"limit": 2, "offset": 1})).json()
    assert page["total"] == 5
    assert page["limit"] == 2
    assert page["offset"] == 1
    assert [item["id"] for item in page["items"]] == ids[1:3]
    assert (await registry_client.get(BASE)).json()["limit"] == 20
    for active, total in [(True, 3), (False, 2)]:
        page = (await registry_client.get(BASE, params={"is_active": str(active).lower()})).json()
        assert page["total"] == total
        assert all(item["is_active"] is active for item in page["items"])
    assert (await registry_client.get(BASE, params={"offset": 50})).json()["items"] == []


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "is_active=invalid"])
async def test_invalid_pagination(registry_client: AsyncClient, query: str) -> None:
    assert (await registry_client.get(f"{BASE}?{query}")).status_code == 422


async def test_lookup_patch_delete(registry_client: AsyncClient) -> None:
    original = (await registry_client.post(BASE, json=VALID)).json()
    path = f"{BASE}/{original['id']}"
    assert (await registry_client.get(path)).json() == original
    for patch in [{}, {"name": original["name"]}]:
        assert (await registry_client.patch(path, json=patch)).json() == original
    response = await registry_client.patch(path, json={"name": "Updated", "is_active": False})
    assert response.status_code == 200
    changed = response.json()
    assert changed["name"] == "Updated"
    assert changed["is_active"] is False
    assert changed["url"] == original["url"]
    assert changed["created_at"] == original["created_at"]
    assert changed["updated_at"] > original["updated_at"]
    assert (await registry_client.get(path)).json() == changed
    response = await registry_client.delete(path)
    assert response.status_code == 204
    assert response.content == b""
    assert (await registry_client.get(path)).status_code == 404
    assert (await registry_client.get(BASE)).json()["total"] == 0


@pytest.mark.parametrize(
    "patch",
    [
        {"name": None},
        {"url": None},
        {"timeout_seconds": 60},
        {"check_interval_seconds": 10, "timeout_seconds": 20},
        {"url": "http://10.0.0.1"},
        {"name": " "},
    ],
)
async def test_invalid_patch_is_atomic(
    registry_client: AsyncClient, patch: dict[str, object]
) -> None:
    original = (await registry_client.post(BASE, json=VALID)).json()
    path = f"{BASE}/{original['id']}"
    assert (await registry_client.patch(path, json=patch)).status_code == 422
    assert (await registry_client.get(path)).json() == original


async def test_patch_validates_merged_state(registry_client: AsyncClient) -> None:
    original = (await registry_client.post(BASE, json=VALID | {"timeout_seconds": 30})).json()
    path = f"{BASE}/{original['id']}"
    assert (
        await registry_client.patch(path, json={"check_interval_seconds": 20})
    ).status_code == 422
    response = await registry_client.patch(
        path, json={"check_interval_seconds": 20, "timeout_seconds": 10}
    )
    assert response.status_code == 200
    assert response.json()["timeout_seconds"] == 10


async def test_missing_and_invalid_uuid(registry_client: AsyncClient) -> None:
    for identifier, expected in [(str(uuid4()), 404), ("invalid", 422)]:
        path = f"{BASE}/{identifier}"
        assert (await registry_client.get(path)).status_code == expected
        assert (await registry_client.patch(path, json={"name": "New"})).status_code == expected
        assert (await registry_client.delete(path)).status_code == expected


async def test_deterministic_ties(registry_client: AsyncClient, registry_engine: Engine) -> None:
    for index in range(3):
        await registry_client.post(BASE, json=VALID | {"name": str(index)})
    with Session(registry_engine) as session, session.begin():
        records = list(session.scalars(select(Service)))
        for record in records:
            record.created_at = records[0].created_at
        expected = sorted(str(record.id) for record in records)
    result = (await registry_client.get(BASE)).json()
    assert [item["id"] for item in result["items"]] == expected


async def test_internal_policy_and_url_update(
    registry_client: AsyncClient, application: FastAPI
) -> None:
    internal = VALID | {"url": "http://api.internal/health"}
    assert (await registry_client.post(BASE, json=internal)).status_code == 422
    application.state.settings.service_internal_hosts = ["api.internal"]
    response = await registry_client.post(BASE, json=internal)
    assert response.status_code == 201
    path = f"{BASE}/{response.json()['id']}"
    assert (
        await registry_client.patch(path, json={"url": "http://db.internal"})
    ).status_code == 422
    response = await registry_client.patch(
        path,
        json={"url": "https://example.com/new", "check_interval_seconds": 30, "timeout_seconds": 2},
    )
    assert response.status_code == 200
    assert response.json()["url"] == "https://example.com/new"
    assert response.json()["check_interval_seconds"] == 30
    assert response.json()["timeout_seconds"] == 2
