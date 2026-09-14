"""add legal batch operation (Onda 8, Plano Mestre §7)

Duas tabelas novas para a Operação Massificada / Lotes:

  * `legal_batches` — o lote agrupado por um CRITÉRIO declarado (cliente/
    carteira/tribunal/matéria/tipo publicação/fase/tese/responsável/prazo/
    playbook, ver LegalBatchCriterion). `status` é sempre um CACHE
    recomputado a partir da contagem dos itens (LegalBatchService), nunca
    a fonte de verdade — a fonte é sempre `legal_batch_items.state`;
  * `legal_batch_items` — cada item do lote com ESTADO PRÓPRIO
    (pendente/classificado/vinculado/rascunho/revisado/bloqueado/
    baixa_confianca/divergente/aguardando_humano/concluido). O lote NUNCA
    esconde falha parcial: um item bloqueado não impede os demais de
    avançar, e o painel do lote sempre conta itens reais.

`decision_kind` classifica a natureza da decisão do item — os quatro
valores em PROTECTED_DECISION_KINDS (tese/prazo_fatal/texto_final/
protocolo) nunca são aprováveis via "aprovar tudo"
(LegalBatchService.approve_all sempre pula esses; só approve_item, item a
item, sempre humano).

`legal_demand_id` é o ponteiro opcional para a LegalDemand (Onda 2) que o
item classifica/enriquece — reusa Caso 360/jornada em vez de duplicar.

RLS direta por workspace_id, mesmo padrão de
`legal_citation_verifications`/`protocol_folders`
(ver app/db/rls.py::LEGAL_BATCH_DIRECT_TENANT_TABLES).

Revision ID: 46cd1d7279bf
Revises: b6b46ea00c28
Create Date: 2026-07-27 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "46cd1d7279bf"
down_revision: str | Sequence[str] | None = "b6b46ea00c28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"

LEGAL_BATCHES_TABLE = "legal_batches"
LEGAL_BATCH_ITEMS_TABLE = "legal_batch_items"
DIRECT_TENANT_TABLES = (LEGAL_BATCHES_TABLE, LEGAL_BATCH_ITEMS_TABLE)

_CRITERION_VALUES = (
    "cliente",
    "carteira",
    "tribunal",
    "materia",
    "tipo_publicacao",
    "fase",
    "tese",
    "responsavel",
    "prazo",
    "playbook",
)
_ACTION_VALUES = (
    "classificar",
    "agrupar",
    "vincular",
    "sugerir_tarefa",
    "preencher_variavel",
    "gerar_rascunho",
    "revisar",
    "pedir_documento",
    "organizar",
)
_DECISION_KIND_VALUES = (
    "classificacao",
    "agrupamento",
    "vinculo",
    "tarefa_sugerida",
    "variavel_preenchida",
    "rascunho",
    "revisao",
    "documento_solicitado",
    "organizacao",
    "tese",
    "prazo_fatal",
    "texto_final",
    "protocolo",
)


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
        LEGAL_BATCHES_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("external_ref", sa.String(length=160), nullable=True),
        sa.Column(
            "criterion_type",
            sa.Enum(*_CRITERION_VALUES, name="legal_batch_criterion_enum"),
            nullable=False,
        ),
        sa.Column("criterion_value", FlexibleJSON(), nullable=False, server_default="{}"),
        sa.Column("playbook_ref", sa.String(length=160), nullable=True),
        sa.Column(
            "default_action",
            sa.Enum(*_ACTION_VALUES, name="legal_batch_action_enum"),
            nullable=True,
        ),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="aberto"
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
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('aberto','processando','com_pendencias','concluido')",
            name="ck_legal_batch_status_values",
        ),
    )
    op.create_index("ix_legal_batches_workspace_id", LEGAL_BATCHES_TABLE, ["workspace_id"])
    op.create_index("ix_legal_batches_external_ref", LEGAL_BATCHES_TABLE, ["external_ref"])
    op.create_index("ix_legal_batches_criterion_type", LEGAL_BATCHES_TABLE, ["criterion_type"])
    op.create_index("ix_legal_batches_status", LEGAL_BATCHES_TABLE, ["status"])

    op.create_table(
        LEGAL_BATCH_ITEMS_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("batch_id", FlexibleUUID(), nullable=False),
        sa.Column("item_ref", sa.String(length=200), nullable=True),
        sa.Column("legal_demand_id", FlexibleUUID(), nullable=True),
        sa.Column(
            "action",
            sa.Enum(*_ACTION_VALUES, name="legal_batch_item_action_enum"),
            nullable=False,
        ),
        sa.Column(
            "decision_kind",
            sa.Enum(*_DECISION_KIND_VALUES, name="legal_batch_item_decision_kind_enum"),
            nullable=False,
        ),
        sa.Column(
            "state", sa.String(length=20), nullable=False, server_default="pendente"
        ),
        sa.Column("state_history", FlexibleJSON(), nullable=False, server_default="[]"),
        sa.Column("input_payload", FlexibleJSON(), nullable=False, server_default="{}"),
        sa.Column("result_payload", FlexibleJSON(), nullable=False, server_default="{}"),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("error_reason", sa.Text(), nullable=True),
        sa.Column("autonomy_level_applied", sa.String(length=4), nullable=True),
        sa.Column("autonomy_policy_id", FlexibleUUID(), nullable=True),
        sa.Column(
            "requires_human_approval",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("approved_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estimated_cost", sa.Numeric(10, 2), nullable=True),
        sa.Column("estimated_time_saved_minutes", sa.Integer(), nullable=True),
        sa.Column("responsible_user_id", FlexibleUUID(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["legal_batches.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["legal_demand_id"], ["legal_demands.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["autonomy_policy_id"], ["agent_autonomy_policies.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["responsible_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "state IN ('pendente','classificado','vinculado','rascunho','revisado',"
            "'bloqueado','baixa_confianca','divergente','aguardando_humano','concluido')",
            name="ck_legal_batch_item_state_values",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_legal_batch_item_confidence_range",
        ),
    )
    op.create_index(
        "ix_legal_batch_items_workspace_id", LEGAL_BATCH_ITEMS_TABLE, ["workspace_id"]
    )
    op.create_index("ix_legal_batch_items_batch_id", LEGAL_BATCH_ITEMS_TABLE, ["batch_id"])
    op.create_index("ix_legal_batch_items_item_ref", LEGAL_BATCH_ITEMS_TABLE, ["item_ref"])
    op.create_index(
        "ix_legal_batch_items_legal_demand_id", LEGAL_BATCH_ITEMS_TABLE, ["legal_demand_id"]
    )
    op.create_index("ix_legal_batch_items_state", LEGAL_BATCH_ITEMS_TABLE, ["state"])
    op.create_index(
        "ix_legal_batch_items_responsible_user_id",
        LEGAL_BATCH_ITEMS_TABLE,
        ["responsible_user_id"],
    )

    for table_name in DIRECT_TENANT_TABLES:
        _enable_rls(table_name)


def downgrade() -> None:
    for table_name in reversed(DIRECT_TENANT_TABLES):
        _disable_rls(table_name)

    op.drop_index(
        "ix_legal_batch_items_responsible_user_id", table_name=LEGAL_BATCH_ITEMS_TABLE
    )
    op.drop_index("ix_legal_batch_items_state", table_name=LEGAL_BATCH_ITEMS_TABLE)
    op.drop_index(
        "ix_legal_batch_items_legal_demand_id", table_name=LEGAL_BATCH_ITEMS_TABLE
    )
    op.drop_index("ix_legal_batch_items_item_ref", table_name=LEGAL_BATCH_ITEMS_TABLE)
    op.drop_index("ix_legal_batch_items_batch_id", table_name=LEGAL_BATCH_ITEMS_TABLE)
    op.drop_index("ix_legal_batch_items_workspace_id", table_name=LEGAL_BATCH_ITEMS_TABLE)
    op.drop_table(LEGAL_BATCH_ITEMS_TABLE)

    op.drop_index("ix_legal_batches_status", table_name=LEGAL_BATCHES_TABLE)
    op.drop_index("ix_legal_batches_criterion_type", table_name=LEGAL_BATCHES_TABLE)
    op.drop_index("ix_legal_batches_external_ref", table_name=LEGAL_BATCHES_TABLE)
    op.drop_index("ix_legal_batches_workspace_id", table_name=LEGAL_BATCHES_TABLE)
    op.drop_table(LEGAL_BATCHES_TABLE)

    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS legal_batch_item_decision_kind_enum")
        op.execute("DROP TYPE IF EXISTS legal_batch_item_action_enum")
        op.execute("DROP TYPE IF EXISTS legal_batch_action_enum")
        op.execute("DROP TYPE IF EXISTS legal_batch_criterion_enum")
