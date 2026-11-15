from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, ValidationError
from sqlalchemy.orm import Session

from app.db.check_repository import list_results
from app.db.session import get_session
from app.schemas.monitoring import (
    CheckPage,
    CheckResponse,
    CheckWindow,
    ServiceMetrics,
    ServiceStatus,
)
from app.services.metrics import service_metrics, service_status
from app.services.service_registry import find_service

router = APIRouter(prefix="/api/v1/services", tags=["monitoring"])
DatabaseSession = Annotated[Session, Depends(get_session)]


def get_window(start: AwareDatetime | None = None, end: AwareDatetime | None = None) -> CheckWindow:
    values = {}
    if start is not None:
        values["start"] = start
    if end is not None:
        values["end"] = end
    try:
        return CheckWindow(**values)
    except ValidationError as exc:
        raise HTTPException(422, "Period must be positive and no greater than 90 days") from exc


@router.get("/{service_id}/checks", response_model=CheckPage)
def get_checks(
    service_id: UUID,
    session: DatabaseSession,
    window: Annotated[CheckWindow, Depends(get_window)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> CheckPage:
    find_service(session, service_id)
    items, total = list_results(session, service_id, window, limit, offset)
    return CheckPage(
        items=[CheckResponse.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{service_id}/metrics", response_model=ServiceMetrics)
def get_metrics(
    service_id: UUID, session: DatabaseSession, window: Annotated[CheckWindow, Depends(get_window)]
) -> ServiceMetrics:
    return service_metrics(session, service_id, window)


@router.get("/{service_id}/status", response_model=ServiceStatus)
def get_status(service_id: UUID, session: DatabaseSession) -> ServiceStatus:
    return service_status(session, service_id)
