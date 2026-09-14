"""F15 — worker de entrega fail-closed da cópia da conversa por e-mail."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.support_ticket import SupportDeliveryStatus, SupportTranscriptEmail
from app.schemas.support_ticket import SupportTicketStatus, SupportTicketUpdate
from app.services.support_ticket_service import SupportTicketService
from app.workers import support_transcript_tasks


@pytest.mark.asyncio
async def test_transcript_worker_delivers_once_and_marks_sent(
    client,
    db_session,
    auth_context,
    support_ticket_payload,
    monkeypatch,
):
    created = await client.post(
        "/api/v1/support/tickets",
        headers={"Idempotency-Key": "transcript-worker-sent"},
        json=support_ticket_payload(),
    )
    assert created.status_code == 201
    ticket_id = created.json()["id"]
    await db_session.commit()

    service = SupportTicketService(db_session)
    from app.models.support_ticket import SupportTicket

    ticket = await db_session.get(SupportTicket, ticket_id)
    ticket = await service.update_ticket(
        ticket,
        actor_user_id=auth_context.user_id,
        payload=SupportTicketUpdate(
            expected_version=ticket.version,
            status=SupportTicketStatus.in_progress,
        ),
    )
    ticket = await service.update_ticket(
        ticket,
        actor_user_id=auth_context.user_id,
        payload=SupportTicketUpdate(
            expected_version=ticket.version,
            status=SupportTicketStatus.resolved,
        ),
    )
    ticket = await service.close_by_customer(ticket, actor_user_id=auth_context.user_id)
    await db_session.commit()

    sent: list[dict] = []

    async def capture_email(**kwargs):
        sent.append(kwargs)
        return True

    monkeypatch.setattr(
        support_transcript_tasks,
        "external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        support_transcript_tasks,
        "send_support_transcript_email",
        capture_email,
    )
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    result = await support_transcript_tasks._async_process_workspace(
        auth_context.workspace_id,
        session_factory=session_factory,
        now=datetime.now(UTC),
    )
    replay = await support_transcript_tasks._async_process_workspace(
        auth_context.workspace_id,
        session_factory=session_factory,
        now=datetime.now(UTC),
    )

    assert result == {"processed": 1, "sent": 1}
    assert replay == {"processed": 0, "sent": 0}
    assert len(sent) == 1
    assert sent[0]["ticket_number"] == ticket.public_number
    assert sent[0]["final_status_label"] == "Encerrado"

    db_session.expire_all()
    delivery = await db_session.scalar(select(SupportTranscriptEmail))
    assert delivery is not None
    assert delivery.status == SupportDeliveryStatus.sent
    assert delivery.attempts == 1
    assert delivery.delivered_at is not None


@pytest.mark.asyncio
async def test_transcript_worker_keeps_pending_with_full_content_while_gate_is_closed(
    client,
    db_session,
    auth_context,
    support_ticket_payload,
    monkeypatch,
):
    created = await client.post(
        "/api/v1/support/tickets",
        headers={"Idempotency-Key": "transcript-worker-gate-closed"},
        json=support_ticket_payload(),
    )
    assert created.status_code == 201
    ticket_id = created.json()["id"]
    await db_session.commit()

    from app.models.support_ticket import SupportTicket

    service = SupportTicketService(db_session)
    ticket = await db_session.get(SupportTicket, ticket_id)
    ticket = await service.update_ticket(
        ticket,
        actor_user_id=auth_context.user_id,
        payload=SupportTicketUpdate(
            expected_version=ticket.version,
            status=SupportTicketStatus.in_progress,
        ),
    )
    ticket = await service.update_ticket(
        ticket,
        actor_user_id=auth_context.user_id,
        payload=SupportTicketUpdate(
            expected_version=ticket.version,
            status=SupportTicketStatus.resolved,
        ),
    )
    ticket = await service.close_by_customer(ticket, actor_user_id=auth_context.user_id)
    expected_public_number = ticket.public_number
    await db_session.commit()

    monkeypatch.setattr(
        support_transcript_tasks,
        "external_delivery_allowed",
        lambda: False,
    )
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    result = await support_transcript_tasks._async_process_workspace(
        auth_context.workspace_id,
        session_factory=session_factory,
    )

    assert result == {"processed": 0, "sent": 0}
    db_session.expire_all()
    delivery = await db_session.scalar(select(SupportTranscriptEmail))
    assert delivery is not None
    assert delivery.status == SupportDeliveryStatus.pending
    assert delivery.attempts == 0
    # Fail-closed never loses the content: the full transcript stays
    # persisted, ready to send the instant the gate reopens.
    assert delivery.transcript_snapshot["ticketNumber"] == expected_public_number
    assert delivery.transcript_snapshot["finalStatus"] == "closed"
