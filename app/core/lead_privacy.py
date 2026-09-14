"""Mascaramento de identidade e contato aplicado antes da serialização HTTP."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from app.schemas.agent_runtime import AgentRunRead, HumanOverrideRead
from app.schemas.approval import ApprovalRead
from app.schemas.card_dossier import AuditLogRead, CardFieldChangeRead, ConversationInsightRead
from app.schemas.client import ClientRead
from app.schemas.control_plane import MeetingRead
from app.schemas.conversation import ConversationRead as MatterConversationRead
from app.schemas.document import DocumentRead
from app.schemas.finance import FinanceEntryRead, FinanceOverviewRead, FinanceReminderRead
from app.schemas.flow import FlowRunRead, ScheduledMessageRead
from app.schemas.klm_operations import (
    AcquisitionEventRead,
    AuditEventRead,
    EmailIngestRead,
    ExternalActionIntentRead,
    FollowUpRead,
    FunnelRead,
    LegalArtifactVersionRead,
    OmnichannelMessageRead,
    OmnichannelThreadRead,
    ProcessMonitoringRead,
)
from app.schemas.knowledge_chunk import KnowledgeChunkRead
from app.schemas.matter import MatterListItem, MatterRead
from app.schemas.meta_channels import (
    MetaCommentThreadRead,
    MetaConversationNoteRead,
    MetaLeadDrawerRead,
    MetaSocialConversationRead,
)
from app.schemas.notification import NotificationRead
from app.schemas.sdr import SDRStatusResponse
from app.schemas.task import TaskRead
from app.schemas.timeline_event import TimelineEventRead
from app.schemas.whatsapp import (
    ConversationRead,
    WhatsAppContactIdentityRead,
    WhatsAppContactRead,
    WhatsAppMessageRead,
)
from app.schemas.whatsapp_campaign import (
    CampaignAudienceRead,
    CampaignRead,
    CampaignRecipientRead,
    OptInRecordRead,
    SuppressionEntryRead,
)
from app.schemas.whatsapp_collaboration import ConversationInternalNoteRead, LeadDrawerRead

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_CPF_RE = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?55\s*)?(?:\(?\d{2}\)?[\s.-]*)?9?\d{4}[\s.-]?\d{4}(?!\d)")


def masked_lead_label(identifier: UUID | str | None) -> str:
    token = str(identifier or "lead").replace("-", "")[-6:].upper()
    return f"Lead {token}"


def redact_text_identifiers(value: str | None) -> str | None:
    if value is None:
        return None
    redacted = _EMAIL_RE.sub("[e-mail oculto]", value)
    redacted = _CPF_RE.sub("[CPF oculto]", redacted)
    return _PHONE_RE.sub("[telefone oculto]", redacted)


def mask_client(value: ClientRead | Any) -> ClientRead:
    client = value if isinstance(value, ClientRead) else ClientRead.model_validate(value)
    label = masked_lead_label(client.id)
    return client.model_copy(
        update={
            "name": label,
            "cpf": None,
            "email": None,
            "phone": "Oculto pelo administrador",
            "employer": None,
            "position": None,
            "sensitive_data_hidden": True,
            "masked_label": label,
        }
    )


def mask_approval(value: ApprovalRead | Any) -> ApprovalRead:
    approval = value if isinstance(value, ApprovalRead) else ApprovalRead.model_validate(value)
    return approval.model_copy(
        update={
            "client_name": masked_lead_label(approval.matter_id),
            "description": redact_text_identifiers(approval.description),
            "resolution_reason": redact_text_identifiers(approval.resolution_reason),
            "document_version_url": None,
            "sensitive_data_hidden": True,
        }
    )


def mask_matter_list_item(value: MatterListItem | Any) -> MatterListItem:
    matter = value if isinstance(value, MatterListItem) else MatterListItem.model_validate(value)
    names = (matter.client.name,)
    return matter.model_copy(
        update={
            "client": mask_client(matter.client),
            "executive_summary": redact_sensitive_metadata(matter.executive_summary, names),
            "next_action": redact_sensitive_metadata(matter.next_action, names),
            "last_interaction": redact_sensitive_metadata(matter.last_interaction, names),
            "conversation_insight": mask_conversation_insight(matter.conversation_insight, names)
            if matter.conversation_insight
            else None,
            "zapsign_doc_token": None,
            "zapsign_sign_url": None,
        }
    )


def mask_matter(value: MatterRead | Any) -> MatterRead:
    matter = value if isinstance(value, MatterRead) else MatterRead.model_validate(value)
    names = (matter.client.name,)
    return matter.model_copy(
        update={
            "client": mask_client(matter.client),
            "executive_summary": redact_sensitive_metadata(matter.executive_summary, names),
            "next_action": redact_sensitive_metadata(matter.next_action, names),
            "last_interaction": redact_sensitive_metadata(matter.last_interaction, names),
            "strategy_notes": redact_sensitive_metadata(matter.strategy_notes, names),
            "risks": redact_sensitive_metadata(matter.risks, names),
            "blocks": redact_sensitive_metadata(matter.blocks, names),
            "tasks": [mask_task(item, names) for item in matter.tasks],
            "documents": [mask_document(item) for item in matter.documents],
            "timeline": [mask_timeline_event(item, names) for item in matter.timeline],
            "conversations": [
                mask_matter_conversation(item, names) for item in matter.conversations
            ],
            "field_changes": [mask_field_change(item, names) for item in matter.field_changes],
            "audit_logs": [mask_audit_log(item, names) for item in matter.audit_logs],
            "agent_runs": [mask_agent_run(item) for item in matter.agent_runs],
            "human_overrides": [mask_human_override(item) for item in matter.human_overrides],
            "knowledge_chunks": [mask_knowledge_chunk(item) for item in matter.knowledge_chunks],
            "conversation_insight": mask_conversation_insight(matter.conversation_insight, names)
            if matter.conversation_insight
            else None,
        }
    )


def mask_document(value: DocumentRead | Any) -> DocumentRead:
    document = value if isinstance(value, DocumentRead) else DocumentRead.model_validate(value)
    return document.model_copy(
        update={
            "name": f"Documento protegido {str(document.id)[-6:].upper()}",
            "url": None,
        }
    )


def mask_task(value: TaskRead | Any, names: tuple[str | None, ...] = ()) -> TaskRead:
    task = value if isinstance(value, TaskRead) else TaskRead.model_validate(value)
    return task.model_copy(
        update={
            "title": redact_sensitive_metadata(task.title, names) or "Tarefa protegida",
            "description": redact_sensitive_metadata(task.description, names),
        }
    )


def mask_timeline_event(
    value: TimelineEventRead | Any, names: tuple[str, ...] = ()
) -> TimelineEventRead:
    event = (
        value if isinstance(value, TimelineEventRead) else TimelineEventRead.model_validate(value)
    )
    return event.model_copy(
        update={
            "title": redact_sensitive_metadata(event.title, names) or "Evento protegido",
            "description": redact_sensitive_metadata(event.description, names),
            "event_data": redact_sensitive_metadata(event.event_data, names),
        }
    )


def mask_matter_conversation(
    value: MatterConversationRead | Any,
    names: tuple[str, ...] = (),
) -> MatterConversationRead:
    conversation = (
        value
        if isinstance(value, MatterConversationRead)
        else MatterConversationRead.model_validate(value)
    )
    return conversation.model_copy(
        update={
            "message": redact_sensitive_metadata(conversation.message, names) or "[conteúdo oculto]"
        }
    )


def mask_field_change(
    value: CardFieldChangeRead | Any, names: tuple[str, ...] = ()
) -> CardFieldChangeRead:
    change = (
        value
        if isinstance(value, CardFieldChangeRead)
        else CardFieldChangeRead.model_validate(value)
    )
    return change.model_copy(
        update={
            "old_value": redact_sensitive_metadata(change.old_value, names),
            "new_value": redact_sensitive_metadata(change.new_value, names),
            "summary": redact_sensitive_metadata(change.summary, names),
        }
    )


def mask_audit_log(value: AuditLogRead | Any, names: tuple[str, ...] = ()) -> AuditLogRead:
    log = value if isinstance(value, AuditLogRead) else AuditLogRead.model_validate(value)
    return log.model_copy(
        update={
            "description": redact_sensitive_metadata(log.description, names),
            "before": redact_sensitive_metadata(log.before, names),
            "after": redact_sensitive_metadata(log.after, names),
        }
    )


def mask_human_override(value: HumanOverrideRead | Any) -> HumanOverrideRead:
    override = (
        value if isinstance(value, HumanOverrideRead) else HumanOverrideRead.model_validate(value)
    )
    return override.model_copy(
        update={
            "notes": "[conteúdo protegido]" if override.notes else None,
            "before_payload": {"sensitiveDataHidden": True} if override.before_payload else None,
            "after_payload": {"sensitiveDataHidden": True} if override.after_payload else None,
        }
    )


def mask_agent_run(value: AgentRunRead | Any) -> AgentRunRead:
    run = value if isinstance(value, AgentRunRead) else AgentRunRead.model_validate(value)
    return run.model_copy(
        update={
            "input_payload": {"sensitiveDataHidden": True} if run.input_payload else None,
            "output_payload": {"sensitiveDataHidden": True} if run.output_payload else None,
            "error_message": redact_text_identifiers(run.error_message),
            "overrides": [mask_human_override(item) for item in run.overrides],
        }
    )


def mask_knowledge_chunk(value: KnowledgeChunkRead | Any) -> KnowledgeChunkRead:
    chunk = (
        value if isinstance(value, KnowledgeChunkRead) else KnowledgeChunkRead.model_validate(value)
    )
    return chunk.model_copy(
        update={
            "source_ref": None,
            "title": f"Trecho protegido {str(chunk.id)[-6:].upper()}",
            "content_text": "[conteúdo documental protegido pelo administrador]",
            "summary": "[resumo protegido]" if chunk.summary else None,
            "chunk_metadata": {"sensitiveDataHidden": True},
        }
    )


def mask_conversation_insight(
    value: ConversationInsightRead | Any,
    names: tuple[str, ...] = (),
) -> ConversationInsightRead:
    insight = (
        value
        if isinstance(value, ConversationInsightRead)
        else ConversationInsightRead.model_validate(value)
    )
    return insight.model_copy(
        update={
            "summary": redact_sensitive_metadata(insight.summary, names),
            "objections": redact_sensitive_metadata(insight.objections, names),
            "requested_documents": redact_sensitive_metadata(insight.requested_documents, names),
            "missing_information": redact_sensitive_metadata(insight.missing_information, names),
            "recommended_action": redact_sensitive_metadata(insight.recommended_action, names),
            "next_best_message": redact_sensitive_metadata(insight.next_best_message, names),
            "raw_payload": {"sensitiveDataHidden": True} if insight.raw_payload else {},
        }
    )


def mask_whatsapp_contact(value: WhatsAppContactRead | Any) -> WhatsAppContactRead:
    contact = (
        value
        if isinstance(value, WhatsAppContactRead)
        else WhatsAppContactRead.model_validate(value)
    )
    return contact.model_copy(
        update={
            "phone": "Oculto pelo administrador",
            "bsuid": None,
            "parentBsuid": None,
            "username": None,
            "name": masked_lead_label(contact.id),
            "avatarUrl": None,
            "lastMessage": redact_text_identifiers(contact.lastMessage),
            "sensitiveDataHidden": True,
        }
    )


def mask_whatsapp_contact_identity(
    value: WhatsAppContactIdentityRead | Any,
) -> WhatsAppContactIdentityRead:
    identity = (
        value
        if isinstance(value, WhatsAppContactIdentityRead)
        else WhatsAppContactIdentityRead.model_validate(value)
    )
    return identity.model_copy(
        update={
            "phone": "•••••" if identity.phone else None,
            "phoneMasked": bool(identity.phone),
            "bsuid": None,
            "parentBsuid": None,
            "username": None,
        }
    )


def mask_whatsapp_message(value: WhatsAppMessageRead | Any) -> WhatsAppMessageRead:
    message = (
        value
        if isinstance(value, WhatsAppMessageRead)
        else WhatsAppMessageRead.model_validate(value)
    )
    return message.model_copy(
        update={
            "body": redact_text_identifiers(message.body) or "",
            "sensitiveDataHidden": True,
        }
    )


def mask_whatsapp_conversation(value: ConversationRead | Any) -> ConversationRead:
    conversation = (
        value if isinstance(value, ConversationRead) else ConversationRead.model_validate(value)
    )
    return conversation.model_copy(
        update={
            "contact": mask_whatsapp_contact(conversation.contact),
            "messages": [mask_whatsapp_message(item) for item in conversation.messages],
        }
    )


def mask_lead_drawer(value: LeadDrawerRead | Any) -> LeadDrawerRead:
    drawer = value if isinstance(value, LeadDrawerRead) else LeadDrawerRead.model_validate(value)
    return drawer.model_copy(
        update={
            "contact": mask_whatsapp_contact(drawer.contact),
            "matter": mask_matter(drawer.matter) if drawer.matter else None,
            "notes": [mask_internal_note(item) for item in drawer.notes],
            "recent_meetings": [mask_meeting(item) for item in drawer.recent_meetings],
        }
    )


def mask_internal_note(value: ConversationInternalNoteRead | Any) -> ConversationInternalNoteRead:
    note = (
        value
        if isinstance(value, ConversationInternalNoteRead)
        else ConversationInternalNoteRead.model_validate(value)
    )
    return note.model_copy(
        update={
            "body": "[conteúdo protegido pelo administrador]",
            "mentions": [],
            "note_metadata": {"sensitiveDataHidden": True},
        }
    )


def mask_klm_thread(value: OmnichannelThreadRead | Any) -> OmnichannelThreadRead:
    thread = (
        value
        if isinstance(value, OmnichannelThreadRead)
        else OmnichannelThreadRead.model_validate(value)
    )
    return thread.model_copy(
        update={
            "last_message_preview": redact_text_identifiers(thread.last_message_preview),
            "thread_metadata": redact_sensitive_metadata(thread.thread_metadata),
            "sensitive_data_hidden": True,
        }
    )


def mask_klm_message(value: OmnichannelMessageRead | Any) -> OmnichannelMessageRead:
    message = (
        value
        if isinstance(value, OmnichannelMessageRead)
        else OmnichannelMessageRead.model_validate(value)
    )
    return message.model_copy(
        update={
            "author_label": masked_lead_label(message.thread_id)
            if message.author_type.value == "client"
            else message.author_label,
            "body": redact_text_identifiers(message.body) or "",
            "message_metadata": redact_sensitive_metadata(message.message_metadata),
            "sensitive_data_hidden": True,
        }
    )


def mask_klm_funnel(value: FunnelRead | Any) -> FunnelRead:
    funnel = value if isinstance(value, FunnelRead) else FunnelRead.model_validate(value)
    cards = [
        {
            **card,
            "name": masked_lead_label(card.get("clientId") or card.get("id")),
            "subject": "Dados protegidos",
            "object": "Conteúdo oculto",
            "nextAction": None,
            "lastMessagePreview": None,
            "tags": [],
            "sensitiveDataHidden": True,
        }
        for card in funnel.cards
    ]
    return funnel.model_copy(update={"cards": cards})


def mask_email_ingest(value: EmailIngestRead | Any) -> EmailIngestRead:
    result = value if isinstance(value, EmailIngestRead) else EmailIngestRead.model_validate(value)
    return result.model_copy(
        update={
            "thread": mask_klm_thread(result.thread),
            "message": mask_klm_message(result.message),
        }
    )


def mask_meta_conversation(value: MetaSocialConversationRead | Any) -> MetaSocialConversationRead:
    conversation = (
        value
        if isinstance(value, MetaSocialConversationRead)
        else MetaSocialConversationRead.model_validate(value)
    )
    messages = [
        message.model_copy(
            update={
                "body": redact_text_identifiers(message.body) or "",
                "author_name": masked_lead_label(conversation.id)
                if message.direction.value == "inbound"
                else message.author_name,
                "message_metadata": redact_sensitive_metadata(message.message_metadata),
            }
        )
        for message in conversation.messages
    ]
    return conversation.model_copy(
        update={
            "external_thread_id": f"hidden:{str(conversation.id)[-6:]}",
            "external_participant_id": None,
            "participant_name": masked_lead_label(conversation.id),
            "participant_handle": None,
            "participant_avatar_url": None,
            "source_metadata": redact_sensitive_metadata(conversation.source_metadata),
            "last_message_preview": redact_text_identifiers(conversation.last_message_preview),
            "messages": messages,
            "sensitive_data_hidden": True,
        }
    )


def mask_meta_comment(value: MetaCommentThreadRead | Any) -> MetaCommentThreadRead:
    item = (
        value
        if isinstance(value, MetaCommentThreadRead)
        else MetaCommentThreadRead.model_validate(value)
    )
    return item.model_copy(
        update={
            "participant_name": masked_lead_label(item.conversation_id),
            "participant_handle": None,
            "last_message_preview": redact_text_identifiers(item.last_message_preview),
            "sensitive_data_hidden": True,
        }
    )


def mask_meta_drawer(value: MetaLeadDrawerRead | Any) -> MetaLeadDrawerRead:
    drawer = (
        value if isinstance(value, MetaLeadDrawerRead) else MetaLeadDrawerRead.model_validate(value)
    )
    return drawer.model_copy(
        update={
            "conversation": mask_meta_conversation(drawer.conversation),
            "matter": mask_matter(drawer.matter) if drawer.matter else None,
            "notes": [mask_meta_note(item) for item in drawer.notes],
            "recent_meetings": [mask_meeting(item) for item in drawer.recent_meetings],
        }
    )


def mask_meta_note(value: MetaConversationNoteRead | Any) -> MetaConversationNoteRead:
    note = (
        value
        if isinstance(value, MetaConversationNoteRead)
        else MetaConversationNoteRead.model_validate(value)
    )
    return note.model_copy(
        update={
            "body": "[conteúdo protegido pelo administrador]",
            "mentions": [],
            "note_metadata": {"sensitiveDataHidden": True},
        }
    )


def mask_meeting(value: MeetingRead | Any) -> MeetingRead:
    meeting = value if isinstance(value, MeetingRead) else MeetingRead.model_validate(value)
    return meeting.model_copy(
        update={
            "title": f"Reunião do {masked_lead_label(meeting.matter_id or meeting.id)}",
            "location": "Online" if meeting.location or meeting.meeting_url else None,
            "meeting_url": None,
            "notes": "[conteúdo protegido]" if meeting.notes else None,
            "recipient_overrides": [],
            "google_calendar_event_id": None,
        }
    )


def mask_finance_reminder(value: FinanceReminderRead | Any) -> FinanceReminderRead:
    reminder = (
        value
        if isinstance(value, FinanceReminderRead)
        else FinanceReminderRead.model_validate(value)
    )
    return reminder.model_copy(
        update={"message": "[conteúdo protegido]" if reminder.message else None}
    )


def mask_finance_entry(value: FinanceEntryRead | Any) -> FinanceEntryRead:
    entry = value if isinstance(value, FinanceEntryRead) else FinanceEntryRead.model_validate(value)
    return entry.model_copy(
        update={
            "title": f"Lançamento do {masked_lead_label(entry.matter_id or entry.id)}",
            "description": None,
            "external_ref": None,
            "entry_metadata": redact_sensitive_metadata(entry.entry_metadata),
            "reminders": [mask_finance_reminder(item) for item in entry.reminders],
        }
    )


def mask_finance_overview(value: FinanceOverviewRead | Any) -> FinanceOverviewRead:
    overview = (
        value
        if isinstance(value, FinanceOverviewRead)
        else FinanceOverviewRead.model_validate(value)
    )
    return overview.model_copy(
        update={
            "upcoming_entries": [mask_finance_entry(item) for item in overview.upcoming_entries],
            "reminders": [mask_finance_reminder(item) for item in overview.reminders],
            "highlights": ["Identidade dos leads protegida pelo administrador."],
        }
    )


def mask_campaign_recipient(value: CampaignRecipientRead | Any) -> CampaignRecipientRead:
    recipient = (
        value
        if isinstance(value, CampaignRecipientRead)
        else CampaignRecipientRead.model_validate(value)
    )
    return recipient.model_copy(
        update={
            "phone": "Oculto pelo administrador",
            "name": masked_lead_label(recipient.contact_id or recipient.matter_id or recipient.id),
            "wamid": None,
            "last_error": redact_text_identifiers(recipient.last_error),
            "send_context": redact_sensitive_metadata(recipient.send_context),
        }
    )


def mask_campaign_audience(value: CampaignAudienceRead | Any) -> CampaignAudienceRead:
    audience = (
        value
        if isinstance(value, CampaignAudienceRead)
        else CampaignAudienceRead.model_validate(value)
    )
    return audience.model_copy(
        update={
            "filters": redact_sensitive_metadata(audience.filters),
            "sample_recipients": [
                {"sensitiveDataHidden": True} for _ in audience.sample_recipients
            ],
        }
    )


def mask_campaign(value: CampaignRead | Any) -> CampaignRead:
    campaign = value if isinstance(value, CampaignRead) else CampaignRead.model_validate(value)
    return campaign.model_copy(
        update={
            "audience": mask_campaign_audience(campaign.audience) if campaign.audience else None,
            "sample_recipients": [
                mask_campaign_recipient(item) for item in campaign.sample_recipients
            ],
        }
    )


def mask_opt_in(value: OptInRecordRead | Any) -> OptInRecordRead:
    record = value if isinstance(value, OptInRecordRead) else OptInRecordRead.model_validate(value)
    return record.model_copy(
        update={
            "phone": "Oculto pelo administrador",
            "details": redact_sensitive_metadata(record.details),
        }
    )


def mask_suppression(value: SuppressionEntryRead | Any) -> SuppressionEntryRead:
    record = (
        value
        if isinstance(value, SuppressionEntryRead)
        else SuppressionEntryRead.model_validate(value)
    )
    return record.model_copy(
        update={
            "phone": "Oculto pelo administrador",
            "details": redact_sensitive_metadata(record.details),
        }
    )


def mask_flow_run(value: FlowRunRead | Any) -> FlowRunRead:
    run = value if isinstance(value, FlowRunRead) else FlowRunRead.model_validate(value)
    return run.model_copy(
        update={"context": {"sensitiveDataHidden": True} if run.context else None}
    )


def mask_scheduled_message(value: ScheduledMessageRead | Any) -> ScheduledMessageRead:
    message = (
        value
        if isinstance(value, ScheduledMessageRead)
        else ScheduledMessageRead.model_validate(value)
    )
    return message.model_copy(
        update={
            "contact_phone": "Oculto pelo administrador",
            "message": "[conteúdo protegido pelo administrador]",
        }
    )


def mask_notification(value: NotificationRead | Any) -> NotificationRead:
    notification = (
        value if isinstance(value, NotificationRead) else NotificationRead.model_validate(value)
    )
    if notification.matter_id:
        return notification.model_copy(
            update={
                "title": f"Atualização do {masked_lead_label(notification.matter_id)}",
                "message": "Os dados de contato deste lead estão ocultos.",
            }
        )
    return notification.model_copy(
        update={
            "title": redact_text_identifiers(notification.title) or "Notificação",
            "message": redact_text_identifiers(notification.message) or "",
        }
    )


def mask_sdr_status(value: SDRStatusResponse | Any) -> SDRStatusResponse:
    response = (
        value if isinstance(value, SDRStatusResponse) else SDRStatusResponse.model_validate(value)
    )
    if response.qualification is None:
        return response
    qualification = response.qualification.model_copy(
        update={
            "client_name": masked_lead_label(response.matter_id),
            "phone": None,
            "employer": None,
            "viability_notes": "[conteúdo protegido]"
            if response.qualification.viability_notes
            else None,
            "handoff_notes": "[conteúdo protegido]"
            if response.qualification.handoff_notes
            else None,
            "conversation_summary": "[conteúdo protegido]"
            if response.qualification.conversation_summary
            else None,
        }
    )
    return response.model_copy(update={"qualification": qualification})


def mask_follow_up(value: FollowUpRead | Any) -> FollowUpRead:
    follow_up = value if isinstance(value, FollowUpRead) else FollowUpRead.model_validate(value)
    return follow_up.model_copy(
        update={
            "reason": redact_text_identifiers(follow_up.reason),
            "last_error": redact_text_identifiers(follow_up.last_error),
            "payload": {"sensitiveDataHidden": True} if follow_up.payload else {},
        }
    )


def mask_acquisition_event(value: AcquisitionEventRead | Any) -> AcquisitionEventRead:
    event = (
        value
        if isinstance(value, AcquisitionEventRead)
        else AcquisitionEventRead.model_validate(value)
    )
    return event.model_copy(
        update={
            "click_ids": {key: "[oculto]" for key in event.click_ids},
            "event_metadata": {"sensitiveDataHidden": True} if event.event_metadata else {},
        }
    )


def mask_audit_event(value: AuditEventRead | Any) -> AuditEventRead:
    event = value if isinstance(value, AuditEventRead) else AuditEventRead.model_validate(value)
    # Audit summaries are free text produced by many domains (tasks, finance,
    # meetings, integrations, and more).  Classifying only a short list of
    # entity types is fail-open: a task summary can contain the client's name
    # even though ``entity_type`` is merely ``task``.  A user whose
    # ``lead_data:view`` was explicitly denied may still inspect the event
    # metadata, but never its free-text payload.
    return event.model_copy(
        update={
            "summary": f"Evento protegido {str(event.id)[-6:].upper()}",
            "before_data": {"sensitiveDataHidden": True} if event.before_data else None,
            "after_data": {"sensitiveDataHidden": True} if event.after_data else None,
        }
    )


def mask_external_intent(value: ExternalActionIntentRead | Any) -> ExternalActionIntentRead:
    intent = (
        value
        if isinstance(value, ExternalActionIntentRead)
        else ExternalActionIntentRead.model_validate(value)
    )
    return intent.model_copy(
        update={"payload": {"sensitiveDataHidden": True} if intent.payload else {}}
    )


def mask_legal_artifact(
    value: LegalArtifactVersionRead | Any,
) -> LegalArtifactVersionRead:
    artifact = (
        value
        if isinstance(value, LegalArtifactVersionRead)
        else LegalArtifactVersionRead.model_validate(value)
    )
    return artifact.model_copy(
        update={
            "title": f"Documento jurídico protegido · v{artifact.version}",
            "content": {"sensitiveDataHidden": True},
            "source_snapshot": {"sensitiveDataHidden": True},
            "sensitive_data_hidden": True,
        }
    )


def mask_process_monitoring(
    value: ProcessMonitoringRead | Any,
) -> ProcessMonitoringRead:
    record = (
        value
        if isinstance(value, ProcessMonitoringRead)
        else ProcessMonitoringRead.model_validate(value)
    )
    return record.model_copy(
        update={
            "case_number": "Oculto pelo administrador",
            "court": "Dados processuais protegidos",
            "last_event_summary": "[conteúdo protegido]" if record.last_event_summary else None,
            "events": [],
            "sensitive_data_hidden": True,
        }
    )


def redact_sensitive_metadata(value: Any, sensitive_names: tuple[str | None, ...] = ()) -> Any:
    if isinstance(value, list):
        return [redact_sensitive_metadata(item, sensitive_names) for item in value]
    if not isinstance(value, dict):
        if not isinstance(value, str):
            return value
        redacted = redact_text_identifiers(value) or ""
        candidates = {
            candidate.strip()
            # Tarefa ou caso sem cliente chega com rótulo None: não há nome a ocultar,
            # e `None.split()` derrubava a listagem inteira com 500 (V37).
            for name in sensitive_names
            if isinstance(name, str) and name.strip()
            for candidate in (name, *name.split())
            if len(candidate.strip()) >= 3
        }
        for candidate in sorted(candidates, key=len, reverse=True):
            redacted = re.sub(re.escape(candidate), "[nome oculto]", redacted, flags=re.IGNORECASE)
        return redacted

    redacted: dict[str, Any] = {}
    for key, item in value.items():
        normalized = key.replace("_", "").lower()
        if normalized in {
            "email",
            "emails",
            "senderemail",
            "phone",
            "phonenumber",
            "cpf",
            "clientname",
            "contactname",
            "fullname",
            "name",
            "sendername",
            "participantname",
            "participanthandle",
            "externalparticipantid",
            "avatarurl",
        }:
            redacted[key] = "[oculto]"
        else:
            redacted[key] = redact_sensitive_metadata(item, sensitive_names)
    return redacted
