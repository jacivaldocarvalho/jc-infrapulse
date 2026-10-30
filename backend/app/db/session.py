from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings


def create_database_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_timeout=settings.db_connect_timeout,
        hide_parameters=True,
        connect_args={
            "connect_timeout": settings.db_connect_timeout,
            "options": f"-c statement_timeout={settings.db_statement_timeout_ms}",
        },
    )


def get_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:
        yield session
