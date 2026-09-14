"""add tenant-scoped AI gateway fallback telemetry

Revision ID: d1e3f5a7b9c2
Revises: c0d2e4f6a8b1
Create Date: 2026-07-24 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "d1e3f5a7b9c2"
down_revision: str | Sequence[str] | None = "c0d2e4f6a8b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "ai_gateway_events"
WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    op.execute(f'ALTER TABLE "{TABLE_NAME}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{TABLE_NAME}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'''CREATE POLICY "{RLS_POLICY}" ON "{TABLE_NAME}"
        AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({predicate}) WITH CHECK ({predicate})'''
    )


def _disable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f'DROP POLICY IF EXISTS "{RLS_POLICY}" ON "{TABLE_NAME}"')
    op.execute(f'ALTER TABLE "{TABLE_NAME}" NO FORCE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{TABLE_NAME}" DISABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("id", FlexibleUUID(), primary_key=True),
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("request_id", sa.String(length=180), nullable=False),
        sa.Column("agent_name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("task_class", sa.String(length=30), nullable=False, server_default=""),
        sa.Column("provider", sa.String(length=60), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("outcome", sa.String(length=180), nullable=False),
        sa.Column("route_key", sa.String(length=260), nullable=False, server_default=""),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "cached_input_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("cost_usd", sa.Numeric(12, 8), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 8), nullable=True),
        sa.Column(
            "request_started",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "billing_unknown",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "fallback_used",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "cache_hit",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("operation", sa.String(length=180), nullable=False, server_default=""),
        sa.Column(
            "metadata_json",
            FlexibleJSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "latency_ms >= 0 AND input_tokens >= 0 AND output_tokens >= 0 "
            "AND cached_input_tokens >= 0",
            name="ck_ai_gateway_events_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_ai_gateway_events_workspace_created",
        TABLE_NAME,
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_ai_gateway_events_request",
        TABLE_NAME,
        ["workspace_id", "request_id"],
    )
    op.create_index(
        "ix_ai_gateway_events_outcome",
        TABLE_NAME,
        ["workspace_id", "outcome"],
    )
    _enable_rls()


def downgrade() -> None:
    _disable_rls()
    op.drop_index("ix_ai_gateway_events_outcome", table_name=TABLE_NAME)
    op.drop_index("ix_ai_gateway_events_request", table_name=TABLE_NAME)
    op.drop_index("ix_ai_gateway_events_workspace_created", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
