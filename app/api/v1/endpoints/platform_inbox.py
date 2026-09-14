"""Inbox omnichannel própria da equipe do SaaS."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.klm_runtime import external_delivery_allowed
from app.core.platform_audit import (
    record_canonical_platform_mutation,
    snapshot_platform_inbox_conversation,
    snapshot_platform_inbox_message,
)
from app.core.platform_rbac import has_platform_permission
from app.core.request_body import read_bounded_body
from app.models.auth_security import AuthSecurityEvent
from app.models.growth_crm import GrowthProspect
from app.models.platform_inbox import (
    PlatformInboxChannel,
    PlatformInboxConversation,
    PlatformInboxDeliveryStatus,
    PlatformInboxDirection,
    PlatformInboxMessage,
    PlatformInboxQueue,
    PlatformInboxStatus,
)
from app.models.support_ticket import SupportTicket
from app.models.support_ticket import (
    SupportTicketPriority,
    SupportTicketQueue,
    SupportTicketSeverity,
    SupportTicketType,
)
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.models.platform_official_whatsapp import PlatformOfficialWhatsAppRole
from app.models.platform_official_whatsapp import PlatformOfficialWhatsAppConnection
from app.models.whatsapp import WaConnectionStatus
from app.services.platform_official_whatsapp_service import PlatformOfficialWhatsAppService
from app.services.platform_growth_service import PlatformGrowthService
from app.services.platform_inbox_email_service import PlatformInboxEmailService
from app.services.platform_inbox_meta_service import PlatformInboxMetaService
from app.services.support_ticket_service import SupportTicketService
from app.schemas.support_ticket import SupportTicketCreate

router = APIRouter(prefix="/platform/inbox", tags=["Platform Inbox"])
public_webchat_router = APIRouter(prefix="/public/platform-webchat", tags=["Public Platform Webchat"])
public_meta_router = APIRouter(prefix="/public/platform-meta", tags=["Public Platform Meta"])
public_email_router = APIRouter(prefix="/public/platform-email", tags=["Public Platform Email"])


def _sla_deadlines(queue: PlatformInboxQueue, now: datetime) -> tuple[datetime, datetime]:
    """SLA operacional inicial; explícito e persistido para não depender da UI."""
    if queue == PlatformInboxQueue.support:
        return now + timedelta(hours=1), now + timedelta(hours=24)
    return now + timedelta(hours=4), now + timedelta(hours=48)


async def _inbox_access(
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> User:
    if context.surface != "platform":
        raise HTTPException(status_code=403, detail={"code": "BLACKBOX-IDENTITY-REQUIRED"})
    user = await db.get(User, context.user_id)
    if not (
        has_platform_permission(user, "support", "view")
        or has_platform_permission(user, "sales", "view")
    ):
        raise HTTPException(status_code=403, detail={"code": "PLATFORM-PERMISSION-DENIED"})
    return user


async def _inbox_write(
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> User:
    if context.surface != "platform":
        raise HTTPException(status_code=403, detail={"code": "BLACKBOX-IDENTITY-REQUIRED"})
    user = await db.get(User, context.user_id)
    if not (
        has_platform_permission(user, "support", "respond")
        or has_platform_permission(user, "sales", "manage")
    ):
        raise HTTPException(status_code=403, detail={"code": "PLATFORM-PERMISSION-DENIED"})
    return user


async def _inbox_sales_write(
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> User:
    if context.surface != "platform":
        raise HTTPException(status_code=403, detail={"code": "BLACKBOX-IDENTITY-REQUIRED"})
    user = await db.get(User, context.user_id)
    if not has_platform_permission(user, "sales", "manage"):
        raise HTTPException(status_code=403, detail={"code": "PLATFORM-PERMISSION-DENIED"})
    return user


async def _inbox_support_write(
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> User:
    if context.surface != "platform":
        raise HTTPException(status_code=403, detail={"code": "BLACKBOX-IDENTITY-REQUIRED"})
    user = await db.get(User, context.user_id)
    if not has_platform_permission(user, "support", "respond"):
        raise HTTPException(status_code=403, detail={"code": "PLATFORM-PERMISSION-DENIED"})
    return user


class ConversationCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    channel: PlatformInboxChannel
    contact_name: str = Field(alias="contactName", min_length=1, max_length=200)
    contact_address: str | None = Field(default=None, alias="contactAddress", max_length=320)
    subject: str | None = Field(default=None, max_length=240)
    queue: PlatformInboxQueue
    initial_message: str = Field(alias="initialMessage", min_length=1, max_length=20_000)
    external_thread_id: str | None = Field(default=None, alias="externalThreadId", max_length=255)


class ConversationPatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    status: PlatformInboxStatus | None = None
    queue: PlatformInboxQueue | None = None
    assigned_to_user_id: UUID | None = Field(default=None, alias="assignedToUserId")
    prospect_id: UUID | None = Field(default=None, alias="prospectId")
    support_ticket_id: UUID | None = Field(default=None, alias="supportTicketId")
    workspace_id: UUID | None = Field(default=None, alias="workspaceId")


class MessageCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    body: str = Field(min_length=1, max_length=20_000)
    visibility: Literal["public", "internal"] = "public"


class CreateLeadFromConversation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    office_name: str | None = Field(default=None, alias="officeName", max_length=200)
    notes: str | None = Field(default=None, max_length=2_000)


class CreateTicketFromConversation(BaseModel):
    subject: str | None = Field(default=None, max_length=240)
    description: str | None = Field(default=None, min_length=10, max_length=20_000)
    severity: SupportTicketSeverity = SupportTicketSeverity.medium


class PublicWebchatStart(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    contact_name: str = Field(alias="contactName", min_length=1, max_length=200)
    contact_email: str | None = Field(default=None, alias="contactEmail", max_length=320)
    subject: str | None = Field(default=None, max_length=240)
    initial_message: str = Field(alias="initialMessage", min_length=1, max_length=20_000)
    intent: Literal["sales", "support"] = "sales"


class PublicWebchatMessage(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)


def _webchat_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _public_conversation(db: AsyncSession, conversation_id: UUID, token: str) -> PlatformInboxConversation:
    item = await db.scalar(
        select(PlatformInboxConversation)
        .options(selectinload(PlatformInboxConversation.messages))
        .where(
            PlatformInboxConversation.id == conversation_id,
            PlatformInboxConversation.channel == PlatformInboxChannel.webchat,
        )
    )
    supplied = _webchat_token_hash(token)
    if item is None or not item.public_access_token_hash or not hmac.compare_digest(item.public_access_token_hash, supplied):
        raise HTTPException(status_code=404, detail={"code": "PLATFORM-WEBCHAT-NOT-FOUND"})
    return item


def _message_data(item: PlatformInboxMessage) -> dict:
    return {
        "id": str(item.id), "conversationId": str(item.conversation_id),
        "direction": item.direction.value, "body": item.body,
        "deliveryStatus": item.delivery_status.value,
        "actorUserId": str(item.actor_user_id) if item.actor_user_id else None,
        "actorLabel": item.actor_label, "externalMessageId": item.external_message_id,
        "occurredAt": item.occurred_at.isoformat(),
    }


def _conversation_data(item: PlatformInboxConversation, *, include_messages: bool = False) -> dict:
    data = {
        "id": str(item.id), "channel": item.channel.value,
        "externalThreadId": item.external_thread_id, "contactName": item.contact_name,
        "contactAddress": item.contact_address, "subject": item.subject,
        "queue": item.queue.value, "status": item.status.value,
        "assignedToUserId": str(item.assigned_to_user_id) if item.assigned_to_user_id else None,
        "prospectId": str(item.prospect_id) if item.prospect_id else None,
        "supportTicketId": str(item.support_ticket_id) if item.support_ticket_id else None,
        "workspaceId": str(item.workspace_id) if item.workspace_id else None,
        "lastMessagePreview": item.last_message_preview,
        "lastMessageAt": item.last_message_at.isoformat() if item.last_message_at else None,
        "firstResponseDueAt": item.first_response_due_at.isoformat() if item.first_response_due_at else None,
        "firstRespondedAt": item.first_responded_at.isoformat() if item.first_responded_at else None,
        "resolutionDueAt": item.resolution_due_at.isoformat() if item.resolution_due_at else None,
        "resolvedAt": item.resolved_at.isoformat() if item.resolved_at else None,
        "unreadCount": item.unread_count, "createdAt": item.created_at.isoformat(),
        "updatedAt": item.updated_at.isoformat(),
    }
    if include_messages:
        data["messages"] = [_message_data(message) for message in item.messages]
    return data


@public_meta_router.get("/webhook", include_in_schema=False)
async def verify_platform_meta_webhook(
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
):
    configured = settings.PLATFORM_META_VERIFY_TOKEN.strip()
    if configured and hub_mode == "subscribe" and hmac.compare_digest(configured, hub_verify_token):
        try:
            return int(hub_challenge)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"code": "PLATFORM-META-002"}) from exc
    raise HTTPException(status_code=403, detail={"code": "PLATFORM-META-001"})


@public_meta_router.post("/webhook", include_in_schema=False)
async def receive_platform_meta_webhook(
    request: Request,
    x_hub_signature_256: str = Header(default=""),
    db: AsyncSession = Depends(get_db),
) -> dict:
    body = await read_bounded_body(request)
    secret = settings.META_APP_SECRET.strip()
    if not secret or not settings.PLATFORM_META_VERIFY_TOKEN.strip():
        raise HTTPException(status_code=503, detail={"code": "PLATFORM-META-NOT-CONFIGURED"})
    supplied = x_hub_signature_256.removeprefix("sha256=")
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail={"code": "PLATFORM-META-SIGNATURE-INVALID"})
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("root")
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail={"code": "PLATFORM-META-PAYLOAD-INVALID"}) from exc
    return await PlatformInboxMetaService(db).ingest(payload)


@public_email_router.post("/webhook", include_in_schema=False)
async def receive_platform_email_webhook(
    request: Request,
    svix_id: str = Header(alias="svix-id", default=""),
    svix_timestamp: str = Header(alias="svix-timestamp", default=""),
    svix_signature: str = Header(alias="svix-signature", default=""),
    db: AsyncSession = Depends(get_db),
) -> dict:
    body = await read_bounded_body(request)
    if not PlatformInboxEmailService.verify_svix(
        body=body, message_id=svix_id, timestamp=svix_timestamp, signature=svix_signature
    ):
        raise HTTPException(status_code=403, detail={"code": "PLATFORM-EMAIL-SIGNATURE-INVALID"})
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("root")
        return await PlatformInboxEmailService(db).ingest(payload, svix_id=svix_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail={"code": "PLATFORM-EMAIL-PAYLOAD-INVALID"}) from exc
    except RuntimeError as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail={"code": "PLATFORM-EMAIL-RETRY", "message": str(exc)}) from exc


@public_webchat_router.post("/conversations", status_code=status.HTTP_201_CREATED, summary="Iniciar conversa pública com a equipe do SaaS")
async def start_public_webchat(payload: PublicWebchatStart, db: AsyncSession = Depends(get_db)) -> dict:
    now = datetime.now(timezone.utc)
    queue = PlatformInboxQueue.support if payload.intent == "support" else PlatformInboxQueue.sales
    first_response_due_at, resolution_due_at = _sla_deadlines(queue, now)
    token = secrets.token_urlsafe(32)
    conversation = PlatformInboxConversation(
        channel=PlatformInboxChannel.webchat,
        external_thread_id=f"webchat:{secrets.token_hex(16)}",
        public_access_token_hash=_webchat_token_hash(token),
        contact_name=payload.contact_name.strip(),
        contact_address=payload.contact_email,
        subject=payload.subject,
        queue=queue,
        status=PlatformInboxStatus.open,
        last_message_preview=payload.initial_message[:300],
        last_message_at=now,
        unread_count=1,
        first_response_due_at=first_response_due_at,
        resolution_due_at=resolution_due_at,
        metadata_json={"source": "public_webchat"},
    )
    db.add(conversation)
    await db.flush()
    db.add(PlatformInboxMessage(
        conversation_id=conversation.id,
        direction=PlatformInboxDirection.inbound,
        body=payload.initial_message.strip(),
        delivery_status=PlatformInboxDeliveryStatus.received,
        actor_label=payload.contact_name.strip(),
        idempotency_key=f"webchat:start:{secrets.token_hex(12)}",
        occurred_at=now,
    ))
    await db.commit()
    return {"conversationId": str(conversation.id), "accessToken": token, "status": conversation.status.value}


@public_webchat_router.get("/conversations/{conversation_id}", summary="Consultar mensagens públicas da sessão de webchat")
async def get_public_webchat(
    conversation_id: UUID,
    x_webchat_token: str = Header(alias="X-Webchat-Token", min_length=20, max_length=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    item = await _public_conversation(db, conversation_id, x_webchat_token)
    messages = [message for message in item.messages if message.direction != PlatformInboxDirection.internal]
    return {
        "conversationId": str(item.id),
        "status": item.status.value,
        "messages": [_message_data(message) for message in messages],
    }


@public_webchat_router.post("/conversations/{conversation_id}/messages", status_code=status.HTTP_201_CREATED, summary="Enviar mensagem na sessão pública de webchat")
async def add_public_webchat_message(
    conversation_id: UUID,
    payload: PublicWebchatMessage,
    x_webchat_token: str = Header(alias="X-Webchat-Token", min_length=20, max_length=200),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=120),
    db: AsyncSession = Depends(get_db),
) -> dict:
    item = await _public_conversation(db, conversation_id, x_webchat_token)
    existing = await db.scalar(select(PlatformInboxMessage).where(
        PlatformInboxMessage.conversation_id == item.id,
        PlatformInboxMessage.idempotency_key == idempotency_key,
    ))
    if existing is not None:
        return _message_data(existing)
    now = datetime.now(timezone.utc)
    message = PlatformInboxMessage(
        conversation_id=item.id,
        direction=PlatformInboxDirection.inbound,
        body=payload.body.strip(),
        delivery_status=PlatformInboxDeliveryStatus.received,
        actor_label=item.contact_name,
        idempotency_key=idempotency_key,
        occurred_at=now,
    )
    db.add(message)
    item.last_message_preview = payload.body[:300]
    item.last_message_at = now
    item.unread_count += 1
    if item.status == PlatformInboxStatus.resolved:
        item.status = PlatformInboxStatus.open
        item.resolved_at = None
        item.first_responded_at = None
        item.first_response_due_at, item.resolution_due_at = _sla_deadlines(item.queue, now)
    await db.commit(); await db.refresh(message)
    return _message_data(message)


@router.get("/conversations", summary="Listar conversas dos canais próprios do SaaS")
async def list_conversations(
    channel: PlatformInboxChannel | None = None,
    queue: PlatformInboxQueue | None = None,
    conversation_status: PlatformInboxStatus | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=500),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_inbox_access),
) -> dict:
    clauses = []
    if channel: clauses.append(PlatformInboxConversation.channel == channel)
    if queue: clauses.append(PlatformInboxConversation.queue == queue)
    if conversation_status: clauses.append(PlatformInboxConversation.status == conversation_status)
    if search:
        term = f"%{search.strip()}%"
        clauses.append(or_(
            PlatformInboxConversation.contact_name.ilike(term),
            PlatformInboxConversation.contact_address.ilike(term),
            PlatformInboxConversation.subject.ilike(term),
        ))
    sort_time = func.coalesce(
        PlatformInboxConversation.last_message_at,
        PlatformInboxConversation.created_at,
    )
    if cursor:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            decoded = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
            cursor_time = datetime.fromisoformat(str(decoded["timestamp"]).replace("Z", "+00:00"))
            cursor_id = UUID(str(decoded["id"]))
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail="Cursor de conversas inválido.") from exc
        clauses.append(or_(
            sort_time < cursor_time,
            (sort_time == cursor_time) & (PlatformInboxConversation.id < cursor_id),
        ))
    statement = select(PlatformInboxConversation).order_by(
        sort_time.desc(),
        PlatformInboxConversation.id.desc(),
    ).limit(limit + 1)
    if clauses: statement = statement.where(*clauses)
    rows = list(await db.scalars(statement))
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        timestamp = last.last_message_at or last.created_at
        payload = json.dumps({"timestamp": timestamp.isoformat(), "id": str(last.id)}, separators=(",", ":"))
        next_cursor = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return {
        "items": [_conversation_data(item) for item in items],
        "count": len(items),
        "limit": limit,
        "nextCursor": next_cursor,
        "hasMore": has_more,
    }


@router.post("/conversations", status_code=status.HTTP_201_CREATED, summary="Registrar conversa recebida por canal próprio")
async def create_conversation(
    payload: ConversationCreate,
    request: Request,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=120),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_inbox_write),
    context: AuthContext = Depends(get_current_auth_context),
) -> dict:
    existing = await db.scalar(select(PlatformInboxMessage).where(PlatformInboxMessage.idempotency_key == idempotency_key))
    if existing:
        response.status_code = status.HTTP_200_OK
        conversation = await db.scalar(
            select(PlatformInboxConversation)
            .options(selectinload(PlatformInboxConversation.messages))
            .where(PlatformInboxConversation.id == existing.conversation_id)
        )
        snapshot = await snapshot_platform_inbox_conversation(db, conversation.id)
        await record_canonical_platform_mutation(
            request, context, db,
            before_snapshot=snapshot, after_snapshot=snapshot,
            module="support" if conversation.queue == PlatformInboxQueue.support else "sales",
            action="create_inbox_conversation",
            reason="Repetição idempotente de registro de conversa.",
            reason_source="system",
        )
        return _conversation_data(conversation, include_messages=True)
    now = datetime.now(timezone.utc)
    first_response_due_at, resolution_due_at = _sla_deadlines(payload.queue, now)
    conversation = PlatformInboxConversation(
        channel=payload.channel, external_thread_id=payload.external_thread_id,
        contact_name=payload.contact_name.strip(), contact_address=payload.contact_address,
        subject=payload.subject, queue=payload.queue, status=PlatformInboxStatus.open,
        last_message_preview=payload.initial_message[:300], last_message_at=now, unread_count=1,
        first_response_due_at=first_response_due_at, resolution_due_at=resolution_due_at,
    )
    db.add(conversation)
    await db.flush()
    db.add(PlatformInboxMessage(
        conversation_id=conversation.id, direction=PlatformInboxDirection.inbound,
        body=payload.initial_message.strip(), delivery_status=PlatformInboxDeliveryStatus.received,
        actor_label=payload.contact_name.strip(), idempotency_key=idempotency_key, occurred_at=now,
    ))
    db.add(AuthSecurityEvent(event_type="platform_inbox_conversation_created", outcome="success", actor_user_id=actor.id, details={"conversation_id": str(conversation.id), "channel": payload.channel.value}))
    await db.flush()
    after = await snapshot_platform_inbox_conversation(db, conversation.id)
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=None, after_snapshot=after,
        module="support" if conversation.queue == PlatformInboxQueue.support else "sales",
        action="create_inbox_conversation",
        reason="Conversa recebida registrada na Inbox do SaaS.",
        reason_source="system",
    )
    return _conversation_data(await db.scalar(select(PlatformInboxConversation).options(selectinload(PlatformInboxConversation.messages)).where(PlatformInboxConversation.id == conversation.id)), include_messages=True)


@router.get("/conversations/{conversation_id}", summary="Consultar conversa e mensagens")
async def get_conversation(conversation_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(_inbox_access)) -> dict:
    item = await db.scalar(select(PlatformInboxConversation).options(selectinload(PlatformInboxConversation.messages)).where(PlatformInboxConversation.id == conversation_id))
    if item is None: raise HTTPException(status_code=404, detail={"code": "PLATFORM-INBOX-NOT-FOUND"})
    return _conversation_data(item, include_messages=True)


@router.post("/conversations/{conversation_id}/read", summary="Marcar conversa como lida")
async def mark_conversation_read(
    conversation_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_inbox_write),
    context: AuthContext = Depends(get_current_auth_context),
) -> dict:
    before = await snapshot_platform_inbox_conversation(
        db, conversation_id, for_update=True
    )
    if before is None:
        raise HTTPException(status_code=404, detail={"code": "PLATFORM-INBOX-NOT-FOUND"})
    item = await db.get(PlatformInboxConversation, conversation_id)
    item.unread_count = 0
    await db.flush()
    after = await snapshot_platform_inbox_conversation(db, conversation_id)
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=before, after_snapshot=after,
        module="support" if item.queue == PlatformInboxQueue.support else "sales",
        action="mark_inbox_conversation_read",
        reason="Conversa aberta pelo operador da Inbox.",
        reason_source="system",
    )
    return _conversation_data(item)


@router.patch("/conversations/{conversation_id}", summary="Atribuir, classificar e vincular conversa")
async def patch_conversation(conversation_id: UUID, payload: ConversationPatch, request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(_inbox_write), context: AuthContext = Depends(get_current_auth_context)) -> dict:
    before = await snapshot_platform_inbox_conversation(db, conversation_id, for_update=True)
    if before is None: raise HTTPException(status_code=404, detail={"code": "PLATFORM-INBOX-NOT-FOUND"})
    item = await db.get(PlatformInboxConversation, conversation_id)
    checks = ((payload.assigned_to_user_id, User), (payload.prospect_id, GrowthProspect), (payload.support_ticket_id, SupportTicket), (payload.workspace_id, Workspace))
    for identifier, model in checks:
        if identifier is not None and await db.get(model, identifier) is None:
            raise HTTPException(status_code=422, detail={"code": "PLATFORM-INBOX-LINK-INVALID"})
    target_workspace_id = (
        payload.workspace_id if "workspace_id" in payload.model_fields_set else item.workspace_id
    )
    target_prospect_id = (
        payload.prospect_id if "prospect_id" in payload.model_fields_set else item.prospect_id
    )
    target_ticket_id = (
        payload.support_ticket_id
        if "support_ticket_id" in payload.model_fields_set
        else item.support_ticket_id
    )
    if target_ticket_id is not None:
        ticket = await db.get(SupportTicket, target_ticket_id)
        if ticket is None or ticket.workspace_id != target_workspace_id:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "PLATFORM-INBOX-LINK-WORKSPACE-MISMATCH",
                    "message": "O ticket selecionado não pertence à conta vinculada.",
                },
            )
    if target_prospect_id is not None:
        prospect = await db.get(GrowthProspect, target_prospect_id)
        if (
            prospect is None
            or (prospect.workspace_id is not None and prospect.workspace_id != target_workspace_id)
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "PLATFORM-INBOX-LINK-WORKSPACE-MISMATCH",
                    "message": "O lead selecionado não é compatível com a conta vinculada.",
                },
            )
    now = datetime.now(timezone.utc)
    if "queue" in payload.model_fields_set and payload.queue is not None and item.first_responded_at is None:
        item.first_response_due_at, item.resolution_due_at = _sla_deadlines(payload.queue, now)
    if "status" in payload.model_fields_set and payload.status is not None:
        item.resolved_at = now if payload.status == PlatformInboxStatus.resolved else None
    for field in payload.model_fields_set:
        setattr(item, field, getattr(payload, field))
    db.add(AuthSecurityEvent(event_type="platform_inbox_conversation_updated", outcome="success", actor_user_id=actor.id, details={"conversation_id": str(item.id), "fields": sorted(payload.model_fields_set)}))
    await db.flush()
    after = await snapshot_platform_inbox_conversation(db, conversation_id)
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=before, after_snapshot=after,
        module="support" if item.queue == PlatformInboxQueue.support else "sales",
        action="update_inbox_conversation",
        reason=f"Campos operacionais atualizados: {', '.join(sorted(payload.model_fields_set))}.",
        reason_source="system",
    )
    return _conversation_data(item)


@router.post("/conversations/{conversation_id}/messages", status_code=status.HTTP_201_CREATED, summary="Responder ou registrar nota interna")
async def add_message(conversation_id: UUID, payload: MessageCreate, request: Request, response: Response, idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=120), db: AsyncSession = Depends(get_db), actor: User = Depends(_inbox_write), context: AuthContext = Depends(get_current_auth_context)) -> dict:
    before = await snapshot_platform_inbox_conversation(db, conversation_id, for_update=True)
    if before is None: raise HTTPException(status_code=404, detail={"code": "PLATFORM-INBOX-NOT-FOUND"})
    item = await db.get(PlatformInboxConversation, conversation_id)
    existing = await db.scalar(select(PlatformInboxMessage).where(PlatformInboxMessage.conversation_id == conversation_id, PlatformInboxMessage.idempotency_key == idempotency_key))
    if existing:
        response.status_code = status.HTTP_200_OK
        message_snapshot = await snapshot_platform_inbox_message(db, existing.id)
        await record_canonical_platform_mutation(
            request, context, db,
            before_snapshot=message_snapshot, after_snapshot=message_snapshot,
            module="support" if item.queue == PlatformInboxQueue.support else "sales",
            action="create_inbox_message",
            reason="Repetição idempotente de mensagem da Inbox.",
            reason_source="system",
        )
        return _message_data(existing)
    internal = payload.visibility == "internal"
    delivery_status = PlatformInboxDeliveryStatus.sent if internal else PlatformInboxDeliveryStatus.not_configured
    external_message_id = None
    if (
        not internal
        and item.channel == PlatformInboxChannel.whatsapp
        and item.contact_address
        and external_delivery_allowed()
    ):
        role = (
            PlatformOfficialWhatsAppRole.support
            if item.queue == PlatformInboxQueue.support
            else PlatformOfficialWhatsAppRole.outbound
        )
        delivered, external_message_id = await PlatformOfficialWhatsAppService(db).send_text_result(
            role=role,
            to=item.contact_address,
            body=payload.body.strip(),
            idempotency_key=idempotency_key,
            requested_by_user_id=actor.id,
            source_type="platform_inbox",
            source_id=str(item.id),
        )
        delivery_status = (
            PlatformInboxDeliveryStatus.sent
            if delivered
            else PlatformInboxDeliveryStatus.failed
        )
    elif (
        not internal
        and item.channel in {PlatformInboxChannel.instagram, PlatformInboxChannel.messenger}
        and item.contact_address
        and external_delivery_allowed()
    ):
        delivered, external_message_id = await PlatformInboxMetaService(db).send_text(
            channel=item.channel,
            recipient_id=item.contact_address,
            body=payload.body.strip(),
        )
        delivery_status = PlatformInboxDeliveryStatus.sent if delivered else PlatformInboxDeliveryStatus.failed
    elif (
        not internal
        and item.channel in {PlatformInboxChannel.email_sales, PlatformInboxChannel.email_support}
        and item.contact_address
        and external_delivery_allowed()
    ):
        delivered, external_message_id = await PlatformInboxEmailService(db).send_text(
            channel=item.channel,
            recipient=item.contact_address,
            subject=item.subject or "Atendimento Meu Executivo",
            body=payload.body.strip(),
            idempotency_key=idempotency_key,
        )
        delivery_status = PlatformInboxDeliveryStatus.sent if delivered else PlatformInboxDeliveryStatus.failed
    message = PlatformInboxMessage(
        conversation_id=item.id,
        direction=PlatformInboxDirection.internal if internal else PlatformInboxDirection.outbound,
        body=payload.body.strip(),
        delivery_status=delivery_status,
        actor_user_id=actor.id, actor_label=actor.name, idempotency_key=idempotency_key,
        external_message_id=external_message_id,
    )
    db.add(message)
    if not internal and item.first_responded_at is None:
        item.first_responded_at = datetime.now(timezone.utc)
    item.last_message_preview = payload.body[:300]
    item.last_message_at = datetime.now(timezone.utc)
    db.add(AuthSecurityEvent(event_type="platform_inbox_message_created", outcome="success", actor_user_id=actor.id, details={"conversation_id": str(item.id), "visibility": payload.visibility, "delivery_status": message.delivery_status.value}))
    await db.flush()
    after = await snapshot_platform_inbox_message(db, message.id)
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=None, after_snapshot=after,
        module="support" if item.queue == PlatformInboxQueue.support else "sales",
        action="create_inbox_message",
        reason=(
            "Nota interna registrada na Inbox."
            if internal
            else f"Resposta registrada com entrega {delivery_status.value}."
        ),
        reason_source="system",
    )
    return _message_data(message)


@router.post("/conversations/{conversation_id}/create-lead", status_code=status.HTTP_201_CREATED, summary="Criar e vincular lead a partir da conversa")
async def create_lead_from_conversation(
    conversation_id: UUID,
    payload: CreateLeadFromConversation,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_inbox_sales_write),
    context: AuthContext = Depends(get_current_auth_context),
) -> dict:
    before = await snapshot_platform_inbox_conversation(
        db, conversation_id, for_update=True
    )
    if before is None:
        raise HTTPException(status_code=404, detail={"code": "PLATFORM-INBOX-NOT-FOUND"})
    item = await db.get(PlatformInboxConversation, conversation_id)
    if item.prospect_id is not None:
        await record_canonical_platform_mutation(
            request, context, db,
            before_snapshot=before, after_snapshot=before,
            module="sales", action="create_lead_from_inbox",
            reason="Lead já vinculado; repetição tratada como no-op.",
            reason_source="system",
        )
        return _conversation_data(item)
    address = (item.contact_address or "").strip()
    prospect = await PlatformGrowthService(db).create_prospect(
        nome_escritorio=(payload.office_name or item.contact_name).strip(),
        contato_nome=item.contact_name,
        contato_email=address if "@" in address else None,
        contato_telefone=address if address and "@" not in address else None,
        origem="inbound_site" if item.channel == PlatformInboxChannel.webchat else "outro",
        owner_user_id=item.assigned_to_user_id,
        notas=payload.notes or f"Lead criado pela Inbox do SaaS. Canal: {item.channel.value}.",
        commercial_metadata={"platform_inbox_conversation_id": str(item.id), "source_channel": item.channel.value},
        actor=actor,
    )
    item.prospect_id = prospect.id
    db.add(AuthSecurityEvent(event_type="platform_inbox_lead_created", outcome="success", actor_user_id=actor.id, details={"conversation_id": str(item.id), "prospect_id": str(prospect.id)}))
    await db.flush()
    after = await snapshot_platform_inbox_conversation(db, conversation_id)
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=before, after_snapshot=after,
        module="sales", action="create_lead_from_inbox",
        reason="Lead criado e vinculado a partir da Inbox do SaaS.",
        reason_source="system",
    )
    return _conversation_data(item)


@router.post("/conversations/{conversation_id}/create-ticket", status_code=status.HTTP_201_CREATED, summary="Criar e vincular ticket a partir da conversa")
async def create_ticket_from_conversation(
    conversation_id: UUID,
    payload: CreateTicketFromConversation,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=120),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_inbox_support_write),
    context: AuthContext = Depends(get_current_auth_context),
) -> dict:
    before = await snapshot_platform_inbox_conversation(
        db, conversation_id, for_update=True
    )
    if before is None:
        raise HTTPException(status_code=404, detail={"code": "PLATFORM-INBOX-NOT-FOUND"})
    item = await db.get(PlatformInboxConversation, conversation_id)
    if item.support_ticket_id is not None:
        await record_canonical_platform_mutation(
            request, context, db,
            before_snapshot=before, after_snapshot=before,
            module="support", action="create_ticket_from_inbox",
            reason="Ticket já vinculado; repetição tratada como no-op.",
            reason_source="system",
        )
        return _conversation_data(item)
    if item.workspace_id is None:
        raise HTTPException(status_code=422, detail={"code": "PLATFORM-INBOX-WORKSPACE-REQUIRED", "message": "Vincule a conta antes de abrir o ticket."})
    membership = await db.scalar(
        select(WorkspaceMembership)
        .where(
            WorkspaceMembership.workspace_id == item.workspace_id,
            WorkspaceMembership.status == MembershipStatus.active,
        )
        .order_by(
            (WorkspaceMembership.role == WorkspaceRole.workspace_owner).desc(),
            WorkspaceMembership.created_at.asc(),
        )
    )
    if membership is None:
        raise HTTPException(status_code=422, detail={"code": "PLATFORM-INBOX-REPORTER-UNAVAILABLE", "message": "A conta não possui usuário ativo para representar o chamado."})
    description = payload.description or f"Conversa recebida por {item.channel.value}:\n\n{item.last_message_preview or item.subject or 'Sem prévia.'}"
    ticket, _created = await SupportTicketService(db).create_ticket(
        workspace_id=item.workspace_id,
        user_id=membership.user_id,
        payload=SupportTicketCreate(
            ticket_type=SupportTicketType.problem,
            subject=payload.subject or item.subject or f"Atendimento de {item.contact_name}",
            description=description,
            priority=SupportTicketPriority.normal,
            severity=payload.severity,
            technical_context={"page": "/blackbox/inbox", "action": "create_ticket_from_conversation"},
        ),
        idempotency_key=idempotency_key,
        queue_override=SupportTicketQueue.support,
        event_actor_user_id=actor.id,
    )
    item.support_ticket_id = ticket.id
    db.add(AuthSecurityEvent(event_type="platform_inbox_ticket_created", outcome="success", actor_user_id=actor.id, details={"conversation_id": str(item.id), "ticket_id": str(ticket.id)}))
    await db.flush()
    after = await snapshot_platform_inbox_conversation(db, conversation_id)
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=before, after_snapshot=after,
        module="support", action="create_ticket_from_inbox",
        reason="Ticket criado e vinculado a partir da Inbox do SaaS.",
        reason_source="system",
    )
    return _conversation_data(item)


@router.get("/metrics", summary="Indicadores da inbox própria")
async def inbox_metrics(db: AsyncSession = Depends(get_db), _: User = Depends(_inbox_access)) -> dict:
    now = datetime.now(timezone.utc)
    total = await db.scalar(select(func.count(PlatformInboxConversation.id))) or 0
    open_count = await db.scalar(select(func.count(PlatformInboxConversation.id)).where(PlatformInboxConversation.status != PlatformInboxStatus.resolved)) or 0
    unread = await db.scalar(select(func.coalesce(func.sum(PlatformInboxConversation.unread_count), 0))) or 0
    first_response_overdue = await db.scalar(select(func.count(PlatformInboxConversation.id)).where(
        PlatformInboxConversation.status != PlatformInboxStatus.resolved,
        PlatformInboxConversation.first_responded_at.is_(None),
        PlatformInboxConversation.first_response_due_at < now,
    )) or 0
    resolution_overdue = await db.scalar(select(func.count(PlatformInboxConversation.id)).where(
        PlatformInboxConversation.status != PlatformInboxStatus.resolved,
        PlatformInboxConversation.resolution_due_at < now,
    )) or 0
    return {
        "total": total, "open": open_count, "unread": unread,
        "firstResponseOverdue": first_response_overdue,
        "resolutionOverdue": resolution_overdue,
        "updatedAt": now.isoformat(),
    }


@router.get("/channels", summary="Estado operacional dos canais próprios da Inbox")
async def inbox_channels(db: AsyncSession = Depends(get_db), _: User = Depends(_inbox_access)) -> list[dict]:
    whatsapp_connections = list(await db.scalars(select(PlatformOfficialWhatsAppConnection)))
    connected_roles = sorted(
        connection.role.value
        for connection in whatsapp_connections
        if connection.is_active and connection.status == WaConnectionStatus.connected
    )
    whatsapp_ready = bool(connected_roles)
    return [
        {
            "channel": PlatformInboxChannel.whatsapp.value,
            "state": "connected" if whatsapp_ready else "not_configured",
            "inbound": whatsapp_ready,
            "outbound": whatsapp_ready and external_delivery_allowed(),
            "detail": f"Papéis conectados: {', '.join(connected_roles)}" if connected_roles else "Nenhum número oficial conectado.",
        },
        {
            "channel": PlatformInboxChannel.webchat.value,
            "state": "available",
            "inbound": True,
            "outbound": True,
            "detail": "Sessões públicas com token e respostas pela mesma conversa.",
        },
        *[
            {
                "channel": channel.value,
                "state": "credentials_configured" if PlatformInboxMetaService.configured(channel) else "not_configured",
                "inbound": PlatformInboxMetaService.configured(channel),
                "outbound": PlatformInboxMetaService.configured(channel) and external_delivery_allowed(),
                "detail": "Adaptador implementado e credenciais presentes; assinatura e entrega externas exigem homologação." if PlatformInboxMetaService.configured(channel) else "Adaptador implementado, mas credenciais oficiais ausentes.",
            }
            for channel in (
                PlatformInboxChannel.instagram,
                PlatformInboxChannel.messenger,
            )
        ],
        *[
            {"channel": channel.value, "state": "credentials_configured" if PlatformInboxEmailService.configured(channel) else "not_configured", "inbound": PlatformInboxEmailService.configured(channel), "outbound": PlatformInboxEmailService.configured(channel) and external_delivery_allowed(), "detail": "Adaptador Resend implementado e credenciais presentes; webhook e entrega externos exigem homologação." if PlatformInboxEmailService.configured(channel) else "Adaptador Resend implementado, mas credencial, signing secret ou endereço da caixa está ausente."}
            for channel in (PlatformInboxChannel.email_sales, PlatformInboxChannel.email_support)
        ],
    ]


@router.get(
    "/assignees",
    summary="Listar responsáveis aptos às filas da inbox",
    deprecated=True,
)
async def inbox_assignees(db: AsyncSession = Depends(get_db), _: User = Depends(_inbox_access)) -> list[dict]:
    users = list(await db.scalars(select(User).where(
        User.is_internal_staff.is_(True),
        User.platform_access_active.is_(True),
        User.is_active.is_(True),
        User.deleted_at.is_(None),
    ).order_by(User.name.asc())))
    return [
        {"id": str(user.id), "name": user.name, "rolePackage": user.platform_role_package}
        for user in users
        if has_platform_permission(user, "support", "respond")
        or has_platform_permission(user, "sales", "manage")
    ]
