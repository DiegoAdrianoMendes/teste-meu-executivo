"""Contratos das chaves de provedores de IA da plataforma (Blackbox).

A chave de API é write-only: entra no PUT e nunca volta em nenhuma resposta;
somente os últimos 4 caracteres aparecem, redigidos.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import CamelCaseSchema, WriteSchema


class PlatformAiCredentialWrite(WriteSchema):
    api_key: str = Field(min_length=8, max_length=4096)


class PlatformAiGateRead(CamelCaseSchema):
    """Estado do gate da plataforma, para a tela ser honesta sobre o que falta."""

    model_apis_enabled: bool
    online_approved: bool
    cipher_configured: bool


class PlatformAiCredentialStatusRead(CamelCaseSchema):
    provider: str
    configured: bool
    is_active: bool
    # Sempre redigida: "****" + últimos 4 caracteres. Nunca a chave em claro.
    api_key_masked: str | None = None
    key_last4: str | None = None
    updated_at: datetime | None = None
    updated_by_user_id: UUID | None = None
    # False para providers sem adapter por chave (vertex usa service account).
    key_managed: bool
    # A variável de ambiente correspondente está preenchida no processo da API.
    env_configured: bool
    # O gateway tem adapter registrado para este provider (maritaca depende da
    # allowlist de base URLs); sem adapter nenhuma chave é usada.
    adapter_registered: bool
    effective_source: Literal["blackbox", "environment", "none"]
    reason: str | None = None


class PlatformAiCredentialsRead(CamelCaseSchema):
    gate: PlatformAiGateRead
    providers: list[PlatformAiCredentialStatusRead]
