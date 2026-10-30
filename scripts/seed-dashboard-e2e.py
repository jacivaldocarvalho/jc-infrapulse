"""Deterministic history only in the disposable dashboard E2E database."""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.config import Settings
from app.db.session import create_database_engine
from app.models.check_result import CheckResult, CheckStatus
from app.models.service import Service
from sqlalchemy.orm import Session

settings = Settings(_env_file=None)
if os.environ.get("INFRAPULSE_FRONTEND_E2E") != "1" or (
    settings.postgres_host,
    settings.postgres_db,
    settings.postgres_user,
) != ("test-db", "infrapulse_test", "infrapulse_test"):
    raise RuntimeError("Refusing to seed outside the disposable frontend E2E database")

engine = create_database_engine(settings)
now = datetime.now(UTC)
with Session(engine) as session, session.begin():
    for index in range(24):
        identifier = UUID(int=index + 1)
        session.add(
            Service(
                id=identifier,
                name="Payments API"
                if index == 0
                else "Catalog API"
                if index == 1
                else f"Service {index:02d}",
                url="https://example.com/health",
                is_active=False,
                created_at=now - timedelta(days=1) + timedelta(seconds=index),
            )
        )
    session.flush()
    for index in range(25):
        session.add(
            CheckResult(
                service_id=UUID(int=1),
                checked_at=now - timedelta(minutes=26 - index),
                status=CheckStatus.UP if index < 20 else CheckStatus.TIMEOUT,
                http_status_code=200 if index < 20 else None,
                response_time_ms=float(index + 10),
                error_message=None if index < 20 else "Check deadline exceeded",
            )
        )
engine.dispose()
print(
    "Seeded 24 inactive services and 25 deterministic historical checks in disposable DB"
)
