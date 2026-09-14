"""API tenant-aware de tarefas colaborativas."""

from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.entitlement_errors import (
    enforce_workspace_feature_or_http,
    entitlement_http_exception,
)
from app.core.deps import (
    AuthContext,
    can_view_sensitive_lead_data,
    get_current_auth_context,
    get_current_permissions,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.core.lead_privacy import mask_task, masked_lead_label, redact_sensitive_metadata
from app.core.platform_rbac import is_platform_root
from app.core.rbac import UNRESTRICTED_PERMISSION_SCOPES, Permission
from app.models.client import Client
from app.models.entitlement import EntitlementKey
from app.models.flow import Flow
from app.models.matter import Matter
from app.models.support_access import SupportAccessEvent, SupportAccessGrant
from app.models.task import TaskPriority, TaskReviewState, TaskStatus
from app.models.user import User
from app.schemas.flow import TaskFlowRunCreate, TaskFlowRunRead
from app.schemas.task import (
    TaskChecklistArchive,
    TaskChecklistCreate,
    TaskChecklistItemCreate,
    TaskChecklistItemStateUpdate,
    TaskChecklistRead,
    TaskCommand,
    TaskCommandRequest,
    TaskCommentCreate,
    TaskCreate,
    TaskDependencyCandidateRead,
    TaskDependencyCreate,
    TaskDependencyRead,
    TaskDependencyRemove,
    TaskEventPage,
    TaskEventRead,
    TaskLabelOptionRead,
    TaskLabelRead,
    TaskLabelsReplace,
    TaskLegalResourcesRead,
    TaskListScope,
    TaskMatterOptionRead,
    TaskMemberOptionPage,
    TaskMemberOptionRead,
    TaskPage,
    TaskParticipantsReplace,
    TaskParticipantStateUpdate,
    TaskRead,
    TaskSubtaskCreate,
    TaskSummaryRead,
    TaskUpdate,
)
from app.schemas.task_context import ContextualTaskCreate, TaskContextType
from app.schemas.task_organization import (
    TaskArchiveWrite,
    TaskBoardColumnCreate,
    TaskBoardColumnRead,
    TaskBoardPlacementRead,
    TaskBoardPlacementWrite,
    TaskBoardRead,
    TaskListMembershipRead,
    TaskListMembershipWrite,
    TaskListRead,
    TaskOrganizationCreate,
    TaskOrganizationRead,
)
from app.services.entitlement_service import EntitlementError
from app.services.flow_service import (
    FlowService,
    TaskFlowApplicationError,
    TaskFlowIdempotencyConflict,
)
from app.services.task_context_service import TaskContextService
from app.services.task_organization_service import TaskOrganizationService
from app.services.task_service import TaskMutationContext, TaskService

router = APIRouter(tags=["Tarefas"])

_TASK_COMMAND_PERMISSION: dict[TaskCommand, str] = {
    TaskCommand.start: "edit",
    TaskCommand.block: "edit",
    TaskCommand.unblock: "edit",
    TaskCommand.submit_for_review: "edit",
    TaskCommand.approve: "review",
    TaskCommand.request_changes: "review",
    TaskCommand.complete: "complete",
    TaskCommand.reopen: "complete",
    TaskCommand.cancel: "complete",
}


def _has_task_permission(permissions: list[Permission], action: str) -> bool:
    return any(
        permission.module == "tasks"
        and permission.action == action
        and permission.scope in UNRESTRICTED_PERMISSION_SCOPES
        for permission in permissions
    )


def _has_unrestricted_permission(
    permissions: list[Permission], module: str, action: str
) -> bool:
    return any(
        permission.module == module
        and permission.action == action
        and permission.scope in UNRESTRICTED_PERMISSION_SCOPES
        for permission in permissions
    )


def _require_task_assignment_permission(
    auth: AuthContext,
    permissions: list[Permission],
) -> None:
    # O escopo administrativo já foi validado por ``require_permission`` para
    # sessões de suporte. Nas sessões tenant, grants/denials persistidos de
    # ``tasks:assign`` são a autoridade final.
    if auth.is_support_access or _has_task_permission(permissions, "assign"):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "AUTH-012",
            "message": "Permissão insuficiente para atribuir participantes da tarefa.",
        },
    )


async def _lead_names(
    db: AsyncSession, matter_id: UUID | None, workspace_id: UUID
) -> tuple[str, ...]:
    if matter_id is None:
        return ()
    name = await db.scalar(
        select(Client.name)
        .join(Matter, Matter.client_id == Client.id)
        .where(Matter.id == matter_id, Matter.workspace_id == workspace_id)
    )
    return (name,) if name else ()


async def _mutation_context(
    request: Request, db: AsyncSession, auth: AuthContext
) -> TaskMutationContext:
    correlation_id = request.headers.get("X-Correlation-ID", "").strip() or str(uuid4())
    session_type = "normal"
    administrative_session_id = None
    root_actor = False
    if auth.is_support_access and auth.support_grant_id is not None:
        grant = await db.get(SupportAccessGrant, auth.support_grant_id)
        if grant is not None:
            root_event_id = await db.scalar(
                select(SupportAccessEvent.id).where(
                    SupportAccessEvent.grant_id == grant.id,
                    SupportAccessEvent.event_type == "support_access_root_direct",
                )
            )
            actor = await db.get(User, auth.user_id)
            root_actor = root_event_id is not None and is_platform_root(actor)
            session_type = "root_direct" if root_actor else "support_approved"
            administrative_session_id = grant.activation_admin_session_id
        else:  # authorize_token já valida; defesa para uma remoção concorrente
            session_type = "support_approved"
    return TaskMutationContext(
        actor_user_id=auth.user_id,
        correlation_id=correlation_id,
        session_type=session_type,
        administrative_session_id=administrative_session_id,
        support_grant_id=auth.support_grant_id,
        is_platform_root=root_actor,
    )


