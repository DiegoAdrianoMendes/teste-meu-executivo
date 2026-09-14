from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.main import app
from app.models.auth_security import AuthSecurityEvent
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.platform_inbox import (
    PlatformInboxConversation,
    PlatformInboxDeliveryStatus,
    PlatformInboxDirection,
    PlatformInboxMessage,
    PlatformInboxQueue,
)
from app.models.platform_official_whatsapp import (
    PlatformOfficialWhatsAppConnection,
    PlatformOfficialWhatsAppRole,
)
from app.models.whatsapp import ConnectionType, WaConnectionStatus
from app.services.platform_audit_event_service import PlatformAuditEventService
from app.services.platform_inbox_email_service import PlatformInboxEmailService
from app.services.platform_inbox_whatsapp_service import PlatformInboxWhatsAppService
from tests.conftest import TestSessionLocal

pytestmark = pytest.mark.asyncio


async def test_platform_inbox_mutations_publish_canonical_snapshot_contracts():
    operations = app.openapi()["paths"]
    mutation_paths = {
        "/api/v1/platform/inbox/conversations": ["post"],
        "/api/v1/platform/inbox/conversations/{conversation_id}": ["patch"],
        "/api/v1/platform/inbox/conversations/{conversation_id}/read": ["post"],
        "/api/v1/platform/inbox/conversations/{conversation_id}/messages": ["post"],
        "/api/v1/platform/inbox/conversations/{conversation_id}/create-lead": ["post"],
        "/api/v1/platform/inbox/conversations/{conversation_id}/create-ticket": ["post"],
    }
    for path, methods in mutation_paths.items():
        for method in methods:
            descriptor = operations[path][method]["x-platform-mutation"]
            assert descriptor["snapshotAdapter"] != "none", (path, method)
            assert descriptor["reasonPolicy"] == "system_allowed", (path, method)


async def test_platform_inbox_requires_blackbox_identity(client):
    response = await client.get("/api/v1/platform/inbox/conversations")
    assert response.status_code == 403


async def test_platform_inbox_conversation_cursor_is_stable_for_equal_timestamps(
    platform_client, db_session
):
    timestamp = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    for index in range(55):
        db_session.add(PlatformInboxConversation(
            channel="webchat",
            contact_name=f"Contato paginado {index:02d}",
            queue="sales",
            status="open",
            last_message_at=timestamp,
            last_message_preview=f"Mensagem {index:02d}",
        ))
    await db_session.commit()

    first = await platform_client.get("/api/v1/platform/inbox/conversations?limit=25")
    assert first.status_code == 200, first.text
    assert first.json()["count"] == 25
    assert first.json()["hasMore"] is True
    assert first.json()["nextCursor"]

    second = await platform_client.get(
        "/api/v1/platform/inbox/conversations?limit=25",
        params={"cursor": first.json()["nextCursor"]},
    )
    third = await platform_client.get(
        "/api/v1/platform/inbox/conversations?limit=25",
        params={"cursor": second.json()["nextCursor"]},
    )
    ids = [
        item["id"]
        for page in (first.json(), second.json(), third.json())
        for item in page["items"]
    ]
    assert len(ids) == 55
    assert len(set(ids)) == 55
    assert third.json()["hasMore"] is False

    invalid = await platform_client.get(
        "/api/v1/platform/inbox/conversations?cursor=not-a-cursor"
    )
    assert invalid.status_code == 422


