"""Authoritative internal employee tasks for the Blackbox."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.platform_audit import record_canonical_platform_mutation
from app.core.platform_rbac import is_platform_root
from app.db.platform_control import set_platform_control_context
from app.models.platform_task import PlatformTask, PlatformTaskStatus
from app.models.user import User
from app.schemas.platform_task import (
    PlatformTaskAssignmentsReplace,
    PlatformTaskCommand,
    PlatformTaskCommandRequest,
    PlatformTaskCommentCreate,
    PlatformTaskCreate,
    PlatformTaskEventPage,
    PlatformTaskEventRead,
    PlatformTaskPage,
    PlatformTaskParticipantStateUpdate,
    PlatformTaskRead,
    PlatformTaskUpdate,
)
from app.services.platform_task_service import (
    PlatformTaskError,
    PlatformTaskMutationContext,
    PlatformTaskMutationResult,
    PlatformTaskService,
    has_platform_task_permission,
    required_platform_task_command_permission,
)

router = APIRouter(prefix="/platform/tasks", tags=["Platform Tasks"])


def require_platform_task_permission(action: str):
    async def dependency(
        context: AuthContext = Depends(get_current_auth_context),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        if context.surface != "platform":
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "BLACKBOX-IDENTITY-REQUIRED",
                    "message": "Esta rota pertence à Central Blackbox.",
                },
            )
        actor = await db.get(User, context.user_id)
        if not has_platform_task_permission(actor, action):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "PLATFORM-TASK-PERMISSION-DENIED",
                    "message": "Seu pacote de acesso não autoriza esta operação.",
                    "module": "platform_tasks",
                    "action": action,
                },
            )
        await set_platform_control_context(
            db,
            actor_user_id=actor.id,
            module="platform_tasks",
            action=action,
            is_platform_root=is_platform_root(actor),
        )
        return actor

    return dependency


async def require_platform_task_command_permission(
    command: PlatformTaskCommand,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Authorize and propagate the exact action required by ``command``."""

    if context.surface != "platform":
        raise HTTPException(
            status_code=403,
            detail={
                "code": "BLACKBOX-IDENTITY-REQUIRED",
                "message": "Esta rota pertence à Central Blackbox.",
            },
        )
    actor = await db.get(User, context.user_id)
    action = required_platform_task_command_permission(command)
    if not has_platform_task_permission(actor, action):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "PLATFORM-TASK-PERMISSION-DENIED",
                "message": "Seu pacote de acesso não autoriza esta operação.",
                "module": "platform_tasks",
                "action": action,
            },
        )
    await set_platform_control_context(
        db,
        actor_user_id=actor.id,
        module="platform_tasks",
        action=action,
        is_platform_root=is_platform_root(actor),
    )
    return actor


_view = require_platform_task_permission("view")
_create = require_platform_task_permission("create")
_edit = require_platform_task_permission("edit")
_assign = require_platform_task_permission("assign")
_comment = require_platform_task_permission("comment")
_command = require_platform_task_command_permission


def _raise(error: PlatformTaskError) -> None:
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
    ) from error


def _context(request: Request, auth: AuthContext, actor: User) -> PlatformTaskMutationContext:
    return PlatformTaskMutationContext.for_actor(
        actor,
        correlation_id=request.headers.get("X-Correlation-ID"),
        auth_context=auth,
    )


async def _task_read(
    service: PlatformTaskService,
    *,
    actor: User,
    task: PlatformTask,
) -> PlatformTaskRead:
    payload = PlatformTaskRead.model_validate(task)
    return payload.model_copy(
        update={"allowed_commands": await service.allowed_commands(actor=actor, task=task)}
    )


def _audit_workspace_id(mutation: PlatformTaskMutationResult) -> UUID | None:
    if mutation.workspace_id is not None:
        return mutation.workspace_id
    # Clearing a link must not erase the audit attribution that is still
    # determinable from the locked before-snapshot.
    for snapshot in (mutation.after, mutation.before):
        value = (snapshot or {}).get("workspaceId")
        if value:
            try:
                return UUID(str(value))
            except (TypeError, ValueError):
                continue
    return None


async def _audit_and_commit(
    *,
    request: Request,
    auth: AuthContext,
    db: AsyncSession,
    mutation: PlatformTaskMutationResult,
    action: str,
    reason: str,
) -> None:
    path_params = request.scope.setdefault("path_params", {})
    previous_workspace = path_params.get("workspace_id")
    had_workspace = "workspace_id" in path_params
    audit_workspace_id = _audit_workspace_id(mutation)
    if audit_workspace_id is not None:
        # The canonical recorder derives workspace attribution from path params.
        # Platform-task routes are keyed by task ID, so expose the workspace only
        # for the duration of this same-transaction audit call.
        path_params["workspace_id"] = audit_workspace_id
    try:
        await record_canonical_platform_mutation(
            request,
            auth,
            db,
            before_snapshot=(
                "platform_task",
                str(mutation.task.id),
                mutation.before,
            )
            if mutation.before is not None
            else None,
            after_snapshot=(
                "platform_task",
                str(mutation.task.id),
                mutation.after,
            )
            if mutation.after is not None
            else None,
            module="platform_tasks",
            action=action,
            reason=reason,
            reason_source="user",
        )
    finally:
        if had_workspace:
            path_params["workspace_id"] = previous_workspace
        else:
            path_params.pop("workspace_id", None)
    await db.commit()