async def _present_task(task, *, db: AsyncSession, workspace_id: UUID, can_view_sensitive: bool):
    if can_view_sensitive:
        return task
    names = await _lead_names(db, task.matter_id, workspace_id)
    if not names and task.client_id is not None:
        client_name = await db.scalar(
            select(Client.name).where(
                Client.id == task.client_id,
                Client.workspace_id == workspace_id,
                Client.deleted_at.is_(None),
            )
        )
        names = (client_name,) if client_name else ()
    return mask_task(task, names)


def _require_task_list_scope(auth: AuthContext, scope: TaskListScope) -> None:
    if scope != TaskListScope.workspace or _allow_workspace_task_access(auth):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "TASK-WORKSPACE-SCOPE-SUPPORT-ONLY",
            "message": (
                "A coleção completa do workspace exige uma sessão "
                "root_direct ou support_approved ativa."
            ),
        },
    )


def _allow_workspace_task_access(auth: AuthContext) -> bool:
    """Confirma o contexto administrativo já revalidado pela autenticação.

    Um token tenant comum nunca pode transformar ``tasks:view`` em acesso
    integral ao workspace. O contexto de suporte é montado apenas depois de
    ``authorize_token`` conferir grant, sessão, workspace, escopo e expiração.
    """

    return bool(
        auth.is_support_access
        and auth.support_grant_id is not None
        and auth.administrative_session_id is not None
        and auth.administrative_session_type in {"root_direct", "support_approved"}
        and auth.support_request_scope is not None
        and auth.support_request_scope in auth.support_scopes
    )


def _require_task_member_scope(
    auth: AuthContext,
    permissions: list[Permission],
    scope: TaskListScope,
) -> None:
    if scope != TaskListScope.workspace:
        _require_task_list_scope(auth, scope)
        return
    if _allow_workspace_task_access(auth) or _has_task_permission(permissions, "assign"):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "TASK-MEMBER-WORKSPACE-SCOPE-DENIED",
            "message": "A lista integral de participantes exige tasks:assign.",
        },
    )


def _decode_filter_reference_map(
    service: TaskService,
    references: list[str] | None,
    *,
    kind: str,
    workspace_id: UUID,
) -> dict[UUID, str]:
    decoded: dict[UUID, str] = {}
    for reference in references or ():
        decoded.setdefault(
            service.decode_filter_reference(
                reference,
                expected_kind=kind,
                workspace_id=workspace_id,
            ),
            reference,
        )
    return decoded


def _resolve_filter_reference(
    service: TaskService,
    reference: str | None,
    legacy_id: UUID | None,
    *,
    kind: str,
    workspace_id: UUID,
) -> UUID | None:
    if reference is None:
        return legacy_id
    resolved = service.decode_filter_reference(
        reference,
        expected_kind=kind,
        workspace_id=workspace_id,
    )
    if legacy_id is not None and legacy_id != resolved:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "TASK_FILTER_REFERENCE_CONFLICT",
                "message": "A referência de filtro diverge do identificador legado.",
            },
        )
    return resolved


@router.get(
    "/matters/{matter_id}/tasks",
    response_model=list[TaskRead],
    summary="Listar tarefas do caso",
)
async def list_tasks(
    matter_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    __: object = Depends(require_permission("tasks", "view")),
):
    tasks = await TaskService(db).get_by_matter_for_actor(
        matter_id,
        workspace_id,
        actor_user_id=auth.user_id,
        allow_workspace=_allow_workspace_task_access(auth),
    )
    if can_view_sensitive:
        return tasks
    names = await _lead_names(db, matter_id, workspace_id)
    return [mask_task(item, names) for item in tasks]