async def test_platform_inbox_full_local_conversation_journey(platform_client, db_session, auth_context):
    key = f"inbox-create-{uuid4()}"
    created = await platform_client.post(
        "/api/v1/platform/inbox/conversations",
        headers={"Idempotency-Key": key},
        json={
            "channel": "whatsapp",
            "contactName": "Ana Prospect",
            "contactAddress": "+5561999990000",
            "subject": "Demonstração do Meu Executivo",
            "queue": "sales",
            "initialMessage": "Quero conhecer os planos para meu escritório.",
        },
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]
    assert created.json()["unreadCount"] == 1
    assert created.json()["messages"][0]["direction"] == "inbound"
    assert created.json()["messages"][0]["deliveryStatus"] == "received"
    assert created.json()["firstResponseDueAt"]
    assert created.json()["resolutionDueAt"]

    replay = await platform_client.post(
        "/api/v1/platform/inbox/conversations",
        headers={"Idempotency-Key": key},
        json={
            "channel": "whatsapp", "contactName": "Não substituir",
            "queue": "support", "initialMessage": "Replay idempotente",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == conversation_id
    assert await db_session.scalar(select(PlatformInboxConversation).where(PlatformInboxConversation.id == UUID(conversation_id)))

    listed = await platform_client.get(
        "/api/v1/platform/inbox/conversations?channel=whatsapp&queue=sales&status=open"
    )
    assert listed.status_code == 200
    assert listed.json()["count"] == 1

    detail = await platform_client.get(
        f"/api/v1/platform/inbox/conversations/{conversation_id}"
    )
    assert detail.status_code == 200
    assert detail.json()["unreadCount"] == 1
    marked_read = await platform_client.post(
        f"/api/v1/platform/inbox/conversations/{conversation_id}/read"
    )
    assert marked_read.status_code == 200
    assert marked_read.json()["unreadCount"] == 0

    public_reply = await platform_client.post(
        f"/api/v1/platform/inbox/conversations/{conversation_id}/messages",
        headers={"Idempotency-Key": f"inbox-public-{uuid4()}"},
        json={"body": "Olá, Ana. Vamos agendar uma demonstração?", "visibility": "public"},
    )
    assert public_reply.status_code == 201
    assert public_reply.json()["direction"] == "outbound"
    assert public_reply.json()["deliveryStatus"] == "not_configured"
    responded = await platform_client.get(
        f"/api/v1/platform/inbox/conversations/{conversation_id}"
    )
    assert responded.json()["firstRespondedAt"]

    internal_note = await platform_client.post(
        f"/api/v1/platform/inbox/conversations/{conversation_id}/messages",
        headers={"Idempotency-Key": f"inbox-note-{uuid4()}"},
        json={"body": "Prospect com potencial para plano Pro.", "visibility": "internal"},
    )
    assert internal_note.status_code == 201
    assert internal_note.json()["direction"] == "internal"
    assert internal_note.json()["deliveryStatus"] == "sent"

    patched = await platform_client.patch(
        f"/api/v1/platform/inbox/conversations/{conversation_id}",
        json={"status": "pending", "queue": "support"},
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "pending"
    assert patched.json()["queue"] == "support"

    resolved = await platform_client.patch(
        f"/api/v1/platform/inbox/conversations/{conversation_id}",
        json={"status": "resolved"},
    )
    assert resolved.json()["resolvedAt"]

    linked_workspace = await platform_client.patch(
        f"/api/v1/platform/inbox/conversations/{conversation_id}",
        json={"workspaceId": str(auth_context.workspace_id)},
    )
    assert linked_workspace.status_code == 200

    lead = await platform_client.post(
        f"/api/v1/platform/inbox/conversations/{conversation_id}/create-lead",
        json={"officeName": "Ana Advocacia"},
    )
    assert lead.status_code == 201, lead.text
    assert lead.json()["prospectId"]

    ticket = await platform_client.post(
        f"/api/v1/platform/inbox/conversations/{conversation_id}/create-ticket",
        headers={"Idempotency-Key": f"inbox-ticket-{uuid4()}"},
        json={"subject": "Atendimento originado na inbox"},
    )
    assert ticket.status_code == 201, ticket.text
    assert ticket.json()["supportTicketId"]

    invalid_link = await platform_client.patch(
        f"/api/v1/platform/inbox/conversations/{conversation_id}",
        json={"prospectId": str(uuid4())},
    )
    assert invalid_link.status_code == 422
    assert await db_session.scalar(select(PlatformInboxMessage).where(PlatformInboxMessage.conversation_id == UUID(conversation_id)))
    events = list(await db_session.scalars(select(AuthSecurityEvent).where(AuthSecurityEvent.event_type.like("platform_inbox_%"))))
    assert {event.event_type for event in events} == {
        "platform_inbox_conversation_created",
        "platform_inbox_conversation_updated",
        "platform_inbox_message_created",
        "platform_inbox_lead_created",
        "platform_inbox_ticket_created",
    }
    canonical = list(await db_session.scalars(
        select(PlatformAuditEvent)
        .where(
            PlatformAuditEvent.resource_type == "platform_inbox_conversation",
            PlatformAuditEvent.resource_id == conversation_id,
        )
        .order_by(PlatformAuditEvent.occurred_at.asc())
    ))
    actions = [event.action for event in canonical]
    assert actions.count("create_inbox_conversation") == 2
    assert "mark_inbox_conversation_read" in actions
    assert actions.count("update_inbox_conversation") == 3
    assert "create_lead_from_inbox" in actions
    assert "create_ticket_from_inbox" in actions
    assert canonical[0].before is None
    assert canonical[0].after["unreadCount"] == 1
    replay_event = canonical[1]
    assert replay_event.changed_paths == []
    message_events = list(await db_session.scalars(
        select(PlatformAuditEvent)
        .where(
            PlatformAuditEvent.resource_type == "platform_inbox_message",
            PlatformAuditEvent.action == "create_inbox_message",
        )
        .order_by(PlatformAuditEvent.occurred_at.asc())
    ))
    assert len(message_events) == 2
    assert {event.after["direction"] for event in message_events} == {"outbound", "internal"}
    assert all(event.before is None for event in message_events)


async def test_inbox_patch_rolls_back_when_canonical_audit_fails(
    platform_client, db_session, monkeypatch
):
    created = await platform_client.post(
        "/api/v1/platform/inbox/conversations",
        headers={"Idempotency-Key": f"inbox-atomic-{uuid4()}"},
        json={
            "channel": "webchat",
            "contactName": "Teste Atômico",
            "queue": "sales",
            "initialMessage": "Validar rollback de classificação.",
        },
    )
    conversation_id = UUID(created.json()["id"])

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(PlatformAuditEventService, "record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        await platform_client.patch(
            f"/api/v1/platform/inbox/conversations/{conversation_id}",
            json={"status": "resolved", "queue": "support"},
        )

    persisted = await db_session.get(PlatformInboxConversation, conversation_id)
    assert persisted is not None
    assert persisted.status.value == "open"
    assert persisted.queue.value == "sales"
    assert persisted.resolved_at is None


async def test_inbox_internal_message_rolls_back_when_canonical_audit_fails(
    platform_client, db_session, monkeypatch
):
    created = await platform_client.post(
        "/api/v1/platform/inbox/conversations",
        headers={"Idempotency-Key": f"inbox-message-atomic-{uuid4()}"},
        json={
            "channel": "webchat",
            "contactName": "Mensagem Atômica",
            "queue": "support",
            "initialMessage": "Validar rollback da nota interna.",
        },
    )
    conversation_id = UUID(created.json()["id"])

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(PlatformAuditEventService, "record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        await platform_client.post(
            f"/api/v1/platform/inbox/conversations/{conversation_id}/messages",
            headers={"Idempotency-Key": f"inbox-note-atomic-{uuid4()}"},
            json={"body": "Nota que deve ser revertida.", "visibility": "internal"},
        )

    messages = list(await db_session.scalars(
        select(PlatformInboxMessage)
        .where(PlatformInboxMessage.conversation_id == conversation_id)
        .order_by(PlatformInboxMessage.occurred_at.asc())
    ))
    assert len(messages) == 1
    assert messages[0].direction == PlatformInboxDirection.inbound


async def test_platform_inbox_metrics_do_not_replace_missing_data(platform_client):
    response = await platform_client.get("/api/v1/platform/inbox/metrics")
    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert response.json()["open"] == 0
    assert response.json()["unread"] == 0
    assert response.json()["updatedAt"]

    channels = await platform_client.get("/api/v1/platform/inbox/channels")
    assert channels.status_code == 200
    states = {item["channel"]: item for item in channels.json()}
    assert states["webchat"]["state"] == "available"
    assert states["instagram"]["state"] == "not_configured"
    assert states["messenger"]["outbound"] is False


async def test_official_whatsapp_webhook_is_ingested_and_delivery_receipts_reconciled(db_session):
    connection = PlatformOfficialWhatsAppConnection(
        role=PlatformOfficialWhatsAppRole.outbound,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        phone_number_id="phone-platform-1",
        is_active=True,
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    service = PlatformInboxWhatsAppService(db_session)
    inbound = {
        "entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "phone-platform-1"},
            "contacts": [{"wa_id": "5561999990000", "profile": {"name": "Contato Meta"}}],
            "messages": [{
                "id": "wamid.inbound.1", "from": "5561999990000",
                "timestamp": "1785192000", "type": "text",
                "text": {"body": "Mensagem oficial recebida."},
            }],
        }}]}],
    }
    result = await service.ingest(inbound)
    assert result == {"matched": True, "messages": 1, "statuses": 0}
    conversation = await db_session.scalar(select(PlatformInboxConversation).where(
        PlatformInboxConversation.external_thread_id == "phone-platform-1:5561999990000"
    ))
    assert conversation is not None
    assert conversation.queue.value == "sales"
    message = await db_session.scalar(select(PlatformInboxMessage).where(
        PlatformInboxMessage.external_message_id == "wamid.inbound.1"
    ))
    assert message is not None
    assert message.body == "Mensagem oficial recebida."
    operational_event = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_id == str(connection.id),
            PlatformAuditEvent.action == "meta_webhook_messages",
        )
    )
    assert operational_event is not None
    assert operational_event.after["payload"] == {
        "messageCount": 1,
        "echoCount": 0,
        "statusCount": 0,
        "contactCount": 1,
        "historyBatchCount": 0,
    }
    assert "Mensagem oficial recebida" not in json.dumps(operational_event.after)

    outbound = PlatformInboxMessage(
        conversation_id=conversation.id,
        direction=PlatformInboxDirection.outbound,
        body="Mensagem oficial enviada.",
        delivery_status=PlatformInboxDeliveryStatus.sent,
        actor_label="Equipe",
        external_message_id="wamid.outbound.1",
        idempotency_key="outbound-receipt-test",
    )
    db_session.add(outbound)
    await db_session.commit()
    receipt = {
        "entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "phone-platform-1"},
            "statuses": [{"id": "wamid.outbound.1", "status": "read"}],
        }}]}],
    }
    reconciled = await service.ingest(receipt)
    assert reconciled == {"matched": True, "messages": 0, "statuses": 1}
    await db_session.refresh(outbound)
    assert outbound.delivery_status == PlatformInboxDeliveryStatus.read


