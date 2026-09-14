from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    can_view_sensitive_lead_data,
    get_current_permissions,
    get_current_user_id,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.core.lead_privacy import mask_meta_comment, mask_meta_conversation, mask_meta_drawer, mask_meta_note
from app.models.meta_channels import MetaChannelType
from app.schemas.meta_channels import (
    FollowStatusRefreshRequest,
    MetaApprovedMessageCreate,
    MetaApprovedMessageRead,
    MetaAutomationRuleCreate,
    MetaAutomationRuleRead,
    MetaCampaignCreate,
    MetaCampaignRead,
    MetaCampaignUpdate,
    MetaChannelConnectionCreate,
    MetaChannelConnectionRead,
    MetaCopilotRead,
    MetaCopilotRequest,
    MetaCommentThreadRead,
    MetaConversationNoteCreate,
    MetaConversationNoteRead,
    MetaLeadDrawerRead,
    MetaMatterUpdateRequest,
    MetaMoveStageRequest,
    MetaSendMessageRequest,
    MetaSocialConversationCreate,
    MetaSocialConversationRead,
)
from app.schemas.meta_oauth import (
    MetaOAuthAuthorizeUrlRead,
    MetaOAuthCallbackRequest,
    MetaOAuthCallbackResult,
    MetaOAuthTokenStatusRead,
)
from app.services.meta_channels_service import MetaChannelsService
from app.services.meta_oauth_service import (
    MetaOAuthConnectionNotFoundError,
    MetaOAuthService,
)

router = APIRouter(prefix="/instagram", tags=["Instagram"])


def _get_oauth_service(db: AsyncSession = Depends(get_db)) -> MetaOAuthService:
    return MetaOAuthService(db)


def _has_permission(permissions, module: str, action: str) -> bool:
    return any(item.module == module and item.action == action for item in permissions)


def _get_service(db: AsyncSession = Depends(get_db)) -> MetaChannelsService:
    return MetaChannelsService(db)


@router.get("/connections", response_model=list[MetaChannelConnectionRead])
async def list_connections(
    svc: MetaChannelsService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
):
    return await svc.list_connections(workspace_id, MetaChannelType.instagram)


@router.post("/connections", response_model=MetaChannelConnectionRead, status_code=status.HTTP_201_CREATED)
async def create_connection(
    payload: MetaChannelConnectionCreate,
    svc: MetaChannelsService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("configuracoes", "edit")),
):
    return await svc.create_connection(workspace_id, MetaChannelType.instagram, payload)