@router.get("/tasks", response_model=TaskPage, summary="Listar tarefas do workspace")
async def list_workspace_tasks(
    scope: TaskListScope = Query(default=TaskListScope.mine),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None),
    search: str | None = Query(default=None, min_length=2, max_length=120),
    task_status: TaskStatus | None = Query(default=None, alias="status"),
    review_state: TaskReviewState | None = Query(default=None, alias="reviewState"),
    matter_ref: str | None = Query(default=None, alias="matterRef", max_length=1024),
    client_ref: str | None = Query(default=None, alias="clientRef", max_length=1024),
    owner_ref: str | None = Query(default=None, alias="ownerRef", max_length=1024),
    assignee_ref: str | None = Query(default=None, alias="assigneeRef", max_length=1024),
    reviewer_ref: str | None = Query(default=None, alias="reviewerRef", max_length=1024),
    label_ref: str | None = Query(default=None, alias="labelRef", max_length=1024),
    matter_id: UUID | None = Query(default=None, alias="matter"),
    client_id: UUID | None = Query(default=None, alias="client"),
    owner_id: UUID | None = Query(default=None, alias="owner"),
    assignee_id: UUID | None = Query(default=None, alias="assignee"),
    reviewer_id: UUID | None = Query(default=None, alias="reviewer"),
    priority: TaskPriority | None = Query(default=None),
    due_from: datetime | None = Query(default=None, alias="dueFrom"),
    due_to: datetime | None = Query(default=None, alias="dueTo"),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("tasks", "view")),
):
    """Página operacional sem conceder acesso adicional ao conteúdo do caso."""

    _require_task_list_scope(auth, scope)

    service = TaskService(db)
    matter_id = _resolve_filter_reference(
        service, matter_ref, matter_id, kind="matter", workspace_id=workspace_id
    )
    client_id = _resolve_filter_reference(
        service, client_ref, client_id, kind="client", workspace_id=workspace_id
    )
    owner_id = _resolve_filter_reference(
        service, owner_ref, owner_id, kind="member", workspace_id=workspace_id
    )
    assignee_id = _resolve_filter_reference(
        service, assignee_ref, assignee_id, kind="member", workspace_id=workspace_id
    )
    reviewer_id = _resolve_filter_reference(
        service, reviewer_ref, reviewer_id, kind="member", workspace_id=workspace_id
    )
    label_id = _resolve_filter_reference(
        service, label_ref, None, kind="task-label", workspace_id=workspace_id
    )

    items, next_cursor, has_more = await service.list_workspace_tasks(
        workspace_id=workspace_id,
        actor_user_id=auth.user_id,
        scope=scope,
        limit=limit,
        cursor=cursor,
        search=search,
        include_sensitive_identity=can_view_sensitive,
        task_status=task_status,
        review_state=review_state,
        matter_id=matter_id,
        client_id=client_id,
        owner_id=owner_id,
        assignee_id=assignee_id,
        reviewer_id=reviewer_id,
        label_id=label_id,
        priority=priority,
        due_from=due_from,
        due_to=due_to,
    )
    rows: list[TaskSummaryRead] = []
    for item in items:
        task = (
            TaskRead.model_validate(item.task)
            if can_view_sensitive
            else mask_task(item.task, (item.client_label,))
        )
        rows.append(
            TaskSummaryRead(
                **task.model_dump(),
                matter_label=(
                    redact_sensitive_metadata(item.matter_label, (item.client_label,))
                    if item.matter_label
                    else None
                ),
                client_label=(
                    item.client_label
                    if can_view_sensitive
                    else (
                        masked_lead_label(item.task.matter_id)
                        if item.client_label and item.task.matter_id
                        else ("Cliente protegido" if item.client_label else None)
                    )
                ),
                my_roles=item.my_roles,
            )
        )
    return TaskPage(items=rows, next_cursor=next_cursor, has_more=has_more)


@router.get(
    "/tasks/label-catalog",
    response_model=list[TaskLabelOptionRead],
    summary="Listar catálogo persistente de etiquetas de tarefas",
)
async def list_task_label_catalog(
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("tasks", "view")),
):
    service = TaskService(db)
    labels = await service.label_catalog(workspace_id)
    return [
        TaskLabelOptionRead(
            id=label.id,
            handle=service.encode_filter_reference(
                kind="task-label",
                resource_id=label.id,
                workspace_id=workspace_id,
            ),
            name=label.name,
            color=label.color,
        )
        for label in labels
    ]


@router.get(
    "/tasks/filter-options/matters",
    response_model=list[TaskMatterOptionRead],
    summary="Pesquisar casos presentes na fila autorizada de tarefas",
)
async def list_task_filter_matter_options(
    scope: TaskListScope = Query(default=TaskListScope.mine),
    search: str | None = Query(default=None, min_length=2, max_length=120),
    selected_ids: list[UUID] | None = Query(default=None, alias="selectedId"),
    selected_refs: list[str] | None = Query(
        default=None, alias="selectedRef", max_length=1024
    ),
    limit: int = Query(default=50, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("tasks", "view")),
):
    _require_task_list_scope(auth, scope)
    service = TaskService(db)
    selected_reference_map = _decode_filter_reference_map(
        service,
        selected_refs,
        kind="matter",
        workspace_id=workspace_id,
    )
    resolved_selected_ids = tuple(
        dict.fromkeys(
            [
                *(selected_ids or ()),
                *selected_reference_map,
            ]
        )
    )
    options = await service.filter_matter_options(
        workspace_id=workspace_id,
        actor_user_id=auth.user_id,
        scope=scope,
        search=search,
        selected_ids=resolved_selected_ids,
        limit=limit,
        include_sensitive_identity=can_view_sensitive,
    )
    return [
        TaskMatterOptionRead(
            id=matter_id,
            handle=selected_reference_map.get(matter_id)
            or service.encode_filter_reference(
                kind="matter", resource_id=matter_id, workspace_id=workspace_id
            ),
            label=redact_sensitive_metadata(matter_label, (client_label,)) or "Caso",
            client_label=(
                client_label if can_view_sensitive else masked_lead_label(matter_id)
            ),
        )
        for matter_id, matter_label, client_label in options
    ]


@router.get(
    "/tasks/filter-options/clients",
    response_model=list[TaskMemberOptionRead],
    summary="Pesquisar clientes presentes na fila autorizada de tarefas",
)
async def list_task_filter_client_options(
    scope: TaskListScope = Query(default=TaskListScope.mine),
    search: str | None = Query(default=None, min_length=2, max_length=120),
    selected_ids: list[UUID] | None = Query(default=None, alias="selectedId"),
    selected_refs: list[str] | None = Query(
        default=None, alias="selectedRef", max_length=1024
    ),
    limit: int = Query(default=50, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("tasks", "view")),
):
    _require_task_list_scope(auth, scope)
    service = TaskService(db)
    selected_reference_map = _decode_filter_reference_map(
        service,
        selected_refs,
        kind="client",
        workspace_id=workspace_id,
    )
    resolved_selected_ids = tuple(
        dict.fromkeys(
            [
                *(selected_ids or ()),
                *selected_reference_map,
            ]
        )
    )
    options = await service.filter_client_options(
        workspace_id=workspace_id,
        actor_user_id=auth.user_id,
        scope=scope,
        search=search,
        selected_ids=resolved_selected_ids,
        limit=limit,
        include_sensitive_identity=can_view_sensitive,
    )
    return [
        TaskMemberOptionRead(
            id=client_id,
            handle=selected_reference_map.get(client_id)
            or service.encode_filter_reference(
                kind="client", resource_id=client_id, workspace_id=workspace_id
            ),
            label=(client_label if can_view_sensitive else masked_lead_label(client_id)),
            role="client",
        )
        for client_id, client_label in options
    ]


