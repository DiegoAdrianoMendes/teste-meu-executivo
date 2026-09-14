"""Schemas do módulo processual contratável à parte — /processos."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import ConfigDict, Field

from app.models.process_monitoring import (
    DeadlineCountingType,
    DeadlineStatus,
    MonitoredProcessStatus,
    ProcessPublicationSource,
    PublicationJourneyState,
)
from app.schemas.base import CamelCaseSchema, WriteSchema


class MonitoredProcessCreate(WriteSchema):
    numero_cnj: str = Field(..., min_length=15, max_length=25)
    tribunal: Optional[str] = None
    matter_id: Optional[UUID] = None
    oab_vinculada: Optional[str] = None
    responsible_user_id: Optional[UUID] = None


class MonitoredProcessUpdate(WriteSchema):
    """Atualização parcial de um processo monitorado.

    Campo ausente não mexe; `null` explícito limpa. Só os três campos: o
    tribunal não é editável porque o worker o deriva do número CNJ a cada
    tick e reverteria qualquer override. Chave desconhecida é recusada, para
    que um erro de digitação (`oabvinculada`) não vire 200 sem efeito.
    """

    model_config = ConfigDict(extra="forbid")

    oab_vinculada: str | None = None
    matter_id: UUID | None = None
    responsible_user_id: UUID | None = None


class MonitoredProcessRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    matter_id: Optional[UUID] = None
    numero_cnj: str
    tribunal: Optional[str] = None
    status: MonitoredProcessStatus
    oab_vinculada: Optional[str] = None
    responsible_user_id: Optional[UUID] = None
    created_by_user_id: UUID
    last_movement_at: Optional[datetime] = None
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ProcessPublicationRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    monitored_process_id: Optional[UUID] = None
    fonte: ProcessPublicationSource
    external_id: str
    numero_cnj: str
    conteudo: str
    published_at: datetime
    tipo: Optional[str] = None
    urgencia: Optional[str] = None
    dias: Optional[int] = None
    tipo_contagem: Optional[DeadlineCountingType] = None
    data_fatal: Optional[date] = None
    memoria_calculo: Optional[list] = None
    prazo_status: Optional[DeadlineStatus] = None
    confirmed_by_user_id: Optional[UUID] = None
    confirmed_at: Optional[datetime] = None
    journey_state: PublicationJourneyState
    journey_updated_at: Optional[datetime] = None
    created_at: datetime


class DeadlineResolutionRequest(WriteSchema):
    """Corpo vazio hoje — reservado para observação futura (ex.: motivo da dispensa)."""

    note: Optional[str] = None
