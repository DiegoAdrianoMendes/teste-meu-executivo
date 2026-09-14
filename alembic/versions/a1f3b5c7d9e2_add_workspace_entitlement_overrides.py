"""Add per-account entitlement overrides (Blackbox function toggles).

Revision ID: a1f3b5c7d9e2
Revises: 8d3ee1871532
Create Date: 2026-07-26 03:10:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision: str = "a1f3b5c7d9e2"
down_revision: str | Sequence[str] | None = "8d3ee1871532"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "workspace_entitlement_overrides"
POLICY_NAME = "workspace_current_id"
WORKSPACE_SETTING = "app.current_workspace_id"
ENTITLEMENT_VALUES = (
    "tokens",
    "ai_executions",
    "ai_conversations",
    "audio_minutes",
    "storage_bytes",
    "files",
    "users",
    "whatsapp_numbers",
    "agents",
    "automations",
    "digital_signature",
    "calendar",
    "email",
    "webhooks",
    "advanced_audit",
    "success_manager",
    "legal_areas",
)


def _entitlement_enum() -> sa.Enum:
    return sa.Enum(
        *ENTITLEMENT_VALUES,
        name="entitlement_key_enum",
        native_enum=False,
        create_constraint=True,
    )


def upgrade() -> None:
    override_mode_enum = sa.Enum(
        "grant",
        "revoke",
        "limit",
        name="workspace_entitlement_override_mode_enum",
        native_enum=False,
        create_constraint=True,
    )
    op.create_table(
        TABLE,
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("entitlement", _entitlement_enum(), nullable=False),
        sa.Column("mode", override_mode_enum, nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "quantity IS NULL OR quantity >= 0",
            name="ck_workspace_entitlement_override_quantity_nonnegative",
        ),
        sa.CheckConstraint(
            "mode <> 'limit' OR quantity IS NOT NULL",
            name="ck_workspace_entitlement_override_limit_requires_quantity",
        ),
        sa.CheckConstraint(
            "length(trim(reason)) > 0",
            name="ck_workspace_entitlement_override_reason_required",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "entitlement", name="uq_workspace_entitlement_override_key"
        ),
    )
    op.create_index(
        "ix_workspace_entitlement_override_workspace",
        TABLE,
        ["workspace_id"],
    )

    if op.get_bind().dialect.name == "postgresql":
        predicate = (
            f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
        )
        op.execute(f'ALTER TABLE "{TABLE}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{TABLE}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'''CREATE POLICY "{POLICY_NAME}" ON "{TABLE}"
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING ({predicate}) WITH CHECK ({predicate})'''
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON "{TABLE}"')
        op.execute(f'ALTER TABLE "{TABLE}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{TABLE}" DISABLE ROW LEVEL SECURITY')
    op.drop_index("ix_workspace_entitlement_override_workspace", table_name=TABLE)
    op.drop_table(TABLE)
