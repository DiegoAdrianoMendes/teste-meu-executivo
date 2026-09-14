"""add agent_autonomy_policies (Plano Mestre §14 — autonomia A0-A4 por evento)

`agent_autonomy_policies` guarda a regra fina de autonomia POR EVENTO —
evento, condições, área, cliente/carteira, agente, ação, nível A0-A4,
capability, risco, confiança mínima, orçamento, prazo, idempotency key,
rollback, aprovação e escalonamento. Distinta do modo HIL simples já
existente (hil_total/hil_marcos/autonomo, guardado em `config_documents`
namespace `agent.autonomy_settings`): o modo HIL é o default do agente,
esta tabela é a política que decide o nível A0-A4 e o que é permitido para
um evento concreto. RLS direta por workspace_id, mesmo padrão de
`contract_billing_proposals` e `klm_knowledge_article_feedback`.

Revision ID: 838f9f719531
Revises: 013d26e9d488
Create Date: 2026-07-26 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "838f9f719531"
down_revision: str | Sequence[str] | None = "013d26e9d488"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"
TABLE_NAME = "agent_autonomy_policies"


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    op.execute(f'ALTER TABLE "{TABLE_NAME}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{TABLE_NAME}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'''CREATE POLICY "{RLS_POLICY}" ON "{TABLE_NAME}"
        AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({predicate}) WITH CHECK ({predicate})'''
    )


def _disable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f'DROP POLICY IF EXISTS "{RLS_POLICY}" ON "{TABLE_NAME}"')
    op.execute(f'ALTER TABLE "{TABLE_NAME}" NO FORCE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{TABLE_NAME}" DISABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("conditions", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("area_key", sa.String(80), nullable=True),
        sa.Column("client_id", FlexibleUUID(), nullable=True),
        sa.Column("portfolio_key", sa.String(120), nullable=True),
        sa.Column("agent_key", sa.String(80), nullable=True),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column(
            "autonomy_level",
            sa.Enum("A0", "A1", "A2", "A3", "A4", name="agent_autonomy_policy_level_enum"),
            nullable=False,
        ),
        sa.Column("capability_key", sa.String(120), nullable=True),
        sa.Column(
            "risk_level",
            sa.Enum("baixo", "medio", "alto", name="agent_autonomy_policy_risk_enum"),
            nullable=False,
            server_default="medio",
        ),
        sa.Column("min_confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("budget_limit", sa.Numeric(14, 2), nullable=True),
        sa.Column("budget_currency", sa.String(3), nullable=False, server_default="BRL"),
        sa.Column("sla_minutes", sa.Integer(), nullable=True),
        sa.Column("idempotency_key_template", sa.String(200), nullable=True),
        sa.Column("rollback_strategy", sa.Text(), nullable=True),
        sa.Column("approval_required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("escalation_target_agent_key", sa.String(80), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "min_confidence IS NULL OR (min_confidence >= 0 AND min_confidence <= 1)",
            name="ck_agent_autonomy_policy_min_confidence_range",
        ),
        sa.CheckConstraint(
            "budget_limit IS NULL OR budget_limit >= 0",
            name="ck_agent_autonomy_policy_budget_nonnegative",
        ),
        sa.CheckConstraint(
            "sla_minutes IS NULL OR sla_minutes > 0",
            name="ck_agent_autonomy_policy_sla_positive",
        ),
    )
    op.create_index(f"ix_{TABLE_NAME}_workspace_id", TABLE_NAME, ["workspace_id"])
    op.create_index(f"ix_{TABLE_NAME}_event_type", TABLE_NAME, ["event_type"])
    op.create_index(f"ix_{TABLE_NAME}_area_key", TABLE_NAME, ["area_key"])
    op.create_index(f"ix_{TABLE_NAME}_client_id", TABLE_NAME, ["client_id"])
    op.create_index(f"ix_{TABLE_NAME}_agent_key", TABLE_NAME, ["agent_key"])
    op.create_index(f"ix_{TABLE_NAME}_action", TABLE_NAME, ["action"])
    op.create_index(f"ix_{TABLE_NAME}_autonomy_level", TABLE_NAME, ["autonomy_level"])
    op.create_index(f"ix_{TABLE_NAME}_is_active", TABLE_NAME, ["is_active"])
    op.create_index(
        "ix_agent_autonomy_policies_workspace_event",
        TABLE_NAME,
        ["workspace_id", "event_type", "is_active"],
    )

    _enable_rls()


def downgrade() -> None:
    _disable_rls()

    op.drop_index("ix_agent_autonomy_policies_workspace_event", table_name=TABLE_NAME)
    op.drop_index(f"ix_{TABLE_NAME}_is_active", table_name=TABLE_NAME)
    op.drop_index(f"ix_{TABLE_NAME}_autonomy_level", table_name=TABLE_NAME)
    op.drop_index(f"ix_{TABLE_NAME}_action", table_name=TABLE_NAME)
    op.drop_index(f"ix_{TABLE_NAME}_agent_key", table_name=TABLE_NAME)
    op.drop_index(f"ix_{TABLE_NAME}_client_id", table_name=TABLE_NAME)
    op.drop_index(f"ix_{TABLE_NAME}_area_key", table_name=TABLE_NAME)
    op.drop_index(f"ix_{TABLE_NAME}_event_type", table_name=TABLE_NAME)
    op.drop_index(f"ix_{TABLE_NAME}_workspace_id", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)

    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS agent_autonomy_policy_risk_enum")
        op.execute("DROP TYPE IF EXISTS agent_autonomy_policy_level_enum")
