"""API tenant-aware do conselho consultivo Meus Executivos.

Esta superfície persiste conversas, perguntas, compartilhamentos e evidências.
Ela não simula respostas: enquanto não houver um motor executivo habilitado, a
rodada termina com uma mensagem de sistema explicitando a indisponibilidade.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_workspace_id, get_db, require_workspace_admin
from app.models.executive import (
    ExecutiveAuthorType,
    ExecutiveMessage,
    ExecutiveMessageKind,
    ExecutiveReport,
    ExecutiveRound,
    ExecutiveRoundStatus,
    ExecutiveSourceReceipt,
    ExecutiveThread,
    ExecutiveThreadMode,
    ExecutiveThreadShare,
    ExecutiveThreadStatus,
    ExecutiveThreadVisibility,
)
from app.models.user import User
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.schemas.executive import (
    ExecutiveContextReference,
    ExecutiveKey,
    ExecutiveMessageCreate,
    ExecutiveThreadCreate,
    ExecutiveThreadShareCreate,
    ExecutiveThreadUpdate,
)
from app.schemas.executive import (
    ExecutiveThreadStatus as SchemaThreadStatus,
)
from app.schemas.executive import (
    ExecutiveThreadVisibility as SchemaThreadVisibility,
)
from app.services.executive_service import ExecutivePolicyViolation, ExecutiveService

router = APIRouter(prefix="/executives", tags=["Meus Executivos"])


class ExecutiveThreadPatch(ExecutiveThreadUpdate):
    visibility: SchemaThreadVisibility | None = None


class ExecutiveMessageRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    body: str = Field(min_length=1, max_length=30_000)
    requested_executives: list[ExecutiveKey] = Field(
        default_factory=list, alias="requestedExecutives"
    )
    mentioned_executives: list[ExecutiveKey] = Field(
        default_factory=list, alias="mentionedExecutives"
    )
    prevent_additional_invites: bool = Field(default=False, alias="preventAdditionalInvites")
    allow_additional_executives: bool | None = Field(
        default=None, alias="allowAdditionalExecutives"
    )
    context: list[dict[str, str]] = Field(default_factory=list, max_length=20)
    context_references: list[ExecutiveContextReference] = Field(
        default_factory=list, alias="contextReferences", max_length=20
    )
    idempotency_key: str | None = Field(
        default=None, alias="idempotencyKey", min_length=8, max_length=160
    )


@router.get("/share-candidates")
async def list_share_candidates(
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Lista destinatários elegíveis pelo papel da membership do workspace."""
    _authorize(actor, "executives.share")
    rows = (
        await db.execute(
            select(WorkspaceMembership, User)
            .join(User, User.id == WorkspaceMembership.user_id)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.status == MembershipStatus.active,
                WorkspaceMembership.role.in_(["workspace_owner", "workspace_admin"]),
                WorkspaceMembership.user_id != actor.user_id,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
            .order_by(User.name.asc())
        )
    ).all()
    return [
        {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "initials": user.initials,
            "workspaceRole": membership.role.value,
        }
        for membership, user in rows
    ]


def _error(error: ExecutivePolicyViolation) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN
        if "ACCESS_DENIED" in error.code
        else status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": error.code, "message": str(error)},
    )


def _authorize(actor: AuthContext, capability: str) -> None:
    try:
        ExecutiveService.require_owner_or_admin(actor.role)
        ExecutiveService.require_capability(capability)
    except ExecutivePolicyViolation as error:
        raise _error(error) from error
    if actor.is_support_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "EXECUTIVE_ACCESS_DENIED",
                "message": "Acesso temporário de suporte não abre o conselho executivo.",
            },
        )


