"""add legal_exceptions (Onda 2 — Central de Exceções, Plano Mestre §6/§3)

Generaliza a fila de aprovações (`approval_queue_items`) para TODOS os
tipos de exceção humana nomeados no plano: publicação sem vínculo
confiável, homônimo, prazo ambíguo, conflito de calendário, alteração de
tese, precedente não verificado, documento indispensável ausente,
procuração inconsistente, custas não comprovadas, versão modificada após
aprovação, ação externa não autorizada.

Tabela nova (não reaproveita `approval_queue_items`) porque `matter_id` é
opcional aqui — várias exceções nascem sem vínculo confiável a nenhum
caso, então a tabela carrega `workspace_id` próprio (RLS direta), no
mesmo padrão de `FinanceEntry`/`monitored_processes`.

Idempotência: `(workspace_id, exception_kind, origin, dedupe_key)` único
enquanto `status='aberta'` — reingestão do mesmo evento pelo mesmo
produtor nunca duplica (mesmo desenho de `uq_approval_pending_intent`).

Revision ID: 3040c77e8efa
Revises: 838f9f719531
Create Date: 2026-07-26 13:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "3040c77e8efa"
down_revision: str | Sequence[str] | None = "838f9f719531"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"
TABLE_NAME = "legal_exceptions"

_EXCEPTION_KIND_VALUES = (
    "publicacao_sem_vinculo_confiavel",
    "homonimo",
    "prazo_ambiguo",
    "conflito_calendario",
    "alteracao_tese",
    "precedente_nao_verificado",
    "documento_indispensavel_ausente",
    "procuracao_inconsistente",
    "custas_nao_comprovadas",
    "versao_modificada_apos_aprovacao",
    "acao_externa_nao_autorizada",
)

_PRIORITY_VALUES = ("alta", "media", "baixa")
_STATUS_VALUES = ("aberta", "resolvida", "escalada")


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
        sa.Column("matter_id", FlexibleUUID(), nullable=True),
        sa.Column("opened_by", FlexibleUUID(), nullable=True),
        sa.Column("resolved_by", FlexibleUUID(), nullable=True),
        sa.Column("escalated_to", FlexibleUUID(), nullable=True),
        sa.Column(
            "exception_kind",
            sa.Enum(
                *_EXCEPTION_KIND_VALUES,
                name="exception_kind_enum",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("origin", sa.String(length=150), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("payload", FlexibleJSON(), nullable=True),
        sa.Column(
            "priority",
            sa.Enum(
                *_PRIORITY_VALUES,
                name="exception_priority_enum",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="media",
        ),
        sa.Column(
            "status",
            sa.Enum(
                *_STATUS_VALUES,
                name="exception_center_status_enum",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="aberta",
        ),
        sa.Column("resolution_reason", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["opened_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["escalated_to"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_legal_exception_workspace_id", TABLE_NAME, ["workspace_id"])
    op.create_index("ix_legal_exception_matter_id", TABLE_NAME, ["matter_id"])
    op.create_index("ix_legal_exception_exception_kind", TABLE_NAME, ["exception_kind"])
    op.create_index("ix_legal_exception_priority", TABLE_NAME, ["priority"])
    op.create_index("ix_legal_exception_status", TABLE_NAME, ["status"])
    op.create_index(
        "uq_legal_exception_open_intent",
        TABLE_NAME,
        ["workspace_id", "exception_kind", "origin", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text("status = 'aberta'"),
        sqlite_where=sa.text("status = 'aberta'"),
    )

    _enable_rls()


def downgrade() -> None:
    _disable_rls()

    op.drop_index("uq_legal_exception_open_intent", table_name=TABLE_NAME)
    op.drop_index("ix_legal_exception_status", table_name=TABLE_NAME)
    op.drop_index("ix_legal_exception_priority", table_name=TABLE_NAME)
    op.drop_index("ix_legal_exception_exception_kind", table_name=TABLE_NAME)
    op.drop_index("ix_legal_exception_matter_id", table_name=TABLE_NAME)
    op.drop_index("ix_legal_exception_workspace_id", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
    # Enums locais nascem com native_enum=False (CHECK constraint por
    # coluna, não CREATE TYPE) — dropar a tabela já basta, sem DROP TYPE
    # residual (mesmo racional de b6d8f0a2c415).
