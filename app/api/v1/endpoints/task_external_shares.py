"""Authenticated management and public redemption of legal task shares."""

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.entitlement_errors import enforce_workspace_feature_or_http
from app.core.config import settings
from app.core.deps import (
    AuthContext,
    get_current_auth_context,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.models.entitlement import EntitlementKey
from app.schemas.task_external_share import (
    PublicTaskExternalShare,
    TaskExternalShareCreateRequest,
    TaskExternalShareIssued,
    TaskExternalSharePreview,
    TaskExternalSharePreviewRequest,
    TaskExternalShareRead,
)
from app.schemas.task_whatsapp_share import (
    TaskWhatsAppContact,
    TaskWhatsAppDraftConfirmRequest,
    TaskWhatsAppDraftPrepared,
    TaskWhatsAppDraftPreview,
    TaskWhatsAppDraftPreviewRequest,
)
from app.services.auth_rate_limit_service import request_network_key
from app.services.task_external_share_service import TaskExternalShareService
from app.services.task_service import TaskMutationContext, TaskService
from app.services.task_whatsapp_share_service import TaskWhatsAppShareService

router = APIRouter(tags=["Tarefas jurídicas — compartilhamento externo"])


async def _require_legal_suite(db: AsyncSession, workspace_id: UUID) -> None:
    await enforce_workspace_feature_or_http(db, workspace_id, EntitlementKey.legal_suite)


async def _require_visible_task(
    db: AsyncSession, task_id: UUID, workspace_id: UUID, user_id: UUID
) -> None:
    task = await TaskService(db).get_for_actor(
        task_id, workspace_id, actor_user_id=user_id, allow_workspace=False
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")


@router.post("/tasks/{task_id}/external-shares/preview", response_model=TaskExternalSharePreview)
async def preview_task_external_share(
    task_id: UUID,
    payload: TaskExternalSharePreviewRequest,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("tasks", "edit")),
) -> TaskExternalSharePreview:
    await _require_legal_suite(db, workspace_id)
    result = await TaskExternalShareService(db).preview(
        task_id, workspace_id, payload, actor_user_id=auth.user_id, allow_workspace=False
    )
    return TaskExternalSharePreview.model_validate(result)


@router.post(
    "/tasks/{task_id}/external-shares",
    response_model=TaskExternalShareIssued,
    status_code=status.HTTP_201_CREATED,
)
async def create_task_external_share(
    request: Request,
    task_id: UUID,
    payload: TaskExternalShareCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=180),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("tasks", "edit")),
) -> TaskExternalShareIssued:
    await _require_legal_suite(db, workspace_id)
    context = TaskMutationContext(
        actor_user_id=auth.user_id,
        correlation_id=request.headers.get("X-Correlation-ID", "").strip() or str(uuid4()),
    )
    share, token = await TaskExternalShareService(db).create(
        task_id,
        workspace_id,
        TaskExternalSharePreviewRequest(
            expires_at=payload.expires_at, safe_context=payload.safe_context
        ),
        payload.preview_fingerprint,
        idempotency_key,
        context=context,
        allow_workspace=False,
    )
    await db.commit()
    base_url = settings.FRONTEND_URL.rstrip("/")
    return TaskExternalShareIssued(
        id=share.id,
        # O fragmento não é enviado ao CDN, proxy, servidor ou Referer.
        url=f"{base_url}/task-share#token={token}",
        expires_at=share.expires_at,
        created_at=share.created_at,
    )


@router.get("/tasks/{task_id}/external-shares", response_model=list[TaskExternalShareRead])
async def list_task_external_shares(
    task_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("tasks", "edit")),
) -> list[TaskExternalShareRead]:
    await _require_legal_suite(db, workspace_id)
    await _require_visible_task(db, task_id, workspace_id, auth.user_id)
    return [
        TaskExternalShareRead.model_validate(item)
        for item in await TaskExternalShareService(db).list_for_task(task_id, workspace_id)
    ]


@router.delete("/tasks/{task_id}/external-shares/{share_id}", response_model=TaskExternalShareRead)
async def revoke_task_external_share(
    request: Request,
    task_id: UUID,
    share_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("tasks", "edit")),
) -> TaskExternalShareRead:
    await _require_legal_suite(db, workspace_id)
    await _require_visible_task(db, task_id, workspace_id, auth.user_id)
    share = await TaskExternalShareService(db).revoke(
        share_id,
        task_id,
        workspace_id,
        context=TaskMutationContext(
            actor_user_id=auth.user_id,
            correlation_id=request.headers.get("X-Correlation-ID", "").strip() or str(uuid4()),
        ),
    )
    await db.commit()
    return TaskExternalShareRead.model_validate(share)


@router.get(
    "/tasks/{task_id}/external-shares/whatsapp/contacts",
    response_model=list[TaskWhatsAppContact],
)
async def list_task_share_whatsapp_contacts(
    task_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _task_permission: object = Depends(require_permission("tasks", "edit")),
    _message_permission: object = Depends(require_permission("mensagens", "view")),
) -> list[TaskWhatsAppContact]:
    await _require_legal_suite(db, workspace_id)
    return await TaskWhatsAppShareService(db).list_contacts(
        task_id, workspace_id, actor_user_id=auth.user_id
    )


@router.post(
    "/tasks/{task_id}/external-shares/whatsapp/preview",
    response_model=TaskWhatsAppDraftPreview,
)
async def preview_task_share_whatsapp_draft(
    task_id: UUID,
    payload: TaskWhatsAppDraftPreviewRequest,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _task_permission: object = Depends(require_permission("tasks", "edit")),
    _message_permission: object = Depends(require_permission("mensagens", "view")),
) -> TaskWhatsAppDraftPreview:
    await _require_legal_suite(db, workspace_id)
    return await TaskWhatsAppShareService(db).preview(
        task_id, workspace_id, payload, actor_user_id=auth.user_id
    )


@router.post(
    "/tasks/{task_id}/external-shares/whatsapp/draft",
    response_model=TaskWhatsAppDraftPrepared,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_task_share_whatsapp_draft(
    request: Request,
    task_id: UUID,
    payload: TaskWhatsAppDraftConfirmRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=180),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _task_permission: object = Depends(require_permission("tasks", "edit")),
    _message_view_permission: object = Depends(require_permission("mensagens", "view")),
    _message_send_permission: object = Depends(
        require_permission("mensagens", "send_message")
    ),
) -> TaskWhatsAppDraftPrepared:
    await _require_legal_suite(db, workspace_id)
    result = await TaskWhatsAppShareService(db).prepare(
        task_id,
        workspace_id,
        payload,
        idempotency_key,
        context=TaskMutationContext(
            actor_user_id=auth.user_id,
            correlation_id=request.headers.get("X-Correlation-ID", "").strip() or str(uuid4()),
        ),
    )
    await db.commit()
    return result


@router.post("/public/task-shares/redeem", response_model=PublicTaskExternalShare)
async def redeem_task_external_share(
    request: Request,
    token: str = Header(alias="X-Task-Share-Token", min_length=64, max_length=512),
    db: AsyncSession = Depends(get_db),
) -> PublicTaskExternalShare:
    result = await TaskExternalShareService(db).redeem(
        token,
        request_network_key(request),
        request.headers.get("X-Correlation-ID", "").strip() or str(uuid4()),
    )
    await db.commit()
    return PublicTaskExternalShare.model_validate(result)
