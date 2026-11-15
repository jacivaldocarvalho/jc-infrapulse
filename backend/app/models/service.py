from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Service(Base):
    __tablename__ = "services"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        CheckConstraint("check_interval_seconds >= 10", name="interval_minimum"),
        CheckConstraint("timeout_seconds >= 1", name="timeout_minimum"),
        CheckConstraint("timeout_seconds < check_interval_seconds", name="timeout_below_interval"),
        Index("ix_services_created_at_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(Text)
    check_interval_seconds: Mapped[int] = mapped_column(Integer, server_default=text("60"))
    timeout_seconds: Mapped[int] = mapped_column(Integer, server_default=text("5"))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
