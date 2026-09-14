"""add finance_recurrences and finance_recurrence_runs

Revision ID: 705c81985819
Revises: e2c0c3a7aa8a
Create Date: 2026-07-25 12:00:00.000000

Nota (F8, sessão concorrente): revision id renomeado de "a1b2c3d4e5f6" para
"705c81985819" — o id anterior colidia com uma migração antiga já mesclada
(a1b2c3d4e5f6_add_billing_platform_and_token_tracking.py), o que quebrava
`alembic heads` (CycleDetected) para todas as sessões. Conteúdo desta
migração inalterado, só o identificador.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "705c81985819"
down_revision: str | Sequence[str] | None = "e2c0c3a7aa8a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"
TABLES = (
    "finance_recurrences",
    "finance_recurrence_runs",
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
        "finance_recurrences",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("client_id", FlexibleUUID(), nullable=False),
        sa.Column("matter_id", FlexibleUUID(), nullable=True),
        sa.Column("entry_title", sa.String(length=200), nullable=False),
        sa.Column("entry_description", sa.Text(), nullable=True),
        sa.Column("entry_value", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column(
            "entry_type",
            # `finance_entry_type_enum` já foi criado em e19f2b7a4c21. Reusar
            # exige postgresql.ENUM: `create_type` é parâmetro do dialeto e o
            # `sa.Enum` genérico o ignora em silêncio, emitindo um segundo
            # CREATE TYPE que derruba a migração em Postgres com
            # DuplicateObject. Em SQLite isso nunca aparece (não há enum
            # nativo), então o erro só surgia no banco real.
            postgresql.ENUM(
                "receivable",
                "expense",
                "refund",
                name="finance_entry_type_enum",
                create_type=False,
            ),
            nullable=False,
            server_default="receivable",
        ),
        sa.Column(
            "kind",
            sa.Enum(
                "assinatura_gateway", "geracao_programada", name="finance_recurrence_kind_enum"
            ),
            nullable=False,
            server_default="geracao_programada",
        ),
        sa.Column(
            "cycle",
            sa.Enum(
                "semanal",
                "quinzenal",
                "mensal",
                "bimestral",
                "trimestral",
                "semestral",
                "anual",
                name="finance_recurrence_cycle_enum",
            ),
            nullable=False,
            server_default="mensal",
        ),
        sa.Column("billing_type", sa.String(length=40), nullable=True),
        sa.Column("next_run", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("max_count", sa.Integer(), nullable=True),
        sa.Column("generated_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "status",
            sa.Enum("ativa", "pausada", "encerrada", name="finance_recurrence_status_enum"),
            nullable=False,
            server_default="ativa",
        ),
        sa.Column("gateway_subscription_id", sa.String(length=120), nullable=True),
        sa.Column("recurrence_metadata", FlexibleJSON(), nullable=False, server_default="{}"),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_finance_recurrences_workspace_id", "finance_recurrences", ["workspace_id"])
    op.create_index("ix_finance_recurrences_client_id", "finance_recurrences", ["client_id"])
    op.create_index("ix_finance_recurrences_matter_id", "finance_recurrences", ["matter_id"])
    op.create_index("ix_finance_recurrences_next_run", "finance_recurrences", ["next_run"])
    op.create_index("ix_finance_recurrences_status", "finance_recurrences", ["status"])
    op.create_index(
        "ix_finance_recurrences_workspace_status", "finance_recurrences", ["workspace_id", "status"]
    )
    op.create_index(
        "ix_finance_recurrences_workspace_next_run",
        "finance_recurrences",
        ["workspace_id", "next_run"],
    )

    op.create_table(
        "finance_recurrence_runs",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("recurrence_id", FlexibleUUID(), nullable=False),
        sa.Column("competence", sa.Date(), nullable=False),
        sa.Column("entry_id", FlexibleUUID(), nullable=True),
        sa.Column("charge_id", FlexibleUUID(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recurrence_id"], ["finance_recurrences.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entry_id"], ["finance_entries.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["charge_id"], ["finance_charges.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recurrence_id", "competence", name="uq_finance_recurrence_runs_recurrence_competence"
        ),
    )
    op.create_index(
        "ix_finance_recurrence_runs_workspace_id", "finance_recurrence_runs", ["workspace_id"]
    )
    op.create_index(
        "ix_finance_recurrence_runs_recurrence_id", "finance_recurrence_runs", ["recurrence_id"]
    )

    _enable_rls()


def downgrade() -> None:
    _disable_rls()

    op.drop_index("ix_finance_recurrence_runs_recurrence_id", table_name="finance_recurrence_runs")
    op.drop_index("ix_finance_recurrence_runs_workspace_id", table_name="finance_recurrence_runs")
    op.drop_table("finance_recurrence_runs")

    op.drop_index("ix_finance_recurrences_workspace_next_run", table_name="finance_recurrences")
    op.drop_index("ix_finance_recurrences_workspace_status", table_name="finance_recurrences")
    op.drop_index("ix_finance_recurrences_status", table_name="finance_recurrences")
    op.drop_index("ix_finance_recurrences_next_run", table_name="finance_recurrences")
    op.drop_index("ix_finance_recurrences_matter_id", table_name="finance_recurrences")
    op.drop_index("ix_finance_recurrences_client_id", table_name="finance_recurrences")
    op.drop_index("ix_finance_recurrences_workspace_id", table_name="finance_recurrences")
    op.drop_table("finance_recurrences")

    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS finance_recurrence_status_enum")
        op.execute("DROP TYPE IF EXISTS finance_recurrence_cycle_enum")
        op.execute("DROP TYPE IF EXISTS finance_recurrence_kind_enum")
