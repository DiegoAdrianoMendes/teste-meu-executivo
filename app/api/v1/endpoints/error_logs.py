"""
Endpoints de gerenciamento de error logs (admin only).
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.platform_audit import (
    record_canonical_platform_mutation,
    snapshot_platform_request_resource,
)
from app.core.platform_access import require_platform_module
from app.models.error_log import ErrorSeverity, ErrorStatus
from app.models.user import User
from app.services.error_tracking_service import ErrorTrackingService

router = APIRouter(prefix="/error-logs", tags=["error-logs"])
require_global_error_log_access = require_platform_module("errors")


class ErrorLogRead(BaseModel):
    id: UUID
    error_code: str
    error_hash: str
    severity: str
    status: str
    exception_type: str
    message: str
    location: str | None
    endpoint: str | None
    method: str | None
    occurrences: int
    first_seen_at: str
    last_seen_at: str
    resolved_at: str | None
    resolved_by: str | None
    resolution_notes: str | None

    model_config = {"from_attributes": True}


class UpdateStatusRequest(BaseModel):
    status: ErrorStatus
    resolved_by: str | None = None
    resolution_notes: str | None = None
    reason: str | None = None


@router.get("", response_model=dict)
async def list_error_logs(
    status_filter: ErrorStatus | None = Query(None, alias="status"),
    severity: ErrorSeverity | None = None,
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_global_error_log_access),
):
    errors, total = await ErrorTrackingService.list_errors(
        db, status=status_filter, severity=severity, skip=skip, limit=limit
    )

    def fmt(e):
        return {
            "id": str(e.id),
            "error_code": e.error_code,
            "error_hash": e.error_hash[:12],  # Primeiros 12 chars para exibição
            "severity": e.severity,
            "status": e.status,
            "exception_type": e.exception_type,
            "message": e.message[:300],
            "location": e.location,
            "endpoint": e.endpoint,
            "method": e.method,
            "occurrences": e.occurrences,
            "first_seen_at": e.first_seen_at.isoformat() if e.first_seen_at else None,
            "last_seen_at": e.last_seen_at.isoformat() if e.last_seen_at else None,
            "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
            "resolved_by": e.resolved_by,
            "resolution_notes": e.resolution_notes,
        }

    return {
        "items": [fmt(e) for e in errors],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.patch("/{error_id}/status")
async def update_error_status(
    error_id: UUID,
    body: UpdateStatusRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_global_error_log_access),
    context: AuthContext = Depends(get_current_auth_context),
):
    reason = (body.reason or body.resolution_notes or "").strip()
    if not 8 <= len(reason) <= 500:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "ERROR-LOG-REASON-REQUIRED",
                "message": "Informe um motivo de 8 a 500 caracteres.",
            },
        )
    before = await snapshot_platform_request_resource(request, db)
    error = await ErrorTrackingService.update_status(
        db,
        error_id=error_id,
        new_status=body.status,
        resolved_by=actor.name,
        resolution_notes=body.resolution_notes,
        commit=False,
    )
    if not error:
        raise HTTPException(status_code=404, detail="Error log not found")
    after = await snapshot_platform_request_resource(request, db)
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=after,
        module="errors",
        action="update_error_log_status",
        reason=reason,
    )
    return {"ok": True, "status": error.status}


@router.get("/summary")
async def error_summary(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_global_error_log_access),
):
    from sqlalchemy import func, select

    from app.models.error_log import ErrorLog

    # Contagem por status
    result = await db.execute(
        select(ErrorLog.status, func.count(ErrorLog.id))
        .group_by(ErrorLog.status)
    )
    by_status = {row[0]: row[1] for row in result}

    # Contagem por severity (só open/in_progress)
    result2 = await db.execute(
        select(ErrorLog.severity, func.count(ErrorLog.id))
        .where(ErrorLog.status != ErrorStatus.resolved)
        .group_by(ErrorLog.severity)
    )
    by_severity = {row[0]: row[1] for row in result2}

    return {
        "by_status": by_status,
        "by_severity": by_severity,
        "total_open": by_status.get(ErrorStatus.open, 0),
        "total_in_progress": by_status.get(ErrorStatus.in_progress, 0),
        "total_resolved": by_status.get(ErrorStatus.resolved, 0),
    }
