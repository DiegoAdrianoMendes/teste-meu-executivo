"""API nativa de continuidade de carteiras nas superfícies isoladas do produto."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    AuthContext,
    get_current_auth_context,
    get_current_workspace_id,
    get_db,
    require_subscription_access,
)
from app.core.platform_access import require_platform_permission
from app.core.platform_audit import audit_platform_mutation, record_canonical_platform_mutation
from app.db.platform_staff_reassignment import (
    set_platform_staff_reassignment_context,
)
from app.models.user import User
from app.schemas.staff_reassignment import (
    StaffReassignmentCreate,
    StaffReassignmentPlanPageRead,
    StaffReassignmentPlanRead,
    StaffReassignmentPreviewRead,
    StaffReassignmentPreviewRequest,
    StaffReassignmentRestore,
)
from app.services.staff_reassignment_service import (
    StaffReassignmentAuthorizationError,
    StaffReassignmentConflictError,
    StaffReassignmentNotFoundError,
    StaffReassignmentService,
    StaffReassignmentValidationError,
)

router = APIRouter()
workspace_router = APIRouter(
    prefix="/admin/staff-reassignments",
    tags=["Continuidade da equipe"],
    dependencies=[Depends(require_subscription_access)],
)
platform_router = APIRouter(
    prefix="/platform/staff-reassignments",
    tags=["Blackbox - Continuidade da equipe"],
)

_platform_view = require_platform_permission("staff_reassignment", "view")
_platform_manage = require_platform_permission("staff_reassignment", "manage")


def _platform_plan_snapshot(plan: StaffReassignmentPlanRead) -> tuple[str, str, dict]:
    return (
        "platform_staff_reassignment",
        str(plan.id),
        {
            "sourceUserId": str(plan.source_user_id),
            "targetUserId": str(plan.target_user_id),
            "mode": plan.mode.value,
            "status": plan.status.value,
            "reasonPresent": bool(plan.reason),
            "scopes": list(plan.scopes),
            "assignmentSummary": dict(plan.assignment_summary),
            "conflictCount": plan.conflict_count,
            "startsAt": plan.starts_at,
            "endsAt": plan.ends_at,
            "appliedAt": plan.applied_at,
            "restoredAt": plan.restored_at,
            "version": plan.version,
        },
    )


def _raise_reassignment_error(error: Exception) -> None:
    if isinstance(error, StaffReassignmentAuthorizationError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "STAFF-REASSIGNMENT-FORBIDDEN",
                "message": str(error),
            },
        ) from error
    if isinstance(error, StaffReassignmentNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "STAFF-REASSIGNMENT-NOT-FOUND",
                "message": str(error),
            },
        ) from error
    if isinstance(error, StaffReassignmentConflictError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "STAFF-REASSIGNMENT-CONFLICT",
                "message": str(error),
            },
        ) from error
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "code": "STAFF-REASSIGNMENT-INVALID",
            "message": str(error),
        },
    ) from error


async def _platform_view_actor(
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_platform_view),
) -> User:
    await set_platform_staff_reassignment_context(
        db,
        actor_user_id=actor.id,
        action="view",
    )
    return actor


async def _platform_manage_actor(
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_platform_manage),
) -> User:
    await set_platform_staff_reassignment_context(
        db,
        actor_user_id=actor.id,
        action="approve",
    )
    return actor


@workspace_router.post(
    "/preview",
    response_model=StaffReassignmentPreviewRead,
    summary="Pré-visualizar a continuidade de uma carteira da conta",
)
async def preview_workspace_staff_reassignment(
    payload: StaffReassignmentPreviewRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> StaffReassignmentPreviewRead:
    try:
        return await StaffReassignmentService(db).preview_workspace(
            workspace_id=workspace_id,
            actor=actor,
            payload=payload,
        )
    except (
        StaffReassignmentAuthorizationError,
        StaffReassignmentNotFoundError,
        StaffReassignmentValidationError,
    ) as error:
        _raise_reassignment_error(error)


@workspace_router.post(
    "",
    response_model=StaffReassignmentPlanRead,
    status_code=status.HTTP_201_CREATED,
    summary="Aplicar uma continuidade temporária ou definitiva na conta",
)
async def apply_workspace_staff_reassignment(
    payload: StaffReassignmentCreate,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> StaffReassignmentPlanRead:
    try:
        return await StaffReassignmentService(db).apply_workspace(
            workspace_id=workspace_id,
            actor=actor,
            payload=payload,
            idempotency_key=idempotency_key,
        )
    except (
        StaffReassignmentAuthorizationError,
        StaffReassignmentConflictError,
        StaffReassignmentNotFoundError,
        StaffReassignmentValidationError,
    ) as error:
        _raise_reassignment_error(error)


@workspace_router.get(
    "",
    response_model=list[StaffReassignmentPlanRead],
    summary="Listar o histórico de continuidade da conta",
    deprecated=True,
)
async def list_workspace_staff_reassignments(
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> list[StaffReassignmentPlanRead]:
    try:
        return await StaffReassignmentService(db).list_workspace(
            workspace_id=workspace_id,
            actor=actor,
        )
    except (
        StaffReassignmentAuthorizationError,
        StaffReassignmentNotFoundError,
    ) as error:
        _raise_reassignment_error(error)


@workspace_router.get(
    "/page",
    response_model=StaffReassignmentPlanPageRead,
    summary="Listar o histórico paginado de continuidade da conta",
)
async def page_workspace_staff_reassignments(
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> StaffReassignmentPlanPageRead:
    try:
        items, total = await StaffReassignmentService(db).page_workspace(
            workspace_id=workspace_id,
            actor=actor,
            limit=limit,
            offset=offset,
        )
        return StaffReassignmentPlanPageRead(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + len(items) < total,
        )
    except (
        StaffReassignmentAuthorizationError,
        StaffReassignmentNotFoundError,
    ) as error:
        _raise_reassignment_error(error)


@workspace_router.get(
    "/{plan_id}",
    response_model=StaffReassignmentPlanRead,
    summary="Consultar uma continuidade específica da conta",
)
async def get_workspace_staff_reassignment(
    plan_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> StaffReassignmentPlanRead:
    try:
        return await StaffReassignmentService(db).get_workspace(
            workspace_id=workspace_id,
            plan_id=plan_id,
            actor=actor,
        )
    except (
        StaffReassignmentAuthorizationError,
        StaffReassignmentNotFoundError,
    ) as error:
        _raise_reassignment_error(error)


@workspace_router.post(
    "/{plan_id}/restore",
    response_model=StaffReassignmentPlanRead,
    summary="Restaurar com segurança uma continuidade temporária",
)
async def restore_workspace_staff_reassignment(
    plan_id: UUID,
    payload: StaffReassignmentRestore,
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> StaffReassignmentPlanRead:
    try:
        return await StaffReassignmentService(db).restore_workspace(
            workspace_id=workspace_id,
            plan_id=plan_id,
            actor=actor,
            expected_version=payload.expected_version,
            reason=payload.reason,
        )
    except (
        StaffReassignmentAuthorizationError,
        StaffReassignmentConflictError,
        StaffReassignmentNotFoundError,
        StaffReassignmentValidationError,
    ) as error:
        _raise_reassignment_error(error)


@platform_router.post(
    "/preview",
    response_model=StaffReassignmentPreviewRead,
    summary="Pré-visualizar a continuidade de uma carteira interna do SaaS",
)
async def preview_platform_staff_reassignment(
    payload: StaffReassignmentPreviewRequest,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_platform_view_actor),
) -> StaffReassignmentPreviewRead:
    try:
        return await StaffReassignmentService(db).preview_platform(
            actor=actor,
            payload=payload,
        )
    except (
        StaffReassignmentAuthorizationError,
        StaffReassignmentNotFoundError,
        StaffReassignmentValidationError,
    ) as error:
        _raise_reassignment_error(error)


@platform_router.post(
    "",
    response_model=StaffReassignmentPlanRead,
    status_code=status.HTTP_201_CREATED,
    summary="Aplicar uma continuidade temporária ou definitiva no Blackbox",
    dependencies=[Depends(audit_platform_mutation)],
)
async def apply_platform_staff_reassignment(
    payload: StaffReassignmentCreate,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_platform_manage_actor),
    context: AuthContext = Depends(get_current_auth_context),
) -> StaffReassignmentPlanRead:
    try:
        service = StaffReassignmentService(db)
        plan = await service.apply_platform(
            actor=actor,
            payload=payload,
            idempotency_key=idempotency_key,
        )
        if not service.operation_was_replayed:
            await record_canonical_platform_mutation(
                request,
                context,
                db,
                before_snapshot=None,
                after_snapshot=_platform_plan_snapshot(plan),
                module="staff_reassignment",
                action="staff_reassignment.apply",
                reason=payload.reason,
            )
        else:
            request.state.canonical_platform_audit_recorded = True
        return plan
    except (
        StaffReassignmentAuthorizationError,
        StaffReassignmentConflictError,
        StaffReassignmentNotFoundError,
        StaffReassignmentValidationError,
    ) as error:
        _raise_reassignment_error(error)


@platform_router.get(
    "",
    response_model=list[StaffReassignmentPlanRead],
    summary="Listar o histórico de continuidade do Blackbox",
    deprecated=True,
)
async def list_platform_staff_reassignments(
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_platform_view_actor),
) -> list[StaffReassignmentPlanRead]:
    return await StaffReassignmentService(db).list_platform(actor=actor)


@platform_router.get(
    "/page",
    response_model=StaffReassignmentPlanPageRead,
    summary="Listar o histórico paginado de continuidade do Blackbox",
)
async def page_platform_staff_reassignments(
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_platform_view_actor),
) -> StaffReassignmentPlanPageRead:
    items, total = await StaffReassignmentService(db).page_platform(
        actor=actor,
        limit=limit,
        offset=offset,
    )
    return StaffReassignmentPlanPageRead(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < total,
    )


@platform_router.get(
    "/{plan_id}",
    response_model=StaffReassignmentPlanRead,
    summary="Consultar uma continuidade específica do Blackbox",
)
async def get_platform_staff_reassignment(
    plan_id: UUID,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_platform_view_actor),
) -> StaffReassignmentPlanRead:
    try:
        return await StaffReassignmentService(db).get_platform(
            plan_id=plan_id,
            actor=actor,
        )
    except (
        StaffReassignmentAuthorizationError,
        StaffReassignmentNotFoundError,
    ) as error:
        _raise_reassignment_error(error)


@platform_router.post(
    "/{plan_id}/restore",
    response_model=StaffReassignmentPlanRead,
    summary="Restaurar com segurança uma continuidade temporária do Blackbox",
    dependencies=[Depends(audit_platform_mutation)],
)
async def restore_platform_staff_reassignment(
    plan_id: UUID,
    payload: StaffReassignmentRestore,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_platform_manage_actor),
    context: AuthContext = Depends(get_current_auth_context),
) -> StaffReassignmentPlanRead:
    try:
        service = StaffReassignmentService(db)
        before = await service.get_platform(plan_id=plan_id, actor=actor)
        plan = await service.restore_platform(
            plan_id=plan_id,
            actor=actor,
            expected_version=payload.expected_version,
            reason=payload.reason,
        )
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=_platform_plan_snapshot(before),
            after_snapshot=_platform_plan_snapshot(plan),
            module="staff_reassignment",
            action="staff_reassignment.restore",
            reason=payload.reason,
        )
        return plan
    except (
        StaffReassignmentAuthorizationError,
        StaffReassignmentConflictError,
        StaffReassignmentNotFoundError,
        StaffReassignmentValidationError,
    ) as error:
        _raise_reassignment_error(error)


router.include_router(workspace_router)
router.include_router(platform_router)

__all__ = ["router"]
