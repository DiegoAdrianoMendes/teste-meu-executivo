"""Tenant-scoped API for versioned task reminder rules."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.tasks import _allow_workspace_task_access, _mutation_context
from app.api.v1.entitlement_errors import entitlement_http_exception
from app.core.deps import (
    AuthContext,
    get_current_auth_context,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.schemas.task_reminder import (
    TaskReminderDeleteRead,
    TaskReminderEventPage,
    TaskReminderRuleCreate,
    TaskReminderRuleRead,
    TaskReminderRuleUpdate,
)
from app.services.entitlement_service import EntitlementError
from app.services.task_reminder_service import (
    TaskReminderMutationContext,
    TaskReminderService,
)

router = APIRouter(tags=["Tarefas - lembretes"])

IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=180)]


async def _context(
    request: Request, db: AsyncSession, auth: AuthContext
) -> TaskReminderMutationContext:
    return TaskReminderMutationContext.from_task_context(
        await _mutation_context(request, db, auth)
    )


@router.get(
    "/tasks/{task_id}/reminder-rules",
    response_model=list[TaskReminderRuleRead],
    summary="Listar regras de lembrete da tarefa",
)
async def list_task_reminder_rules(
    task_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    __: object = Depends(require_permission("tasks", "view")),
):
    return await TaskReminderService(db).list_rules(
        workspace_id=workspace_id,
        task_id=task_id,
        actor_user_id=auth.user_id,
        allow_workspace_access=_allow_workspace_task_access(auth),
    )


@router.post(
    "/tasks/{task_id}/reminder-rules",
    response_model=TaskReminderRuleRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar regra de lembrete da tarefa",
)
async def create_task_reminder_rule(
    request: Request,
    response: Response,
    task_id: UUID,
    data: TaskReminderRuleCreate,
    idempotency_key: IdempotencyKey,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("tasks", "edit")),
):
    try:
        result, replayed = await TaskReminderService(db).create_rule(
            workspace_id=workspace_id,
            task_id=task_id,
            data=data,
            idempotency_key=idempotency_key,
            context=await _context(request, db, auth),
            allow_workspace_access=_allow_workspace_task_access(auth),
        )
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    response.headers["Idempotent-Replayed"] = str(replayed).lower()
    return result


@router.patch(
    "/tasks/{task_id}/reminder-rules/{rule_id}",
    response_model=TaskReminderRuleRead,
    summary="Atualizar regra de lembrete com CAS",
)
async def update_task_reminder_rule(
    request: Request,
    response: Response,
    task_id: UUID,
    rule_id: UUID,
    data: TaskReminderRuleUpdate,
    idempotency_key: IdempotencyKey,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("tasks", "edit")),
):
    result, replayed = await TaskReminderService(db).update_rule(
        workspace_id=workspace_id,
        task_id=task_id,
        rule_id=rule_id,
        data=data,
        idempotency_key=idempotency_key,
        context=await _context(request, db, auth),
        allow_workspace_access=_allow_workspace_task_access(auth),
    )
    response.headers["Idempotent-Replayed"] = str(replayed).lower()
    return result


@router.delete(
    "/tasks/{task_id}/reminder-rules/{rule_id}",
    response_model=TaskReminderDeleteRead,
    summary="Remover regra de lembrete com CAS",
)
async def delete_task_reminder_rule(
    request: Request,
    response: Response,
    task_id: UUID,
    rule_id: UUID,
    idempotency_key: IdempotencyKey,
    expected_version: int = Query(alias="expectedVersion", ge=1),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("tasks", "edit")),
):
    result, replayed = await TaskReminderService(db).delete_rule(
        workspace_id=workspace_id,
        task_id=task_id,
        rule_id=rule_id,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        context=await _context(request, db, auth),
        allow_workspace_access=_allow_workspace_task_access(auth),
    )
    response.headers["Idempotent-Replayed"] = str(replayed).lower()
    return result


@router.get(
    "/tasks/{task_id}/reminder-rules/{rule_id}/events",
    response_model=TaskReminderEventPage,
    summary="Listar histórico imutável da regra de lembrete",
)
async def list_task_reminder_events(
    task_id: UUID,
    rule_id: UUID,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    __: object = Depends(require_permission("tasks", "view")),
):
    items, total = await TaskReminderService(db).list_events(
        workspace_id=workspace_id,
        task_id=task_id,
        rule_id=rule_id,
        actor_user_id=auth.user_id,
        allow_workspace_access=_allow_workspace_task_access(auth),
        limit=limit,
        offset=offset,
    )
    return TaskReminderEventPage(items=items, total=total)


__all__ = ["router"]
