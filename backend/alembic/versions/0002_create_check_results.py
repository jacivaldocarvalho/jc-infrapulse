"""Persist HTTP monitoring results.

Revision ID: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "check_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "UP",
                "DOWN",
                "TIMEOUT",
                "DNS_ERROR",
                "TLS_ERROR",
                "CONNECTION_ERROR",
                "HTTP_ERROR",
                native_enum=False,
                create_constraint=True,
                name="check_status",
            ),
            nullable=False,
        ),
        sa.Column("http_status_code", sa.Integer(), nullable=True),
        sa.Column("response_time_ms", sa.Float(), nullable=True),
        sa.Column("error_message", sa.String(200), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_check_results"),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "http_status_code BETWEEN 100 AND 599", name=op.f("ck_check_results_http_status_range")
        ),
        sa.CheckConstraint(
            "response_time_ms >= 0", name=op.f("ck_check_results_response_time_nonnegative")
        ),
    )
    op.create_index(
        "ix_check_results_service_checked_id", "check_results", ["service_id", "checked_at", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_check_results_service_checked_id", table_name="check_results")
    op.drop_table("check_results")
