"""add bank_transactions and reconciliation_matches (F7)

Conciliação bancária: `bank_transactions` guarda linhas cruas importadas de
OFX/CSV ou puxadas do extrato Asaas (fingerprint único por workspace evita
duplicata em reimportação). `reconciliation_matches` guarda a sugestão ou
confirmação de conciliação de cada linha (1:1). Ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §5 F7 / §3.8.

Revision ID: 7d525e80f1c2
Revises: 601bf15e6501
Create Date: 2026-07-25 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "7d525e80f1c2"
down_revision: str | Sequence[str] | None = "601bf15e6501"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"
TABLES = (
    "bank_transactions",
    "reconciliation_matches",
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
        "bank_transactions",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column(
            "source",
            sa.Enum("ofx", "csv", "asaas", name="bank_transaction_source_enum"),
            nullable=False,
        ),
        sa.Column("external_fingerprint", sa.String(length=200), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Numeric(15, 2), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("raw", FlexibleJSON(), nullable=False, server_default="{}"),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "external_fingerprint",
            name="uq_bank_transactions_workspace_fingerprint",
        ),
    )
    op.create_index("ix_bank_transactions_workspace_id", "bank_transactions", ["workspace_id"])
    op.create_index(
        "ix_bank_transactions_external_fingerprint", "bank_transactions", ["external_fingerprint"]
    )
    op.create_index("ix_bank_transactions_date", "bank_transactions", ["date"])
    op.create_index(
        "ix_bank_transactions_workspace_date", "bank_transactions", ["workspace_id", "date"]
    )

    op.create_table(
        "reconciliation_matches",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("bank_transaction_id", FlexibleUUID(), nullable=False),
        sa.Column(
            "target_kind",
            sa.Enum("charge", "entry", "expense", name="reconciliation_target_kind_enum"),
            nullable=True,
        ),
        sa.Column("target_id", FlexibleUUID(), nullable=True),
        sa.Column(
            "method",
            sa.Enum("deterministico", "ia", "manual", name="reconciliation_method_enum"),
            nullable=False,
        ),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("suggested_category", sa.String(length=80), nullable=True),
        sa.Column("suggested_tags", FlexibleJSON(), nullable=False, server_default="[]"),
        sa.Column(
            "status",
            sa.Enum("sugerido", "confirmado", "rejeitado", name="reconciliation_status_enum"),
            nullable=False,
            server_default="sugerido",
        ),
        sa.Column("confirmed_by", FlexibleUUID(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ai_snapshot", FlexibleJSON(), nullable=False, server_default="{}"),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["bank_transaction_id"], ["bank_transactions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "bank_transaction_id", name="uq_reconciliation_matches_bank_transaction_id"
        ),
    )
    op.create_index(
        "ix_reconciliation_matches_workspace_id", "reconciliation_matches", ["workspace_id"]
    )
    op.create_index(
        "ix_reconciliation_matches_bank_transaction_id",
        "reconciliation_matches",
        ["bank_transaction_id"],
    )
    op.create_index("ix_reconciliation_matches_status", "reconciliation_matches", ["status"])
    op.create_index("ix_reconciliation_matches_target_id", "reconciliation_matches", ["target_id"])
    op.create_index(
        "ix_reconciliation_matches_workspace_status",
        "reconciliation_matches",
        ["workspace_id", "status"],
    )

    _enable_rls()


def downgrade() -> None:
    _disable_rls()

    op.drop_index("ix_reconciliation_matches_workspace_status", table_name="reconciliation_matches")
    op.drop_index("ix_reconciliation_matches_target_id", table_name="reconciliation_matches")
    op.drop_index("ix_reconciliation_matches_status", table_name="reconciliation_matches")
    op.drop_index("ix_reconciliation_matches_bank_transaction_id", table_name="reconciliation_matches")
    op.drop_index("ix_reconciliation_matches_workspace_id", table_name="reconciliation_matches")
    op.drop_table("reconciliation_matches")

    op.drop_index("ix_bank_transactions_workspace_date", table_name="bank_transactions")
    op.drop_index("ix_bank_transactions_date", table_name="bank_transactions")
    op.drop_index("ix_bank_transactions_external_fingerprint", table_name="bank_transactions")
    op.drop_index("ix_bank_transactions_workspace_id", table_name="bank_transactions")
    op.drop_table("bank_transactions")

    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS reconciliation_status_enum")
        op.execute("DROP TYPE IF EXISTS reconciliation_method_enum")
        op.execute("DROP TYPE IF EXISTS reconciliation_target_kind_enum")
        op.execute("DROP TYPE IF EXISTS bank_transaction_source_enum")
