"""Create the service registry.

Revision ID: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "services",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column(
            "check_interval_seconds", sa.Integer(), server_default=sa.text("60"), nullable=False
        ),
        sa.Column("timeout_seconds", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_services"),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_services_name_not_blank")),
        sa.CheckConstraint(
            "check_interval_seconds >= 10", name=op.f("ck_services_interval_minimum")
        ),
        sa.CheckConstraint("timeout_seconds >= 1", name=op.f("ck_services_timeout_minimum")),
        sa.CheckConstraint(
            "timeout_seconds < check_interval_seconds",
            name=op.f("ck_services_timeout_below_interval"),
        ),
    )
    op.create_index("ix_services_created_at_id", "services", ["created_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_services_created_at_id", table_name="services")
    op.drop_table("services")
