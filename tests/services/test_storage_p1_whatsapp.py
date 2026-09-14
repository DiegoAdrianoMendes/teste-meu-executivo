from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4, uuid5

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.config import settings
from app.models.client import Client
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.klm_operations import ExternalActionIntent, KlmAuditEvent
from app.models.matter import Matter
from app.models.storage_object import StorageAVStatus, StorageObject, StorageObjectState
from app.models.timeline_event import TimelineEvent
from app.models.whatsapp import (
    ConnectionType,
    WaConnectionStatus,
    WaMessageStatus,
    WhatsAppConnection,
    WhatsAppContact,
)
from app.schemas.whatsapp import ConversationDocumentSendRequest, SendContractRequest
from app.services.generated_document_delivery_service import (
    GeneratedDocumentDeliveryService,
)
from app.services.local_object_storage_service import LocalObjectUnavailableError
from app.services.s3_service import S3Service
from app.services.sdr_intake_service import (
    InboundMediaStorageState,
    _ensure_whatsapp_media_document,
    resolve_inbound_media_storage,
    upload_media_to_s3,
)
from app.services.whatsapp.errors import WhatsAppProviderError
from app.services.whatsapp.inbox_service import WhatsAppInboxService

OGG = b"OggS\x00\x02whatsapp-audio-payload"
PDF = b"%PDF-1.7\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"
META_URL = "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=audio-1"


@pytest.fixture(autouse=True)
def local_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(S3Service, "LOCAL_ROOT", tmp_path / "object-storage")
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_PRIVATE_AWS_STORAGE_ENABLED", False)
    monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", "whatsapp-test-token")
    monkeypatch.setattr(settings, "PAPERCLIP_PUBLIC_URL", "http://test")


class _MetaMediaResponse:
    status_code = 200

    def json(self) -> dict[str, str]:
        return {"url": META_URL}


class _StreamedMediaResponse:
    status_code = 200

    def __init__(self, payload: bytes, *, include_length: bool = True) -> None:
        self.payload = payload
        self.headers = {"content-type": "audio/ogg"}
        if include_length:
            self.headers["content-length"] = str(len(payload))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def aiter_bytes(self):
        midpoint = max(1, len(self.payload) // 2)
        yield self.payload[:midpoint]
        yield self.payload[midpoint:]


class _FakeMetaClient:
    get_calls = 0
    stream_calls = 0
    payload = OGG
    include_length = True

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, *_args, **_kwargs):
        type(self).get_calls += 1
        return _MetaMediaResponse()

    def stream(self, *_args, **_kwargs):
        type(self).stream_calls += 1
        return _StreamedMediaResponse(
            type(self).payload,
            include_length=type(self).include_length,
        )

    @classmethod
    def reset(cls, *, payload: bytes = OGG, include_length: bool = True) -> None:
        cls.get_calls = 0
        cls.stream_calls = 0
        cls.payload = payload
        cls.include_length = include_length


async def _whatsapp_case(db_session, workspace_id):
    connection = WhatsAppConnection(
        workspace_id=workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id=f"pn-{uuid4().hex[:8]}",
        status=WaConnectionStatus.connected,
    )
    client = Client(workspace_id=workspace_id, name="Cliente Audio")
    db_session.add_all([connection, client])
    await db_session.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client.id,
        demand_type="Audio inbound",
    )
    db_session.add(matter)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511999990999",
        name="Cliente Audio",
        matter_id=matter.id,
    )
    db_session.add(contact)
    await db_session.flush()
    return client, matter, contact


