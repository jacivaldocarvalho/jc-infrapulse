from uuid import UUID

from sqlalchemy.orm import Session

from app.db import check_repository
from app.schemas.monitoring import CheckWindow, ServiceMetrics, ServiceStatus
from app.services.service_registry import find_service


def service_status(session: Session, service_id: UUID) -> ServiceStatus:
    find_service(session, service_id)
    latest = check_repository.latest_result(session, service_id)
    return ServiceStatus(
        service_id=service_id,
        last_status=latest.status if latest else None,
        last_checked_at=latest.checked_at if latest else None,
    )


def service_metrics(session: Session, service_id: UUID, window: CheckWindow) -> ServiceMetrics:
    find_service(session, service_id)
    total, up, average, p95 = check_repository.aggregate_results(session, service_id, window)
    latest = check_repository.latest_result(session, service_id, window)
    return ServiceMetrics(
        service_id=service_id,
        start=window.start,
        end=window.end,
        total_checks=total,
        up_checks=up,
        down_checks=total - up,
        uptime_percentage=up / total * 100 if total else None,
        average_response_time_ms=average,
        p95_response_time_ms=p95,
        last_status=latest.status if latest else None,
        last_checked_at=latest.checked_at if latest else None,
    )
