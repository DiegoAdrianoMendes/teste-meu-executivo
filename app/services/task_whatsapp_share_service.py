"""Prepare a legal-task share as a local Conversations draft, never a send."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.tenant import set_workspace_context
from app.models.klm_operations import (
    KlmAuditEvent,
    MessageAuthorType,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
    OmnichannelThread,
)
from app.models.omnichannel_v6 import (
    OmnichannelEndpoint,
    OmnichannelIdentity,
    OmnichannelThreadBinding,
)
from app.models.task_external_share import TaskExternalShare
from app.schemas.task_whatsapp_share import (
    TaskWhatsAppContact,
    TaskWhatsAppDraftConfirmRequest,
    TaskWhatsAppDraftPrepared,
    TaskWhatsAppDraftPreview,
    TaskWhatsAppDraftPreviewRequest,
)
from app.services.task_external_share_service import TaskExternalShareService, _as_utc
from app.services.task_service import TaskMutationContext, TaskService


def _now() -> datetime:
    return datetime.now(UTC)


class TaskWhatsAppShareService:
    """Fail-safe adapter: it intentionally does not invoke any dispatcher or outbox."""

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _masked_address(address: str | None) -> str | None:
        digits = "".join(character for character in (address or "") if character.isdigit())
        return f"WhatsApp terminado em {digits[-4:]}" if len(digits) >= 4 else None

    @staticmethod
    def _message_key(workspace_id: UUID, idempotency_key: str) -> str:
        digest = hmac.new(
            settings.SECRET_KEY.encode(),
            f"task-whatsapp-draft:{workspace_id}:{idempotency_key}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return f"task-wa-draft:{digest}"

    @staticmethod
    def _fingerprint(payload: dict) -> str:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hmac.new(
            settings.SECRET_KEY.encode(),
            f"task-whatsapp-preview:{canonical}".encode(),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _contact_state(
        thread: OmnichannelThread,
        identity: OmnichannelIdentity,
        endpoint: OmnichannelEndpoint,
        workspace_id: UUID,
    ) -> TaskWhatsAppContact:
        consent = (endpoint.consent_state or "unknown").strip().lower()
        consent_ok = consent in {"granted", "opted_in", "consented", "active"}
        window_expires_at = (
            _as_utc(thread.last_inbound_at) + timedelta(hours=24)
            if thread.last_inbound_at
            else None
        )
        window_open = bool(window_expires_at and window_expires_at > _now())
        reasons: list[str] = []
        if endpoint.blocked_at is not None or consent in {"blocked", "opted_out", "denied"}:
            reasons.append("contato_bloqueado_ou_sem_consentimento")
        elif not consent_ok:
            reasons.append("consentimento_nao_comprovado")
        if not window_open:
            reasons.append("fora_da_janela_de_24_horas")
        return TaskWhatsAppContact(
            contact_ref=TaskService.encode_filter_reference(
                kind="task-whatsapp-thread", resource_id=thread.id, workspace_id=workspace_id
            ),
            label=identity.display_name,
            masked_address=TaskWhatsAppShareService._masked_address(
                endpoint.address or endpoint.normalized_address
            ),
            consent_state=consent,
            service_window_state="open" if window_open else "closed",
            service_window_expires_at=window_expires_at,
            send_eligible=consent_ok and window_open and endpoint.blocked_at is None,
            gate_reasons=reasons,
        )

    async def _task(self, task_id: UUID, workspace_id: UUID, actor_user_id: UUID):
        await set_workspace_context(self.db, workspace_id)
        task = await TaskService(self.db).get_for_actor(
            task_id, workspace_id, actor_user_id=actor_user_id, allow_workspace=False
        )
        if task is None:
            raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
        return task

    async def _route(
        self, task_id: UUID, workspace_id: UUID, actor_user_id: UUID, contact_ref: str
    ):
        task = await self._task(task_id, workspace_id, actor_user_id)
        thread_id = TaskService.decode_filter_reference(
            contact_ref, expected_kind="task-whatsapp-thread", workspace_id=workspace_id
        )
        context_filter = (
            OmnichannelThread.matter_id == task.matter_id
            if task.matter_id
            else OmnichannelThread.client_id == task.client_id
            if task.client_id
            else None
        )
        if context_filter is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "TASK_WHATSAPP_CONTEXT_REQUIRED",
                    "message": "A tarefa precisa estar vinculada a caso ou cliente.",
                },
            )
        row = (
            await self.db.execute(
                select(
                    OmnichannelThread,
                    OmnichannelIdentity,
                    OmnichannelEndpoint,
                    OmnichannelThreadBinding,
                )
                .join(
                    OmnichannelIdentity,
                    (OmnichannelIdentity.workspace_id == OmnichannelThread.workspace_id)
                    & (OmnichannelIdentity.id == OmnichannelThread.identity_id),
                )
                .join(
                    OmnichannelEndpoint,
                    (OmnichannelEndpoint.workspace_id == OmnichannelThread.workspace_id)
                    & (OmnichannelEndpoint.identity_id == OmnichannelIdentity.id)
                    & (OmnichannelEndpoint.channel == "whatsapp"),
                )
                .outerjoin(
                    OmnichannelThreadBinding,
                    (OmnichannelThreadBinding.workspace_id == OmnichannelThread.workspace_id)
                    & (OmnichannelThreadBinding.thread_id == OmnichannelThread.id)
                    & (OmnichannelThreadBinding.endpoint_id == OmnichannelEndpoint.id)
                    & (OmnichannelThreadBinding.binding_state == "active"),
                )
                .where(
                    OmnichannelThread.workspace_id == workspace_id,
                    OmnichannelThread.id == thread_id,
                    OmnichannelThread.primary_channel == OmnichannelChannel.whatsapp,
                    context_filter,
                )
                .order_by(OmnichannelEndpoint.verified_at.desc().nullslast())
                .limit(1)
            )
        ).first()
        if row is None:
            raise HTTPException(
                status_code=404, detail="Contato WhatsApp não encontrado para esta tarefa."
            )
        return task, *row

    async def list_contacts(
        self, task_id: UUID, workspace_id: UUID, *, actor_user_id: UUID
    ) -> list[TaskWhatsAppContact]:
        task = await self._task(task_id, workspace_id, actor_user_id)
        filters = []
        if task.matter_id:
            filters.append(OmnichannelThread.matter_id == task.matter_id)
        if task.client_id:
            filters.append(OmnichannelThread.client_id == task.client_id)
        if not filters:
            return []
        rows = (
            await self.db.execute(
                select(OmnichannelThread, OmnichannelIdentity, OmnichannelEndpoint)
                .join(
                    OmnichannelIdentity,
                    (OmnichannelIdentity.workspace_id == OmnichannelThread.workspace_id)
                    & (OmnichannelIdentity.id == OmnichannelThread.identity_id),
                )
                .join(
                    OmnichannelEndpoint,
                    (OmnichannelEndpoint.workspace_id == OmnichannelThread.workspace_id)
                    & (OmnichannelEndpoint.identity_id == OmnichannelIdentity.id)
                    & (OmnichannelEndpoint.channel == "whatsapp"),
                )
                .where(
                    OmnichannelThread.workspace_id == workspace_id,
                    OmnichannelThread.primary_channel == OmnichannelChannel.whatsapp,
                    or_(*filters),
                )
                .order_by(OmnichannelThread.last_message_at.desc().nullslast())
            )
        ).all()
        seen: set[UUID] = set()
        result = []
        for thread, identity, endpoint in rows:
            if thread.id in seen:
                continue
            seen.add(thread.id)
            result.append(self._contact_state(thread, identity, endpoint, workspace_id))
        return result

    async def _validated_preview(
        self,
        task_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        data: TaskWhatsAppDraftPreviewRequest,
    ):
        task, thread, identity, endpoint, binding = await self._route(
            task_id, workspace_id, actor_user_id, data.contact_ref
        )
        try:
            share_id = UUID(data.share_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Referência do link inválida.") from exc
        fragment = parse_qs(urlsplit(data.share_url).fragment)
        token = (fragment.get("token") or [""])[0]
        share = await self.db.scalar(
            select(TaskExternalShare).where(
                TaskExternalShare.workspace_id == workspace_id,
                TaskExternalShare.task_id == task_id,
                TaskExternalShare.id == share_id,
                TaskExternalShare.token_hash == TaskExternalShareService._token_hash(token),
            )
        )
        if share is None or share.revoked_at is not None or _as_utc(share.expires_at) <= _now():
            raise HTTPException(status_code=409, detail="O link não está ativo.")
        body = data.message_body.strip()
        if data.share_url not in body:
            raise HTTPException(
                status_code=422, detail="A mensagem deve manter o link seguro criado."
            )
        contact = self._contact_state(thread, identity, endpoint, workspace_id)
        fingerprint = self._fingerprint(
            {
                "taskId": str(task.id),
                "threadId": str(thread.id),
                "endpointId": str(endpoint.id),
                "shareId": str(share.id),
                "messageBody": body,
                "consentState": contact.consent_state,
                "windowExpiresAt": contact.service_window_expires_at.isoformat()
                if contact.service_window_expires_at
                else None,
            }
        )
        return task, thread, endpoint, binding, contact, body, fingerprint

    async def preview(
        self,
        task_id: UUID,
        workspace_id: UUID,
        data: TaskWhatsAppDraftPreviewRequest,
        *,
        actor_user_id: UUID,
    ) -> TaskWhatsAppDraftPreview:
        *_, contact, body, fingerprint = await self._validated_preview(
            task_id, workspace_id, actor_user_id, data
        )
        return TaskWhatsAppDraftPreview(
            contact=contact,
            message_body=body,
            preview_fingerprint=fingerprint,
            delivery_mode="conversations_local_draft",
            external_actions_executed=0,
            human_confirmation_required=True,
        )

    async def prepare(
        self,
        task_id: UUID,
        workspace_id: UUID,
        data: TaskWhatsAppDraftConfirmRequest,
        idempotency_key: str,
        *,
        context: TaskMutationContext,
    ) -> TaskWhatsAppDraftPrepared:
        if not data.confirmed:
            raise HTTPException(status_code=422, detail="A confirmação humana é obrigatória.")
        task, thread, endpoint, binding, contact, body, fingerprint = await self._validated_preview(
            task_id, workspace_id, context.actor_user_id, data
        )
        if not hmac.compare_digest(fingerprint, data.preview_fingerprint):
            raise HTTPException(status_code=409, detail="A prévia mudou. Revise novamente.")
        message_key = self._message_key(workspace_id, idempotency_key)
        existing = await self.db.scalar(
            select(OmnichannelMessage).where(
                OmnichannelMessage.workspace_id == workspace_id,
                OmnichannelMessage.idempotency_key == message_key,
            )
        )
        if existing is not None:
            expected = hashlib.sha256(body.encode()).hexdigest()
            if (existing.message_metadata or {}).get("draftBodySha256") != expected:
                raise HTTPException(
                    status_code=409, detail="A chave de repetição já foi usada com outro texto."
                )
            return TaskWhatsAppDraftPrepared(
                contact=contact,
                message_body=existing.body,
                delivery_status="local_draft",
                delivery_mode="conversations_local_draft",
                external_actions_executed=0,
                conversation_path="/app/conversas",
                replayed=True,
            )
        message = OmnichannelMessage(
            workspace_id=workspace_id,
            thread_id=thread.id,
            matter_id=task.matter_id,
            endpoint_id=endpoint.id,
            binding_id=binding.id if binding else None,
            connection_id=endpoint.connection_id,
            author_user_id=context.actor_user_id,
            channel=OmnichannelChannel.whatsapp,
            direction=MessageDirection.outbound,
            author_type=MessageAuthorType.human,
            author_label="Equipe jurídica",
            body=body,
            audience="client_private",
            content_type="text",
            transport="local",
            window_expires_at=contact.service_window_expires_at,
            idempotency_key=message_key,
            delivery_status=MessageDeliveryStatus.local_draft,
            occurred_at=_now(),
            message_metadata={
                "source": "legal_task_external_share",
                "taskId": str(task.id),
                "draftBodySha256": hashlib.sha256(body.encode()).hexdigest(),
                "governance": {
                    "humanConfirmationRecorded": True,
                    "consentState": contact.consent_state,
                    "serviceWindowState": contact.service_window_state,
                    "sendEligibleAtPreparation": contact.send_eligible,
                    "externalActionsExecuted": 0,
                },
            },
        )
        self.db.add(message)
        await self.db.flush()
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=context.actor_user_id,
                actor_type="human",
                actor_label="Equipe jurídica",
                event_type="task_whatsapp_share_draft_prepared",
                entity_type="omnichannel_message",
                entity_id=message.id,
                summary=(
                    "Rascunho local preparado para revisão humana em Conversas; "
                    "nenhum envio executado."
                ),
                correlation_id=context.correlation_id,
                after_data={
                    "taskId": str(task.id),
                    "deliveryStatus": "local_draft",
                    "sendEligibleAtPreparation": contact.send_eligible,
                    "externalActionsExecuted": 0,
                },
            )
        )
        await self.db.flush()
        return TaskWhatsAppDraftPrepared(
            contact=contact,
            message_body=body,
            delivery_status="local_draft",
            delivery_mode="conversations_local_draft",
            external_actions_executed=0,
            conversation_path="/app/conversas",
        )