@router.get(
    "/tasks/matter-options",
    response_model=list[TaskMatterOptionRead],
    summary="Pesquisar casos elegíveis para nova tarefa",
)
async def list_task_matter_options(
    search: str | None = Query(default=None, min_length=2, max_length=120),
    limit: int = Query(default=20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("tasks", "create")),
):
    service = TaskService(db)
    options = await service.matter_options(
        workspace_id,
        search=search,
        limit=limit,
        include_sensitive_identity=can_view_sensitive,
    )
    return [
        TaskMatterOptionRead(
            id=matter_id,
            handle=service.encode_filter_reference(
                kind="matter",
                resource_id=matter_id,
                workspace_id=workspace_id,
            ),
            label=redact_sensitive_metadata(matter_label, (client_label,)) or "Caso",
            client_label=(client_label if can_view_sensitive else masked_lead_label(matter_id)),
        )
        for matter_id, matter_label, client_label in options
    ]


@router.get(
    "/tasks/member-options",
    response_model=list[TaskMemberOptionRead],
    summary="Pesquisar participantes elegíveis",
)
async def list_task_member_options(
    scope: TaskListScope = Query(default=TaskListScope.workspace),
    search: str | None = Query(default=None, min_length=2, max_length=120),
    limit: int = Query(default=20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    permissions: list[Permission] = Depends(get_current_permissions),
    __: object = Depends(require_permission("tasks", "view")),
):
    _require_task_member_scope(auth, permissions, scope)
    service = TaskService(db)
    options = await service.member_options(
        workspace_id,
        actor_user_id=auth.user_id,
        scope=scope,
        search=search,
        limit=limit,
    )
    return [
        TaskMemberOptionRead(
            id=user_id,
            handle=service.encode_filter_reference(
                kind="member",
                resource_id=user_id,
                workspace_id=workspace_id,
            ),
            label=label,
            role=role,
        )
        for user_id, label, role in options
    ]


@router.get(
    "/tasks/member-options/page",
    response_model=TaskMemberOptionPage,
    summary="Paginar participantes elegíveis",
)
async def page_task_member_options(
    scope: TaskListScope = Query(default=TaskListScope.workspace),
    search: str | None = Query(default=None, min_length=2, max_length=120),
    selected_ids: list[UUID] | None = Query(default=None, alias="selectedId"),
    selected_refs: list[str] | None = Query(
        default=None, alias="selectedRef", max_length=1024
    ),
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    permissions: list[Permission] = Depends(get_current_permissions),
    __: object = Depends(require_permission("tasks", "view")),
):
    _require_task_member_scope(auth, permissions, scope)
    service = TaskService(db)
    selected_reference_map = _decode_filter_reference_map(
        service,
        selected_refs,
        kind="member",
        workspace_id=workspace_id,
    )
    resolved_selected_ids = tuple(
        dict.fromkeys(
            [
                *(selected_ids or ()),
                *selected_reference_map,
            ]
        )
    )
    options, total = await service.member_options_page(
        workspace_id,
        actor_user_id=auth.user_id,
        scope=scope,
        search=search,
        limit=limit,
        offset=offset,
        selected_ids=resolved_selected_ids,
    )
    items = [
        TaskMemberOptionRead(
            id=user_id,
            handle=selected_reference_map.get(user_id)
            or service.encode_filter_reference(
                kind="member", resource_id=user_id, workspace_id=workspace_id
            ),
            label=label,
            role=role,
        )
        for user_id, label, role in options
    ]
    return TaskMemberOptionPage(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < total,
    )


def _organization_payload(rows) -> TaskOrganizationRead:
    lists, boards, memberships, placements = rows
    return TaskOrganizationRead(
        lists=[TaskListRead.model_validate(item) for item in lists],
        boards=[
            TaskBoardRead(
                id=board.id,
                name=board.name,
                description=board.description,
                position=board.position,
                version=board.version,
                archived_at=board.archived_at,
                columns=[
                    TaskBoardColumnRead.model_validate(column)
                    for column in board.columns
                    if column.archived_at is None
                ],
            )
            for board in boards
        ],
        list_memberships=[TaskListMembershipRead.model_validate(item) for item in memberships],
        placements=[TaskBoardPlacementRead.model_validate(item) for item in placements],
    )


@router.get(
    "/tasks/organization",
    response_model=TaskOrganizationRead,
    summary="Listar listas e quadros jurídicos",
)
async def get_task_organization(
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("tasks", "view")),
):
    await enforce_workspace_feature_or_http(db, workspace_id, EntitlementKey.legal_suite)
    return _organization_payload(
        await TaskOrganizationService(db).organization(
            workspace_id,
            actor_user_id=auth.user_id,
            allow_workspace=_allow_workspace_task_access(auth),
        )
    )


