from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.db.append_only import ImmutableRecordError
from app.models.klm_operations import (
    MessageAuthorType,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
    OmnichannelThread,
    ThreadStatus,
)
from app.models.omnichannel_v6 import (
    OmnichannelProviderWebhookEnvelope,
    OmnichannelProviderWebhookProcessingState,
)
from app.models.storage_object import StorageAVStatus, StorageObject, StorageObjectState
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.message_evidence_service import (
    MessageEvidenceService,
    ProviderEnvelopeFingerprintCollisionError,
    canonical_sha256,
)


async def _message(db_session, workspace_id):
    thread = OmnichannelThread(
        workspace_id=workspace_id,
        primary_channel=OmnichannelChannel.whatsapp,
        status=ThreadStatus.open,
        subject="Negativa de tratamento",
    )
    db_session.add(thread)
    await db_session.flush()
    message = OmnichannelMessage(
        workspace_id=workspace_id,
        thread_id=thread.id,
        channel=OmnichannelChannel.whatsapp,
        direction=MessageDirection.inbound,
        author_type=MessageAuthorType.client,
        author_label="Cliente",
        body="Preciso do medicamento hoje.",
        content_type="text",
        provider="meta_cloud",
        transport="official_api",
        external_message_id="wamid.evidence.1",
        idempotency_key="evidence-original-1",
        delivery_status=MessageDeliveryStatus.received,
        occurred_at=datetime(2026, 7, 21, 18, 30, tzinfo=UTC),
        message_metadata={"connectionKey": "whatsapp:test-connection"},
    )
    db_session.add(message)
    await db_session.flush()
    return thread, message


@pytest.mark.asyncio
async def test_original_edit_and_delete_remain_in_one_verified_chain(
    db_session,
    auth_context,
):
    thread, message = await _message(db_session, auth_context.workspace_id)
    service = MessageEvidenceService(db_session)

    original, duplicated = await service.record_projection_capture(message)
    assert duplicated is False
    edited, duplicated = await service.record_event(
        workspace_id=auth_context.workspace_id,
        provider="meta_cloud",
        connection_key="whatsapp:test-connection",
        external_message_id="wamid.evidence.1",
        event_type="edited",
        message_id=message.id,
        thread_id=thread.id,
        body_snapshot="Preciso do medicamento ainda hoje.",
        content_type="text",
        event_payload={"type": "text", "access_token": "must-not-leak"},
        provider_occurred_at=datetime(2026, 7, 21, 18, 31, tzinfo=UTC),
    )
    assert duplicated is False
    deleted, duplicated = await service.record_event(
        workspace_id=auth_context.workspace_id,
        provider="meta_cloud",
        connection_key="whatsapp:test-connection",
        external_message_id="wamid.evidence.1",
        event_type="deleted",
        observation_state="provider_signal",
        message_id=message.id,
        thread_id=thread.id,
        body_snapshot="Preciso do medicamento ainda hoje.",
        content_type="text",
        event_payload={"status": "deleted"},
        provider_occurred_at=datetime(2026, 7, 21, 18, 32, tzinfo=UTC),
    )
    assert duplicated is False
    await db_session.commit()

    chain = await service.read_chain(
        auth_context.workspace_id,
        thread.id,
        message.id,
    )
    assert chain["integrityStatus"] == "verified"
    assert chain["contentLifecycleState"] == "deleted"
    assert chain["eventCount"] == 3
    assert chain["contentRevisionCount"] == 3
    assert chain["originalSnapshot"]["bodySnapshot"] == "Preciso do medicamento hoje."
    assert [event["eventType"] for event in chain["events"]] == [
        "captured",
        "edited",
        "deleted",
    ]
    assert edited.event_payload["access_token"] == "[REDACTED]"
    assert deleted.previous_event_id == edited.id
    assert edited.previous_event_id == original.id

    await db_session.refresh(message)
    assert message.body == "Preciso do medicamento ainda hoje."
    assert message.content_lifecycle_state == "deleted"
    assert message.evidence_event_count == 3
    assert message.evidence_revision_count == 3
    assert message.evidence_chain_sha256 == deleted.evidence_sha256

    replay, duplicated = await service.record_event(
        workspace_id=auth_context.workspace_id,
        provider="meta_cloud",
        connection_key="whatsapp:test-connection",
        external_message_id="wamid.evidence.1",
        event_type="deleted",
        observation_state="provider_signal",
        message_id=message.id,
        thread_id=thread.id,
        body_snapshot="Preciso do medicamento ainda hoje.",
        content_type="text",
        event_payload={"status": "deleted"},
        provider_occurred_at=datetime(2026, 7, 21, 18, 32, tzinfo=UTC),
    )
    assert duplicated is True
    assert replay.id == deleted.id


