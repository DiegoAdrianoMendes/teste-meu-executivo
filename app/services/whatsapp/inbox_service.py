from __future__ import annotations

import base64
import hashlib
import io
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1.entitlement_errors import entitlement_http_exception
from app.core.klm_runtime import aws_external_access_allowed
from app.core.logging import logger
from app.models.document import Document, DocumentType
from app.models.entitlement import EntitlementKey
from app.models.klm_operations import (
    ExternalActionIntent,
    ExternalActionIntentStatus,
    KlmAuditEvent,
)
from app.models.matter import KanbanColumnId, Matter, MicroStatus
from app.models.storage_object import StorageAVStatus, StorageObjectState
from app.models.timeline_event import TimelineEvent, TimelineEventType
from app.models.user import User
from app.models.whatsapp import WaMessageStatus, WaQueueStatus, WhatsAppContact
from app.models.workspace import Workspace
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.schemas.control_plane import MeetingCreate
from app.schemas.whatsapp import (
    ApplyFlowRequest,
    ConversationAssigneeRead,
    ConversationAssignRequest,
    ConversationDocumentSendRequest,
    ConversationHandoffRequest,
    ConversationRead,
    MessageTemplateRead,
    ScheduleConversationMeetingRequest,
    ScheduleConversationMessageRequest,
    SendContractRequest,
    SendDocumentRequest,
    SendMessageRequest,
    SendProposalRequest,
)
from app.schemas.zapsign import ZapSignSigner
from app.services.conversation_ownership_service import (
    human_release,
    human_takeover,
    resolve_contact_thread,
)
from app.services.document_service import DocumentService
from app.services.entitlement_service import EntitlementEnforcementService, EntitlementError
from app.services.flow_service import FlowService
from app.services.meeting_service import MeetingService
from app.services.message_template_service import MessageTemplateService
from app.services.whatsapp.errors import WhatsAppProviderError
from app.services.whatsapp.message_service import WhatsAppMessageService
from app.services.zapsign_service import get_zapsign_service


@dataclass(frozen=True)
class GeneratedDocumentUploadResult:
    download_url: str | None
    delivery_state: str
    delivery_created: bool = False