@pytest.mark.asyncio
async def test_whatsapp_audio_stream_converges_to_storage_document_and_replays_safely(
    db_session,
    auth_context,
    monkeypatch,
):
    bound_client, matter, contact = await _whatsapp_case(
        db_session,
        auth_context.workspace_id,
    )
    _FakeMetaClient.reset()
    monkeypatch.setattr(httpx, "AsyncClient", _FakeMetaClient)
    monkeypatch.setattr(
        "app.core.klm_runtime.external_delivery_allowed",
        lambda: True,
    )
    contact.matter_id = None
    await db_session.flush()

    media_id = "100000000000001"
    key = await upload_media_to_s3(
        media_id=media_id,
        media_type="audio",
        contact_id=contact.id,
        matter_id=None,
        db=db_session,
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
    )
    assert key is not None
    replay_key = await upload_media_to_s3(
        media_id=media_id,
        media_type="audio",
        contact_id=contact.id,
        matter_id=None,
        db=db_session,
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
    )
    assert replay_key == key
    assert _FakeMetaClient.get_calls == 1
    assert _FakeMetaClient.stream_calls == 1

    expected_object_id = uuid5(
        auth_context.workspace_id,
        f"whatsapp-media:{media_id}",
    )
    stored = await db_session.scalar(
        select(StorageObject).where(StorageObject.object_id == expected_object_id)
    )
    assert stored is not None
    assert stored.state is StorageObjectState.available
    assert stored.storage_key == key
    assert stored.byte_size == len(OGG)
    assert stored.checksum_sha256 == hashlib.sha256(OGG).hexdigest()
    assert stored.created_by_user_id == auth_context.user_id
    assert stored.matter_id is None
    assert stored.client_id is None

    contact.matter_id = matter.id
    await db_session.flush()
    media_type = await _ensure_whatsapp_media_document(
        db=db_session,
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        media_s3_key=key,
        media_type="document",
    )
    assert media_type == "audio"
    await db_session.refresh(stored)
    assert stored.matter_id == matter.id
    assert stored.client_id == bound_client.id
    document = await db_session.get(Document, expected_object_id)
    assert document is not None
    assert document.status is DocumentStatus.completo
    assert document.s3_key == stored.storage_key
    assert document.mime_type == "audio/ogg"


@pytest.mark.asyncio
async def test_whatsapp_inbound_aws_media_enters_quarantine_and_replays_without_refetch(
    db_session,
    auth_context,
    monkeypatch,
):
    _, matter, contact = await _whatsapp_case(db_session, auth_context.workspace_id)
    _FakeMetaClient.reset()
    monkeypatch.setattr(httpx, "AsyncClient", _FakeMetaClient)
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "production")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", True)
    monkeypatch.setattr(settings, "KLM_PRIVATE_AWS_STORAGE_ENABLED", True)
    monkeypatch.setattr(settings, "S3_KMS_KEY_ID", "alias/test-private-storage")

    uploaded: dict[str, object] = {}

    class FakeS3:
        def upload_authorized_private_file(self, handle, key, **kwargs):
            uploaded.update(
                key=key,
                payload=handle.read(),
                checksum=kwargs["checksum_sha256"],
            )

    class FakePrivateStorage:
        def __init__(self, db):
            self.db = db
            self.s3 = FakeS3()

        def require_remote_runtime(self):
            return None

        def verify_private_bucket(self):
            return None

        async def create_upload_authorization(self, **kwargs):
            assert kwargs["idempotency_key"] == "whatsapp-inbound:100000000000002"
            version_id = uuid4()
            record = StorageObject(
                id=version_id,
                workspace_id=kwargs["workspace_id"],
                object_id=kwargs["object_id"],
                version=1,
                matter_id=kwargs["matter_id"],
                client_id=kwargs["client_id"],
                original_filename=kwargs["filename"],
                declared_content_type=kwargs["content_type"],
                expected_byte_size=kwargs["byte_size"],
                expected_sha256=kwargs["checksum_sha256"],
                storage_key=f"tenants/{kwargs['workspace_id'].hex}/clean/{version_id.hex}",
                quarantine_key=f"quarantine/{version_id.hex}",
                state=StorageObjectState.pending,
                av_status=StorageAVStatus.pending,
                created_by_user_id=kwargs["actor_user_id"],
            )
            self.db.add(record)
            await self.db.flush()
            return SimpleNamespace(
                storage_object=record,
                upload_required=True,
            )

        async def confirm_upload(self, **kwargs):
            record = await self.db.scalar(
                select(StorageObject).where(
                    StorageObject.workspace_id == kwargs["workspace_id"],
                    StorageObject.object_id == kwargs["object_id"],
                )
            )
            record.byte_size = len(OGG)
            record.checksum_sha256 = hashlib.sha256(OGG).hexdigest()
            record.detected_content_type = "audio/ogg"
            record.uploaded_at = datetime.now(UTC)
            record.state = StorageObjectState.quarantined
            record.av_status = StorageAVStatus.pending
            await self.db.flush()
            return record

    monkeypatch.setattr(
        "app.services.private_document_storage_service.PrivateDocumentStorageService",
        FakePrivateStorage,
    )

    first = await upload_media_to_s3(
        media_id="100000000000002",
        media_type="audio",
        contact_id=contact.id,
        matter_id=matter.id,
        db=db_session,
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
    )
    replay = await upload_media_to_s3(
        media_id="100000000000002",
        media_type="audio",
        contact_id=contact.id,
        matter_id=matter.id,
        db=db_session,
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
    )

    assert first == replay
    assert _FakeMetaClient.get_calls == 1
    assert _FakeMetaClient.stream_calls == 1
    assert uploaded["payload"] == OGG
    assert uploaded["checksum"] == hashlib.sha256(OGG).hexdigest()
    record = await db_session.scalar(
        select(StorageObject).where(StorageObject.storage_key == first)
    )
    assert record is not None
    assert record.state is StorageObjectState.quarantined
    assert record.av_status is StorageAVStatus.pending

    pending_resolution = await resolve_inbound_media_storage(
        db=db_session,
        workspace_id=auth_context.workspace_id,
        candidate_reference=record.storage_key,
    )
    assert pending_resolution.state is InboundMediaStorageState.retry_required
    assert pending_resolution.retryable is True
    assert pending_resolution.reconciliation_required is True

    record.av_status = StorageAVStatus.infected
    await db_session.flush()
    infected_resolution = await resolve_inbound_media_storage(
        db=db_session,
        workspace_id=auth_context.workspace_id,
        candidate_reference=record.storage_key,
    )
    assert infected_resolution.state is InboundMediaStorageState.unavailable
    assert infected_resolution.storage_key is None
    assert infected_resolution.retryable is False


