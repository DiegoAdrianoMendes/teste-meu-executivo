"""add finance_charges and asaas_webhook_receipts

Revision ID: e2c0c3a7aa8a
Revises: 2f4a6c8e0b31
Create Date: 2026-07-24 19:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "e2c0c3a7aa8a"
down_revision: str | Sequence[str] | None = "2f4a6c8e0b31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"
TABLES = (
    "finance_charges",
    "asaas_webhook_receipts",
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
        "finance_charges",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("entry_id", FlexibleUUID(), nullable=False),
        sa.Column(
            "gateway",
            sa.Enum("asaas", name="finance_charge_gateway_enum"),
            nullable=False,
            server_default="asaas",
        ),
        sa.Column("external_id", sa.String(length=120), nullable=True),
        sa.Column("billing_type", sa.String(length=40), nullable=True),
        sa.Column(
            "charge_kind",
            sa.Enum("avulsa", "parcela", "recorrencia", name="finance_charge_kind_enum"),
            nullable=False,
            server_default="avulsa",
        ),
        sa.Column(
            "status",
            sa.Enum(
                "rascunho",
                "criada",
                "visualizada",
                "confirmada",
                "recebida",
                "vencida",
                "estornada",
                "chargeback",
                "cancelada",
                name="finance_charge_status_enum",
            ),
            nullable=False,
            server_default="rascunho",
        ),
        sa.Column("value", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("net_value", sa.Numeric(15, 2), nullable=True),
        sa.Column("fee_value", sa.Numeric(15, 2), nullable=True),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invoice_url", sa.String(length=500), nullable=True),
        sa.Column("bank_slip_url", sa.String(length=500), nullable=True),
        sa.Column("pix_payload", sa.String(length=2000), nullable=True),
        sa.Column("external_reference", sa.String(length=120), nullable=True),
        sa.Column("installment_ref", sa.String(length=120), nullable=True),
        sa.Column("subscription_ref", sa.String(length=120), nullable=True),
        sa.Column("payload_snapshot", FlexibleJSON(), nullable=False, server_default="{}"),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entry_id"], ["finance_entries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entry_id", name="uq_finance_charges_entry_id"),
    )
    op.create_index("ix_finance_charges_workspace_id", "finance_charges", ["workspace_id"])
    op.create_index("ix_finance_charges_entry_id", "finance_charges", ["entry_id"])
    op.create_index("ix_finance_charges_external_id", "finance_charges", ["external_id"])
    op.create_index("ix_finance_charges_external_reference", "finance_charges", ["external_reference"])
    op.create_index("ix_finance_charges_status", "finance_charges", ["status"])
    op.create_index("ix_finance_charges_due_date", "finance_charges", ["due_date"])
    op.create_index(
        "ix_finance_charges_workspace_status", "finance_charges", ["workspace_id", "status"]
    )
    op.create_index(
        "ix_finance_charges_workspace_due_date", "finance_charges", ["workspace_id", "due_date"]
    )

    op.create_table(
        "asaas_webhook_receipts",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum("recebido", "processado", "erro", name="asaas_webhook_receipt_status_enum"),
            nullable=False,
            server_default="recebido",
        ),
        sa.Column("payload", FlexibleJSON(), nullable=False, server_default="{}"),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "event_id", name="uq_asaas_webhook_receipts_workspace_event_id"),
    )
    op.create_index("ix_asaas_webhook_receipts_workspace_id", "asaas_webhook_receipts", ["workspace_id"])
    op.create_index("ix_asaas_webhook_receipts_status", "asaas_webhook_receipts", ["status"])
    op.create_index(
        "ix_asaas_webhook_receipts_workspace_status",
        "asaas_webhook_receipts",
        ["workspace_id", "status"],
    )

    _enable_rls()


def downgrade() -> None:
    _disable_rls()

    op.drop_index("ix_asaas_webhook_receipts_workspace_status", table_name="asaas_webhook_receipts")
    op.drop_index("ix_asaas_webhook_receipts_status", table_name="asaas_webhook_receipts")
    op.drop_index("ix_asaas_webhook_receipts_workspace_id", table_name="asaas_webhook_receipts")
    op.drop_table("asaas_webhook_receipts")

    op.drop_index("ix_finance_charges_workspace_due_date", table_name="finance_charges")
    op.drop_index("ix_finance_charges_workspace_status", table_name="finance_charges")
    op.drop_index("ix_finance_charges_due_date", table_name="finance_charges")
    op.drop_index("ix_finance_charges_status", table_name="finance_charges")
    op.drop_index("ix_finance_charges_external_reference", table_name="finance_charges")
    op.drop_index("ix_finance_charges_external_id", table_name="finance_charges")
    op.drop_index("ix_finance_charges_entry_id", table_name="finance_charges")
    op.drop_index("ix_finance_charges_workspace_id", table_name="finance_charges")
    op.drop_table("finance_charges")

    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS asaas_webhook_receipt_status_enum")
        op.execute("DROP TYPE IF EXISTS finance_charge_status_enum")
        op.execute("DROP TYPE IF EXISTS finance_charge_kind_enum")
        op.execute("DROP TYPE IF EXISTS finance_charge_gateway_enum")
