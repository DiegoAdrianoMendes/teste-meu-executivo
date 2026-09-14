"""Blackbox task center over safe, rebuildable work-item projections."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.platform_access import require_platform_permission
from app.core.platform_audit import record_canonical_platform_mutation
from app.models.platform_work_item import PlatformWorkItem
from app.models.user import User
from app.schemas.platform_work_item import (
    PlatformWorkItemAssignRequest,
    PlatformWorkItemCommandRequest,
    PlatformWorkItemCommandResult,
    PlatformWorkItemCounts,
    PlatformWorkItemPage,
    PlatformWorkItemRead,
    PlatformWorkItemReconcileRequest,
    PlatformWorkItemReconcileResult,
    PlatformWorkItemSnoozeRequest,
)
from app.services.platform_work_item_service import (
    PlatformWorkItemError,
    PlatformWorkItemService,
)

router = APIRouter(prefix="/platform/work-items", tags=["Platform Work Items"])

_platform_tasks_view = require_platform_permission("platform_tasks", "view")
_platform_tasks_assign = require_platform_permission("platform_tasks", "assign")
_platform_tasks_edit = require_platform_permission("platform_tasks", "edit")
_settings_manage = require_platform_permission("settings", "manage")


def _snapshot(item: PlatformWorkItem) -> tuple[str, str, dict]:
    return (
        "platform_work_item",
        str(item.id),
        {
            "sourceType": item.source_type,
            "sourceId": str(item.source_id),
            "sourceVersion": item.source_version,
            "kind": item.kind,
            "state": item.state,
            "priority": item.priority,
            "workspaceId": str(item.workspace_id) if item.workspace_id else None,
            "assignedUserId": str(item.assigned_user_id) if item.assigned_user_id else None,
            "assignedTeamId": str(item.assigned_team_id) if item.assigned_team_id else None,
            "reviewerUserId": str(item.reviewer_user_id) if item.reviewer_user_id else None,
            "dueAt": item.due_at,
            "snoozedUntil": item.snoozed_until,
            "closedAt": item.closed_at,
            "projectionVersion": item.projection_version,
        },
    )


def _error(error: PlatformWorkItemError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": str(error)},
    )


@router.get("", response_model=PlatformWorkItemPage)
async def list_platform_work_items(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None, max_length=500),
    scope: Literal["mine", "team", "platform"] = Query(default="platform"),
    team_id: UUID | None = Query(default=None, alias="teamId"),
    states: list[str] | None = Query(default=None),
    priorities: list[str] | None = Query(default=None),
    source_types: list[str] | None = Query(default=None, alias="sourceTypes"),
    workspace_id: UUID | None = Query(default=None, alias="workspaceId"),
    assigned_user_id: UUID | None = Query(default=None, alias="assignedToUserId"),
    unassigned: bool | None = Query(default=None),
    search: str | None = Query(default=None, max_length=120),
    overdue: bool | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_platform_tasks_view),
) -> PlatformWorkItemPage:
    service = PlatformWorkItemService(db)
    try:
        items, next_cursor = await service.list(
            actor=actor,
            limit=limit,
            cursor=cursor,
            scope=scope,
            team_id=team_id,
            states=states,
            priorities=priorities,
            source_types=source_types,
            workspace_id=workspace_id,
            assigned_user_id=assigned_user_id,
            unassigned=unassigned,
            search=search,
            overdue=overdue,
        )
    except PlatformWorkItemError as error:
        raise _error(error) from error
    return PlatformWorkItemPage(
        items=[PlatformWorkItemRead.model_validate(item) for item in items],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        generated_at=datetime.now().astimezone(),
    )


@router.get("/counts", response_model=PlatformWorkItemCounts)
async def count_platform_work_items(
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_platform_tasks_view),
) -> PlatformWorkItemCounts:
    return PlatformWorkItemCounts.model_validate(
        await PlatformWorkItemService(db).counts(actor=actor)
    )


@router.get("/{item_id}", response_model=PlatformWorkItemRead)
async def get_platform_work_item(
    item_id: UUID,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_platform_tasks_view),
) -> PlatformWorkItemRead:
    service = PlatformWorkItemService(db)
    try:
        item = await service.get(actor=actor, item_id=item_id)
    except PlatformWorkItemError as error:
        raise _error(error) from error
    return PlatformWorkItemRead.model_validate(service.present(actor, item))


@router.post("/{item_id}/assign", response_model=PlatformWorkItemRead)
async def assign_platform_work_item(
    item_id: UUID,
    payload: PlatformWorkItemAssignRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_platform_tasks_assign),
    context: AuthContext = Depends(get_current_auth_context),
) -> PlatformWorkItemRead:
    service = PlatformWorkItemService(db)
    try:
        before = _snapshot(await service.get(actor=actor, item_id=item_id))
        item = await service.assign(
            actor=actor,
            item_id=item_id,
            assigned_user_id=payload.assigned_user_id,
            expected_projection_version=payload.expected_projection_version,
        )
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=_snapshot(item),
            module="platform_tasks",
            action="work_item.assign",
            reason="Responsável da tarefa operacional atualizado.",
            reason_source="system",
        )
    except PlatformWorkItemError as error:
        raise _error(error) from error
    return PlatformWorkItemRead.model_validate(service.present(actor, item))


@router.post("/{item_id}/snooze", response_model=PlatformWorkItemRead)
async def snooze_platform_work_item(
    item_id: UUID,
    payload: PlatformWorkItemSnoozeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_platform_tasks_edit),
    context: AuthContext = Depends(get_current_auth_context),
) -> PlatformWorkItemRead:
    service = PlatformWorkItemService(db)
    try:
        before = _snapshot(await service.get(actor=actor, item_id=item_id))
        item = await service.snooze(
            actor=actor,
            item_id=item_id,
            snoozed_until=payload.snoozed_until,
            expected_projection_version=payload.expected_projection_version,
        )
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=_snapshot(item),
            module="platform_tasks",
            action="work_item.snooze",
            reason=payload.reason,
            reason_source="user",
        )
    except PlatformWorkItemError as error:
        raise _error(error) from error
    return PlatformWorkItemRead.model_validate(service.present(actor, item))


@router.post(
    "/{item_id}/commands/{command}",
    response_model=PlatformWorkItemCommandResult,
)
async def dispatch_platform_work_item_command(
    item_id: UUID,
    command: str,
    payload: PlatformWorkItemCommandRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_platform_tasks_view),
    context: AuthContext = Depends(get_current_auth_context),
) -> PlatformWorkItemCommandResult:
    service = PlatformWorkItemService(db)
    try:
        item, previous_version = await service.dispatch_command(
            actor=actor,
            item_id=item_id,
            command=command,
            expected_source_version=payload.expected_source_version,
            reason=payload.reason,
            correlation_id=request.headers.get("X-Correlation-ID"),
            administrative_session_id=context.administrative_session_id,
            session_type=context.administrative_session_type,
            route=str(getattr(request.scope.get("route"), "path", request.url.path)),
        )
    except PlatformWorkItemError as error:
        raise _error(error) from error
    request.state.canonical_platform_audit_recorded = True
    return PlatformWorkItemCommandResult(
        work_item=PlatformWorkItemRead.model_validate(service.present(actor, item)),
        source_type=item.source_type,
        source_id=item.source_id,
        command=command,
        previous_source_version=previous_version,
        source_version=item.source_version,
    )


@router.post("/reconcile", response_model=PlatformWorkItemReconcileResult)
async def reconcile_platform_work_items(
    payload: PlatformWorkItemReconcileRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_settings_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> PlatformWorkItemReconcileResult:
    service = PlatformWorkItemService(db)
    try:
        before_count = int((await db.scalar(select(func.count(PlatformWorkItem.id)))) or 0)
        results = await service.reconcile(source_types=payload.source_types)
        after_count = int((await db.scalar(select(func.count(PlatformWorkItem.id)))) or 0)
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=(
                "platform_work_item_projection",
                "all",
                {"total": before_count},
            ),
            after_snapshot=(
                "platform_work_item_projection",
                "all",
                {
                    "total": after_count,
                    "adapters": [result.__dict__ for result in results],
                },
            ),
            module="dashboard",
            action="work_item.reconcile",
            reason="Reconciliação solicitada das projeções operacionais.",
            reason_source="system",
        )
    except PlatformWorkItemError as error:
        raise _error(error) from error
    return PlatformWorkItemReconcileResult(
        adapters=[result.__dict__ for result in results],
        projected_at=datetime.now().astimezone(),
    )