@pytest.mark.asyncio
async def test_generated_document_delivery_waits_for_clean_then_sends_once(
    db_session,
    auth_context,
    monkeypatch,
):
    _, matter, contact = await _whatsapp_case(db_session, auth_context.workspace_id)
    storage = StorageObject(
        workspace_id=auth_context.workspace_id,
        object_id=uuid4(),
        version=1,
        matter_id=matter.id,
        client_id=matter.client_id,
        original_filename="proposta.pdf",
        declared_content_type="application/pdf",
        detected_content_type="application/pdf",
        expected_byte_size=len(PDF),
        byte_size=len(PDF),
        expected_sha256=hashlib.sha256(PDF).hexdigest(),
        checksum_sha256=hashlib.sha256(PDF).hexdigest(),
        storage_key="tenants/workspace/objects/proposal/clean",
        quarantine_key="/".join(("quarantine", "proposal")),
        state=StorageObjectState.quarantined,
        av_status=StorageAVStatus.pending,
        uploaded_at=datetime.now(UTC),
        created_by_user_id=auth_context.user_id,
    )
    db_session.add(storage)
    await db_session.flush()
    service = GeneratedDocumentDeliveryService(db_session)
    queued = await service.queue_after_clean(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        storage_object=storage,
        contact_id=contact.id,
        connection_id=contact.whatsapp_connection_id,
        filename="proposta.pdf",
        caption="Segue a proposta.",
        idempotency_key="test-key",
        kind="proposal",
        matter_id=matter.id,
    )
    assert queued.created is True
    assert await service.dispatch_intent(queued.intent.id) == "awaiting_av"

    sent: list[dict[str, object]] = []

    async def authorize_download(_self, **_kwargs):
        return SimpleNamespace(download_url="https://signed.invalid/proposal?ttl=300")

    class FakeMessageService:
        async def send_storage_document(
            self,
            payload,
            sent_by_id,
            workspace_id,
            **kwargs,
        ):
            sent.append(
                {
                    "url": payload.url,
                    "key": payload.idempotency_key,
                    "sentBy": sent_by_id,
                    "workspaceId": workspace_id,
                    **kwargs,
                }
            )
            return SimpleNamespace(status=WaMessageStatus.sent, messageId=uuid4())

    class FakeWhatsAppService:
        def __init__(self, _db):
            self.message_service = FakeMessageService()

    monkeypatch.setattr(
        "app.services.generated_document_delivery_service.aws_private_storage_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.generated_document_delivery_service.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.private_document_storage_service.PrivateDocumentStorageService.create_download_authorization",
        authorize_download,
    )
    monkeypatch.setattr(
        "app.services.whatsapp_service.WhatsAppService",
        FakeWhatsAppService,
    )
    storage.state = StorageObjectState.available
    storage.av_status = StorageAVStatus.clean
    storage.quarantine_key = None
    await db_session.flush()

    assert (
        await service.dispatch_due(
            workspace_id=auth_context.workspace_id,
            limit=10,
        )
        == 1
    )
    assert len(sent) == 1
    assert sent[0]["url"] == "https://signed.invalid/proposal?ttl=300"
    assert sent[0]["storage_key"] == storage.storage_key
    assert sent[0]["storage_object_version_id"] == storage.id
    assert "signed.invalid" not in str(queued.intent.payload)
    assert queued.intent.payload["state"] == "sent"
    await db_session.refresh(matter)
    assert matter.micro_status.value == "proposta_enviada"

    replay = await service.queue_after_clean(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        storage_object=storage,
        contact_id=contact.id,
        connection_id=contact.whatsapp_connection_id,
        filename="proposta.pdf",
        caption="Segue a proposta.",
        idempotency_key="test-key",
        kind="proposal",
        matter_id=matter.id,
    )
    assert replay.created is False
    assert await service.dispatch_intent(replay.intent.id) == "sent"
    assert len(sent) == 1
    delivery_events = list(
        (
            await db_session.scalars(
                select(TimelineEvent).where(TimelineEvent.matter_id == matter.id)
            )
        ).all()
    )
    assert sum(
        event.event_data.get("delivery_intent_id") == str(queued.intent.id)
        for event in delivery_events
    ) == 1
    assert await db_session.get(ExternalActionIntent, queued.intent.id) is not None
    assert (
        await db_session.scalar(
            select(KlmAuditEvent.id).where(
                KlmAuditEvent.entity_id == queued.intent.id,
                KlmAuditEvent.event_type == "generated_document_delivery_sent",
            )
        )
        is not None
    )


