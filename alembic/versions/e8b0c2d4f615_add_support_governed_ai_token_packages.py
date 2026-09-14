"""Add support-governed commercial AI token packages.

Revision ID: e8b0c2d4f615
Revises: d7f9b1c3e504
Create Date: 2026-07-22
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.base import FlexibleUUID

revision: str = "e8b0c2d4f615"
down_revision: str | Sequence[str] | None = "d7f9b1c3e504"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REQUEST_TABLE = "workspace_ai_token_package_requests"
POLICY_NAME = "workspace_current_id"
WORKSPACE_SETTING = "app.current_workspace_id"


def upgrade() -> None:
    op.create_table(
        "ai_token_packages",
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("token_quantity", sa.BigInteger(), nullable=False),
        sa.Column("commercial_price_cents", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="BRL"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("token_quantity > 0", name="ck_ai_token_package_tokens_positive"),
        sa.CheckConstraint(
            "commercial_price_cents IS NULL OR commercial_price_cents >= 0",
            name="ck_ai_token_package_price_nonnegative",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_ai_token_packages_key"),
    )
    op.create_index(
        "ix_ai_token_packages_active_order",
        "ai_token_packages",
        ["is_active", "sort_order"],
    )

    request_status = sa.Enum(
        "requested",
        "approved",
        "rejected",
        "revoked",
        name="ai_token_package_request_status_enum",
        native_enum=False,
    )
    op.create_table(
        REQUEST_TABLE,
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("package_id", FlexibleUUID(), nullable=False),
        sa.Column("requested_by_user_id", FlexibleUUID(), nullable=False),
        sa.Column("decided_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("status", request_status, nullable=False, server_default="requested"),
        sa.Column("package_key_snapshot", sa.String(length=80), nullable=False),
        sa.Column("package_name_snapshot", sa.String(length=160), nullable=False),
        sa.Column("token_quantity_snapshot", sa.BigInteger(), nullable=False),
        sa.Column("commercial_price_cents_snapshot", sa.Integer(), nullable=True),
        sa.Column("currency_snapshot", sa.String(length=3), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("request_note", sa.Text(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "token_quantity_snapshot > 0",
            name="ck_workspace_ai_token_package_tokens_positive",
        ),
        sa.CheckConstraint(
            "commercial_price_cents_snapshot IS NULL OR commercial_price_cents_snapshot >= 0",
            name="ck_workspace_ai_token_package_price_nonnegative",
        ),
        sa.CheckConstraint(
            "period_end > period_start",
            name="ck_workspace_ai_token_package_period",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["package_id"], ["ai_token_packages.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_workspace_ai_token_package_request_idempotency",
        ),
    )
    op.create_index(
        "ix_workspace_ai_token_package_requests_status",
        REQUEST_TABLE,
        ["workspace_id", "status", "created_at"],
    )
    op.create_index(
        "ix_workspace_ai_token_package_requests_active_period",
        REQUEST_TABLE,
        ["workspace_id", "period_start", "period_end", "status"],
    )

    if op.get_bind().dialect.name == "postgresql":
        predicate = (
            f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
        )
        op.execute(f'ALTER TABLE "{REQUEST_TABLE}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{REQUEST_TABLE}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'''CREATE POLICY "{POLICY_NAME}" ON "{REQUEST_TABLE}"
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING ({predicate}) WITH CHECK ({predicate})'''
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON "{REQUEST_TABLE}"')
        op.execute(f'ALTER TABLE "{REQUEST_TABLE}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{REQUEST_TABLE}" DISABLE ROW LEVEL SECURITY')
    op.drop_index(
        "ix_workspace_ai_token_package_requests_active_period",
        table_name=REQUEST_TABLE,
    )
    op.drop_index(
        "ix_workspace_ai_token_package_requests_status",
        table_name=REQUEST_TABLE,
    )
    op.drop_table(REQUEST_TABLE)
    op.drop_index("ix_ai_token_packages_active_order", table_name="ai_token_packages")
    op.drop_table("ai_token_packages")
