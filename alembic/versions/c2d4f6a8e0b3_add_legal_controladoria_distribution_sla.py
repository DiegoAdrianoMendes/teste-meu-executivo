"""add legal controladoria — distribution + SLA escalation (Onda 4, Plano Mestre §8)

Duas frentes na mesma migração porque são a mesma feature (controladoria
jurídica) e nascem juntas:

1. `distribution_rules` + `distribution_decisions` — distribuição
   configurável de trabalho jurídico (LegalDemand) com histórico auditável
   de toda (re)atribuição (sempre com motivo).
2. `sla_escalation_logs` — histórico de escalonamento de SLA em camadas,
   com confirmação de leitura (`acknowledged_at`).
3. Colunas novas em `legal_demands` para as camadas de SLA (prazo legal,
   prazo fatal, prazo interno, SLA do cliente, prazo do playbook, margem
   de segurança) e o cache do prazo efetivo calculado.

RLS direta por workspace_id nas três tabelas novas, mesmo padrão de
`legal_exceptions`/`migration_jobs` (ver app/db/rls.py::
LEGAL_CONTROLADORIA_DIRECT_TENANT_TABLES). `legal_demands` já tem RLS —
as colunas novas não mudam a política.

Revision ID: c2d4f6a8e0b3
Revises: d3f7a9c1b2e4
Create Date: 2026-07-27 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "c2d4f6a8e0b3"
down_revision: str | Sequence[str] | None = "d3f7a9c1b2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"

DISTRIBUTION_RULES_TABLE = "distribution_rules"
DISTRIBUTION_DECISIONS_TABLE = "distribution_decisions"
SLA_ESCALATION_LOGS_TABLE = "sla_escalation_logs"
DIRECT_TENANT_TABLES = (
    DISTRIBUTION_RULES_TABLE,
    DISTRIBUTION_DECISIONS_TABLE,
    SLA_ESCALATION_LOGS_TABLE,
)

_DISTRIBUTION_STRATEGY_VALUES = ("round_robin", "least_load", "seniority", "fixed")
_DISTRIBUTION_TRIGGER_VALUES = (
    "regra_automatica",
    "redistribuicao_manual",
    "ausencia_substituicao",
    "rebalanceamento_capacidade",
)
_SLA_ESCALATION_LEVEL_VALUES = ("d7", "d3", "d1", "d0", "atrasado")


def _dt_column(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=True)


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
    # ── distribution_rules ──────────────────────────────────────────────
    op.create_table(
        DISTRIBUTION_RULES_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("match_natures", FlexibleJSON(), nullable=False, server_default="[]"),
        sa.Column("match_areas", FlexibleJSON(), nullable=False, server_default="[]"),
        sa.Column("match_client_ids", FlexibleJSON(), nullable=False, server_default="[]"),
        sa.Column("match_risks", FlexibleJSON(), nullable=False, server_default="[]"),
        sa.Column("match_units", FlexibleJSON(), nullable=False, server_default="[]"),
        sa.Column("match_teams", FlexibleJSON(), nullable=False, server_default="[]"),
        sa.Column(
            "strategy",
            sa.Enum(
                *_DISTRIBUTION_STRATEGY_VALUES,
                name="distribution_strategy_enum",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="round_robin",
        ),
        sa.Column("candidate_user_ids", FlexibleJSON(), nullable=False, server_default="[]"),
        sa.Column("max_active_per_candidate", sa.Integer(), nullable=True),
        sa.Column("respect_availability", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "name", name="uq_distribution_rule_workspace_name"
        ),
    )
    op.create_index(
        "ix_distribution_rule_workspace_id", DISTRIBUTION_RULES_TABLE, ["workspace_id"]
    )
    op.create_index("ix_distribution_rule_active", DISTRIBUTION_RULES_TABLE, ["active"])

    # ── distribution_decisions ──────────────────────────────────────────
    op.create_table(
        DISTRIBUTION_DECISIONS_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("legal_demand_id", FlexibleUUID(), nullable=False),
        sa.Column("rule_id", FlexibleUUID(), nullable=True),
        sa.Column(
            "trigger",
            sa.Enum(
                *_DISTRIBUTION_TRIGGER_VALUES,
                name="distribution_trigger_enum",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("previous_user_id", FlexibleUUID(), nullable=True),
        sa.Column("new_user_id", FlexibleUUID(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("decided_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column(
            "decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
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
        sa.ForeignKeyConstraint(
            ["rule_id"], ["distribution_rules.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["previous_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["new_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_distribution_decision_workspace_id", DISTRIBUTION_DECISIONS_TABLE, ["workspace_id"]
    )
    op.create_index(
        "ix_distribution_decision_legal_demand_id",
        DISTRIBUTION_DECISIONS_TABLE,
        ["legal_demand_id"],
    )
    op.create_index(
        "ix_distribution_decision_trigger", DISTRIBUTION_DECISIONS_TABLE, ["trigger"]
    )

    # ── sla_escalation_logs ──────────────────────────────────────────────
    op.create_table(
        SLA_ESCALATION_LOGS_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("legal_demand_id", FlexibleUUID(), nullable=False),
        sa.Column(
            "level",
            sa.Enum(
                *_SLA_ESCALATION_LEVEL_VALUES,
                name="sla_escalation_level_enum",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("layer", sa.String(length=40), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("escalated_to_user_id", FlexibleUUID(), nullable=True),
        sa.Column("channel_results", FlexibleJSON(), nullable=False, server_default="{}"),
        sa.Column("acknowledged_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["escalated_to_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["acknowledged_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "legal_demand_id",
            "level",
            name="uq_sla_escalation_workspace_demand_level",
        ),
    )
    op.create_index(
        "ix_sla_escalation_log_workspace_id", SLA_ESCALATION_LOGS_TABLE, ["workspace_id"]
    )
    op.create_index(
        "ix_sla_escalation_log_legal_demand_id", SLA_ESCALATION_LOGS_TABLE, ["legal_demand_id"]
    )
    op.create_index("ix_sla_escalation_log_level", SLA_ESCALATION_LOGS_TABLE, ["level"])

    for table_name in DIRECT_TENANT_TABLES:
        _enable_rls(table_name)

    # ── SLA em camadas em legal_demands ─────────────────────────────────
    op.add_column("legal_demands", _dt_column("prazo_legal_at"))
    op.add_column("legal_demands", _dt_column("prazo_fatal_at"))
    op.add_column("legal_demands", _dt_column("prazo_interno_at"))
    op.add_column("legal_demands", _dt_column("sla_cliente_at"))
    op.add_column("legal_demands", _dt_column("prazo_playbook_at"))
    op.add_column(
        "legal_demands", sa.Column("margem_seguranca_dias", sa.Integer(), nullable=True)
    )
    op.add_column("legal_demands", _dt_column("sla_effective_at"))
    op.add_column(
        "legal_demands", sa.Column("sla_effective_layer", sa.String(length=40), nullable=True)
    )
    op.add_column(
        "legal_demands", sa.Column("sla_escalation_level", sa.String(length=20), nullable=True)
    )
    op.create_index(
        "ix_legal_demand_sla_effective_at", "legal_demands", ["sla_effective_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_legal_demand_sla_effective_at", table_name="legal_demands")
    op.drop_column("legal_demands", "sla_escalation_level")
    op.drop_column("legal_demands", "sla_effective_layer")
    op.drop_column("legal_demands", "sla_effective_at")
    op.drop_column("legal_demands", "margem_seguranca_dias")
    op.drop_column("legal_demands", "prazo_playbook_at")
    op.drop_column("legal_demands", "sla_cliente_at")
    op.drop_column("legal_demands", "prazo_interno_at")
    op.drop_column("legal_demands", "prazo_fatal_at")
    op.drop_column("legal_demands", "prazo_legal_at")

    for table_name in reversed(DIRECT_TENANT_TABLES):
        _disable_rls(table_name)

    op.drop_index("ix_sla_escalation_log_level", table_name=SLA_ESCALATION_LOGS_TABLE)
    op.drop_index(
        "ix_sla_escalation_log_legal_demand_id", table_name=SLA_ESCALATION_LOGS_TABLE
    )
    op.drop_index("ix_sla_escalation_log_workspace_id", table_name=SLA_ESCALATION_LOGS_TABLE)
    op.drop_table(SLA_ESCALATION_LOGS_TABLE)

    op.drop_index(
        "ix_distribution_decision_trigger", table_name=DISTRIBUTION_DECISIONS_TABLE
    )
    op.drop_index(
        "ix_distribution_decision_legal_demand_id", table_name=DISTRIBUTION_DECISIONS_TABLE
    )
    op.drop_index(
        "ix_distribution_decision_workspace_id", table_name=DISTRIBUTION_DECISIONS_TABLE
    )
    op.drop_table(DISTRIBUTION_DECISIONS_TABLE)

    op.drop_index("ix_distribution_rule_active", table_name=DISTRIBUTION_RULES_TABLE)
    op.drop_index("ix_distribution_rule_workspace_id", table_name=DISTRIBUTION_RULES_TABLE)
    op.drop_table(DISTRIBUTION_RULES_TABLE)
    # Enums locais nascem com native_enum=False (CHECK constraint por
    # coluna, não CREATE TYPE) — dropar a tabela já basta, sem DROP TYPE
    # residual (mesmo racional de 3040c77e8efa).
