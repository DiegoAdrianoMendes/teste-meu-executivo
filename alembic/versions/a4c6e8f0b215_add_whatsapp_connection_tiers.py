"""add WhatsApp connection tier/ramp-up tracking

Revision ID: a4c6e8f0b215
Revises: 0b7c9e1a2d34
Create Date: 2026-07-23 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision: str = "a4c6e8f0b215"
down_revision: str | Sequence[str] | None = "0b7c9e1a2d34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"
TABLE_NAME = "whatsapp_connection_tiers"

TIER_ENUM_NAME = "wa_connection_tier_enum"
TIER_VALUES = ("tier_250", "tier_1k", "tier_10k", "tier_100k", "unlimited")


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
    tier_enum = sa.Enum(*TIER_VALUES, name=TIER_ENUM_NAME)

    op.create_table(
        TABLE_NAME,
        sa.Column("id", FlexibleUUID(), primary_key=True),
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("connection_id", FlexibleUUID(), nullable=False),
        sa.Column(
            "tier",
            tier_enum,
            nullable=False,
            server_default="tier_250",
        ),
        sa.Column(
            "daily_unique_recipients",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "window_started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "ramp_up_stage",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("quality_rating", sa.String(length=20), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["whatsapp_connections.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "connection_id", name="uq_whatsapp_connection_tiers_connection"
        ),
        sa.CheckConstraint(
            "quality_rating IS NULL OR quality_rating IN ('verde', 'amarelo', 'vermelho')",
            name="ck_whatsapp_connection_tiers_quality_rating",
        ),
        sa.CheckConstraint(
            "daily_unique_recipients >= 0",
            name="ck_whatsapp_connection_tiers_daily_unique_recipients",
        ),
        sa.CheckConstraint(
            "ramp_up_stage >= 0",
            name="ck_whatsapp_connection_tiers_ramp_up_stage",
        ),
    )
    op.create_index(
        "ix_whatsapp_connection_tiers_workspace",
        TABLE_NAME,
        ["workspace_id"],
    )
    op.create_index(
        "ix_whatsapp_connection_tiers_connection_id",
        TABLE_NAME,
        ["connection_id"],
    )

    _enable_rls()


def downgrade() -> None:
    _disable_rls()

    op.drop_index("ix_whatsapp_connection_tiers_connection_id", table_name=TABLE_NAME)
    op.drop_index("ix_whatsapp_connection_tiers_workspace", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        sa.Enum(name=TIER_ENUM_NAME).drop(bind, checkfirst=True)
