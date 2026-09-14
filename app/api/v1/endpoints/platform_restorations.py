"""Preview-first contextual restoration API."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.identity_surface import has_active_platform_access
from app.core.platform_audit import record_canonical_platform_mutation
from app.core.platform_rbac import is_platform_root
from app.models.platform_restoration import PlatformRestorationJob, PlatformRestorationPreview
from app.models.user import User
from app.schemas.platform_restoration import (
    PlatformRestorationCreate,
    PlatformRestorationJobRead,
    PlatformRestorationPreviewCreate,
    PlatformRestorationPreviewRead,
)
from app.services.platform_restoration_service import (
    PlatformRestorationError,
    PlatformRestorationService,
)

router = APIRouter(prefix="/platform/restorations", tags=["Platform Restorations"])


async def require_restoration_actor(
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> User:
    actor = await db.get(User, context.user_id)
    if context.surface != "platform" or actor is None or not has_active_platform_access(actor):
        raise HTTPException(status_code=403, detail={"code": "BLACKBOX-IDENTITY-REQUIRED", "message": "Identidade Blackbox obrigatória."})
    return actor


def _raise(error: PlatformRestorationError) -> None:
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
    ) from error


def _assert_owner(actor: User, job: PlatformRestorationJob) -> None:
    if actor.id != job.actor_user_id and not is_platform_root(actor):
        raise HTTPException(status_code=403, detail={"code": "PLATFORM_RESTORE_FORBIDDEN", "message": "Restauração pertence a outro operador."})


def _preview_snapshot(preview: PlatformRestorationPreview) -> tuple[str, str, dict]:
    return (
        "restoration_preview",
        str(preview.id),
        {
            "resourceType": preview.resource_type,
            "resourceId": preview.resource_id,
            "currentVersion": preview.current_version,
            "targetVersion": preview.target_version,
            "eligible": preview.eligible,
            "warningCount": len(preview.warnings or []),
            "ineligibleReasonCount": len(preview.ineligible_reasons or []),
            "expiresAt": preview.expires_at,
            "consumedAt": preview.consumed_at,
        },
    )


def _job_snapshot(job: PlatformRestorationJob) -> tuple[str, str, dict]:
    return (
        "restoration_job",
        str(job.id),
        {
            "previewId": str(job.preview_id),
            "status": job.status,
            "errorCode": job.error_code,
            "startedAt": job.started_at,
            "completedAt": job.completed_at,
        },
    )


async def _audit(
    request: Request,
    context: AuthContext,
    db: AsyncSession,
    *,
    before_snapshot: tuple[str, str, dict] | None,
    after_snapshot: tuple[str, str, dict],
    action: str,
    reason: str,
    reason_source: str,
) -> None:
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        module="restorations",
        action=action,
        reason=reason,
        reason_source=reason_source,
    )


@router.post("/previews", response_model=PlatformRestorationPreviewRead, status_code=status.HTTP_201_CREATED)
async def create_restoration_preview(
    payload: PlatformRestorationPreviewCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_restoration_actor),
    context: AuthContext = Depends(get_current_auth_context),
) -> PlatformRestorationPreviewRead:
    try:
        preview = await PlatformRestorationService(db).create_preview(
            actor=actor, source_audit_event_id=payload.source_audit_event_id,
            resource_type=payload.resource_type, resource_id=payload.resource_id,
            target_version=payload.target_version,
        )
    except PlatformRestorationError as error:
        _raise(error)
    await _audit(
        request,
        context,
        db,
        before_snapshot=None,
        after_snapshot=_preview_snapshot(preview),
        action="preview.created",
        reason="Prévia contextual de restauração criada.",
        reason_source="system",
    )
    await db.refresh(preview)
    return PlatformRestorationPreviewRead.model_validate(preview)


@router.post("", response_model=PlatformRestorationJobRead, status_code=status.HTTP_202_ACCEPTED)
async def create_restoration(
    payload: PlatformRestorationCreate,
    request: Request,
    idempotency_key: str = Header(min_length=8, max_length=120, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_restoration_actor),
    context: AuthContext = Depends(get_current_auth_context),
) -> PlatformRestorationJobRead:
    try:
        job = await PlatformRestorationService(db).create_job(
            actor=actor, preview_id=payload.preview_id,
            expected_resource_version=payload.expected_resource_version,
            reason=payload.reason, confirmation=payload.confirmation,
            idempotency_key=idempotency_key,
        )
        created_for_request = getattr(job, "_created_for_request", False)
        await _audit(
            request,
            context,
            db,
            before_snapshot=None if created_for_request else _job_snapshot(job),
            after_snapshot=_job_snapshot(job),
            action="requested",
            reason=payload.reason,
            reason_source="user",
        )
        if created_for_request:
            await db.commit()
            from app.workers.platform_restoration_tasks import run_platform_restoration
            try:
                run_platform_restoration.apply_async(args=[str(job.id)])
            except Exception:
                before_dispatch = _job_snapshot(job)
                job.status = "failed"
                job.error_code = "ASYNC_WORKER_UNAVAILABLE"
                job.error_detail = "Worker assíncrono indisponível no despacho."
                job.completed_at = datetime.now(UTC)
                await _audit(
                    request,
                    context,
                    db,
                    before_snapshot=before_dispatch,
                    after_snapshot=_job_snapshot(job),
                    action="dispatch_failed",
                    reason="Worker assíncrono indisponível no despacho da restauração.",
                    reason_source="system",
                )
                await db.commit()
        await db.refresh(job)
    except PlatformRestorationError as error:
        _raise(error)
    return PlatformRestorationJobRead.model_validate(job)


@router.get("/{job_id}", response_model=PlatformRestorationJobRead)
async def get_restoration(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_restoration_actor),
) -> PlatformRestorationJobRead:
    try:
        job = await PlatformRestorationService(db).get_job(job_id)
    except PlatformRestorationError as error:
        _raise(error)
    _assert_owner(actor, job)
    return PlatformRestorationJobRead.model_validate(job)


@router.post("/{job_id}/cancel", response_model=PlatformRestorationJobRead, status_code=status.HTTP_202_ACCEPTED)
async def cancel_restoration(
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_restoration_actor),
    context: AuthContext = Depends(get_current_auth_context),
) -> PlatformRestorationJobRead:
    service = PlatformRestorationService(db)
    try:
        current = await service.get_job(job_id)
        _assert_owner(actor, current)
        before = _job_snapshot(current)
        job = await service.request_cancel(job_id)
        await _audit(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=_job_snapshot(job),
            action="cancelled",
            reason=job.reason,
            reason_source="user",
        )
        await db.refresh(job)
    except PlatformRestorationError as error:
        _raise(error)
    return PlatformRestorationJobRead.model_validate(job)
