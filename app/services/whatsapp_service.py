from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import BinaryIO
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.models.whatsapp_campaign import MetaTemplateStatus
from app.schemas.whatsapp import (
    ApplyFlowRequest,
    ConversationAssigneeRead,
    ConversationAssignRequest,
    ConversationDocumentSendRequest,
    ConversationHandoffRequest,
    ConversationRead,
    MessageTemplateRead,
    MessageTemplateSettingsRead,
    MessageTemplateSettingsUpdate,
    ProviderMediaUploadIntentRead,
    ProviderMediaUploadRead,
    ScheduleConversationMeetingRequest,
    ScheduleConversationMessageRequest,
    SendContractRequest,
    SendDocumentRequest,
    SendMessageRequest,
    SendMessageResponse,
    SendProposalRequest,
    SendStructuredMessageRequest,
    StartWhatsAppConversationRequest,
    TechProviderCallbackRequest,
    WhatsAppConnectionCreateManual,
    WhatsAppConnectionCreateQr,
    WhatsAppConnectionRead,
    WhatsAppConnectionStatus,
    WhatsAppConnectionStatusRead,
    WhatsAppContactRead,
)
from app.schemas.whatsapp_automation import (
    WhatsAppAutomationSettingsRead,
    WhatsAppAutomationSettingsUpdate,
)
from app.schemas.whatsapp_campaign import (
    AudienceListCreate,
    AudienceListImportResult,
    AudienceListMemberCreate,
    AudienceListRead,
    AudienceListUpdate,
    CampaignCreate,
    CampaignLaunchRequest,
    CampaignRead,
    CampaignUpdate,
    MetaTemplateRead,
    OptInRecordCreate,
    OptInRecordRead,
    SuppressionEntryCreate,
    SuppressionEntryRead,
)
from app.schemas.whatsapp_collaboration import (
    ConversationInternalNoteCreate,
    ConversationInternalNoteRead,
    LeadDrawerRead,
    MessageOptimizationRead,
    MoveConversationStageRequest,
)
from app.services.message_evidence_service import (
    MessageEvidenceService,
    ProviderEnvelopeContext,
)
from app.services.message_template_service import MessageTemplateService
from app.services.tech_provider_service import TechProviderService
from app.services.whatsapp.automation_rule_service import WhatsAppAutomationRuleService
from app.services.whatsapp.campaign_service import WhatsAppCampaignService
from app.services.whatsapp.collaboration_service import WhatsAppCollaborationService
from app.services.whatsapp.connection_service import WhatsAppConnectionService
from app.services.whatsapp.errors import WhatsAppProviderError
from app.services.whatsapp.inbox_service import WhatsAppInboxService
from app.services.whatsapp.media_upload import (
    WhatsAppMediaUploadError,
    normalized_media_extension,
    resolve_media_policy,
    validate_media_content,
    validate_media_size,
)
from app.services.whatsapp.message_service import WhatsAppMessageService
from app.services.whatsapp.meta_cloud_client import MetaCloudClient
from app.services.whatsapp.provider_media_receipt_service import (
    ProviderMediaReceiptError,
    ProviderMediaReceiptService,
)
from app.services.whatsapp.qr_gateway_client import QrGatewayClient
from app.services.whatsapp.secrets import WhatsAppSecretCipher
from app.services.whatsapp.strategy_factory import WhatsAppStrategyFactory
from app.services.whatsapp.template_management_service import TemplateManagementService
from app.services.whatsapp.utility_template_provisioning_service import (
    UtilityTemplateProvisioningService,
)
from app.services.whatsapp.webhook_service import WhatsAppWebhookService


