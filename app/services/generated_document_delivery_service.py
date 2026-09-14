"""Durable continuation from a clean private document to WhatsApp delivery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import String, and_, cast, event, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.klm_runtime import aws_private_storage_allowed, external_delivery_allowed
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.models.klm_operations import (
    ExternalActionIntent,
    ExternalActionIntentStatus,
    KlmAuditEvent,
)
from app.models.matter import KanbanColumnId, Matter, MicroStatus
from app.models.storage_object import StorageAVStatus, StorageObject, StorageObjectState
from app.models.timeline_event import TimelineEvent, TimelineEventType
from app.models.whatsapp import WaMessageStatus, WhatsAppContact
from app.schemas.whatsapp import SendDocumentRequest
from app.services.local_object_storage_service import (
    LocalObjectStorageError,
    LocalObjectStorageService,
)
from app.services.private_document_storage_service import PrivateDocumentStorageService
from app.services.whatsapp.errors import WhatsAppProviderError

ACTION_TYPE = "whatsapp_generated_document_delivery"
_TERMINAL_STATES = {"sent", "blocked", "failed", "delivery_uncertain"}
_DELIVERY_AFTER_COMMIT = "generated_document_delivery_after_commit"
_DELIVERY_HOOKS_INSTALLED = "generated_document_delivery_hooks_installed"


def _dispatch_deliveries_after_commit(session: Session) -> None:
    jobs = list(dict.fromkeys(session.info.pop(_DELIVERY_AFTER_COMMIT, [])))
    if not jobs:
        return
    from app.workers.storage_tasks import dispatch_generated_document_deliveries

    for workspace_id, version_id in jobs:
        try:
            dispatch_generated_document_deliveries.apply_async(
                kwargs={"workspace_id": workspace_id, "version_id": version_id}
            )
        except Exception as exc:  # noqa: BLE001 - periodic recovery owns retry
            logger.error(
                "storage.generated_delivery_enqueue_failed",
                workspace_id=workspace_id,
                version_id=version_id,
                error_type=type(exc).__name__,
            )


def _clear_deliveries_after_rollback(session: Session) -> None:
    session.info.pop(_DELIVERY_AFTER_COMMIT, None)


@dataclass(frozen=True)
class GeneratedDeliveryQueueResult:
    intent: ExternalActionIntent
    created: bool


class GeneratedDocumentDeliveryService:
    """Persist and recover one outbound effect without storing signed URLs."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def queue_after_clean(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        storage_object: StorageObject,
        contact_id: UUID,
        connection_id: UUID | None,
        filename: str,
        caption: str | None,
        idempotency_key: str,
        kind: str,
        matter_id: UUID | None,
    ) -> GeneratedDeliveryQueueResult:
        key = idempotency_key.strip()
        if not 8 <= len(key) <= 180:
            raise ValueError("Idempotency-Key de entrega deve conter 8 a 180 caracteres.")
        payload = {
            "schemaVersion": 1,
            "state": "awaiting_av",
            "storageObjectVersionId": str(storage_object.id),
            "storageObjectId": str(storage_object.object_id),
            "contactId": str(contact_id),
            "connectionId": str(connection_id) if connection_id else None,
            "filename": filename,
            "caption": caption,
            "kind": kind,
            "attemptCount": 0,
        }
        existing = await self.db.scalar(
            select(ExternalActionIntent).where(
                ExternalActionIntent.workspace_id == workspace_id,
                ExternalActionIntent.idempotency_key == key,
            )
        )
        if existing is not None:
            self._validate_replay(
                existing,
                actor_user_id=actor_user_id,
                matter_id=matter_id,
                payload=payload,
            )
            self._queue_ready_dispatch(existing, storage_object)
            return GeneratedDeliveryQueueResult(existing, False)

        intent = ExternalActionIntent(
            workspace_id=workspace_id,
            requested_by_user_id=actor_user_id,
            matter_id=matter_id,
            action_type=ACTION_TYPE,
            provider="whatsapp",
            idempotency_key=key,
            status=ExternalActionIntentStatus.approved_local,
            payload=payload,
            reason="Entrega autorizada pelo operador e aguardando AV clean.",
        )
        try:
            async with self.db.begin_nested():
                self.db.add(intent)
                await self.db.flush()
        except IntegrityError:
            existing = await self.db.scalar(
                select(ExternalActionIntent).where(
                    ExternalActionIntent.workspace_id == workspace_id,
                    ExternalActionIntent.idempotency_key == key,
                )
            )
            if existing is None:
                raise
            self._validate_replay(
                existing,
                actor_user_id=actor_user_id,
                matter_id=matter_id,
                payload=payload,
            )
            self._queue_ready_dispatch(existing, storage_object)
            return GeneratedDeliveryQueueResult(existing, False)
        await self._audit(
            intent,
            "generated_document_delivery_queued",
            "Documento gerado aguardando antivírus antes do envio por WhatsApp.",
            after={"state": "awaiting_av", "storageObjectVersionId": str(storage_object.id)},
        )
        self._queue_ready_dispatch(intent, storage_object)
        return GeneratedDeliveryQueueResult(intent, True)

    def _queue_ready_dispatch(
        self,
        intent: ExternalActionIntent,
        storage_object: StorageObject,
    ) -> None:
        state = str((intent.payload or {}).get("state") or "awaiting_av")
        if (
            state in _TERMINAL_STATES
            or storage_object.state is not StorageObjectState.available
            or storage_object.av_status is not StorageAVStatus.clean
        ):
            return
        sync_session = self.db.sync_session
        if not sync_session.info.get(_DELIVERY_HOOKS_INSTALLED):
            event.listen(sync_session, "after_commit", _dispatch_deliveries_after_commit)
            event.listen(sync_session, "after_rollback", _clear_deliveries_after_rollback)
            sync_session.info[_DELIVERY_HOOKS_INSTALLED] = True
        sync_session.info.setdefault(_DELIVERY_AFTER_COMMIT, []).append(
            (str(intent.workspace_id), str(storage_object.id))
        )

    @staticmethod
    def _validate_replay(
        intent: ExternalActionIntent,
        *,
        actor_user_id: UUID,
        matter_id: UUID | None,
        payload: dict[str, Any],
    ) -> None:
        existing_payload = intent.payload or {}
        immutable_keys = {
            "storageObjectVersionId",
            "storageObjectId",
            "contactId",
            "connectionId",
            "filename",
            "caption",
            "kind",
        }
        if (
            intent.action_type != ACTION_TYPE
            or intent.requested_by_user_id != actor_user_id
            or intent.matter_id != matter_id
            or any(existing_payload.get(key) != payload.get(key) for key in immutable_keys)
        ):
            raise ValueError("Idempotency-Key já pertence a outra entrega externa.")

    async def dispatch_for_storage_version(self, storage_version_id: UUID) -> int:
        storage = await self.db.get(StorageObject, storage_version_id)
        if storage is None:
            return 0
        version_reference = ExternalActionIntent.payload[
            "storageObjectVersionId"
        ].as_string()
        intent_ids = list(
            (
                await self.db.scalars(
                    select(ExternalActionIntent.id).where(
                        ExternalActionIntent.action_type == ACTION_TYPE,
                        ExternalActionIntent.workspace_id == storage.workspace_id,
                        version_reference == str(storage_version_id),
                        ExternalActionIntent.status
                        == ExternalActionIntentStatus.approved_local,
                    )
                )
            ).all()
        )
        dispatched = 0
        for intent_id in intent_ids:
            result = await self.dispatch_intent(intent_id)
            if result == "sent":
                dispatched += 1
        return dispatched

    async def dispatch_due(self, *, workspace_id: UUID, limit: int = 50) -> int:
        version_reference = ExternalActionIntent.payload[
            "storageObjectVersionId"
        ].as_string()
        eligible_storage = or_(
            StorageObject.id.is_(None),
            StorageObject.deleted_at.is_not(None),
            StorageObject.av_status == StorageAVStatus.infected,
            and_(
                StorageObject.state == StorageObjectState.available,
                StorageObject.av_status == StorageAVStatus.clean,
            ),
        )
        intents = list(
            (
                await self.db.scalars(
                    select(ExternalActionIntent)
                    .outerjoin(
                        StorageObject,
                        and_(
                            StorageObject.workspace_id
                            == ExternalActionIntent.workspace_id,
                            cast(StorageObject.id, String) == version_reference,
                        ),
                    )
                    .where(
                        ExternalActionIntent.action_type == ACTION_TYPE,
                        ExternalActionIntent.workspace_id == workspace_id,
                        ExternalActionIntent.status
                        == ExternalActionIntentStatus.approved_local,
                        eligible_storage,
                    )
                    .order_by(ExternalActionIntent.created_at, ExternalActionIntent.id)
                    .limit(limit)
                )
            ).all()
        )
        progressed = 0
        for intent in intents:
            state = str((intent.payload or {}).get("state") or "awaiting_av")
            if state in _TERMINAL_STATES:
                continue
            result = await self.dispatch_intent(intent.id)
            if result in {"sent", "waiting_external", "awaiting_av"}:
                progressed += int(result == "sent")
        return progressed

    async def dispatch_intent(self, intent_id: UUID) -> str:
        intent = await self.db.scalar(
            select(ExternalActionIntent)
            .where(
                ExternalActionIntent.id == intent_id,
                ExternalActionIntent.action_type == ACTION_TYPE,
            )
            .with_for_update()
        )
        if intent is None:
            return "missing"
        payload = dict(intent.payload or {})
        state = str(payload.get("state") or "awaiting_av")
        if state in _TERMINAL_STATES:
            return state
        try:
            storage_version_id = UUID(str(payload["storageObjectVersionId"]))
            contact_id = UUID(str(payload["contactId"]))
            connection_id = (
                UUID(str(payload["connectionId"]))
                if payload.get("connectionId")
                else None
            )
        except (KeyError, TypeError, ValueError):
            await self._settle(
                intent,
                state="blocked",
                reason="Contrato persistido de entrega é inválido.",
                error_code="generated_delivery_contract_invalid",
            )
            return "blocked"

        storage = await self.db.scalar(
            select(StorageObject).where(
                StorageObject.id == storage_version_id,
                StorageObject.workspace_id == intent.workspace_id,
            )
        )
        if storage is None or storage.deleted_at is not None:
            await self._settle(
                intent,
                state="blocked",
                reason="Documento da entrega não existe ou foi excluído.",
                error_code="generated_delivery_storage_missing",
            )
            return "blocked"
        if storage.av_status is StorageAVStatus.infected:
            await self._settle(
                intent,
                state="blocked",
                reason="Documento infectado foi bloqueado antes do envio.",
                error_code="generated_delivery_malware_blocked",
            )
            return "blocked"
        if (
            storage.state is not StorageObjectState.available
            or storage.av_status is not StorageAVStatus.clean
        ):
            await self._settle(
                intent,
                state="awaiting_av",
                reason="Documento ainda aguarda veredito AV clean.",
            )
            return "awaiting_av"
        contact = await self.db.scalar(
            select(WhatsAppContact).where(
                WhatsAppContact.id == contact_id,
                WhatsAppContact.workspace_id == intent.workspace_id,
            )
        )
        if contact is None or intent.requested_by_user_id is None:
            await self._settle(
                intent,
                state="blocked",
                reason="Contato ou ator da entrega não está mais disponível.",
                error_code="generated_delivery_principal_missing",
            )
            return "blocked"
        if not external_delivery_allowed():
            await self._settle(
                intent,
                state="waiting_external",
                reason="Entrega externa está desabilitada; item permanece recuperável.",
            )
            return "waiting_external"

        try:
            if aws_private_storage_allowed():
                authorization = await PrivateDocumentStorageService(
                    self.db
                ).create_download_authorization(
                    workspace_id=intent.workspace_id,
                    object_id=storage.object_id,
                    version=storage.version,
                    expires_in=300,
                    actor_user_id=intent.requested_by_user_id,
                )
                delivery_url = authorization.download_url
            else:
                authorization = await LocalObjectStorageService(
                    self.db
                ).create_download_authorization(
                    workspace_id=intent.workspace_id,
                    object_id=storage.object_id,
                    version=storage.version,
                    expires_in=300,
                    purpose="media_delivery",
                )
                delivery_url = authorization.ticket.url
        except LocalObjectStorageError as exc:
            await self._settle(
                intent,
                state="retry_required",
                reason="Ticket privado de entrega indisponível; nova tentativa é segura.",
                error_code=safe_error_type(exc),
            )
            return "retry_required"

        from app.services.whatsapp_service import WhatsAppService

        attempt = int(payload.get("attemptCount", 0) or 0) + 1
        outbound_key = f"generated-document:{intent.id}"
        try:
            result = await WhatsAppService(self.db).message_service.send_storage_document(
                SendDocumentRequest(
                    contact_id=contact.id,
                    connection_id=connection_id,
                    url=delivery_url,
                    filename=str(payload.get("filename") or storage.original_filename),
                    caption=(str(payload["caption"]) if payload.get("caption") else None),
                    idempotency_key=outbound_key,
                ),
                intent.requested_by_user_id,
                intent.workspace_id,
                storage_object_version_id=storage.id,
                storage_key=storage.storage_key,
            )
        except HTTPException as exc:
            provider_error = exc.__cause__
            delivery_uncertain = (
                isinstance(provider_error, WhatsAppProviderError)
                and provider_error.delivery_uncertain
            )
            if delivery_uncertain:
                # WhatsAppMessageService has already durably fenced the provider
                # attempt as ``delivery_uncertain``.  Mirroring that state here is
                # essential: treating the intent as terminal ``failed`` would hide
                # an ambiguous external effect from reconciliation and could invite
                # an operator to send the same legal document again.
                await self._settle(
                    intent,
                    state="delivery_uncertain",
                    reason=(
                        "O provedor pode ter recebido o documento; "
                        "reconciliação humana obrigatória."
                    ),
                    error_code=getattr(provider_error, "code", None)
                    or f"http_{exc.status_code}",
                    attempt_count=attempt,
                )
                return "delivery_uncertain"
            await self._settle(
                intent,
                state="failed",
                reason="Provedor recusou a entrega; revisão humana necessária.",
                error_code=f"http_{exc.status_code}",
                attempt_count=attempt,
            )
            return "failed"
        except Exception as exc:
            # The provider may have received the request. Never auto-retry an
            # ambiguous delivery and risk sending the legal document twice.
            await self._settle(
                intent,
                state="delivery_uncertain",
                reason="Resultado do provedor é incerto; reconciliação humana necessária.",
                error_code=safe_error_type(exc),
                attempt_count=attempt,
            )
            return "delivery_uncertain"

        if result.status is WaMessageStatus.pending:
            await self._settle(
                intent,
                state="waiting_external",
                reason="Mensagem persistida e aguardando abertura do gate externo.",
                attempt_count=attempt,
                message_id=result.messageId,
            )
            return "waiting_external"
        if result.status is WaMessageStatus.delivery_uncertain:
            await self._settle(
                intent,
                state="delivery_uncertain",
                reason="Entrega aceita sem resultado conclusivo do provedor.",
                attempt_count=attempt,
                message_id=result.messageId,
            )
            return "delivery_uncertain"
        if result.status is WaMessageStatus.failed:
            await self._settle(
                intent,
                state="failed",
                reason="Entrega rejeitada pelo provedor.",
                attempt_count=attempt,
                message_id=result.messageId,
            )
            return "failed"

        await self._settle(
            intent,
            state="sent",
            reason="Documento limpo enviado pelo canal WhatsApp.",
            attempt_count=attempt,
            message_id=result.messageId,
            commit=False,
        )
        await self._record_business_success(intent, payload, result.messageId)
        await self.db.commit()
        return "sent"

    async def _record_business_success(
        self,
        intent: ExternalActionIntent,
        payload: dict[str, Any],
        message_id: UUID,
    ) -> None:
        if intent.matter_id is None:
            return
        matter = await self.db.scalar(
            select(Matter).where(
                Matter.id == intent.matter_id,
                Matter.workspace_id == intent.workspace_id,
            )
        )
        if matter is None:
            return
        if payload.get("kind") == "proposal":
            matter.column_id = KanbanColumnId.proposta_fechamento
            matter.micro_status = MicroStatus.proposta_enviada
            matter.next_action = "Aguardar retorno sobre a proposta enviada."
        self.db.add(
            TimelineEvent(
                matter_id=matter.id,
                agent_id=intent.requested_by_user_id,
                event_type=TimelineEventType.agent_action,
                title="Documento enviado pelo WhatsApp",
                description=str(payload.get("filename") or "Documento gerado"),
                event_data={
                    "delivery_intent_id": str(intent.id),
                    "message_id": str(message_id),
                    "kind": payload.get("kind"),
                },
            )
        )
        await self.db.flush()

    async def _settle(
        self,
        intent: ExternalActionIntent,
        *,
        state: str,
        reason: str,
        error_code: str | None = None,
        attempt_count: int | None = None,
        message_id: UUID | None = None,
        commit: bool = True,
    ) -> None:
        before_state = str((intent.payload or {}).get("state") or "awaiting_av")
        payload = dict(intent.payload or {})
        if (
            before_state == state
            and intent.reason == reason
            and payload.get("lastErrorCode") == error_code
            and attempt_count is None
            and message_id is None
        ):
            return
        payload.update(
            {
                "state": state,
                "updatedAt": datetime.now(UTC).isoformat(),
                "lastErrorCode": error_code,
            }
        )
        if attempt_count is not None:
            payload["attemptCount"] = attempt_count
        if message_id is not None:
            payload["messageId"] = str(message_id)
        intent.payload = payload
        intent.reason = reason
        await self._audit(
            intent,
            f"generated_document_delivery_{state}",
            reason,
            before={"state": before_state},
            after={
                "state": state,
                "messageId": str(message_id) if message_id else None,
                "errorCode": error_code,
            },
        )
        if commit:
            await self.db.commit()

    async def _audit(
        self,
        intent: ExternalActionIntent,
        event_type: str,
        summary: str,
        *,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        self.db.add(
            KlmAuditEvent(
                workspace_id=intent.workspace_id,
                actor_user_id=intent.requested_by_user_id,
                actor_type="human" if intent.requested_by_user_id else "system",
                actor_label=str(intent.requested_by_user_id or "delivery-worker"),
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
