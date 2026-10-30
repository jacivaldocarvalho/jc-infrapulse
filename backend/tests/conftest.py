from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def application() -> FastAPI:
    return create_app(Settings(_env_file=None, postgres_password=SecretStr("test-only")))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client(application: FastAPI) -> AsyncIterator[AsyncClient]:
    async with application.router.lifespan_context(application):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as test_client:
            yield test_client
