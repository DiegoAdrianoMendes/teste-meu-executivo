"""add fee_arrangements and time_entries

Revision ID: 8ecdc71ea65b
Revises: 4d4cb0b6d235
Create Date: 2026-07-25 15:00:00.000000

F9 — Honorários jurídicos: `fee_arrangements` (molde contratual de honorários
por caso: fixo, entrada+parcelas, mensalidade, por hora, êxito, quota litis,
híbrido) e `time_entries` (lançamento de horas do modelo POR_HORA, consolidado
em fatura mensal idempotente por `FeeService.close_monthly_invoice`).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision: str = "8ecdc71ea65b"
down_revision: str | Sequence[str] | None = "4d4cb0b6d235"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"
TABLES = (
    "fee_arrangements",
    "time_entries",
)


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    for table_name in TABLES:
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'''CREATE POLICY "{RLS_POLICY}" ON "{table_name}"
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING ({predicate}) WITH CHECK ({predicate})'''
        )


def _disable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name in TABLES:
        op.execute(f'DROP POLICY IF EXISTS "{RLS_POLICY}" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    op.create_table(
        "fee_arrangements",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("matter_id", FlexibleUUID(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "fixo",
                "entrada_exito",
                "mensalidade",
                "por_hora",
                "exito",
                "quota_litis",
                "hibrido",
                name="fee_arrangement_kind_enum",
            ),
            nullable=False,
        ),
        sa.Column("fixed_value", sa.Numeric(15, 2), nullable=True),
        sa.Column("percent", sa.Numeric(6, 3), nullable=True),
        sa.Column("base_calculo_exito", sa.Text(), nullable=True),
        sa.Column("hourly_rate", sa.Numeric(10, 2), nullable=True),
        sa.Column(
            "status",
            sa.Enum("ativo", "encerrado", name="fee_arrangement_status_enum"),
            nullable=False,
            server_default="ativo",
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fee_arrangements_workspace_id", "fee_arrangements", ["workspace_id"])
    op.create_index("ix_fee_arrangements_matter_id", "fee_arrangements", ["matter_id"])
    op.create_index("ix_fee_arrangements_status", "fee_arrangements", ["status"])

    op.create_table(
        "time_entries",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("arrangement_id", FlexibleUUID(), nullable=False),
        sa.Column("matter_id", FlexibleUUID(), nullable=False),
        sa.Column("user_id", FlexibleUUID(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("hourly_rate", sa.Numeric(10, 2), nullable=False),
        sa.Column("billed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("invoice_entry_id", FlexibleUUID(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["arrangement_id"], ["fee_arrangements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["invoice_entry_id"], ["finance_entries.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_time_entries_workspace_id", "time_entries", ["workspace_id"])
    op.create_index("ix_time_entries_arrangement_id", "time_entries", ["arrangement_id"])
    op.create_index("ix_time_entries_matter_id", "time_entries", ["matter_id"])
    op.create_index("ix_time_entries_user_id", "time_entries", ["user_id"])
    op.create_index("ix_time_entries_date", "time_entries", ["date"])
    op.create_index("ix_time_entries_billed", "time_entries", ["billed"])
    op.create_index("ix_time_entries_invoice_entry_id", "time_entries", ["invoice_entry_id"])

    _enable_rls()


def downgrade() -> None:
    _disable_rls()

    op.drop_index("ix_time_entries_invoice_entry_id", table_name="time_entries")
    op.drop_index("ix_time_entries_billed", table_name="time_entries")
    op.drop_index("ix_time_entries_date", table_name="time_entries")
    op.drop_index("ix_time_entries_user_id", table_name="time_entries")
    op.drop_index("ix_time_entries_matter_id", table_name="time_entries")
    op.drop_index("ix_time_entries_arrangement_id", table_name="time_entries")
    op.drop_index("ix_time_entries_workspace_id", table_name="time_entries")
    op.drop_table("time_entries")

    op.drop_index("ix_fee_arrangements_status", table_name="fee_arrangements")
    op.drop_index("ix_fee_arrangements_matter_id", table_name="fee_arrangements")
    op.drop_index("ix_fee_arrangements_workspace_id", table_name="fee_arrangements")
    op.drop_table("fee_arrangements")

    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS fee_arrangement_status_enum")
        op.execute("DROP TYPE IF EXISTS fee_arrangement_kind_enum")
