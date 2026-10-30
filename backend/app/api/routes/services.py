from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.service_repository import list_services
from app.db.session import get_session
from app.schemas.service import ServiceCreate, ServicePage, ServiceResponse, ServiceUpdate
from app.services import service_registry

router = APIRouter(prefix="/api/v1/services", tags=["services"])
DatabaseSession = Annotated[Session, Depends(get_session)]


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


@router.post("", response_model=ServiceResponse, status_code=201)
def create_service(
    data: ServiceCreate,
    session: DatabaseSession,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ServiceResponse:
    return ServiceResponse.model_validate(service_registry.create_service(session, data, settings))


@router.get("", response_model=ServicePage)
def get_services(
    session: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    is_active: bool | None = None,
) -> ServicePage:
    items, total = list_services(session, limit, offset, is_active)
    return ServicePage(
        items=[ServiceResponse.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{service_id}", response_model=ServiceResponse)
def get_service(service_id: UUID, session: DatabaseSession) -> ServiceResponse:
    return ServiceResponse.model_validate(service_registry.find_service(session, service_id))


@router.patch("/{service_id}", response_model=ServiceResponse)
def update_service(
    service_id: UUID,
    data: ServiceUpdate,
    session: DatabaseSession,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ServiceResponse:
    return ServiceResponse.model_validate(
        service_registry.update_service(session, service_id, data, settings)
    )


@router.delete("/{service_id}", status_code=204)
def delete_service(service_id: UUID, session: DatabaseSession) -> Response:
    service_registry.delete_service(session, service_id)
    return Response(status_code=204)
