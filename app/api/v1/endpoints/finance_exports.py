from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    AuthContext,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.models.finance_dimensional import FinanceExportJob
from app.schemas.finance_export import FinanceExportCreate, FinanceExportRead
from app.services.finance_export_service import record_finance_export_event
from app.services.platform_export_service import PlatformExportError, PlatformExportService

router = APIRouter(prefix="/finance/exports", tags=["Financeiro — Exports"])


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def _export_deps(
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("finance", "export")),
    db: AsyncSession = Depends(get_db),
):
    return workspace_id, auth, db


@router.get("", response_model=list[FinanceExportRead])
async def list_exports(d=Depends(_export_deps)):
    ws, _, db = d
    return list(
        (
            await db.scalars(
                select(FinanceExportJob)
                .where(FinanceExportJob.workspace_id == ws)
                .order_by(FinanceExportJob.created_at.desc(), FinanceExportJob.id.desc())
                .limit(50)
            )
        ).all()
    )


@router.post("", response_model=FinanceExportRead, status_code=status.HTTP_202_ACCEPTED)
async def create_export(p: FinanceExportCreate, d=Depends(_export_deps)):
    ws, auth, db = d
    obj = FinanceExportJob(
        workspace_id=ws,
        actor_user_id=auth.user_id,
        format=p.format,
        status="queued",
        drill_context=p.drill_context,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    db.add(obj)
    await db.flush()
    record_finance_export_event(db, obj, "finance_export_requested")
    await db.commit()
    await db.refresh(obj)
    from app.workers.finance_export_tasks import run_finance_export

    try:
        # A fila indisponível deve falhar rápido e persistir um estado honesto;
        # o request HTTP não pode aguardar o ciclo interno de retries do broker.
        run_finance_export.apply_async(args=[str(obj.id), str(ws)], retry=False)
    except Exception:
        before = str(getattr(obj.status, "value", obj.status))
        obj.status = "failed"
        obj.error_detail = "FINANCE_EXPORT_ENQUEUE_FAILED"
        record_finance_export_event(
            db, obj, "finance_export_failed", before_status=before, error_code=obj.error_detail
        )
        await db.commit()
    return obj


@router.get("/{job_id}", response_model=FinanceExportRead)
async def get_export(job_id: UUID, d=Depends(_export_deps)):
    obj = await d[2].get(FinanceExportJob, job_id)
    if not obj or obj.workspace_id != d[0]:
        raise HTTPException(404, "Export não encontrada")
    if (
        obj.expires_at
        and _as_utc(obj.expires_at) < datetime.now(UTC)
        and obj.status not in ("expired", "cancelled")
    ):
        before = str(getattr(obj.status, "value", obj.status))
        obj.status = "expired"
        record_finance_export_event(d[2], obj, "finance_export_expired", before_status=before)
        await d[2].commit()
    return obj


@router.post("/{job_id}/cancel", response_model=FinanceExportRead)
async def cancel_export(job_id: UUID, d=Depends(_export_deps)):
    obj = await d[2].get(FinanceExportJob, job_id)
    if not obj or obj.workspace_id != d[0]:
        raise HTTPException(404, "Export não encontrada")
    if obj.status in ("queued", "running"):
        before = str(getattr(obj.status, "value", obj.status))
        obj.cancel_requested_at = datetime.now(UTC)
        obj.status = "cancelled"
        record_finance_export_event(d[2], obj, "finance_export_cancelled", before_status=before)
        await d[2].commit()
    return obj


@router.get("/{job_id}/download")
async def download_export(job_id: UUID, d=Depends(_export_deps)):
    obj = await d[2].get(FinanceExportJob, job_id)
    if not obj or obj.workspace_id != d[0]:
        raise HTTPException(404, "Export não encontrada")
    now = datetime.now(UTC)
    if obj.expires_at and _as_utc(obj.expires_at) <= now:
        before = str(getattr(obj.status, "value", obj.status))
        obj.status = "expired"
        record_finance_export_event(d[2], obj, "finance_export_expired", before_status=before)
        await d[2].commit()
        raise HTTPException(410, "Export expirada")
    if obj.status != "completed" or not obj.storage_path:
        raise HTTPException(409, "Export ainda não está disponível")
    try:
        artifact = await PlatformExportService(d[2]).authorize_download(obj)
    except PlatformExportError as error:
        raise HTTPException(error.status_code, error.message) from error
    media = {
        "pdf": "application/pdf",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }[obj.format]
    record_finance_export_event(
        d[2],
        obj,
        "finance_export_downloaded",
        before_status=str(getattr(obj.status, "value", obj.status)),
    )
    await d[2].commit()
    headers = {"X-Checksum-SHA256": obj.checksum_sha256 or "", "Cache-Control": "private, no-store"}
    if artifact.signed_url:
        return RedirectResponse(artifact.signed_url, status_code=307, headers=headers)
    return FileResponse(
        artifact.local_path,
        media_type=media,
        filename=f"finance-{obj.id}.{obj.format}",
        headers=headers,
    )
