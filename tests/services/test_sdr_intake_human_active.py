"""S1: com um humano na conversa, o SDR não é despachado (o CRM continua)."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.client import Client
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import Matter, PracticeArea
from app.models.whatsapp import (
    ConnectionType,
    WaConnectionStatus,
    WaQueueStatus,
    WhatsAppConnection,
    WhatsAppContact,
)
from app.services.sdr_intake_service import handle_inbound_lead


@pytest.fixture
def _despachos(monkeypatch):
    despacho = AsyncMock(return_value=None)
    novo = AsyncMock(return_value=None)
    monkeypatch.setattr("app.workers.event_bus.dispatch_whatsapp_message", despacho)
    monkeypatch.setattr("app.workers.event_bus.dispatch_new_lead", novo)
    monkeypatch.setattr(
        "app.services.knowledge_service.KnowledgeService.index_whatsapp_message",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.conversation_insight_service.ConversationInsightService.generate_snapshot",
        AsyncMock(return_value=None),
    )
    return despacho, novo


async def _contato(db_session, workspace_id, *, queue_status, com_matter: bool):
    connection = WhatsAppConnection(
        workspace_id=workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id=f"pn-s1-{uuid4().hex[:8]}",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=workspace_id,
        whatsapp_connection_id=connection.id,
        phone=f"5511{uuid4().int % 10**9:09d}",
        name="Lead",
        queue_status=queue_status,
    )
    db_session.add(contact)
    await db_session.flush()
    if com_matter:
        client = Client(workspace_id=workspace_id, name="Cliente S1")
        db_session.add(client)
        await db_session.flush()
        matter = Matter(
            workspace_id=workspace_id,
            client_id=client.id,
            practice_area=PracticeArea.civel,
            demand_type="Qualificação em curso",
            sdr_qualification={},
        )
        db_session.add(matter)
        await db_session.flush()
        contact.matter_id = matter.id
        await db_session.flush()
    await db_session.commit()
    return contact


@pytest.mark.asyncio
async def test_lead_existente_com_humano_nao_despacha_e_audita(
    db_session, auth_context, _despachos
):
    despacho, _ = _despachos
    contact = await _contato(
        db_session, auth_context.workspace_id, queue_status=WaQueueStatus.human, com_matter=True
    )

    await handle_inbound_lead(
        db=db_session,
        contact_id=contact.id,
        phone=contact.phone,
        contact_name=contact.name,
        message_body="oi, e o meu processo?",
        wamid="wamid.s1-humano",
    )

    despacho.assert_not_called()
    eventos = (
        await db_session.scalars(
            select(KlmAuditEvent).where(
                KlmAuditEvent.entity_id == contact.id,
                KlmAuditEvent.event_type == "whatsapp_sdr_dispatch_suppressed_human_active",
            )
        )
    ).all()
    assert len(eventos) == 1
    assert eventos[0].after_data["wamid"] == "wamid.s1-humano"


@pytest.mark.asyncio
async def test_lead_existente_com_ia_continua_despachando(db_session, auth_context, _despachos):
    despacho, _ = _despachos
    contact = await _contato(
        db_session, auth_context.workspace_id, queue_status=WaQueueStatus.ai, com_matter=True
    )

    await handle_inbound_lead(
        db=db_session,
        contact_id=contact.id,
        phone=contact.phone,
        contact_name=contact.name,
        message_body="oi",
        wamid="wamid.s1-ia",
    )

    despacho.assert_awaited_once()


@pytest.mark.asyncio
async def test_lead_novo_com_humano_cria_o_caso_mas_nao_chama_o_sdr(
    db_session, auth_context, _despachos
):
    _, novo = _despachos
    contact = await _contato(
        db_session, auth_context.workspace_id, queue_status=WaQueueStatus.human, com_matter=False
    )

    await handle_inbound_lead(
        db=db_session,
        contact_id=contact.id,
        phone=contact.phone,
        contact_name=contact.name,
        message_body="preciso de ajuda",
        wamid="wamid.s1-novo",
    )

    novo.assert_not_called()
    await db_session.refresh(contact)
    assert contact.matter_id is not None, "o CRM continua: cliente e caso nascem"
