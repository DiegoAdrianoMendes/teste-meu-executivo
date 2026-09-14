"""Schemas: pasta de protocolo + manifesto (Onda 7, Plano Mestre §10).

Ver app/services/protocol_folder_service.py — a plataforma PREPARA e
VALIDA; não existe, neste módulo, nenhum schema de request para
"protocolar" (o único verbo de escrita além da montagem é o registro
POSTERIOR do recibo, `ProtocolReceiptRegisterRequest`).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.schemas.base import CamelCaseSchema, WriteSchema


class ProtocolFolderBuildRequest(WriteSchema):
    artifact_id: UUID
    tribunal: str | None = Field(default=None, max_length=160)
    signature_confirmed: bool = False


class ProtocolFolderFileRead(CamelCaseSchema):
    order: int
    folder: str
    filename: str
    source_type: str
    source_id: UUID
    sha256: str
    size_bytes: int
    extension: str


class ProtocolFolderRead(CamelCaseSchema):
    id: UUID
    legal_demand_id: UUID
    matter_id: UUID
    artifact_version_id: UUID
    tribunal: str
    act_type: str
    state: str
    manifest: dict[str, Any]
    files: list[dict[str, Any]]
    signature_confirmed: bool
    built_by_user_id: UUID | None = None
    built_at: datetime | None = None
    receipt_registered_at: datetime | None = None
    receipt_registered_by_user_id: UUID | None = None
    receipt_reference: str | None = None
    receipt_note: str | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def is_protocoled(self) -> bool:
        return self.receipt_registered_at is not None


class ProtocolReceiptRegisterRequest(WriteSchema):
    """Registro POSTERIOR e exclusivamente humano do recibo real — nunca
    executa nem simula um protocolo, apenas anota que ele já aconteceu
    fora da plataforma."""

    reference: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=4000)
    protocoled_at: datetime | None = None