class WhatsAppInboxService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        message_service: WhatsAppMessageService,
        template_service: MessageTemplateService,
    ) -> None:
        self.db = db
        self.message_service = message_service
        self.template_service = template_service
        self.flow_service = FlowService(db)
        self.meeting_service = MeetingService(db)

    async def list_assignable_users(self, workspace_id: UUID) -> list[ConversationAssigneeRead]:
        result = await self.db.execute(
            select(WorkspaceMembership)
            .join(User, User.id == WorkspaceMembership.user_id)
            .options(selectinload(WorkspaceMembership.user))
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.status == MembershipStatus.active,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
            .order_by(WorkspaceMembership.created_at.asc())
        )
        items: list[ConversationAssigneeRead] = []
        for membership in result.scalars().all():
            if membership.user is None:
                continue
            items.append(
                ConversationAssigneeRead(
                    id=membership.user_id,
                    name=membership.user.name,
                    email=membership.user.email,
                    phone=membership.user.phone,
                    role=membership.role.value,
                )
            )
        return items

    async def assign_conversation(
        self,
        contact_id: UUID,
        workspace_id: UUID,
        payload: ConversationAssignRequest,
        acting_user_id: UUID,
    ) -> ConversationRead:
        contact = await self._get_contact(contact_id, workspace_id)
        matter = await self._get_contact_matter(contact, workspace_id)
        if payload.user_id is not None:
            await self._require_active_workspace_member(workspace_id, payload.user_id)
        contact.assigned_user_id = payload.user_id
        if payload.user_id:
            contact.queue_status = WaQueueStatus.human
            await self._take_over_contact_thread(
                contact,
                human_user_id=payload.user_id,
                acting_user_id=acting_user_id,
                reason="inbox_assign",
            )
        if matter:
            matter.responsible_human_id = payload.user_id
            matter.next_action = "Responsável atualizado a partir do inbox."
            self._append_timeline(
                matter_id=matter.id,
                agent_id=acting_user_id,
                title="Responsável do atendimento atualizado",
                description="O responsável do chat foi alterado pelo inbox operacional.",
                event_data={"assigned_user_id": str(payload.user_id) if payload.user_id else None},
            )
        await self.db.flush()
        return await self.message_service.get_conversation(contact_id, workspace_id)

    async def _actor_label(self, acting_user_id: UUID) -> str:
        user = await self.db.get(User, acting_user_id)
        return (user.name if user is not None and user.name else None) or "Inbox operacional"

    async def _take_over_contact_thread(
        self,
        contact: WhatsAppContact,
        *,
        human_user_id: UUID,
        acting_user_id: UUID,
        reason: str,
    ) -> None:
        """S1: assumir a conversa no inbox também toma a thread canônica do contato
        (é ela que o autorizador de agentes consulta). Best-effort: a atribuição
        do contato nunca falha por causa da thread."""
        try:
            thread = await resolve_contact_thread(
                self.db, workspace_id=contact.workspace_id, contact_id=contact.id
            )
            if thread is None or thread.status.value in ("closed", "blocked"):
                return
            await human_takeover(
                self.db,
                workspace_id=contact.workspace_id,
                thread_id=thread.id,
                human_user_id=human_user_id,
                actor_label=await self._actor_label(acting_user_id),
                actor_type="human",
                reason=reason,
            )
        except Exception as exc:  # noqa: BLE001 - best-effort, registrado
            logger.warning(
                "inbox.thread_takeover_failed",
                contact_id=str(contact.id),
                error_type=type(exc).__name__,
            )

    async def _release_contact_thread(
        self, contact: WhatsAppContact, *, acting_user_id: UUID, reason: str
    ) -> None:
        try:
            thread = await resolve_contact_thread(
                self.db, workspace_id=contact.workspace_id, contact_id=contact.id
            )
            if thread is None:
                return
            await human_release(
                self.db,
                workspace_id=contact.workspace_id,
                thread_id=thread.id,
                actor_label=await self._actor_label(acting_user_id),
                reason=reason,
            )
        except Exception as exc:  # noqa: BLE001 - best-effort, registrado
            logger.warning(
                "inbox.thread_release_failed",
                contact_id=str(contact.id),
                error_type=type(exc).__name__,
            )

    async def claim_conversation(
        self,
        contact_id: UUID,
        workspace_id: UUID,
        acting_user_id: UUID,
    ) -> ConversationRead:
        return await self.assign_conversation(
            contact_id,
            workspace_id,
            ConversationAssignRequest(user_id=acting_user_id),
            acting_user_id,
        )

    async def handoff_conversation(
        self,
        contact_id: UUID,
        workspace_id: UUID,
        payload: ConversationHandoffRequest,
        acting_user_id: UUID,
    ) -> ConversationRead:
        contact = await self._get_contact(contact_id, workspace_id)
        matter = await self._get_contact_matter(contact, workspace_id)
        contact.queue_status = payload.queue_status
        contact.human_control_state = payload.human_control_state
        if payload.queue_status == WaQueueStatus.ai and not contact.assigned_user_id:
            contact.assigned_user_id = acting_user_id
        if payload.queue_status == WaQueueStatus.human:
            await self._take_over_contact_thread(
                contact,
                human_user_id=contact.assigned_user_id or acting_user_id,
                acting_user_id=acting_user_id,
                reason="inbox_handoff",
            )
        elif payload.queue_status == WaQueueStatus.ai:
            await self._release_contact_thread(
                contact, acting_user_id=acting_user_id, reason="inbox_handoff_ai"
            )
        if matter:
            matter.next_action = {
                WaQueueStatus.ai: "IA retomou o acompanhamento do atendimento.",
                WaQueueStatus.human: "Atendimento humano assumiu o caso.",
                WaQueueStatus.follow_up: "Caso movido para fluxo de follow-up.",
                WaQueueStatus.closed: "Conversa encerrada pelo time.",
            }[payload.queue_status]
            self._append_timeline(
                matter_id=matter.id,
                agent_id=acting_user_id,
                title="Modo de atendimento atualizado",
                description=matter.next_action,
                event_data={
                    "queue_status": payload.queue_status.value,
                    "human_control_state": payload.human_control_state.value,
                },
            )
        await self.db.flush()
        return await self.message_service.get_conversation(contact_id, workspace_id)

    async def apply_flow(
        self,
        contact_id: UUID,
        workspace_id: UUID,
        payload: ApplyFlowRequest,
        acting_user_id: UUID,
    ) -> ConversationRead:
        contact = await self._get_contact(contact_id, workspace_id)
        matter = await self._get_contact_matter(contact, workspace_id)
        template = await self.flow_service.get_template(
            payload.template_id,
            workspace_id,
            commit_backfill=False,
        )
        if not template:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WA-FLOW-001", "message": "Template de flow não encontrado."},
            )

        run = None
        try:
            contact.active_flow_template_id = str(template.id)
            contact.active_flow_label = template.name
            if payload.move_to_follow_up:
                contact.queue_status = WaQueueStatus.follow_up
            if payload.next_follow_up_at:
                contact.next_follow_up_at = payload.next_follow_up_at

            if matter:
                new_flow = await self.flow_service.apply_template(
                    template.id,
                    workspace_id,
                    payload.custom_name or f"{template.name} · {contact.name or contact.phone}",
                    actor_user_id=acting_user_id,
                    activate=True,
                    commit=False,
                )
                run = await self.flow_service.start_flow_run(
                    new_flow.id,
                    matter.id,
                    workspace_id,
                    context={"source": "whatsapp_inbox", "contact_id": str(contact.id)},
                    actor_user_id=acting_user_id,
                    commit=False,
                    publish=False,
                )
                matter.next_action = f"Flow aplicado via inbox: {template.name}."
                self._append_timeline(
                    matter_id=matter.id,
                    agent_id=acting_user_id,
                    title="Flow aplicado no atendimento",
                    description=f"O template '{template.name}' foi aplicado à conversa.",
                    event_data={"template_id": str(template.id), "flow_id": str(new_flow.id)},
                )

            await self.db.flush()
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise

        if run is not None:
            await self.db.refresh(run)
            self.flow_service.publish_initial_flow_run(run, workspace_id)
        return await self.message_service.get_conversation(contact_id, workspace_id)

    async def schedule_message(
        self,
        contact_id: UUID,
        workspace_id: UUID,
        payload: ScheduleConversationMessageRequest,
        acting_user_id: UUID,
    ) -> ConversationRead:
        contact = await self._get_contact(contact_id, workspace_id)
        await self.flow_service.schedule_message(
            matter_id=contact.matter_id,
            workspace_id=workspace_id,
            contact_phone=contact.phone,
            message=payload.body,
            scheduled_at=payload.scheduled_at,
        )
        contact.queue_status = WaQueueStatus.follow_up
        contact.next_follow_up_at = payload.scheduled_at
        if contact.matter_id:
            self._append_timeline(
                matter_id=contact.matter_id,
                agent_id=acting_user_id,
                title="Mensagem agendada",
                description=f"Mensagem agendada para {payload.scheduled_at.isoformat()}.",
                event_data={"scheduled_at": payload.scheduled_at.isoformat()},
            )
        await self.db.flush()
        return await self.message_service.get_conversation(contact_id, workspace_id)

    async def schedule_meeting(
        self,
        contact_id: UUID,
        workspace_id: UUID,
        payload: ScheduleConversationMeetingRequest,
        acting_user_id: UUID,
    ) -> ConversationRead:
        contact = await self._get_contact(contact_id, workspace_id)
        meeting = await self.meeting_service.create_for_workspace(
            workspace_id,
            MeetingCreate(
                matter_id=contact.matter_id,
                responsible_user_id=payload.responsible_user_id or contact.assigned_user_id,
                title=payload.title,
                starts_at=payload.starts_at,
                ends_at=payload.ends_at,
                location=payload.location,
                meeting_url=payload.meeting_url,
                notes=payload.notes,
                recipient_overrides=[],
                google_calendar_event_id=None,
            ),
            actor_user_id=acting_user_id,
        )
        if contact.matter_id:
            self._append_timeline(
                matter_id=contact.matter_id,
                agent_id=acting_user_id,
                title="Reunião criada a partir do chat",
                description=f"Reunião '{payload.title}' agendada para {payload.starts_at.isoformat()}.",
                event_data={"meeting_id": str(meeting.id)},
            )
        await self.db.flush()
        return await self.message_service.get_conversation(contact_id, workspace_id)

    async def send_media(
        self,
        contact_id: UUID,
        workspace_id: UUID,
        payload: ConversationDocumentSendRequest,
        acting_user_id: UUID,
        idempotency_key: str,
    ) -> ConversationRead:
        contact = await self._get_contact(contact_id, workspace_id)
        if payload.document_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "WA-DOC-004",
                    "message": (
                        "O envio de mídia exige um documento versionado e autorizado."
                    ),
                },
            )
        document = await self.db.scalar(
            select(Document)
            .join(Matter, Matter.id == Document.matter_id)
            .where(
                Document.id == payload.document_id,
                Matter.id == contact.matter_id,
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
            )
        )
        if not document:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WA-DOC-001", "message": "Documento não encontrado para este atendimento."},
            )
        try:
            url, storage_object = (
                await DocumentService(self.db).create_media_delivery_authorization(
                    document.id,
                    workspace_id,
                )
            )
        except EntitlementError as exc:
            raise entitlement_http_exception(exc) from exc
        filename = document.name
        if not url or not filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "WA-DOC-002", "message": "Informe um documento disponível ou uma URL temporária válida."},
            )
        delivery = await self.message_service.send_storage_document(
            SendDocumentRequest(
                contact_id=contact.id,
                connection_id=payload.connection_id,
                url=url,
                filename=filename,
                caption=payload.caption,
                idempotency_key=idempotency_key,
            ),
            acting_user_id,
            workspace_id,
            storage_object_version_id=storage_object.id,
            storage_key=storage_object.storage_key,
        )
        if contact.matter_id:
            event_title = "Mídia enviada pelo chat"
            event_description = f"O arquivo '{filename}' foi enviado ao cliente via WhatsApp."
            if delivery.status is WaMessageStatus.pending:
                event_title = "Mídia enfileirada pelo chat"
                event_description = (
                    f"O arquivo '{filename}' aguarda a abertura do canal externo."
                )
            elif delivery.status is WaMessageStatus.delivery_uncertain:
                event_title = "Entrega de mídia aguardando reconciliação"
                event_description = (
                    f"O resultado do envio de '{filename}' não foi conclusivo."
                )
            self._append_timeline(
                matter_id=contact.matter_id,
                agent_id=acting_user_id,
                title=event_title,
                description=event_description,
                event_data={
                    "filename": filename,
                    "delivery": "purpose_bound_ticket",
                    "delivery_status": delivery.status.value,
                },
            )
        await self.db.flush()
        return await self.message_service.get_conversation(contact_id, workspace_id)

    async def send_proposal(
        self,
        contact_id: UUID,
        workspace_id: UUID,
        payload: SendProposalRequest,
        acting_user_id: UUID,
        idempotency_key: str,
    ) -> ConversationRead:
        contact = await self._get_contact(contact_id, workspace_id)
        workspace, matter = await self._get_workspace_and_matter(contact, workspace_id)
        template = await self.template_service.get_template_by_id(workspace, payload.template_id)
        if not template or template.kind != "proposal":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WA-PRO-001", "message": "Template de proposta não encontrado."},
            )

        render_context = await self._build_template_context(workspace, contact, matter)
        rendered_body = self.template_service.render_template(template.body, render_context)
        format_kind = payload.format or template.format or "text"
        generated = GeneratedDocumentUploadResult(None, "not_applicable")
        if format_kind == "pdf":
            pdf_bytes = self._render_simple_pdf(template.title or "Proposta comercial", rendered_body, template.footer)
            filename = self._safe_filename((template.title or "proposta-comercial") + ".pdf")
            generated = await self._upload_generated_document(
                workspace_id,
                matter.id if matter else None,
                filename,
                pdf_bytes,
                acting_user_id,
                doc_type=DocumentType.outro,
                mime_type="application/pdf",
                contact_id=contact.id,
                connection_id=payload.connection_id,
                caption=payload.custom_message or "Segue a proposta comercial em PDF.",
                delivery_idempotency_key=idempotency_key,
                delivery_kind="proposal",
            )
            if generated.download_url is not None:
                await self.message_service.send_document(
                    SendDocumentRequest(
                        contact_id=contact.id,
                        connection_id=payload.connection_id,
                        url=generated.download_url,
                        filename=filename,
                        caption=payload.custom_message or "Segue a proposta comercial em PDF.",
                        idempotency_key=(
                            "proposal:"
                            + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
                        ),
                    ),
                    acting_user_id,
                    workspace_id,
                )
        else:
            await self.message_service.send_message(
                payload=SendMessageRequest(
                    contact_id=contact.id,
                    connection_id=payload.connection_id,
                    body=payload.custom_message or rendered_body,
                    idempotency_key=(
                        "proposal-text:"
                        + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
                    ),
                ),
                sent_by_id=acting_user_id,
                workspace_id=workspace_id,
            )

        if matter and (
            format_kind != "pdf"
            or generated.download_url is not None
        ):
            matter.column_id = KanbanColumnId.proposta_fechamento
            matter.micro_status = MicroStatus.proposta_enviada
            matter.next_action = "Aguardar retorno sobre a proposta enviada."
            self._append_timeline(
                matter_id=matter.id,
                agent_id=acting_user_id,
                title="Proposta enviada pelo inbox",
                description="A proposta comercial foi enviada ao cliente pelo WhatsApp.",
                event_data={"template_id": template.id, "format": format_kind},
            )
        elif matter and generated.delivery_created:
            matter.next_action = "Aguardar AV clean e envio assíncrono da proposta."
            self._append_timeline(
                matter_id=matter.id,
                agent_id=acting_user_id,
                title="Proposta gerada e enfileirada",
                description="O PDF aguarda AV clean antes do envio pelo WhatsApp.",
                event_data={"template_id": template.id, "format": format_kind},
            )
        await self.db.flush()
        return await self.message_service.get_conversation(contact_id, workspace_id)

    async def send_contract(
        self,
        contact_id: UUID,
        workspace_id: UUID,
        payload: SendContractRequest,
        acting_user_id: UUID,
        idempotency_key: str,
    ) -> ConversationRead:
        try:
            await EntitlementEnforcementService(self.db).enforce_feature(
                workspace_id=workspace_id,
                entitlement=EntitlementKey.digital_signature,
            )
        except EntitlementError as exc:
            raise entitlement_http_exception(exc) from exc

        contact = await self._get_contact(contact_id, workspace_id)
        workspace, matter = await self._get_workspace_and_matter(contact, workspace_id)
        if not matter:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "WA-CON-001", "message": "O contrato depende de um card/matter vinculado à conversa."},
            )
        template = await self.template_service.get_template_by_id(workspace, payload.template_id)
        if not template or template.kind != "contract":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WA-CON-002", "message": "Template de contrato não encontrado."},
            )

        render_context = await self._build_template_context(workspace, contact, matter)
        rendered_contract = self.template_service.render_template(template.body, render_context)
        contract_pdf = self._render_simple_pdf(template.title or "Contrato", rendered_contract, template.footer)
        zapsign = await get_zapsign_service(workspace_id=workspace_id, db=self.db)
        if not matter.client:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "WA-CON-003", "message": "O card precisa de cliente vinculado para gerar contrato."},
            )
        signer = ZapSignSigner(
            name=matter.client.name,
            email=matter.client.email,
            phone_number=(matter.client.phone or contact.phone or "").replace("+", "").replace(" ", ""),
            auth_mode=payload.auth_mode or "assinaturaTela-tokenEmail",
            send_automatic_email=bool(matter.client.email),
        )
        intent = await self._prepare_contract_delivery_intent(
            workspace_id=workspace_id,
            matter_id=matter.id,
            contact_id=contact.id,
            acting_user_id=acting_user_id,
            idempotency_key=idempotency_key,
            template_id=template.id,
            contract_sha256=hashlib.sha256(contract_pdf).hexdigest(),
            connection_id=payload.connection_id,
            auth_mode=signer.auth_mode,
            custom_message=payload.custom_message,
        )
        intent_payload = dict(intent.payload or {})
        intent_state = str(intent_payload.get("state") or "prepared")
        if intent_state == "sent":
            return await self.message_service.get_conversation(contact_id, workspace_id)
        if intent_state in {"provider_call_started", "delivery_uncertain"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-CON-DELIVERY-UNCERTAIN",
                    "message": (
                        "A criação do contrato pode ter sido aceita pelo provedor. "
                        "Reconcilie o intento antes de tentar novamente."
                    ),
                },
            )
        if intent_state == "provider_failed":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-CON-PROVIDER-FAILED",
                    "message": "A tentativa anterior foi recusada pelo provedor.",
                },
            )

        document_token = str(intent_payload.get("documentToken") or "")
        sign_url = str(intent_payload.get("signUrl") or "")
        document_status = str(intent_payload.get("providerStatus") or "pending")
        if intent_state == "prepared":
            await self._settle_contract_intent(
                intent,
                state="provider_call_started",
                reason="Requisição ZapSign iniciada; repetição automática bloqueada.",
            )
            try:
                document = await zapsign.create_document(
                    name=template.title or "Contrato",
                    base64_pdf=base64.b64encode(contract_pdf).decode("utf-8"),
                    signers=[signer],
                    external_id=str(intent.id),
                    brand_name=workspace.name,
                )
            except ValueError as exc:
                await self._settle_contract_intent(
                    intent,
                    state="provider_failed",
                    reason="Integração ZapSign indisponível antes do envio.",
                    error_code=type(exc).__name__,
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "WA-CON-PROVIDER-NOT-CONFIGURED",
                        "message": "Integração ZapSign não configurada.",
                    },
                ) from exc
            except httpx.HTTPStatusError as exc:
                response_status = exc.response.status_code
                known_rejection = 400 <= response_status < 500
                await self._settle_contract_intent(
                    intent,
                    state=("provider_failed" if known_rejection else "delivery_uncertain"),
                    reason=(
                        "ZapSign recusou a requisição."
                        if known_rejection
                        else "ZapSign pode ter aceitado a requisição; reconciliação obrigatória."
                    ),
                    error_code=f"http_{response_status}",
                )
                raise HTTPException(
                    status_code=(
                        status.HTTP_422_UNPROCESSABLE_ENTITY
                        if known_rejection
                        else status.HTTP_409_CONFLICT
                    ),
                    detail={
                        "code": (
                            "WA-CON-PROVIDER-REJECTED"
                            if known_rejection
                            else "WA-CON-DELIVERY-UNCERTAIN"
                        ),
                        "message": (
                            "ZapSign recusou a criação do contrato."
                            if known_rejection
                            else "Resultado ZapSign incerto; reconciliação obrigatória."
                        ),
                    },
                ) from exc
            except Exception as exc:
                await self._settle_contract_intent(
                    intent,
                    state="delivery_uncertain",
                    reason="Resultado ZapSign incerto; reconciliação obrigatória.",
                    error_code=type(exc).__name__,
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "WA-CON-DELIVERY-UNCERTAIN",
                        "message": "Resultado ZapSign incerto; reconciliação obrigatória.",
                    },
                ) from exc

            document_token = document.token
            sign_url = document.sign_url or ""
            document_status = document.status
            matter.zapsign_doc_token = document_token
            matter.zapsign_sign_url = sign_url or None
            matter.zapsign_status = document_status
            await self._settle_contract_intent(
                intent,
                state="provider_succeeded",
                reason="ZapSign confirmou a criação do contrato.",
                extra={
                    "documentToken": document_token,
                    "signUrl": sign_url,
                    "providerStatus": document_status,
                },
            )

        if not document_token:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-CON-RECEIPT-MISSING",
                    "message": "O recibo persistido da ZapSign está incompleto.",
                },
            )

        contract_message_template = next(
            (item for item in (await self.template_service.get_settings(workspace)).contract_templates if item.kind == "contract_message"),
            None,
        )
        whatsapp_message = payload.custom_message
        if not whatsapp_message:
            base_message = contract_message_template.body if contract_message_template else "Seu contrato está pronto: {contract_link}"
            whatsapp_message = self.template_service.render_template(
                base_message,
                {**render_context, "contract_link": sign_url},
            )

        try:
            result = await self.message_service.send_message(
                payload=SendMessageRequest(
                    contact_id=contact.id,
                    connection_id=payload.connection_id,
                    body=whatsapp_message,
                    idempotency_key=f"contract-message:{intent.id}",
                ),
                sent_by_id=acting_user_id,
                workspace_id=workspace_id,
            )
        except HTTPException as exc:
            provider_error = exc.__cause__
            uncertain = (
                isinstance(provider_error, WhatsAppProviderError)
                and provider_error.delivery_uncertain
            )
            await self._settle_contract_intent(
                intent,
                state=("delivery_uncertain" if uncertain else "whatsapp_failed"),
                reason=(
                    "WhatsApp pode ter recebido o contrato; reconciliação obrigatória."
                    if uncertain
                    else "WhatsApp recusou a mensagem do contrato."
                ),
                error_code=f"http_{exc.status_code}",
            )
            raise
        except Exception as exc:
            await self._settle_contract_intent(
                intent,
                state="delivery_uncertain",
                reason="Resultado WhatsApp incerto; reconciliação obrigatória.",
                error_code=type(exc).__name__,
            )
            raise

        if result.status.value == "delivery_uncertain":
            await self._settle_contract_intent(
                intent,
                state="delivery_uncertain",
                reason="WhatsApp aceitou o envio sem confirmação conclusiva.",
                extra={"messageId": str(result.messageId)},
            )
            return await self.message_service.get_conversation(contact_id, workspace_id)

        matter.column_id = KanbanColumnId.aprovacao_pendente
        matter.micro_status = MicroStatus.assinatura_pendente
        matter.next_action = "Acompanhar assinatura do contrato enviado."
        current_payload = dict(intent.payload or {})
        if not current_payload.get("businessEventRecorded"):
            self._append_timeline(
                matter_id=matter.id,
                agent_id=acting_user_id,
                title="Contrato enviado pelo inbox",
                description="O contrato foi gerado e enviado para assinatura via fluxo oficial.",
                event_data={
                    "template_id": template.id,
                    "doc_token": document_token,
                    "delivery_intent_id": str(intent.id),
                },
            )
        await self._settle_contract_intent(
            intent,
            state="sent",
            reason="Contrato criado e mensagem enviada com recibos persistidos.",
            extra={
                "messageId": str(result.messageId),
                "businessEventRecorded": True,
            },
        )
        return await self.message_service.get_conversation(contact_id, workspace_id)

    async def _prepare_contract_delivery_intent(
        self,
        *,
        workspace_id: UUID,
        matter_id: UUID,
        contact_id: UUID,
        acting_user_id: UUID,
        idempotency_key: str,
        template_id: str,
        contract_sha256: str,
        connection_id: UUID | None,
        auth_mode: str,
        custom_message: str | None,
    ) -> ExternalActionIntent:
        normalized = idempotency_key.strip()
        if not 8 <= len(normalized) <= 160:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "WA-IDEMPOTENCY-INVALID",
                    "message": "Idempotency-Key deve conter entre 8 e 160 caracteres.",
                },
            )
        stored_key = "contract:" + hashlib.sha256(
            f"{workspace_id}:{normalized}".encode()
        ).hexdigest()
        payload_digest = hashlib.sha256(
            "|".join(
                [
                    str(matter_id),
                    str(contact_id),
                    str(template_id),
                    contract_sha256,
                    str(connection_id or ""),
                    auth_mode,
                    hashlib.sha256((custom_message or "").encode("utf-8")).hexdigest(),
                ]
            ).encode("utf-8")
        ).hexdigest()
        existing = await self.db.scalar(
            select(ExternalActionIntent).where(
                ExternalActionIntent.workspace_id == workspace_id,
                ExternalActionIntent.idempotency_key == stored_key,
            )
        )
        if existing is not None:
            if (
                existing.action_type != "zapsign_contract_delivery"
                or existing.matter_id != matter_id
                or existing.requested_by_user_id != acting_user_id
                or (existing.payload or {}).get("payloadDigest") != payload_digest
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "WA-IDEMPOTENCY-CONFLICT",
                        "message": "Idempotency-Key já pertence a outra operação.",
                    },
                )
            return existing

        intent = ExternalActionIntent(
            workspace_id=workspace_id,
            requested_by_user_id=acting_user_id,
            matter_id=matter_id,
            action_type="zapsign_contract_delivery",
            provider="zapsign+whatsapp",
            idempotency_key=stored_key,
            status=ExternalActionIntentStatus.approved_local,
            payload={
                "schemaVersion": 1,
                "state": "prepared",
                "payloadDigest": payload_digest,
                "contactId": str(contact_id),
                "templateId": str(template_id),
            },
            reason="Criação de contrato autorizada e preparada.",
        )
        try:
            async with self.db.begin_nested():
                self.db.add(intent)
                await self.db.flush()
        except IntegrityError:
            existing = await self.db.scalar(
                select(ExternalActionIntent).where(
                    ExternalActionIntent.workspace_id == workspace_id,
                    ExternalActionIntent.idempotency_key == stored_key,
                )
            )
            if existing is None:
                raise
            if (
                existing.action_type != "zapsign_contract_delivery"
                or existing.matter_id != matter_id
                or existing.requested_by_user_id != acting_user_id
                or (existing.payload or {}).get("payloadDigest") != payload_digest
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "WA-IDEMPOTENCY-CONFLICT",
                        "message": "Idempotency-Key já pertence a outra operação.",
                    },
                )
            return existing
        await self._audit_contract_intent(
            intent,
            event_type="contract_delivery_prepared",
            summary="Intento de contrato persistido antes do efeito externo.",
            before=None,
            after={"state": "prepared"},
        )
        await self.db.commit()
        return intent

    async def _settle_contract_intent(
        self,
        intent: ExternalActionIntent,
        *,
        state: str,
        reason: str,
        error_code: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        previous = str((intent.payload or {}).get("state") or "prepared")
        payload = dict(intent.payload or {})
        payload.update(extra or {})
        payload.update(
            {
                "state": state,
                "lastErrorCode": error_code,
                "updatedAt": datetime.now(UTC).isoformat(),
            }
        )
        intent.payload = payload
        intent.reason = reason
        await self._audit_contract_intent(
            intent,
            event_type=f"contract_delivery_{state}",
            summary=reason,
            before={"state": previous},
            after={"state": state, "errorCode": error_code},
        )
        await self.db.commit()

    async def _audit_contract_intent(
        self,
        intent: ExternalActionIntent,
        *,
        event_type: str,
        summary: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        self.db.add(
            KlmAuditEvent(
                workspace_id=intent.workspace_id,
                actor_user_id=intent.requested_by_user_id,
                actor_type="human",
                actor_label=str(intent.requested_by_user_id),
                event_type=event_type,
                entity_type="external_action_intent",
                entity_id=intent.id,
                summary=summary,
                correlation_id=intent.idempotency_key,
                before_data=before,
                after_data=after,
            )
        )
        await self.db.flush()

    async def list_templates(self, workspace_id: UUID) -> list[MessageTemplateRead]:
        workspace = await self._get_workspace(workspace_id)
        return await self.template_service.list_chat_templates(workspace)

    async def get_template_settings(self, workspace_id: UUID):
        workspace = await self._get_workspace(workspace_id)
        return await self.template_service.get_settings(workspace)

    async def update_template_settings(self, workspace_id: UUID, payload):
        workspace = await self._get_workspace(workspace_id)
        return await self.template_service.update_settings(workspace, payload)

    async def _get_workspace_and_matter(
        self,
        contact: WhatsAppContact,
        workspace_id: UUID,
    ) -> tuple[Workspace, Matter | None]:
        workspace = await self._get_workspace(workspace_id)
        matter = await self._get_contact_matter(
            contact,
            workspace_id,
            load_relationships=True,
        )
        return workspace, matter

    async def _build_template_context(
        self,
        workspace: Workspace,
        contact: WhatsAppContact,
        matter: Matter | None,
    ) -> dict[str, Any]:
        assigned_user: User | None = None
        if contact.assigned_user_id:
            assigned_user = await self.db.scalar(
                select(User)
                .join(WorkspaceMembership, WorkspaceMembership.user_id == User.id)
                .where(
                    User.id == contact.assigned_user_id,
                    User.is_active.is_(True),
                    User.deleted_at.is_(None),
                    WorkspaceMembership.workspace_id == workspace.id,
                    WorkspaceMembership.status == MembershipStatus.active,
                )
            )
        client = matter.client if matter else None
        return {
            "workspace_name": workspace.name,
            "client_name": (client.name if client else None) or contact.name or "Cliente",
            "client_email": (client.email if client else None) or "",
            "client_phone": (client.phone if client else None) or contact.phone or "",
            "client_cpf": (client.cpf if client else None) or "",
            "responsible_name": assigned_user.name if assigned_user else (matter.responsible_human.name if matter and matter.responsible_human else "Equipe"),
            "practice_area": matter.practice_area.value if matter else "",
            "demand_type": matter.demand_type if matter else "",
            "executive_summary": matter.executive_summary if matter else "",
            "next_action": matter.next_action if matter else "",
            "document_list": "documentos pessoais, comprovantes e arquivos relacionados ao caso",
        }

    async def _upload_generated_document(
        self,
        workspace_id: UUID,
        matter_id: UUID | None,
        filename: str,
        payload: bytes,
        acting_user_id: UUID,
        *,
        doc_type: DocumentType,
        mime_type: str,
        contact_id: UUID | None = None,
        connection_id: UUID | None = None,
        caption: str | None = None,
        delivery_idempotency_key: str | None = None,
        delivery_kind: str = "document",
    ) -> GeneratedDocumentUploadResult:
        try:
            storage_idempotency_key = (
                "generated-storage:"
                + hashlib.sha256(
                    delivery_idempotency_key.encode("utf-8")
                ).hexdigest()
                if delivery_idempotency_key
                else None
            )
            _document, stored = await DocumentService(self.db).store_generated_document(
                workspace_id=workspace_id,
                matter_id=matter_id,
                actor_user_id=acting_user_id,
                filename=filename,
                doc_type=doc_type,
                mime_type=mime_type,
                payload=payload,
                idempotency_key=storage_idempotency_key,
            )
            # In AWS, generated documents always cross the same durable delivery
            # intent, including clean replays. This prevents a replay from using
            # an immediate-send key different from the original queued delivery.
            if (
                aws_external_access_allowed()
                and stored.av_status is not StorageAVStatus.infected
                and contact_id is not None
                and delivery_idempotency_key is not None
            ):
                from app.services.generated_document_delivery_service import (
                    GeneratedDocumentDeliveryService,
                )

                delivery_key = (
                    "generated-delivery:"
                    + hashlib.sha256(
                        delivery_idempotency_key.encode("utf-8")
                    ).hexdigest()
                )
                queued = await GeneratedDocumentDeliveryService(
                    self.db
                ).queue_after_clean(
                    workspace_id=workspace_id,
                    actor_user_id=acting_user_id,
                    storage_object=stored,
                    contact_id=contact_id,
                    connection_id=connection_id,
                    filename=filename,
                    caption=caption,
                    idempotency_key=delivery_key,
                    kind=delivery_kind,
                    matter_id=matter_id,
                )
                return GeneratedDocumentUploadResult(
                    download_url=None,
                    delivery_state=str(
                        (queued.intent.payload or {}).get("state") or "awaiting_av"
                    ),
                    delivery_created=queued.created,
                )
            if stored.state is not StorageObjectState.available:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "WA-DOC-003",
                        "message": "Documento gerado foi bloqueado pela triagem de segurança.",
                    },
                )
            authorization = await DocumentService(self.db).create_download_url(
                stored.object_id,
                workspace_id,
                purpose="media_delivery",
                actor_user_id=acting_user_id,
            )
        except EntitlementError as exc:
            raise entitlement_http_exception(exc) from exc
        return GeneratedDocumentUploadResult(
            download_url=authorization.download_url,
            delivery_state="immediate",
        )

    async def _require_active_workspace_member(
        self,
        workspace_id: UUID,
        user_id: UUID,
    ) -> None:
        membership_id = await self.db.scalar(
            select(WorkspaceMembership.id)
            .join(User, User.id == WorkspaceMembership.user_id)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.status == MembershipStatus.active,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
        if membership_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "WA-INBOX-001",
                    "message": "O responsável deve ser um membro ativo deste workspace.",
                },
            )

    async def _get_contact_matter(
        self,
        contact: WhatsAppContact,
        workspace_id: UUID,
        *,
        load_relationships: bool = False,
    ) -> Matter | None:
        if not contact.matter_id:
            return None
        query = select(Matter).where(
            Matter.id == contact.matter_id,
            Matter.workspace_id == workspace_id,
            Matter.deleted_at.is_(None),
        )
        if load_relationships:
            query = query.options(
                selectinload(Matter.client),
                selectinload(Matter.responsible_human),
            )
        matter = await self.db.scalar(query)
        if matter is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "WA-INBOX-002",
                    "message": "O card vinculado à conversa não pertence a este workspace.",
                },
            )
        return matter

    async def _get_contact(self, contact_id: UUID, workspace_id: UUID) -> WhatsAppContact:
        result = await self.db.execute(
            select(WhatsAppContact)
            .options(selectinload(WhatsAppContact.assigned_user))
            .where(WhatsAppContact.id == contact_id, WhatsAppContact.workspace_id == workspace_id)
        )
        contact = result.scalar_one_or_none()
        if not contact:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WA-003", "message": "Contato não encontrado."},
            )
        return contact

    async def _get_workspace(self, workspace_id: UUID) -> Workspace:
        workspace = await self.db.get(Workspace, workspace_id)
        if not workspace:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WKS-001", "message": "Workspace não encontrado."},
            )
        return workspace

    def _append_timeline(
        self,
        *,
        matter_id: UUID,
        agent_id: UUID,
        title: str,
        description: str,
        event_data: dict[str, Any] | None = None,
    ) -> None:
        self.db.add(
            TimelineEvent(
                matter_id=matter_id,
                agent_id=agent_id,
                event_type=TimelineEventType.agent_action,
                title=title,
                description=description,
                event_data=event_data or {},
            )
        )

    @staticmethod
    def _safe_filename(filename: str) -> str:
        return "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in filename).strip("-") or "arquivo.pdf"

    def _render_simple_pdf(self, title: str, body: str, footer: str | None = None) -> bytes:
        lines = [title, "", *body.splitlines()]
        if footer:
            lines += ["", footer]
        return _basic_pdf_from_lines(lines)


def _basic_pdf_from_lines(lines: list[str]) -> bytes:
    text_lines: list[str] = []
    y = 780
    for raw_line in lines:
        line = raw_line.encode("latin-1", "replace").decode("latin-1")
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        text_lines.append(f"1 0 0 1 50 {y} Tm ({escaped}) Tj")
        y -= 18
        if y < 60:
            break
    stream = "BT /F1 12 Tf 14 TL " + " ".join(text_lines) + " ET"
    objects = [
        "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
        "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
        "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj",
        "4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj",
        f"5 0 obj << /Length {len(stream.encode('latin-1'))} >> stream\n{stream}\nendstream endobj",
    ]
    buffer = io.BytesIO()
    buffer.write(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(buffer.tell())
        buffer.write(obj.encode("latin-1"))
        buffer.write(b"\n")
    xref_offset = buffer.tell()
    buffer.write(f"xref\n0 {len(offsets)}\n".encode("latin-1"))
    buffer.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        buffer.write(f"{offset:010d} 00000 n \n".encode("latin-1"))
    buffer.write(
        (
            f"trailer << /Size {len(offsets)} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF"
        ).encode("latin-1")
    )
    return buffer.getvalue()