async def test_official_whatsapp_webhook_records_non_message_operational_signal(db_session):
    connection = PlatformOfficialWhatsAppConnection(
        role=PlatformOfficialWhatsAppRole.support,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        phone_number_id="phone-platform-quality",
        waba_id="waba-platform-quality",
        is_active=True,
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.commit()

    result = await PlatformInboxWhatsAppService(db_session).ingest(
        {
            "entry": [
                {
                    "id": "waba-platform-quality",
                    "changes": [
                        {
                            "field": "phone_number_quality_update",
                            "value": {
                                "metadata": {
                                    "phone_number_id": "phone-platform-quality"
                                },
                                "quality_rating": "YELLOW",
                            },
                        }
                    ],
                }
            ]
        }
    )

    assert result == {"matched": True, "messages": 0, "statuses": 0}
    event = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_id == str(connection.id),
            PlatformAuditEvent.action
            == "meta_webhook_phone_number_quality_update",
        )
    )
    assert event is not None
    assert event.after["field"] == "phone_number_quality_update"
    assert event.after["payload"]["quality_rating"] == "YELLOW"


async def test_official_whatsapp_webhook_merges_existing_brazilian_phone_aliases(db_session):
    connection = PlatformOfficialWhatsAppConnection(
        role=PlatformOfficialWhatsAppRole.outbound,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        phone_number_id="phone-platform-br-alias",
        is_active=True,
        status=WaConnectionStatus.connected,
    )
    canonical = PlatformInboxConversation(
        channel="whatsapp",
        external_thread_id="phone-platform-br-alias:5561982502136",
        contact_name="Kleist teste",
        contact_address="5561982502136",
        queue=PlatformInboxQueue.sales,
        status="open",
        unread_count=0,
    )
    legacy = PlatformInboxConversation(
        channel="whatsapp",
        external_thread_id="phone-platform-br-alias:556182502136",
        contact_name="Kleist Monteiro",
        contact_address="556182502136",
        queue=PlatformInboxQueue.sales,
        status="open",
        unread_count=1,
    )
    db_session.add_all([connection, canonical, legacy])
    await db_session.flush()
    db_session.add(
        PlatformInboxMessage(
            conversation_id=legacy.id,
            direction=PlatformInboxDirection.inbound,
            body="Mensagem anterior.",
            delivery_status=PlatformInboxDeliveryStatus.received,
            actor_label="Contato",
            external_message_id="wamid.br.alias.previous",
            idempotency_key="meta:wamid.br.alias.previous",
        )
    )
    await db_session.commit()

    result = await PlatformInboxWhatsAppService(db_session).ingest(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "phone-platform-br-alias"},
                                "contacts": [
                                    {
                                        "wa_id": "556182502136",
                                        "profile": {"name": "Kleist Monteiro"},
                                    }
                                ],
                                "messages": [
                                    {
                                        "id": "wamid.br.alias.current",
                                        "from": "556182502136",
                                        "timestamp": "1785192100",
                                        "type": "text",
                                        "text": {"body": "Oi"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ]
        }
    )

    assert result == {"matched": True, "messages": 1, "statuses": 0}
    assert await db_session.scalar(
        select(func.count()).select_from(PlatformInboxConversation).where(
            PlatformInboxConversation.channel == "whatsapp",
            PlatformInboxConversation.external_thread_id.in_(
                {
                    "phone-platform-br-alias:5561982502136",
                    "phone-platform-br-alias:556182502136",
                }
            ),
        )
    ) == 1
    assert await db_session.scalar(
        select(func.count()).select_from(PlatformInboxMessage).where(
            PlatformInboxMessage.conversation_id == canonical.id
        )
    ) == 2


