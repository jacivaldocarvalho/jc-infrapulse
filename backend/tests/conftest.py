import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from alembic import command
from app.core.config import Settings
from app.db.base import Base
from app.db.session import create_database_engine, get_session
from app.main import create_app
from app.models.service import Service  # noqa: F401


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


@pytest.fixture(scope="session")
def postgres_engine() -> Iterator[Engine]:
    if os.environ.get("INFRAPULSE_RUN_INTEGRATION") != "1":
        pytest.skip("Run the standalone compose.test.yaml project for PostgreSQL integration")
    settings = Settings(_env_file=None)
    if (settings.postgres_host, settings.postgres_db, settings.postgres_user) != (
        "test-db",
        "infrapulse_test",
        "infrapulse_test",
    ):
        pytest.fail("Refusing integration tests outside the dedicated test-db container")
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    engine = create_database_engine(settings)
    with engine.connect() as connection:
        assert not connection.scalar(text("SELECT to_regclass('public.services')"))
    command.upgrade(config, "head")
    command.check(config)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def registry_engine(request: pytest.FixtureRequest) -> Iterator[Engine]:
    if os.environ.get("INFRAPULSE_RUN_INTEGRATION") == "1":
        engine: Engine = request.getfixturevalue("postgres_engine")
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE services"))
        yield engine
    else:
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        # SQLite is only a fast unit-test fixture. PostgreSQL uses Alembic exclusively.
        Base.metadata.create_all(engine)
        try:
            yield engine
        finally:
            engine.dispose()


@pytest.fixture
async def registry_client(
    application: FastAPI, registry_engine: Engine
) -> AsyncIterator[AsyncClient]:
    def override_session() -> Iterator[Session]:
        with Session(registry_engine, expire_on_commit=False) as session:
            yield session

    application.dependency_overrides[get_session] = override_session
    async with application.router.lifespan_context(application):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as test_client:
            yield test_client