@pytest.mark.asyncio
async def test_generated_document_delivery_preserves_ambiguous_provider_state(
    db_session,
    auth_context,
    monkeypatch,
):
    _, matter, contact = await _whatsapp_case(db_session, auth_context.workspace_id)
    storage = StorageObject(
        workspace_id=auth_context.workspace_id,
        object_id=uuid4(),
        version=1,
        matter_id=matter.id,
        client_id=matter.client_id,
        original_filename="ambiguous.pdf",
        declared_content_type="application/pdf",
        detected_content_type="application/pdf",
        expected_byte_size=len(PDF),
        byte_size=len(PDF),
        expected_sha256=hashlib.sha256(PDF).hexdigest(),
        checksum_sha256=hashlib.sha256(PDF).hexdigest(),
        storage_key="tenants/test-workspace/objects/test-document/clean",
        quarantine_key=None,
        state=StorageObjectState.available,
        av_status=StorageAVStatus.clean,
        uploaded_at=datetime.now(UTC),
        created_by_user_id=auth_context.user_id,
    )
    db_session.add(storage)
    await db_session.flush()
    service = GeneratedDocumentDeliveryService(db_session)
    queued = await service.queue_after_clean(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        storage_object=storage,
        contact_id=contact.id,
        connection_id=contact.whatsapp_connection_id,
        filename="ambiguous.pdf",
        caption="Documento sintético de teste.",
        idempotency_key="test-generated-delivery-ambiguous",
        kind="proposal",
        matter_id=matter.id,
    )

    async def authorize_download(_self, **_kwargs):
        return SimpleNamespace(download_url="https://example.invalid/test-document")

    class AmbiguousMessageService:
        async def send_storage_document(self, *_args, **_kwargs):
            provider_error = WhatsAppProviderError(
                code="WA-TEST-AMBIGUOUS",
                message="Synthetic ambiguous provider result.",
                status_code=502,
                request_started=True,
                delivery_outcome_known=False,
            )
            raise provider_error.to_http_exception() from provider_error

    class FakeWhatsAppService:
        def __init__(self, _db):
            self.message_service = AmbiguousMessageService()

    monkeypatch.setattr(
        "app.services.generated_document_delivery_service.aws_private_storage_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.generated_document_delivery_service.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.private_document_storage_service.PrivateDocumentStorageService.create_download_authorization",
        authorize_download,
    )
    monkeypatch.setattr(
        "app.services.whatsapp_service.WhatsAppService",
        FakeWhatsAppService,
    )

    assert await service.dispatch_intent(queued.intent.id) == "delivery_uncertain"
    await db_session.refresh(queued.intent)
    assert queued.intent.payload["state"] == "delivery_uncertain"
    assert queued.intent.payload["lastErrorCode"] == "WA-TEST-AMBIGUOUS"


