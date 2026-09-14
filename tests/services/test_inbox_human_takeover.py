"""S1: assumir ou devolver a conversa no inbox mexe na thread canônica do contato."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.client import Client
from app.models.klm_operations import (
    KlmAuditEvent,
    OmnichannelChannel,
    OmnichannelThread,
    ThreadStatus,
)
from app.models.matter import Matter, PracticeArea
from app.models.omnichannel_v6 import OmnichannelThreadBinding
from app.models.whatsapp import (
    HumanControlState,
    WaQueueStatus,
    WhatsAppConnection,
    WhatsAppContact,
)
from app.schemas.whatsapp import ConversationAssignRequest, ConversationHandoffRequest
from app.services.whatsapp.inbox_service import WhatsAppInboxService


def _service(db_session) -> WhatsAppInboxService:
    return WhatsAppInboxService(
        db_session,
        message_service=SimpleNamespace(get_conversation=AsyncMock(return_value=None)),
        template_service=SimpleNamespace(),
    )


async def _contato_com_thread(
    db_session, workspace_id, *, matter_id=None, status=ThreadStatus.open
):
    connection = WhatsAppConnection(workspace_id=workspace_id)
    db_session.add(connection)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=workspace_id,
        whatsapp_connection_id=connection.id,
        phone=f"5511{uuid4().int % 10**9:09d}",
        matter_id=matter_id,
    )
    db_session.add(contact)
    await db_session.flush()
    thread = OmnichannelThread(
        workspace_id=workspace_id,
        matter_id=matter_id,
        primary_channel=OmnichannelChannel.whatsapp,
        status=status,
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
    await db_session.flush()
    return contact, thread


async def _matter(db_session, workspace_id) -> Matter:
    client = Client(workspace_id=workspace_id, name="Cliente inbox")
    db_session.add(client)
    await db_session.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client.id,
        practice_area=PracticeArea.civel,
        demand_type="Inbox",
    )
    db_session.add(matter)
    await db_session.flush()
    return matter


async def _thread_atual(db_session, thread_id) -> OmnichannelThread:
    return await db_session.scalar(
        select(OmnichannelThread)
        .where(OmnichannelThread.id == thread_id)
        .execution_options(populate_existing=True)
    )


@pytest.mark.asyncio
async def test_assumir_toma_a_thread_do_contato_mesmo_sem_matter(db_session, auth_context):
    contact, thread = await _contato_com_thread(db_session, auth_context.workspace_id)
    await db_session.commit()

    await _service(db_session).assign_conversation(
        contact.id,
        auth_context.workspace_id,
        ConversationAssignRequest(user_id=auth_context.user_id),
        auth_context.user_id,
    )

    atual = await _thread_atual(db_session, thread.id)
    assert atual.status == ThreadStatus.human_active
    assert atual.assigned_human_id == auth_context.user_id
    eventos = (
        await db_session.scalars(
            select(KlmAuditEvent).where(
                KlmAuditEvent.entity_id == thread.id,
                KlmAuditEvent.event_type == "conversation_ownership_human_takeover",
            )
        )
    ).all()
    assert len(eventos) == 1
    assert eventos[0].after_data["reason"] == "inbox_assign"


@pytest.mark.asyncio
async def test_assumir_um_contato_nao_toma_a_thread_de_outro_contato_do_mesmo_caso(
    db_session, auth_context
):
    matter = await _matter(db_session, auth_context.workspace_id)
    contact_a, thread_a = await _contato_com_thread(
        db_session, auth_context.workspace_id, matter_id=matter.id
    )
    _contact_b, thread_b = await _contato_com_thread(
        db_session, auth_context.workspace_id, matter_id=matter.id
    )
    await db_session.commit()

    await _service(db_session).claim_conversation(
        contact_a.id, auth_context.workspace_id, auth_context.user_id
    )

    assert (await _thread_atual(db_session, thread_a.id)).status == ThreadStatus.human_active
    assert (await _thread_atual(db_session, thread_b.id)).status == ThreadStatus.open


@pytest.mark.asyncio
async def test_desatribuir_e_follow_up_nao_tocam_a_thread(db_session, auth_context):
    contact, thread = await _contato_com_thread(db_session, auth_context.workspace_id)
    await db_session.commit()
    service = _service(db_session)

    await service.assign_conversation(
        contact.id,
        auth_context.workspace_id,
        ConversationAssignRequest(user_id=None),
        auth_context.user_id,
    )
    assert (await _thread_atual(db_session, thread.id)).status == ThreadStatus.open

    await service.handoff_conversation(
        contact.id,
        auth_context.workspace_id,
        ConversationHandoffRequest(
            queue_status=WaQueueStatus.follow_up, human_control_state=HumanControlState.copilot
        ),
        auth_context.user_id,
    )
    assert (await _thread_atual(db_session, thread.id)).status == ThreadStatus.open


@pytest.mark.asyncio
async def test_handoff_para_ia_devolve_a_thread_ao_status_anterior(db_session, auth_context):
    contact, thread = await _contato_com_thread(
        db_session, auth_context.workspace_id, status=ThreadStatus.waiting_documents
    )
    await db_session.commit()
    service = _service(db_session)

    await service.handoff_conversation(
        contact.id,
        auth_context.workspace_id,
        ConversationHandoffRequest(
            queue_status=WaQueueStatus.human, human_control_state=HumanControlState.manual_pause
        ),
        auth_context.user_id,
    )
    assert (await _thread_atual(db_session, thread.id)).status == ThreadStatus.human_active

    await service.handoff_conversation(
        contact.id,
        auth_context.workspace_id,
        ConversationHandoffRequest(
            queue_status=WaQueueStatus.ai, human_control_state=HumanControlState.copilot
        ),
        auth_context.user_id,
    )
    atual = await _thread_atual(db_session, thread.id)
    assert atual.status == ThreadStatus.waiting_documents
    assert atual.assigned_human_id == auth_context.user_id


@pytest.mark.asyncio
async def test_contato_sem_thread_canonica_nao_quebra_a_atribuicao(db_session, auth_context):
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

    await _service(db_session).claim_conversation(
        contact.id, auth_context.workspace_id, auth_context.user_id
    )
    await db_session.refresh(contact)
    assert contact.queue_status == WaQueueStatus.human
    assert contact.assigned_user_id == auth_context.user_id
