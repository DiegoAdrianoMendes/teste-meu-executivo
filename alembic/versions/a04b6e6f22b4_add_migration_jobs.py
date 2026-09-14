"""add migration_jobs, migration_staging_records, migration_divergences
(Onda 3 — Importador Universal de Migração, Plano Mestre §12)

Metade agnóstica da fábrica de migração: MigrationJob (o lote — hash +
manifesto de origem, nunca o binário bruto), MigrationStagingRecord (cada
linha isolada em staging, nunca escreve direto em produção até a
importação) e MigrationDivergence (fila de divergências que alimenta o
semáforo de corte vermelho/amarelo/verde). Os adapters dedicados
Astrea/ADVBOX/DataJuri ficam fora desta onda (ver
docs/ONDA3_ADAPTERS_BLOQUEADOS.md); `migration_source_system_enum` já
reserva os 3 valores.

Revision ID: a04b6e6f22b4
Revises: 3040c77e8efa
Create Date: 2026-07-27 03:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "a04b6e6f22b4"
down_revision: str | Sequence[str] | None = "3040c77e8efa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"

_JOBS_TABLE = "migration_jobs"
_STAGING_TABLE = "migration_staging_records"
_DIVERGENCES_TABLE = "migration_divergences"

_SOURCE_SYSTEM_VALUES = (
    "universal_csv",
    "universal_xlsx",
    "zip_docs",
    "json",
    "astrea",
    "advbox",
    "datajuri",
)
_JOB_STATUS_VALUES = (
    "recebido",
    "staging",
    "validado",
    "previa",
    "importando",
    "reconciliando",
    "concluido",
    "revertido",
    "erro",
)
_DOMAIN_VALUES = (
    "clientes",
    "contatos",
    "partes",
    "casos",
    "processos",
    "tarefas",
    "prazos",
    "documentos",
    "contratos",
    "honorarios",
    "despesas",
    "recebimentos",
    "tags",
)
_CONFLICT_VALUES = ("none", "duplicate", "ambiguous")
_RECORD_STATUS_VALUES = (
    "pendente",
    "valido",
    "invalido",
    "importado",
    "mesclado",
    "rejeitado",
)
_DIVERGENCE_SEVERITY_VALUES = ("critica", "atencao")


def _enable_rls(table_name: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
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
        _JOBS_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column(
            "source_system",
            sa.Enum(
                *_SOURCE_SYSTEM_VALUES,
                name="migration_source_system_enum",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                *_JOB_STATUS_VALUES,
                name="migration_job_status_enum",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="recebido",
        ),
        sa.Column("source_manifest", FlexibleJSON(), nullable=False),
        sa.Column("field_mapping", FlexibleJSON(), nullable=False),
        sa.Column("preview_summary", FlexibleJSON(), nullable=False),
        sa.Column("reconciliation_summary", FlexibleJSON(), nullable=False),
        sa.Column("semaphore", sa.String(length=10), nullable=True),
        sa.Column("semaphore_computed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cutover_decision", FlexibleJSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_by", FlexibleUUID(), nullable=True),
        sa.Column("created_by", FlexibleUUID(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rolled_back_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(f"ix_{_JOBS_TABLE}_workspace_id", _JOBS_TABLE, ["workspace_id"])
    op.create_index(f"ix_{_JOBS_TABLE}_source_system", _JOBS_TABLE, ["source_system"])
    op.create_index(f"ix_{_JOBS_TABLE}_status", _JOBS_TABLE, ["status"])
    _enable_rls(_JOBS_TABLE)

    op.create_table(
        _STAGING_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("job_id", FlexibleUUID(), nullable=False),
        sa.Column(
            "domain",
            sa.Enum(
                *_DOMAIN_VALUES,
                name="migration_domain_enum",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("raw", FlexibleJSON(), nullable=False),
        sa.Column("normalized", FlexibleJSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=300), nullable=False),
        sa.Column("weak_identity", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "conflict",
            sa.Enum(
                *_CONFLICT_VALUES,
                name="migration_conflict_enum",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="none",
        ),
        sa.Column(
            "record_status",
            sa.Enum(
                *_RECORD_STATUS_VALUES,
                name="migration_record_status_enum",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="pendente",
        ),
        sa.Column("validation_error", sa.Text(), nullable=True),
        sa.Column("target_id", FlexibleUUID(), nullable=True),
        sa.Column("target_table", sa.String(length=60), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], [f"{_JOBS_TABLE}.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "domain",
            "dedupe_key",
            name="uq_migration_staging_job_domain_dedupe",
        ),
    )
    op.create_index(
        "ix_migration_staging_workspace_job_domain",
        _STAGING_TABLE,
        ["workspace_id", "job_id", "domain"],
    )
    op.create_index(
        "ix_migration_staging_job_status", _STAGING_TABLE, ["job_id", "record_status"]
    )
    op.create_index(f"ix_{_STAGING_TABLE}_workspace_id", _STAGING_TABLE, ["workspace_id"])
    op.create_index(f"ix_{_STAGING_TABLE}_job_id", _STAGING_TABLE, ["job_id"])
    op.create_index(f"ix_{_STAGING_TABLE}_domain", _STAGING_TABLE, ["domain"])
    op.create_index(f"ix_{_STAGING_TABLE}_conflict", _STAGING_TABLE, ["conflict"])
    op.create_index(f"ix_{_STAGING_TABLE}_record_status", _STAGING_TABLE, ["record_status"])
    op.create_index(f"ix_{_STAGING_TABLE}_target_id", _STAGING_TABLE, ["target_id"])
    _enable_rls(_STAGING_TABLE)

    op.create_table(
        _DIVERGENCES_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("job_id", FlexibleUUID(), nullable=False),
        sa.Column(
            "domain",
            sa.Enum(
                *_DOMAIN_VALUES,
                name="migration_divergence_domain_enum",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("tipo", sa.String(length=120), nullable=False),
        sa.Column(
            "severity",
            sa.Enum(
                *_DIVERGENCE_SEVERITY_VALUES,
                name="migration_divergence_severity_enum",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="atencao",
        ),
        sa.Column("detalhe", sa.Text(), nullable=False),
        sa.Column("context", FlexibleJSON(), nullable=True),
        sa.Column("resolvido", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("resolved_by", FlexibleUUID(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], [f"{_JOBS_TABLE}.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_migration_divergences_workspace_job", _DIVERGENCES_TABLE, ["workspace_id", "job_id"]
    )
    op.create_index(
        "ix_migration_divergences_job_resolved", _DIVERGENCES_TABLE, ["job_id", "resolvido"]
    )
    op.create_index(f"ix_{_DIVERGENCES_TABLE}_workspace_id", _DIVERGENCES_TABLE, ["workspace_id"])
    op.create_index(f"ix_{_DIVERGENCES_TABLE}_job_id", _DIVERGENCES_TABLE, ["job_id"])
    op.create_index(f"ix_{_DIVERGENCES_TABLE}_tipo", _DIVERGENCES_TABLE, ["tipo"])
    op.create_index(f"ix_{_DIVERGENCES_TABLE}_severity", _DIVERGENCES_TABLE, ["severity"])
    op.create_index(f"ix_{_DIVERGENCES_TABLE}_resolvido", _DIVERGENCES_TABLE, ["resolvido"])
    _enable_rls(_DIVERGENCES_TABLE)


def downgrade() -> None:
    _disable_rls(_DIVERGENCES_TABLE)
    op.drop_index(f"ix_{_DIVERGENCES_TABLE}_resolvido", table_name=_DIVERGENCES_TABLE)
    op.drop_index(f"ix_{_DIVERGENCES_TABLE}_severity", table_name=_DIVERGENCES_TABLE)
    op.drop_index(f"ix_{_DIVERGENCES_TABLE}_tipo", table_name=_DIVERGENCES_TABLE)
    op.drop_index(f"ix_{_DIVERGENCES_TABLE}_job_id", table_name=_DIVERGENCES_TABLE)
    op.drop_index(f"ix_{_DIVERGENCES_TABLE}_workspace_id", table_name=_DIVERGENCES_TABLE)
    op.drop_index("ix_migration_divergences_job_resolved", table_name=_DIVERGENCES_TABLE)
    op.drop_index("ix_migration_divergences_workspace_job", table_name=_DIVERGENCES_TABLE)
    op.drop_table(_DIVERGENCES_TABLE)

    _disable_rls(_STAGING_TABLE)
    op.drop_index(f"ix_{_STAGING_TABLE}_target_id", table_name=_STAGING_TABLE)
    op.drop_index(f"ix_{_STAGING_TABLE}_record_status", table_name=_STAGING_TABLE)
    op.drop_index(f"ix_{_STAGING_TABLE}_conflict", table_name=_STAGING_TABLE)
    op.drop_index(f"ix_{_STAGING_TABLE}_domain", table_name=_STAGING_TABLE)
    op.drop_index(f"ix_{_STAGING_TABLE}_job_id", table_name=_STAGING_TABLE)
    op.drop_index(f"ix_{_STAGING_TABLE}_workspace_id", table_name=_STAGING_TABLE)
    op.drop_index("ix_migration_staging_job_status", table_name=_STAGING_TABLE)
    op.drop_index("ix_migration_staging_workspace_job_domain", table_name=_STAGING_TABLE)
    op.drop_table(_STAGING_TABLE)

    _disable_rls(_JOBS_TABLE)
    op.drop_index(f"ix_{_JOBS_TABLE}_status", table_name=_JOBS_TABLE)
    op.drop_index(f"ix_{_JOBS_TABLE}_source_system", table_name=_JOBS_TABLE)
    op.drop_index(f"ix_{_JOBS_TABLE}_workspace_id", table_name=_JOBS_TABLE)
    op.drop_table(_JOBS_TABLE)
    # Enums nascem com native_enum=False (CHECK constraint por coluna, não
    # CREATE TYPE) — dropar as tabelas já basta, sem DROP TYPE residual
    # (mesmo racional de 3040c77e8efa/b6d8f0a2c415).
