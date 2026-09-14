"""Contratos das credenciais BYOK de LLM por workspace.

A chave de API é write-only: entra no PUT e nunca volta em nenhuma resposta —
somente a máscara com os últimos 4 caracteres.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.schemas.base import CamelCaseSchema, WriteSchema


class WorkspaceAiCredentialWrite(WriteSchema):
    api_key: str = Field(min_length=8, max_length=4096)


class WorkspaceAiCredentialRead(CamelCaseSchema):
    provider: str
    configured: bool
    is_active: bool
    # Sempre redigida: "****" + últimos 4 caracteres. Nunca a chave em claro.
    api_key_masked: str
    updated_at: datetime | None = None


class WorkspaceAiCredentialStatusRead(CamelCaseSchema):
    """Status por provider na listagem — máscara opcional quando não configurado."""

    provider: str
    configured: bool
    is_active: bool
    api_key_masked: str | None = None
    updated_at: datetime | None = None
