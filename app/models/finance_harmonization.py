"""
Modelo: Harmonização de Documentos Financeiros × Lançamentos Manuais (F14)
Extensão da Conciliação Bancária (F7, ver `app/models/bank_reconciliation.py`)
— aqui o "extrato" é qualquer documento financeiro (PDF/XML/CSV de banco ou
DRE) enviado pelo usuário, comparado contra o financeiro local
(`FinanceEntry` + `BankTransaction`) do período, em vez de um extrato
bancário cru sendo casado contra cobranças.

`FinanceHarmonizationRun` é a verificação completa de um documento contra um
período: nasce sempre em `aguardando_validacao` (nunca aplica nada sozinha) e
só transiciona para `validado`/`rejeitado` por decisão humana explícita
(`decided_by`/`decided_at`). Uma verificação NUNCA muda `FinanceEntry` ou
`BankTransaction` — é puramente diagnóstica; decidir uma linha ou validar o
run apenas registra a decisão humana (`FinanceHarmonizationItem.decision`),
diferente do F7 onde confirmar uma sugestão aplica a baixa. Isso evita um
sistema paralelo de aplicação: quem aplica baixa continua sendo o F7/F1.

`FinanceHarmonizationItem` é cada linha da tabela comparativa (bate | não
bate | só no documento | só no sistema). Quando a linha existe no documento,
os campos `doc_*` guardam a transação normalizada pelo parser (ver
`app/services/finance_document_parser.py`); quando a linha só existe no
sistema, `doc_*` fica nulo e `matched_source`/`matched_entry_id` aponta para
o `FinanceEntry`/`BankTransaction` órfão daquele período.

`FinanceHarmonizationQuota` é o contador mensal idempotente (1 verificação
completa/mês por workspace + 2 bônus padrão = 3 usos/mês; `bonus_extra`
guarda bônus adicionais concedidos pelo admin da plataforma via
`/blackbox`). `UNIQUE(workspace_id, competence)` garante que o contador
nunca duplica: incrementar é sempre `SELECT ... FOR UPDATE` + `UPDATE`
dentro da mesma transação do run.
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.workspace import Workspace


class FinanceHarmonizationSourceType(str, enum.Enum):
    pdf = "pdf"
    xml = "xml"
    csv = "csv"


class FinanceHarmonizationStatus(str, enum.Enum):
    draft = "draft"
    aguardando_validacao = "aguardando_validacao"
    validado = "validado"
    rejeitado = "rejeitado"


class FinanceHarmonizationMatchKind(str, enum.Enum):
    exato = "exato"
    aproximado = "aproximado"
    so_no_documento = "so_no_documento"
    so_no_sistema = "so_no_sistema"


class FinanceHarmonizationMatchedSource(str, enum.Enum):
    finance_entry = "finance_entry"
    bank_transaction = "bank_transaction"


class FinanceHarmonizationDecision(str, enum.Enum):
    pendente = "pendente"
    confirmado = "confirmado"
    rejeitado = "rejeitado"
    ajustar = "ajustar"


class FinanceHarmonizationRun(UUIDMixin, TimestampMixin, Base):
    """Verificação completa de um documento financeiro contra o período (F14)."""

    __tablename__ = "finance_harmonization_runs"
    __table_args__ = (
        Index("ix_finance_harmonization_runs_workspace_status", "workspace_id", "status"),
        Index("ix_finance_harmonization_runs_workspace_competence", "workspace_id", "competence"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_document_name: Mapped[str] = mapped_column(String(300), nullable=False)
    source_type: Mapped[FinanceHarmonizationSourceType] = mapped_column(
        Enum(FinanceHarmonizationSourceType, name="finance_harmonization_source_type_enum"),
        nullable=False,
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    # "YYYY-MM" derivado de period_start — mesma chave de competência usada
    # pela quota (`FinanceHarmonizationQuota.competence`).
    competence: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    status: Mapped[FinanceHarmonizationStatus] = mapped_column(
        Enum(FinanceHarmonizationStatus, name="finance_harmonization_status_enum"),
        nullable=False,
        default=FinanceHarmonizationStatus.draft,
        index=True,
    )
    # {"batem": n, "naoBatem": n, "soNoDocumento": n, "soNoSistema": n,
    #  "valorDocumento": "...", "valorSistema": "..."} — snapshot exibido na
    # tabela comparativa sem recalcular a cada leitura.
    totals: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    # Mensagem honesta quando o parser não pôde processar o arquivo (ex.: PDF
    # sem lib de extração disponível no venv) — nunca quebra o upload, só
    # marca o run sem linhas de documento.
    parser_warning: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_by: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_by: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    workspace: Mapped["Workspace"] = relationship("Workspace")
    items: Mapped[list["FinanceHarmonizationItem"]] = relationship(
        "FinanceHarmonizationItem",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="FinanceHarmonizationItem.created_at",
    )


class FinanceHarmonizationItem(UUIDMixin, TimestampMixin, Base):
    """Linha comparativa de uma `FinanceHarmonizationRun` (F14)."""

    __tablename__ = "finance_harmonization_items"
    __table_args__ = (
        Index("ix_finance_harmonization_items_workspace_run", "workspace_id", "run_id"),
        Index("ix_finance_harmonization_items_run_kind", "run_id", "match_kind"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("finance_harmonization_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # ── Transação do documento (nula quando `match_kind == so_no_sistema`) ──
    doc_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    doc_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    doc_description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    doc_fingerprint: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    doc_raw: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)

    # ── Contrapartida no sistema (nula quando `match_kind == so_no_documento`) ──
    matched_source: Mapped[Optional[FinanceHarmonizationMatchedSource]] = mapped_column(
        Enum(FinanceHarmonizationMatchedSource, name="finance_harmonization_matched_source_enum"),
        nullable=True,
    )
    matched_entry_id: Mapped[Optional[UUID]] = mapped_column(nullable=True, index=True)

    match_kind: Mapped[FinanceHarmonizationMatchKind] = mapped_column(
        Enum(FinanceHarmonizationMatchKind, name="finance_harmonization_match_kind_enum"),
        nullable=False,
        index=True,
    )
    delta_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    delta_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    decision: Mapped[FinanceHarmonizationDecision] = mapped_column(
        Enum(FinanceHarmonizationDecision, name="finance_harmonization_decision_enum"),
        nullable=False,
        default=FinanceHarmonizationDecision.pendente,
        index=True,
    )
    decided_by: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    workspace: Mapped["Workspace"] = relationship("Workspace")
    run: Mapped["FinanceHarmonizationRun"] = relationship(
        "FinanceHarmonizationRun", back_populates="items"
    )


class FinanceHarmonizationQuota(UUIDMixin, TimestampMixin, Base):
    """Contador mensal idempotente de verificações completas (F14).

    `used_count` soma toda tentativa de criar um run (mesmo quando o parser
    volta `aguardando_parser`) — é a "verificação completa" que consome a
    cota, não a qualidade do resultado. Limite padrão = 1 base + 2 bônus = 3
    por competência; `bonus_extra` é aditivo, concedido só pelo admin da
    plataforma.
    """

    __tablename__ = "finance_harmonization_quotas"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "competence", name="uq_finance_harmonization_quota_workspace_competence"
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    competence: Mapped[str] = mapped_column(String(7), nullable=False)
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bonus_extra: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    granted_by: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    granted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    workspace: Mapped["Workspace"] = relationship("Workspace")
