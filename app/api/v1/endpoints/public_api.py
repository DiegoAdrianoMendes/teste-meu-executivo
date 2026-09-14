from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.entitlement_errors import entitlement_http_exception
from app.core.deps import get_db
from app.core.deps_public_api import PublicApiContext, require_public_api_scope
from app.schemas.matter import MatterMoveRequest, MatterRead
from app.schemas.public_api import (
    PublicApiCampaignCreateRequest,
    PublicApiCampaignCreateResponse,
    PublicApiConversationNoteCreate,
    PublicApiConversationRead,
    PublicApiLeadCreate,
    PublicApiLeadCreateResponse,
    PublicApiNoteCreateResponse,
    PublicApiTemplateSendRequest,
    PublicApiTemplateSendResponse,
)
from app.schemas.whatsapp import WhatsAppContactRead
from app.services.entitlement_service import EntitlementError
from app.services.public_api_service import PublicApiMutationExecution, PublicApiService

router = APIRouter(prefix="/public-api", tags=["Public API"])


def _get_service(db: AsyncSession = Depends(get_db)) -> PublicApiService:
    return PublicApiService(db)


def _mutation_response(execution: PublicApiMutationExecution) -> JSONResponse:
    return JSONResponse(
        status_code=execution.status_code,
        content=execution.response_body,
    )


@router.get(
    "/contacts",
    response_model=list[WhatsAppContactRead],
    summary="Listar contatos do workspace via token externo",
)
async def list_contacts(
    search: str | None = None,
    limit: int = 50,
    svc: PublicApiService = Depends(_get_service),
    context: PublicApiContext = Depends(require_public_api_scope("contacts:read")),
):
    items = await svc.list_contacts(context.workspace_id, search=search, limit=limit)
    await svc.audit_request(
        token=context.token,
        scope=context.scope,
        path=context.request_path,
        method=context.request_method,
        status_code=200,
        ip_address=context.ip_address,
        user_agent=context.user_agent,
        metadata={"returned": len(items), "search": search},
    )
    return items


@router.get(
    "/conversations/{contact_id}",
    response_model=PublicApiConversationRead,
    summary="Ler conversa do WhatsApp via token externo",
)
async def get_conversation(
    contact_id: UUID,
    svc: PublicApiService = Depends(_get_service),
    context: PublicApiContext = Depends(require_public_api_scope("conversations:read")),
):
    conversation = await svc.get_conversation(context.workspace_id, contact_id)
    await svc.audit_request(
        token=context.token,
        scope=context.scope,
        path=context.request_path,
        method=context.request_method,
        status_code=200,
        ip_address=context.ip_address,
        user_agent=context.user_agent,
        metadata={"contactId": str(contact_id)},
    )
    return PublicApiConversationRead(conversation=conversation)


@router.post(
    "/leads",
    response_model=PublicApiLeadCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Criar lead/card via API externa",
)
async def create_lead(
    payload: PublicApiLeadCreate,
    svc: PublicApiService = Depends(_get_service),
    context: PublicApiContext = Depends(require_public_api_scope("leads:write")),
):
    async def operation():
        return await svc.create_lead(context.workspace_id, payload, commit=False)

    try:
        execution = await svc.execute_mutation(
            token=context.token,
            scope=context.scope,
            operation="leads.create",
            idempotency_key=context.idempotency_key or "",
            payload=payload,
            resource=None,
            response_status=status.HTTP_201_CREATED,
            path=context.request_path,
            method=context.request_method,
            ip_address=context.ip_address,
            user_agent=context.user_agent,
            handler=operation,
            audit_metadata=lambda body: {"matterId": (body.get("matter") or {}).get("id")},
        )
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    return _mutation_response(execution)


@router.post(
    "/matters/{matter_id}/move-stage",
    response_model=MatterRead,
    summary="Mover etapa do card via API externa",
)
async def move_stage(
    matter_id: UUID,
    payload: MatterMoveRequest,
    svc: PublicApiService = Depends(_get_service),
    context: PublicApiContext = Depends(require_public_api_scope("matters:write")),
):
    async def operation():
        return await svc.move_stage(
            context.workspace_id,
            matter_id,
            column_id=(
                payload.column_id.value
                if hasattr(payload.column_id, "value")
                else str(payload.column_id)
            ),
            micro_status=(
                payload.micro_status.value
                if payload.micro_status is not None
                and hasattr(payload.micro_status, "value")
                else payload.micro_status
            ),
            commit=False,
        )

    execution = await svc.execute_mutation(
        token=context.token,
        scope=context.scope,
        operation="matters.move_stage",
        idempotency_key=context.idempotency_key or "",
        payload=payload,
        resource={"matterId": str(matter_id)},
        response_status=status.HTTP_200_OK,
        path=context.request_path,
        method=context.request_method,
        ip_address=context.ip_address,
        user_agent=context.user_agent,
        handler=operation,
        audit_metadata=lambda body: {
            "matterId": str(matter_id),
            "columnId": body.get("columnId"),
        },
    )
    return _mutation_response(execution)


@router.post(
    "/conversations/{contact_id}/notes",
    response_model=PublicApiNoteCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar nota interna via API externa",
)
async def create_note(
    contact_id: UUID,
    payload: PublicApiConversationNoteCreate,
    svc: PublicApiService = Depends(_get_service),
    context: PublicApiContext = Depends(require_public_api_scope("notes:write")),
):
    async def operation():
        note = await svc.add_note(
            context.workspace_id,
            contact_id,
            author_user_id=context.token.created_by_id,
            payload=payload,
            commit=False,
        )
        return PublicApiNoteCreateResponse(note=note)

    execution = await svc.execute_mutation(
        token=context.token,
        scope=context.scope,
        operation="conversations.notes.create",
        idempotency_key=context.idempotency_key or "",
        payload=payload,
        resource={"contactId": str(contact_id)},
        response_status=status.HTTP_201_CREATED,
        path=context.request_path,
        method=context.request_method,
        ip_address=context.ip_address,
        user_agent=context.user_agent,
        handler=operation,
        audit_metadata=lambda body: {
            "contactId": str(contact_id),
            "noteId": (body.get("note") or {}).get("id"),
        },
    )
    return _mutation_response(execution)


@router.post(
    "/templates/send",
    response_model=PublicApiTemplateSendResponse,
    summary="Disparar template oficial da Meta via API externa",
)
async def send_template(
    payload: PublicApiTemplateSendRequest,
    svc: PublicApiService = Depends(_get_service),
    context: PublicApiContext = Depends(require_public_api_scope("templates:send")),
):
    execution = await svc.execute_send_template_mutation(
        token=context.token,
        scope=context.scope,
        idempotency_key=context.idempotency_key or "",
        payload=payload,
        path=context.request_path,
        method=context.request_method,
        ip_address=context.ip_address,
        user_agent=context.user_agent,
    )
    return _mutation_response(execution)


@router.post(
    "/campaigns",
    response_model=PublicApiCampaignCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Criar campanha oficial via API externa",
)
async def create_campaign(
    payload: PublicApiCampaignCreateRequest,
    svc: PublicApiService = Depends(_get_service),
    context: PublicApiContext = Depends(require_public_api_scope("campaigns:write")),
):
    execution = await svc.execute_create_campaign_mutation(
        token=context.token,
        scope=context.scope,
        idempotency_key=context.idempotency_key or "",
        payload=payload,
        path=context.request_path,
        method=context.request_method,
        ip_address=context.ip_address,
        user_agent=context.user_agent,
    )
    return _mutation_response(execution)
