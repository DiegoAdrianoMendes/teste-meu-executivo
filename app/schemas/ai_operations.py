"""Schemas de observabilidade da operação de IA (custos, rotas, fallback).

Somente metadados agregados: nunca prompt, resposta ou credencial.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.base import CamelCaseSchema


class AiOperationsClassProviderRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    task_class: str
    provider: str
    executions: int
    fallback_count: int
    cost_usd: Decimal
    billing_unknown: int


class AiOperationsAgentRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    agent_name: str
    executions: int
    cost_usd: Decimal


class AiOperationsDayRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    day: str
    executions: int
    cost_usd: Decimal
    fallback_count: int


class AiOperationsWorkspaceRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    workspace_id: UUID
    executions: int
    cost_usd: Decimal
    fallback_count: int


class AiOperationsRouteAlert(BaseModel):
    """Rota com falha recente — sinal durável do estado de saúde da rota.

    Substitui a leitura do circuit breaker em memória (process-local, não
    consultável entre tasks do ECS) por evidência persistida e agregável.
    """

    model_config = ConfigDict(from_attributes=True)

    route_key: str
    provider: str
    model: str
    failures: int
    last_failure_at: datetime | None


class AiOperationsSummary(BaseModel):
    generated_at: datetime
    window_days: int
    executions: int
    cost_usd: Decimal
    fallback_count: int
    fallback_rate: float
    billing_unknown: int
    by_class_provider: list[AiOperationsClassProviderRow]
    by_agent: list[AiOperationsAgentRow]
    by_day: list[AiOperationsDayRow]
    by_workspace: list[AiOperationsWorkspaceRow]
    route_alerts: list[AiOperationsRouteAlert]


class AiOperationsEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    created_at: datetime
    agent_name: str
    task_class: str
    provider: str
    model: str
    outcome: str
    route_key: str
    fallback_used: bool
    cache_hit: bool
    billing_unknown: bool
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None
    estimated_cost_usd: Decimal | None
    operation: str


class AiOperationsEventPage(CamelCaseSchema):
    total: int
    limit: int
    offset: int
    has_more: bool
    items: list[AiOperationsEvent]
