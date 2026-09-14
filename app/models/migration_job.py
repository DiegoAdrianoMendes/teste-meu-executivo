"""
Modelo: Importador Universal de Migração (Onda 3, Plano Mestre §12).

Metade AGNÓSTICA da fábrica de migração — funciona para qualquer origem que
consiga exportar CSV/XLSX/ZIP/JSON, sem depender de nenhum concorrente
específico. Os adapters dedicados (Astrea/ADVBOX/DataJuri) ficam FORA desta
onda por falta de um arquivo real de export de cada um (ver
`docs/ONDA3_ADAPTERS_BLOQUEADOS.md`); o enum `MigrationSourceSystem` já
reserva os três valores para quando esses arquivos existirem — nenhum
código novo deve ser necessário além de um parser específico.

Três tabelas, RLS direta por `workspace_id` (mesmo padrão de
`FinanceEntry`/`legal_exceptions`/`monitored_processes`):

  * `MigrationJob` — o lote de migração em si: de onde veio o arquivo
    (hash + manifesto, nunca o binário bruto — ver nota abaixo), em que
    fase do pipeline está, o resumo de prévia/reconciliação e o semáforo
    de corte com a decisão humana auditada.
  * `MigrationStagingRecord` — cada linha do arquivo de origem, isolada em
    staging por `(job_id, domain, dedupe_key)`. Nunca escreve direto nas
    tabelas de produção: `target_id` só é preenchido depois que o passo de
    importação cria/mescla a entidade real (`Client`/`Matter`/
    `FinanceEntry`, conforme o domínio).
  * `MigrationDivergence` — fila de divergências encontradas na
    reconciliação (severidade `critica` alimenta o semáforo vermelho,
    `atencao` alimenta o amarelo) — nunca resolve sozinha, sempre espera
    decisão humana (`resolvido`/`resolved_by`/`resolved_at`).

Por que o arquivo bruto NUNCA é persistido em disco: mesmo racional de
`FinanceHarmonizationRun`/`finance_document_parser.py` (F14) — o hash
SHA-256 é calculado sobre os bytes originais ANTES de qualquer parsing e
gravado no manifesto (`MigrationJob.source_manifest`), o que garante que
duas cargas do mesmo arquivo produzem o mesmo hash (idempotência de
`receive_upload`); o que preserva "nunca alterado" linha a linha é
`MigrationStagingRecord.raw`, que guarda o dicionário bruto da linha
parseada e NUNCA é sobrescrito pela normalização (`normalized`), o mesmo
desenho de `FinanceHarmonizationItem.doc_raw` vs. campos normalizados.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.workspace import Workspace


class MigrationSourceSystem(StrEnum):
    """Origem declarada do arquivo. Os 3 últimos valores são reservados
    para os adapters dedicados bloqueados (ver docs/ONDA3_ADAPTERS_BLOQUEADOS.md)
    — o enum já os prevê para não exigir migração de banco quando eles
    forem destravados, só o parser específico."""

    universal_csv = "universal_csv"
    universal_xlsx = "universal_xlsx"
    zip_docs = "zip_docs"
    json = "json"
    astrea = "astrea"
    advbox = "advbox"
    datajuri = "datajuri"


class MigrationJobStatus(StrEnum):
    """Estados do pipeline §12, em ordem linear (com `erro` alcançável de
    qualquer estado e `revertido` alcançável de qualquer estado pós-`previa`
    que já tenha criado alguma entidade). 'previa' é a forma ASCII de
    'prévia' — enums não carregam acento neste código-base (ver
    `ExceptionKind`/`LegalDemandPhase`)."""

    recebido = "recebido"
    staging = "staging"
    validado = "validado"
    previa = "previa"
    importando = "importando"
    reconciliando = "reconciliando"
    concluido = "concluido"
    revertido = "revertido"
    erro = "erro"


# Estados que já podem ter criado entidades de produção e portanto são
# elegíveis a rollback (MigrationService.rollback rejeita fora daqui).
MIGRATION_ROLLBACK_ELIGIBLE_STATUSES = frozenset(
    {
        MigrationJobStatus.importando,
        MigrationJobStatus.reconciliando,
        MigrationJobStatus.concluido,
    }
)


class MigrationDomain(StrEnum):
    """Os 13 domínios de dado que o pipeline agnóstico sabe estagiar."""

    clientes = "clientes"
    contatos = "contatos"
    partes = "partes"
    casos = "casos"
    processos = "processos"
    tarefas = "tarefas"
    prazos = "prazos"
    documentos = "documentos"
    contratos = "contratos"
    honorarios = "honorarios"
    despesas = "despesas"
    recebimentos = "recebimentos"
    tags = "tags"


# Domínios com importador real (materializam entidade de produção) nesta
# onda — os demais permanecem staged com `target_id` nulo e geram uma
# divergência não-crítica "importador_pendente" na reconciliação (nunca
# fingem ter importado; ver MigrationService._IMPORTERS).
MIGRATION_DOMAINS_WITH_IMPORTER = frozenset(
    {
        MigrationDomain.clientes,
        MigrationDomain.casos,
        MigrationDomain.processos,
        MigrationDomain.recebimentos,
        MigrationDomain.despesas,
    }
)


class MigrationConflict(StrEnum):
    """Resultado da checagem de dedupe de uma linha de staging contra o
    restante do job + a base de produção já existente."""

    none = "none"
    duplicate = "duplicate"
    ambiguous = "ambiguous"


class MigrationRecordStatus(StrEnum):
    """Estado de UMA linha de staging — a soma destes por domínio é o que
    alimenta a reconciliação (origem vs. importado vs. mesclado vs.
    rejeitado)."""

    pendente = "pendente"
    valido = "valido"
    invalido = "invalido"
    importado = "importado"
    mesclado = "mesclado"
    rejeitado = "rejeitado"


MIGRATION_RECORD_TERMINAL_STATUSES = frozenset(
    {
        MigrationRecordStatus.importado,
        MigrationRecordStatus.mesclado,
        MigrationRecordStatus.rejeitado,
    }
)


class MigrationDivergenceSeverity(StrEnum):
    """Alimenta o semáforo: qualquer `critica` não resolvida = vermelho;
    só `atencao` não resolvida = amarelo; nenhuma pendente = verde."""

    critica = "critica"
    atencao = "atencao"


def _migration_enum(enum_type: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


class MigrationJob(UUIDMixin, TimestampMixin, Base):
    """Um lote de migração — o job dono do pipeline inteiro (§12)."""

    __tablename__ = "migration_jobs"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_system: Mapped[MigrationSourceSystem] = mapped_column(
        _migration_enum(MigrationSourceSystem, "migration_source_system_enum"),
        nullable=False,
        index=True,
    )
    status: Mapped[MigrationJobStatus] = mapped_column(
        _migration_enum(MigrationJobStatus, "migration_job_status_enum"),
        nullable=False,
        default=MigrationJobStatus.recebido,
        index=True,
    )
    # {filename, contentType, byteSize, sha256, formatDetected, uploadedAt,
    #  rowCounts: {<domain>: n}, rejectedRows: n} — nunca inclui o binário.
    source_manifest: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    # Mapeamento configurável domain -> {target_field: source_column}.
    # Quando ausente, MigrationService usa o mapeamento padrão embutido.
    field_mapping: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    # {<domain>: {"staged": n, "validos": n, "invalidos": n, "duplicados":
    #  n, "ambiguos": n}} — snapshot calculado por build_preview().
    preview_summary: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    # {<domain>: {"origem": n, "importado": n, "mesclado": n, "rejeitado":
    #  n}} — snapshot calculado por reconcile().
    reconciliation_summary: Mapped[dict] = mapped_column(
        FlexibleJSON(), nullable=False, default=dict
    )
    semaphore: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    semaphore_computed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # {"decision": "prosseguir"|"nao_prosseguir", "decidedBy": "<uuid>",
    #  "decidedAt": "<iso>", "notes": "..."} — registrado, nunca vira
    # garantia de qualidade dos dados (texto do Plano Mestre §12).
    cutover_decision: Mapped[Optional[dict]] = mapped_column(FlexibleJSON(), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rolled_back_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rolled_back_by: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    workspace: Mapped["Workspace"] = relationship("Workspace")
    creator: Mapped[Optional["User"]] = relationship("User", foreign_keys=[created_by])
    staging_records: Mapped[list["MigrationStagingRecord"]] = relationship(
        "MigrationStagingRecord",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="MigrationStagingRecord.created_at",
    )
    divergences: Mapped[list["MigrationDivergence"]] = relationship(
        "MigrationDivergence",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="MigrationDivergence.created_at",
    )

    def __repr__(self) -> str:
        return f"<MigrationJob id={self.id} source={self.source_system} status={self.status}>"


class MigrationStagingRecord(UUIDMixin, TimestampMixin, Base):
    """Uma linha do arquivo de origem, isolada em staging (§12)."""

    __tablename__ = "migration_staging_records"
    __table_args__ = (
        # Idempotência de estagiamento: reprocessar o mesmo job nunca
        # duplica a mesma linha (mesma chave determinística).
        UniqueConstraint(
            "job_id",
            "domain",
            "dedupe_key",
            name="uq_migration_staging_job_domain_dedupe",
        ),
        Index(
            "ix_migration_staging_workspace_job_domain",
            "workspace_id",
            "job_id",
            "domain",
        ),
        Index(
            "ix_migration_staging_job_status",
            "job_id",
            "record_status",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("migration_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    domain: Mapped[MigrationDomain] = mapped_column(
        _migration_enum(MigrationDomain, "migration_domain_enum"), nullable=False, index=True
    )
    # Conteúdo bruto da linha, exatamente como veio do arquivo — nunca
    # sobrescrito (é a garantia de "arquivo nunca alterado" linha a linha).
    raw: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    # Conteúdo normalizado (CPF/CNPJ só dígitos, CNJ canônico, datas ISO) —
    # produzido por MigrationService._normalize_row, recalculável a
    # qualquer momento a partir de `raw` sem perder o original.
    normalized: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(300), nullable=False)
    # Verdadeiro quando o dedupe_key caiu num identificador fraco (nome,
    # sem CPF/CNJ/referência externa) — repetição vira `ambiguous`, nunca
    # `duplicate` (não mescla cego homônimos).
    weak_identity: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    conflict: Mapped[MigrationConflict] = mapped_column(
        _migration_enum(MigrationConflict, "migration_conflict_enum"),
        nullable=False,
        default=MigrationConflict.none,
        index=True,
    )
    record_status: Mapped[MigrationRecordStatus] = mapped_column(
        _migration_enum(MigrationRecordStatus, "migration_record_status_enum"),
        nullable=False,
        default=MigrationRecordStatus.pendente,
        index=True,
    )
    validation_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Preenchido só depois que a importação cria/mescla a entidade real —
    # nunca aponta pra nada antes disso.
    target_id: Mapped[Optional[UUID]] = mapped_column(nullable=True, index=True)
    target_table: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    imported_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    workspace: Mapped["Workspace"] = relationship("Workspace")
    job: Mapped["MigrationJob"] = relationship("MigrationJob", back_populates="staging_records")

    def __repr__(self) -> str:
        return (
            f"<MigrationStagingRecord id={self.id} domain={self.domain} "
            f"status={self.record_status}>"
        )


class MigrationDivergence(UUIDMixin, TimestampMixin, Base):
    """Item da fila de divergências encontrada na reconciliação (§12)."""

    __tablename__ = "migration_divergences"
    __table_args__ = (
        Index(
            "ix_migration_divergences_workspace_job",
            "workspace_id",
            "job_id",
        ),
        Index(
            "ix_migration_divergences_job_resolved",
            "job_id",
            "resolvido",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("migration_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Nulo para divergências de nível-arquivo (ex.: XLSX sem lib disponível,
    # ZIP corrompido) que não pertencem a um domínio específico.
    domain: Mapped[Optional[MigrationDomain]] = mapped_column(
        _migration_enum(MigrationDomain, "migration_divergence_domain_enum"), nullable=True
    )
    # Código estável (ex.: "saldo_financeiro_divergente",
    # "prazo_fatal_perdido", "processo_sem_vinculo", "cnj_invalido",
    # "dominio_desconhecido", "importador_pendente") — string livre e não
    # enum fechado: a fila de divergências cresce com os adapters futuros
    # sem exigir migração de banco a cada tipo novo (mesmo racional de
    # `LegalException.origin`, que também é string).
    tipo: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    severity: Mapped[MigrationDivergenceSeverity] = mapped_column(
        _migration_enum(MigrationDivergenceSeverity, "migration_divergence_severity_enum"),
        nullable=False,
        default=MigrationDivergenceSeverity.atencao,
        index=True,
    )
    detalhe: Mapped[str] = mapped_column(Text, nullable=False)
    # Contexto estruturado opcional (ex.: staging_record_id, valores
    # comparados) para a UI reconstruir o problema sem reparsear texto.
    context: Mapped[Optional[dict]] = mapped_column(FlexibleJSON(), nullable=True)
    resolvido: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    resolved_by: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    workspace: Mapped["Workspace"] = relationship("Workspace")
    job: Mapped["MigrationJob"] = relationship("MigrationJob", back_populates="divergences")

    def __repr__(self) -> str:
        return f"<MigrationDivergence id={self.id} tipo={self.tipo} severity={self.severity}>"