@router.post(
    "/tasks/lists",
    response_model=TaskListRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar lista jurídica de tarefas",
)
async def create_task_list(
    request: Request,
    data: TaskOrganizationCreate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("tasks", "edit")),
):
    await enforce_workspace_feature_or_http(db, workspace_id, EntitlementKey.legal_suite)
    return await TaskOrganizationService(db).create_list(
        workspace_id, data, await _mutation_context(request, db, auth)
    )


@router.post(
    "/tasks/boards",
    response_model=TaskBoardRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar quadro jurídico de tarefas",
)
async def create_task_board(
    request: Request,
    data: TaskOrganizationCreate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("tasks", "edit")),
):
    await enforce_workspace_feature_or_http(db, workspace_id, EntitlementKey.legal_suite)
    board = await TaskOrganizationService(db).create_board(
        workspace_id, data, await _mutation_context(request, db, auth)
    )
    return TaskBoardRead(
        id=board.id,
        name=board.name,
        description=board.description,
        position=board.position,
        version=board.version,
        archived_at=board.archived_at,
        columns=[],
    )


@router.post(
    "/tasks/boards/{board_id}/columns",
    response_model=TaskBoardColumnRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar coluna sem criar estado de tarefa",
)
async def create_task_board_column(
    request: Request,
    board_id: UUID,
    data: TaskBoardColumnCreate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("tasks", "edit")),
):
    await enforce_workspace_feature_or_http(db, workspace_id, EntitlementKey.legal_suite)
    return await TaskOrganizationService(db).create_column(
        workspace_id,
        board_id,
        name=data.name,
        expected_board_version=data.expected_board_version,
        context=await _mutation_context(request, db, auth),
    )


@router.put(
    "/tasks/list-memberships",
    response_model=TaskListMembershipRead,
    summary="Adicionar tarefa a uma lista",
)
async def assign_task_list(
    request: Request,
    data: TaskListMembershipWrite,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("tasks", "edit")),
):
    await enforce_workspace_feature_or_http(db, workspace_id, EntitlementKey.legal_suite)
    return await TaskOrganizationService(db).assign_list(
        workspace_id,
        task_id=data.task_id,
        list_id=data.list_id,
        expected_task_version=data.expected_task_version,
        context=await _mutation_context(request, db, auth),
        allow_workspace=_allow_workspace_task_access(auth),
    )


@router.put(
    "/tasks/board-placements",
    response_model=TaskBoardPlacementRead,
    summary="Posicionar tarefa em coluna sem alterar seu status",
)
async def place_task_on_board(
    request: Request,
    data: TaskBoardPlacementWrite,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("tasks", "edit")),
):
    await enforce_workspace_feature_or_http(db, workspace_id, EntitlementKey.legal_suite)
    return await TaskOrganizationService(db).place_task(
        workspace_id,
        task_id=data.task_id,
        board_id=data.board_id,
        column_id=data.column_id,
        expected_task_version=data.expected_task_version,
        expected_placement_version=data.expected_placement_version,
        context=await _mutation_context(request, db, auth),
        allow_workspace=_allow_workspace_task_access(auth),
    )


@router.post(
    "/tasks/{task_id}/archive",
    response_model=TaskRead,
    summary="Arquivar tarefa preservando histórico",
)
async def archive_task(
    request: Request,
    task_id: UUID,
    data: TaskArchiveWrite,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("tasks", "complete")),
):
    task = await TaskOrganizationService(db).set_archived(
        workspace_id, task_id, expected_version=data.expected_version, reason=data.reason,
        archived=True, context=await _mutation_context(request, db, auth),
        allow_workspace=_allow_workspace_task_access(auth),
    )
    return await _present_task(
        task,
        db=db,
        workspace_id=workspace_id,
        can_view_sensitive=can_view_sensitive,
    )


@router.post(
    "/tasks/{task_id}/unarchive",
    response_model=TaskRead,
    summary="Restaurar tarefa arquivada",
)
async def unarchive_task(
    request: Request,
    task_id: UUID,
    data: TaskArchiveWrite,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("tasks", "complete")),
):
    task = await TaskOrganizationService(db).set_archived(
        workspace_id, task_id, expected_version=data.expected_version, reason=data.reason,
        archived=False, context=await _mutation_context(request, db, auth),
        allow_workspace=_allow_workspace_task_access(auth),
    )
    return await _present_task(
        task,
        db=db,
        workspace_id=workspace_id,
        can_view_sensitive=can_view_sensitive,
    )


