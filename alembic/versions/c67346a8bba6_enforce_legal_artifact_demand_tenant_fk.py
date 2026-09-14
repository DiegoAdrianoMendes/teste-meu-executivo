"""Enforce tenant-bound legal_demand_id FK on klm_legal_artifact_versions.

Achado do gate `native_architecture` (critério `tenant_reference_integrity`,
ver `scripts/verify_native_architecture_superiority.py`):
`klm_legal_artifact_versions.legal_demand_id` era uma FK SIMPLES para
`legal_demands.id`, sem escopo de workspace — um artefato do workspace A
podia referenciar uma demanda do workspace B, quebrando a isolação de
tenant no nível de schema (mesma classe de risco que a evidência corrigida
em `d4f6a8c0e215_enforce_tenant_evidence_integrity.py`). A própria tabela
já fazia o vínculo certo com `matter_id`
(`fk_klm_legal_artifact_workspace_matter`, FK composta contra
`[matters.workspace_id, matters.id]`) — esta migração espelha exatamente o
mesmo padrão para `legal_demand_id`:

  1. cria `uq_legal_demands_workspace_id` em `legal_demands` — âncora
     composta necessária para qualquer FK `[workspace_id, id]` apontar
     para ela (mesmo papel de `uq_klm_legal_artifact_version_workspace_id`
     em `klm_legal_artifact_versions`, criada em `d4f6a8c0e215`);
  2. dropa a FK simples antiga `fk_klm_legal_artifact_legal_demand`
     (`legal_demand_id -> legal_demands.id`, criada em
     `d0f2a4c6b813_add_legal_editor_provenance_columns.py`);
  3. cria a FK composta `fk_klm_legal_artifact_workspace_legal_demand`
     (`[workspace_id, legal_demand_id] -> [legal_demands.workspace_id,
     legal_demands.id]`, `ondelete=SET NULL`, mesmo `ondelete` da FK
     simples que substitui).

Fail-closed: antes de alterar o schema, `_require_tenant_consistency`
varre `klm_legal_artifact_versions` por linhas cujo `legal_demand_id` já
aponte pra uma `LegalDemand` de outro workspace (ou órfã) — se houver
qualquer violação, a migração aborta em vez de mascarar o problema criando
uma constraint que a base de dados de produção não conseguiria satisfazer.

Revision ID: c67346a8bba6
Revises: 46cd1d7279bf
Create Date: 2026-07-27 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c67346a8bba6"
down_revision: str | Sequence[str] | None = "46cd1d7279bf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ARTIFACT_TABLE = "klm_legal_artifact_versions"
DEMAND_TABLE = "legal_demands"

DEMAND_WORKSPACE_UNIQUE = "uq_legal_demands_workspace_id"
LEGACY_DEMAND_FK = "fk_klm_legal_artifact_legal_demand"
TENANT_DEMAND_FK = "fk_klm_legal_artifact_workspace_legal_demand"

SQLITE_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def _require_tenant_consistency() -> None:
    bind = op.get_bind()
    violations = int(
        bind.execute(
            sa.text(
                f"""
                SELECT COUNT(*)
                FROM "{ARTIFACT_TABLE}" AS artifact
                LEFT JOIN "{DEMAND_TABLE}" AS demand
                  ON demand.id = artifact.legal_demand_id
                WHERE artifact.legal_demand_id IS NOT NULL
                  AND (demand.id IS NULL OR artifact.workspace_id <> demand.workspace_id)
                """
            )
        ).scalar_one()
    )
    if violations:
        raise RuntimeError(
            "Legal artifact tenant FK migration blocked by cross-workspace or "
            f"orphan legal_demand_id references: {violations}"
        )


def upgrade() -> None:
    _require_tenant_consistency()

    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            DEMAND_TABLE,
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.create_unique_constraint(
                DEMAND_WORKSPACE_UNIQUE, ["workspace_id", "id"]
            )
        with op.batch_alter_table(
            ARTIFACT_TABLE,
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.drop_constraint(LEGACY_DEMAND_FK, type_="foreignkey")
            batch_op.create_foreign_key(
                TENANT_DEMAND_FK,
                DEMAND_TABLE,
                ["workspace_id", "legal_demand_id"],
                ["workspace_id", "id"],
                ondelete="SET NULL",
            )
        return

    op.create_unique_constraint(
        DEMAND_WORKSPACE_UNIQUE, DEMAND_TABLE, ["workspace_id", "id"]
    )
    op.drop_constraint(LEGACY_DEMAND_FK, ARTIFACT_TABLE, type_="foreignkey")
    op.create_foreign_key(
        TENANT_DEMAND_FK,
        ARTIFACT_TABLE,
        DEMAND_TABLE,
        ["workspace_id", "legal_demand_id"],
        ["workspace_id", "id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            ARTIFACT_TABLE,
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.drop_constraint(TENANT_DEMAND_FK, type_="foreignkey")
            batch_op.create_foreign_key(
                LEGACY_DEMAND_FK,
                DEMAND_TABLE,
                ["legal_demand_id"],
                ["id"],
                ondelete="SET NULL",
            )
        with op.batch_alter_table(
            DEMAND_TABLE,
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.drop_constraint(DEMAND_WORKSPACE_UNIQUE, type_="unique")
        return

    op.drop_constraint(TENANT_DEMAND_FK, ARTIFACT_TABLE, type_="foreignkey")
    op.create_foreign_key(
        LEGACY_DEMAND_FK,
        ARTIFACT_TABLE,
        DEMAND_TABLE,
        ["legal_demand_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint(DEMAND_WORKSPACE_UNIQUE, DEMAND_TABLE, type_="unique")
