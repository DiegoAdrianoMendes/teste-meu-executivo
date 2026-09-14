"""
Endpoints: Importador Universal de Migração (Onda 3, Plano Mestre §12).

Gate de capability `admin`/`manage` para toda ação mutante (criar job,
importar, reconciliar, decidir corte, reprocessar, resolver divergência,
rollback) — migração de dados de todo o workspace é operação sensível,
mesmo padrão de `admin`/`manage` usado em `app/api/v1/endpoints/
klm_operations.py` para operações administrativas equivalentes. Leitura
(status, prévia, divergências, semáforo) usa `admin`/`view`, que também
inclui Finance Manager.
"""

from __future__ import annotations

import json as json_module
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import AuthContext, get_current_workspace_id, get_db, require_permission
from app.core.request_body import read_bounded_upload
from app.models.migration_job import MigrationConflict, MigrationDomain, MigrationSourceSystem
from app.schemas.migration import (
    MigrationCutoverDecisionRequest,
    MigrationDivergenceRead,
    MigrationDivergenceResolveRequest,
    MigrationImportRequest,
    MigrationImportResult,
    MigrationJobListRead,
    MigrationJobRead,
    MigrationSemaphoreRead,
    MigrationStagingRecordRead,
    MigrationStagingRecordReprocessRequest,
)
from app.services.migration_service import MigrationService

router = APIRouter(prefix="/migration", tags=["Importador Universal de Migração"])

require_migration_view = require_permission("admin", "view")
require_migration_manage = require_permission("admin", "manage")


@router.post(
    "/jobs",
    response_model=MigrationJobRead,
    status_code=status.HTTP_201_CREATED,
    summary="Enviar arquivo (CSV/XLSX/ZIP/JSON) e iniciar um job de migração",
)
async def create_migration_job(
    file: UploadFile = File(...),
    source_system: MigrationSourceSystem = Form(default=MigrationSourceSystem.universal_csv),
    default_domain: Optional[str] = Form(default=None),
    field_mapping: Optional[str] = Form(default=None),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_migration_manage),
    db: AsyncSession = Depends(get_db),
) -> MigrationJobRead:
    content = await read_bounded_upload(
        file,
        max_bytes=settings.MIGRATION_UPLOAD_MAX_BYTES,
    )
    parsed_mapping = json_module.loads(field_mapping) if field_mapping else None
    job = await MigrationService(db, workspace_id).receive_and_stage(
        filename=file.filename or "arquivo",
        content_type=file.content_type,
        content=content,
        source_system=source_system,
        created_by=context.user_id,
        default_domain=default_domain,
        field_mapping=parsed_mapping,
    )
    await db.commit()
    await db.refresh(job)
    return MigrationJobRead.model_validate(job)


@router.get(
    "/jobs",
    response_model=MigrationJobListRead,
    summary="Listar jobs de migração do workspace",
)
async def list_migration_jobs(
    skip: int = 0,
    limit: int = 50,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_migration_view),
    db: AsyncSession = Depends(get_db),
) -> MigrationJobListRead:
    jobs = await MigrationService(db, workspace_id).list_jobs(skip=skip, limit=limit)
    return MigrationJobListRead(
        items=[MigrationJobRead.model_validate(job) for job in jobs], total=len(jobs)
    )


@router.get(
    "/jobs/{job_id}",
    response_model=MigrationJobRead,
    summary="Status detalhado de um job de migração",
)
async def get_migration_job(
    job_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_migration_view),
    db: AsyncSession = Depends(get_db),
) -> MigrationJobRead:
    job = await MigrationService(db, workspace_id).get_job(job_id)
    return MigrationJobRead.model_validate(job)


@router.post(
    "/jobs/{job_id}/previa",
    response_model=MigrationJobRead,
    summary="Calcular a prévia (contagens por domínio) do job validado",
)
async def build_migration_preview(
    job_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_migration_manage),
    db: AsyncSession = Depends(get_db),
) -> MigrationJobRead:
    job = await MigrationService(db, workspace_id).build_preview(job_id)
    await db.commit()
    await db.refresh(job)
    return MigrationJobRead.model_validate(job)


@router.post(
    "/jobs/{job_id}/importar",
    response_model=MigrationImportResult,
    summary="Importar um lote de registros pendentes (idempotente, retomável)",
)
async def import_migration_batch(
    job_id: UUID,
    payload: Optional[MigrationImportRequest] = None,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_migration_manage),
    db: AsyncSession = Depends(get_db),
) -> MigrationImportResult:
    batch_size = payload.batch_size if payload is not None else 50
    result = await MigrationService(db, workspace_id).import_batch(job_id, batch_size=batch_size)
    await db.commit()
    return MigrationImportResult(**result)


