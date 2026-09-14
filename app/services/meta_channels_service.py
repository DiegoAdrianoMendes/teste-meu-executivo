from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.klm_runtime import external_delivery_allowed
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.models.meta_channels import (
    FollowRelationshipStatus,
    ApprovedMessageStatus,
    MetaApprovedMessage,
    MetaAutomationRule,
    MetaCampaignStatus,
    MetaChannelCampaign,
    MetaChannelConnection,
    MetaChannelType,
    MetaConnectionStatus,
    MetaConversationNote,
    MetaSocialConversation,
    MetaSocialMessage,
    SocialEventType,
)
from app.models.whatsapp import (
    HumanControlState,
    WaMessageDirection,
    WaMessageStatus,
    WaQueueStatus,
)
from app.schemas.control_plane import MeetingRead
from app.schemas.matter import MatterRead, MatterMoveRequest, MatterUpdate
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
    MetaSocialConversationCreate,
    MetaSocialConversationRead,
    MetaSocialMessageRead,
)
from app.services.meta_ai_service import MetaAIService
from app.services.matter_service import MatterService
from app.services.meeting_service import MeetingService
from app.services.meta_policy_gate import MetaPolicyGate


COMMENT_EVENT_TYPES = {
    SocialEventType.post_comment,
    SocialEventType.keyword_comment,
    SocialEventType.page_comment,
}

# Diretriz 8 do PLANO_META_MAXIMO (2026-07-23) + AUDITORIA achado Messenger/IG
# "capability matrix declara supported sem transporte": nenhuma capability nasce
# `True` por padrão. Uma conexão recém-criada (status=pending, sem webhook
# verificado) só tem capacidades locais reais (inbox manual, notas, drawer);
# tudo que depende de transporte oficial da Meta (webhook + token verificado)
# nasce `False` com razão legível. `_capabilities_for_connection` eleva o que
# a conexão realmente prova (status=connected + webhook_registered) sem nunca
# remover chaves — apenas muda valor/estado, conforme a diretriz de não apagar.
_UNVERIFIED_REASON = "Conexão ainda não verificada (sem webhook confirmado pela Meta); capacidade indisponível."
_LOCAL_ONLY_REASON = "Suportado localmente (inbox manual/CRM); não depende de transporte externo da Meta."


def _capability(supported: bool, reason: str) -> dict:
    return {"supported": supported, "reason": reason}


def _default_capabilities(channel_type: MetaChannelType) -> dict:
    """Matriz honesta para uma conexão nova (status=pending, sem webhook).

    Só o que roda localmente hoje (sem depender de OAuth de Página/Instagram
    Login ainda pendente na F3-A/F4) nasce `supported=True`; o resto nasce
    `unverified` (supported=False) até a conexão provar webhook + token.
    """
    if channel_type == MetaChannelType.instagram:
        return {
            "directMessages": _capability(False, _UNVERIFIED_REASON),
            "comments": _capability(False, _UNVERIFIED_REASON),
            "storyReplies": _capability(False, _UNVERIFIED_REASON),
            "mentions": _capability(False, _UNVERIFIED_REASON),
            "followSignal": _capability(False, _UNVERIFIED_REASON),
            "entryPoints": _capability(False, _UNVERIFIED_REASON),
            "linksInText": _capability(True, _LOCAL_ONLY_REASON),
            "icebreakers": _capability(False, _UNVERIFIED_REASON),
        }
    return {
        "pageMessaging": _capability(False, _UNVERIFIED_REASON),
        "pageComments": _capability(False, _UNVERIFIED_REASON),
        "menus": _capability(False, _UNVERIFIED_REASON),
        "icebreakers": _capability(False, _UNVERIFIED_REASON),
        "postbacks": _capability(False, _UNVERIFIED_REASON),
        "entryPoints": _capability(False, _UNVERIFIED_REASON),
        "linksInText": _capability(True, _LOCAL_ONLY_REASON),
    }


# Campanhas Messenger/Instagram não têm executor de broadcast orgânico
# (AUDITORIA achado Messenger: "compliance autodeclarado"; PLANO seção 5 item 3).
# Nenhum status que prometa disparo (scheduled/running) pode ser assumido
# enquanto o transporte não existir; draft/paused/completed/stopped/failed
# continuam livres pois não prometem envio automático.
_CAMPAIGN_STATUSES_REQUIRING_TRANSPORT = {
    MetaCampaignStatus.scheduled,
    MetaCampaignStatus.running,
}
_CAMPAIGN_TRANSPORT_BLOCKED_DETAIL = (
    "Canal sem transporte orgânico de broadcast; use reengajamento em janela ou mídia paga."
)


