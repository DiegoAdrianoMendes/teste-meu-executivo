"""Add typed plan entitlements and atomic monthly usage reservations.

Revision ID: c4e6f8a0d003
Revises: b3d5f7a9c002
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision: str = "c4e6f8a0d003"
down_revision: str | Sequence[str] | None = "b3d5f7a9c002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY_NAME = "workspace_current_id"
WORKSPACE_SETTING = "app.current_workspace_id"
TENANT_TABLES = (
    "workspace_monthly_entitlement_usage",
    "entitlement_usage_reservations",
)
ENTITLEMENT_VALUES = (
    "tokens",
    "ai_executions",
    "ai_conversations",
    "storage_bytes",
    "files",
    "users",
    "whatsapp_numbers",
    "agents",
    "automations",
)
RESERVATION_STATUS_VALUES = ("reserved", "committed", "released")


def _entitlement_enum() -> sa.Enum:
    return sa.Enum(
        *ENTITLEMENT_VALUES,
        name="entitlement_key_enum",
        native_enum=False,
        create_constraint=True,
    )


def _reservation_status_enum() -> sa.Enum:
    return sa.Enum(
        *RESERVATION_STATUS_VALUES,
        name="usage_reservation_status_enum",
        native_enum=False,
        create_constraint=True,
    )


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = (
        f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    )
    for table_name in TENANT_TABLES:
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'''CREATE POLICY "{POLICY_NAME}" ON "{table_name}"
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING ({predicate}) WITH CHECK ({predicate})'''
        )


def upgrade() -> None:
    op.create_table(
        "plan_entitlements",
        sa.Column("plan_id", FlexibleUUID(), nullable=False),
        sa.Column("entitlement", _entitlement_enum(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("limit_quantity", sa.BigInteger(), nullable=True),
        sa.Column("hard_limit", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("id", FlexibleUUID(), nullable=False),
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
            "limit_quantity IS NULL OR limit_quantity >= 0",
            name="ck_plan_entitlement_limit_nonnegative",
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "entitlement", name="uq_plan_entitlement_key"),
    )
    op.create_index(
        "ix_plan_entitlement_key",
        "plan_entitlements",
        ["entitlement"],
    )

    op.create_table(
        "workspace_monthly_entitlement_usage",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("entitlement", _entitlement_enum(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column(
            "consumed_quantity", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column(
            "reserved_quantity", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("limit_quantity", sa.BigInteger(), nullable=True),
        sa.Column("hard_limit", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("limit_source", sa.String(length=32), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
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
            "consumed_quantity >= 0",
            name="ck_workspace_monthly_usage_consumed_nonnegative",
        ),
        sa.CheckConstraint(
            "reserved_quantity >= 0",
            name="ck_workspace_monthly_usage_reserved_nonnegative",
        ),
        sa.CheckConstraint(
            "limit_quantity IS NULL OR limit_quantity >= 0",
            name="ck_workspace_monthly_usage_limit_nonnegative",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "entitlement",
            "period_start",
            name="uq_workspace_monthly_usage_key",
        ),
    )
    op.create_index(
        "ix_workspace_monthly_usage_workspace_period",
        "workspace_monthly_entitlement_usage",
        ["workspace_id", "period_start"],
    )

    op.create_table(
        "entitlement_usage_reservations",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("usage_id", FlexibleUUID(), nullable=False),
        sa.Column("entitlement", _entitlement_enum(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("reserved_quantity", sa.BigInteger(), nullable=False),
        sa.Column("committed_quantity", sa.BigInteger(), nullable=True),
        sa.Column("status", _reservation_status_enum(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
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
            "reserved_quantity > 0",
            name="ck_usage_reservation_quantity_positive",
        ),
        sa.CheckConstraint(
            "committed_quantity IS NULL OR committed_quantity >= 0",
            name="ck_usage_reservation_committed_nonnegative",
        ),
        sa.CheckConstraint(
            "((status = 'committed' AND committed_quantity IS NOT NULL) OR "
            "(status <> 'committed' AND committed_quantity IS NULL))",
            name="ck_usage_reservation_committed_state",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["usage_id"],
            ["workspace_monthly_entitlement_usage.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "entitlement",
            "period_start",
            "idempotency_key",
            name="uq_usage_reservation_idempotency",
        ),
    )
    op.create_index(
        "ix_usage_reservation_workspace_period",
        "entitlement_usage_reservations",
        ["workspace_id", "period_start"],
    )
    op.create_index(
        "ix_usage_reservation_usage_status",
        "entitlement_usage_reservations",
        ["usage_id", "status"],
    )
    _enable_rls()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table_name in reversed(TENANT_TABLES):
            op.execute(f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON "{table_name}"')
            op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')

    op.drop_index(
        "ix_usage_reservation_usage_status",
        table_name="entitlement_usage_reservations",
    )
    op.drop_index(
        "ix_usage_reservation_workspace_period",
        table_name="entitlement_usage_reservations",
    )
    op.drop_table("entitlement_usage_reservations")
    op.drop_index(
        "ix_workspace_monthly_usage_workspace_period",
        table_name="workspace_monthly_entitlement_usage",
    )
    op.drop_table("workspace_monthly_entitlement_usage")
    op.drop_index("ix_plan_entitlement_key", table_name="plan_entitlements")
    op.drop_table("plan_entitlements")
