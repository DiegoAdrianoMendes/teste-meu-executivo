from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.models.klm_operations import (
    MessageAuthorType,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
    OmnichannelThread,
    ThreadStatus,
)
from app.models.omnichannel_v6 import OmnichannelChannelConnection
from app.models.whatsapp import ConnectionType, WaConnectionStatus, WhatsAppConnection
from app.services.omnichannel_delivery import ScheduledDispatchError, ScheduledDispatchRequest
from app.services.whatsapp.omnichannel_dispatcher import MetaCloudScheduledDispatcher
from app.services.whatsapp.provider_media_receipt_service import ProviderMediaReceiptService


class RecordingMetaClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def send_message_payload(self, **kwargs):
        self.calls.append(kwargs)
        return {"messages": [{"id": "wamid.official.1"}]}


async def _connections(db_session, workspace_id, *, transport_mode: str = "coexistence"):
    legacy = WhatsAppConnection(
        workspace_id=workspace_id,
        connection_type=ConnectionType.TECH_PROVIDER,
        transport_mode=transport_mode,
        phone_number_id=f"pn-{uuid4().hex[:8]}",
        display_phone_number="+55 11 99999-9999",
        status=WaConnectionStatus.connected,
        access_token_encrypted="test-token-placeholder",
    )
    db_session.add(legacy)
    await db_session.flush()
    native = OmnichannelChannelConnection(
        workspace_id=workspace_id,
        channel="whatsapp",
        provider="meta_cloud",
        connection_key=f"whatsapp:{legacy.id}",
        display_name="WhatsApp oficial",
        external_account_id=legacy.phone_number_id,
        transport_mode=transport_mode,
        status="connected",
        is_default=True,
        capabilities={},
        limits={},
        routing_policy={"serviceWindowHours": 24},
        cost_policy={"ledgerEnabled": True},
        health={"state": "connected"},
        configuration={
            "legacyConnectionId": str(legacy.id),
            "phoneNumberId": legacy.phone_number_id,
        },
        credential_reference=f"whatsapp-connection://{legacy.id}",
    )
    db_session.add(native)
    await db_session.commit()
    return legacy, native


def _request(
    native,
    *,
    payload: dict,
    expires_at: datetime | None,
    canonical_message_id=None,
):
    return ScheduledDispatchRequest(
        workspace_id=native.workspace_id,
        scheduled_message_id=uuid4(),
        canonical_message_id=canonical_message_id or uuid4(),
        connection_id=native.id,
        connection_key=native.connection_key,
        provider="meta_cloud",
        transport_mode=native.transport_mode,
        channel="whatsapp",
        endpoint_id=uuid4(),
        external_user_id="+55 (11) 98888-7777",
        address=None,
        body=payload.get("body"),
        template_id=payload.get("name") if payload.get("kind") == "template" else None,
        attachments=[],
        message_payload=payload,
        idempotency_key=f"schedule:{uuid4()}",
        service_window_expires_at=expires_at,
    )


async def _reply_messages(db_session, native, *, separate_threads: bool = False):
    target_thread = OmnichannelThread(
        workspace_id=native.workspace_id,
        primary_channel=OmnichannelChannel.whatsapp,
        status=ThreadStatus.open,
        subject="Atendimento WhatsApp",
    )
    canonical_thread = target_thread
    db_session.add(target_thread)
    if separate_threads:
        canonical_thread = OmnichannelThread(
            workspace_id=native.workspace_id,
            primary_channel=OmnichannelChannel.whatsapp,
            status=ThreadStatus.open,
            subject="Outra conversa WhatsApp",
        )
        db_session.add(canonical_thread)
    await db_session.flush()
    target = OmnichannelMessage(
        workspace_id=native.workspace_id,
        thread_id=target_thread.id,
        connection_id=native.id,
        channel=OmnichannelChannel.whatsapp,
        direction=MessageDirection.inbound,
        author_type=MessageAuthorType.client,
        author_label="Cliente",
        body="Mensagem original",
        external_message_id="wamid.provider.original.123",
        idempotency_key=f"reply-target:{uuid4()}",
        delivery_status=MessageDeliveryStatus.received,
    )
    canonical = OmnichannelMessage(
        workspace_id=native.workspace_id,
        thread_id=canonical_thread.id,
        connection_id=native.id,
        channel=OmnichannelChannel.whatsapp,
        direction=MessageDirection.outbound,
        author_type=MessageAuthorType.human,
        author_label="Atendente",
        body="Resposta",
        idempotency_key=f"reply-canonical:{uuid4()}",
        delivery_status=MessageDeliveryStatus.queued,
    )
    db_session.add_all([target, canonical])
    await db_session.commit()
    return target, canonical