class MetaChannelsService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.matter_service = MatterService(db)
        self.meeting_service = MeetingService(db)
        self.ai_service = MetaAIService(db)

    async def list_connections(
        self, workspace_id: UUID, channel_type: MetaChannelType
    ) -> list[MetaChannelConnectionRead]:
        result = await self.db.execute(
            select(MetaChannelConnection)
            .where(
                MetaChannelConnection.workspace_id == workspace_id,
                MetaChannelConnection.channel_type == channel_type,
            )
            .order_by(MetaChannelConnection.updated_at.desc())
        )
        return [MetaChannelConnectionRead.model_validate(item) for item in result.scalars().all()]

    async def create_connection(
        self,
        workspace_id: UUID,
        channel_type: MetaChannelType,
        payload: MetaChannelConnectionCreate,
    ) -> MetaChannelConnectionRead:
        connection = MetaChannelConnection(
            workspace_id=workspace_id,
            channel_type=channel_type,
            page_id=payload.page_id,
            instagram_account_id=payload.instagram_account_id,
            external_account_id=payload.external_account_id
            or payload.page_id
            or payload.instagram_account_id,
            account_name=payload.account_name,
            username=payload.username,
            scopes=payload.scopes,
            capability_matrix=payload.capability_matrix or _default_capabilities(channel_type),
            status=MetaConnectionStatus.pending,
            webhook_registered=False,
            last_error=(
                payload.last_error
                or "Cadastro local salvo; autenticação e webhook externos ainda não foram verificados."
            ),
            connected_at=None,
            last_synced_at=None,
        )
        self.db.add(connection)
        await self.db.flush()
        return MetaChannelConnectionRead.model_validate(connection)

    async def list_conversations(
        self, workspace_id: UUID, channel_type: MetaChannelType
    ) -> list[MetaSocialConversationRead]:
        result = await self.db.execute(
            select(MetaSocialConversation)
            .options(
                selectinload(MetaSocialConversation.messages),
                selectinload(MetaSocialConversation.assigned_user),
            )
            .where(
                MetaSocialConversation.workspace_id == workspace_id,
                MetaSocialConversation.channel_type == channel_type,
            )
            .order_by(
                MetaSocialConversation.last_message_at.desc().nullslast(),
                MetaSocialConversation.updated_at.desc(),
            )
        )
        return [
            await self._build_conversation_read(item, include_messages=False)
            for item in result.scalars().unique().all()
        ]

    async def create_conversation(
        self,
        workspace_id: UUID,
        channel_type: MetaChannelType,
        acting_user_id: UUID,
        payload: MetaSocialConversationCreate,
    ) -> MetaSocialConversationRead:
        connection = await self._get_connection(payload.connection_id, workspace_id, channel_type)
        conversation = MetaSocialConversation(
            workspace_id=workspace_id,
            channel_type=channel_type,
            connection_id=connection.id,
            matter_id=payload.matter_id,
            external_thread_id=payload.external_thread_id,
            external_participant_id=payload.external_participant_id,
            participant_name=payload.participant_name,
            participant_handle=payload.participant_handle,
            participant_avatar_url=payload.participant_avatar_url,
            event_type=payload.event_type,
            queue_status=payload.queue_status,
            human_control_state=payload.human_control_state,
            follow_status=payload.follow_status,
            source_title=payload.source_title,
            source_url=payload.source_url,
            source_metadata=payload.source_metadata,
            active_flow_label=payload.active_flow_label,
            next_follow_up_at=payload.next_follow_up_at,
            policy_state=payload.policy_state,
            is_public_thread=payload.is_public_thread,
            last_message_preview=payload.initial_message,
            last_message_at=datetime.now(timezone.utc) if payload.initial_message else None,
            unread_count=1 if payload.initial_message else 0,
        )
        self.db.add(conversation)
        await self.db.flush()

        if payload.initial_message:
            initial_message = MetaSocialMessage(
                conversation_id=conversation.id,
                direction=WaMessageDirection.inbound,
                status=WaMessageStatus.delivered,
                body=payload.initial_message,
                author_name=payload.participant_name,
                is_public_reply=payload.is_public_thread,
                timestamp=datetime.now(timezone.utc),
            )
            self.db.add(initial_message)
            await self.db.flush()
            from app.services.omnichannel_legacy_bridge_service import (
                OmnichannelLegacyBridgeService,
            )

            await OmnichannelLegacyBridgeService(self.db).ingest_meta_social_message(
                workspace_id=workspace_id,
                connection=connection,
                conversation=conversation,
                message=initial_message,
            )
            await self._run_automation_for_conversation(
                workspace_id=workspace_id,
                channel_type=channel_type,
                conversation=conversation,
                inbound_text=payload.initial_message,
            )
            self.db.expire(conversation, ["messages"])

        return await self.get_conversation(workspace_id, channel_type, conversation.id)

    async def get_conversation(
        self,
        workspace_id: UUID,
        channel_type: MetaChannelType,
        conversation_id: UUID,
    ) -> MetaSocialConversationRead:
        conversation = await self._get_conversation(conversation_id, workspace_id, channel_type)
        return await self._build_conversation_read(conversation, include_messages=True)

    async def send_message(
        self,
        workspace_id: UUID,
        channel_type: MetaChannelType,
        conversation_id: UUID,
        acting_user_id: UUID,
        body: str,
        *,
        is_public_reply: bool = False,
    ) -> MetaSocialConversationRead:
        conversation = await self._get_conversation(conversation_id, workspace_id, channel_type)
        message = MetaSocialMessage(
            conversation_id=conversation.id,
            direction=WaMessageDirection.outbound,
            status=WaMessageStatus.pending,
            body=body.strip(),
            sent_by_id=acting_user_id,
            is_public_reply=is_public_reply,
            message_metadata={
                "deliveryStatus": "local_draft",
                "externalActionStatus": "blocked",
                "externalGate": "closed",
                "blockedReason": "meta_transport_not_connected",
                "externalActionsExecuted": 0,
            },
            timestamp=datetime.now(timezone.utc),
        )
        self.db.add(message)
        conversation.last_message_preview = message.body
        conversation.last_message_at = message.timestamp
        conversation.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        connection = await self._get_connection(
            conversation.connection_id, workspace_id, channel_type
        )
        from app.services.omnichannel_legacy_bridge_service import (
            OmnichannelLegacyBridgeService,
        )

        await OmnichannelLegacyBridgeService(self.db).ingest_meta_social_message(
            workspace_id=workspace_id,
            connection=connection,
            conversation=conversation,
            message=message,
        )
        self.db.expire(conversation, ["messages"])
        return await self.get_conversation(workspace_id, channel_type, conversation.id)

    async def list_comments(
        self, workspace_id: UUID, channel_type: MetaChannelType
    ) -> list[MetaCommentThreadRead]:
        result = await self.db.execute(
            select(MetaSocialConversation)
            .where(
                MetaSocialConversation.workspace_id == workspace_id,
                MetaSocialConversation.channel_type == channel_type,
                MetaSocialConversation.event_type.in_(tuple(COMMENT_EVENT_TYPES)),
            )
            .order_by(MetaSocialConversation.last_message_at.desc().nullslast())
        )
        threads = result.scalars().all()
        return [
            MetaCommentThreadRead(
                id=item.id,
                conversationId=item.id,
                participantName=item.participant_name,
                participantHandle=item.participant_handle,
                eventType=item.event_type,
                sourceTitle=item.source_title,
                sourceUrl=item.source_url,
                followStatus=item.follow_status,
                lastMessagePreview=item.last_message_preview,
                lastMessageAt=item.last_message_at,
                policyState=item.policy_state,
            )
            for item in threads
        ]

    async def generate_copilot(
        self,
        workspace_id: UUID,
        channel_type: MetaChannelType,
        conversation_id: UUID,
        payload: MetaCopilotRequest,
    ) -> MetaCopilotRead:
        conversation = await self._get_conversation(conversation_id, workspace_id, channel_type)
        rule = await self._find_matching_rule(
            workspace_id=workspace_id,
            channel_type=channel_type,
            conversation=conversation,
            inbound_text=conversation.last_message_preview or "",
        )
        return await self.ai_service.generate_copilot(
            channel_type=channel_type,
            conversation=conversation,
            goal=payload.goal,
            tone=payload.tone,
            automation_rule=rule,
        )

    async def refresh_follow_status(
        self,
        workspace_id: UUID,
        channel_type: MetaChannelType,
        conversation_id: UUID,
        payload: FollowStatusRefreshRequest,
    ) -> MetaSocialConversationRead:
        conversation = await self._get_conversation(conversation_id, workspace_id, channel_type)
        conversation.follow_status = payload.status
        conversation.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        return await self.get_conversation(workspace_id, channel_type, conversation.id)

    async def list_approved_messages(
        self, workspace_id: UUID, channel_type: MetaChannelType
    ) -> list[MetaApprovedMessageRead]:
        result = await self.db.execute(
            select(MetaApprovedMessage)
            .where(
                MetaApprovedMessage.workspace_id == workspace_id,
                MetaApprovedMessage.channel_type == channel_type,
            )
            .order_by(MetaApprovedMessage.updated_at.desc())
        )
        return [MetaApprovedMessageRead.model_validate(item) for item in result.scalars().all()]

    async def create_approved_message(
        self,
        workspace_id: UUID,
        channel_type: MetaChannelType,
        acting_user_id: UUID,
        payload: MetaApprovedMessageCreate,
    ) -> MetaApprovedMessageRead:
        record = MetaApprovedMessage(
            workspace_id=workspace_id,
            channel_type=channel_type,
            message_kind=payload.message_kind,
            title=payload.title.strip(),
            body=payload.body.strip(),
            cta_label=payload.cta_label,
            cta_url=payload.cta_url,
            status=payload.status,
            requires_following=payload.requires_following,
            restrictions=payload.restrictions,
            created_by_id=acting_user_id,
            approved_by_id=acting_user_id
            if payload.status == ApprovedMessageStatus.approved
            else None,
        )
        self.db.add(record)
        await self.db.flush()
        return MetaApprovedMessageRead.model_validate(record)

    async def list_automation_rules(
        self, workspace_id: UUID, channel_type: MetaChannelType
    ) -> list[MetaAutomationRuleRead]:
        result = await self.db.execute(
            select(MetaAutomationRule)
            .where(
                MetaAutomationRule.workspace_id == workspace_id,
                MetaAutomationRule.channel_type == channel_type,
            )
            .order_by(MetaAutomationRule.updated_at.desc())
        )
        return [MetaAutomationRuleRead.model_validate(item) for item in result.scalars().all()]

    async def create_automation_rule(
        self,
        workspace_id: UUID,
        channel_type: MetaChannelType,
        payload: MetaAutomationRuleCreate,
    ) -> MetaAutomationRuleRead:
        if payload.approved_message_id is not None:
            await self._get_approved_message(
                payload.approved_message_id, workspace_id, channel_type
            )
        rule = MetaAutomationRule(
            workspace_id=workspace_id,
            channel_type=channel_type,
            title=payload.title.strip(),
            event_type=payload.event_type,
            enabled=payload.enabled,
            keyword=payload.keyword,
            approved_message_id=payload.approved_message_id,
            action_config=payload.action_config,
        )
        self.db.add(rule)
        await self.db.flush()
        return MetaAutomationRuleRead.model_validate(rule)

    async def list_campaigns(
        self, workspace_id: UUID, channel_type: MetaChannelType
    ) -> list[MetaCampaignRead]:
        result = await self.db.execute(
            select(MetaChannelCampaign)
            .where(
                MetaChannelCampaign.workspace_id == workspace_id,
                MetaChannelCampaign.channel_type == channel_type,
            )
            .order_by(MetaChannelCampaign.updated_at.desc())
        )
        return [MetaCampaignRead.model_validate(item) for item in result.scalars().all()]

    async def create_campaign(
        self,
        workspace_id: UUID,
        channel_type: MetaChannelType,
        acting_user_id: UUID,
        payload: MetaCampaignCreate,
    ) -> MetaCampaignRead:
        await self._get_connection(payload.connection_id, workspace_id, channel_type)
        if payload.approved_message_id is not None:
            await self._get_approved_message(
                payload.approved_message_id, workspace_id, channel_type
            )
        # scheduled_at hoje só é metadado informativo: a campanha não pode
        # nascer "scheduled" (isso prometeria um disparo que não existe).
        requested_status = (
            MetaCampaignStatus.scheduled if payload.scheduled_at else MetaCampaignStatus.draft
        )
        self._guard_campaign_status(requested_status)
        computed_policy = await self._compute_policy_summary(
            workspace_id, channel_type, payload.connection_id
        )
        campaign = MetaChannelCampaign(
            workspace_id=workspace_id,
            channel_type=channel_type,
            connection_id=payload.connection_id,
            created_by_id=acting_user_id,
            approved_message_id=payload.approved_message_id,
            title=payload.title.strip(),
            description=payload.description,
            audience_filter=payload.audience_filter,
            policy_summary=computed_policy,
            scheduled_at=payload.scheduled_at,
            status=requested_status,
            total_recipients=int(payload.audience_filter.get("estimatedRecipients", 0) or 0),
            eligible_recipients=computed_policy["eligibleRecipients"],
            blocked_recipients=computed_policy["blockedRecipients"],
        )
        self.db.add(campaign)
        await self.db.flush()
        return MetaCampaignRead.model_validate(campaign)

    async def update_campaign(
        self,
        workspace_id: UUID,
        channel_type: MetaChannelType,
        campaign_id: UUID,
        payload: MetaCampaignUpdate,
    ) -> MetaCampaignRead:
        campaign = await self._get_campaign(campaign_id, workspace_id, channel_type)
        update_data = payload.model_dump(exclude_unset=True)
        requested_status = update_data.get("status")
        if requested_status is not None:
            self._guard_campaign_status(MetaCampaignStatus(requested_status))
        # policy_summary/eligible/blocked digitados no payload são sempre
        # ignorados e recalculados server-side a partir do último inbound real
        # (AUDITORIA achado Messenger: "compliance autodeclarado").
        update_data.pop("policy_summary", None)
        computed_policy = await self._compute_policy_summary(
            workspace_id, channel_type, campaign.connection_id
        )
        update_data["policy_summary"] = computed_policy
        update_data["eligible_recipients"] = computed_policy["eligibleRecipients"]
        update_data["blocked_recipients"] = computed_policy["blockedRecipients"]
        for field, value in update_data.items():
            setattr(campaign, field, value)
        await self.db.flush()
        await self.db.refresh(campaign)
        return MetaCampaignRead.model_validate(campaign)

    def _guard_campaign_status(self, campaign_status: MetaCampaignStatus) -> None:
        if campaign_status in _CAMPAIGN_STATUSES_REQUIRING_TRANSPORT:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_CAMPAIGN_TRANSPORT_BLOCKED_DETAIL,
            )

    async def _compute_policy_summary(
        self,
        workspace_id: UUID,
        channel_type: MetaChannelType,
        connection_id: UUID,
    ) -> dict:
        """Elegibilidade real de reengajamento, canal a canal.

        Ignora qualquer eligibleRecipients/blockedRecipients digitado pelo
        cliente (AUDITORIA achado Messenger: "compliance autodeclarado"):
        elegível = conversa com janela de 24h aberta agora, a partir do
        último inbound real (`MetaPolicyGate`, mesmo gate usado pelos workers
        de disparo). Sem inbound registrado => bloqueada, "janela expirada".
        """
        now = datetime.now(timezone.utc)
        channel_label = "instagram" if channel_type == MetaChannelType.instagram else "messenger"
        result = await self.db.execute(
            select(MetaSocialConversation.id).where(
                MetaSocialConversation.workspace_id == workspace_id,
                MetaSocialConversation.channel_type == channel_type,
                MetaSocialConversation.connection_id == connection_id,
            )
        )
        conversation_ids = [row[0] for row in result.all()]

        eligible = 0
        blocked = 0
        for conversation_id in conversation_ids:
            inbound_result = await self.db.execute(
                select(MetaSocialMessage.timestamp)
                .where(
                    MetaSocialMessage.conversation_id == conversation_id,
                    MetaSocialMessage.direction == WaMessageDirection.inbound,
                )
                .order_by(MetaSocialMessage.timestamp.desc())
                .limit(1)
            )
            last_inbound_at = inbound_result.scalar_one_or_none()
            decision = MetaPolicyGate.evaluate(
                channel_label,
                last_inbound_at=last_inbound_at,
                now=now,
                author_type="agent",
                action_type="broadcast",
            )
            if decision.allowed:
                eligible += 1
            else:
                blocked += 1

        return {
            "eligibleRecipients": eligible,
            "blockedRecipients": blocked,
            "totalConversations": len(conversation_ids),
            "windowHours": 24,
            "computedAt": now.isoformat(),
            "reason": (
                "Elegibilidade calculada server-side a partir da janela de 24h desde o "
                "último inbound real de cada conversa; valores digitados no formulário "
                "são ignorados."
            ),
        }

    async def list_notes(
        self,
        workspace_id: UUID,
        channel_type: MetaChannelType,
        conversation_id: UUID,
    ) -> list[MetaConversationNoteRead]:
        await self._get_conversation(conversation_id, workspace_id, channel_type)
        result = await self.db.execute(
            select(MetaConversationNote)
            .options(selectinload(MetaConversationNote.author))
            .where(
                MetaConversationNote.workspace_id == workspace_id,
                MetaConversationNote.channel_type == channel_type,
                MetaConversationNote.conversation_id == conversation_id,
            )
            .order_by(MetaConversationNote.created_at.desc())
        )
        return [
            MetaConversationNoteRead(
                id=item.id,
                conversationId=item.conversation_id,
                matterId=item.matter_id,
                authorUserId=item.author_user_id,
                authorName=item.author.name if item.author else item.author_name,
                body=item.body,
                mentions=[str(value) for value in (item.mentions or [])],
                noteMetadata=item.note_metadata or {},
                createdAt=item.created_at,
            )
            for item in result.scalars().all()
        ]

    async def create_note(
        self,
        workspace_id: UUID,
        channel_type: MetaChannelType,
        conversation_id: UUID,
        acting_user_id: UUID,
        payload: MetaConversationNoteCreate,
    ) -> MetaConversationNoteRead:
        conversation = await self._get_conversation(conversation_id, workspace_id, channel_type)
        author_name = await self._get_user_name(acting_user_id)
        note = MetaConversationNote(
            workspace_id=workspace_id,
            channel_type=channel_type,
            conversation_id=conversation.id,
            matter_id=conversation.matter_id,
            author_user_id=acting_user_id,
            author_name=author_name,
            body=payload.body.strip(),
            mentions=payload.mentions,
            note_metadata={"source": "social_inbox"},
        )
        self.db.add(note)
        await self.db.flush()
        if conversation.matter_id:
            await self.matter_service.add_note(
                conversation.matter_id,
                f"[Nota interna {channel_type.value}] {payload.body.strip()}",
                agent_id=acting_user_id,
                workspace_id=workspace_id,
            )
        return (await self.list_notes(workspace_id, channel_type, conversation_id))[0]

    async def get_drawer(
        self,
        workspace_id: UUID,
        channel_type: MetaChannelType,
        conversation_id: UUID,
        *,
        can_edit_matter: bool,
        can_manage_conversation: bool,
    ) -> MetaLeadDrawerRead:
        conversation = await self._get_conversation(conversation_id, workspace_id, channel_type)
        matter = None
        recent_meetings: list[MeetingRead] = []
        if conversation.matter_id:
            matter = await self.matter_service.get_by_id(conversation.matter_id, workspace_id)
            if matter:
                meetings = await self.meeting_service.list_for_workspace(workspace_id)
                recent_meetings = [
                    item if isinstance(item, MeetingRead) else MeetingRead.model_validate(item)
                    for item in meetings
                    if item.matter_id == conversation.matter_id
                ][:5]
        return MetaLeadDrawerRead(
            conversation=await self._build_conversation_read(conversation, include_messages=False),
            matter=matter and MatterRead.model_validate(matter),
            notes=await self.list_notes(workspace_id, channel_type, conversation_id),
            recentMeetings=recent_meetings,
            canEditMatter=can_edit_matter,
            canManageConversation=can_manage_conversation,
        )

    async def move_stage(
        self,
        workspace_id: UUID,
        channel_type: MetaChannelType,
        conversation_id: UUID,
        acting_user_id: UUID,
        payload: MetaMoveStageRequest,
        *,
        can_edit_matter: bool,
        can_manage_conversation: bool,
    ) -> MetaLeadDrawerRead:
        if not can_edit_matter:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Sem permissão para mover o card."
            )
        conversation = await self._get_conversation(conversation_id, workspace_id, channel_type)
        if not conversation.matter_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A conversa ainda não está vinculada a um card.",
            )
        await self.matter_service.move_card(
            conversation.matter_id,
            MatterMoveRequest(column_id=payload.column_id, micro_status=payload.micro_status),
            moved_by=acting_user_id,
            workspace_id=workspace_id,
        )
        return await self.get_drawer(
            workspace_id,
            channel_type,
            conversation_id,
            can_edit_matter=can_edit_matter,
            can_manage_conversation=can_manage_conversation,
        )

    async def update_matter(
        self,
        workspace_id: UUID,
        channel_type: MetaChannelType,
        conversation_id: UUID,
        acting_user_id: UUID,
        payload: MetaMatterUpdateRequest,
        *,
        can_edit_matter: bool,
        can_manage_conversation: bool,
    ) -> MetaLeadDrawerRead:
        if not can_edit_matter:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Sem permissão para editar o card."
            )
        conversation = await self._get_conversation(conversation_id, workspace_id, channel_type)
        if not conversation.matter_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A conversa ainda não está vinculada a um card.",
            )
        matter = await self.matter_service.get_by_id(conversation.matter_id, workspace_id)
        if matter is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Card não encontrado."
            )
        await self.matter_service.update(
            conversation.matter_id,
            MatterUpdate(**payload.model_dump(exclude_none=True)),
            updated_by=acting_user_id,
            workspace_id=workspace_id,
        )
        return await self.get_drawer(
            workspace_id,
            channel_type,
            conversation_id,
            can_edit_matter=can_edit_matter,
            can_manage_conversation=can_manage_conversation,
        )

    async def _build_conversation_read(
        self,
        conversation: MetaSocialConversation,
        *,
        include_messages: bool,
    ) -> MetaSocialConversationRead:
        messages = []
        if include_messages:
            messages = [
                MetaSocialMessageRead.model_validate(item) for item in conversation.messages
            ]
        return MetaSocialConversationRead(
            id=conversation.id,
            channelType=conversation.channel_type,
            connectionId=conversation.connection_id,
            matterId=conversation.matter_id,
            assignedUserId=conversation.assigned_user_id,
            assignedUserName=conversation.assigned_user.name
            if conversation.assigned_user
            else None,
            externalThreadId=conversation.external_thread_id,
            externalParticipantId=conversation.external_participant_id,
            participantName=conversation.participant_name,
            participantHandle=conversation.participant_handle,
            participantAvatarUrl=conversation.participant_avatar_url,
            eventType=conversation.event_type,
            queueStatus=conversation.queue_status,
            humanControlState=conversation.human_control_state,
            followStatus=conversation.follow_status,
            sourceTitle=conversation.source_title,
            sourceUrl=conversation.source_url,
            sourceMetadata=conversation.source_metadata or {},
            activeFlowLabel=conversation.active_flow_label,
            nextFollowUpAt=conversation.next_follow_up_at,
            lastMessagePreview=conversation.last_message_preview,
            lastMessageAt=conversation.last_message_at,
            unreadCount=conversation.unread_count,
            policyState=conversation.policy_state,
            isPublicThread=conversation.is_public_thread,
            messages=messages,
        )

    async def _get_connection(
        self, connection_id: UUID, workspace_id: UUID, channel_type: MetaChannelType
    ) -> MetaChannelConnection:
        result = await self.db.execute(
            select(MetaChannelConnection).where(
                MetaChannelConnection.id == connection_id,
                MetaChannelConnection.workspace_id == workspace_id,
                MetaChannelConnection.channel_type == channel_type,
            )
        )
        connection = result.scalar_one_or_none()
        if connection is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conexão do canal não encontrada."
            )
        return connection

    async def _get_conversation(
        self, conversation_id: UUID, workspace_id: UUID, channel_type: MetaChannelType
    ) -> MetaSocialConversation:
        result = await self.db.execute(
            select(MetaSocialConversation)
            .options(
                selectinload(MetaSocialConversation.messages),
                selectinload(MetaSocialConversation.assigned_user),
            )
            .where(
                MetaSocialConversation.id == conversation_id,
                MetaSocialConversation.workspace_id == workspace_id,
                MetaSocialConversation.channel_type == channel_type,
            )
        )
        conversation = result.scalars().first()
        if conversation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversa social não encontrada."
            )
        return conversation

    async def _get_approved_message(
        self,
        message_id: UUID,
        workspace_id: UUID,
        channel_type: MetaChannelType,
    ) -> MetaApprovedMessage:
        result = await self.db.execute(
            select(MetaApprovedMessage).where(
                MetaApprovedMessage.id == message_id,
                MetaApprovedMessage.workspace_id == workspace_id,
                MetaApprovedMessage.channel_type == channel_type,
            )
        )
        record = result.scalar_one_or_none()
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Mensagem aprovada não encontrada."
            )
        return record

    async def _get_campaign(
        self, campaign_id: UUID, workspace_id: UUID, channel_type: MetaChannelType
    ) -> MetaChannelCampaign:
        result = await self.db.execute(
            select(MetaChannelCampaign).where(
                MetaChannelCampaign.id == campaign_id,
                MetaChannelCampaign.workspace_id == workspace_id,
                MetaChannelCampaign.channel_type == channel_type,
            )
        )
        campaign = result.scalar_one_or_none()
        if campaign is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Campanha do canal não encontrada."
            )
        return campaign

    async def _get_user_name(self, user_id: UUID) -> str | None:
        from app.models.user import User

        result = await self.db.execute(select(User.name).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def _find_matching_rule(
        self,
        *,
        workspace_id: UUID,
        channel_type: MetaChannelType,
        conversation: MetaSocialConversation,
        inbound_text: str,
    ) -> MetaAutomationRule | None:
        result = await self.db.execute(
            select(MetaAutomationRule)
            .where(
                MetaAutomationRule.workspace_id == workspace_id,
                MetaAutomationRule.channel_type == channel_type,
                MetaAutomationRule.enabled.is_(True),
                MetaAutomationRule.event_type == conversation.event_type,
            )
            .order_by(MetaAutomationRule.updated_at.desc())
        )
        normalized_inbound = (inbound_text or "").strip().lower()
        for rule in result.scalars().all():
            keyword = (rule.keyword or "").strip().lower()
            if keyword and keyword not in normalized_inbound:
                continue
            action_config = rule.action_config or {}
            if (
                action_config.get("requiresFollowing")
                and conversation.follow_status != FollowRelationshipStatus.following
            ):
                continue
            return rule
        return None

    async def _run_automation_for_conversation(
        self,
        *,
        workspace_id: UUID,
        channel_type: MetaChannelType,
        conversation: MetaSocialConversation,
        inbound_text: str,
    ) -> None:
        conversation = await self._get_conversation(conversation.id, workspace_id, channel_type)
        rule = await self._find_matching_rule(
            workspace_id=workspace_id,
            channel_type=channel_type,
            conversation=conversation,
            inbound_text=inbound_text,
        )
        if rule is None:
            return

        action_config = rule.action_config or {}
        approved_message = None
        if rule.approved_message_id:
            approved_message = await self._get_approved_message(
                rule.approved_message_id, workspace_id, channel_type
            )

        target_queue_status = action_config.get("targetQueueStatus")
        if target_queue_status in {item.value for item in WaQueueStatus}:
            conversation.queue_status = WaQueueStatus(target_queue_status)
        if conversation.queue_status == WaQueueStatus.human:
            conversation.human_control_state = HumanControlState.copilot

        target_column_id = action_config.get("targetColumnId")
        if target_column_id and conversation.matter_id:
            try:
                await self.matter_service.move_card(
                    conversation.matter_id,
                    MatterMoveRequest(column_id=target_column_id, micro_status=None),
                    moved_by=conversation.assigned_user_id,
                    workspace_id=workspace_id,
                )
            except Exception as exc:
                logger.warning(
                    "meta_channels.automation_move_stage_failed",
                    conversation_id=str(conversation.id),
                    rule_id=str(rule.id),
                    error_type=safe_error_type(exc),
                )

        reply_body: str | None = None
        message_metadata: dict[str, object] = {
            "automationRuleId": str(rule.id),
            "automationTitle": rule.title,
            "aiGenerated": False,
        }
        is_public_reply = bool(action_config.get("publicReply", conversation.is_public_thread))

        if action_config.get("aiEnabled"):
            try:
                generated = await self.ai_service.generate_automation_reply(
                    channel_type=channel_type,
                    conversation=conversation,
                    rule=rule,
                    approved_message=approved_message,
                )
                reply_body = generated["reply"]
                is_public_reply = bool(generated.get("suggested_public_reply", is_public_reply))
                message_metadata.update(
                    {
                        "aiGenerated": True,
                        "provider": generated.get("provider"),
                        "modelUsed": generated.get("model_used"),
                    }
                )
            except Exception as exc:
                logger.warning(
                    "meta_channels.automation_ai_failed",
                    conversation_id=str(conversation.id),
                    rule_id=str(rule.id),
                    error_type=safe_error_type(exc),
                )

        if not reply_body and approved_message is not None:
            reply_body = approved_message.body
            message_metadata["approvedMessageId"] = str(approved_message.id)

        if reply_body:
            now = datetime.now(timezone.utc)
            delivery_allowed = external_delivery_allowed()
            message_status = WaMessageStatus.pending
            if delivery_allowed:
                message_metadata.update(
                    {
                        "deliveryStatus": "queued",
                        "externalActionStatus": "awaiting_transport",
                        "externalGate": "open",
                        "externalActionsExecuted": 0,
                        "blockedReason": "meta_transport_not_connected",
                    }
                )
            else:
                message_metadata.update(
                    {
                        "deliveryStatus": "local_draft",
                        "externalActionStatus": "blocked",
                        "externalGate": "closed",
                        "blockedReason": "klm_external_gate_closed",
                        "externalActionsExecuted": 0,
                    }
                )
                logger.info(
                    "meta_channels.automation_outbound_blocked_by_klm_gate",
                    workspace_id=str(workspace_id),
                    conversation_id=str(conversation.id),
                    rule_id=str(rule.id),
                    message_status=message_status.value,
                    delivery_status="local_draft",
                )
            self.db.add(
                MetaSocialMessage(
                    conversation_id=conversation.id,
                    direction=WaMessageDirection.outbound,
                    status=message_status,
                    body=reply_body.strip(),
                    author_name=f"IA {channel_type.value.title()}",
                    is_public_reply=is_public_reply,
                    message_metadata=message_metadata,
                    timestamp=now,
                )
            )
            conversation.last_message_preview = reply_body.strip()
            conversation.last_message_at = now
            conversation.updated_at = now
            self.db.expire(conversation, ["messages"])
        await self.db.flush()