@router.get("/tasks/{task_id}", response_model=TaskRead, summary="Detalhar tarefa")
async def get_task(
    task_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    __: object = Depends(require_permission("tasks", "view")),
):
    task = await TaskService(db).get_for_actor(
        task_id,
        workspace_id,
        actor_user_id=auth.user_id,
        allow_workspace=_allow_workspace_task_access(auth),
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    return await _present_task(
        task, db=db, workspace_id=workspace_id, can_view_sensitive=can_view_sensitive
    )


def _task_flow_run_read(run, flow_name: str) -> TaskFlowRunRead:
    return TaskFlowRunRead(
        id=run.id,
        flow_id=run.flow_id,
        revision_id=run.revision_id,
        matter_id=run.matter_id,
        task_id=run.task_id,
        flow_name=flow_name,
        status=run.status,
        current_node_id=run.current_node_id,
        pause_reason=run.pause_reason,
        error_message=run.error_message,
        started_at=run.started_at,
        completed_at=run.completed_at,
        paused_at=run.paused_at,
    )


@router.get(
    "/tasks/{task_id}/workflow-runs",
    response_model=list[TaskFlowRunRead],
    summary="Listar workflows aplicados à tarefa",
)
async def list_task_workflow_runs(
    task_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("tasks", "edit")),
    __: object = Depends(require_permission("automations", "manage")),
):
    task = await TaskService(db).get_for_actor(
        task_id,
        workspace_id,
        actor_user_id=auth.user_id,
        allow_workspace=_allow_workspace_task_access(auth),
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    rows = await FlowService(db).list_task_flow_runs(task_id=task_id, workspace_id=workspace_id)
    return [_task_flow_run_read(run, flow_name) for run, flow_name in rows]


@router.post(
    "/tasks/{task_id}/workflow-runs",
    response_model=TaskFlowRunRead,
    status_code=status.HTTP_201_CREATED,
    summary="Aplicar workflow à tarefa",
)
async def apply_task_workflow(
    task_id: UUID,
    data: TaskFlowRunCreate,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=1, max_length=180
    ),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("tasks", "edit")),
    __: object = Depends(require_permission("automations", "manage")),
):
    task = await TaskService(db).get_for_actor(
        task_id,
        workspace_id,
        actor_user_id=auth.user_id,
        allow_workspace=_allow_workspace_task_access(auth),
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    if not idempotency_key.strip():
        raise HTTPException(status_code=422, detail="Idempotency-Key não pode ser vazia.")
    try:
        run = await FlowService(db).start_task_flow_run(
            task_id=task_id,
            flow_id=data.flow_id,
            workspace_id=workspace_id,
            actor_user_id=auth.user_id,
            idempotency_key=idempotency_key.strip(),
        )
    except TaskFlowIdempotencyConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except TaskFlowApplicationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    flow_name = await db.scalar(select(Flow.name).where(Flow.id == run.flow_id))
    return _task_flow_run_read(run, flow_name or "Workflow")


@router.post(
    "/tasks",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar tarefa com contexto explícito",
)
async def create_contextual_task(
    request: Request,
    data: ContextualTaskCreate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    permissions: list[Permission] = Depends(get_current_permissions),
    _: object = Depends(require_permission("tasks", "create")),
):
    """Cria a mesma Task canônica sem inventar um Caso 360 para contextos não jurídicos."""

    if data.context.type == TaskContextType.contract:
        await enforce_workspace_feature_or_http(
            db, workspace_id, EntitlementKey.legal_suite
        )
    if data.model_fields_set & {"assigned_to", "owner_id", "participants"}:
        _require_task_assignment_permission(auth, permissions)
    try:
        task = await TaskContextService(db).create(
            data,
            workspace_id=workspace_id,
            context=await _mutation_context(request, db, auth),
        )
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    return await _present_task(
        task, db=db, workspace_id=workspace_id, can_view_sensitive=can_view_sensitive
    )


@router.post(
    "/matters/{matter_id}/tasks",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar tarefa",
)
async def create_task(
    request: Request,
    matter_id: UUID,
    data: TaskCreate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    permissions: list[Permission] = Depends(get_current_permissions),
    _: object = Depends(require_permission("tasks", "create")),
):
    if data.model_fields_set & {"assigned_to", "owner_id", "participants"}:
        _require_task_assignment_permission(auth, permissions)
    try:
        task = await TaskService(db).create(
            matter_id,
            data,
            workspace_id=workspace_id,
            created_by=auth.user_id,
            context=await _mutation_context(request, db, auth),
        )
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    return await _present_task(
        task, db=db, workspace_id=workspace_id, can_view_sensitive=can_view_sensitive
    )


@router.patch(
    "/tasks/{task_id}/toggle",
    response_model=TaskRead,
    summary="Concluir tarefa simples (compatibilidade)",
)
async def toggle_task(
    request: Request,
    task_id: UUID,
    expected_version: int = Query(alias="expectedVersion", ge=1),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("tasks", "complete")),
):
    task = await TaskService(db).toggle_status(
        task_id,
        workspace_id=workspace_id,
        toggled_by=auth.user_id,
        expected_version=expected_version,
        context=await _mutation_context(request, db, auth),
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    return await _present_task(
        task, db=db, workspace_id=workspace_id, can_view_sensitive=can_view_sensitive
    )


@router.patch("/tasks/{task_id}", response_model=TaskRead, summary="Atualizar tarefa")
async def update_task(
    request: Request,
    task_id: UUID,
    data: TaskUpdate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    permissions: list[Permission] = Depends(get_current_permissions),
    _: object = Depends(require_permission("tasks", "edit")),
):
    if "assigned_to" in data.model_fields_set:
        _require_task_assignment_permission(auth, permissions)
    task = await TaskService(db).update(
        task_id,
        data,
        workspace_id=workspace_id,
        actor_user_id=auth.user_id,
        context=await _mutation_context(request, db, auth),
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    return await _present_task(
        task, db=db, workspace_id=workspace_id, can_view_sensitive=can_view_sensitive
    )


@router.put(
    "/tasks/{task_id}/participants",
    response_model=TaskRead,
    summary="Substituir participantes ativos",
)
async def replace_task_participants(
    request: Request,
    task_id: UUID,
    data: TaskParticipantsReplace,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("tasks", "assign")),
):
    try:
        task = await TaskService(db).replace_participants(
            task_id,
            data,
            workspace_id,
            context=await _mutation_context(request, db, auth),
        )
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    if task is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    return await _present_task(
        task, db=db, workspace_id=workspace_id, can_view_sensitive=can_view_sensitive
    )


@router.put(
    "/tasks/{task_id}/participants/{participant_user_id}/state",
    response_model=TaskRead,
    summary="Atualizar progresso individual de executor",
)
async def update_task_participant_state(
    request: Request,
    task_id: UUID,
    participant_user_id: UUID,
    data: TaskParticipantStateUpdate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    permissions: list[Permission] = Depends(get_current_permissions),
    _: object = Depends(require_permission("tasks", "edit")),
):
    allow_other_user = participant_user_id != auth.user_id
    if allow_other_user:
        _require_task_assignment_permission(auth, permissions)
    task = await TaskService(db).set_participant_state(
        task_id,
        participant_user_id,
        data,
        workspace_id,
        context=await _mutation_context(request, db, auth),
        allow_other_user=allow_other_user,
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    return await _present_task(
        task, db=db, workspace_id=workspace_id, can_view_sensitive=can_view_sensitive
    )


@router.post(
    "/tasks/{task_id}/commands/{command}",
    response_model=TaskRead,
    summary="Executar transição explícita",
)
async def command_task(
    request: Request,
    task_id: UUID,
    command: TaskCommand,
    data: TaskCommandRequest,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    permissions: list[Permission] = Depends(get_current_permissions),
):
    required_action = _TASK_COMMAND_PERMISSION[command]
    if not auth.is_support_access and not _has_task_permission(permissions, required_action):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "AUTH-012",
                "message": "Permissão insuficiente para esta ação na tarefa.",
            },
        )
    try:
        task = await TaskService(db).execute_command(
            task_id,
            command,
            data,
            workspace_id,
            context=await _mutation_context(request, db, auth),
        )
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    if task is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    return await _present_task(
        task, db=db, workspace_id=workspace_id, can_view_sensitive=can_view_sensitive
    )