def _thread_read(row: ExecutiveThread) -> dict[str, Any]:
    def enum_value(value: Any) -> Any:
        return value.value if hasattr(value, "value") else value

    return {
        "id": row.id,
        "workspaceId": row.workspace_id,
        "createdByUserId": row.created_by_user_id,
        "title": row.title or "Nova conversa executiva",
        "visibility": enum_value(row.visibility),
        "status": enum_value(row.status),
        "mode": enum_value(row.mode),
        "selectedExecutives": row.selected_executives,
        "ceoAlwaysPresent": row.ceo_always_present,
        "lastMessageAt": row.last_message_at,
        "version": row.version,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


def _message_read(row: ExecutiveMessage) -> dict[str, Any]:
    return {
        "id": row.id,
        "threadId": row.thread_id,
        "roundId": row.round_id,
        "authorType": row.author_type.value,
        "authorUserId": row.author_user_id,
        "executiveKey": row.executive_key.value if row.executive_key else None,
        "body": row.body,
        "messageKind": row.message_kind.value,
        "sequence": row.sequence,
        "status": row.status,
        "sourceReceiptId": row.source_receipt_id,
        "modelRunId": row.model_run_id,
        "sources": [],
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


async def _thread_or_404(
    db: AsyncSession,
    workspace_id: UUID,
    thread_id: UUID,
    actor_user_id: UUID,
    *,
    owner_only: bool = False,
) -> ExecutiveThread:
    access_filter = ExecutiveThread.created_by_user_id == actor_user_id
    if not owner_only:
        access_filter = or_(
            access_filter,
            exists().where(
                ExecutiveThreadShare.workspace_id == workspace_id,
                ExecutiveThreadShare.thread_id == ExecutiveThread.id,
                ExecutiveThreadShare.user_id == actor_user_id,
                ExecutiveThreadShare.revoked_at.is_(None),
            ),
        )
    row = await db.scalar(
        select(ExecutiveThread).where(
            ExecutiveThread.id == thread_id,
            ExecutiveThread.workspace_id == workspace_id,
            ExecutiveThread.status != ExecutiveThreadStatus.deleted,
            access_filter,
        )
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "EXECUTIVE_THREAD_NOT_FOUND",
                "message": "Conversa executiva não encontrada neste workspace.",
            },
        )
    return row


async def _round_read(db: AsyncSession, row: ExecutiveRound) -> dict[str, Any]:
    messages = (
        await db.scalars(
            select(ExecutiveMessage)
            .where(
                ExecutiveMessage.workspace_id == row.workspace_id,
                ExecutiveMessage.round_id == row.id,
            )
            .order_by(ExecutiveMessage.sequence)
        )
    ).all()
    return {
        "id": row.id,
        "threadId": row.thread_id,
        "userMessageId": row.user_message_id,
        "status": row.status.value,
        "requestedExecutives": row.requested_executives,
        "selectedByCeo": row.selected_by_ceo,
        "selectionReasons": row.selection_reasons,
        "contextScope": row.context_scope,
        "messages": [_message_read(item) for item in messages],
        "startedAt": row.started_at,
        "completedAt": row.completed_at,
        "cancelledAt": row.cancelled_at,
        "errorSummary": row.error_summary,
    }


@router.get("/profiles")
async def list_profiles(
    actor: AuthContext = Depends(require_workspace_admin),
) -> list[dict[str, Any]]:
    _authorize(actor, "executives.view")
    return [
        profile.model_dump(mode="json", by_alias=True)
        for profile in ExecutiveService.list_profiles()
    ]


@router.get("/profiles/{executive_key}")
async def get_profile(
    executive_key: str, actor: AuthContext = Depends(require_workspace_admin)
) -> dict[str, Any]:
    _authorize(actor, "executives.view")
    try:
        profile = ExecutiveService.get_profile(executive_key)
    except ExecutivePolicyViolation as error:
        raise HTTPException(
            status_code=404, detail={"code": error.code, "message": str(error)}
        ) from error
    return profile.model_dump(mode="json", by_alias=True)


@router.get("/threads")
async def list_threads(
    thread_status: SchemaThreadStatus = Query(default=SchemaThreadStatus.active, alias="status"),
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    _authorize(actor, "executives.view")
    shared = exists().where(
        ExecutiveThreadShare.workspace_id == workspace_id,
        ExecutiveThreadShare.thread_id == ExecutiveThread.id,
        ExecutiveThreadShare.user_id == actor.user_id,
        ExecutiveThreadShare.revoked_at.is_(None),
    )
    rows = (
        await db.scalars(
            select(ExecutiveThread)
            .where(
                ExecutiveThread.workspace_id == workspace_id,
                ExecutiveThread.status == ExecutiveThreadStatus(thread_status.value),
                or_(ExecutiveThread.created_by_user_id == actor.user_id, shared),
            )
            .order_by(
                ExecutiveThread.last_message_at.desc().nullslast(),
                ExecutiveThread.created_at.desc(),
            )
        )
    ).all()
    return [_thread_read(row) for row in rows]


@router.post("/threads", status_code=status.HTTP_201_CREATED)
async def create_thread(
    payload: ExecutiveThreadCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    _authorize(actor, "executives.chat")
    try:
        payload = ExecutiveService.validate_thread_create(payload)
    except ExecutivePolicyViolation as error:
        raise _error(error) from error
    now = datetime.now(UTC)
    row = ExecutiveThread(
        workspace_id=workspace_id,
        created_by_user_id=actor.user_id,
        title=payload.title or "Nova conversa executiva",
        visibility=ExecutiveThreadVisibility(payload.visibility.value),
        mode=ExecutiveThreadMode(payload.mode.value),
        selected_executives=[key.value for key in payload.selected_executives],
        ceo_always_present=True,
        version=1,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.flush()
    return _thread_read(row)


@router.get("/threads/{thread_id}")
async def get_thread(
    thread_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    _authorize(actor, "executives.view")
    return _thread_read(await _thread_or_404(db, workspace_id, thread_id, actor.user_id))


@router.patch("/threads/{thread_id}")
async def update_thread(
    payload: ExecutiveThreadPatch,
    thread_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    _authorize(actor, "executives.chat")
    row = await _thread_or_404(db, workspace_id, thread_id, actor.user_id, owner_only=True)
    if payload.title is not None:
        row.title, row.title_manually_edited = payload.title, True
    if payload.visibility is not None:
        row.visibility = ExecutiveThreadVisibility(payload.visibility.value)
    if payload.status is not None:
        row.status = ExecutiveThreadStatus(payload.status.value)
    if payload.selected_executives is not None:
        try:
            selection = ExecutiveService.normalize_participants(payload.selected_executives)
        except ExecutivePolicyViolation as error:
            raise _error(error) from error
        row.selected_executives = [key.value for key in selection.participants]
    row.version += 1
    await db.flush()
    return _thread_read(row)


@router.post("/threads/{thread_id}/archive")
async def archive_thread(
    thread_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    _authorize(actor, "executives.chat")
    row = await _thread_or_404(db, workspace_id, thread_id, actor.user_id, owner_only=True)
    row.status = ExecutiveThreadStatus.archived
    row.version += 1
    await db.flush()
    return _thread_read(row)


@router.delete(
    "/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
async def delete_thread(
    thread_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    _authorize(actor, "executives.chat")
    row = await _thread_or_404(db, workspace_id, thread_id, actor.user_id, owner_only=True)
    row.status, row.deleted_at = ExecutiveThreadStatus.deleted, datetime.now(UTC)
    row.version += 1
    await db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/threads/{thread_id}/shares")
async def list_shares(
    thread_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    _authorize(actor, "executives.share")
    await _thread_or_404(db, workspace_id, thread_id, actor.user_id, owner_only=True)
    rows = (
        await db.scalars(
            select(ExecutiveThreadShare).where(
                ExecutiveThreadShare.workspace_id == workspace_id,
                ExecutiveThreadShare.thread_id == thread_id,
                ExecutiveThreadShare.revoked_at.is_(None),
            )
        )
    ).all()
    return [
        {
            "threadId": row.thread_id,
            "userId": row.user_id,
            "grantedBy": row.granted_by_user_id,
            "grantedAt": row.granted_at,
            "revokedAt": row.revoked_at,
            "permission": row.permission,
        }
        for row in rows
    ]


@router.post("/threads/{thread_id}/shares", status_code=status.HTTP_201_CREATED)
async def create_share(
    payload: ExecutiveThreadShareCreate,
    thread_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    _authorize(actor, "executives.share")
    thread = await _thread_or_404(
        db, workspace_id, thread_id, actor.user_id, owner_only=True
    )
    user_id = payload.user_id
    member = await db.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.status == MembershipStatus.active,
        )
    )
    if member is None or await db.get(User, user_id) is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "EXECUTIVE_SHARE_TARGET_NOT_FOUND",
                "message": "Usuário ativo não pertence a este workspace.",
            },
        )
    try:
        ExecutiveService.require_share_target(
            actor_workspace_id=str(workspace_id),
            target_workspace_id=str(member.workspace_id),
            target_membership_status=member.status,
            target_role=member.role,
        )
    except ExecutivePolicyViolation as error:
        raise _error(error) from error
    existing = await db.scalar(
        select(ExecutiveThreadShare).where(
            ExecutiveThreadShare.workspace_id == workspace_id,
            ExecutiveThreadShare.thread_id == thread_id,
            ExecutiveThreadShare.user_id == user_id,
        )
    )
    now = datetime.now(UTC)
    if existing:
        existing.revoked_at, existing.granted_at, existing.granted_by_user_id = (
            None,
            now,
            actor.user_id,
        )
        row = existing
    else:
        row = ExecutiveThreadShare(
            workspace_id=workspace_id,
            thread_id=thread_id,
            user_id=user_id,
            granted_by_user_id=actor.user_id,
            granted_at=now,
            permission="view_and_reply",
        )
        db.add(row)
    thread.visibility = ExecutiveThreadVisibility.shared
    thread.version += 1
    await db.flush()
    return {
        "threadId": row.thread_id,
        "userId": row.user_id,
        "grantedBy": row.granted_by_user_id,
        "grantedAt": row.granted_at,
        "revokedAt": row.revoked_at,
        "permission": row.permission,
    }


@router.delete(
    "/threads/{thread_id}/shares/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def revoke_share(
    thread_id: UUID,
    user_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    _authorize(actor, "executives.share")
    thread = await _thread_or_404(
        db, workspace_id, thread_id, actor.user_id, owner_only=True
    )
    row = await db.scalar(
        select(ExecutiveThreadShare).where(
            ExecutiveThreadShare.workspace_id == workspace_id,
            ExecutiveThreadShare.thread_id == thread_id,
            ExecutiveThreadShare.user_id == user_id,
            ExecutiveThreadShare.revoked_at.is_(None),
        )
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "EXECUTIVE_SHARE_NOT_FOUND",
                "message": "Compartilhamento não encontrado.",
            },
        )
    row.revoked_at = datetime.now(UTC)
    active_share_count = await db.scalar(
        select(func.count(ExecutiveThreadShare.user_id)).where(
            ExecutiveThreadShare.workspace_id == workspace_id,
            ExecutiveThreadShare.thread_id == thread_id,
            ExecutiveThreadShare.revoked_at.is_(None),
        )
    )
    if (active_share_count or 0) == 0:
        thread.visibility = ExecutiveThreadVisibility.private
    thread.version += 1
    await db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/threads/{thread_id}/messages")
async def list_messages(
    thread_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    _authorize(actor, "executives.view")
    await _thread_or_404(db, workspace_id, thread_id, actor.user_id)
    rows = (
        await db.scalars(
            select(ExecutiveMessage)
            .where(
                ExecutiveMessage.workspace_id == workspace_id,
                ExecutiveMessage.thread_id == thread_id,
            )
            .order_by(ExecutiveMessage.created_at, ExecutiveMessage.sequence)
        )
    ).all()
    return [_message_read(row) for row in rows]


@router.post("/threads/{thread_id}/messages", status_code=status.HTTP_202_ACCEPTED)
async def create_message(
    payload: ExecutiveMessageRequest,
    thread_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    _authorize(actor, "executives.chat")
    thread = await _thread_or_404(db, workspace_id, thread_id, actor.user_id)
    canonical = ExecutiveMessageCreate(
        body=payload.body,
        requested_executives=payload.requested_executives or payload.mentioned_executives,
        prevent_additional_invites=payload.prevent_additional_invites,
        context={"legacyContext": payload.context},
        context_references=payload.context_references,
        idempotency_key=payload.idempotency_key,
    )
    if canonical.idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "EXECUTIVE_IDEMPOTENCY_UNAVAILABLE",
                "message": (
                    "Idempotência durável ainda não está habilitada para rodadas executivas."
                ),
            },
        )
    try:
        selection = ExecutiveService.normalize_participants(
            [*thread.selected_executives, *canonical.requested_executives],
            message_body=canonical.body,
        )
    except ExecutivePolicyViolation as error:
        raise _error(error) from error
    now = datetime.now(UTC)
    round_row = ExecutiveRound(
        workspace_id=workspace_id,
        thread_id=thread.id,
        status=ExecutiveRoundStatus.failed,
        requested_executives=[key.value for key in selection.participants],
        selected_by_ceo=[],
        selection_reasons={},
        context_scope={
            "references": [
                item.model_dump(mode="json", by_alias=True) for item in canonical.context_references
            ],
            "legacyContext": payload.context,
        },
        started_at=now,
        completed_at=now,
        error_summary="Motor executivo indisponível; nenhuma resposta foi simulada.",
        created_at=now,
        updated_at=now,
    )
    db.add(round_row)
    await db.flush()
    question = ExecutiveMessage(
        workspace_id=workspace_id,
        thread_id=thread.id,
        round_id=round_row.id,
        author_type=ExecutiveAuthorType.human,
        author_user_id=actor.user_id,
        body=canonical.body,
        message_kind=ExecutiveMessageKind.question,
        sequence=0,
        status="completed",
        created_at=now,
        updated_at=now,
    )
    notice = ExecutiveMessage(
        workspace_id=workspace_id,
        thread_id=thread.id,
        round_id=round_row.id,
        author_type=ExecutiveAuthorType.system,
        body=(
            "Motor executivo indisponível. Sua pergunta foi registrada, "
            "mas nenhuma resposta de IA foi fabricada."
        ),
        message_kind=ExecutiveMessageKind.error,
        sequence=1,
        status="failed",
        created_at=now,
        updated_at=now,
    )
    db.add_all([question, notice])
    await db.flush()
    round_row.user_message_id = question.id
    thread.last_message_at = now
    await db.flush()
    return await _round_read(db, round_row)


@router.post("/rounds/{round_id}/cancel")
async def cancel_round(
    round_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    _authorize(actor, "executives.chat")
    row = await db.scalar(
        select(ExecutiveRound).where(
            ExecutiveRound.id == round_id, ExecutiveRound.workspace_id == workspace_id
        )
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "EXECUTIVE_ROUND_NOT_FOUND", "message": "Rodada não encontrada."},
        )
    if row.status not in {ExecutiveRoundStatus.completed, ExecutiveRoundStatus.cancelled}:
        row.status, row.cancelled_at, row.completed_at = (
            ExecutiveRoundStatus.cancelled,
            datetime.now(UTC),
            datetime.now(UTC),
        )
        await db.flush()
    return await _round_read(db, row)


@router.post("/rounds/{round_id}/retry", status_code=status.HTTP_409_CONFLICT)
async def retry_round(
    round_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    _authorize(actor, "executives.chat")
    exists = await db.scalar(
        select(func.count(ExecutiveRound.id)).where(
            ExecutiveRound.id == round_id, ExecutiveRound.workspace_id == workspace_id
        )
    )
    if not exists:
        raise HTTPException(
            status_code=404,
            detail={"code": "EXECUTIVE_ROUND_NOT_FOUND", "message": "Rodada não encontrada."},
        )
    raise HTTPException(
        status_code=409,
        detail={
            "code": "EXECUTIVE_ENGINE_UNAVAILABLE",
            "message": "O motor executivo continua indisponível; nenhuma resposta será simulada.",
        },
    )


@router.get("/threads/{thread_id}/sources")
async def list_sources(
    thread_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    _authorize(actor, "executives.view")
    await _thread_or_404(db, workspace_id, thread_id, actor.user_id)
    rows = (
        await db.scalars(
            select(ExecutiveSourceReceipt)
            .where(
                ExecutiveSourceReceipt.workspace_id == workspace_id,
                ExecutiveSourceReceipt.thread_id == thread_id,
            )
            .order_by(ExecutiveSourceReceipt.generated_at.desc())
        )
    ).all()
    return [
        {
            "id": row.id,
            "workspaceId": row.workspace_id,
            "threadId": row.thread_id,
            "roundId": row.round_id,
            "executiveKey": row.executive_key.value if row.executive_key else None,
            "pipelineVersion": row.pipeline_version,
            "queryHash": row.query_hash,
            "period": row.period,
            "structuredQueries": row.structured_queries,
            "selectedSources": row.selected_sources,
            "rejectedSources": row.rejected_sources,
            "insufficiencyReasons": row.insufficiency_reasons,
            "generatedAt": row.generated_at,
        }
        for row in rows
    ]


@router.get("/reports")
async def list_reports(
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    _authorize(actor, "executives.reports")
    rows = (
        await db.scalars(
            select(ExecutiveReport)
            .where(ExecutiveReport.workspace_id == workspace_id)
            .order_by(ExecutiveReport.period_end.desc(), ExecutiveReport.created_at.desc())
        )
    ).all()
    return [
        {
            "id": row.id,
            "workspaceId": row.workspace_id,
            "periodStart": row.period_start,
            "periodEnd": row.period_end,
            "status": row.status.value,
            "title": row.title,
            "body": row.body or "",
            "executiveParticipants": row.executive_participants,
            "sourceReceiptId": row.source_receipt_id,
            "generationKey": row.generation_key,
            "publishedAt": row.published_at,
            "supersededBy": row.superseded_by_id,
            "createdAt": row.created_at,
            "updatedAt": row.updated_at,
        }
        for row in rows
    ]
