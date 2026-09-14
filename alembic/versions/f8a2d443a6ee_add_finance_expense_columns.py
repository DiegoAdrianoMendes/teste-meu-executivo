"""add finance expense columns (F8)

Estende `finance_entries` (sem quebrar F1/F2) com category, cost_center,
reimbursable, reimbursement_entry_id (FK self), receipt_document_ref
(FK storage_objects), recurrence_id e approval_status/approved_by/approved_at
para o fluxo de aprovação de despesas acima do limite do workspace.
Ver docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §C.3/F8.

Revision ID: f8a2d443a6ee
Revises: 705c81985819
Create Date: 2026-07-25 00:00:00.000000

Encadeada após 705c81985819 (F3 — finance_recurrences), pois `recurrence_id`
referencia conceitualmente aquele molde (sem FK, ver nota no modelo).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.db.base import FlexibleUUID

revision: str = "f8a2d443a6ee"
down_revision: str | Sequence[str] | None = "705c81985819"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `op.add_column` NÃO emite CREATE TYPE: o autocreate do sa.Enum só roda em
    # create_table. Sem isto a migração morre em Postgres com UndefinedObject
    # ("type finance_expense_approval_status_enum does not exist"). Em SQLite
    # nunca aparece, porque não existe enum nativo — o erro só nasce no banco
    # real. Criar explícito e depois referenciar com create_type=False é a
    # convenção já usada em a1b2c3d4e5f6 e 4e5f6a7b8c0.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DO $$ BEGIN "
            "CREATE TYPE finance_expense_approval_status_enum AS ENUM "
            "('not_required', 'pending', 'approved', 'rejected'); "
            "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
        )

    op.add_column("finance_entries", sa.Column("category", sa.String(length=80), nullable=True))
    op.add_column("finance_entries", sa.Column("cost_center", sa.String(length=80), nullable=True))
    op.add_column(
        "finance_entries",
        sa.Column("reimbursable", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "finance_entries",
        sa.Column("reimbursement_entry_id", FlexibleUUID(), nullable=True),
    )
    op.add_column(
        "finance_entries",
        sa.Column("receipt_document_ref", FlexibleUUID(), nullable=True),
    )
    op.add_column("finance_entries", sa.Column("recurrence_id", FlexibleUUID(), nullable=True))
    op.add_column(
        "finance_entries",
        sa.Column(
            "approval_status",
            postgresql.ENUM(
                "not_required",
                "pending",
                "approved",
                "rejected",
                name="finance_expense_approval_status_enum",
                create_type=False,
            ).with_variant(
                sa.Enum(
                    "not_required",
                    "pending",
                    "approved",
                    "rejected",
                    name="finance_expense_approval_status_enum",
                ),
                "sqlite",
            ),
            nullable=False,
            server_default="not_required",
        ),
    )
    op.add_column("finance_entries", sa.Column("approved_by", FlexibleUUID(), nullable=True))
    op.add_column(
        "finance_entries",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_unique_constraint(
        "uq_finance_entries_reimbursement_entry_id",
        "finance_entries",
        ["reimbursement_entry_id"],
    )
    op.create_foreign_key(
        "fk_finance_entries_reimbursement_entry_id",
        "finance_entries",
        "finance_entries",
        ["reimbursement_entry_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_finance_entries_receipt_document_ref",
        "finance_entries",
        "storage_objects",
        ["receipt_document_ref"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_finance_entries_approved_by",
        "finance_entries",
        "users",
        ["approved_by"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_index("ix_finance_entries_category", "finance_entries", ["category"])
    op.create_index("ix_finance_entries_cost_center", "finance_entries", ["cost_center"])
    op.create_index("ix_finance_entries_receipt_document_ref", "finance_entries", ["receipt_document_ref"])
    op.create_index("ix_finance_entries_recurrence_id", "finance_entries", ["recurrence_id"])
    op.create_index("ix_finance_entries_approval_status", "finance_entries", ["approval_status"])


def downgrade() -> None:
    op.drop_index("ix_finance_entries_approval_status", table_name="finance_entries")
    op.drop_index("ix_finance_entries_recurrence_id", table_name="finance_entries")
    op.drop_index("ix_finance_entries_receipt_document_ref", table_name="finance_entries")
    op.drop_index("ix_finance_entries_cost_center", table_name="finance_entries")
    op.drop_index("ix_finance_entries_category", table_name="finance_entries")

    op.drop_constraint("fk_finance_entries_approved_by", "finance_entries", type_="foreignkey")
    op.drop_constraint("fk_finance_entries_receipt_document_ref", "finance_entries", type_="foreignkey")
    op.drop_constraint("fk_finance_entries_reimbursement_entry_id", "finance_entries", type_="foreignkey")
    op.drop_constraint("uq_finance_entries_reimbursement_entry_id", "finance_entries", type_="unique")

    op.drop_column("finance_entries", "approved_at")
    op.drop_column("finance_entries", "approved_by")
    op.drop_column("finance_entries", "approval_status")
    op.drop_column("finance_entries", "recurrence_id")
    op.drop_column("finance_entries", "receipt_document_ref")
    op.drop_column("finance_entries", "reimbursement_entry_id")
    # Faltava: sem este drop a volta deixava `reimbursable` para trás e a
    # reaplicação da cadeia morria com DuplicateColumn. Downgrade que não
    # desfaz tudo é rollback que não funciona em produção.
    op.drop_column("finance_entries", "reimbursable")
    op.drop_column("finance_entries", "cost_center")
    op.drop_column("finance_entries", "category")

    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS finance_expense_approval_status_enum")