@router.post(
    "/tasks/{task_id}/comments",
    response_model=TaskRead,
    summary="Adicionar comentário à tarefa",
)
async def comment_task(
    request: Request,
    task_id: UUID,
    data: TaskCommentCreate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    permissions: list[Permission] = Depends(get_current_permissions),
    _: object = Depends(require_permission("tasks", "comment")),
):
    if data.document_ids and not (
        auth.is_support_access
        or _has_unrestricted_permission(permissions, "kanban", "view")
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "TASK_ATTACHMENT_PERMISSION_DENIED",
                "message": "Anexar documento exige acesso de leitura ao caso.",
            },
        )
    task = await TaskService(db).add_comment(
        task_id,
        data,
        workspace_id,
        context=await _mutation_context(request, db, auth),
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    return await _present_task(
        task, db=db, workspace_id=workspace_id, can_view_sensitive=can_view_sensitive
    )


@router.get(
    "/tasks/{task_id}/legal-resources",
    response_model=TaskLegalResourcesRead,
    summary="Listar recursos avançados da tarefa jurídica",
)
async def list_task_legal_resources(
    task_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("tasks", "view")),
):
    resources = await TaskService(db).legal_resources(
        task_id,
        workspace_id,
        actor_user_id=auth.user_id,
        allow_workspace=_allow_workspace_task_access(auth),
    )
    if resources is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    return TaskLegalResourcesRead.model_validate(resources)


@router.get(
    "/tasks/{task_id}/dependency-candidates",
    response_model=list[TaskDependencyCandidateRead],
    summary="Pesquisar tarefas visíveis compatíveis para dependência",
)
async def list_task_dependency_candidates(
    task_id: UUID,
    search: str | None = Query(default=None, min_length=2, max_length=120),
    limit: int = Query(default=50, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("tasks", "view")),
):
    service = TaskService(db)
    candidates = await service.dependency_candidates(
        task_id,
        workspace_id,
        actor_user_id=auth.user_id,
        allow_workspace=_allow_workspace_task_access(auth),
        search=search,
        limit=limit,
    )
    if candidates is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    names = await _lead_names(
        db,
        candidates[0].matter_id if candidates else None,
        workspace_id,
    )

    def candidate_label(item) -> str:
        if can_view_sensitive:
            return item.title
        if item.matter_id is None:
            return "Tarefa protegida"
        return redact_sensitive_metadata(item.title, names) or "Tarefa protegida"

    return [
        TaskDependencyCandidateRead(
            handle=service.encode_filter_reference(
                kind="task-dependency",
                resource_id=item.id,
                workspace_id=workspace_id,
            ),
            label=candidate_label(item),
            status=item.status,
            due_at=item.due_at,
        )
        for item in candidates
    ]


@router.put(
    "/tasks/{task_id}/labels",
    response_model=list[TaskLabelRead],
    summary="Substituir etiquetas da tarefa",
)
async def replace_task_labels(
    request: Request,
    task_id: UUID,
    data: TaskLabelsReplace,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("tasks", "edit")),
):
    labels = await TaskService(db).replace_labels(
        task_id,
        data,
        workspace_id,
        context=await _mutation_context(request, db, auth),
    )
    if labels is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    return labels


@router.post(
    "/tasks/{task_id}/subtasks",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar subtarefa no mesmo caso",
)
async def create_task_subtask(
    request: Request,
    task_id: UUID,
    data: TaskSubtaskCreate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    permissions: list[Permission] = Depends(get_current_permissions),
    _: object = Depends(require_permission("tasks", "create")),
):
    if data.model_fields_set & {"assigned_to", "owner_id", "participants"}:
        _require_task_assignment_permission(auth, permissions)
    try:
        task = await TaskService(db).create_subtask(
            task_id,
            data,
            workspace_id,
            context=await _mutation_context(request, db, auth),
        )
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    if task is None:
        raise HTTPException(status_code=404, detail="Tarefa pai não encontrada.")
    return await _present_task(
        task, db=db, workspace_id=workspace_id, can_view_sensitive=can_view_sensitive
    )