@pytest.mark.asyncio
async def test_whatsapp_media_helper_fails_closed_without_tenant_context(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.core.klm_runtime.external_delivery_allowed",
        lambda: True,
    )
    def forbidden_client(*_args, **_kwargs):
        raise AssertionError("HTTP must not start without tenant context")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden_client)

    with pytest.raises(LocalObjectUnavailableError):
        await upload_media_to_s3(
            media_id="100000000000003",
            media_type="audio",
            contact_id=uuid4(),
            matter_id=None,
        )


@pytest.mark.asyncio
async def test_whatsapp_stream_size_cap_cleans_staging_without_storage_ghost(
    db_session,
    auth_context,
    monkeypatch,
):
    _, matter, contact = await _whatsapp_case(db_session, auth_context.workspace_id)
    oversized = b"OggS" + b"x" * 32
    _FakeMetaClient.reset(payload=oversized, include_length=False)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeMetaClient)
    monkeypatch.setattr(settings, "LOCAL_STORAGE_MAX_BYTES", 8)
    monkeypatch.setattr(
        "app.core.klm_runtime.external_delivery_allowed",
        lambda: True,
    )

    result = await upload_media_to_s3(
        media_id="100000000000004",
        media_type="audio",
        contact_id=contact.id,
        matter_id=matter.id,
        db=db_session,
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
    )
    assert result is None
    assert (
        await db_session.scalar(
            select(StorageObject).where(
                StorageObject.workspace_id == auth_context.workspace_id
            )
        )
        is None
    )
    assert list(S3Service.iter_local_objects("staging")) == []


@pytest.mark.asyncio
async def test_whatsapp_generated_pdf_uses_versioned_storage_and_media_ticket(
    db_session,
    auth_context,
):
    bound_client, matter, _ = await _whatsapp_case(
        db_session,
        auth_context.workspace_id,
    )
    service = WhatsAppInboxService(
        db_session,
        message_service=SimpleNamespace(),
        template_service=SimpleNamespace(),
    )
    generated = await service._upload_generated_document(
        auth_context.workspace_id,
        matter.id,
        "proposta.pdf",
        PDF,
        auth_context.user_id,
        doc_type=DocumentType.outro,
        mime_type="application/pdf",
    )
    assert generated.delivery_state == "immediate"
    assert generated.download_url is not None
    assert "/api/v1/local-storage/media-download?ticket=" in generated.download_url
    ticket = generated.download_url.split("ticket=", 1)[1]
    claims = S3Service.decode_local_object_ticket(ticket, operation="media_download")
    assert claims["purpose"] == "media_delivery"

    stored = await db_session.scalar(
        select(StorageObject).where(
            StorageObject.object_id == claims["object_id"]
        )
    )
    assert stored is not None
    assert stored.state is StorageObjectState.available
    assert stored.byte_size == len(PDF)
    assert stored.checksum_sha256 == hashlib.sha256(PDF).hexdigest()
    assert stored.created_by_user_id == auth_context.user_id
    assert stored.matter_id == matter.id
    assert stored.client_id == bound_client.id
    document = await db_session.get(Document, stored.object_id)
    assert document is not None
    assert document.status is DocumentStatus.completo


@pytest.mark.asyncio
async def test_whatsapp_send_media_rejects_arbitrary_url_bypass(
    db_session,
    auth_context,
):
    _, _, contact = await _whatsapp_case(db_session, auth_context.workspace_id)
    service = WhatsAppInboxService(
        db_session,
        message_service=SimpleNamespace(),
        template_service=SimpleNamespace(),
    )

    with pytest.raises(HTTPException) as error:
        await service.send_media(
            contact.id,
            auth_context.workspace_id,
            ConversationDocumentSendRequest(
                url="https://attacker.example/private.pdf",
                filename="private.pdf",
            ),
            auth_context.user_id,
            "test-media-arbitrary-url-bypass",
        )

    assert error.value.status_code == 400
    assert error.value.detail["code"] == "WA-DOC-004"


