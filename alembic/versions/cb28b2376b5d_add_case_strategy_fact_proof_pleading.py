"""add case strategy matrix — fact/proof/pleading (Onda 5, Plano Mestre §3 item 8)

Três tabelas novas, todas ligadas a `legal_demands` (Onda 2), que formam a
Matriz Estratégia Fato–Prova–Pedido:

1. `case_facts` — fato alegado (cronologia/data, controvertido, origem).
2. `case_proof_links` — vínculo fato↔prova (força enum, status
   tem/falta/solicitada), referenciando `documents` quando a prova já foi
   anexada.
3. `case_pleadings` — pedido, com o fundamento e os fatos (`case_facts`)
   que o sustentam.

RLS direta por workspace_id nas três tabelas novas, mesmo padrão de
`distribution_rules`/`sla_escalation_logs` (ver app/db/rls.py::
CASE_STRATEGY_DIRECT_TENANT_TABLES). `legal_demands`/`documents` já têm RLS
própria — esta migração só referencia, nunca recria essas políticas.

Revision ID: cb28b2376b5d
Revises: c2d4f6a8e0b3
Create Date: 2026-07-27 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "cb28b2376b5d"
down_revision: str | Sequence[str] | None = "c2d4f6a8e0b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"

CASE_FACTS_TABLE = "case_facts"
CASE_PROOF_LINKS_TABLE = "case_proof_links"
CASE_PLEADINGS_TABLE = "case_pleadings"
DIRECT_TENANT_TABLES = (
    CASE_FACTS_TABLE,
    CASE_PROOF_LINKS_TABLE,
    CASE_PLEADINGS_TABLE,
)

_PROOF_FORCE_VALUES = ("plena", "robusta", "mediana", "indiciaria", "fragil")
_PROOF_STATUS_VALUES = ("tem", "falta", "solicitada")


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
    # ── case_facts ───────────────────────────────────────────────────────
    op.create_table(
        CASE_FACTS_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("legal_demand_id", FlexibleUUID(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_controverted", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("origin", sa.String(length=200), nullable=True),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=True),
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
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_case_fact_workspace_id", CASE_FACTS_TABLE, ["workspace_id"])
    op.create_index("ix_case_fact_legal_demand_id", CASE_FACTS_TABLE, ["legal_demand_id"])
    op.create_index("ix_case_fact_is_controverted", CASE_FACTS_TABLE, ["is_controverted"])

    # ── case_proof_links ─────────────────────────────────────────────────
    op.create_table(
        CASE_PROOF_LINKS_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("legal_demand_id", FlexibleUUID(), nullable=False),
        sa.Column("fact_id", FlexibleUUID(), nullable=False),
        sa.Column("document_id", FlexibleUUID(), nullable=True),
        sa.Column(
            "force",
            sa.Enum(
                *_PROOF_FORCE_VALUES,
                name="case_proof_force_enum",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                *_PROOF_STATUS_VALUES,
                name="case_proof_status_enum",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="falta",
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("obtained_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=True),
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
        sa.ForeignKeyConstraint(["fact_id"], ["case_facts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_case_proof_link_workspace_id", CASE_PROOF_LINKS_TABLE, ["workspace_id"]
    )
    op.create_index(
        "ix_case_proof_link_legal_demand_id", CASE_PROOF_LINKS_TABLE, ["legal_demand_id"]
    )
    op.create_index("ix_case_proof_link_fact_id", CASE_PROOF_LINKS_TABLE, ["fact_id"])
    op.create_index(
        "ix_case_proof_link_document_id", CASE_PROOF_LINKS_TABLE, ["document_id"]
    )
    op.create_index("ix_case_proof_link_status", CASE_PROOF_LINKS_TABLE, ["status"])

    # ── case_pleadings ───────────────────────────────────────────────────
    op.create_table(
        CASE_PLEADINGS_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("legal_demand_id", FlexibleUUID(), nullable=False),
        sa.Column("request_text", sa.Text(), nullable=False),
        sa.Column("legal_basis", sa.Text(), nullable=True),
        sa.Column(
            "supporting_fact_ids", FlexibleJSON(), nullable=False, server_default="[]"
        ),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=True),
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
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_case_pleading_workspace_id", CASE_PLEADINGS_TABLE, ["workspace_id"]
    )
    op.create_index(
        "ix_case_pleading_legal_demand_id", CASE_PLEADINGS_TABLE, ["legal_demand_id"]
    )

    for table_name in DIRECT_TENANT_TABLES:
        _enable_rls(table_name)


def downgrade() -> None:
    for table_name in reversed(DIRECT_TENANT_TABLES):
        _disable_rls(table_name)

    op.drop_index("ix_case_pleading_legal_demand_id", table_name=CASE_PLEADINGS_TABLE)
    op.drop_index("ix_case_pleading_workspace_id", table_name=CASE_PLEADINGS_TABLE)
    op.drop_table(CASE_PLEADINGS_TABLE)

    op.drop_index("ix_case_proof_link_status", table_name=CASE_PROOF_LINKS_TABLE)
    op.drop_index("ix_case_proof_link_document_id", table_name=CASE_PROOF_LINKS_TABLE)
    op.drop_index("ix_case_proof_link_fact_id", table_name=CASE_PROOF_LINKS_TABLE)
    op.drop_index(
        "ix_case_proof_link_legal_demand_id", table_name=CASE_PROOF_LINKS_TABLE
    )
    op.drop_index("ix_case_proof_link_workspace_id", table_name=CASE_PROOF_LINKS_TABLE)
    op.drop_table(CASE_PROOF_LINKS_TABLE)

    op.drop_index("ix_case_fact_is_controverted", table_name=CASE_FACTS_TABLE)
    op.drop_index("ix_case_fact_legal_demand_id", table_name=CASE_FACTS_TABLE)
    op.drop_index("ix_case_fact_workspace_id", table_name=CASE_FACTS_TABLE)
    op.drop_table(CASE_FACTS_TABLE)
    # Enums locais nascem com native_enum=False (CHECK constraint por
    # coluna, não CREATE TYPE) — dropar a tabela já basta, sem DROP TYPE
    # residual (mesmo racional de c2d4f6a8e0b3).
