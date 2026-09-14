from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.support_ticket import (
    SupportDeliveryStatus,
    SupportNotificationOutbox,
)
from app.workers import support_notification_tasks


@pytest.mark.asyncio
async def test_support_email_outbox_is_delivered_once_with_minimized_payload(
    client,
    db_session,
    auth_context,
    support_ticket_payload,
    monkeypatch,
):
    created = await client.post(
        "/api/v1/support/tickets",
        headers={"Idempotency-Key": "support-email-worker"},
        json=support_ticket_payload(),
    )
    assert created.status_code == 201
    await db_session.commit()

    sent: list[dict] = []

    async def capture_email(**kwargs):
        sent.append(kwargs)
        return True

    monkeypatch.setattr(
        support_notification_tasks,
        "external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        support_notification_tasks,
        "send_operational_alert_email",
        capture_email,
    )
    session_factory = async_sessionmaker(
        db_session.bind,
        expire_on_commit=False,
    )
    result = await support_notification_tasks._async_process_workspace(
        auth_context.workspace_id,
        session_factory=session_factory,
        now=datetime.now(UTC),
    )
    replay = await support_notification_tasks._async_process_workspace(
        auth_context.workspace_id,
        session_factory=session_factory,
        now=datetime.now(UTC),
    )

    assert result == {"processed": 1, "sent": 1}
    assert replay == {"processed": 0, "sent": 0}
    assert len(sent) == 1
    assert sent[0]["to_email"] == "teste@example.com"
    assert "Falha ao concluir cadastro" not in str(sent[0])
    assert "A tela nao conclui" not in str(sent[0])
    assert "?support=" in sent[0]["cta_url"]

    db_session.expire_all()
    delivery = await db_session.scalar(select(SupportNotificationOutbox))
    assert delivery is not None
    assert delivery.status == SupportDeliveryStatus.sent
    assert delivery.attempts == 1
    assert delivery.delivered_at is not None


@pytest.mark.asyncio
async def test_support_email_worker_keeps_pending_while_external_gate_is_closed(
    client,
    db_session,
    auth_context,
    support_ticket_payload,
    monkeypatch,
):
    created = await client.post(
        "/api/v1/support/tickets",
        headers={"Idempotency-Key": "support-email-local-gate"},
        json=support_ticket_payload(),
    )
    assert created.status_code == 201
    await db_session.commit()
    monkeypatch.setattr(
        support_notification_tasks,
        "external_delivery_allowed",
        lambda: False,
    )
    session_factory = async_sessionmaker(
        db_session.bind,
        expire_on_commit=False,
    )

    result = await support_notification_tasks._async_process_workspace(
        auth_context.workspace_id,
        session_factory=session_factory,
    )

    assert result == {"processed": 0, "sent": 0}
    db_session.expire_all()
    delivery = await db_session.scalar(select(SupportNotificationOutbox))
    assert delivery is not None
    assert delivery.status == SupportDeliveryStatus.pending
    assert delivery.attempts == 0
