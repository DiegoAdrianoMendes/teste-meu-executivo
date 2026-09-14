"""
Schemas da plataforma de integrações abertas e nativas.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.base import CamelCaseSchema, WriteSchema


class IntegrationSetupHintRead(CamelCaseSchema):
    auth_type: str
    webhook_security: str
    required_settings: List[str] = Field(default_factory=list)
    recommended_base_url: Optional[str] = None
    recommended_api_version: Optional[str] = None
    setup_steps: List[str] = Field(default_factory=list)


class IntegrationCatalogProviderRead(CamelCaseSchema):
    key: str
    label: str
    category: str
    description: str
    capabilities: List[str] = Field(default_factory=list)
    native: bool = True
    default_webhook_path: str
    docs_url: Optional[str] = None
    setup_hint: IntegrationSetupHintRead


class OpenApiResourceRead(CamelCaseSchema):
    key: str
    label: str
    path: str
    method: str
    description: str


class OpenApiSurfaceRead(CamelCaseSchema):
    workspace_id: UUID
    workspace_slug: str
    base_url: str
    docs_url: Optional[str] = None
    openapi_url: Optional[str] = None
    webhook_base_url: str
    resources: List[OpenApiResourceRead] = Field(default_factory=list)


class WebhookReceiptRead(CamelCaseSchema):
    provider: str
    workspace_slug: str
    accepted: bool
    connection_found: bool
    matched_matter_id: Optional[UUID] = None
    event_title: str
    received_at: datetime


# ── Tracking de conversão paga — Google Data Manager ────────────────────────
#
# Resolve de uma IntegrationConnection com provider+label fixos, lida por
# app.services.google_conversions_service.load_google_data_manager_config —
# este schema só descreve o formulário de conexão; o despacho real já existe
# (Onda 10) e permanece atrás de KLM_EXTERNAL_ACTIONS_ENABLED.
#
# O Meta CAPI não tem um schema equivalente aqui: já existe conexão completa
# via IntegrationsPage.tsx (aba "Mensuração Meta (CAPI)") +
# KlmAdsMeasurementConfig* em app/api/v1/endpoints/klm_operations.py,
# resolvendo a mesma IntegrationConnection(provider=custom,
# label="meta_capi") que load_meta_capi_config já lê.
#
# Campos de segredo (developerToken/accessToken) são opcionais na escrita:
# omitidos, o valor já salvo é preservado; a leitura (status) nunca devolve
# o segredo, apenas um booleano "has*".


def _stripped_or_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class GoogleDataManagerTrackingUpsertRequest(WriteSchema):
    customer_id: str = Field(min_length=1, max_length=60)
    developer_token: Optional[str] = Field(
        default=None,
        max_length=4000,
        description="Novo developer token. Omitir mantém o valor já salvo.",
    )
    access_token: Optional[str] = Field(
        default=None,
        max_length=4000,
        description="Novo access token. Omitir mantém o valor já salvo.",
    )

    @field_validator("customer_id")
    @classmethod
    def _strip_customer_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("customerId não pode ser vazio.")
        return stripped

    @field_validator("developer_token", "access_token")
    @classmethod
    def _strip_secrets(cls, value: Optional[str]) -> Optional[str]:
        return _stripped_or_none(value)


class GoogleDataManagerTrackingStatusRead(CamelCaseSchema):
    configured: bool
    customer_id: Optional[str] = None
    has_developer_token: bool
    has_access_token: bool
