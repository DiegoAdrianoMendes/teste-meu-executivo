"""Schemas da régua de cobrança (dunning) — F4.

Ver docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §5 F4.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import Field

from app.models.finance_dunning import FinanceDunningLogStatus, FinanceDunningStep
from app.schemas.base import CamelCaseSchema, WriteSchema


class FinanceDunningStepConfig(CamelCaseSchema):
    enabled: bool = True
    offset_days: int = Field(..., ge=0, le=120)
    requires_approval: bool = False
    message_template: str = ""


class FinanceDunningStepConfigUpdate(WriteSchema):
    enabled: Optional[bool] = None
    offset_days: Optional[int] = Field(default=None, ge=0, le=120)
    requires_approval: Optional[bool] = None
    message_template: Optional[str] = None


DUNNING_DEFAULT_STEP_CONFIG: dict[str, dict] = {
    FinanceDunningStep.d1_lembrete.value: {
        "enabled": True,
        "offsetDays": 1,
        "requiresApproval": False,
        "messageTemplate": (
            "Olá {clientName}, passando para lembrar que a cobrança "
            "'{entryTitle}' no valor de {amount} venceu em {dueDate}. "
            "Qualquer dúvida, estamos à disposição."
        ),
    },
    FinanceDunningStep.d7_segunda_via.value: {
        "enabled": True,
        "offsetDays": 7,
        "requiresApproval": False,
        "messageTemplate": (
            "Olá {clientName}, sua cobrança '{entryTitle}' de {amount} segue em "
            "aberto (vencida em {dueDate}). Segue a segunda via para regularização."
        ),
    },
    FinanceDunningStep.d15_formal.value: {
        "enabled": True,
        "offsetDays": 15,
        "requiresApproval": True,
        "messageTemplate": (
            "Prezado(a) {clientName}, a cobrança '{entryTitle}' de {amount}, "
            "vencida em {dueDate}, permanece em aberto. Solicitamos a regularização "
            "no menor prazo possível para evitar medidas de cobrança formais."
        ),
    },
}


class FinanceDunningSettingsRead(CamelCaseSchema):
    steps: dict[str, FinanceDunningStepConfig]


class FinanceDunningSettingsUpdate(WriteSchema):
    steps: dict[str, FinanceDunningStepConfigUpdate] = Field(default_factory=dict)


class FinanceDunningLogRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    charge_id: UUID
    step: FinanceDunningStep
    status: FinanceDunningLogStatus
    reason_code: Optional[str] = None
    message_preview: Optional[str] = None
    viewed_before_dispatch: bool
    requires_approval: bool
    approved_by_user_id: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    owner_notified_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    # Resumo da cobrança/lançamento de origem, para exibir a fila e o
    # histórico de disparos sem uma segunda chamada por linha. `None` quando
    # o lançamento associado não pôde ser resolvido (ex.: removido).
    entry_title: Optional[str] = None
    amount: Optional[float] = None
    due_date: Optional[datetime] = None
