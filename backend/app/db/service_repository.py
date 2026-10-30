from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.service import Service


def get_service(session: Session, service_id: UUID, *, for_update: bool = False) -> Service | None:
    query = select(Service).where(Service.id == service_id)
    if for_update:
        query = query.with_for_update()
    return session.scalar(query)


def list_services(
    session: Session, limit: int, offset: int, is_active: bool | None
) -> tuple[list[Service], int]:
    query = select(Service)
    count = select(func.count()).select_from(Service)
    if is_active is not None:
        query = query.where(Service.is_active == is_active)
        count = count.where(Service.is_active == is_active)
    items = session.scalars(
        query.order_by(Service.created_at, Service.id).limit(limit).offset(offset)
    ).all()
    return list(items), session.scalar(count) or 0
