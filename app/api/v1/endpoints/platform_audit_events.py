"""Consulta paginada da trilha canônica de auditoria do Blackbox."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.platform_access import require_platform_permission
from app.models.user import User
from app.schemas.platform_audit_event import (
    PlatformAuditEventRead,
    PlatformAuditFieldDiff,
    PlatformAuditPage,
)
from app.services.platform_audit_event_service import (
    PlatformAuditEventService,
    changed_paths,
    is_audit_event_restorable,
    sanitize_audit_text,
    sanitize_audit_value,
    semantic_field_diff,
)

router = APIRouter(prefix="/platform/audit-events", tags=["Platform Audit"])
_audit_view = require_platform_permission("audit", "view")


@router.get("", response_model=PlatformAuditPage)
async def list_platform_audit_events(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None, max_length=500),
    module: str | None = Query(default=None, max_length=80),
    result: str | None = Query(default=None, max_length=32),
    workspace_id: UUID | None = Query(default=None, alias="workspaceId"),
    actor_id: UUID | None = Query(default=None, alias="actorId"),
    actor_role: str | None = Query(default=None, alias="actorRole", max_length=80),
    action: str | None = Query(default=None, max_length=120),
    resource_type: str | None = Query(default=None, alias="resourceType", max_length=120),
    correlation_id: str | None = Query(default=None, alias="correlationId", max_length=160),
    operation_id: str | None = Query(default=None, alias="operationId", max_length=160),
    occurred_from: datetime | None = Query(default=None, alias="from"),
    occurred_to: datetime | None = Query(default=None, alias="to"),
    restorable: bool | None = Query(default=None),
    search: str | None = Query(default=None, max_length=120),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_audit_view),
) -> PlatformAuditPage:
    items, next_cursor = await PlatformAuditEventService(db).list_events(
        limit=limit,
        cursor=cursor,
        module=module,
        result=result,
        workspace_id=workspace_id,
        actor_id=actor_id,
        actor_role=actor_role,
        action=action,
        resource_type=resource_type,
        correlation_id=correlation_id,
        operation_id=operation_id,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        restorable=restorable,
        search=search,
    )
    return PlatformAuditPage(
        items=[_safe_event_read(item) for item in items],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
    )


def _safe_event_read(item: object) -> PlatformAuditEventRead:
    """Apply current redaction rules when reading old as well as new events."""
    before = sanitize_audit_value(getattr(item, "before", None))
    after = sanitize_audit_value(getattr(item, "after", None))
    return PlatformAuditEventRead.model_validate(item).model_copy(
        update={
            "before": before,
            "after": after,
            "changed_paths": changed_paths(before, after),
            "field_diffs": [
                PlatformAuditFieldDiff.model_validate(diff)
                for diff in semantic_field_diff(before, after)
            ],
            "reason": sanitize_audit_text(str(getattr(item, "reason", ""))),
            "workspace_label_snapshot": (
                sanitize_audit_text(str(getattr(item, "workspace_label_snapshot")))
                if getattr(item, "workspace_label_snapshot", None) is not None
                else None
            ),
            "resource_label_snapshot": (
                sanitize_audit_text(str(getattr(item, "resource_label_snapshot")))
                if getattr(item, "resource_label_snapshot", None) is not None
                else None
            ),
            "restorable": is_audit_event_restorable(item),
        }
    )
