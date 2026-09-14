"""Add durable pre-provider execution claims.

Revision ID: 04d6a8c2e140
Revises: f3e5a7c9d124
Create Date: 2026-07-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision: str = "04d6a8c2e140"
down_revision: str | Sequence[str] | None = "f3e5a7c9d124"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "model_provider_call_journal"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("request_id", sa.String(length=180), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "claimed",
                "provider_started",
                "succeeded",
                "uncertain",
                "failed_pre_provider",
                name="model_provider_call_state_enum",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("task_class", sa.String(length=30), nullable=False),
        sa.Column("operation", sa.String(length=180), nullable=False),
        sa.Column("provider", sa.String(length=60), nullable=False),
        sa.Column("model_used", sa.String(length=120), nullable=False),
        sa.Column("route_key", sa.String(length=190), nullable=False),
        sa.Column("estimated_input_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_output_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 8), nullable=False),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("entitlement_reservation_id", FlexibleUUID(), nullable=True),
        sa.Column("provider_request_id", sa.String(length=180), nullable=False),
        sa.Column("actual_input_tokens", sa.Integer(), nullable=False),
        sa.Column("actual_output_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False),
        sa.Column("actual_cost_usd", sa.Numeric(12, 8), nullable=True),
        sa.Column("price_catalog_version", sa.String(length=120), nullable=False),
        sa.Column("route_catalog_version", sa.String(length=120), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=False),
        sa.Column("provider_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.CheckConstraint(
            "estimated_input_tokens >= 0 AND estimated_output_tokens >= 0 "
            "AND reserved_tokens >= 0 AND actual_input_tokens >= 0 "
            "AND actual_output_tokens >= 0 AND cached_input_tokens >= 0 "
            "AND attempt_count >= 0",
            name="ck_model_provider_call_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["entitlement_reservation_id"],
            ["entitlement_usage_reservations.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "request_id",
            name="uq_model_provider_call_workspace_request",
        ),
    )
    op.create_index("ix_model_provider_call_workspace", TABLE, ["workspace_id"])
    op.create_index("ix_model_provider_call_state", TABLE, ["state"])
    op.create_index("ix_model_provider_call_period", TABLE, ["period_start"])


def downgrade() -> None:
    op.drop_index("ix_model_provider_call_period", table_name=TABLE)
    op.drop_index("ix_model_provider_call_state", table_name=TABLE)
    op.drop_index("ix_model_provider_call_workspace", table_name=TABLE)
    op.drop_table(TABLE)
