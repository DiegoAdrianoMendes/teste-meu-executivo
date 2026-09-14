from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.klm_operations import KlmAuditEvent, OmnichannelMessage, OmnichannelThread
from app.models.whatsapp import (
    ConnectionType,
    HumanControlState,
    WaConnectionStatus,
    WaMessageDirection,
    WaQueueStatus,
    WhatsAppConnection,
    WhatsAppContact,
    WhatsAppMessage,
)
from app.services.message_evidence_service import MessageEvidenceService
from app.services.whatsapp_service import WhatsAppService


async def _coexistence_connection(db_session, workspace_id):
    connection = WhatsAppConnection(
        workspace_id=workspace_id,
        connection_type=ConnectionType.TECH_PROVIDER,
        transport_mode="coexistence",
        phone_number_id="pn-coexistence-webhook",
        display_phone_number="+55 11 4000-0000",
        status=WaConnectionStatus.connected,
        webhook_registered=True,
        is_default=True,
    )
    db_session.add(connection)
    await db_session.commit()
    return connection


@pytest.mark.asyncio
async def test_business_app_echo_is_one_canonical_human_reply_and_pauses_ai(
    db_session, auth_context
):
    connection = await _coexistence_connection(db_session, auth_context.workspace_id)
    service = WhatsAppService(db_session).webhook_service
    value = {
        "_klmWebhookField": "smb_message_echoes",
        "metadata": {"phone_number_id": connection.phone_number_id},
        "message_echoes": [
            {
                "id": "wamid.business-app.echo.1",
                "from": "551140000000",
                "to": "+55 (11) 98888-7777",
                "timestamp": "1784580000",
                "type": "text",
                "text": {"body": "Olá, aqui é o advogado responsável."},
            }
        ],
    }

    await service._handle_value(value, auth_context.workspace_id)
    await service._handle_value(value, auth_context.workspace_id)

    messages = list(
        (
            await db_session.scalars(
                select(WhatsAppMessage).where(
                    WhatsAppMessage.wamid == "wamid.business-app.echo.1"
                )
            )
        ).all()
    )
    assert len(messages) == 1
    assert messages[0].direction == WaMessageDirection.outbound
    assert messages[0].message_metadata["originSurface"] == "business_app"
    assert messages[0].message_metadata["billableByMeta"] is False

    contact = await db_session.get(WhatsAppContact, messages[0].contact_id)
    assert contact is not None
    assert contact.phone == "5511988887777"
    assert contact.queue_status == WaQueueStatus.human
    assert contact.human_control_state == HumanControlState.manual_pause

    canonical = await db_session.scalar(
        select(OmnichannelMessage).where(
            OmnichannelMessage.external_message_id == "wamid.business-app.echo.1"
        )
    )
    assert canonical is not None
    thread = await db_session.get(OmnichannelThread, canonical.thread_id)
    assert thread is not None
    assert thread.policy_state["aiPaused"] is True
    assert thread.policy_state["pauseReason"] == "whatsapp_business_app_echo"
    assert (
        await db_session.scalar(
            select(func.count(KlmAuditEvent.id)).where(
                KlmAuditEvent.event_type == "whatsapp_coexistence_human_reply"
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_history_sync_imports_interactive_reply_without_running_live_automation(
    db_session, auth_context
):
    connection = await _coexistence_connection(db_session, auth_context.workspace_id)
    service = WhatsAppService(db_session).webhook_service

    await service._ingest_coexistence_message(
        connection=connection,
        workspace_id=auth_context.workspace_id,
        message_data={
            "id": "wamid.history.interactive.1",
            "from": "+55 (11) 97777-6666",
            "timestamp": "1784570000",
            "type": "interactive",
            "interactive": {
                "type": "button_reply",
                "button_reply": {"id": "schedule", "title": "Agendar"},
            },
        },
        contact_phone="+55 (11) 97777-6666",
        outbound=False,
        historical=True,
    )
    await db_session.commit()

    legacy = await db_session.scalar(
        select(WhatsAppMessage).where(
            WhatsAppMessage.wamid == "wamid.history.interactive.1"
        )
    )
    assert legacy is not None
    assert legacy.body == "Agendar"
    assert legacy.message_metadata["historySync"] is True
    assert legacy.message_metadata["structuredContent"] == {
        "kind": "button_reply",
        "id": "schedule",
        "title": "Agendar",
        "description": None,
    }
    assert legacy.inbound_pipeline == {}
    assert (
        await db_session.scalar(
            select(func.count(OmnichannelMessage.id)).where(
                OmnichannelMessage.external_message_id == "wamid.history.interactive.1"
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_same_provider_message_preserves_original_edit_and_delete_signal(
    db_session,
    auth_context,
):
    connection = await _coexistence_connection(db_session, auth_context.workspace_id)
    service = WhatsAppService(db_session).webhook_service
    original = {
        "id": "wamid.coexistence.edited.1",
        "from": "+55 (11) 97777-5555",
        "timestamp": "1784571000",
        "type": "text",
        "text": {"body": "A negativa foi ontem."},
    }
    edited = {
        **original,
        "timestamp": "1784571060",
        "text": {"body": "A negativa foi hoje."},
    }
    deleted = {
        "id": original["id"],
        "from": original["from"],
        "timestamp": "1784571120",
        "type": "unsupported",
        "errors": [{"code": 131051, "title": "Unsupported message"}],
    }

    for payload in (original, edited, deleted, deleted):
        await service._ingest_coexistence_message(
            connection=connection,
            workspace_id=auth_context.workspace_id,
            message_data=payload,
            contact_phone=original["from"],
            outbound=False,
            historical=True,
        )
        await db_session.commit()

    legacy = await db_session.scalar(
        select(WhatsAppMessage).where(WhatsAppMessage.wamid == original["id"])
    )
    canonical = await db_session.scalar(
        select(OmnichannelMessage).where(
            OmnichannelMessage.external_message_id == original["id"]
        )
    )
    assert legacy is not None
    assert canonical is not None
    assert legacy.body == "A negativa foi hoje."
    assert legacy.message_metadata["contentLifecycleState"] == "deleted"
    assert canonical.body == "A negativa foi hoje."
    assert canonical.content_lifecycle_state == "deleted"

    chain = await MessageEvidenceService(db_session).read_chain(
        auth_context.workspace_id,
        canonical.thread_id,
        canonical.id,
    )
    assert chain["integrityStatus"] == "verified"
    assert chain["originalSnapshot"]["bodySnapshot"] == "A negativa foi ontem."
    assert [item["eventType"] for item in chain["events"]].count("edited") == 1
    assert [item["eventType"] for item in chain["events"]].count("deleted") == 1
    assert chain["events"][-1]["eventPayload"]["errors"][0]["code"] == 131051
