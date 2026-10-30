from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.session import get_session

pytestmark = pytest.mark.anyio


async def test_health_status(client: AsyncClient) -> None:
    assert (await client.get("/health")).status_code == 200


async def test_health_content(client: AsyncClient) -> None:
    assert (await client.get("/health")).json() == {
        "status": "healthy",
        "service": "infrapulse-api",
    }


async def test_ready_with_database(client: AsyncClient, application: FastAPI) -> None:
    session = Mock(spec=Session)
    application.dependency_overrides[get_session] = lambda: session
    response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    session.execute.assert_called_once()
    assert str(session.execute.call_args.args[0]) == "SELECT 1"


async def test_ready_without_database(client: AsyncClient, application: FastAPI) -> None:
    session = Mock(spec=Session)
    session.execute.side_effect = OperationalError(
        "SELECT 1", {}, Exception("sensitive-database-details")
    )
    application.dependency_overrides[get_session] = lambda: session
    response = await client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    assert "sensitive-database-details" not in response.text


async def test_api_info(client: AsyncClient) -> None:
    response = await client.get("/api/v1")
    assert response.status_code == 200
    assert response.json() == {"name": "infrapulse-api", "version": "0.1.0"}
