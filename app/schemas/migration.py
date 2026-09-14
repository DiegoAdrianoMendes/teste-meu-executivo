"""
Schemas Pydantic para o Importador Universal de Migração (Onda 3, §12).
Ver app/models/migration_job.py e app/services/migration_service.py.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import Field

from app.models.migration_job import (
    MigrationConflict,
    MigrationDivergenceSeverity,
    MigrationDomain,
    MigrationJobStatus,
    MigrationRecordStatus,
    MigrationSourceSystem,
)
from app.schemas.base import CamelCaseSchema, WriteSchema


class MigrationJobRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    source_system: MigrationSourceSystem
    status: MigrationJobStatus
    source_manifest: dict
    field_mapping: dict
    preview_summary: dict
    reconciliation_summary: dict
    semaphore: Optional[str] = None
    semaphore_computed_at: Optional[datetime] = None
    cutover_decision: Optional[dict] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    rolled_back_at: Optional[datetime] = None
    created_by: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime


class MigrationJobListRead(CamelCaseSchema):
    items: list[MigrationJobRead]
    total: int


class MigrationImportRequest(WriteSchema):
    batch_size: int = Field(default=50, ge=1, le=500)


class MigrationImportResult(CamelCaseSchema):
    processed: int
    remaining: int
    status: MigrationJobStatus


class MigrationSemaphoreRead(CamelCaseSchema):
    semaphore: Optional[str] = None
    semaphore_computed_at: Optional[datetime] = None
    reconciliation_summary: dict
    cutover_decision: Optional[dict] = None


class MigrationCutoverDecisionRequest(WriteSchema):
    """`decidedBy` NUNCA é aceito aqui — o endpoint sempre injeta o usuário
    autenticado antes de chamar o service (mesma lição do Risco 2 aplicada
    em `ExceptionResolveRequest`)."""

    decision: str = Field(pattern="^(prosseguir|nao_prosseguir)$")
    notes: Optional[str] = None


class MigrationStagingRecordRead(CamelCaseSchema):
    id: UUID
    job_id: UUID
    domain: MigrationDomain
    raw: dict
    normalized: dict
    dedupe_key: str
    weak_identity: bool
    conflict: MigrationConflict
    record_status: MigrationRecordStatus
    validation_error: Optional[str] = None
    target_id: Optional[UUID] = None
    target_table: Optional[str] = None
    imported_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class MigrationStagingRecordReprocessRequest(WriteSchema):
    normalized_patch: dict = Field(default_factory=dict)


class MigrationDivergenceRead(CamelCaseSchema):
    id: UUID
    job_id: UUID
    domain: Optional[MigrationDomain] = None
    tipo: str
    severity: MigrationDivergenceSeverity
    detalhe: str
    context: Optional[dict] = None
    resolvido: bool
    resolved_by: Optional[UUID] = None
    resolved_at: Optional[datetime] = None
    resolution_note: Optional[str] = None
    created_at: datetime


class MigrationDivergenceResolveRequest(WriteSchema):
    """`resolvedBy` NUNCA é aceito aqui — sempre o usuário autenticado."""

    resolution_note: Optional[str] = None
