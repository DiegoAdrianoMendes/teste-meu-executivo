from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.whatsapp import (
    ConnectionType,
    WaConnectionStatus,
    WhatsAppConnection,
    WhatsAppContact,
)
from app.models.whatsapp_campaign import (
    MetaTemplateStatus,
    WhatsAppMetaTemplate,
)
from app.schemas.whatsapp import StartWhatsAppConversationRequest
from app.services.whatsapp_service import WhatsAppService

pytestmark = pytest.mark.usefixtures("active_subscription")


async def _connection_and_template(db_session, workspace_id, *, status=MetaTemplateStatus.approved):
    connection = WhatsAppConnection(
        workspace_id=workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn_start_conversation",
        phone_number="5561999999998",
        status=WaConnectionStatus.connected,
        connected_at=datetime.now(UTC),
    )
    db_session.add(connection)
    await db_session.flush()
    template = WhatsAppMetaTemplate(
        workspace_id=workspace_id,
        whatsapp_connection_id=connection.id,
        name="primeiro_contato",
        category="UTILITY",
        language="pt_BR",
        status=status,
        components=[{"type": "BODY", "text": "Olá {{1}}"}],
        raw_payload={},
    )
    db_session.add(template)
    await db_session.flush()
    return connection, template


@pytest.mark.asyncio
async def test_start_conversation_creates_contact_and_sends_approved_template(
    db_session,
    auth_context,
):
    connection, template = await _connection_and_template(
        db_session,
        auth_context.workspace_id,
    )
    service = WhatsAppService(db_session)
    service.message_service.send_structured = AsyncMock(return_value=None)
    expected_conversation = object()
    service.message_service.get_conversation = AsyncMock(return_value=expected_conversation)

    result = await service.start_conversation(
        StartWhatsAppConversationRequest(
            connection_id=connection.id,
            meta_template_id=template.id,
            phone="+55 (61) 99999-9999",
            contact_name="Joana Cliente",
            body_parameters=["Joana"],
        ),
        sent_by_id=auth_context.user_id,
        workspace_id=auth_context.workspace_id,
        idempotency_key="start-conversation-001",
    )

    contact = await db_session.scalar(
        select(WhatsAppContact).where(
            WhatsAppContact.workspace_id == auth_context.workspace_id,
            WhatsAppContact.phone == "5561999999999",
        )
    )
    assert contact is not None
    assert contact.name == "Joana Cliente"
    assert contact.whatsapp_connection_id == connection.id
    sent_payload = service.message_service.send_structured.await_args.args[0]
    assert sent_payload.contact_id == contact.id
    assert sent_payload.connection_id == connection.id
    assert sent_payload.idempotency_key == "start-conversation-001"
    assert sent_payload.message_payload == {
        "kind": "template",
        "name": "primeiro_contato",
        "language": "pt_BR",
        "components": [
            {
                "type": "body",
                "parameters": [{"type": "text", "text": "Joana"}],
            }
        ],
    }
    assert result is expected_conversation


@pytest.mark.asyncio
async def test_start_conversation_rejects_template_not_approved(
    db_session,
    auth_context,
):
    connection, template = await _connection_and_template(
        db_session,
        auth_context.workspace_id,
        status=MetaTemplateStatus.pending,
    )
    service = WhatsAppService(db_session)
    service.message_service.send_structured = AsyncMock()

    with pytest.raises(HTTPException) as error:
        await service.start_conversation(
            StartWhatsAppConversationRequest(
                connection_id=connection.id,
                meta_template_id=template.id,
                phone="5561999999999",
            ),
            sent_by_id=auth_context.user_id,
            workspace_id=auth_context.workspace_id,
            idempotency_key="start-conversation-pending",
        )

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "WA-START-002"
    service.message_service.send_structured.assert_not_awaited()
    assert await db_session.scalar(select(WhatsAppContact)) is None


@pytest.mark.asyncio
async def test_start_conversation_rejects_template_from_another_connection(
    db_session,
    auth_context,
):
    connection, template = await _connection_and_template(
        db_session,
        auth_context.workspace_id,
    )
    other_connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=False,
        phone_number_id="pn_other_connection",
        status=WaConnectionStatus.connected,
        connected_at=datetime.now(UTC),
    )
    db_session.add(other_connection)
    await db_session.flush()
    service = WhatsAppService(db_session)
    service.message_service.send_structured = AsyncMock()

    with pytest.raises(HTTPException) as error:
        await service.start_conversation(
            StartWhatsAppConversationRequest(
                connection_id=other_connection.id,
                meta_template_id=template.id,
                phone="5561999999999",
            ),
            sent_by_id=auth_context.user_id,
            workspace_id=auth_context.workspace_id,
            idempotency_key="start-conversation-mismatch",
        )

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "WA-START-003"
    service.message_service.send_structured.assert_not_awaited()
    assert connection.id != other_connection.id