@router.get("", response_model=PlatformTaskPage)
async def list_platform_tasks(
    scope: Literal["mine", "reviews", "team", "platform"] = Query(default="mine"),
    statuses: list[PlatformTaskStatus] | None = Query(default=None),
    priorities: list[str] | None = Query(default=None),
    workspace_id: UUID | None = Query(default=None, alias="workspaceId"),
    search: str | None = Query(default=None, max_length=120),
    overdue: bool | None = Query(default=None),
    unassigned: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_view),
) -> PlatformTaskPage:
    try:
        service = PlatformTaskService(db)
        items, total = await service.list(
            actor=actor,
            limit=limit,
            offset=offset,
            scope=scope,
            statuses=statuses,
            priorities=priorities,
            workspace_id=workspace_id,
            search=search,
            overdue=overdue,
            unassigned=unassigned,
        )
    except PlatformTaskError as error:
        _raise(error)
    return PlatformTaskPage(
        items=[await _task_read(service, actor=actor, task=item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < total,
    )


@router.get("/{task_id}", response_model=PlatformTaskRead)
async def get_platform_task(
    task_id: UUID,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_view),
) -> PlatformTaskRead:
    try:
        service = PlatformTaskService(db)
        task = await service.get(actor=actor, task_id=task_id)
    except PlatformTaskError as error:
        _raise(error)
    return await _task_read(service, actor=actor, task=task)


@router.post(
    "",
    response_model=PlatformTaskRead,
    status_code=status.HTTP_201_CREATED,
    openapi_extra={
        "x-platform-mutation": {
            "key": "platform_task.create",
            "module": "platform_tasks",
            "action": "create",
            "resourceType": "platform_task",
            "reasonPolicy": "required",
            "snapshotAdapter": "platform_task",
            "mutationKind": "command",
            "restorationAdapter": None,
            "idempotencyPolicy": "optional",
            "externalEffect": False,
            "auditSchemaVersion": 1,
        }
    },
)
async def create_platform_task(
    payload: PlatformTaskCreate,
    request: Request,
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_create),
) -> PlatformTaskRead:
    service = PlatformTaskService(db)
    try:
        mutation = await service.create(data=payload, context=_context(request, auth, actor))
        await _audit_and_commit(
            request=request,
            auth=auth,
            db=db,
            mutation=mutation,
            action="platform_task.created",
            reason=payload.reason,
        )
    except PlatformTaskError as error:
        _raise(error)
    return await _task_read(service, actor=actor, task=mutation.task)


@router.patch(
    "/{task_id}",
    response_model=PlatformTaskRead,
    openapi_extra={
        "x-platform-mutation": {
            "key": "platform_task.update",
            "module": "platform_tasks",
            "action": "edit",
            "resourceType": "platform_task",
            "reasonPolicy": "required",
            "snapshotAdapter": "platform_task",
            "mutationKind": "command",
            "restorationAdapter": None,
            "idempotencyPolicy": "optional",
            "externalEffect": False,
            "auditSchemaVersion": 1,
        }
    },
)
async def update_platform_task(
    task_id: UUID,
    payload: PlatformTaskUpdate,
    request: Request,
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_edit),
) -> PlatformTaskRead:
    service = PlatformTaskService(db)
    try:
        mutation = await service.update(
            task_id=task_id, data=payload, context=_context(request, auth, actor)
        )
        await _audit_and_commit(
            request=request,
            auth=auth,
            db=db,
            mutation=mutation,
            action="platform_task.updated",
            reason=payload.reason,
        )
    except PlatformTaskError as error:
        _raise(error)
    return await _task_read(service, actor=actor, task=mutation.task)


@router.put(
    "/{task_id}/assignments",
    response_model=PlatformTaskRead,
    openapi_extra={
        "x-platform-mutation": {
            "key": "platform_task.assignments.replace",
            "module": "platform_tasks",
            "action": "assign",
            "resourceType": "platform_task",
            "reasonPolicy": "required",
            "snapshotAdapter": "platform_task",
            "mutationKind": "command",
            "restorationAdapter": None,
            "idempotencyPolicy": "optional",
            "externalEffect": False,
            "auditSchemaVersion": 1,
        }
    },
)
async def replace_platform_task_assignments(
    task_id: UUID,
    payload: PlatformTaskAssignmentsReplace,
    request: Request,
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_assign),
) -> PlatformTaskRead:
    service = PlatformTaskService(db)
    try:
        mutation = await service.replace_assignments(
            task_id=task_id, data=payload, context=_context(request, auth, actor)
        )
        await _audit_and_commit(
            request=request,
            auth=auth,
            db=db,
            mutation=mutation,
            action="platform_task.assignments_changed",
            reason=payload.reason,
        )
    except PlatformTaskError as error:
        _raise(error)
    return await _task_read(service, actor=actor, task=mutation.task)