@pytest.mark.asyncio
async def test_evidence_chain_uses_advisory_lock_without_mutation_privilege(
    db_session,
    auth_context,
    monkeypatch,
):
    lock = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "app.services.message_evidence_service.acquire_transaction_advisory_lock",
        lock,
    )
    _thread, message = await _message(db_session, auth_context.workspace_id)

    event, duplicated = await MessageEvidenceService(
        db_session
    ).record_projection_capture(message)

    assert duplicated is False
    assert lock.await_count == 2
    assert [call.kwargs for call in lock.await_args_list] == [
        {
            "namespace": "message-evidence-idempotency",
            "value": (
                f"{auth_context.workspace_id}:"
                f"{event.idempotency_key}"
            ),
        },
        {
            "namespace": "message-evidence-chain",
            "value": f"{auth_context.workspace_id}:{event.chain_id}",
        },
    ]


@pytest.mark.asyncio
async def test_exact_provider_envelope_is_append_only_and_manifest_is_self_verifying(
    db_session,
    auth_context,
):
    thread, message = await _message(db_session, auth_context.workspace_id)
    service = MessageEvidenceService(db_session)
    raw_payload = b'{"object":"whatsapp_business_account","entry":[]}'
    signature = "sha256=verified-provider-signature"
    envelope = await service.capture_provider_envelope(
        provider="meta_cloud",
        raw_payload=raw_payload,
        signature_header=signature,
        signature_scheme="x-hub-signature-256",
        signature_verified=True,
        request_metadata={"authorization": "must-not-leak", "path": "/webhook"},
    )
    await service.record_projection_capture(message)
    await service.record_event(
        workspace_id=auth_context.workspace_id,
        provider="meta_cloud",
        connection_key="whatsapp:test-connection",
        external_message_id="wamid.evidence.1",
        event_type="provider_receipt",
        message_id=message.id,
        thread_id=thread.id,
        envelope=envelope,
        body_snapshot=message.body,
        content_type="text",
        event_payload={"id": "wamid.evidence.1"},
    )
    await db_session.commit()

    stored = await db_session.scalar(select(OmnichannelProviderWebhookEnvelope))
    assert stored is not None
    assert stored.raw_payload == raw_payload
    assert stored.byte_size == len(raw_payload)
    assert stored.payload_sha256 == hashlib.sha256(raw_payload).hexdigest()
    assert stored.request_metadata["authorization"] == "[REDACTED]"

    manifest = await service.export_manifest(
        auth_context.workspace_id,
        thread.id,
        message.id,
    )
    claimed_hash = manifest.pop("manifestSha256")
    assert claimed_hash == canonical_sha256(manifest)
    assert manifest["rawProviderEnvelopeIncluded"] is False
    assert manifest["chain"]["events"][-1]["sourceEnvelopeSha256"] == envelope.payload_sha256

    stored.signature_header = "tampered"
    with pytest.raises(ImmutableRecordError, match="immutable"):
        await db_session.flush()


@pytest.mark.asyncio
async def test_exact_signed_redelivery_reuses_one_envelope_and_one_processing_state(
    db_session,
):
    service = MessageEvidenceService(db_session)
    raw_payload = b'{"object":"whatsapp_business_account","entry":[]}'
    signature = "sha256=exact-meta-signature"

    first = await service.capture_provider_envelope(
        provider="meta_cloud",
        raw_payload=raw_payload,
        signature_header=signature,
        signature_scheme="x-hub-signature-256",
        signature_verified=True,
        request_metadata={"path": "/first-request"},
    )
    replay = await service.capture_provider_envelope(
        provider="meta_cloud",
        raw_payload=raw_payload,
        signature_header=signature,
        signature_scheme="x-hub-signature-256",
        signature_verified=True,
        request_metadata={"path": "/meta-redelivery"},
    )

    assert first.deduplicated is False
    assert replay.deduplicated is True
    assert replay.id == first.id
    assert await db_session.scalar(
        select(func.count()).select_from(OmnichannelProviderWebhookEnvelope)
    ) == 1
    assert await db_session.scalar(
        select(func.count()).select_from(OmnichannelProviderWebhookProcessingState)
    ) == 1
    state = await db_session.get(OmnichannelProviderWebhookProcessingState, first.id)
    assert state is not None
    assert state.status == "queued"
    assert state.attempts == 0


@pytest.mark.asyncio
async def test_same_payload_with_different_signature_is_a_distinct_signed_delivery(
    db_session,
):
    service = MessageEvidenceService(db_session)
    raw_payload = b'{"object":"whatsapp_business_account","entry":[]}'

    first = await service.capture_provider_envelope(
        provider="meta_cloud",
        raw_payload=raw_payload,
        signature_header="sha256=signature-one",
        signature_scheme="x-hub-signature-256",
        signature_verified=True,
    )
    second = await service.capture_provider_envelope(
        provider="meta_cloud",
        raw_payload=raw_payload,
        signature_header="sha256=signature-two",
        signature_scheme="x-hub-signature-256",
        signature_verified=True,
    )

    assert first.id != second.id
    assert second.deduplicated is False
    assert await db_session.scalar(
        select(func.count()).select_from(OmnichannelProviderWebhookEnvelope)
    ) == 2
    assert await db_session.scalar(
        select(func.count()).select_from(OmnichannelProviderWebhookProcessingState)
    ) == 2


