"""F15 — cópia da conversa por e-mail ao encerrar o chamado (enfileiramento)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

import app.services.support_ticket_service as support_service_module
from app.models.support_ticket import (
    SupportDeliveryStatus,
    SupportMessageVisibility,
    SupportTicketStatus,
    SupportTranscriptEmail,
)
from app.models.user import AgentType, User
from app.schemas.support_ticket import (
    SupportAvailabilityWeekday,
    SupportEscalationSettingsUpdate,
    SupportMessageCreate,
    SupportRatingCreate,
    SupportTechnicalContext,
    SupportTicketCreate,
    SupportTicketPriority,
    SupportTicketSeverity,
    SupportTicketType,
    SupportTicketUpdate,
)
from app.services.support_ticket_service import SupportTicketService


def _payload() -> SupportTicketCreate:
    return SupportTicketCreate(
        ticket_type=SupportTicketType.problem,
        subject="Falha ao emitir boleto",
        description="Descricao suficientemente longa para o contrato de suporte.",
        priority=SupportTicketPriority.normal,
        severity=SupportTicketSeverity.medium,
        technical_context=SupportTechnicalContext(
            page="/app/financeiro",
            action="Emitir boleto",
            frontend_version="2026.07.25",
        ),
    )


async def _advance_to_resolved(service: SupportTicketService, ticket, *, actor_user_id):
    """new -> in_progress -> resolved (the only permitted direct path)."""
    ticket = await service.update_ticket(
        ticket,
        actor_user_id=actor_user_id,
        payload=SupportTicketUpdate(
            expected_version=ticket.version,
            status=SupportTicketStatus.in_progress,
        ),
    )
    ticket = await service.update_ticket(
        ticket,
        actor_user_id=actor_user_id,
        payload=SupportTicketUpdate(
            expected_version=ticket.version,
            status=SupportTicketStatus.resolved,
        ),
    )
    return ticket


async def _ai_user(db_session, *, name: str = "SDR Jurídico (IA)") -> User:
    user = User(
        name=name,
        email=f"ia-{uuid4().hex[:10]}@example.com",
        agent_type=AgentType.ai,
        initials="IA",
        color="#0f172a",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.mark.asyncio
async def test_closing_ticket_enqueues_transcript_with_ordered_public_messages_only(
    db_session,
    auth_context,
    support_factory,
):
    platform_actor, _ = await support_factory.user(
        name="Agente Suporte",
        platform_role_package="support",
    )
    ai_user = await _ai_user(db_session)
    service = SupportTicketService(db_session)
    ticket, _ = await service.create_ticket(
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=_payload(),
        idempotency_key="transcript-happy-path",
    )

    await service.add_message(
        ticket,
        actor_user_id=platform_actor.id,
        actor_label=platform_actor.name,
        payload=SupportMessageCreate(body="Resposta pública da equipe."),
        idempotency_key="msg-public-human",
        platform=True,
    )
    await service.add_message(
        ticket,
        actor_user_id=platform_actor.id,
        actor_label="Nota interna",
        payload=SupportMessageCreate(
            body="Nota interna que NUNCA pode ir ao cliente.",
            visibility=SupportMessageVisibility.internal,
        ),
        idempotency_key="msg-internal",
        platform=True,
    )
    await service.add_message(
        ticket,
        actor_user_id=ai_user.id,
        actor_label="SDR Jurídico (IA)",
        payload=SupportMessageCreate(body="Triagem automática concluída."),
        idempotency_key="msg-public-ai",
        platform=True,
    )

    ticket = await _advance_to_resolved(service, ticket, actor_user_id=auth_context.user_id)
    ticket = await service.update_ticket(
        ticket,
        actor_user_id=auth_context.user_id,
        payload=SupportTicketUpdate(
            expected_version=ticket.version,
            status=SupportTicketStatus.closed,
        ),
    )

    record = await db_session.scalar(
        select(SupportTranscriptEmail).where(SupportTranscriptEmail.ticket_id == ticket.id)
    )
    assert record is not None
    assert record.closure_event_key == f"closed:{ticket.version}"
    assert record.final_status == "closed"
    assert record.resolved is True
    assert record.unsatisfied is False
    assert record.status == SupportDeliveryStatus.pending
    assert record.recipient_user_id == ticket.reporter_user_id

    messages = record.transcript_snapshot["messages"]
    assert [m["body"] for m in messages] == [
        "Resposta pública da equipe.",
        "Triagem automática concluída.",
    ]
    assert all("NUNCA" not in m["body"] for m in messages)
    assert messages[0]["authorKind"] == "Humano"
    assert messages[1]["authorKind"] == "IA"
    assert record.transcript_snapshot["escalation"] == {}


@pytest.mark.asyncio
async def test_reopen_and_close_again_creates_a_new_transcript_without_duplicating(
    db_session,
    auth_context,
):
    service = SupportTicketService(db_session)
    ticket, _ = await service.create_ticket(
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=_payload(),
        idempotency_key="transcript-reopen",
    )
    ticket = await _advance_to_resolved(service, ticket, actor_user_id=auth_context.user_id)
    ticket = await service.close_by_customer(ticket, actor_user_id=auth_context.user_id)
    first_key = f"closed:{ticket.version}"

    # Re-dispatching for the exact same closure (e.g. a retried caller) must
    # not create a second row.
    await service._dispatch_closure_transcript_email(ticket)

    rows = list(
        (
            await db_session.scalars(
                select(SupportTranscriptEmail).where(
                    SupportTranscriptEmail.ticket_id == ticket.id
                )
            )
        ).all()
    )
    assert len(rows) == 1
    assert rows[0].closure_event_key == first_key

    ticket = await service.reopen(ticket, actor_user_id=auth_context.user_id)
    ticket = await service.update_ticket(
        ticket,
        actor_user_id=auth_context.user_id,
        payload=SupportTicketUpdate(
            expected_version=ticket.version,
            status=SupportTicketStatus.resolved,
        ),
    )
    ticket = await service.close_by_customer(ticket, actor_user_id=auth_context.user_id)
    second_key = f"closed:{ticket.version}"
    assert second_key != first_key

    rows = list(
        (
            await db_session.scalars(
                select(SupportTranscriptEmail).where(
                    SupportTranscriptEmail.ticket_id == ticket.id
                )
            )
        ).all()
    )
    assert {row.closure_event_key for row in rows} == {first_key, second_key}


@pytest.mark.asyncio
async def test_cancelled_ticket_marks_not_resolved_and_includes_escalation_block(
    db_session,
    auth_context,
):
    service = SupportTicketService(db_session)
    await service.update_escalation_settings(
        payload=SupportEscalationSettingsUpdate(
            enabled=True,
            scheduling_url="https://cal.example.com/humano",
            whatsapp_number="+55 61 90000-0000",
            business_days=[
                SupportAvailabilityWeekday.monday,
                SupportAvailabilityWeekday.tuesday,
            ],
            business_hours_start="09:00",
            business_hours_end="18:00",
        ),
        actor_user_id=auth_context.user_id,
    )

    ticket, _ = await service.create_ticket(
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=_payload(),
        idempotency_key="transcript-cancelled",
    )
    ticket = await service.update_ticket(
        ticket,
        actor_user_id=auth_context.user_id,
        payload=SupportTicketUpdate(
            expected_version=ticket.version,
            status=SupportTicketStatus.cancelled,
        ),
    )

    record = await db_session.scalar(
        select(SupportTranscriptEmail).where(SupportTranscriptEmail.ticket_id == ticket.id)
    )
    assert record is not None
    assert record.final_status == "cancelled"
    assert record.resolved is False
    escalation = record.transcript_snapshot["escalation"]
    assert escalation["schedulingUrl"] == "https://cal.example.com/humano"
    assert escalation["whatsappNumber"] == "+55 61 90000-0000"
    assert "Seg" in escalation["businessHours"]
    assert "09:00" in escalation["businessHours"]


@pytest.mark.asyncio
async def test_unsatisfied_rating_triggers_escalation_block_even_when_resolved(
    db_session,
    auth_context,
):
    service = SupportTicketService(db_session)
    await service.update_escalation_settings(
        payload=SupportEscalationSettingsUpdate(
            enabled=True,
            scheduling_url="https://cal.example.com/humano",
        ),
        actor_user_id=auth_context.user_id,
    )
    ticket, _ = await service.create_ticket(
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=_payload(),
        idempotency_key="transcript-unsatisfied",
    )
    ticket = await _advance_to_resolved(service, ticket, actor_user_id=auth_context.user_id)
    await service.rate(
        ticket,
        actor_user_id=auth_context.user_id,
        payload=SupportRatingCreate(score=1, comment="Não resolveu meu problema."),
    )
    ticket = await service.close_by_customer(ticket, actor_user_id=auth_context.user_id)

    record = await db_session.scalar(
        select(SupportTranscriptEmail).where(SupportTranscriptEmail.ticket_id == ticket.id)
    )
    assert record is not None
    assert record.resolved is True
    assert record.unsatisfied is True
    assert record.transcript_snapshot["escalation"]["schedulingUrl"] == (
        "https://cal.example.com/humano"
    )


@pytest.mark.asyncio
async def test_auto_close_lifecycle_also_enqueues_transcript_email(
    db_session,
    auth_context,
    monkeypatch,
):
    fixed_now = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(support_service_module, "_now", lambda: fixed_now)
    service = SupportTicketService(db_session)
    ticket, _ = await service.create_ticket(
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=_payload(),
        idempotency_key="transcript-auto-close",
    )
    ticket.status = SupportTicketStatus.resolved
    ticket.resolved_at = fixed_now - timedelta(days=8)
    await db_session.flush()

    await service.process_lifecycle(
        workspace_id=auth_context.workspace_id,
        now=fixed_now,
    )

    record = await db_session.scalar(
        select(SupportTranscriptEmail).where(SupportTranscriptEmail.ticket_id == ticket.id)
    )
    assert record is not None
    assert record.final_status == "closed"
