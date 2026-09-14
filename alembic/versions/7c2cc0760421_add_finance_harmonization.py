"""add finance_harmonization_runs/items/quotas (F14)

Harmonização de documentos financeiros × lançamentos manuais: estende a
conciliação bancária (F7, `bank_transactions`/`reconciliation_matches`) para
qualquer documento (extrato/DRE em PDF/XML/CSV) comparado contra o
financeiro local. `finance_harmonization_runs` é a verificação completa do
mês (sempre nasce `aguardando_validacao`, nunca aplica nada sozinha).
`finance_harmonization_items` é cada linha comparativa (bate | não bate | só
no documento | só no sistema). `finance_harmonization_quotas` é o contador
mensal idempotente (`UNIQUE(workspace_id, competence)`) de 1 verificação
base + 2 bônus padrão + bônus extra do admin da plataforma.

Ver docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §5 F14 (harmonização) /
`app/models/finance_harmonization.py`.

Revision ID: 7c2cc0760421
Revises: 2fbd909dec50
Create Date: 2026-07-25 23:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "7c2cc0760421"
down_revision: str | Sequence[str] | None = "2fbd909dec50"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"
TABLES = (
    "finance_harmonization_runs",
    "finance_harmonization_items",
    "finance_harmonization_quotas",
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
        "finance_harmonization_runs",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("source_document_name", sa.String(length=300), nullable=False),
        sa.Column(
            "source_type",
            sa.Enum("pdf", "xml", "csv", name="finance_harmonization_source_type_enum"),
            nullable=False,
        ),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("competence", sa.String(length=7), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "aguardando_validacao",
                "validado",
                "rejeitado",
                name="finance_harmonization_status_enum",
            ),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("totals", FlexibleJSON(), nullable=False, server_default="{}"),
        sa.Column("parser_warning", sa.String(length=500), nullable=True),
        sa.Column("created_by", FlexibleUUID(), nullable=True),
        sa.Column("decided_by", FlexibleUUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_finance_harmonization_runs_workspace_id", "finance_harmonization_runs", ["workspace_id"]
    )
    op.create_index(
        "ix_finance_harmonization_runs_status", "finance_harmonization_runs", ["status"]
    )
    op.create_index(
        "ix_finance_harmonization_runs_competence", "finance_harmonization_runs", ["competence"]
    )
    op.create_index(
        "ix_finance_harmonization_runs_workspace_status",
        "finance_harmonization_runs",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_finance_harmonization_runs_workspace_competence",
        "finance_harmonization_runs",
        ["workspace_id", "competence"],
    )

    op.create_table(
        "finance_harmonization_items",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("run_id", FlexibleUUID(), nullable=False),
        sa.Column("doc_date", sa.Date(), nullable=True),
        sa.Column("doc_value", sa.Numeric(15, 2), nullable=True),
        sa.Column("doc_description", sa.String(length=500), nullable=True),
        sa.Column("doc_fingerprint", sa.String(length=200), nullable=True),
        sa.Column("doc_raw", FlexibleJSON(), nullable=False, server_default="{}"),
        sa.Column(
            "matched_source",
            sa.Enum(
                "finance_entry", "bank_transaction", name="finance_harmonization_matched_source_enum"
            ),
            nullable=True,
        ),
        sa.Column("matched_entry_id", FlexibleUUID(), nullable=True),
        sa.Column(
            "match_kind",
            sa.Enum(
                "exato",
                "aproximado",
                "so_no_documento",
                "so_no_sistema",
                name="finance_harmonization_match_kind_enum",
            ),
            nullable=False,
        ),
        sa.Column("delta_value", sa.Numeric(15, 2), nullable=True),
        sa.Column("delta_days", sa.Integer(), nullable=True),
        sa.Column(
            "decision",
            sa.Enum(
                "pendente",
                "confirmado",
                "rejeitado",
                "ajustar",
                name="finance_harmonization_decision_enum",
            ),
            nullable=False,
            server_default="pendente",
        ),
        sa.Column("decided_by", FlexibleUUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["finance_harmonization_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_finance_harmonization_items_workspace_id", "finance_harmonization_items", ["workspace_id"]
    )
    op.create_index("ix_finance_harmonization_items_run_id", "finance_harmonization_items", ["run_id"])
    op.create_index(
        "ix_finance_harmonization_items_matched_entry_id",
        "finance_harmonization_items",
        ["matched_entry_id"],
    )
    op.create_index(
        "ix_finance_harmonization_items_match_kind", "finance_harmonization_items", ["match_kind"]
    )
    op.create_index(
        "ix_finance_harmonization_items_decision", "finance_harmonization_items", ["decision"]
    )
    op.create_index(
        "ix_finance_harmonization_items_workspace_run",
        "finance_harmonization_items",
        ["workspace_id", "run_id"],
    )
    op.create_index(
        "ix_finance_harmonization_items_run_kind",
        "finance_harmonization_items",
        ["run_id", "match_kind"],
    )

    op.create_table(
        "finance_harmonization_quotas",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("competence", sa.String(length=7), nullable=False),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bonus_extra", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("granted_by", FlexibleUUID(), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["granted_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "competence", name="uq_finance_harmonization_quota_workspace_competence"
        ),
    )
    op.create_index(
        "ix_finance_harmonization_quotas_workspace_id",
        "finance_harmonization_quotas",
        ["workspace_id"],
    )

    _enable_rls()


def downgrade() -> None:
    _disable_rls()

    op.drop_index(
        "ix_finance_harmonization_quotas_workspace_id", table_name="finance_harmonization_quotas"
    )
    op.drop_table("finance_harmonization_quotas")

    op.drop_index("ix_finance_harmonization_items_run_kind", table_name="finance_harmonization_items")
    op.drop_index(
        "ix_finance_harmonization_items_workspace_run", table_name="finance_harmonization_items"
    )
    op.drop_index("ix_finance_harmonization_items_decision", table_name="finance_harmonization_items")
    op.drop_index("ix_finance_harmonization_items_match_kind", table_name="finance_harmonization_items")
    op.drop_index(
        "ix_finance_harmonization_items_matched_entry_id", table_name="finance_harmonization_items"
    )
    op.drop_index("ix_finance_harmonization_items_run_id", table_name="finance_harmonization_items")
    op.drop_index(
        "ix_finance_harmonization_items_workspace_id", table_name="finance_harmonization_items"
    )
    op.drop_table("finance_harmonization_items")

    op.drop_index(
        "ix_finance_harmonization_runs_workspace_competence", table_name="finance_harmonization_runs"
    )
    op.drop_index(
        "ix_finance_harmonization_runs_workspace_status", table_name="finance_harmonization_runs"
    )
    op.drop_index("ix_finance_harmonization_runs_competence", table_name="finance_harmonization_runs")
    op.drop_index("ix_finance_harmonization_runs_status", table_name="finance_harmonization_runs")
    op.drop_index("ix_finance_harmonization_runs_workspace_id", table_name="finance_harmonization_runs")
    op.drop_table("finance_harmonization_runs")

    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS finance_harmonization_decision_enum")
        op.execute("DROP TYPE IF EXISTS finance_harmonization_match_kind_enum")
        op.execute("DROP TYPE IF EXISTS finance_harmonization_matched_source_enum")
        op.execute("DROP TYPE IF EXISTS finance_harmonization_status_enum")
        op.execute("DROP TYPE IF EXISTS finance_harmonization_source_type_enum")