class WhatsAppService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.cipher = WhatsAppSecretCipher()
        self.meta_client = MetaCloudClient()
        self.qr_client = QrGatewayClient()
        self.connection_service = WhatsAppConnectionService(
            db,
            cipher=self.cipher,
            meta_client=self.meta_client,
            qr_client=self.qr_client,
        )
        self.strategy_factory = WhatsAppStrategyFactory(
            meta_client=self.meta_client,
            qr_client=self.qr_client,
            access_token_resolver=self.connection_service.get_meta_access_token,
            instance_token_resolver=self.connection_service.get_instance_token,
        )
        self.message_service = WhatsAppMessageService(
            db,
            connection_service=self.connection_service,
            strategy_factory=self.strategy_factory,
        )
        self.template_service = MessageTemplateService(db)
        self.automation_rule_service = WhatsAppAutomationRuleService(db)
        self.campaign_service = WhatsAppCampaignService(
            db,
            connection_service=self.connection_service,
            meta_client=self.meta_client,
        )
        self.collaboration_service = WhatsAppCollaborationService(db)
        self.inbox_service = WhatsAppInboxService(
            db,
            message_service=self.message_service,
            template_service=self.template_service,
        )
        self.webhook_service = WhatsAppWebhookService(
            db,
            connection_service=self.connection_service,
            message_service=self.message_service,
            automation_rule_service=self.automation_rule_service,
            campaign_service=self.campaign_service,
        )
        self.tech_provider_service = TechProviderService(
            self.connection_service,
            meta_client=self.meta_client,
        )
        self.template_management_service = TemplateManagementService(
            db,
            connection_service=self.connection_service,
            meta_client=self.meta_client,
        )
        self.utility_template_provisioning_service = UtilityTemplateProvisioningService(
            db,
            connection_service=self.connection_service,
            meta_client=self.meta_client,
        )

    async def _prepare_and_enqueue_utility_catalog(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        only_keys: set[str] | None = None,
        retry_rejected: bool = False,
    ) -> None:
        """Persist the onboarding intent before waking the worker.

        Broker failure never rolls back a valid WhatsApp connection. The
        persisted local rows remain visible and can be retried from App or
        Blackbox instead of silently pretending they were submitted.
        """
        await self.utility_template_provisioning_service.prepare(
            workspace_id=workspace_id,
            connection_id=connection_id,
            only_keys=only_keys,
            retry_rejected=retry_rejected,
        )
        try:
            from app.workers.whatsapp_utility_template_tasks import (
                provision_utility_templates,
            )

            provision_utility_templates.apply_async(
                args=[str(workspace_id), str(connection_id)],
                kwargs={
                    "only_keys": sorted(only_keys) if only_keys else None,
                    "retry_rejected": retry_rejected,
                },
                countdown=2,
                retry=False,
            )
        except Exception:  # noqa: BLE001 - durable intent remains operator-visible
            logger.warning(
                "whatsapp.utility_template_catalog_wakeup_failed",
                workspace_id=str(workspace_id),
                connection_id=str(connection_id),
            )

    @staticmethod
    def _hash_seekable_file(file_content: BinaryIO) -> tuple[str, int]:
        digest = hashlib.sha256()
        size_bytes = 0
        file_content.seek(0)
        while chunk := file_content.read(1024 * 1024):
            digest.update(chunk)
            size_bytes += len(chunk)
        file_content.seek(0)
        return digest.hexdigest(), size_bytes

    async def upload_provider_media(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        filename: str,
        content_type: str,
        file_content: BinaryIO,
        source_sha256: str,
        source_size_bytes: int,
        idempotency_key: str | None = None,
    ) -> ProviderMediaUploadRead:
        """Upload media through a durable, idempotent provider checkpoint."""
        from app.models.whatsapp import ConnectionType

        policy = resolve_media_policy(content_type)
        validate_media_size(source_size_bytes, policy)
        actual_sha256, actual_size = await asyncio.to_thread(
            self._hash_seekable_file,
            file_content,
        )
        if actual_size != source_size_bytes or actual_sha256 != source_sha256.lower():
            raise WhatsAppMediaUploadError(
                "O arquivo mudou durante a preparação do upload.",
                code="provider_media_source_changed",
            )
        await asyncio.to_thread(
            validate_media_content,
            file_content,
            filename=filename,
            policy=policy,
        )

        legacy = await self.connection_service.get_connection(connection_id, workspace_id)
        if legacy.connection_type == ConnectionType.QR_CODE or not legacy.phone_number_id:
            raise ProviderMediaReceiptError(
                "Upload por ID de mídia exige uma conexão oficial da Meta.",
                code="provider_media_official_connection_required",
            )
        await self.connection_service.ensure_omnichannel_projection(legacy)
        receipt_service = ProviderMediaReceiptService(self.db)
        native = await receipt_service.resolve_native_connection(
            workspace_id=workspace_id,
            legacy_connection_id=legacy.id,
            provider="meta_cloud",
        )
        access_token = await self.connection_service.resolve_meta_access_token(legacy)
        safe_filename = (filename.strip() or "arquivo")[:255]
        filename_sha256 = hashlib.sha256(safe_filename.encode("utf-8")).hexdigest()
        source_extension = normalized_media_extension(safe_filename)
        payload_sha256 = hashlib.sha256(
            json.dumps(
                {
                    "connectionId": str(native.id),
                    "filenameSha256": filename_sha256,
                    "mediaType": policy.media_type,
                    "sourceContentType": policy.content_type,
                    "sourceExtension": source_extension,
                    "sourceSha256": actual_sha256,
                    "sourceSizeBytes": actual_size,
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        raw_idempotency_key = str(idempotency_key or "").strip()
        if len(raw_idempotency_key) > 512:
            raise WhatsAppMediaUploadError(
                "A chave idempotente do upload excede o limite permitido.",
                code="provider_media_idempotency_key_invalid",
            )
        if not raw_idempotency_key:
            raw_idempotency_key = f"provider-media:{payload_sha256}"
        idempotency_key_hash = hashlib.sha256(raw_idempotency_key.encode("utf-8")).hexdigest()
        intent, _created = await receipt_service.prepare_upload_intent(
            workspace_id=workspace_id,
            connection_id=native.id,
            provider="meta_cloud",
            idempotency_key_hash=idempotency_key_hash,
            payload_sha256=payload_sha256,
            media_type=policy.media_type,
            source_sha256=actual_sha256,
            source_size_bytes=actual_size,
            source_content_type=policy.content_type,
            source_extension=source_extension,
            source_filename_sha256=filename_sha256,
        )
        upload_intent_id = intent.id

        if intent.state == "completed":
            receipt = await receipt_service.receipt_for_intent(intent)
            return self._provider_media_upload_read(intent, receipt, deduplicated=True)
        if intent.state == "provider_accepted":
            accepted_at = intent.provider_accepted_at or datetime.now(UTC)
            receipt = await receipt_service.complete_upload_intent(
                intent,
                expires_at=accepted_at + timedelta(days=30),
            )
            return self._provider_media_upload_read(intent, receipt, deduplicated=True)
        if intent.state in {"dispatching", "delivery_uncertain"}:
            raise ProviderMediaReceiptError(
                "O resultado deste upload precisa ser reconciliado antes de uma nova tentativa.",
                code="provider_media_reconciliation_required",
                status_code=409,
                intent_id=upload_intent_id,
            )
        if intent.state == "failed":
            raise ProviderMediaReceiptError(
                intent.last_error_message
                or "Esta tentativa de upload foi rejeitada pelo provedor.",
                code="provider_media_upload_failed",
                status_code=409,
                intent_id=upload_intent_id,
            )

        intent, claimed = await receipt_service.claim_upload_intent(intent)
        if not claimed:
            if intent.state == "completed":
                receipt = await receipt_service.receipt_for_intent(intent)
                return self._provider_media_upload_read(intent, receipt, deduplicated=True)
            if intent.state == "provider_accepted":
                accepted_at = intent.provider_accepted_at or datetime.now(UTC)
                receipt = await receipt_service.complete_upload_intent(
                    intent,
                    expires_at=accepted_at + timedelta(days=30),
                )
                return self._provider_media_upload_read(intent, receipt, deduplicated=True)
            raise ProviderMediaReceiptError(
                "Outra execução já assumiu este upload.",
                code="provider_media_reconciliation_required",
                status_code=409,
                intent_id=upload_intent_id,
            )
        try:
            provider_receipt = await self.meta_client.upload_media(
                phone_number_id=legacy.phone_number_id,
                access_token=access_token,
                filename=safe_filename,
                content_type=policy.content_type,
                file_content=file_content,
            )
        except WhatsAppProviderError as exc:
            state = (
                "delivery_uncertain"
                if exc.delivery_uncertain
                else ("failed" if exc.request_started else "prepared")
            )
            try:
                await receipt_service.record_upload_error(
                    intent,
                    state=state,
                    code=exc.code,
                    message=exc.message,
                )
            except Exception as checkpoint_error:
                await self.db.rollback()
                raise ProviderMediaReceiptError(
                    "A falha do provedor não pôde ser reconciliada automaticamente.",
                    code="provider_media_reconciliation_required",
                    status_code=409,
                    intent_id=upload_intent_id,
                ) from checkpoint_error
            if state == "delivery_uncertain":
                raise ProviderMediaReceiptError(
                    "A Meta pode ter aceitado a mídia; o upload exige reconciliação.",
                    code="provider_media_reconciliation_required",
                    status_code=409,
                    intent_id=upload_intent_id,
                ) from exc
            raise ProviderMediaReceiptError(
                exc.message,
                code=exc.code,
                status_code=exc.status_code,
                intent_id=upload_intent_id,
            ) from exc
        except Exception as exc:
            try:
                await receipt_service.record_upload_error(
                    intent,
                    state="delivery_uncertain",
                    code="provider_media_unexpected_transport_error",
                    message="Falha inesperada após o início do upload.",
                )
            except Exception:
                await self.db.rollback()
            raise ProviderMediaReceiptError(
                "O resultado do upload é incerto e exige reconciliação.",
                code="provider_media_reconciliation_required",
                status_code=409,
                intent_id=upload_intent_id,
            ) from exc

        provider_media_id = str(provider_receipt.get("id") or "").strip()
        intent = await receipt_service.checkpoint_provider_acceptance(
            intent,
            provider_media_id=provider_media_id,
            provider_receipt=provider_receipt,
        )
        accepted_at = intent.provider_accepted_at or datetime.now(UTC)
        receipt = await receipt_service.complete_upload_intent(
            intent,
            expires_at=accepted_at + timedelta(days=30),
        )
        return self._provider_media_upload_read(intent, receipt, deduplicated=False)

    @staticmethod
    def _provider_media_upload_read(
        intent,
        receipt,
        *,
        deduplicated: bool,
    ) -> ProviderMediaUploadRead:
        return ProviderMediaUploadRead(
            upload_intent_id=intent.id,
            receipt_id=receipt.id,
            provider_media_id=receipt.provider_media_id,
            connection_id=receipt.connection_id,
            media_type=receipt.media_type,
            source_sha256=receipt.source_sha256,
            source_size_bytes=receipt.source_size_bytes,
            source_content_type=receipt.source_content_type,
            uploaded_at=receipt.uploaded_at,
            expires_at=receipt.expires_at,
            deduplicated=deduplicated,
        )

    async def get_provider_media_upload_intent(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        intent_id: UUID,
    ) -> ProviderMediaUploadIntentRead:
        legacy = await self.connection_service.get_connection(connection_id, workspace_id)
        await self.connection_service.ensure_omnichannel_projection(legacy)
        receipt_service = ProviderMediaReceiptService(self.db)
        native = await receipt_service.resolve_native_connection(
            workspace_id=workspace_id,
            legacy_connection_id=legacy.id,
            provider="meta_cloud",
        )
        intent = await receipt_service.get_upload_intent(
            workspace_id=workspace_id,
            connection_id=native.id,
            intent_id=intent_id,
        )
        return await self._provider_media_upload_intent_read(intent, receipt_service)

    async def reconcile_provider_media_upload_intent(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        intent_id: UUID,
        actor_user_id: UUID,
        decision: str,
        reason: str,
        provider_media_id: str | None = None,
        provider_receipt: dict | None = None,
    ) -> ProviderMediaUploadIntentRead:
        legacy = await self.connection_service.get_connection(connection_id, workspace_id)
        await self.connection_service.ensure_omnichannel_projection(legacy)
        receipt_service = ProviderMediaReceiptService(self.db)
        native = await receipt_service.resolve_native_connection(
            workspace_id=workspace_id,
            legacy_connection_id=legacy.id,
            provider="meta_cloud",
        )
        intent = await receipt_service.get_upload_intent(
            workspace_id=workspace_id,
            connection_id=native.id,
            intent_id=intent_id,
        )
        intent = await receipt_service.reconcile_upload_intent(
            intent,
            actor_user_id=actor_user_id,
            decision=decision,
            reason=reason,
            provider_media_id=provider_media_id,
            provider_receipt=provider_receipt,
        )
        if intent.state == "provider_accepted":
            accepted_at = intent.provider_accepted_at or datetime.now(UTC)
            await receipt_service.complete_upload_intent(
                intent,
                expires_at=accepted_at + timedelta(days=30),
            )
        return await self._provider_media_upload_intent_read(intent, receipt_service)

    async def _provider_media_upload_intent_read(
        self,
        intent,
        receipt_service: ProviderMediaReceiptService,
    ) -> ProviderMediaUploadIntentRead:
        reconciliation = await receipt_service.latest_reconciliation(intent)
        return ProviderMediaUploadIntentRead(
            upload_intent_id=intent.id,
            connection_id=intent.connection_id,
            state=intent.state,
            attempt_count=intent.attempt_count,
            media_type=intent.media_type,
            source_sha256=intent.source_sha256,
            source_size_bytes=intent.source_size_bytes,
            source_content_type=intent.source_content_type,
            provider_media_id=intent.provider_media_id,
            receipt_id=intent.receipt_id,
            request_started_at=intent.request_started_at,
            provider_accepted_at=intent.provider_accepted_at,
            delivery_uncertain_at=intent.delivery_uncertain_at,
            completed_at=intent.completed_at,
            last_error_code=intent.last_error_code,
            last_error_message=intent.last_error_message,
            reconciliation_decision=(
                reconciliation.decision if reconciliation is not None else None
            ),
            reconciliation_reason=(
                reconciliation.reason if reconciliation is not None else None
            ),
            reconciled_by_user_id=(
                reconciliation.actor_user_id if reconciliation is not None else None
            ),
            reconciled_at=(
                reconciliation.resolved_at if reconciliation is not None else None
            ),
        )

    async def list_connections(self, workspace_id: UUID) -> list[WhatsAppConnectionRead]:
        return await self.connection_service.list_connections(workspace_id)

    async def create_manual_connection(
        self,
        payload: WhatsAppConnectionCreateManual,
        workspace_id: UUID,
    ) -> WhatsAppConnectionRead:
        connection = await self.connection_service.create_or_update_manual_connection(
            workspace_id=workspace_id,
            payload=payload,
        )
        await self._prepare_and_enqueue_utility_catalog(
            workspace_id=workspace_id,
            connection_id=connection.id,
        )
        return connection

    async def create_qr_connection(
        self,
        payload: WhatsAppConnectionCreateQr,
        workspace_id: UUID,
    ) -> WhatsAppConnectionRead:
        return await self.connection_service.create_qr_connection(
            workspace_id=workspace_id,
            payload=payload,
        )

    async def set_default_connection(
        self, workspace_id: UUID, connection_id: UUID
    ) -> WhatsAppConnectionRead:
        return await self.connection_service.set_default_connection(workspace_id, connection_id)

    async def delete_connection(self, workspace_id: UUID, connection_id: UUID) -> None:
        await self.connection_service.delete_connection(workspace_id, connection_id)

    async def get_connection_status(
        self, workspace_id: UUID, connection_id: UUID
    ) -> WhatsAppConnectionStatusRead:
        return await self.connection_service.get_connection_status(workspace_id, connection_id)

    async def complete_embedded_signup(
        self,
        payload: TechProviderCallbackRequest,
        workspace_id: UUID,
    ) -> WhatsAppConnectionRead:
        connection = await self.tech_provider_service.complete_embedded_signup(
            workspace_id=workspace_id,
            payload=payload,
        )
        await self._prepare_and_enqueue_utility_catalog(
            workspace_id=workspace_id,
            connection_id=connection.id,
        )
        return connection

    async def connect(
        self,
        access_token: str,
        phone_number_id: str,
        business_account_id: str,
        workspace_id: UUID,
    ) -> WhatsAppConnectionStatus:
        connection = await self.connection_service.create_or_update_manual_connection(
            workspace_id=workspace_id,
            payload=WhatsAppConnectionCreateManual(
                access_token=access_token,
                phone_number_id=phone_number_id,
                business_account_id=business_account_id,
                is_default=True,
            ),
            update_default=True,
        )
        await self._prepare_and_enqueue_utility_catalog(
            workspace_id=workspace_id,
            connection_id=connection.id,
        )
        return await self.get_status(workspace_id)

    async def disconnect(self, workspace_id: UUID) -> None:
        await self.connection_service.disconnect_default_connection(workspace_id)

    async def get_status(self, workspace_id: UUID) -> WhatsAppConnectionStatus:
        return await self.connection_service.get_legacy_status(workspace_id)

    async def send_message(
        self,
        payload: SendMessageRequest,
        sent_by_id: UUID,
        workspace_id: UUID,
        idempotency_key: str | None = None,
    ) -> SendMessageResponse:
        if idempotency_key:
            payload = payload.model_copy(update={"idempotency_key": idempotency_key})
        return await self.message_service.send_message(payload, sent_by_id, workspace_id)

    async def send_document(
        self,
        payload: SendDocumentRequest,
        sent_by_id: UUID,
        workspace_id: UUID,
        idempotency_key: str | None = None,
    ) -> SendMessageResponse:
        if idempotency_key:
            payload = payload.model_copy(update={"idempotency_key": idempotency_key})
        return await self.message_service.send_document(payload, sent_by_id, workspace_id)

    async def send_structured(
        self,
        payload: SendStructuredMessageRequest,
        sent_by_id: UUID,
        workspace_id: UUID,
        idempotency_key: str | None = None,
    ) -> SendMessageResponse:
        if idempotency_key:
            payload = payload.model_copy(update={"idempotency_key": idempotency_key})
        return await self.message_service.send_structured(payload, sent_by_id, workspace_id)

    async def start_conversation(
        self,
        payload: StartWhatsAppConversationRequest,
        *,
        sent_by_id: UUID,
        workspace_id: UUID,
        idempotency_key: str,
    ) -> ConversationRead:
        template = await self.campaign_service._get_template(
            workspace_id,
            payload.meta_template_id,
        )
        if template.status != MetaTemplateStatus.approved:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-START-002",
                    "message": "O template ainda não está aprovado pela Meta.",
                },
            )
        if template.whatsapp_connection_id != payload.connection_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-START-003",
                    "message": "O template não pertence à conexão selecionada.",
                },
            )
        connection = await self.campaign_service._resolve_official_connection(
            workspace_id,
            payload.connection_id,
        )
        contact = await self.campaign_service.ensure_direct_contact(
            workspace_id=workspace_id,
            connection=connection,
            phone=payload.phone,
            name=payload.contact_name,
        )
        components = []
        if payload.header_parameters:
            components.append(
                {
                    "type": "header",
                    "parameters": [
                        {"type": "text", "text": value}
                        for value in payload.header_parameters
                    ],
                }
            )
        if payload.body_parameters:
            components.append(
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": value}
                        for value in payload.body_parameters
                    ],
                }
            )
        await self.message_service.send_structured(
            SendStructuredMessageRequest(
                contact_id=contact.id,
                connection_id=connection.id,
                message_payload={
                    "kind": "template",
                    "name": template.name,
                    "language": template.language,
                    "components": components,
                },
                idempotency_key=idempotency_key,
            ),
            sent_by_id,
            workspace_id,
        )
        return await self.message_service.get_conversation(contact.id, workspace_id)

    async def _send_text_direct(
        self,
        phone: str,
        body: str,
        workspace_id: UUID,
        idempotency_key: str,
        connection_id: UUID | None = None,
    ) -> bool:
        # Propaga o veredito real do envio (True=enviado; False=gate fechado
        # ou falha do provider) para o chamador reportar delivery_status fiel.
        return await self.message_service.send_text_direct(
            phone=phone,
            body=body,
            workspace_id=workspace_id,
            connection_id=connection_id,
            idempotency_key=idempotency_key,
        )

    async def send_text_direct(
        self,
        *,
        phone: str,
        body: str,
        workspace_id: UUID,
        idempotency_key: str,
        connection_id: UUID | None = None,
    ) -> bool:
        """Public compatibility alias for durable system-authored messages."""
        return await self._send_text_direct(
            phone=phone,
            body=body,
            workspace_id=workspace_id,
            connection_id=connection_id,
            idempotency_key=idempotency_key,
        )

    async def resolve_webhook_signing_secrets(self, payload_bytes: bytes) -> list[str]:
        return await self.webhook_service.resolve_webhook_signing_secrets(payload_bytes)

    def verify_webhook_signature(
        self, payload_bytes: bytes, signature: str, *, candidate_secrets: list[str]
    ) -> bool:
        return self.webhook_service.verify_webhook_signature(
            payload_bytes, signature, candidate_secrets=candidate_secrets
        )

    async def capture_webhook_envelope(
        self,
        *,
        raw_payload: bytes,
        signature_header: str,
        request_metadata: dict | None = None,
    ) -> ProviderEnvelopeContext:
        return await MessageEvidenceService(self.db).capture_provider_envelope(
            provider="meta_whatsapp",
            raw_payload=raw_payload,
            signature_header=signature_header,
            signature_scheme="x-hub-signature-256",
            signature_verified=True,
            request_metadata=request_metadata,
        )

    async def process_webhook(
        self,
        payload: dict,
        *,
        envelope: ProviderEnvelopeContext | None = None,
    ) -> None:
        await self.webhook_service.process_webhook(payload, envelope=envelope)

    async def get_webhook_processing_state(self, envelope_id: UUID):
        """Internal operational lookup; never returns the raw signed payload."""
        return await MessageEvidenceService(
            self.db
        ).get_provider_webhook_processing_state(envelope_id)

    async def list_contacts(
        self,
        workspace_id: UUID,
        search: str | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[WhatsAppContactRead]:
        return await self.message_service.list_contacts(
            workspace_id, search=search, skip=skip, limit=limit
        )

    async def get_contact(self, *, workspace_id: UUID, contact_id: UUID):
        """Contato bruto, para quem precisa dos identificadores e não da conversa.

        Escopado por workspace na própria consulta: contato de outro tenant
        volta como None, nunca como erro distinto — quem chama não deve
        conseguir separar "não existe" de "não é seu".
        """
        from sqlalchemy import select

        from app.models.whatsapp import WhatsAppContact

        return await self.db.scalar(
            select(WhatsAppContact).where(
                WhatsAppContact.id == contact_id,
                WhatsAppContact.workspace_id == workspace_id,
            )
        )

    async def get_conversation(self, contact_id: UUID, workspace_id: UUID) -> ConversationRead:
        return await self.message_service.get_conversation(contact_id, workspace_id)

    async def image_message_to_pdf(self, message_id: UUID, workspace_id: UUID) -> bytes:
        return await self.message_service.image_message_to_pdf(message_id, workspace_id)

    async def mark_as_read(self, contact_id: UUID, workspace_id: UUID) -> None:
        await self.message_service.mark_as_read(contact_id, workspace_id)

    async def list_assignable_users(self, workspace_id: UUID) -> list[ConversationAssigneeRead]:
        return await self.inbox_service.list_assignable_users(workspace_id)

    async def assign_conversation(
        self,
        contact_id: UUID,
        workspace_id: UUID,
        payload: ConversationAssignRequest,
        acting_user_id: UUID,
    ) -> ConversationRead:
        return await self.inbox_service.assign_conversation(
            contact_id, workspace_id, payload, acting_user_id
        )

    async def claim_conversation(
        self, contact_id: UUID, workspace_id: UUID, acting_user_id: UUID
    ) -> ConversationRead:
        return await self.inbox_service.claim_conversation(contact_id, workspace_id, acting_user_id)

    async def handoff_conversation(
        self,
        contact_id: UUID,
        workspace_id: UUID,
        payload: ConversationHandoffRequest,
        acting_user_id: UUID,
    ) -> ConversationRead:
        return await self.inbox_service.handoff_conversation(
            contact_id, workspace_id, payload, acting_user_id
        )

    async def apply_flow(
        self,
        contact_id: UUID,
        workspace_id: UUID,
        payload: ApplyFlowRequest,
        acting_user_id: UUID,
    ) -> ConversationRead:
        return await self.inbox_service.apply_flow(
            contact_id, workspace_id, payload, acting_user_id
        )

    async def schedule_message_for_conversation(
        self,
        contact_id: UUID,
        workspace_id: UUID,
        payload: ScheduleConversationMessageRequest,
        acting_user_id: UUID,
    ) -> ConversationRead:
        return await self.inbox_service.schedule_message(
            contact_id, workspace_id, payload, acting_user_id
        )

    async def schedule_meeting_for_conversation(
        self,
        contact_id: UUID,
        workspace_id: UUID,
        payload: ScheduleConversationMeetingRequest,
        acting_user_id: UUID,
    ) -> ConversationRead:
        return await self.inbox_service.schedule_meeting(
            contact_id, workspace_id, payload, acting_user_id
        )

    async def send_media_for_conversation(
        self,
        contact_id: UUID,
        workspace_id: UUID,
        payload: ConversationDocumentSendRequest,
        acting_user_id: UUID,
        idempotency_key: str,
    ) -> ConversationRead:
        return await self.inbox_service.send_media(
            contact_id, workspace_id, payload, acting_user_id, idempotency_key
        )

    async def send_proposal_for_conversation(
        self,
        contact_id: UUID,
        workspace_id: UUID,
        payload: SendProposalRequest,
        acting_user_id: UUID,
        idempotency_key: str,
    ) -> ConversationRead:
        return await self.inbox_service.send_proposal(
            contact_id, workspace_id, payload, acting_user_id, idempotency_key
        )

    async def send_contract_for_conversation(
        self,
        contact_id: UUID,
        workspace_id: UUID,
        payload: SendContractRequest,
        acting_user_id: UUID,
        idempotency_key: str,
    ) -> ConversationRead:
        return await self.inbox_service.send_contract(
            contact_id, workspace_id, payload, acting_user_id, idempotency_key
        )

    async def list_templates(self, workspace_id: UUID) -> list[MessageTemplateRead]:
        return await self.inbox_service.list_templates(workspace_id)

    async def get_template_settings(self, workspace_id: UUID) -> MessageTemplateSettingsRead:
        return await self.inbox_service.get_template_settings(workspace_id)

    async def update_template_settings(
        self,
        workspace_id: UUID,
        payload: MessageTemplateSettingsUpdate,
    ) -> MessageTemplateSettingsRead:
        return await self.inbox_service.update_template_settings(workspace_id, payload)

    async def list_meta_templates(
        self, workspace_id: UUID, connection_id: UUID | None = None
    ) -> list[MetaTemplateRead]:
        return await self.campaign_service.list_meta_templates(
            workspace_id, connection_id=connection_id
        )

    async def sync_meta_templates(
        self, workspace_id: UUID, connection_id: UUID | None = None
    ) -> list[MetaTemplateRead]:
        return await self.campaign_service.sync_meta_templates(
            workspace_id, connection_id=connection_id
        )

    async def list_unified_templates(
        self,
        workspace_id: UUID,
        connection_id: UUID | None = None,
        include_removed_source: bool = False,
    ):
        return await self.template_management_service.list_unified(
            workspace_id,
            connection_id=connection_id,
            include_removed_source=include_removed_source,
        )

    async def create_template_draft(self, workspace_id: UUID, payload):
        return await self.template_management_service.create_draft(workspace_id, payload)

    async def get_template_draft(self, workspace_id: UUID, template_id: UUID):
        return await self.template_management_service.get_draft(workspace_id, template_id)

    async def update_template_draft(self, workspace_id: UUID, template_id: UUID, payload):
        return await self.template_management_service.update_draft(
            workspace_id, template_id, payload
        )

    async def delete_template_draft(self, workspace_id: UUID, template_id: UUID) -> None:
        await self.template_management_service.delete_draft(workspace_id, template_id)

    async def remove_local_template_record(
        self,
        workspace_id: UUID,
        template_id: UUID,
        *,
        confirm_history_loss: bool,
        actor_user_id: UUID | None = None,
        actor_label: str | None = None,
    ) -> None:
        await self.template_management_service.remove_local_record(
            workspace_id,
            template_id,
            confirm_history_loss=confirm_history_loss,
            actor_user_id=actor_user_id,
            actor_label=actor_label,
        )

    async def submit_template_draft(
        self,
        workspace_id: UUID,
        template_id: UUID,
        *,
        requested_by_user_id: UUID | None = None,
        requester_role=None,
        requester_label: str | None = None,
    ):
        return await self.template_management_service.submit(
            workspace_id,
            template_id,
            requested_by_user_id=requested_by_user_id,
            requester_role=requester_role,
            requester_label=requester_label,
        )

    async def list_pending_template_approvals(self, workspace_id: UUID):
        return await self.template_management_service.list_pending_internal_approvals(
            workspace_id
        )

    async def approve_template_internal_submission(
        self,
        workspace_id: UUID,
        template_id: UUID,
        *,
        approver_user_id: UUID,
        approver_label: str,
    ):
        return await self.template_management_service.approve_internal_submission(
            workspace_id,
            template_id,
            approver_user_id=approver_user_id,
            approver_label=approver_label,
        )

    async def clone_rejected_template_as_draft(self, workspace_id: UUID, template_id: UUID):
        return await self.template_management_service.clone_rejected_as_draft(
            workspace_id, template_id
        )

    async def list_utility_template_catalog(
        self, workspace_id: UUID, connection_id: UUID | None = None
    ):
        return await self.utility_template_provisioning_service.list_catalog(
            workspace_id=workspace_id,
            connection_id=connection_id,
        )

    async def retry_utility_template_catalog_item(
        self,
        workspace_id: UUID,
        catalog_key: str,
        connection_id: UUID | None = None,
    ):
        connection = await self.utility_template_provisioning_service.resolve_connection(
            workspace_id, connection_id
        )
        if connection is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-TPL-CATALOG-NO-CONNECTION",
                    "message": "Nenhuma conexão oficial ativa está disponível.",
                },
            )
        await self._prepare_and_enqueue_utility_catalog(
            workspace_id=workspace_id,
            connection_id=connection.id,
            only_keys={catalog_key},
            retry_rejected=True,
        )
        return await self.list_utility_template_catalog(workspace_id, connection.id)

    async def list_opt_ins(self, workspace_id: UUID) -> list[OptInRecordRead]:
        return await self.campaign_service.list_opt_ins(workspace_id)

    async def upsert_opt_in(
        self, workspace_id: UUID, payload: OptInRecordCreate
    ) -> OptInRecordRead:
        return await self.campaign_service.upsert_opt_in(workspace_id, payload)

    async def list_suppressions(self, workspace_id: UUID) -> list[SuppressionEntryRead]:
        return await self.campaign_service.list_suppressions(workspace_id)

    async def upsert_suppression(
        self, workspace_id: UUID, payload: SuppressionEntryCreate
    ) -> SuppressionEntryRead:
        return await self.campaign_service.upsert_suppression(workspace_id, payload)

    async def list_audience_lists(
        self,
        workspace_id: UUID,
        *,
        include_archived: bool = False,
        mask_sensitive: bool = False,
    ) -> list[AudienceListRead]:
        return await self.campaign_service.list_audience_lists(
            workspace_id,
            include_archived=include_archived,
            mask_sensitive=mask_sensitive,
        )

    async def get_audience_list(
        self,
        workspace_id: UUID,
        audience_list_id: UUID,
        *,
        mask_sensitive: bool = False,
    ) -> AudienceListRead:
        return await self.campaign_service.get_audience_list(
            workspace_id,
            audience_list_id,
            mask_sensitive=mask_sensitive,
        )

    async def create_audience_list(
        self,
        workspace_id: UUID,
        payload: AudienceListCreate,
        acting_user_id: UUID,
    ) -> AudienceListRead:
        return await self.campaign_service.create_audience_list(
            workspace_id,
            payload,
            acting_user_id=acting_user_id,
        )

    async def update_audience_list(
        self,
        workspace_id: UUID,
        audience_list_id: UUID,
        payload: AudienceListUpdate,
    ) -> AudienceListRead:
        return await self.campaign_service.update_audience_list(
            workspace_id,
            audience_list_id,
            payload,
        )

    async def add_audience_list_member(
        self,
        workspace_id: UUID,
        audience_list_id: UUID,
        payload: AudienceListMemberCreate,
    ) -> AudienceListRead:
        return await self.campaign_service.add_audience_list_member(
            workspace_id,
            audience_list_id,
            payload,
        )

    async def import_audience_list_csv(
        self,
        workspace_id: UUID,
        audience_list_id: UUID,
        csv_text: str,
        *,
        filename: str | None = None,
    ) -> AudienceListImportResult:
        return await self.campaign_service.import_audience_list_csv(
            workspace_id,
            audience_list_id,
            csv_text,
            filename=filename,
        )

    async def export_audience_list_csv(self, workspace_id: UUID, audience_list_id: UUID) -> str:
        return await self.campaign_service.export_audience_list_csv(workspace_id, audience_list_id)

    async def list_campaigns(self, workspace_id: UUID) -> list[CampaignRead]:
        return await self.campaign_service.list_campaigns(workspace_id)

    async def get_campaign(self, workspace_id: UUID, campaign_id: UUID) -> CampaignRead:
        return await self.campaign_service.get_campaign(workspace_id, campaign_id)

    async def create_campaign(
        self,
        workspace_id: UUID,
        payload: CampaignCreate,
        acting_user_id: UUID,
        *,
        idempotency_key: str | None = None,
    ) -> CampaignRead:
        return await self.campaign_service.create_campaign(
            workspace_id,
            payload,
            acting_user_id=acting_user_id,
            idempotency_key=idempotency_key,
        )

    async def update_campaign(
        self, workspace_id: UUID, campaign_id: UUID, payload: CampaignUpdate
    ) -> CampaignRead:
        return await self.campaign_service.update_campaign(workspace_id, campaign_id, payload)

    async def launch_campaign(
        self,
        workspace_id: UUID,
        campaign_id: UUID,
        payload: CampaignLaunchRequest,
        *,
        reject_terminal_status: bool = False,
    ) -> CampaignRead:
        return await self.campaign_service.launch_campaign(
            workspace_id,
            campaign_id,
            payload,
            reject_terminal_status=reject_terminal_status,
        )

    async def pause_campaign(self, workspace_id: UUID, campaign_id: UUID) -> CampaignRead:
        return await self.campaign_service.pause_campaign(workspace_id, campaign_id)

    async def resume_campaign(self, workspace_id: UUID, campaign_id: UUID) -> CampaignRead:
        return await self.campaign_service.resume_campaign(workspace_id, campaign_id)

    async def stop_campaign(self, workspace_id: UUID, campaign_id: UUID) -> CampaignRead:
        return await self.campaign_service.stop_campaign(workspace_id, campaign_id)

    async def list_conversation_notes(
        self, contact_id: UUID, workspace_id: UUID
    ) -> list[ConversationInternalNoteRead]:
        return await self.collaboration_service.list_notes(contact_id, workspace_id)

    async def create_conversation_note(
        self,
        contact_id: UUID,
        workspace_id: UUID,
        acting_user_id: UUID,
        payload: ConversationInternalNoteCreate,
    ) -> ConversationInternalNoteRead:
        return await self.collaboration_service.create_note(
            contact_id, workspace_id, acting_user_id, payload
        )

    async def get_lead_drawer(
        self,
        contact_id: UUID,
        workspace_id: UUID,
        *,
        can_edit_matter: bool,
        can_manage_conversation: bool,
    ) -> LeadDrawerRead:
        return await self.collaboration_service.get_lead_drawer(
            contact_id,
            workspace_id,
            can_edit_matter=can_edit_matter,
            can_manage_conversation=can_manage_conversation,
        )

    async def move_conversation_stage(
        self,
        contact_id: UUID,
        workspace_id: UUID,
        acting_user_id: UUID,
        payload: MoveConversationStageRequest,
        *,
        can_edit_matter: bool,
        can_manage_conversation: bool,
    ) -> LeadDrawerRead:
        return await self.collaboration_service.move_stage(
            contact_id,
            workspace_id,
            acting_user_id,
            payload,
            can_edit_matter=can_edit_matter,
            can_manage_conversation=can_manage_conversation,
        )

    async def optimize_message(self, body: str, tone: str) -> MessageOptimizationRead:
        return MessageOptimizationRead(
            suggestions=await self.collaboration_service.optimize_message(body, tone)
        )

    async def get_automation_settings(self, workspace_id: UUID) -> WhatsAppAutomationSettingsRead:
        return await self.automation_rule_service.get_settings(workspace_id)

    async def update_automation_settings(
        self,
        workspace_id: UUID,
        payload: WhatsAppAutomationSettingsUpdate,
    ) -> WhatsAppAutomationSettingsRead:
        return await self.automation_rule_service.update_settings(workspace_id, payload)

    async def get_campaign_metrics(self, workspace_id: UUID) -> dict:
        return await self.campaign_service.get_campaign_metrics(workspace_id)
