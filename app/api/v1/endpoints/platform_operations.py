"""Preview-first persisted bulk-operation endpoints for the Blackbox."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.platform_audit import record_canonical_platform_mutation
from app.core.platform_access import require_platform_permission
from app.core.platform_rbac import has_platform_permission, is_platform_root
from app.models.user import User
from app.models.platform_operations import PlatformBulkJob
from app.schemas.platform_operations import (
    PlatformBulkJobCreate,
    PlatformBulkJobPage,
    PlatformBulkJobRead,
    PlatformBulkPreviewCreate,
    PlatformBulkPreviewRead,
    PlatformBulkRestorationCreate,
    PlatformSelectionCreate,
    PlatformSelectionRead,
)
from app.services.platform_bulk_service import PlatformBulkError, PlatformBulkService

router = APIRouter(prefix="/platform/operations", tags=["Platform Operations"])
logger = logging.getLogger(__name__)
_bulk_view = require_platform_permission("bulk_operations", "view")
_bulk_manage = require_platform_permission("bulk_operations", "manage")


def _selection_snapshot(selection) -> tuple[str, str, dict]:
    return (
        "bulk_selection",
        str(selection.id),
        {
            "resourceType": selection.resource_type,
            "mode": selection.mode,
            "filterHash": selection.filter_hash,
            "filterKeys": sorted((selection.filters or {}).keys()),
            "excludedCount": len(selection.excluded_ids or []),
            "targetCount": selection.target_count,
            "status": selection.status,
            "expiresAt": selection.expires_at,
        },
    )


def _preview_snapshot(preview) -> tuple[str, str, dict]:
    return (
        "bulk_preview",
        str(preview.id),
        {
            "selectionSetId": str(preview.selection_set_id),
            "resourceType": preview.resource_type,
            "action": preview.action,
            "status": preview.status,
            "risk": preview.risk,
            "targetCount": preview.target_count,
            "eligibleCount": preview.eligible_count,
            "noOpCount": preview.no_op_count,
            "blockedCount": preview.blocked_count,
            "warningCodes": [item.get("code") for item in (preview.warnings or [])[:50]],
            "expiresAt": preview.expires_at,
            "consumedAt": preview.consumed_at,
        },
    )


def _job_snapshot(job) -> tuple[str, str, dict]:
    return (
        "bulk_job",
        str(job.id),
        {
            "previewId": str(job.preview_id),
            "selectionSetId": str(job.selection_set_id),
            "resourceType": job.resource_type,
            "action": job.action,
            "status": job.status,
            "risk": job.risk,
            "requestedCount": job.requested_count,
            "eligibleCount": job.eligible_count,
            "processedCount": job.processed_count,
            "succeededCount": job.succeeded_count,
            "failedCount": job.failed_count,
            "conflictedCount": job.conflicted_count,
            "skippedCount": job.skipped_count,
            "cancelledCount": job.cancelled_count,
            "cancelRequestedAt": job.cancel_requested_at,
            "completedAt": job.completed_at,
        },
    )


async def _record_control_audit(
    request: Request,
    context: AuthContext,
    db: AsyncSession,
    *,
    before_snapshot: tuple[str, str, dict] | None,
    after_snapshot: tuple[str, str, dict] | None,
    action: str,
    reason: str,
    reason_source: str = "system",
) -> None:
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        module="bulk_operations",
        action=action,
        reason=reason,
        reason_source=reason_source,
    )


def _raise_bulk_error(error: PlatformBulkError) -> None:
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
    ) from error


def _require_resource_permission(actor: User, resource_type: str, action: str) -> None:
    module = _resource_module(resource_type)
    if not has_platform_permission(actor, module, action):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "BULK_RESOURCE_FORBIDDEN",
                "message": "O pacote não autoriza operação em massa neste domínio.",
            },
        )


def _resource_module(resource_type: str) -> str:
    return {
        "workspace": "customers",
        "customer_user": "customers",
        "staff": "staff",
        "lead": "sales",
        "ticket": "support",
        "subscription": "billing",
        "workspace_invitation": "customers",
        "staff_invitation": "staff",
    }[resource_type]


def _visible_resource_types(actor: User) -> set[str]:
    resource_types = {
        "workspace",
        "customer_user",
        "staff",
        "lead",
        "ticket",
        "subscription",
        "workspace_invitation",
        "staff_invitation",
    }
    return {
        resource_type
        for resource_type in resource_types
        if has_platform_permission(actor, _resource_module(resource_type), "view")
    }


@router.post(
    "/selections",
    response_model=PlatformSelectionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Materialize a frozen workspace selection",
)
async def create_platform_selection(
    payload: PlatformSelectionCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_bulk_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> PlatformSelectionRead:
    _require_resource_permission(actor, payload.resource_type, "view")
    service = PlatformBulkService(db)
    try:
        selection = await service.create_selection(
            actor_user_id=actor.id,
            resource_type=payload.resource_type,
            mode=payload.mode,
            ids=payload.ids,
            filters=payload.filters,
            sort=[item.model_dump() for item in payload.sort],
            excluded_ids=payload.excluded_ids,
        )
    except PlatformBulkError as error:
        _raise_bulk_error(error)
    await _record_control_audit(
        request,
        context,
        db,
        before_snapshot=None,
        after_snapshot=_selection_snapshot(selection),
        action="selection.create",
        reason="Seleção congelada criada para operação em massa.",
    )
    return PlatformSelectionRead(**service.selection_payload(selection))


@router.get("/selections/{selection_id}", response_model=PlatformSelectionRead)
async def get_platform_selection(
    selection_id: UUID,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_bulk_view),
) -> PlatformSelectionRead:
    service = PlatformBulkService(db)
    try:
        selection = await service.get_selection(selection_id)
        if selection.actor_user_id != actor.id:
            raise PlatformBulkError(
                "BULK_SELECTION_FORBIDDEN", "Selection belongs to another actor.", status_code=403
            )
    except PlatformBulkError as error:
        _raise_bulk_error(error)
    return PlatformSelectionRead(**service.selection_payload(selection))


@router.post(
    "/bulk/previews",
    response_model=PlatformBulkPreviewRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_platform_bulk_preview(
    payload: PlatformBulkPreviewCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_bulk_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> PlatformBulkPreviewRead:
    service = PlatformBulkService(db)
    try:
        selection = await service.get_selection(payload.selection_set_id)
        _require_resource_permission(actor, selection.resource_type, "manage")
        if selection.target_count > 100:
            preview = await service.create_deferred_preview(
                actor_user_id=actor.id,
                selection_set_id=payload.selection_set_id,
                action=payload.action,
                parameters=payload.parameters,
            )
            await db.commit()
            from app.workers.platform_bulk_tasks import build_platform_bulk_preview

            try:
                build_platform_bulk_preview.apply_async(args=[str(preview.id)])
            except Exception:
                # A intenção já foi persistida. O recovery periódico
                # reenfileira previews em building sem duplicar o cálculo.
                logger.error(
                    "bulk_preview_enqueue_failed", extra={"preview_id": str(preview.id)}
                )
        else:
            preview = await service.create_preview(
                actor_user_id=actor.id,
                selection_set_id=payload.selection_set_id,
                action=payload.action,
                parameters=payload.parameters,
            )
    except PlatformBulkError as error:
        _raise_bulk_error(error)
    await _record_control_audit(
        request,
        context,
        db,
        before_snapshot=None,
        after_snapshot=_preview_snapshot(preview),
        action="preview.create",
        reason="Prévia de impacto criada para operação em massa.",
    )
    return PlatformBulkPreviewRead(**service.preview_payload(preview))


@router.get("/bulk/previews/{preview_id}", response_model=PlatformBulkPreviewRead)
async def get_platform_bulk_preview(
    preview_id: UUID,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_bulk_view),
) -> PlatformBulkPreviewRead:
    service = PlatformBulkService(db)
    try:
        preview = await service.get_preview(preview_id)
        if preview.actor_user_id != actor.id:
            raise PlatformBulkError(
                "BULK_PREVIEW_FORBIDDEN", "Preview belongs to another actor.", status_code=403
            )
        _require_resource_permission(actor, preview.resource_type, "view")
    except PlatformBulkError as error:
        _raise_bulk_error(error)
    return PlatformBulkPreviewRead(**service.preview_payload(preview))


@router.post(
    "/bulk/jobs",
    response_model=PlatformBulkJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_platform_bulk_job(
    payload: PlatformBulkJobCreate,
    request: Request,
    idempotency_key: str = Header(min_length=8, max_length=120, alias="Idempotency-Key"),
    local_dispatch_delay_ms: int = Header(
        default=0,
        ge=0,
        le=5_000,
        alias="X-KLM-Local-Dispatch-Delay-Ms",
    ),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_bulk_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> PlatformBulkJobRead:
    if local_dispatch_delay_ms and not (settings.uses_local_infrastructure and is_platform_root(actor)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O atraso de despacho só existe no runtime local isolado para o titular da plataforma.",
        )
    service = PlatformBulkService(db)
    try:
        preview = await service.get_preview(payload.preview_id)
        _require_resource_permission(actor, preview.resource_type, "manage")
        existing = await db.scalar(
            select(PlatformBulkJob).where(
                PlatformBulkJob.actor_user_id == actor.id,
                PlatformBulkJob.idempotency_key == idempotency_key,
            )
        )
        created_for_request = existing is None
        before_snapshot = _job_snapshot(existing) if existing is not None else None
        job = await service.create_job(
            actor_user_id=actor.id,
            preview_id=payload.preview_id,
            reason=payload.reason,
            confirmation_phrase=payload.confirmation_phrase,
            idempotency_key=idempotency_key,
        )
        await _record_control_audit(
            request,
            context,
            db,
            before_snapshot=before_snapshot,
            after_snapshot=_job_snapshot(job),
            action="job.request",
            reason=payload.reason,
            reason_source="user",
        )
        # Torna a intenção durável antes de publicar a mensagem. O recovery
        # periódico reenfileira jobs queued caso o broker esteja indisponível
        # neste exato intervalo.
        await db.commit()
        # Import tardio: evita carregar o grafo inteiro do Celery no startup da
        # API e elimina o ciclo endpoint -> task -> celery_app -> task.
        if created_for_request:
            from app.workers.platform_bulk_tasks import run_platform_bulk_job

            try:
                run_platform_bulk_job.apply_async(
                    args=[str(job.id)],
                    countdown=local_dispatch_delay_ms / 1_000,
                )
            except Exception:
                logger.error("bulk_job_enqueue_failed", extra={"job_id": str(job.id)})
                dispatch_before = _job_snapshot(job)
                now = datetime.now(UTC)
                pending_items = await service.list_job_items(job.id)
                for item in pending_items:
                    if item.status != "pending":
                        continue
                    item.status = "failed"
                    item.error_code = "ASYNC_WORKER_UNAVAILABLE"
                    item.error_detail = "Worker assíncrono indisponível no despacho."
                    item.processed_at = now
                job.status = "failed"
                job.processed_count = len(pending_items)
                job.failed_count = len(pending_items)
                job.completed_at = now
                job.heartbeat_at = now
                await _record_control_audit(
                    request,
                    context,
                    db,
                    before_snapshot=dispatch_before,
                    after_snapshot=_job_snapshot(job),
                    action="job.dispatch_failed",
                    reason="Worker assíncrono indisponível no despacho do lote.",
                    reason_source="system",
                )
                await db.commit()
        items = await service.list_job_items(job.id)
    except PlatformBulkError as error:
        _raise_bulk_error(error)
    return PlatformBulkJobRead(**service.job_payload(job, items=items))


@router.get("/bulk/jobs", response_model=PlatformBulkJobPage)
async def list_platform_bulk_jobs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_bulk_view),
) -> PlatformBulkJobPage:
    service = PlatformBulkService(db)
    jobs, total = await service.list_jobs(
        limit=limit,
        offset=offset,
        resource_types=_visible_resource_types(actor),
    )
    return PlatformBulkJobPage(
        items=[PlatformBulkJobRead(**service.job_payload(job)) for job in jobs],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/bulk/jobs/{job_id}", response_model=PlatformBulkJobRead)
async def get_platform_bulk_job(
    job_id: UUID,
    item_limit: int = Query(default=200, ge=1, le=1_000, alias="itemLimit"),
    item_offset: int = Query(default=0, ge=0, alias="itemOffset"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_bulk_view),
) -> PlatformBulkJobRead:
    service = PlatformBulkService(db)
    try:
        job = await service.get_job(job_id)
        _require_resource_permission(actor, job.resource_type, "view")
        items = await service.list_job_items(job_id, limit=item_limit, offset=item_offset)
    except PlatformBulkError as error:
        _raise_bulk_error(error)
    return PlatformBulkJobRead(**service.job_payload(job, items=items))


@router.post(
    "/bulk/jobs/{job_id}/cancel",
    response_model=PlatformBulkJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_platform_bulk_job(
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_bulk_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> PlatformBulkJobRead:
    service = PlatformBulkService(db)
    try:
        existing_job = await service.get_job(job_id)
        _require_resource_permission(actor, existing_job.resource_type, "manage")
        before_snapshot = _job_snapshot(existing_job)
        job = await service.request_cancel(job_id)
        await _record_control_audit(
            request,
            context,
            db,
            before_snapshot=before_snapshot,
            after_snapshot=_job_snapshot(job),
            action="job.cancel",
            reason=f"Cancelamento solicitado para o lote {job.action}.",
        )
        items = await service.list_job_items(job_id)
    except PlatformBulkError as error:
        _raise_bulk_error(error)
    return PlatformBulkJobRead(**service.job_payload(job, items=items))


@router.post(
    "/bulk/jobs/{job_id}/retry-failures",
    response_model=PlatformBulkPreviewRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_platform_bulk_retry_preview(
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_bulk_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> PlatformBulkPreviewRead:
    service = PlatformBulkService(db)
    try:
        original = await service.get_job(job_id)
        _require_resource_permission(actor, original.resource_type, "manage")
        preview = await service.create_retry_preview(
            actor_user_id=actor.id,
            original_job_id=job_id,
        )
        await _record_control_audit(
            request,
            context,
            db,
            before_snapshot=_job_snapshot(original),
            after_snapshot=_preview_snapshot(preview),
            action="retry.preview.create",
            reason=f"Revalidação das falhas do lote {original.action}.",
        )
    except PlatformBulkError as error:
        _raise_bulk_error(error)
    return PlatformBulkPreviewRead(**service.preview_payload(preview))


@router.post(
    "/bulk/jobs/{job_id}/restoration-preview",
    response_model=PlatformBulkPreviewRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_platform_bulk_restoration_preview(
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_bulk_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> PlatformBulkPreviewRead:
    service = PlatformBulkService(db)
    try:
        original = await service.get_job(job_id)
        _require_resource_permission(actor, original.resource_type, "manage")
        preview = await service.create_restoration_preview(
            actor_user_id=actor.id,
            original_job_id=job_id,
        )
        await _record_control_audit(
            request,
            context,
            db,
            before_snapshot=_job_snapshot(original),
            after_snapshot=_preview_snapshot(preview),
            action="restoration.preview.create",
            reason=f"Prévia de restauração criada para o lote {original.action}.",
        )
    except PlatformBulkError as error:
        _raise_bulk_error(error)
    return PlatformBulkPreviewRead(**service.preview_payload(preview))


@router.post(
    "/bulk/jobs/{job_id}/restore",
    response_model=PlatformBulkJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def restore_platform_bulk_job(
    job_id: UUID,
    payload: PlatformBulkRestorationCreate,
    request: Request,
    idempotency_key: str = Header(min_length=8, max_length=120, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_bulk_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> PlatformBulkJobRead:
    service = PlatformBulkService(db)
    try:
        original = await service.get_job(job_id)
        _require_resource_permission(actor, original.resource_type, "manage")
        preview = await service.get_preview(payload.preview_id)
        if preview.parameters.get("originalJobId") != str(job_id) or preview.action != "restore":
            raise PlatformBulkError(
                "BULK_RESTORE_PREVIEW_MISMATCH",
                "A prévia não pertence ao lote informado.",
                status_code=409,
            )
        existing = await db.scalar(
            select(PlatformBulkJob).where(
                PlatformBulkJob.actor_user_id == actor.id,
                PlatformBulkJob.idempotency_key == idempotency_key,
            )
        )
        before_snapshot = _job_snapshot(existing) if existing is not None else None
        job = await service.create_job(
            actor_user_id=actor.id,
            preview_id=payload.preview_id,
            reason=payload.reason,
            confirmation_phrase=payload.confirmation_phrase,
            idempotency_key=idempotency_key,
        )
        await _record_control_audit(
            request,
            context,
            db,
            before_snapshot=before_snapshot,
            after_snapshot=_job_snapshot(job),
            action="restoration.job.request",
            reason=payload.reason,
            reason_source="user",
        )
        await db.commit()
        from app.workers.platform_bulk_tasks import run_platform_bulk_job

        try:
            run_platform_bulk_job.apply_async(args=[str(job.id)])
        except Exception:
            logger.error("bulk_restore_enqueue_failed", extra={"job_id": str(job.id)})
        items = await service.list_job_items(job.id)
    except PlatformBulkError as error:
        _raise_bulk_error(error)
    return PlatformBulkJobRead(**service.job_payload(job, items=items))