@router.post(
    "/jobs/{job_id}/reconciliacao",
    response_model=MigrationJobRead,
    summary="Reconciliar domínio a domínio e calcular o semáforo de corte",
)
async def reconcile_migration_job(
    job_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_migration_manage),
    db: AsyncSession = Depends(get_db),
) -> MigrationJobRead:
    job = await MigrationService(db, workspace_id).reconcile(job_id)
    await db.commit()
    await db.refresh(job)
    return MigrationJobRead.model_validate(job)


@router.get(
    "/jobs/{job_id}/semaforo",
    response_model=MigrationSemaphoreRead,
    summary="Semáforo de corte (vermelho/amarelo/verde) + decisão registrada",
)
async def get_migration_semaphore(
    job_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_migration_view),
    db: AsyncSession = Depends(get_db),
) -> MigrationSemaphoreRead:
    payload = await MigrationService(db, workspace_id).get_semaphore(job_id)
    return MigrationSemaphoreRead(**payload)


@router.post(
    "/jobs/{job_id}/decisao-corte",
    response_model=MigrationJobRead,
    summary="Registrar a decisão humana de corte — nunca vira garantia de qualidade",
)
async def decide_migration_cutover(
    job_id: UUID,
    payload: MigrationCutoverDecisionRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_migration_manage),
    db: AsyncSession = Depends(get_db),
) -> MigrationJobRead:
    job = await MigrationService(db, workspace_id).decide_cutover(
        job_id, decided_by=context.user_id, decision=payload.decision, notes=payload.notes
    )
    await db.commit()
    await db.refresh(job)
    return MigrationJobRead.model_validate(job)


@router.post(
    "/jobs/{job_id}/rollback",
    response_model=MigrationJobRead,
    summary="Reverter o job inteiro — desfaz o que foi criado, preserva o que foi mesclado",
)
async def rollback_migration_job(
    job_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_migration_manage),
    db: AsyncSession = Depends(get_db),
) -> MigrationJobRead:
    job = await MigrationService(db, workspace_id).rollback(job_id, rolled_back_by=context.user_id)
    await db.commit()
    await db.refresh(job)
    return MigrationJobRead.model_validate(job)


@router.get(
    "/jobs/{job_id}/staging",
    response_model=List[MigrationStagingRecordRead],
    summary="Listar registros de staging do job, filtrando por domínio/conflito",
)
async def list_migration_staging_records(
    job_id: UUID,
    domain: Optional[MigrationDomain] = Query(default=None),
    conflict: Optional[MigrationConflict] = Query(default=None),
    skip: int = 0,
    limit: int = 100,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_migration_view),
    db: AsyncSession = Depends(get_db),
) -> List[MigrationStagingRecordRead]:
    records = await MigrationService(db, workspace_id).list_staging_records(
        job_id, domain=domain, conflict=conflict, skip=skip, limit=limit
    )
    return [MigrationStagingRecordRead.model_validate(record) for record in records]


@router.patch(
    "/jobs/{job_id}/staging/{record_id}/reprocessar",
    response_model=MigrationStagingRecordRead,
    summary="Corrigir manualmente um registro inválido/rejeitado e devolvê-lo à fila",
)
async def reprocess_migration_staging_record(
    job_id: UUID,
    record_id: UUID,
    payload: MigrationStagingRecordReprocessRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_migration_manage),
    db: AsyncSession = Depends(get_db),
) -> MigrationStagingRecordRead:
    record = await MigrationService(db, workspace_id).reprocess_record(
        job_id, record_id, normalized_patch=payload.normalized_patch
    )
    await db.commit()
    await db.refresh(record)
    return MigrationStagingRecordRead.model_validate(record)


@router.get(
    "/jobs/{job_id}/divergencias",
    response_model=List[MigrationDivergenceRead],
    summary="Fila de divergências do job",
)
async def list_migration_divergences(
    job_id: UUID,
    resolved: Optional[bool] = Query(default=None),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_migration_view),
    db: AsyncSession = Depends(get_db),
) -> List[MigrationDivergenceRead]:
    divergences = await MigrationService(db, workspace_id).list_divergences(
        job_id, resolved=resolved
    )
    return [MigrationDivergenceRead.model_validate(item) for item in divergences]


@router.post(
    "/jobs/{job_id}/divergencias/{divergence_id}/resolver",
    response_model=MigrationDivergenceRead,
    summary="Resolver uma divergência — autoria sempre do usuário autenticado",
)
async def resolve_migration_divergence(
    job_id: UUID,
    divergence_id: UUID,
    payload: MigrationDivergenceResolveRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_migration_manage),
    db: AsyncSession = Depends(get_db),
) -> MigrationDivergenceRead:
    divergence = await MigrationService(db, workspace_id).resolve_divergence(
        job_id,
        divergence_id,
        resolved_by=context.user_id,
        resolution_note=payload.resolution_note,
    )
    await db.commit()
    await db.refresh(divergence)
    return MigrationDivergenceRead.model_validate(divergence)
