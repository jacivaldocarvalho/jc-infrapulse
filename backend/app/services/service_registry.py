import logging
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.url_policy import validate_destination
from app.db import service_repository
from app.models.service import Service
from app.schemas.service import ServiceCreate, ServiceUpdate

logger = logging.getLogger(__name__)


class ServiceNotFoundError(Exception):
    pass


class InvalidServiceError(Exception):
    pass


def find_service(session: Session, service_id: UUID, *, for_update: bool = False) -> Service:
    service = service_repository.get_service(session, service_id, for_update=for_update)
    if service is None:
        raise ServiceNotFoundError
    return service


def create_service(session: Session, data: ServiceCreate, settings: Settings) -> Service:
    try:
        validate_destination(data.url, settings)
    except ValueError as exc:
        raise InvalidServiceError(str(exc)) from exc
    with session.begin():
        service = Service(**data.model_dump(mode="json"))
        session.add(service)
        session.flush()
        session.refresh(service)
    logger.info("service_created id=%s", service.id)
    return service


def update_service(
    session: Session, service_id: UUID, patch: ServiceUpdate, settings: Settings
) -> Service:
    with session.begin():
        service = find_service(session, service_id, for_update=True)
        current = {name: getattr(service, name) for name in ServiceCreate.model_fields}
        try:
            data = ServiceCreate.model_validate(current | patch.model_dump(exclude_unset=True))
            if "url" in patch.model_fields_set:
                validate_destination(data.url, settings)
        except (ValidationError, ValueError) as exc:
            raise InvalidServiceError("Invalid service update or destination") from exc
        changes = data.model_dump(mode="json")
        if any(getattr(service, name) != value for name, value in changes.items()):
            for name, value in changes.items():
                setattr(service, name, value)
            service.updated_at = datetime.now(UTC)
            session.flush()
            session.refresh(service)
    logger.info("service_updated id=%s", service.id)
    return service


def delete_service(session: Session, service_id: UUID) -> None:
    with session.begin():
        service = find_service(session, service_id, for_update=True)
        session.delete(service)
    logger.info("service_deleted id=%s", service_id)