async def test_platform_coexistence_echo_is_outbound_and_idempotent(db_session):
    connection = PlatformOfficialWhatsAppConnection(
        role=PlatformOfficialWhatsAppRole.support,
        connection_type=ConnectionType.TECH_PROVIDER,
        transport_mode="coexistence",
        phone_number_id="phone-platform-echo",
        is_active=True,
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.commit()
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{
            "field": "smb_message_echoes",
            "value": {
                "metadata": {"phone_number_id": "phone-platform-echo"},
                "message_echoes": [{
                    "id": "wamid.platform.echo.1",
                    "from": "551140000000",
                    "to": "+55 (61) 99999-0000",
                    "timestamp": "1785192000",
                    "type": "text",
                    "text": {"body": "Resposta pelo aplicativo."},
                }],
            },
        }]}],
    }

    service = PlatformInboxWhatsAppService(db_session)
    first = await service.ingest(payload)
    second = await service.ingest(payload)

    assert first == {"matched": True, "messages": 1, "statuses": 0}
    assert second == {"matched": True, "messages": 0, "statuses": 0}
    conversation = await db_session.scalar(
        select(PlatformInboxConversation).where(
            PlatformInboxConversation.external_thread_id
            == "phone-platform-echo:5561999990000"
        )
    )
    assert conversation is not None
    assert conversation.unread_count == 0
    assert conversation.first_response_due_at is None
    message = await db_session.scalar(
        select(PlatformInboxMessage).where(
            PlatformInboxMessage.external_message_id == "wamid.platform.echo.1"
        )
    )
    assert message is not None
    assert message.direction == PlatformInboxDirection.outbound
    assert message.metadata_json["sourceField"] == "smb_message_echoes"


