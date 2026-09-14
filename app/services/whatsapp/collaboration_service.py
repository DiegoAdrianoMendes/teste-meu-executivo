from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.whatsapp import WaMessageDirection, WaMessageStatus, WhatsAppContact, WhatsAppMessage
from app.models.whatsapp_note import WhatsAppConversationNote
from app.schemas.control_plane import MeetingRead
from app.schemas.matter import MatterRead, MatterMoveRequest
from app.schemas.whatsapp import WhatsAppContactRead
from app.schemas.whatsapp_collaboration import (
    ConversationInternalNoteCreate,
    ConversationInternalNoteRead,
    LeadDrawerRead,
    MoveConversationStageRequest,
)
from app.services.matter_service import MatterService
from app.services.meeting_service import MeetingService


class WhatsAppCollaborationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.matter_service = MatterService(db)
        self.meeting_service = MeetingService(db)

    async def list_notes(self, contact_id: UUID, workspace_id: UUID) -> list[ConversationInternalNoteRead]:
        contact = await self._get_contact(contact_id, workspace_id)
        result = await self.db.execute(
            select(WhatsAppConversationNote)
            .options(selectinload(WhatsAppConversationNote.author))
            .where(
                WhatsAppConversationNote.contact_id == contact.id,
                WhatsAppConversationNote.workspace_id == workspace_id,
            )
            .order_by(WhatsAppConversationNote.created_at.desc())
        )
        notes = result.scalars().all()
        return [
            ConversationInternalNoteRead(
                id=note.id,
                contactId=note.contact_id,
                matterId=note.matter_id,
                authorUserId=note.author_user_id,
                authorName=note.author.name if note.author else None,
                body=note.body,
                mentions=[str(item) for item in (note.mentions or [])],
                noteMetadata=note.note_metadata or {},
                createdAt=note.created_at,
            )
            for note in notes
        ]

    async def create_note(
        self,
        contact_id: UUID,
        workspace_id: UUID,
        acting_user_id: UUID,
        payload: ConversationInternalNoteCreate,
    ) -> ConversationInternalNoteRead:
        contact = await self._get_contact(contact_id, workspace_id)
        note = WhatsAppConversationNote(
            workspace_id=workspace_id,
            contact_id=contact.id,
            matter_id=contact.matter_id,
            author_user_id=acting_user_id,
            body=payload.body.strip(),
            mentions=payload.mentions,
            note_metadata={"source": "inbox"},
        )
        self.db.add(note)
        await self.db.flush()

        if contact.matter_id:
            await self.matter_service.add_note(
                contact.matter_id,
                f"[Nota interna do inbox] {payload.body.strip()}",
                agent_id=acting_user_id,
                workspace_id=workspace_id,
            )

        await self.db.commit()
        return (await self.list_notes(contact_id, workspace_id))[0]

    async def get_lead_drawer(
        self,
        contact_id: UUID,
        workspace_id: UUID,
        *,
        can_edit_matter: bool,
        can_manage_conversation: bool,
    ) -> LeadDrawerRead:
        contact = await self._get_contact(contact_id, workspace_id)
        matter = None
        recent_meetings: list[MeetingRead] = []
        if contact.matter_id:
            matter = await self.matter_service.get_by_id(contact.matter_id, workspace_id)
            if matter:
                meetings = await self.meeting_service.list_for_workspace(workspace_id)
                recent_meetings = [
                    item if isinstance(item, MeetingRead) else MeetingRead.model_validate(item)
                    for item in meetings
                    if item.matter_id == contact.matter_id
                ][:5]

        return LeadDrawerRead(
            contact=await self._build_contact_read(contact),
            matter=matter and MatterRead.model_validate(matter),
            notes=await self.list_notes(contact_id, workspace_id),
            recentMeetings=recent_meetings,
            canEditMatter=can_edit_matter,
            canManageConversation=can_manage_conversation,
        )

    async def move_stage(
        self,
        contact_id: UUID,
        workspace_id: UUID,
        acting_user_id: UUID,
        payload: MoveConversationStageRequest,
        *,
        can_edit_matter: bool,
        can_manage_conversation: bool,
    ) -> LeadDrawerRead:
        if not can_edit_matter:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sem permissão para mover o card.")
        contact = await self._get_contact(contact_id, workspace_id)
        if not contact.matter_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "WA-COL-001", "message": "A conversa não está vinculada a um card."},
            )
        await self.matter_service.move_card(
            contact.matter_id,
            MatterMoveRequest(column_id=payload.column_id, micro_status=payload.micro_status),
            moved_by=acting_user_id,
            workspace_id=workspace_id,
        )
        await self.db.commit()
        return await self.get_lead_drawer(
            contact_id,
            workspace_id,
            can_edit_matter=can_edit_matter,
            can_manage_conversation=can_manage_conversation,
        )

    async def optimize_message(self, body: str, tone: str) -> list[str]:
        clean = " ".join(body.split())
        if not clean:
            return []
        openings = {
            "clear": "Olá! Segue uma versão mais objetiva:",
            "short": "Versão enxuta:",
            "commercial": "Versão com CTA comercial:",
            "warm": "Versão mais acolhedora:",
        }
        optimized = {
            "clear": clean.replace("??", "?"),
            "short": clean[:220].rstrip(),
            "commercial": f"{clean.rstrip('.')}.\n\nSe fizer sentido, posso te explicar o próximo passo agora mesmo.",
            "warm": f"Olá! {clean[0].upper() + clean[1:] if len(clean) > 1 else clean}\n\nFico à disposição para te ajudar no que precisar.",
        }
        tone_key = tone if tone in optimized else "clear"
        suggestions = [optimized[tone_key]]
        if tone_key != "short":
            suggestions.append(optimized["short"])
        if tone_key != "commercial":
            suggestions.append(optimized["commercial"])
        return [f"{openings[tone_key]}\n\n{suggestions[0]}", *suggestions[1:3]]

    async def _get_contact(self, contact_id: UUID, workspace_id: UUID) -> WhatsAppContact:
        result = await self.db.execute(
            select(WhatsAppContact)
            .options(
                selectinload(WhatsAppContact.assigned_user),
                selectinload(WhatsAppContact.connection),
            )
            .where(
                WhatsAppContact.id == contact_id,
                WhatsAppContact.workspace_id == workspace_id,
            )
        )
        contact = result.scalars().first()
        if contact is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WA-COL-404", "message": "Conversa não encontrada."},
            )
        return contact

    async def _build_contact_read(self, contact: WhatsAppContact) -> WhatsAppContactRead:
        last_message_result = await self.db.execute(
            select(WhatsAppMessage)
            .where(WhatsAppMessage.contact_id == contact.id)
            .order_by(WhatsAppMessage.timestamp.desc())
            .limit(1)
        )
        last_message = last_message_result.scalars().first()
        unread_result = await self.db.execute(
            select(func.count(WhatsAppMessage.id)).where(
                WhatsAppMessage.contact_id == contact.id,
                WhatsAppMessage.direction == WaMessageDirection.inbound,
                WhatsAppMessage.status != WaMessageStatus.read,
            )
        )
        unread_count = unread_result.scalar() or 0
        return WhatsAppContactRead(
            id=contact.id,
            whatsappConnectionId=contact.whatsapp_connection_id,
            phone=contact.phone,
            name=contact.name,
            avatarUrl=contact.avatar_url,
            matterId=contact.matter_id,
            lastSeen=contact.last_seen,
            isBlocked=contact.is_blocked,
            unreadCount=unread_count,
            lastMessage=last_message.body if last_message else None,
            lastMessageTime=last_message.timestamp if last_message else None,
            assignedUserId=contact.assigned_user_id,
            assignedUserName=contact.assigned_user.name if contact.assigned_user else None,
            queueStatus=contact.queue_status,
            humanControlState=contact.human_control_state,
            activeFlowTemplateId=contact.active_flow_template_id,
            activeFlowLabel=contact.active_flow_label,
            nextFollowUpAt=contact.next_follow_up_at,
        )
