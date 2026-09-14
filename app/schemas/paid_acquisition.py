"""Schemas: Aquisição paga integrada (F5) — drafts de campanha e funil por campanha.

Nenhum dado aqui representa uma chamada real à Marketing API. Toda criação de
campanha é um rascunho (ExternalActionIntent) aguardando aprovação humana; a
execução real permanece atrás de `require_external_delivery_allowed`.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.base import CamelCaseSchema, WriteSchema

PaidAcquisitionChannel = Literal["ctwa", "ctm", "ctd"]
PaidAcquisitionObjective = Literal[
    "lead_generation", "conversations", "traffic", "conversions"
]

CHANNEL_LABELS: dict[str, str] = {
    "ctwa": "Click-to-WhatsApp",
    "ctm": "Click-to-Messenger",
    "ctd": "Click-to-Instagram-Direct",
}


class PaidAcquisitionCreativeInput(WriteSchema):
    headline: str = Field(min_length=1, max_length=120)
    primary_text: str = Field(min_length=1, max_length=2000)
    image_ref: str | None = Field(default=None, max_length=255)
    call_to_action: str = Field(default="WHATSAPP_MESSAGE", max_length=60)


class PrepareCampaignDraftRequest(WriteSchema):
    idempotency_key: str = Field(min_length=1, max_length=140)
    channel: PaidAcquisitionChannel
    objective: PaidAcquisitionObjective = "conversations"
    campaign_name: str = Field(min_length=1, max_length=180)
    daily_budget: Decimal = Field(gt=0)
    currency: str = Field(default="BRL", min_length=3, max_length=3)
    creative: PaidAcquisitionCreativeInput
    welcome_message: str = Field(min_length=1, max_length=1000)
    ice_breakers: list[str] = Field(default_factory=list, max_length=4)
    connection_id: UUID | None = Field(
        default=None,
        description=(
            "Conexão de canal (WhatsApp/Página/Instagram) a usar como destino. "
            "Se omitido, usa a conexão padrão ativa do canal exigido."
        ),
    )

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, value: str) -> str:
        return value.upper()

    @field_validator("ice_breakers")
    @classmethod
    def _non_empty_ice_breakers(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        return cleaned[:4]


class CampaignDraftRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    channel: PaidAcquisitionChannel
    channel_label: str
    status: str
    provider: str | None
    marketing_api_payload: dict[str, Any]
    reason: str
    requested_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime


class PaidAcquisitionSyncStatus(CamelCaseSchema):
    gate_open: bool
    connected: bool
    provider: str = "meta_ads"
    last_synced_at: datetime | None
    synced_campaigns: int
    synced_receipts: int
    last_receipt_source: str | None
    state: Literal["not_synced", "synced_demo", "synced_manual", "synced_official"]
    message: str


class FunnelCampaignRow(CamelCaseSchema):
    campaign_key: str
    campaign_external_id: str | None
    campaign_name: str
    provider: str | None
    channel_hint: PaidAcquisitionChannel | None = None
    spend: float
    spend_currency: str
    spend_origin: Literal["oficial", "manual", "demonstracao", "sem_dado"]
    conversations: int
    qualified_leads: int
    contracts: int
    revenue: float
    cpl: float | None
    cac: float | None
    roas: float | None


class FunnelSummaryRead(CamelCaseSchema):
    date_from: date
    date_to: date
    rows: list[FunnelCampaignRow]
    totals: FunnelCampaignRow
