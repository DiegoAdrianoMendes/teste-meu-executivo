"""Editor jurídico — proveniência/consulta em klm_legal_artifact_versions (Onda 6, §9 item 1).

Estende `klm_legal_artifact_versions` (não recria a tabela, não cria tabela
nova) com as colunas relacionais que faltavam para o editor jurídico gerar
a partir do modelo timbrado do escritório e vincular à `LegalDemand` (Caso
360), em vez do `matter_id` cru:

  * `legal_demand_id` — a demanda jurídica (Caso 360) de origem, quando
    existir (nullable: nem toda LegalDemand tem Matter/artefato ainda).
  * `author_type`/`author_agent_key` — autor humano OU agente (mesmo padrão
    de `OmnichannelMessage.author_type`, ver app/models/klm_operations.py);
    CHECK garante `author_agent_key` preenchido quando `author_type='agent'`.
  * `office_template_key`/`office_template_object_id`/
    `office_template_object_version` — qual `PlaybookOfficeTemplate` (Onda
    12) foi usado como base timbrada, com o mesmo padrão de pin de versão
    já usado em `PlaybookOfficeTemplate`/`PlaybookDocumentTemplate`
    (`document_object_id`+`document_object_version` sempre juntos).
  * `protocolability_state` — resumo do checklist de protocolabilidade
    (`content["checklist"]`, calculado por
    `app/services/legal_editor_service.py`); começa sempre em `'bloqueado'`
    e SÓ chega a `'protocolavel'` quando o checklist não encontra nenhuma
    pendência — a geração nunca protocola por conta própria.

O conteúdo estruturado por seções, as variáveis do Caso 360 usadas para
preencher o modelo, fontes, citações e comentários continuam dentro de
`content` (JSON), que já é coberto pelo `evidence_sha256` (schema v1,
`canonical_legal_artifact_evidence`); esta migração não toca o contrato do
hash — as colunas novas são metadado de proveniência para filtro/consulta,
não entram na assinatura criptográfica.

RLS de `klm_legal_artifact_versions` já existe (`workspace_id` direto,
Onda 2) e cobre as colunas novas sem trabalho extra.

Revision ID: d0f2a4c6b813
Revises: e7a9c1d3f5b7
Create Date: 2026-07-27 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision: str = "d0f2a4c6b813"
down_revision: str | Sequence[str] | None = "e7a9c1d3f5b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "klm_legal_artifact_versions"

AUTHOR_TYPE_CHECK = "ck_klm_legal_artifact_author_type_values"
AUTHOR_AGENT_KEY_PAIR_CHECK = "ck_klm_legal_artifact_author_agent_key_pair"
OFFICE_TEMPLATE_PIN_PAIR_CHECK = "ck_klm_legal_artifact_office_template_pin_pair"
PROTOCOLABILITY_STATE_CHECK = "ck_klm_legal_artifact_protocolability_state_values"
LEGAL_DEMAND_FK = "fk_klm_legal_artifact_legal_demand"


def upgrade() -> None:
    with op.batch_alter_table(TABLE) as batch:
        batch.add_column(sa.Column("legal_demand_id", FlexibleUUID(), nullable=True))
        batch.add_column(
            sa.Column(
                "author_type",
                sa.String(length=20),
                nullable=False,
                server_default="human",
            )
        )
        batch.add_column(sa.Column("author_agent_key", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("office_template_key", sa.String(length=60), nullable=True))
        batch.add_column(sa.Column("office_template_object_id", FlexibleUUID(), nullable=True))
        batch.add_column(
            sa.Column("office_template_object_version", sa.Integer(), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "protocolability_state",
                sa.String(length=20),
                nullable=False,
                server_default="bloqueado",
            )
        )
        batch.create_foreign_key(
            LEGAL_DEMAND_FK,
            "legal_demands",
            ["legal_demand_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_check_constraint(
            AUTHOR_TYPE_CHECK, "author_type IN ('human','agent')"
        )
        batch.create_check_constraint(
            AUTHOR_AGENT_KEY_PAIR_CHECK,
            "author_type <> 'agent' OR author_agent_key IS NOT NULL",
        )
        batch.create_check_constraint(
            OFFICE_TEMPLATE_PIN_PAIR_CHECK,
            "(office_template_object_id IS NULL) = (office_template_object_version IS NULL)",
        )
        batch.create_check_constraint(
            PROTOCOLABILITY_STATE_CHECK,
            "protocolability_state IN ('bloqueado','pendente','protocolavel')",
        )

    op.create_index(
        "ix_klm_legal_artifact_version_legal_demand_id",
        TABLE,
        ["legal_demand_id"],
    )
    op.create_index(
        "ix_klm_legal_artifact_version_protocolability_state",
        TABLE,
        ["workspace_id", "protocolability_state"],
    )


def downgrade() -> None:
    op.drop_index("ix_klm_legal_artifact_version_protocolability_state", table_name=TABLE)
    op.drop_index("ix_klm_legal_artifact_version_legal_demand_id", table_name=TABLE)

    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint(PROTOCOLABILITY_STATE_CHECK, type_="check")
        batch.drop_constraint(OFFICE_TEMPLATE_PIN_PAIR_CHECK, type_="check")
        batch.drop_constraint(AUTHOR_AGENT_KEY_PAIR_CHECK, type_="check")
        batch.drop_constraint(AUTHOR_TYPE_CHECK, type_="check")
        batch.drop_constraint(LEGAL_DEMAND_FK, type_="foreignkey")
        batch.drop_column("protocolability_state")
        batch.drop_column("office_template_object_version")
        batch.drop_column("office_template_object_id")
        batch.drop_column("office_template_key")
        batch.drop_column("author_agent_key")
        batch.drop_column("author_type")
        batch.drop_column("legal_demand_id")
