"""add case_parties — Caso 360 canônico (Onda 5, Plano Mestre §5)

Uma tabela nova, ligada a `legal_demands` (Onda 2): `case_parties` — parte
do caso além do cliente titular, com polo processual (CPC — ativo/
passivo/terceiro_interessado), papel específico dentro do polo e
qualificação estruturada (JSON livre).

Não recria a evidência/prova: isso já existe em `case_proof_links`
(alembic/versions/cb28b2376b5d_add_case_strategy_fact_proof_pleading.py) —
o Caso 360 reusa aquela tabela para o bloco "provas" do agregado.

RLS direta por workspace_id, mesmo padrão de `case_facts`/`case_proof_links`
(ver app/db/rls.py::CASE_360_DIRECT_TENANT_TABLES). `legal_demands` já tem
RLS própria — esta migração só referencia, nunca recria essa política.

Revision ID: e7a9c1d3f5b7
Revises: cb28b2376b5d
Create Date: 2026-07-27 11:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "e7a9c1d3f5b7"
down_revision: str | Sequence[str] | None = "cb28b2376b5d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"

CASE_PARTIES_TABLE = "case_parties"
DIRECT_TENANT_TABLES = (CASE_PARTIES_TABLE,)

_CASE_PARTY_POLE_VALUES = ("ativo", "passivo", "terceiro_interessado")


def _workspace_predicate() -> str:
    return f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"


def _enable_rls(table_name: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = _workspace_predicate()
    op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'''CREATE POLICY "{RLS_POLICY}" ON "{table_name}"
        AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({predicate}) WITH CHECK ({predicate})'''
    )


def _disable_rls(table_name: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f'DROP POLICY IF EXISTS "{RLS_POLICY}" ON "{table_name}"')
    op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    op.create_table(
        CASE_PARTIES_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("legal_demand_id", FlexibleUUID(), nullable=False),
        sa.Column("name", sa.String(length=240), nullable=False),
        sa.Column(
            "pole",
            sa.Enum(
                *_CASE_PARTY_POLE_VALUES,
                name="case_party_pole_enum",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=120), nullable=True),
        sa.Column("qualification", FlexibleJSON(), nullable=False, server_default="{}"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["legal_demand_id"], ["legal_demands.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_case_party_workspace_id", CASE_PARTIES_TABLE, ["workspace_id"])
    op.create_index(
        "ix_case_party_legal_demand_id", CASE_PARTIES_TABLE, ["legal_demand_id"]
    )
    op.create_index("ix_case_party_pole", CASE_PARTIES_TABLE, ["pole"])

    for table_name in DIRECT_TENANT_TABLES:
        _enable_rls(table_name)


def downgrade() -> None:
    for table_name in reversed(DIRECT_TENANT_TABLES):
        _disable_rls(table_name)

    op.drop_index("ix_case_party_pole", table_name=CASE_PARTIES_TABLE)
    op.drop_index("ix_case_party_legal_demand_id", table_name=CASE_PARTIES_TABLE)
    op.drop_index("ix_case_party_workspace_id", table_name=CASE_PARTIES_TABLE)
    op.drop_table(CASE_PARTIES_TABLE)
    # Enum local nasce com native_enum=False (CHECK constraint por coluna,
    # não CREATE TYPE) — dropar a tabela já basta, sem DROP TYPE residual
    # (mesmo racional de cb28b2376b5d).
