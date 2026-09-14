"""add protocol folder + manifesto (Onda 7, Plano Mestre §10)

Uma tabela nova, `protocol_folders`, ligada a `legal_demands` (Onda 2),
`matters` (fundação) e `klm_legal_artifact_versions` (Onda 2/5/6):

  * projeção MUTÁVEL (mesmo espírito de `klm_legal_artifact_states`,
    NUNCA `register_append_only_model`) — cada nova montagem/validação da
    pasta de protocolo de uma mesma demanda+versão de artefato ATUALIZA a
    mesma linha (`manifest`/`files`/`state`), o que é o que permite
    detectar "hash de arquivo alterado após aprovação" comparando o hash
    recém-calculado contra o hash já persistido de uma montagem anterior
    (ver app/services/protocol_folder_service.py);
  * `state` fecha no mesmo vocabulário fail-closed de
    `klm_legal_artifact_versions.protocolability_state`
    (bloqueado/pendente/protocolavel) — nunca existe um quarto estado
    'pronto' fora desse vocabulário;
  * o registro do recibo (`receipt_registered_at`/`..._by_user_id`/
    `receipt_reference`/`receipt_note`) é sempre POSTERIOR e
    exclusivamente humano — não existe, em nenhuma rota da API, um verbo
    que "protocole"; este par de colunas só é preenchido depois do
    protocolo real ter acontecido fora da plataforma.

RLS direta por workspace_id, mesmo padrão de
`legal_citation_verifications`/`legal_review_findings`/`document_assets`
(ver app/db/rls.py::PROTOCOL_FOLDER_DIRECT_TENANT_TABLES).

Revision ID: b6b46ea00c28
Revises: 4ce67f6149cd
Create Date: 2026-07-27 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "b6b46ea00c28"
down_revision: str | Sequence[str] | None = "4ce67f6149cd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"

PROTOCOL_FOLDERS_TABLE = "protocol_folders"
DIRECT_TENANT_TABLES = (PROTOCOL_FOLDERS_TABLE,)


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
        PROTOCOL_FOLDERS_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("legal_demand_id", FlexibleUUID(), nullable=False),
        sa.Column("matter_id", FlexibleUUID(), nullable=False),
        sa.Column("artifact_version_id", FlexibleUUID(), nullable=False),
        sa.Column("tribunal", sa.String(length=160), nullable=False),
        sa.Column("act_type", sa.String(length=40), nullable=False),
        sa.Column(
            "state",
            sa.String(length=20),
            nullable=False,
            server_default="bloqueado",
        ),
        sa.Column("manifest", FlexibleJSON(), nullable=False, server_default="{}"),
        sa.Column("files", FlexibleJSON(), nullable=False, server_default="[]"),
        sa.Column(
            "signature_confirmed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("built_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("receipt_registered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("receipt_registered_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("receipt_reference", sa.String(length=200), nullable=True),
        sa.Column("receipt_note", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["artifact_version_id"],
            ["klm_legal_artifact_versions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["built_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["receipt_registered_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "legal_demand_id",
            "artifact_version_id",
            name="uq_protocol_folder_demand_artifact",
        ),
        sa.CheckConstraint(
            "state IN ('bloqueado','pendente','protocolavel')",
            name="ck_protocol_folder_state_values",
        ),
        sa.CheckConstraint(
            "(receipt_registered_at IS NULL) = (receipt_registered_by_user_id IS NULL)",
            name="ck_protocol_folder_receipt_pair",
        ),
    )
    op.create_index(
        "ix_protocol_folder_workspace_id", PROTOCOL_FOLDERS_TABLE, ["workspace_id"]
    )
    op.create_index(
        "ix_protocol_folder_legal_demand_id", PROTOCOL_FOLDERS_TABLE, ["legal_demand_id"]
    )
    op.create_index(
        "ix_protocol_folder_matter_id", PROTOCOL_FOLDERS_TABLE, ["matter_id"]
    )
    op.create_index(
        "ix_protocol_folder_artifact_version_id",
        PROTOCOL_FOLDERS_TABLE,
        ["artifact_version_id"],
    )
    op.create_index("ix_protocol_folder_state", PROTOCOL_FOLDERS_TABLE, ["state"])

    for table_name in DIRECT_TENANT_TABLES:
        _enable_rls(table_name)


def downgrade() -> None:
    for table_name in reversed(DIRECT_TENANT_TABLES):
        _disable_rls(table_name)

    op.drop_index("ix_protocol_folder_state", table_name=PROTOCOL_FOLDERS_TABLE)
    op.drop_index(
        "ix_protocol_folder_artifact_version_id", table_name=PROTOCOL_FOLDERS_TABLE
    )
    op.drop_index("ix_protocol_folder_matter_id", table_name=PROTOCOL_FOLDERS_TABLE)
    op.drop_index("ix_protocol_folder_legal_demand_id", table_name=PROTOCOL_FOLDERS_TABLE)
    op.drop_index("ix_protocol_folder_workspace_id", table_name=PROTOCOL_FOLDERS_TABLE)
    op.drop_table(PROTOCOL_FOLDERS_TABLE)