async def test_platform_coexistence_history_updates_sync_without_live_sla(db_session):
    connection = PlatformOfficialWhatsAppConnection(
        role=PlatformOfficialWhatsAppRole.outbound,
        connection_type=ConnectionType.TECH_PROVIDER,
        transport_mode="coexistence",
        phone_number_id="phone-platform-history",
        display_phone_number="+55 11 4000-0000",
        coexistence_sync_status="requested",
        is_active=True,
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.commit()
    service = PlatformInboxWhatsAppService(db_session)

    contacts = await service.ingest({
        "event": "smb_app_state_sync",
        "data": {"metadata": {"phone_number_id": "phone-platform-history"}},
    })
    assert contacts["matched"] is True
    assert connection.coexistence_sync_status == "contacts_received"

    history = await service.ingest({
        "event": "history",
        "data": {
            "metadata": {
                "phone_number_id": "phone-platform-history",
                "display_phone_number": "+55 11 4000-0000",
            },
            "history": [{"threads": [{
                "id": "5561988880000",
                "messages": [
                    {
                        "id": "wamid.platform.history.in",
                        "from": "5561988880000",
                        "timestamp": "1785191000",
                        "type": "text",
                        "text": {"body": "Mensagem histórica recebida."},
                    },
                    {
                        "id": "wamid.platform.history.out",
                        "from": "551140000000",
                        "to": "5561988880000",
                        "timestamp": "1785192000",
                        "type": "text",
                        "text": {"body": "Mensagem histórica enviada."},
                    },
                ],
            }]}],
        },
    })

    assert history == {"matched": True, "messages": 2, "statuses": 0}
    assert connection.coexistence_sync_status == "completed"
    conversation = await db_session.scalar(
        select(PlatformInboxConversation).where(
            PlatformInboxConversation.external_thread_id
            == "phone-platform-history:5561988880000"
        )
    )
    assert conversation is not None
    assert conversation.unread_count == 0
    assert conversation.first_response_due_at is None
    messages = list(
        (
            await db_session.scalars(
                select(PlatformInboxMessage)
                .where(PlatformInboxMessage.conversation_id == conversation.id)
                .order_by(PlatformInboxMessage.occurred_at)
            )
        ).all()
    )
    assert [message.direction for message in messages] == [
        PlatformInboxDirection.inbound,
        PlatformInboxDirection.outbound,
    ]
    assert all(message.metadata_json["historySync"] for message in messages)


async def test_inbox_uses_official_whatsapp_transport_when_enabled(
    platform_client,
    monkeypatch,
):
    created = await platform_client.post(
        "/api/v1/platform/inbox/conversations",
        headers={"Idempotency-Key": f"transport-create-{uuid4()}"},
        json={
            "channel": "whatsapp", "contactName": "Contato oficial",
            "contactAddress": "5561999990000", "queue": "sales",
            "initialMessage": "Olá pelo canal oficial.",
        },
    )
    monkeypatch.setattr(
        "app.api.v1.endpoints.platform_inbox.external_delivery_allowed",
        lambda: True,
    )

    async def fake_send(
        self,
        *,
        role,
        to,
        body,
        idempotency_key,
        requested_by_user_id,
        source_type,
        source_id,
    ):
        assert role == PlatformOfficialWhatsAppRole.outbound
        assert to == "5561999990000"
        assert idempotency_key.startswith("transport-message-")
        assert requested_by_user_id is not None
        assert source_type == "platform_inbox"
        assert source_id == created.json()["id"]
        return True, "wamid.outbound.accepted"

    monkeypatch.setattr(
        "app.api.v1.endpoints.platform_inbox.PlatformOfficialWhatsAppService.send_text_result",
        fake_send,
    )
    response = await platform_client.post(
        f"/api/v1/platform/inbox/conversations/{created.json()['id']}/messages",
        headers={"Idempotency-Key": f"transport-message-{uuid4()}"},
        json={"body": "Resposta enviada pela Cloud API.", "visibility": "public"},
    )
    assert response.status_code == 201
    assert response.json()["deliveryStatus"] == "sent"
    assert response.json()["externalMessageId"] == "wamid.outbound.accepted"


async def test_public_meta_webhook_routes_platform_number_to_blackbox_inbox(
    client,
    db_session,
    monkeypatch,
):
    secret = "platform-meta-webhook-secret"
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", TestSessionLocal)
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", secret)
    connection = PlatformOfficialWhatsAppConnection(
        role=PlatformOfficialWhatsAppRole.support,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        phone_number_id="phone-platform-route",
        is_active=True,
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.commit()
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"field": "messages", "value": {
            "metadata": {"phone_number_id": "phone-platform-route"},
            "contacts": [{"wa_id": "5561988880000", "profile": {"name": "Cliente suporte"}}],
            "messages": [{
                "id": "wamid.platform.route.1",
                "from": "5561988880000",
                "timestamp": "1785192000",
                "type": "text",
                "text": {"body": "Preciso de ajuda com minha conta."},
            }],
        }}]}],
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    captured: dict[str, object] = {}

    class _DbSessionContext:
        def __init__(self, session):
            self._session = session

        async def __aenter__(self):
            return self._session

        async def __aexit__(self, *_exc):
            return None

    def _run_task_inline(*_args, **kwargs):
        from app.workers import whatsapp_webhook_tasks

        envelope_id = kwargs["kwargs"]["envelope_id"]
        captured["coro"] = whatsapp_webhook_tasks._async_process_inbound_webhook(
            envelope_id,
            session_factory=lambda: _DbSessionContext(db_session),
        )

    monkeypatch.setattr(
        "app.workers.whatsapp_webhook_tasks.process_inbound_webhook.apply_async",
        _run_task_inline,
    )

    response = await client.post(
        "/api/v1/whatsapp/webhook",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok"}
    outcome = await captured["coro"]
    assert outcome.status == "processed"
    conversation = await db_session.scalar(select(PlatformInboxConversation).where(
        PlatformInboxConversation.external_thread_id == "phone-platform-route:5561988880000"
    ))
    assert conversation is not None
    assert conversation.queue == PlatformInboxQueue.support


async def test_messenger_webhook_is_signed_idempotent_and_reaches_platform_inbox(
    client,
    db_session,
    monkeypatch,
):
    secret = "platform-social-app-secret"
    monkeypatch.setattr(settings, "META_APP_SECRET", secret)
    monkeypatch.setattr(settings, "PLATFORM_META_VERIFY_TOKEN", "verify-social")
    monkeypatch.setattr(settings, "PLATFORM_META_PAGE_ID", "page-123")
    monkeypatch.setattr(settings, "PLATFORM_META_PAGE_ACCESS_TOKEN", "page-token")
    verified = await client.get(
        "/api/v1/public/platform-meta/webhook?hub.mode=subscribe&hub.verify_token=verify-social&hub.challenge=777"
    )
    assert verified.status_code == 200
    assert verified.json() == 777

    payload = {
        "object": "page",
        "entry": [{
            "id": "page-123",
            "messaging": [{
                "sender": {"id": "psid-456"},
                "recipient": {"id": "page-123"},
                "timestamp": 1785192000000,
                "message": {"mid": "mid.social.1", "text": "Quero falar com vendas."},
            }],
        }],
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    first = await client.post(
        "/api/v1/public/platform-meta/webhook",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature},
    )
    replay = await client.post(
        "/api/v1/public/platform-meta/webhook",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature},
    )
    assert first.status_code == 200, first.text
    assert first.json() == {"matched": True, "channel": "messenger", "messages": 1}
    assert replay.json() == {"matched": True, "channel": "messenger", "messages": 0}
    conversation = await db_session.scalar(select(PlatformInboxConversation).where(
        PlatformInboxConversation.channel == "messenger",
        PlatformInboxConversation.external_thread_id == "page-123:psid-456",
    ))
    assert conversation is not None

    invalid = await client.post(
        "/api/v1/public/platform-meta/webhook",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=bad"},
    )
    assert invalid.status_code == 403


