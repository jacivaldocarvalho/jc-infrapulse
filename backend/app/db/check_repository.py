import math
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import case, extract, func, literal, select
from sqlalchemy.orm import Session

from app.models.check_result import CheckResult, CheckStatus
from app.models.service import Service
from app.monitoring.checker import CheckOutcome, CheckTarget
from app.schemas.monitoring import CheckWindow


def save_result(session: Session, outcome: CheckOutcome) -> bool:
    with session.begin():
        # Serialize against DELETE; cascade handles deletion after this transaction.
        if (
            session.scalar(
                select(Service.id).where(Service.id == outcome.service_id).with_for_update()
            )
            is None
        ):
            return False
        session.add(
            CheckResult(
                service_id=outcome.service_id,
                checked_at=outcome.checked_at,
                status=outcome.status,
                http_status_code=outcome.http_status_code,
                response_time_ms=outcome.response_time_ms,
                error_message=outcome.error_message,
            )
        )
    return True


def list_due_targets(session: Session, limit: int, excluded: set[UUID]) -> list[CheckTarget]:
    latest = (
        select(CheckResult.checked_at)
        .where(CheckResult.service_id == Service.id)
        .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc())
        .limit(1)
        .correlate(Service)
        .scalar_subquery()
    )
    query = (
        select(Service)
        .where(
            Service.is_active.is_(True),
            Service.id.not_in(excluded),
            (latest.is_(None))
            | (
                extract("epoch", literal(datetime.now(UTC))) - extract("epoch", latest)
                >= Service.check_interval_seconds
            ),
        )
        .order_by(latest.asc().nulls_first(), Service.id)
        .limit(limit)
    )
    return [
        CheckTarget(s.id, s.url, s.timeout_seconds, s.check_interval_seconds)
        for s in session.scalars(query)
    ]


def active_target(session: Session, service_id: UUID) -> CheckTarget | None:
    service = session.get(Service, service_id)
    if service is None or not service.is_active:
        return None
    return CheckTarget(
        service.id, service.url, service.timeout_seconds, service.check_interval_seconds
    )


def latest_result(
    session: Session, service_id: UUID, window: CheckWindow | None = None
) -> CheckResult | None:
    query = select(CheckResult).where(CheckResult.service_id == service_id)
    if window is not None:
        query = query.where(
            CheckResult.checked_at >= window.start, CheckResult.checked_at < window.end
        )
    return session.scalar(
        query.order_by(CheckResult.checked_at.desc(), CheckResult.id.desc()).limit(1)
    )


def list_results(
    session: Session, service_id: UUID, window: CheckWindow, limit: int, offset: int
) -> tuple[list[CheckResult], int]:
    conditions = (
        CheckResult.service_id == service_id,
        CheckResult.checked_at >= window.start,
        CheckResult.checked_at < window.end,
    )
    total = session.scalar(select(func.count()).select_from(CheckResult).where(*conditions)) or 0
    items = session.scalars(
        select(CheckResult)
        .where(*conditions)
        .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(items), total


def aggregate_results(
    session: Session, service_id: UUID, window: CheckWindow
) -> tuple[int, int, float | None, float | None]:
    conditions = (
        CheckResult.service_id == service_id,
        CheckResult.checked_at >= window.start,
        CheckResult.checked_at < window.end,
    )
    row = session.execute(
        select(
            func.count(),
            func.sum(case((CheckResult.status == CheckStatus.UP, 1), else_=0)),
            func.avg(CheckResult.response_time_ms),
            func.count(CheckResult.response_time_ms),
        )
        .select_from(CheckResult)
        .where(*conditions)
    ).one()
    total, up, average, samples = int(row[0]), int(row[1] or 0), row[2], int(row[3])
    p95: float | None = None
    if samples:
        if session.get_bind().dialect.name == "postgresql":
            p95 = session.scalar(
                select(func.percentile_cont(0.95).within_group(CheckResult.response_time_ms)).where(
                    *conditions
                )
            )
        else:
            # Portable equivalent for SQLite fixtures: fetch at most two values.
            rank = (samples - 1) * 0.95
            values = list(
                session.scalars(
                    select(CheckResult.response_time_ms)
                    .where(*conditions, CheckResult.response_time_ms.is_not(None))
                    .order_by(CheckResult.response_time_ms)
                    .offset(math.floor(rank))
                    .limit(2)
                )
            )
            low = values[0]
            high = values[-1]
            assert low is not None and high is not None
            p95 = low + (high - low) * (rank - math.floor(rank))
    return total, up, average, p95
