"""S1: regra de automação cala quando um humano está na conversa."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.klm_operations import KlmAuditEvent
from app.models.whatsapp import (
    ConnectionType,
    WaConnectionStatus,
    WaQueueStatus,
    WhatsAppConnection,
    WhatsAppContact,
)
from app.models.workspace import Workspace
from app.services.whatsapp.automation_rule_service import WhatsAppAutomationRuleService


async def _cenario(db_session, auth_context, *, queue_status):
    workspace = await db_session.get(Workspace, auth_context.workspace_id)
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id=f"pn-s1-{uuid4().hex[:8]}",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone=f"5511{uuid4().int % 10**9:09d}",
        queue_status=queue_status,
    )
    db_session.add(contact)
    workspace.settings = {
        **(workspace.settings or {}),
        "whatsappAutomation": {
            "enabled": True,
            "onboardingCompleted": True,
            "timezone": "America/Sao_Paulo",
            "businessHoursStart": 0,
            "businessHoursEnd": 23,
            "rules": [
                {
                    "id": "welcome-s1",
                    "label": "Boas-vindas",
                    "enabled": True,
                    "triggerType": "welcome",
                    "replyText": "Olá! Já vamos falar com você.",
                    "silenceWindowHours": 12,
                }
            ],
        },
    }
    await db_session.commit()
    return contact


@pytest.mark.asyncio
async def test_regra_cala_com_humano_e_audita(db_session, auth_context):
    contact = await _cenario(db_session, auth_context, queue_status=WaQueueStatus.human)
    service = WhatsAppAutomationRuleService(
        db_session,
        clock=lambda: datetime(2026, 9, 4, 15, 0, tzinfo=UTC),
        delivery_gate=lambda: True,
    )

    assert await service.apply_inbound_rules(contact=contact, inbound_text="Oi") is None
    eventos = (
        await db_session.scalars(
            select(KlmAuditEvent).where(
                KlmAuditEvent.entity_id == contact.id,
                KlmAuditEvent.event_type == "whatsapp_automation_suppressed_human_active",
            )
        )
    ).all()
    assert len(eventos) == 1


@pytest.mark.asyncio
async def test_regra_continua_respondendo_com_ia(db_session, auth_context):
    contact = await _cenario(db_session, auth_context, queue_status=WaQueueStatus.ai)
    service = WhatsAppAutomationRuleService(
        db_session,
        clock=lambda: datetime(2026, 9, 4, 15, 0, tzinfo=UTC),
        delivery_gate=lambda: True,
    )

    outcome = await service.apply_inbound_rules(contact=contact, inbound_text="Oi")
    assert outcome is not None and outcome["replyText"] == "Olá! Já vamos falar com você."
