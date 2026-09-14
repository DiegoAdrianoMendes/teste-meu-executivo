"""
Modelo: LegalException (Central de Exceções)

Generaliza `ApprovalQueueItem` (app/models/approval.py) para TODOS os tipos
de exceção humana nomeados no Plano Mestre (§6/§3): publicação sem vínculo
confiável, homônimo, prazo ambíguo, conflito de calendário, alteração de
tese, precedente não verificado, documento indispensável ausente,
procuração inconsistente, custas não comprovadas, versão modificada após
aprovação, ação externa não autorizada.

Por que uma tabela nova em vez de reaproveitar `approval_queue_items`:
`ApprovalQueueItem.matter_id` é obrigatório e o workspace é herdado do
`Matter` (ParentWorkspacePolicy) — mas várias exceções nascem SEM vínculo
confiável nenhum a um caso (esse é literalmente o motivo de existir da
exceção "publicacao_sem_vinculo_confiavel"). `LegalException.matter_id` é
opcional, então esta tabela carrega workspace_id PRÓPRIO (RLS direta),
igual ao padrão já usado por `FinanceEntry`/`monitored_processes`.

Disciplina reaproveitada de `ApprovalQueueItem`/`ApprovalService` (não
duplicada):
  * idempotência por chave — aqui `(workspace_id, exception_kind, origin,
    dedupe_key)` único enquanto `status='aberta'`, mesmo desenho do índice
    parcial `uq_approval_pending_intent`;
  * resolvedor SEMPRE derivado do usuário autenticado, NUNCA do payload do
    cliente (lição do Risco 2 — ver app/api/v1/endpoints/approvals.py,
    que sobrescreve `resolved_by` com `current_user_id` antes de chamar o
    service).
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin
from app.models.approval import ApprovalPriority

if TYPE_CHECKING:
    from app.models.matter import Matter
    from app.models.task import Task
    from app.models.user import User
    from app.models.workspace import Workspace


class ExceptionKind(StrEnum):
    """Os 11 tipos de exceção humana nomeados no Plano Mestre (§6/§3)."""

    publicacao_sem_vinculo_confiavel = "publicacao_sem_vinculo_confiavel"
    homonimo = "homonimo"
    prazo_ambiguo = "prazo_ambiguo"
    conflito_calendario = "conflito_calendario"
    alteracao_tese = "alteracao_tese"
    precedente_nao_verificado = "precedente_nao_verificado"
    documento_indispensavel_ausente = "documento_indispensavel_ausente"
    procuracao_inconsistente = "procuracao_inconsistente"
    custas_nao_comprovadas = "custas_nao_comprovadas"
    versao_modificada_apos_aprovacao = "versao_modificada_apos_aprovacao"
    acao_externa_nao_autorizada = "acao_externa_nao_autorizada"
    # Onda 7 (DMS jurídico, Plano Mestre §11 item 2): um espelho externo
    # (Drive/OneDrive/SharePoint) divergiu do documento canônico no Meu
    # Executivo. NUNCA resolvido automaticamente — vira item humano aqui.
    conflito_sincronizacao_documental = "conflito_sincronizacao_documental"


class ExceptionCenterStatus(StrEnum):
    aberta = "aberta"
    resolvida = "resolvida"
    escalada = "escalada"


class ExceptionCenterDecision(StrEnum):
    """Ações terminais aceitas por `ExceptionCenterService.resolve()` —
    'aberta' nunca é um alvo de resolução, só o estado inicial."""

    resolvida = "resolvida"
    escalada = "escalada"


def _exception_kind_enum() -> Enum:
    return Enum(
        ExceptionKind,
        name="exception_kind_enum",
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


def _exception_priority_enum() -> Enum:
    return Enum(
        ApprovalPriority,
        name="exception_priority_enum",
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


def _exception_status_enum() -> Enum:
    return Enum(
        ExceptionCenterStatus,
        name="exception_center_status_enum",
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


class LegalException(UUIDMixin, TimestampMixin, Base):
    """
    Entidade LegalException — item da Central de Exceções.
    Idempotente por (workspace_id, exception_kind, origin, dedupe_key)
    enquanto status='aberta': reingestão do mesmo evento pelo mesmo
    produtor nunca duplica (mesmo comportamento de replay de
    `ApprovalService.create`).
    """
    __tablename__ = "legal_exceptions"
    __table_args__ = (
        Index(
            "uq_legal_exception_open_intent",
            "workspace_id",
            "exception_kind",
            "origin",
            "dedupe_key",
            unique=True,
            postgresql_where=text("status = 'aberta'"),
            sqlite_where=text("status = 'aberta'"),
        ),
        # No máximo 1 task materializada por exceção — mesmo desenho de
        # ux_tasks_source_exception_id no outro lado da referência.
        Index(
            "ux_legal_exceptions_linked_task_id",
            "linked_task_id",
            unique=True,
            postgresql_where=text("linked_task_id IS NOT NULL"),
            sqlite_where=text("linked_task_id IS NOT NULL"),
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Opcional de propósito: várias exceções (ex.: publicação sem vínculo
    # confiável) nascem sem nenhum Matter identificado com confiança.
    matter_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("matters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    opened_by: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_by: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    escalated_to: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    exception_kind: Mapped[ExceptionKind] = mapped_column(
        _exception_kind_enum(), nullable=False, index=True
    )
    # Identifica o produtor (módulo.função) que abriu a exceção — ex.:
    # "process_monitoring.ingest_publication", "finance_dunning.dispatch".
    origin: Mapped[str] = mapped_column(String(150), nullable=False)
    # Chave de idempotência dentro do produtor — junto com
    # workspace_id + exception_kind + origin forma a unicidade "aberta"
    # (mesmo desenho de intent_key em ApprovalQueueItem).
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Payload contextual bruto do evento que originou a exceção (ex.:
    # numero_cnj, publication_id, dunning_log_id) — o humano decide sem
    # precisar caçar em outra tela.
    payload: Mapped[Optional[dict]] = mapped_column(FlexibleJSON(), nullable=True)
    priority: Mapped[ApprovalPriority] = mapped_column(
        _exception_priority_enum(),
        nullable=False,
        default=ApprovalPriority.media,
        index=True,
    )
    status: Mapped[ExceptionCenterStatus] = mapped_column(
        _exception_status_enum(),
        nullable=False,
        default=ExceptionCenterStatus.aberta,
        index=True,
    )
    resolution_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    escalated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Materialização como tarefa humana (decisão do dono): preenchida por
    # ExceptionCenterService.open_exception quando matter_id existe — a
    # task nasce junto com a exceção, na mesma transação. SET NULL se a
    # task some (hoje nada apaga Task; é defesa, não caminho esperado).
    # use_alter=True quebra o ciclo de FK legal_exceptions<->tasks para
    # ordenação de CREATE/DROP TABLE via metadata (mesmo padrão de
    # app/models/executive.py::ExecutiveRound.user_message_id) — o nome
    # explícito casa com fk_legal_exceptions_linked_task_id criada em
    # alembic/versions/d7e9f1a3c5b6_link_legal_exceptions_to_tasks.py.
    linked_task_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey(
            "tasks.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_legal_exceptions_linked_task_id",
        ),
        nullable=True,
    )

    # ── Relacionamentos ──
    workspace: Mapped["Workspace"] = relationship("Workspace")
    matter: Mapped["Matter | None"] = relationship("Matter")
    opener: Mapped["User | None"] = relationship("User", foreign_keys=[opened_by])
    resolver: Mapped["User | None"] = relationship("User", foreign_keys=[resolved_by])
    escalation_target: Mapped["User | None"] = relationship(
        "User", foreign_keys=[escalated_to]
    )
    linked_task: Mapped["Task | None"] = relationship(
        "Task", foreign_keys=[linked_task_id]
    )

    def __repr__(self) -> str:
        return f"<LegalException id={self.id} kind={self.exception_kind} status={self.status}>"