@pytest.mark.asyncio
async def test_whatsapp_send_media_uses_storage_version_not_signed_url_as_identity(
    db_session,
    auth_context,
    monkeypatch,
):
    _, matter, contact = await _whatsapp_case(db_session, auth_context.workspace_id)
    document = Document(
        matter_id=matter.id,
        created_by=auth_context.user_id,
        name="contrato.pdf",
        doc_type=DocumentType.contrato,
        status=DocumentStatus.completo,
        s3_key="tenants/test/objects/contract/clean",
        mime_type="application/pdf",
    )
    db_session.add(document)
    await db_session.flush()
    storage_version = SimpleNamespace(
        id=uuid4(),
        storage_key="tenants/test/objects/contract/clean",
    )
    calls: list[dict[str, object]] = []

    async def authorize(_self, document_id, workspace_id):
        assert document_id == document.id
        assert workspace_id == auth_context.workspace_id
        return "https://example.invalid/test-signed-ticket", storage_version

    class FakeMessageService:
        async def send_storage_document(
            self,
            payload,
            sent_by_id,
            workspace_id,
            **kwargs,
        ):
            calls.append(
                {
                    "payload": payload,
                    "sentBy": sent_by_id,
                    "workspaceId": workspace_id,
                    **kwargs,
                }
            )
            return SimpleNamespace(status=WaMessageStatus.sent, messageId=uuid4())

        async def get_conversation(self, contact_id, workspace_id):
            return SimpleNamespace(contact_id=contact_id, workspace_id=workspace_id)

    monkeypatch.setattr(
        "app.services.whatsapp.inbox_service.DocumentService.create_media_delivery_authorization",
        authorize,
    )
    service = WhatsAppInboxService(
        db_session,
        message_service=FakeMessageService(),
        template_service=SimpleNamespace(),
    )

    await service.send_media(
        contact.id,
        auth_context.workspace_id,
        ConversationDocumentSendRequest(
            document_id=document.id,
            caption="Documento sintético de teste.",
        ),
        auth_context.user_id,
        "test-storage-media-message",
    )

    assert len(calls) == 1
    assert calls[0]["storage_object_version_id"] == storage_version.id
    assert calls[0]["storage_key"] == storage_version.storage_key
    assert calls[0]["payload"].idempotency_key == "test-storage-media-message"
    assert calls[0]["payload"].url == "https://example.invalid/test-signed-ticket"


@pytest.mark.asyncio
async def test_contract_timeout_is_persisted_and_never_replayed_to_provider(
    db_session,
    auth_context,
    monkeypatch,
):
    _, matter, contact = await _whatsapp_case(db_session, auth_context.workspace_id)
    provider_calls = 0

    async def allow_feature(_self, **_kwargs):
        return None

    class FakeTemplateService:
        async def get_template_by_id(self, _workspace, _template_id):
            return SimpleNamespace(
                id="test-contract-template",
                kind="contract",
                body="Contrato sintético para {client_name}",
                title="Contrato sintético",
                footer=None,
            )

        @staticmethod
        def render_template(body, _context):
            return body.replace("{client_name}", "Cliente de teste")

    class AmbiguousZapSign:
        async def create_document(self, **_kwargs):
            nonlocal provider_calls
            provider_calls += 1
            raise TimeoutError("synthetic provider timeout")

    async def zapsign_factory(**_kwargs):
        return AmbiguousZapSign()

    monkeypatch.setattr(
        "app.services.whatsapp.inbox_service.EntitlementEnforcementService.enforce_feature",
        allow_feature,
    )
    monkeypatch.setattr(
        "app.services.whatsapp.inbox_service.get_zapsign_service",
        zapsign_factory,
    )
    service = WhatsAppInboxService(
        db_session,
        message_service=SimpleNamespace(),
        template_service=FakeTemplateService(),
    )
    payload = SendContractRequest(template_id="test-contract-template")

    with pytest.raises(HTTPException) as first_error:
        await service.send_contract(
            contact.id,
            auth_context.workspace_id,
            payload,
            auth_context.user_id,
            "test-contract-provider-timeout",
        )
    assert first_error.value.status_code == 409
    assert first_error.value.detail["code"] == "WA-CON-DELIVERY-UNCERTAIN"

    intent = await db_session.scalar(
        select(ExternalActionIntent).where(
            ExternalActionIntent.workspace_id == auth_context.workspace_id,
            ExternalActionIntent.matter_id == matter.id,
            ExternalActionIntent.action_type == "zapsign_contract_delivery",
        )
    )
    assert intent is not None
    assert intent.payload["state"] == "delivery_uncertain"
    assert intent.payload["lastErrorCode"] == "TimeoutError"
    assert provider_calls == 1

    with pytest.raises(HTTPException) as replay_error:
        await service.send_contract(
            contact.id,
            auth_context.workspace_id,
            payload,
            auth_context.user_id,
            "test-contract-provider-timeout",
        )
    assert replay_error.value.status_code == 409
    assert replay_error.value.detail["code"] == "WA-CON-DELIVERY-UNCERTAIN"
    assert provider_calls == 1
