"""S1: a escalada automática toma a thread canônica do contato, idempotente e best-effort."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.klm_operations import (
    KlmAuditEvent,
    OmnichannelChannel,
    OmnichannelThread,
    ThreadStatus,
)
from app.models.omnichannel_v6 import OmnichannelThreadBinding
from app.models.whatsapp import WhatsAppConnection, WhatsAppContact
from app.services.whatsapp.webhook_service import WhatsAppWebhookService


def _servico(db_session) -> WhatsAppWebhookService:
    service = WhatsAppWebhookService.__new__(WhatsAppWebhookService)
    service.db = db_session
    return service


async def _contato_com_thread(db_session, workspace_id):
    connection = WhatsAppConnection(workspace_id=workspace_id)
    db_session.add(connection)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=workspace_id,
        whatsapp_connection_id=connection.id,
        phone=f"5511{uuid4().int % 10**9:09d}",
    )
    db_session.add(contact)
    await db_session.flush()
    thread = OmnichannelThread(
        workspace_id=workspace_id,
        primary_channel=OmnichannelChannel.whatsapp,
        status=ThreadStatus.open,
    )
    db_session.add(thread)
    await db_session.flush()
    db_session.add(
        OmnichannelThreadBinding(
            workspace_id=workspace_id,
            thread_id=thread.id,
            provider="whatsapp",
            connection_key=f"whatsapp:{connection.id}",
            external_thread_id=f"whatsapp-contact:{contact.id}",
            binding_state="active",
        )
    )
    await db_session.commit()
    return contact, thread


async def _eventos(db_session, thread_id):
    return (
        await db_session.scalars(
            select(KlmAuditEvent).where(
                KlmAuditEvent.entity_id == thread_id,
                KlmAuditEvent.event_type == "conversation_ownership_human_takeover",
            )
        )
    ).all()


@pytest.mark.asyncio
async def test_escalada_toma_a_thread_uma_vez_so(db_session, auth_context):
    contact, thread = await _contato_com_thread(db_session, auth_context.workspace_id)
    service = _servico(db_session)

    await service._take_over_contact_thread(
        contact, assigned_user_id=auth_context.user_id, reason="auto_escalation"
    )
    await service._take_over_contact_thread(
        contact,
        assigned_user_id=auth_context.user_id,
        thread_id=thread.id,
        reason="auto_escalation",
    )

    atual = await db_session.scalar(
        select(OmnichannelThread)
        .where(OmnichannelThread.id == thread.id)
        .execution_options(populate_existing=True)
    )
    assert atual.status == ThreadStatus.human_active
    assert atual.assigned_human_id == auth_context.user_id
    assert len(await _eventos(db_session, thread.id)) == 1


@pytest.mark.asyncio
async def test_escalada_sem_thread_canonica_nao_quebra(db_session, auth_context):
    connection = WhatsAppConnection(workspace_id=auth_context.workspace_id)
    db_session.add(connection)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone=f"5511{uuid4().int % 10**9:09d}",
    )
    db_session.add(contact)
    await db_session.commit()

    await _servico(db_session)._take_over_contact_thread(
        contact, assigned_user_id=auth_context.user_id, reason="auto_escalation"
    )