@pytest.mark.asyncio
async def test_delivery_fingerprint_collision_never_collapses_different_signed_bytes(
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.message_evidence_service.provider_delivery_fingerprint",
        lambda **_kwargs: "f" * 64,
    )
    service = MessageEvidenceService(db_session)
    first = await service.capture_provider_envelope(
        provider="meta_cloud",
        raw_payload=b'{"delivery":1}',
        signature_header="sha256=signature-one",
        signature_scheme="x-hub-signature-256",
        signature_verified=True,
    )

    with pytest.raises(
        ProviderEnvelopeFingerprintCollisionError,
        match="collision",
    ):
        await service.capture_provider_envelope(
            provider="meta_cloud",
            raw_payload=b'{"delivery":2}',
            signature_header="sha256=signature-two",
            signature_scheme="x-hub-signature-256",
            signature_verified=True,
        )

    stored = list(
        (await db_session.scalars(select(OmnichannelProviderWebhookEnvelope))).all()
    )
    assert [row.id for row in stored] == [first.id]


@pytest.mark.asyncio
async def test_database_trigger_blocks_direct_evidence_tampering(
    db_session,
    auth_context,
):
    _thread, message = await _message(db_session, auth_context.workspace_id)
    event, _ = await MessageEvidenceService(db_session).record_projection_capture(message)
    await db_session.commit()

    with pytest.raises(DBAPIError, match="immutable"):
        await db_session.execute(
            text(
                "UPDATE klm_omnichannel_message_evidence_events "
                "SET body_snapshot = 'adulterado' WHERE id = :event_id"
            ),
            {"event_id": str(event.id)},
        )
    await db_session.rollback()


@pytest.mark.asyncio
async def test_chain_cannot_be_read_through_another_workspace(
    db_session,
    auth_context,
):
    thread, message = await _message(db_session, auth_context.workspace_id)
    service = MessageEvidenceService(db_session)
    await service.record_projection_capture(message)
    outside = Workspace(
        name="Conta externa",
        slug=f"conta-externa-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(outside)
    await db_session.commit()

    with pytest.raises(LookupError, match="Mensagem não encontrada"):
        await service.read_chain(outside.id, thread.id, message.id)


@pytest.mark.asyncio
async def test_media_chain_exposes_exact_downloadable_object_version(
    db_session,
    auth_context,
):
    thread, message = await _message(db_session, auth_context.workspace_id)
    logical_object_id = uuid4()
    checksum = hashlib.sha256(b"original-audio-bytes").hexdigest()
    storage = StorageObject(
        workspace_id=auth_context.workspace_id,
        object_id=logical_object_id,
        version=3,
        original_filename="audio-cliente.ogg",
        declared_content_type="audio/ogg",
        detected_content_type="audio/ogg",
        expected_byte_size=20,
        byte_size=20,
        expected_sha256=checksum,
        checksum_sha256=checksum,
        storage_key=f"tenants/{auth_context.workspace_id}/audio/version-3",
        state=StorageObjectState.available,
        av_status=StorageAVStatus.clean,
    )
    db_session.add(storage)
    await db_session.flush()
    service = MessageEvidenceService(db_session)
    await service.record_projection_capture(message)
    await service.record_event(
        workspace_id=auth_context.workspace_id,
        provider="meta_cloud",
        connection_key="whatsapp:test-connection",
        external_message_id="wamid.evidence.1",
        event_type="media_captured",
        message_id=message.id,
        thread_id=thread.id,
        body_snapshot="[audio]",
        content_type="audio",
        event_payload={"mediaId": "meta-audio-1"},
        storage_object_id=storage.id,
        storage_object_version=storage.version,
        media_sha256=storage.checksum_sha256,
        media_byte_size=storage.byte_size,
        media_mime_type=storage.detected_content_type,
        media_filename=storage.original_filename,
        provider_media_id="meta-audio-1",
    )
    await db_session.commit()

    chain = await service.read_chain(
        auth_context.workspace_id,
        thread.id,
        message.id,
    )
    media = chain["events"][-1]["media"]
    assert media["storageObjectId"] == storage.id
    assert media["objectId"] == logical_object_id
    assert media["storageObjectVersion"] == 3
    assert media["sha256"] == checksum

    masked = await service.read_chain(
        auth_context.workspace_id,
        thread.id,
        message.id,
        include_sensitive=False,
    )
    assert masked["events"][-1]["media"]["objectId"] is None