@router.put(
    "/{task_id}/participants/{user_id}/state",
    response_model=PlatformTaskRead,
    openapi_extra={
        "x-platform-mutation": {
            "key": "platform_task.participant.state",
            "module": "platform_tasks",
            "action": "edit",
            "resourceType": "platform_task",
            "reasonPolicy": "required",
            "snapshotAdapter": "platform_task",
            "mutationKind": "command",
            "restorationAdapter": None,
            "idempotencyPolicy": "optional",
            "externalEffect": False,
            "auditSchemaVersion": 1,
        }
    },
)
async def update_platform_task_participant_state(
    task_id: UUID,
    user_id: UUID,
    payload: PlatformTaskParticipantStateUpdate,
    request: Request,
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_edit),
) -> PlatformTaskRead:
    service = PlatformTaskService(db)
    try:
        mutation = await service.set_participant_state(
            task_id=task_id,
            user_id=user_id,
            data=payload,
            context=_context(request, auth, actor),
        )
        await _audit_and_commit(
            request=request,
            auth=auth,
            db=db,
            mutation=mutation,
            action="platform_task.participant_state_changed",
            reason=payload.reason,
        )
    except PlatformTaskError as error:
        _raise(error)
    return await _task_read(service, actor=actor, task=mutation.task)


@router.post(
    "/{task_id}/commands/{command}",
    response_model=PlatformTaskRead,
    openapi_extra={
        "x-platform-mutation": {
            "key": "platform_task.command",
            "module": "platform_tasks",
            "action": "command",
            "resourceType": "platform_task",
            "reasonPolicy": "system_allowed",
            "snapshotAdapter": "platform_task",
            "mutationKind": "command",
            "restorationAdapter": None,
            "idempotencyPolicy": "optional",
            "externalEffect": False,
            "auditSchemaVersion": 1,
        }
    },
)
async def command_platform_task(
    task_id: UUID,
    command: PlatformTaskCommand,
    payload: PlatformTaskCommandRequest,
    request: Request,
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    # Service applies the narrower edit/review/complete permission per command.
    actor: User = Depends(_command),
) -> PlatformTaskRead:
    service = PlatformTaskService(db)
    try:
        mutation = await service.execute_command(
            task_id=task_id,
            command=command,
            data=payload,
            context=_context(request, auth, actor),
        )
        reason = payload.reason or f"Comando {command.value} executado."
        await _audit_and_commit(
            request=request,
            auth=auth,
            db=db,
            mutation=mutation,
            action=f"platform_task.{command.value}",
            reason=reason,
        )
    except PlatformTaskError as error:
        _raise(error)
    return await _task_read(service, actor=actor, task=mutation.task)


@router.post(
    "/{task_id}/comments",
    response_model=PlatformTaskRead,
    openapi_extra={
        "x-platform-mutation": {
            "key": "platform_task.comment",
            "module": "platform_tasks",
            "action": "comment",
            "resourceType": "platform_task",
            "reasonPolicy": "system_allowed",
            "snapshotAdapter": "platform_task",
            "mutationKind": "command",
            "restorationAdapter": None,
            "idempotencyPolicy": "optional",
            "externalEffect": False,
            "auditSchemaVersion": 1,
        }
    },
)
async def comment_platform_task(
    task_id: UUID,
    payload: PlatformTaskCommentCreate,
    request: Request,
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_comment),
) -> PlatformTaskRead:
    service = PlatformTaskService(db)
    try:
        mutation = await service.add_comment(
            task_id=task_id, data=payload, context=_context(request, auth, actor)
        )
        await _audit_and_commit(
            request=request,
            auth=auth,
            db=db,
            mutation=mutation,
            action="platform_task.comment_added",
            reason="Comentário interno adicionado.",
        )
    except PlatformTaskError as error:
        _raise(error)
    return await _task_read(service, actor=actor, task=mutation.task)


@router.get("/{task_id}/events", response_model=PlatformTaskEventPage)
async def list_platform_task_events(
    task_id: UUID,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_view),
) -> PlatformTaskEventPage:
    try:
        items, total = await PlatformTaskService(db).list_events(
            actor=actor, task_id=task_id, limit=limit, offset=offset
        )
    except PlatformTaskError as error:
        _raise(error)
    return PlatformTaskEventPage(
        items=[PlatformTaskEventRead.model_validate(item) for item in items], total=total
    )


__all__ = ["require_platform_task_permission", "router"]