async def test_inbox_uses_meta_social_transport_when_enabled(platform_client, monkeypatch):
    monkeypatch.setattr(settings, "PLATFORM_INSTAGRAM_ACCOUNT_ID", "ig-account")
    monkeypatch.setattr(settings, "PLATFORM_INSTAGRAM_ACCESS_TOKEN", "ig-token")
    created = await platform_client.post(
        "/api/v1/platform/inbox/conversations",
        headers={"Idempotency-Key": f"ig-create-{uuid4()}"},
        json={
            "channel": "instagram", "contactName": "Contato Instagram",
            "contactAddress": "igsid-789", "queue": "sales",
            "initialMessage": "Mensagem recebida no Instagram.",
        },
    )
    monkeypatch.setattr(
        "app.api.v1.endpoints.platform_inbox.external_delivery_allowed",
        lambda: True,
    )

    async def fake_send(self, *, channel, recipient_id, body):
        assert channel.value == "instagram"
        assert recipient_id == "igsid-789"
        assert body == "Resposta oficial pelo Instagram."
        return True, "mid.instagram.outbound"

    monkeypatch.setattr(
        "app.api.v1.endpoints.platform_inbox.PlatformInboxMetaService.send_text",
        fake_send,
    )
    response = await platform_client.post(
        f"/api/v1/platform/inbox/conversations/{created.json()['id']}/messages",
        headers={"Idempotency-Key": f"ig-message-{uuid4()}"},
        json={"body": "Resposta oficial pelo Instagram.", "visibility": "public"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["deliveryStatus"] == "sent"
    assert response.json()["externalMessageId"] == "mid.instagram.outbound"

    channels = await platform_client.get("/api/v1/platform/inbox/channels")
    instagram = next(item for item in channels.json() if item["channel"] == "instagram")
    assert instagram["state"] == "credentials_configured"
    assert instagram["inbound"] is True


async def test_resend_webhook_verifies_svix_retrieves_body_and_deduplicates(
    client,
    db_session,
    monkeypatch,
):
    raw_secret = b"resend-test-signing-key-32-bytes"
    webhook_secret = "whsec_" + base64.b64encode(raw_secret).decode()
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(settings, "RESEND_WEBHOOK_SECRET", webhook_secret)
    monkeypatch.setattr(settings, "PLATFORM_EMAIL_SUPPORT_ADDRESS", "suporte@meuexecutivo.com")

    async def fake_received(self, email_id):
        assert email_id == "email-received-1"
        return {"text": "Preciso de ajuda com o acesso.", "subject": "Acesso bloqueado"}

    monkeypatch.setattr(
        "app.api.v1.endpoints.platform_inbox.PlatformInboxEmailService._received_email",
        fake_received,
    )
    payload = {
        "type": "email.received",
        "created_at": "2026-07-27T20:00:00Z",
        "data": {
            "email_id": "email-received-1",
            "created_at": "2026-07-27T20:00:00Z",
            "from": "cliente@example.com",
            "to": ["suporte@meuexecutivo.com"],
            "subject": "Acesso bloqueado",
            "message_id": "<mail-1@example.com>",
        },
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    svix_id = "msg_resend_1"
    timestamp = str(int(time.time()))
    signed = f"{svix_id}.{timestamp}.".encode() + body
    signature = "v1," + base64.b64encode(hmac.new(raw_secret, signed, hashlib.sha256).digest()).decode()
    headers = {
        "Content-Type": "application/json", "svix-id": svix_id,
        "svix-timestamp": timestamp, "svix-signature": signature,
    }
    first = await client.post("/api/v1/public/platform-email/webhook", content=body, headers=headers)
    replay = await client.post("/api/v1/public/platform-email/webhook", content=body, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json() == {"matched": True, "channel": "email_support", "messages": 1, "statuses": 0}
    assert replay.json() == {"matched": True, "channel": "email_support", "messages": 0, "statuses": 0}
    conversation = await db_session.scalar(select(PlatformInboxConversation).where(
        PlatformInboxConversation.external_thread_id == "suporte@meuexecutivo.com:cliente@example.com"
    ))
    assert conversation is not None
    assert conversation.queue == PlatformInboxQueue.support
    message = await db_session.scalar(select(PlatformInboxMessage).where(
        PlatformInboxMessage.external_message_id == "email-received-1"
    ))
    assert message is not None and message.body == "Preciso de ajuda com o acesso."

    outbound = PlatformInboxMessage(
        conversation_id=conversation.id,
        direction=PlatformInboxDirection.outbound,
        body="Resposta por e-mail.",
        delivery_status=PlatformInboxDeliveryStatus.sent,
        actor_label="Equipe",
        external_message_id="resend-delivery-1",
        idempotency_key="resend-delivery-local",
    )
    db_session.add(outbound)
    await db_session.commit()
    receipt = await PlatformInboxEmailService(db_session).ingest(
        {"type": "email.delivered", "data": {"email_id": "resend-delivery-1"}},
        svix_id="msg_delivery_1",
    )
    assert receipt == {"matched": True, "channel": "email_support", "messages": 0, "statuses": 1}
    await db_session.refresh(outbound)
    assert outbound.delivery_status == PlatformInboxDeliveryStatus.delivered

    rejected = await client.post(
        "/api/v1/public/platform-email/webhook",
        content=body,
        headers={**headers, "svix-signature": "v1,bad"},
    )
    assert rejected.status_code == 403


async def test_inbox_uses_resend_transport_when_enabled(platform_client, monkeypatch):
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(settings, "RESEND_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setattr(settings, "PLATFORM_EMAIL_SALES_ADDRESS", "vendas@meuexecutivo.com")
    created = await platform_client.post(
        "/api/v1/platform/inbox/conversations",
        headers={"Idempotency-Key": f"email-create-{uuid4()}"},
        json={
            "channel": "email_sales", "contactName": "Prospect por e-mail",
            "contactAddress": "prospect@example.com", "subject": "Plano Pro", "queue": "sales",
            "initialMessage": "Quero receber uma proposta.",
        },
    )
    monkeypatch.setattr("app.api.v1.endpoints.platform_inbox.external_delivery_allowed", lambda: True)

    async def fake_send(self, *, channel, recipient, subject, body, idempotency_key):
        assert channel.value == "email_sales"
        assert recipient == "prospect@example.com"
        assert subject == "Plano Pro"
        assert idempotency_key.startswith("email-send-")
        return True, "resend-outbound-1"

    monkeypatch.setattr(
        "app.api.v1.endpoints.platform_inbox.PlatformInboxEmailService.send_text",
        fake_send,
    )
    response = await platform_client.post(
        f"/api/v1/platform/inbox/conversations/{created.json()['id']}/messages",
        headers={"Idempotency-Key": f"email-send-{uuid4()}"},
        json={"body": "Segue o próximo passo.", "visibility": "public"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["deliveryStatus"] == "sent"
    assert response.json()["externalMessageId"] == "resend-outbound-1"


async def test_public_webchat_session_reaches_inbox_and_hides_internal_notes(
    client,
    platform_client,
):
    started = await client.post(
        "/api/v1/public/platform-webchat/conversations",
        json={
            "contactName": "Visitante do site",
            "contactEmail": "visitante@example.com",
            "subject": "Quero uma demonstração",
            "initialMessage": "Gostaria de entender o plano para meu escritório.",
            "intent": "sales",
        },
    )
    assert started.status_code == 201, started.text
    conversation_id = started.json()["conversationId"]
    token = started.json()["accessToken"]

    inbound = await client.post(
        f"/api/v1/public/platform-webchat/conversations/{conversation_id}/messages",
        headers={"X-Webchat-Token": token, "Idempotency-Key": f"webchat-{uuid4()}"},
        json={"body": "Tenho uma equipe com oito pessoas."},
    )
    assert inbound.status_code == 201
    assert inbound.json()["direction"] == "inbound"

    internal = await platform_client.post(
        f"/api/v1/platform/inbox/conversations/{conversation_id}/messages",
        headers={"Idempotency-Key": f"webchat-note-{uuid4()}"},
        json={"body": "Lead qualificado.", "visibility": "internal"},
    )
    assert internal.status_code == 201
    public_view = await client.get(
        f"/api/v1/public/platform-webchat/conversations/{conversation_id}",
        headers={"X-Webchat-Token": token},
    )
    assert public_view.status_code == 200
    assert [message["body"] for message in public_view.json()["messages"]] == [
        "Gostaria de entender o plano para meu escritório.",
        "Tenho uma equipe com oito pessoas.",
    ]

    denied = await client.get(
        f"/api/v1/public/platform-webchat/conversations/{conversation_id}",
        headers={"X-Webchat-Token": "token-invalido-com-tamanho-suficiente"},
    )
    assert denied.status_code == 404
