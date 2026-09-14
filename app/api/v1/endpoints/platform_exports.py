"""Private asynchronous export API for the Blackbox."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.identity_surface import has_active_platform_access
from app.core.platform_audit import record_canonical_platform_mutation
from app.core.platform_rbac import is_platform_root
from app.models.user import User
from app.schemas.platform_export import PlatformExportCreate, PlatformExportPage, PlatformExportRead
from app.services.platform_export_service import (
    EXPORT_MEDIA_TYPES,
    PlatformExportError,
    PlatformExportService,
)

router = APIRouter(prefix="/platform/exports", tags=["Platform Exports"])


async def require_export_actor(
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> User:
    actor = await db.get(User, context.user_id)
    if context.surface != "platform" or actor is None or not has_active_platform_access(actor):
        raise HTTPException(status_code=403, detail={"code": "BLACKBOX-IDENTITY-REQUIRED", "message": "Identidade Blackbox obrigatória."})
    return actor


def _raise(error: PlatformExportError) -> None:
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
    ) from error


def _assert_owner(actor: User, actor_user_id: UUID) -> None:
    if actor.id != actor_user_id and not is_platform_root(actor):
        raise HTTPException(
            status_code=403,
            detail={"code": "PLATFORM_EXPORT_FORBIDDEN", "message": "Exportação pertence a outro operador."},
        )


def _export_snapshot(job) -> tuple[str, str, dict]:
    return (
        "platform_export",
        str(job.id),
        {
            "dataset": job.dataset,
            "format": job.format,
            "status": job.status,
            "processedRows": job.processed_rows,
            "rowCount": job.row_count,
            "byteSize": job.byte_size,
            "checksumSha256": job.checksum_sha256,
            "expiresAt": job.expires_at,
            "cancelRequestedAt": job.cancel_requested_at,
        },
    )


async def _audit_export(
    request: Request,
    context: AuthContext,
    db: AsyncSession,
    *,
    job,
    action: str,
    before_snapshot: tuple[str, str, dict] | None,
) -> None:
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before_snapshot,
        after_snapshot=_export_snapshot(job),
        module="exports",
        action=action,
        reason=f"Exportação administrativa {action}.",
        reason_source="system",
    )


@router.post("", response_model=PlatformExportRead, status_code=status.HTTP_202_ACCEPTED)
async def create_platform_export(
    payload: PlatformExportCreate,
    request: Request,
    idempotency_key: str = Header(min_length=8, max_length=120, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_export_actor),
    context: AuthContext = Depends(get_current_auth_context),
) -> PlatformExportRead:
    service = PlatformExportService(db)
    try:
        job = await service.create_job(
            actor=actor,
            dataset=payload.dataset,
            format=payload.format,
            filters=payload.filters,
            columns=payload.columns,
            selection_set_id=payload.selection_set_id,
            idempotency_key=idempotency_key,
        )
        created_for_request = getattr(job, "_created_for_request", False)
        await _audit_export(
            request,
            context,
            db,
            job=job,
            action="requested",
            before_snapshot=None if created_for_request else _export_snapshot(job),
        )
        if created_for_request:
            await db.commit()
            from app.workers.platform_export_tasks import run_platform_export

            try:
                run_platform_export.apply_async(args=[str(job.id)])
            except Exception:
                before_dispatch = _export_snapshot(job)
                job.status = "failed"
                job.error_code = "ASYNC_WORKER_UNAVAILABLE"
                job.error_detail = "Worker assíncrono indisponível no despacho."
                job.completed_at = datetime.now(UTC)
                await _audit_export(
                    request,
                    context,
                    db,
                    job=job,
                    action="dispatch_failed",
                    before_snapshot=before_dispatch,
                )
                await db.commit()
        await db.refresh(job)
    except PlatformExportError as error:
        _raise(error)
    return PlatformExportRead.model_validate(job)


@router.get("", response_model=PlatformExportPage)
async def list_platform_exports(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_export_actor),
) -> PlatformExportPage:
    jobs, total = await PlatformExportService(db).list_jobs(actor=actor, limit=limit, offset=offset)
    return PlatformExportPage(
        items=[PlatformExportRead.model_validate(job) for job in jobs],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{job_id}", response_model=PlatformExportRead)
async def get_platform_export(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_export_actor),
) -> PlatformExportRead:
    try:
        job = await PlatformExportService(db).get_job(job_id)
    except PlatformExportError as error:
        _raise(error)
    _assert_owner(actor, job.actor_user_id)
    PlatformExportService.authorize(actor, job.dataset)
    return PlatformExportRead.model_validate(job)


@router.post("/{job_id}/cancel", response_model=PlatformExportRead, status_code=status.HTTP_202_ACCEPTED)
async def cancel_platform_export(
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_export_actor),
    context: AuthContext = Depends(get_current_auth_context),
) -> PlatformExportRead:
    service = PlatformExportService(db)
    try:
        current = await service.get_job(job_id)
        _assert_owner(actor, current.actor_user_id)
        PlatformExportService.authorize(actor, current.dataset)
        before = _export_snapshot(current)
        job = await service.request_cancel(job_id)
        await _audit_export(
            request,
            context,
            db,
            job=job,
            action="cancelled",
            before_snapshot=before,
        )
        await db.refresh(job)
    except PlatformExportError as error:
        _raise(error)
    return PlatformExportRead.model_validate(job)


@router.get("/{job_id}/download")
async def download_platform_export(
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_export_actor),
    context: AuthContext = Depends(get_current_auth_context),
):
    service = PlatformExportService(db)
    try:
        job = await service.get_job(job_id)
        _assert_owner(actor, job.actor_user_id)
        service.authorize(actor, job.dataset)
    except PlatformExportError as error:
        _raise(error)
    if job.status != "completed" or not job.storage_path:
        raise HTTPException(status_code=409, detail={"code": "PLATFORM_EXPORT_NOT_READY", "message": "Arquivo ainda não está disponível."})
    expires_at = job.expires_at if job.expires_at and job.expires_at.tzinfo else job.expires_at.replace(tzinfo=UTC) if job.expires_at else None
    if expires_at is None or expires_at <= datetime.now(UTC):
        before = _export_snapshot(job)
        try:
            await service.delete_artifact(job.storage_path)
        except PlatformExportError:
            # O arquivo permanece privado e a autorização expira mesmo se a
            # limpeza física precisar ser retomada pelo reaper/lifecycle.
            pass
        job.status = "expired"
        await _audit_export(
            request,
            context,
            db,
            job=job,
            action="expired",
            before_snapshot=before,
        )
        await db.commit()
        raise HTTPException(status_code=410, detail={"code": "PLATFORM_EXPORT_EXPIRED", "message": "Arquivo expirado."})
    try:
        artifact = await service.authorize_download(job)
    except PlatformExportError as error:
        _raise(error)
    await _audit_export(
        request,
        context,
        db,
        job=job,
        action="downloaded",
        before_snapshot=_export_snapshot(job),
    )
    if artifact.signed_url is not None:
        return RedirectResponse(
            artifact.signed_url,
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Cache-Control": "no-store, private"},
        )
    if artifact.local_path is None:
        raise HTTPException(
            status_code=410,
            detail={
                "code": "PLATFORM_EXPORT_FILE_MISSING",
                "message": "Arquivo não está mais disponível.",
            },
        )
    return FileResponse(
        artifact.local_path,
        media_type=EXPORT_MEDIA_TYPES[job.format],
        filename=f"blackbox-{job.dataset}-{job.id}.{job.format}",
        headers={"Cache-Control": "no-store, private"},
    )
