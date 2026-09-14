"""Add idempotent model usage receipts and task aggregates.

Revision ID: f3e5a7c9d124
Revises: f2d4e6a8b013
Create Date: 2026-07-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision: str = "f3e5a7c9d124"
down_revision: str | Sequence[str] | None = "f2d4e6a8b013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEDGER_TABLE = "platform_model_usage_ledger"


def _enable_append_only_guard() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION klm_reject_append_only_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            f'''CREATE TRIGGER "trg_{LEDGER_TABLE}_immutable"
            BEFORE UPDATE OR DELETE ON "{LEDGER_TABLE}"
            FOR EACH ROW EXECUTE FUNCTION klm_reject_append_only_mutation()'''
        )
    elif dialect == "sqlite":
        for operation in ("update", "delete"):
            op.execute(
                f'''
                CREATE TRIGGER IF NOT EXISTS "trg_{LEDGER_TABLE}_immutable_{operation}"
                BEFORE {operation.upper()} ON "{LEDGER_TABLE}"
                BEGIN
                    SELECT RAISE(ABORT, '{LEDGER_TABLE} is immutable');
                END
                '''
            )


def _disable_append_only_guard() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            f'DROP TRIGGER IF EXISTS "trg_{LEDGER_TABLE}_immutable" ON "{LEDGER_TABLE}"'
        )
    elif dialect == "sqlite":
        for operation in ("update", "delete"):
            op.execute(
                f'DROP TRIGGER IF EXISTS "trg_{LEDGER_TABLE}_immutable_{operation}"'
            )


def upgrade() -> None:
    op.create_table(
        "platform_token_usage_by_task",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("task_class", sa.String(length=30), nullable=False),
        sa.Column("model_used", sa.String(length=120), nullable=False),
        sa.Column("provider", sa.String(length=60), nullable=False),
        sa.Column("usage_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_runs", sa.Integer(), nullable=False),
        sa.Column("total_input_tokens", sa.Integer(), nullable=False),
        sa.Column("total_output_tokens", sa.Integer(), nullable=False),
        sa.Column("total_cost_usd", sa.Numeric(10, 6), nullable=False),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "task_class",
            "model_used",
            "provider",
            "usage_date",
            name="uq_token_usage_task_workspace_model_date",
        ),
    )
    op.create_index(
        "ix_token_usage_task_date",
        "platform_token_usage_by_task",
        ["usage_date"],
    )
    op.create_index(
        "ix_token_usage_task_workspace",
        "platform_token_usage_by_task",
        ["workspace_id"],
    )
    op.create_index(
        "ix_token_usage_task_class",
        "platform_token_usage_by_task",
        ["task_class"],
    )

    op.create_table(
        LEDGER_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("request_id", sa.String(length=180), nullable=False),
        sa.Column("task_class", sa.String(length=30), nullable=False),
        sa.Column("operation", sa.String(length=180), nullable=False),
        sa.Column("model_used", sa.String(length=120), nullable=False),
        sa.Column("provider", sa.String(length=60), nullable=False),
        sa.Column("provider_request_id", sa.String(length=180), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 8), nullable=False),
        sa.Column("price_catalog_version", sa.String(length=120), nullable=False),
        sa.Column("route_catalog_version", sa.String(length=120), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "request_id",
            name="uq_model_usage_ledger_workspace_request",
        ),
    )
    op.create_index(
        "ix_model_usage_ledger_recorded",
        LEDGER_TABLE,
        ["recorded_at"],
    )
    op.create_index(
        "ix_model_usage_ledger_workspace",
        LEDGER_TABLE,
        ["workspace_id"],
    )
    op.create_index(
        "ix_model_usage_ledger_task_class",
        LEDGER_TABLE,
        ["task_class"],
    )
    _enable_append_only_guard()


def downgrade() -> None:
    _disable_append_only_guard()
    op.drop_index("ix_model_usage_ledger_task_class", table_name=LEDGER_TABLE)
    op.drop_index("ix_model_usage_ledger_workspace", table_name=LEDGER_TABLE)
    op.drop_index("ix_model_usage_ledger_recorded", table_name=LEDGER_TABLE)
    op.drop_table(LEDGER_TABLE)
    op.drop_index(
        "ix_token_usage_task_class",
        table_name="platform_token_usage_by_task",
    )
    op.drop_index(
        "ix_token_usage_task_workspace",
        table_name="platform_token_usage_by_task",
    )
    op.drop_index(
        "ix_token_usage_task_date",
        table_name="platform_token_usage_by_task",
    )
    op.drop_table("platform_token_usage_by_task")