@router.get("/conversations", response_model=list[MetaSocialConversationRead])
async def list_conversations(
    svc: MetaChannelsService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    conversations = await svc.list_conversations(workspace_id, MetaChannelType.instagram)
    return conversations if can_view_sensitive else [mask_meta_conversation(item) for item in conversations]


@router.post("/conversations", response_model=MetaSocialConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: MetaSocialConversationCreate,
    svc: MetaChannelsService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    current_user_id: UUID = Depends(get_current_user_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    conversation = await svc.create_conversation(workspace_id, MetaChannelType.instagram, current_user_id, payload)
    return conversation if can_view_sensitive else mask_meta_conversation(conversation)


@router.get("/conversations/{conversation_id}", response_model=MetaSocialConversationRead)
async def get_conversation(
    conversation_id: UUID,
    svc: MetaChannelsService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    conversation = await svc.get_conversation(workspace_id, MetaChannelType.instagram, conversation_id)
    return conversation if can_view_sensitive else mask_meta_conversation(conversation)


@router.post("/conversations/{conversation_id}/copilot", response_model=MetaCopilotRead)
async def generate_copilot(
    conversation_id: UUID,
    payload: MetaCopilotRequest,
    svc: MetaChannelsService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
):
    return await svc.generate_copilot(workspace_id, MetaChannelType.instagram, conversation_id, payload)


@router.post("/conversations/{conversation_id}/messages", response_model=MetaSocialConversationRead)
async def send_message(
    conversation_id: UUID,
    payload: MetaSendMessageRequest,
    svc: MetaChannelsService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    current_user_id: UUID = Depends(get_current_user_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    conversation = await svc.send_message(
        workspace_id,
        MetaChannelType.instagram,
        conversation_id,
        current_user_id,
        payload.body,
        is_public_reply=payload.is_public_reply,
    )
    return conversation if can_view_sensitive else mask_meta_conversation(conversation)


@router.get("/comments", response_model=list[MetaCommentThreadRead])
async def list_comments(
    svc: MetaChannelsService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    comments = await svc.list_comments(workspace_id, MetaChannelType.instagram)
    return comments if can_view_sensitive else [mask_meta_comment(item) for item in comments]


@router.post("/conversations/{conversation_id}/follow-status", response_model=MetaSocialConversationRead)
async def refresh_follow_status(
    conversation_id: UUID,
    payload: FollowStatusRefreshRequest,
    svc: MetaChannelsService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    conversation = await svc.refresh_follow_status(workspace_id, MetaChannelType.instagram, conversation_id, payload)
    return conversation if can_view_sensitive else mask_meta_conversation(conversation)


@router.get("/approved-messages", response_model=list[MetaApprovedMessageRead])
async def list_approved_messages(
    svc: MetaChannelsService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("configuracoes", "view")),
):
    return await svc.list_approved_messages(workspace_id, MetaChannelType.instagram)


@router.post("/approved-messages", response_model=MetaApprovedMessageRead, status_code=status.HTTP_201_CREATED)
async def create_approved_message(
    payload: MetaApprovedMessageCreate,
    svc: MetaChannelsService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    current_user_id: UUID = Depends(get_current_user_id),
    _: object = Depends(require_permission("configuracoes", "edit")),
):
    return await svc.create_approved_message(workspace_id, MetaChannelType.instagram, current_user_id, payload)


@router.get("/automation-rules", response_model=list[MetaAutomationRuleRead])
async def list_automation_rules(
    svc: MetaChannelsService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("configuracoes", "view")),
):
    return await svc.list_automation_rules(workspace_id, MetaChannelType.instagram)


@router.post("/automation-rules", response_model=MetaAutomationRuleRead, status_code=status.HTTP_201_CREATED)
async def create_automation_rule(
    payload: MetaAutomationRuleCreate,
    svc: MetaChannelsService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("configuracoes", "edit")),
):
    return await svc.create_automation_rule(workspace_id, MetaChannelType.instagram, payload)


@router.get("/campaigns", response_model=list[MetaCampaignRead])
async def list_campaigns(
    svc: MetaChannelsService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
):
    return await svc.list_campaigns(workspace_id, MetaChannelType.instagram)


@router.post("/campaigns", response_model=MetaCampaignRead, status_code=status.HTTP_201_CREATED)
async def create_campaign(
    payload: MetaCampaignCreate,
    svc: MetaChannelsService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    current_user_id: UUID = Depends(get_current_user_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    return await svc.create_campaign(workspace_id, MetaChannelType.instagram, current_user_id, payload)


@router.patch("/campaigns/{campaign_id}", response_model=MetaCampaignRead)
async def update_campaign(
    campaign_id: UUID,
    payload: MetaCampaignUpdate,
    svc: MetaChannelsService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    return await svc.update_campaign(workspace_id, MetaChannelType.instagram, campaign_id, payload)


@router.get("/conversations/{conversation_id}/notes", response_model=list[MetaConversationNoteRead])
async def list_notes(
    conversation_id: UUID,
    svc: MetaChannelsService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    notes = await svc.list_notes(workspace_id, MetaChannelType.instagram, conversation_id)
    return notes if can_view_sensitive else [mask_meta_note(item) for item in notes]


@router.post("/conversations/{conversation_id}/notes", response_model=MetaConversationNoteRead, status_code=status.HTTP_201_CREATED)
async def create_note(
    conversation_id: UUID,
    payload: MetaConversationNoteCreate,
    svc: MetaChannelsService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    current_user_id: UUID = Depends(get_current_user_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    note = await svc.create_note(workspace_id, MetaChannelType.instagram, conversation_id, current_user_id, payload)
    return note if can_view_sensitive else mask_meta_note(note)


@router.get("/conversations/{conversation_id}/drawer", response_model=MetaLeadDrawerRead)
async def get_drawer(
    conversation_id: UUID,
    svc: MetaChannelsService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    permissions = Depends(get_current_permissions),
    _: object = Depends(require_permission("mensagens", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    drawer = await svc.get_drawer(
        workspace_id,
        MetaChannelType.instagram,
        conversation_id,
        can_edit_matter=_has_permission(permissions, "kanban", "edit"),
        can_manage_conversation=_has_permission(permissions, "mensagens", "send_message"),
    )
    return drawer if can_view_sensitive else mask_meta_drawer(drawer)


@router.post("/conversations/{conversation_id}/move-stage", response_model=MetaLeadDrawerRead)
async def move_stage(
    conversation_id: UUID,
    payload: MetaMoveStageRequest,
    svc: MetaChannelsService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    current_user_id: UUID = Depends(get_current_user_id),
    permissions = Depends(get_current_permissions),
    _: object = Depends(require_permission("kanban", "edit")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    drawer = await svc.move_stage(
        workspace_id,
        MetaChannelType.instagram,
        conversation_id,
        current_user_id,
        payload,
        can_edit_matter=True,
        can_manage_conversation=_has_permission(permissions, "mensagens", "send_message"),
    )
    return drawer if can_view_sensitive else mask_meta_drawer(drawer)


@router.put("/conversations/{conversation_id}/matter", response_model=MetaLeadDrawerRead)
async def update_matter(
    conversation_id: UUID,
    payload: MetaMatterUpdateRequest,
    svc: MetaChannelsService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    current_user_id: UUID = Depends(get_current_user_id),
    permissions = Depends(get_current_permissions),
    _: object = Depends(require_permission("kanban", "edit")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    drawer = await svc.update_matter(
        workspace_id,
        MetaChannelType.instagram,
        conversation_id,
        current_user_id,
        payload,
        can_edit_matter=True,
        can_manage_conversation=_has_permission(permissions, "mensagens", "send_message"),
    )
    return drawer if can_view_sensitive else mask_meta_drawer(drawer)


def _connection_not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "META-OAUTH-404", "message": "Conexão Instagram não encontrada neste workspace."},
    )


@router.get("/oauth/authorize-url", response_model=MetaOAuthAuthorizeUrlRead)
async def get_authorize_url(
    connection_id: UUID = Query(...),
    redirect_uri: str = Query(...),
    svc: MetaOAuthService = Depends(_get_oauth_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("integrations", "manage")),
):
    try:
        return await svc.build_authorization_url(
            workspace_id=workspace_id, connection_id=connection_id, redirect_uri=redirect_uri
        )
    except MetaOAuthConnectionNotFoundError as error:
        raise _connection_not_found() from error


@router.get("/oauth/callback", response_model=MetaOAuthCallbackResult)
async def get_oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    svc: MetaOAuthService = Depends(_get_oauth_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("integrations", "manage")),
):
    try:
        return await svc.handle_callback(workspace_id=workspace_id, code=code, state=state)
    except MetaOAuthConnectionNotFoundError as error:
        raise _connection_not_found() from error


@router.post("/oauth/callback", response_model=MetaOAuthCallbackResult)
async def post_oauth_callback(
    payload: MetaOAuthCallbackRequest,
    svc: MetaOAuthService = Depends(_get_oauth_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("integrations", "manage")),
):
    try:
        return await svc.handle_callback(workspace_id=workspace_id, code=payload.code, state=payload.state)
    except MetaOAuthConnectionNotFoundError as error:
        raise _connection_not_found() from error


@router.get("/oauth/token-status/{connection_id}", response_model=MetaOAuthTokenStatusRead)
async def get_token_status(
    connection_id: UUID,
    svc: MetaOAuthService = Depends(_get_oauth_service),
    channels_svc: MetaChannelsService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("integrations", "view")),
):
    connection = await channels_svc._get_connection(connection_id, workspace_id, MetaChannelType.instagram)
    return svc.connection_token_status(connection)