@router.post(
    "/tasks/{task_id}/dependencies",
    response_model=TaskDependencyRead,
    status_code=status.HTTP_201_CREATED,
    summary="Adicionar dependência acíclica",
)
async def add_task_dependency(
    request: Request,
    task_id: UUID,
    data: TaskDependencyCreate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("tasks", "edit")),
):
    service = TaskService(db)
    if data.predecessor_ref is not None:
        data = data.model_copy(
            update={
                "predecessor_task_id": service.decode_filter_reference(
                    data.predecessor_ref,
                    expected_kind="task-dependency",
                    workspace_id=workspace_id,
                ),
                "predecessor_ref": None,
            }
        )
    dependency = await service.add_dependency(
        task_id,
        data,
        workspace_id,
        context=await _mutation_context(request, db, auth),
    )
    if dependency is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    return dependency


@router.delete(
    "/tasks/{task_id}/dependencies/{dependency_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remover dependência com motivo",
)
async def remove_task_dependency(
    request: Request,
    task_id: UUID,
    dependency_id: UUID,
    data: TaskDependencyRemove,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("tasks", "edit")),
):
    removed = await TaskService(db).remove_dependency(
        task_id,
        dependency_id,
        data,
        workspace_id,
        context=await _mutation_context(request, db, auth),
    )
    if removed is None:
        raise HTTPException(status_code=404, detail="Tarefa ou dependência não encontrada.")
    return None


@router.get(
    "/tasks/{task_id}/checklists",
    response_model=list[TaskChecklistRead],
    summary="Listar checklists estruturados da tarefa",
)
async def list_task_checklists(
    task_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("tasks", "view")),
):
    result = await TaskService(db).list_checklists(
        task_id,
        workspace_id,
        actor_user_id=auth.user_id,
        allow_workspace=_allow_workspace_task_access(auth),
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    return result


@router.post(
    "/tasks/{task_id}/checklists",
    response_model=TaskChecklistRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar checklist estruturado",
)
async def create_task_checklist(
    request: Request,
    task_id: UUID,
    data: TaskChecklistCreate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("tasks", "edit")),
):
    result = await TaskService(db).create_checklist(
        task_id,
        data,
        workspace_id,
        context=await _mutation_context(request, db, auth),
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    return result


@router.post(
    "/tasks/{task_id}/checklists/{checklist_id}/items",
    response_model=TaskChecklistRead,
    status_code=status.HTTP_201_CREATED,
    summary="Adicionar item ao checklist",
)
async def add_task_checklist_item(
    request: Request,
    task_id: UUID,
    checklist_id: UUID,
    data: TaskChecklistItemCreate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("tasks", "edit")),
):
    result = await TaskService(db).add_checklist_item(
        task_id,
        checklist_id,
        data,
        workspace_id,
        context=await _mutation_context(request, db, auth),
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Tarefa ou checklist não encontrado.")
    return result


@router.put(
    "/tasks/{task_id}/checklists/{checklist_id}/items/{item_id}",
    response_model=TaskChecklistRead,
    summary="Atualizar estado do item do checklist",
)
async def update_task_checklist_item(
    request: Request,
    task_id: UUID,
    checklist_id: UUID,
    item_id: UUID,
    data: TaskChecklistItemStateUpdate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("tasks", "edit")),
):
    result = await TaskService(db).set_checklist_item_state(
        task_id,
        checklist_id,
        item_id,
        data,
        workspace_id,
        context=await _mutation_context(request, db, auth),
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Tarefa, checklist ou item não encontrado.")
    return result


@router.delete(
    "/tasks/{task_id}/checklists/{checklist_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Arquivar checklist preservando histórico",
)
async def archive_task_checklist(
    request: Request,
    task_id: UUID,
    checklist_id: UUID,
    data: TaskChecklistArchive,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("tasks", "edit")),
):
    result = await TaskService(db).archive_checklist(
        task_id,
        checklist_id,
        data,
        workspace_id,
        context=await _mutation_context(request, db, auth),
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Tarefa ou checklist não encontrado.")
    return None


@router.get(
    "/tasks/{task_id}/events",
    response_model=TaskEventPage,
    summary="Listar histórico imutável da tarefa",
)
async def list_task_events(
    task_id: UUID,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    __: object = Depends(require_permission("tasks", "view")),
):
    service = TaskService(db)
    allow_workspace = _allow_workspace_task_access(auth)
    task = await service.get_for_actor(
        task_id,
        workspace_id,
        actor_user_id=auth.user_id,
        allow_workspace=allow_workspace,
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    result = await service.list_events(
        task_id,
        workspace_id,
        actor_user_id=auth.user_id,
        allow_workspace=allow_workspace,
        limit=limit,
        offset=offset,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    items, total = result
    if not can_view_sensitive:
        names = await _lead_names(db, task.matter_id, workspace_id)
        items = [
            TaskEventRead.model_validate(item).model_copy(
                update={
                    "before_data": redact_sensitive_metadata(item.before_data, names),
                    "after_data": redact_sensitive_metadata(item.after_data, names),
                    "comment": redact_sensitive_metadata(item.comment, names),
                }
            )
            for item in items
        ]
    return TaskEventPage(items=items, total=total)
