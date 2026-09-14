"""
Schemas: Operação Massificada / Lotes (Onda 8, Plano Mestre §7).
Ver app/models/legal_batch.py e app/services/legal_batch_service.py.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import Field, model_validator

from app.models.legal_batch import (
    LegalBatchAction,
    LegalBatchCriterion,
    LegalBatchDecisionKind,
)
from app.schemas.base import CamelCaseSchema, WriteSchema


class LegalBatchItemSeed(WriteSchema):
    """Um item a ser criado dentro do lote na criação."""

    item_ref: Optional[str] = None
    legal_demand_id: Optional[UUID] = None
    action: LegalBatchAction
    decision_kind: LegalBatchDecisionKind
    input_payload: dict = Field(default_factory=dict)
    responsible_user_id: Optional[UUID] = None
    estimated_cost: Optional[float] = None
    estimated_time_saved_minutes: Optional[int] = None


class LegalBatchCreate(WriteSchema):
    name: str
    external_ref: Optional[str] = None
    criterion_type: LegalBatchCriterion
    criterion_value: dict = Field(default_factory=dict)
    playbook_ref: Optional[str] = None
    default_action: Optional[LegalBatchAction] = None
    items: list[LegalBatchItemSeed] = Field(default_factory=list)


class LegalBatchItemRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    batch_id: UUID
    item_ref: Optional[str] = None
    legal_demand_id: Optional[UUID] = None
    action: LegalBatchAction
    decision_kind: LegalBatchDecisionKind
    state: str
    state_history: list
    input_payload: dict
    result_payload: dict
    confidence: Optional[float] = None
    error_reason: Optional[str] = None
    autonomy_level_applied: Optional[str] = None
    autonomy_policy_id: Optional[UUID] = None
    requires_human_approval: bool
    approved_by_user_id: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    estimated_cost: Optional[float] = None
    estimated_time_saved_minutes: Optional[int] = None
    responsible_user_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime


class LegalBatchRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    name: str
    external_ref: Optional[str] = None
    criterion_type: LegalBatchCriterion
    criterion_value: dict
    playbook_ref: Optional[str] = None
    default_action: Optional[LegalBatchAction] = None
    status: str
    created_by_user_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime


class LegalBatchPanel(CamelCaseSchema):
    """Painel do lote — SEMPRE contagem real dos itens, nunca um contador
    incrementado paralelamente (ver LegalBatchService.get_panel)."""

    batch_id: UUID
    status: str
    total_items: int
    pendente: int
    classificado: int
    vinculado: int
    rascunho: int
    revisado: int
    bloqueado: int
    baixa_confianca: int
    divergente: int
    aguardando_humano: int
    concluido: int
    estimated_cost_total: float
    estimated_time_saved_minutes_total: int


class LegalBatchProcessResult(CamelCaseSchema):
    """Resultado de um processamento em lote — falha parcial NUNCA
    escondida: cada item processado aparece com seu resultado individual."""

    batch_id: UUID
    processed_item_ids: list[UUID]
    succeeded_item_ids: list[UUID]
    failed_item_ids: list[UUID]
    panel: LegalBatchPanel


class LegalBatchApproveAllResult(CamelCaseSchema):
    """Resultado de 'aprovar tudo' — os itens em PROTECTED_DECISION_KINDS
    NUNCA entram em `approved_item_ids`, sempre em `skipped_protected_item_ids`."""

    batch_id: UUID
    approved_item_ids: list[UUID]
    skipped_protected_item_ids: list[UUID]
    skipped_not_pending_item_ids: list[UUID]


class LegalBatchRollbackRequest(WriteSchema):
    reason: Optional[str] = None


class LegalBatchCreateFromContext(WriteSchema):
    """Recorte por contexto: cria o lote a partir de TODAS as
    `LegalDemand` de um cliente, ou de UMA `LegalDemand` específica —
    nunca os dois ao mesmo tempo, nunca nenhum (ver validador abaixo).
    Reusa integralmente `LegalBatchService.create_batch` por baixo
    (idempotência por `external_ref`, item a item, painel sempre
    recomputado) — só resolve QUAIS demandas entram como item."""

    name: str
    external_ref: Optional[str] = None
    client_id: Optional[UUID] = None
    legal_demand_id: Optional[UUID] = None
    playbook_ref: Optional[str] = None
    action: LegalBatchAction = LegalBatchAction.classificar
    decision_kind: LegalBatchDecisionKind = LegalBatchDecisionKind.classificacao

    @model_validator(mode="after")
    def validate_single_context(self) -> "LegalBatchCreateFromContext":
        if bool(self.client_id) == bool(self.legal_demand_id):
            raise ValueError(
                "Informe client_id OU legal_demand_id para o lote por contexto "
                "— nunca os dois, nunca nenhum."
            )
        return self
