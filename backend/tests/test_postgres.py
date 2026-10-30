from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.models.service import Service

pytestmark = pytest.mark.integration


def test_migration_and_postgres_persistence(postgres_engine: Engine) -> None:
    assert "services" in inspect(postgres_engine).get_table_names()
    with postgres_engine.connect() as connection:
        version = connection.scalar(text("SHOW server_version_num"))
        assert isinstance(version, str)
        assert int(version) // 10000 == 17
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0001"
    identifier = uuid4()
    with Session(postgres_engine) as session, session.begin():
        session.add(Service(id=identifier, name="Persisted", url="https://example.com"))
    with Session(postgres_engine) as session:
        record = session.get(Service, identifier)
        assert record is not None
        assert record.name == "Persisted"
        assert record.check_interval_seconds == 60
        assert record.timeout_seconds == 5
        assert record.is_active is True
        assert record.created_at.utcoffset() == timedelta(0)
        assert record.updated_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    "fields",
    [
        {"check_interval_seconds": 9},
        {"timeout_seconds": 0},
        {"timeout_seconds": 60},
        {"name": " "},
        {"name": "n" * 121},
    ],
)
def test_database_constraints(postgres_engine: Engine, fields: dict[str, object]) -> None:
    with (
        pytest.raises((IntegrityError, DataError)),
        Session(postgres_engine) as session,
        session.begin(),
    ):
        session.add(Service(**({"name": "API", "url": "https://example.com"} | fields)))