@pytest.mark.asyncio
async def test_official_dispatcher_sends_freeform_inside_service_window(
    db_session, auth_context, monkeypatch
):
    legacy, native = await _connections(db_session, auth_context.workspace_id)
    meta = RecordingMetaClient()
    dispatcher = MetaCloudScheduledDispatcher(db_session, native, meta_client=meta)
    monkeypatch.setattr(dispatcher.connection_service, "get_meta_access_token", lambda _row: "token")

    receipt = await dispatcher.dispatch(
        _request(
            native,
            payload={"kind": "text", "body": "Podemos continuar por aqui."},
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    assert receipt.external_message_id == "wamid.official.1"
    assert receipt.payload["originSurface"] == "cloud_api"
    assert receipt.payload["transportMode"] == "coexistence"
    assert meta.calls[0]["phone_number_id"] == legacy.phone_number_id
    assert meta.calls[0]["to"] == "5511988887777"
    assert meta.calls[0]["message"]["type"] == "text"
    assert meta.calls[0]["opaque_callback_data"].startswith("klm:")


@pytest.mark.asyncio
async def test_official_dispatcher_resolves_local_reply_to_same_thread_wamid(
    db_session, auth_context, monkeypatch
):
    _legacy, native = await _connections(db_session, auth_context.workspace_id)
    target, canonical = await _reply_messages(db_session, native)
    meta = RecordingMetaClient()
    dispatcher = MetaCloudScheduledDispatcher(db_session, native, meta_client=meta)
    monkeypatch.setattr(dispatcher.connection_service, "get_meta_access_token", lambda _row: "token")

    await dispatcher.dispatch(
        _request(
            native,
            payload={
                "kind": "text",
                "body": "Respondendo com contexto oficial.",
                "collaboration": {"replyToMessageId": str(target.id)},
            },
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            canonical_message_id=canonical.id,
        )
    )

    assert meta.calls[0]["message"]["context"] == {
        "message_id": "wamid.provider.original.123"
    }


@pytest.mark.asyncio
async def test_official_dispatcher_rejects_reply_target_from_another_thread(
    db_session, auth_context, monkeypatch
):
    _legacy, native = await _connections(db_session, auth_context.workspace_id)
    target, canonical = await _reply_messages(db_session, native, separate_threads=True)
    meta = RecordingMetaClient()
    dispatcher = MetaCloudScheduledDispatcher(db_session, native, meta_client=meta)
    monkeypatch.setattr(dispatcher.connection_service, "get_meta_access_token", lambda _row: "token")

    with pytest.raises(ScheduledDispatchError) as caught:
        await dispatcher.dispatch(
            _request(
                native,
                payload={
                    "kind": "text",
                    "body": "Não deve sair.",
                    "collaboration": {"replyToMessageId": str(target.id)},
                },
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                canonical_message_id=canonical.id,
            )
        )

    assert caught.value.code == "whatsapp_reply_target_not_found"
    assert meta.calls == []


@pytest.mark.asyncio
async def test_official_dispatcher_requires_template_outside_service_window(
    db_session, auth_context, monkeypatch
):
    _legacy, native = await _connections(db_session, auth_context.workspace_id)
    meta = RecordingMetaClient()
    dispatcher = MetaCloudScheduledDispatcher(db_session, native, meta_client=meta)
    monkeypatch.setattr(dispatcher.connection_service, "get_meta_access_token", lambda _row: "token")

    with pytest.raises(ScheduledDispatchError) as caught:
        await dispatcher.dispatch(
            _request(
                native,
                payload={"kind": "text", "body": "Retomando o contato."},
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
    assert caught.value.code == "whatsapp_template_required"
    assert caught.value.retryable is False
    assert meta.calls == []

    receipt = await dispatcher.dispatch(
        _request(
            native,
            payload={
                "kind": "template",
                "name": "retomar_atendimento_juridico",
                "language": "pt_BR",
                "components": [],
            },
            expires_at=None,
        )
    )
    assert receipt.pricing_category == "template"
    assert meta.calls[0]["message"]["type"] == "template"


@pytest.mark.asyncio
async def test_official_dispatcher_rejects_cross_mode_projection(
    db_session, auth_context
):
    _legacy, native = await _connections(db_session, auth_context.workspace_id)
    native.transport_mode = "official_api"
    await db_session.commit()
    dispatcher = MetaCloudScheduledDispatcher(
        db_session,
        native,
        meta_client=RecordingMetaClient(),
    )

    with pytest.raises(ScheduledDispatchError) as caught:
        await dispatcher.dispatch(
            _request(
                native,
                payload={"kind": "text", "body": "Mensagem"},
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
    assert caught.value.code == "whatsapp_transport_mismatch"


@pytest.mark.asyncio
async def test_official_dispatcher_sends_provider_media_only_with_same_connection_receipt(
    db_session, auth_context, monkeypatch
):
    _legacy, native = await _connections(db_session, auth_context.workspace_id)
    upload_receipt = await ProviderMediaReceiptService(db_session).record_verified_upload(
        workspace_id=auth_context.workspace_id,
        connection_id=native.id,
        provider="meta_cloud",
        provider_media_id="meta-media-owned-1",
        media_type="image",
        source_sha256="a" * 64,
        source_size_bytes=1024,
        source_content_type="image/jpeg",
        provider_receipt={"id": "meta-media-owned-1"},
    )
    await db_session.commit()
    meta = RecordingMetaClient()
    dispatcher = MetaCloudScheduledDispatcher(db_session, native, meta_client=meta)
    monkeypatch.setattr(
        dispatcher.connection_service,
        "get_meta_access_token",
        lambda _row: "token",
    )

    await dispatcher.dispatch(
        _request(
            native,
            payload={
                "kind": "media",
                "type": "image",
                "providerMediaId": "meta-media-owned-1",
                "providerMediaReceiptId": str(upload_receipt.id),
            },
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    assert meta.calls[0]["message"] == {
        "type": "image",
        "image": {"id": "meta-media-owned-1"},
    }


@pytest.mark.asyncio
async def test_official_dispatcher_rejects_receipt_from_another_connection(
    db_session, auth_context
):
    _legacy, native = await _connections(db_session, auth_context.workspace_id)
    _other_legacy, other_native = await _connections(db_session, auth_context.workspace_id)
    foreign_receipt = await ProviderMediaReceiptService(db_session).record_verified_upload(
        workspace_id=auth_context.workspace_id,
        connection_id=other_native.id,
        provider="meta_cloud",
        provider_media_id="meta-media-other-route",
        media_type="document",
        source_sha256="b" * 64,
        source_size_bytes=2048,
        source_content_type="application/pdf",
        provider_receipt={"id": "meta-media-other-route"},
    )
    await db_session.commit()
    meta = RecordingMetaClient()
    dispatcher = MetaCloudScheduledDispatcher(db_session, native, meta_client=meta)

    with pytest.raises(ScheduledDispatchError) as caught:
        await dispatcher.dispatch(
            _request(
                native,
                payload={
                    "kind": "media",
                    "type": "document",
                    "providerMediaId": "meta-media-other-route",
                    "providerMediaReceiptId": str(foreign_receipt.id),
                },
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )

    assert caught.value.code == "provider_media_receipt_not_found"
    assert caught.value.retryable is False
    assert meta.calls == []


@pytest.mark.asyncio
async def test_official_dispatcher_verifies_nested_template_media_receipt(
    db_session,
    auth_context,
    monkeypatch,
):
    _legacy, native = await _connections(db_session, auth_context.workspace_id)
    upload_receipt = await ProviderMediaReceiptService(db_session).record_verified_upload(
        workspace_id=auth_context.workspace_id,
        connection_id=native.id,
        provider="meta_cloud",
        provider_media_id="meta-template-document",
        media_type="document",
        source_sha256="d" * 64,
        source_size_bytes=4096,
        source_content_type="application/pdf",
        provider_receipt={"id": "meta-template-document"},
    )
    await db_session.commit()
    meta = RecordingMetaClient()
    dispatcher = MetaCloudScheduledDispatcher(db_session, native, meta_client=meta)
    monkeypatch.setattr(
        dispatcher.connection_service,
        "get_meta_access_token",
        lambda _row: "token",
    )

    await dispatcher.dispatch(
        _request(
            native,
            payload={
                "kind": "template",
                "name": "proposta_juridica",
                "language": "pt_BR",
                "components": [
                    {
                        "type": "header",
                        "parameters": [
                            {
                                "type": "document",
                                "document": {
                                    "id": "meta-template-document",
                                    "filename": "proposta.pdf",
                                    "providerMediaReceiptId": str(upload_receipt.id),
                                },
                            }
                        ],
                    }
                ],
            },
            expires_at=None,
        )
    )

    assert meta.calls[0]["message"]["template"]["components"][0]["parameters"] == [
        {
            "type": "document",
            "document": {"id": "meta-template-document", "filename": "proposta.pdf"},
        }
    ]


@pytest.mark.asyncio
async def test_official_dispatcher_rejects_expired_nested_template_media_receipt(
    db_session,
    auth_context,
):
    _legacy, native = await _connections(db_session, auth_context.workspace_id)
    upload_receipt = await ProviderMediaReceiptService(db_session).record_verified_upload(
        workspace_id=auth_context.workspace_id,
        connection_id=native.id,
        provider="meta_cloud",
        provider_media_id="meta-template-expired",
        media_type="image",
        source_sha256="e" * 64,
        source_size_bytes=1024,
        source_content_type="image/jpeg",
        provider_receipt={"id": "meta-template-expired"},
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    await db_session.commit()
    meta = RecordingMetaClient()
    dispatcher = MetaCloudScheduledDispatcher(db_session, native, meta_client=meta)

    with pytest.raises(ScheduledDispatchError) as caught:
        await dispatcher.dispatch(
            _request(
                native,
                payload={
                    "kind": "template",
                    "name": "proposta_juridica",
                    "language": "pt_BR",
                    "components": [
                        {
                            "type": "header",
                            "parameters": [
                                {
                                    "type": "image",
                                    "image": {
                                        "id": "meta-template-expired",
                                        "providerMediaReceiptId": str(upload_receipt.id),
                                    },
                                }
                            ],
                        }
                    ],
                },
                expires_at=None,
            )
        )

    assert caught.value.code == "provider_media_receipt_expired"
    assert meta.calls == []
