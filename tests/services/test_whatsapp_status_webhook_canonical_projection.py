"""V30: o status do provedor (entregue/lida/falha) chega à mensagem canônica da conversa,
com ou sem registro legado (a inbox envia sem WhatsAppMessage)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models.klm_operations import (
    MessageAuthorType,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
    OmnichannelThread,
)
from app.models.omnichannel_v6 import OmnichannelChannelConnection, OmnichannelMessageDeliveryEvent
from app.models.whatsapp import (
    ConnectionType,
    WaConnectionStatus,
    WaMessageDirection,
    WaMessageStatus,
    WhatsAppConnection,
    WhatsAppContact,
    WhatsAppMessage,
)
from app.services.whatsapp.campaign_service import whatsapp_delivery_status_event_key
from app.services.whatsapp.connection_service import WhatsAppConnectionService
from app.services.whatsapp.webhook_service import WhatsAppWebhookService


async def _connection(db_session, workspace_id):
    connection = WhatsAppConnection(
        workspace_id=workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn-v30",
        phone_number="5561999990000",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    await WhatsAppConnectionService(db_session).ensure_omnichannel_projection(connection)
    projection = await db_session.scalar(
        select(OmnichannelChannelConnection).where(
            OmnichannelChannelConnection.workspace_id == workspace_id,
            OmnichannelChannelConnection.connection_key == f"whatsapp:{connection.id}",
        )
    )
    assert projection is not None
    return connection, projection


async def _canonical_message(db_session, workspace_id, projection, wamid):
    thread = OmnichannelThread(
        workspace_id=workspace_id,
        primary_channel=OmnichannelChannel.whatsapp,
        subject="Conversa do titular",
    )
    db_session.add(thread)
    await db_session.flush()
    message = OmnichannelMessage(
        workspace_id=workspace_id,
        thread_id=thread.id,
        connection_id=projection.id,
        channel=OmnichannelChannel.whatsapp,
        direction=MessageDirection.outbound,
        author_type=MessageAuthorType.human,
        author_label="Kleist Advocacia",
        body="Teste de envio pela API oficial",
        idempotency_key=f"inbox-{wamid}",
        external_message_id=wamid,
        delivery_status=MessageDeliveryStatus.sent,
    )
    db_session.add(message)
    await db_session.flush()
    return message


def _service(db_session):
    return WhatsAppWebhookService(
        db_session,
        connection_service=AsyncMock(),
        message_service=AsyncMock(),
        automation_rule_service=AsyncMock(),
        campaign_service=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_delivered_status_reaches_canonical_message_without_legacy_record(
    db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(
        "app.services.whatsapp.webhook_service.external_delivery_allowed", lambda: True
    )
    connection, projection = await _connection(db_session, auth_context.workspace_id)
    message = await _canonical_message(
        db_session, auth_context.workspace_id, projection, "wamid-inbox-v30"
    )
    await db_session.commit()
    service = _service(db_session)

    payload = {"id": "wamid-inbox-v30", "status": "delivered", "timestamp": "1757000000"}
    await service._handle_status_update(connection.id, payload)
    await db_session.refresh(message)

    assert message.delivery_status == MessageDeliveryStatus.delivered
    service.campaign_service.sync_delivery_status.assert_awaited_once_with(
        "wamid-inbox-v30", "delivered", payload
    )

    # Replay do mesmo recibo (reentrega da Meta / retry do Celery): idempotente, sem erro.
    await service._handle_status_update(connection.id, payload)
    await db_session.refresh(message)
    assert message.delivery_status == MessageDeliveryStatus.delivered

    read_payload = {"id": "wamid-inbox-v30", "status": "read", "timestamp": "1757000060"}
    await service._handle_status_update(connection.id, read_payload)
    await db_session.refresh(message)
    assert message.delivery_status == MessageDeliveryStatus.read


@pytest.mark.asyncio
async def test_delivered_status_reaches_legacy_and_canonical_messages(
    db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(
        "app.services.whatsapp.webhook_service.external_delivery_allowed", lambda: True
    )
    connection, projection = await _connection(db_session, auth_context.workspace_id)
    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5561988880000",
        name="Contato V30",
    )
    db_session.add(contact)
    await db_session.flush()
    legacy = WhatsAppMessage(
        contact_id=contact.id,
        whatsapp_connection_id=connection.id,
        wamid="wamid-legacy-v30",
        direction=WaMessageDirection.outbound,
        status=WaMessageStatus.sent,
        body="Mensagem com registro legado",
    )
    db_session.add(legacy)
    canonical = await _canonical_message(
        db_session, auth_context.workspace_id, projection, "wamid-legacy-v30"
    )
    await db_session.commit()
    service = _service(db_session)

    await service._handle_status_update(
        connection.id,
        {"id": "wamid-legacy-v30", "status": "delivered", "timestamp": "1757000000"},
    )
    await db_session.refresh(legacy)
    await db_session.refresh(canonical)

    assert legacy.status == WaMessageStatus.delivered
    assert canonical.delivery_status == MessageDeliveryStatus.delivered


@pytest.mark.asyncio
async def test_status_for_unknown_message_is_ignored_without_error(
    db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(
        "app.services.whatsapp.webhook_service.external_delivery_allowed", lambda: True
    )
    connection, _projection = await _connection(db_session, auth_context.workspace_id)
    await db_session.commit()
    service = _service(db_session)

    await service._handle_status_update(
        connection.id, {"id": "wamid-desconhecido", "status": "delivered"}
    )
    service.campaign_service.sync_delivery_status.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_status_projects_error_code_to_canonical_message(
    db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(
        "app.services.whatsapp.webhook_service.external_delivery_allowed", lambda: True
    )
    connection, projection = await _connection(db_session, auth_context.workspace_id)
    message = await _canonical_message(
        db_session, auth_context.workspace_id, projection, "wamid-failed-v30"
    )
    await db_session.commit()
    service = _service(db_session)

    await service._handle_status_update(
        connection.id,
        {
            "id": "wamid-failed-v30",
            "status": "failed",
            "timestamp": "1757000000",
            "errors": [{"code": 131047, "title": "Re-engagement message"}],
        },
    )
    await db_session.refresh(message)
    assert message.delivery_status == MessageDeliveryStatus.failed
    payload = {
        "id": "wamid-failed-v30",
        "status": "failed",
        "timestamp": "1757000000",
        "errors": [{"code": 131047, "title": "Re-engagement message"}],
    }
    key = whatsapp_delivery_status_event_key("wamid-failed-v30", "failed", payload)
    event = await db_session.scalar(
        select(OmnichannelMessageDeliveryEvent).where(
            OmnichannelMessageDeliveryEvent.external_event_id == f"whatsapp-status:{key}"
        )
    )
    assert event is not None
    assert event.error_code == "131047"
    assert event.error_message == "Re-engagement message"


@pytest.mark.asyncio
async def test_closed_external_gate_does_not_project_canonical_status(
    db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(
        "app.services.whatsapp.webhook_service.external_delivery_allowed", lambda: False
    )
    connection, projection = await _connection(db_session, auth_context.workspace_id)
    message = await _canonical_message(
        db_session, auth_context.workspace_id, projection, "wamid-gate-v30"
    )
    await db_session.commit()
    service = _service(db_session)

    await service._handle_status_update(
        connection.id, {"id": "wamid-gate-v30", "status": "delivered", "timestamp": "1757000000"}
    )
    await db_session.refresh(message)
    assert message.delivery_status == MessageDeliveryStatus.sent
    service.campaign_service.sync_delivery_status.assert_awaited_once()


@pytest.mark.asyncio
async def test_campaign_receipt_already_projected_is_not_recorded_twice(
    db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(
        "app.services.whatsapp.webhook_service.external_delivery_allowed", lambda: True
    )
    connection, projection = await _connection(db_session, auth_context.workspace_id)
    message = await _canonical_message(
        db_session, auth_context.workspace_id, projection, "wamid-campaign-v30"
    )
    payload = {"id": "wamid-campaign-v30", "status": "delivered", "timestamp": "1757000000"}
    key = whatsapp_delivery_status_event_key("wamid-campaign-v30", "delivered", payload)
    now = datetime.now(UTC)
    db_session.add(
        OmnichannelMessageDeliveryEvent(
            workspace_id=auth_context.workspace_id,
            message_id=message.id,
            thread_id=message.thread_id,
            connection_id=projection.id,
            provider="meta_cloud",
            connection_key=f"whatsapp:{connection.id}",
            external_event_id=f"campaign-status:{key}",
            external_message_id="wamid-campaign-v30",
            provider_status="delivered",
            projected_status_before="sent",
            projected_status_after="delivered",
            applied=True,
            payload_sha256="0" * 64,
            occurred_at=now,
            received_at=now,
        )
    )
    await db_session.commit()
    service = _service(db_session)

    await service._handle_status_update(connection.id, payload)

    events = (
        await db_session.scalars(
            select(OmnichannelMessageDeliveryEvent).where(
                OmnichannelMessageDeliveryEvent.external_message_id == "wamid-campaign-v30"
            )
        )
    ).all()
    assert len(events) == 1
