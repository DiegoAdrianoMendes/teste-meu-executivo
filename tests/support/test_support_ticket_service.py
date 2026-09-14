from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

import app.services.support_ticket_service as support_service_module
from app.db.append_only import ImmutableRecordError
from app.models.klm_operations import (
    TechnicalRequest,
    TechnicalRequestSeverity,
    TechnicalRequestStatus,
)
from app.models.support_ticket import (
    SupportDeliveryStatus,
    SupportNotificationOutbox,
    SupportTicket,
    SupportTicketEvent,
    SupportTicketMessage,
    SupportTicketPriority,
    SupportTicketQueue,
    SupportTicketSeverity,
    SupportTicketStatus,
    SupportTicketType,
)
from app.schemas.support_ticket import (
    SupportMessageCreate,
    SupportTechnicalContext,
    SupportTicketCreate,
    SupportTicketUpdate,
)
from app.services.support_ticket_service import (
    SupportTicketConflictError,
    SupportTicketService,
    SupportTicketValidationError,
)


def _decode_platform_cursor(raw: str) -> tuple[datetime, object]:
    padded = raw + "=" * (-len(raw) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    return datetime.fromisoformat(payload["timestamp"]), payload["id"]


def _payload(
    *,
    severity: SupportTicketSeverity = SupportTicketSeverity.medium,
    ticket_type: SupportTicketType = SupportTicketType.problem,
) -> SupportTicketCreate:
    return SupportTicketCreate(
        ticket_type=ticket_type,
        subject=f"Falha de suporte {severity.value}",
        description="Descricao suficientemente longa para o contrato de suporte.",
        priority=SupportTicketPriority.normal,
        severity=severity,
        technical_context=SupportTechnicalContext(
            page="/app/teste",
            action="Executar teste",
            frontend_version="2026.07.24",
            failed_request_ids=["request:1"],
        ),
    )


@pytest.mark.asyncio
async def test_platform_ticket_cursor_preserves_equal_timestamps(db_session, auth_context):
    service = SupportTicketService(db_session)
    timestamp = datetime(2026, 7, 28, 14, 0, tzinfo=UTC)
    created_ids: set[str] = set()
    for index in range(55):
        ticket, _ = await service.create_ticket(
            workspace_id=auth_context.workspace_id,
            user_id=auth_context.user_id,
            payload=_payload(),
            idempotency_key=f"cursor-ticket-{index}-{uuid4()}",
        )
        ticket.created_at = timestamp
        created_ids.add(str(ticket.id))
    await db_session.flush()

    first, first_cursor = await service.list_platform(limit=25)
    assert first_cursor is not None
    cursor_time, cursor_id = _decode_platform_cursor(first_cursor)
    second, second_cursor = await service.list_platform(
        limit=25, cursor=(cursor_time, UUID(str(cursor_id)))
    )
    assert second_cursor is not None
    cursor_time, cursor_id = _decode_platform_cursor(second_cursor)
    third, third_cursor = await service.list_platform(
        limit=25, cursor=(cursor_time, UUID(str(cursor_id)))
    )

    returned = {str(ticket.id) for ticket in [*first, *second, *third]}
    assert returned == created_ids
    assert len(first) == 25
    assert len(second) == 25
    assert len(third) == 5
    assert third_cursor is None


@pytest.mark.asyncio
async def test_severity_drives_exact_sla_and_queue_rules(
    db_session,
    auth_context,
    monkeypatch,
):
    fixed_now = datetime(2026, 7, 24, 15, 30, tzinfo=UTC)
    monkeypatch.setattr(support_service_module, "_now", lambda: fixed_now)
    service = SupportTicketService(db_session)
    expectations = {
        SupportTicketSeverity.low: (timedelta(hours=8), timedelta(days=5)),
        SupportTicketSeverity.medium: (timedelta(hours=4), timedelta(days=3)),
        SupportTicketSeverity.high: (timedelta(hours=1), timedelta(hours=24)),
        SupportTicketSeverity.critical: (timedelta(minutes=15), timedelta(hours=4)),
    }

    for severity, (first_sla, resolution_sla) in expectations.items():
        ticket, created = await service.create_ticket(
            workspace_id=auth_context.workspace_id,
            user_id=auth_context.user_id,
            payload=_payload(severity=severity),
            idempotency_key=f"sla-{severity.value}",
        )
        assert created is True
        assert ticket.first_response_due_at == fixed_now + first_sla
        assert ticket.resolution_due_at == fixed_now + resolution_sla
        assert ticket.queue is (
            SupportTicketQueue.technical
            if severity is SupportTicketSeverity.critical
            else SupportTicketQueue.support
        )

    instability, _ = await service.create_ticket(
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=_payload(
            severity=SupportTicketSeverity.low,
            ticket_type=SupportTicketType.instability,
        ),
        idempotency_key="sla-instability",
    )
    assert instability.queue is SupportTicketQueue.technical


@pytest.mark.asyncio
async def test_transition_graph_timestamps_reopen_and_severity_sla_recalculation(
    db_session,
    auth_context,
    monkeypatch,
):
    fixed_now = datetime(2026, 7, 24, 16, 0, tzinfo=UTC)
    monkeypatch.setattr(support_service_module, "_now", lambda: fixed_now)
    service = SupportTicketService(db_session)
    ticket, _ = await service.create_ticket(
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=_payload(),
        idempotency_key="transition-ticket",
    )

    with pytest.raises(SupportTicketValidationError, match="não permitida"):
        await service.update_ticket(
            ticket,
            actor_user_id=auth_context.user_id,
            payload=SupportTicketUpdate(
                expected_version=ticket.version,
                status=SupportTicketStatus.resolved,
            ),
        )

    for target in (
        SupportTicketStatus.triage,
        SupportTicketStatus.in_progress,
        SupportTicketStatus.resolved,
        SupportTicketStatus.closed,
        SupportTicketStatus.in_progress,
    ):
        previous_version = ticket.version
        ticket = await service.update_ticket(
            ticket,
            actor_user_id=auth_context.user_id,
            payload=SupportTicketUpdate(
                expected_version=previous_version,
                status=target,
            ),
        )
        assert ticket.version == previous_version + 1

    assert ticket.status is SupportTicketStatus.in_progress
    assert ticket.reopened_at == fixed_now
    assert ticket.resolved_at is None
    assert ticket.closed_at is None

    next_now = fixed_now + timedelta(hours=1)
    monkeypatch.setattr(support_service_module, "_now", lambda: next_now)
    ticket = await service.update_ticket(
        ticket,
        actor_user_id=auth_context.user_id,
        payload=SupportTicketUpdate(
            expected_version=ticket.version,
            severity=SupportTicketSeverity.high,
        ),
    )
    assert ticket.first_response_due_at == next_now + timedelta(hours=1)
    assert ticket.resolution_due_at == next_now + timedelta(hours=24)


@pytest.mark.asyncio
async def test_optimistic_conflict_rejects_a_stale_expected_version(
    db_session,
    auth_context,
):
    service = SupportTicketService(db_session)
    ticket, _ = await service.create_ticket(
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=_payload(),
        idempotency_key="optimistic-conflict-ticket",
    )
    stale_version = ticket.version
    await service.update_ticket(
        ticket,
        actor_user_id=auth_context.user_id,
        payload=SupportTicketUpdate(
            expected_version=stale_version,
            status=SupportTicketStatus.triage,
        ),
    )

    with pytest.raises(SupportTicketConflictError, match="outra sessão"):
        await service.update_ticket(
            ticket,
            actor_user_id=auth_context.user_id,
            payload=SupportTicketUpdate(
                expected_version=stale_version,
                priority=SupportTicketPriority.high,
            ),
        )


@pytest.mark.asyncio
async def test_public_replies_drive_first_response_and_waiting_customer_state(
    db_session,
    auth_context,
    support_factory,
    monkeypatch,
):
    fixed_now = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)
    monkeypatch.setattr(support_service_module, "_now", lambda: fixed_now)
    platform_actor, _ = await support_factory.user(
        name="Support Agent",
        platform_role_package="support",
    )
    service = SupportTicketService(db_session)
    ticket, _ = await service.create_ticket(
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=_payload(),
        idempotency_key="reply-state-ticket",
    )

    _, platform_created = await service.add_message(
        ticket,
        actor_user_id=platform_actor.id,
        actor_label=platform_actor.name,
        payload=SupportMessageCreate(body="Resposta da equipe."),
        idempotency_key="platform-public-message",
        platform=True,
    )
    assert platform_created is True
    assert ticket.first_responded_at == fixed_now
    assert ticket.status is SupportTicketStatus.waiting_customer
    assert ticket.version == 2

    _, customer_created = await service.add_message(
        ticket,
        actor_user_id=auth_context.user_id,
        actor_label="Cliente",
        payload=SupportMessageCreate(body="Informacao complementar."),
        idempotency_key="customer-public-message",
        platform=False,
    )
    assert customer_created is True
    assert ticket.status is SupportTicketStatus.in_progress
    assert ticket.version == 3


@pytest.mark.asyncio
async def test_close_reopen_window_and_closed_message_conflict(
    db_session,
    auth_context,
    monkeypatch,
):
    fixed_now = datetime(2026, 7, 24, 18, 0, tzinfo=UTC)
    monkeypatch.setattr(support_service_module, "_now", lambda: fixed_now)
    service = SupportTicketService(db_session)
    ticket, _ = await service.create_ticket(
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=_payload(),
        idempotency_key="close-reopen-ticket",
    )
    ticket.status = SupportTicketStatus.validating
    await db_session.flush()

    closed = await service.close_by_customer(
        ticket,
        actor_user_id=auth_context.user_id,
    )
    assert closed.status is SupportTicketStatus.closed
    assert closed.closed_at == fixed_now

    with pytest.raises(SupportTicketConflictError, match="Reabra"):
        await service.add_message(
            closed,
            actor_user_id=auth_context.user_id,
            actor_label="Cliente",
            payload=SupportMessageCreate(body="Mensagem tardia."),
            idempotency_key="closed-message",
            platform=False,
        )

    closed.closed_at = fixed_now - timedelta(days=31)
    with pytest.raises(SupportTicketConflictError, match="trinta dias"):
        await service.reopen(closed, actor_user_id=auth_context.user_id)

    closed.closed_at = fixed_now - timedelta(days=30)
    reopened = await service.reopen(closed, actor_user_id=auth_context.user_id)
    assert reopened.status is SupportTicketStatus.in_progress
    assert reopened.reopened_at == fixed_now


@pytest.mark.asyncio
async def test_messages_and_events_are_append_only_at_orm_and_database_layers(
    db_session,
    auth_context,
):
    service = SupportTicketService(db_session)
    ticket, _ = await service.create_ticket(
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=_payload(),
        idempotency_key="append-only-ticket",
    )
    message, _ = await service.add_message(
        ticket,
        actor_user_id=auth_context.user_id,
        actor_label="Cliente",
        payload=SupportMessageCreate(body="Evidencia original."),
        idempotency_key="append-only-message",
        platform=False,
    )
    event = await db_session.scalar(
        select(SupportTicketEvent)
        .where(SupportTicketEvent.ticket_id == ticket.id)
        .order_by(SupportTicketEvent.created_at.asc())
    )
    assert event is not None
    message_id = message.id
    event_id = event.id
    await db_session.commit()

    message.body = "Evidencia alterada."
    with pytest.raises(ImmutableRecordError, match="immutable"):
        await db_session.flush()
    await db_session.rollback()

    persisted_event = await db_session.get(SupportTicketEvent, event_id)
    await db_session.delete(persisted_event)
    with pytest.raises(ImmutableRecordError, match="immutable"):
        await db_session.flush()
    await db_session.rollback()

    with pytest.raises(DBAPIError, match="immutable"):
        await db_session.execute(
            text("DELETE FROM support_ticket_messages WHERE id = :id"),
            {"id": str(message_id)},
        )
    await db_session.rollback()
    assert await db_session.scalar(
        select(func.count(SupportTicketMessage.id)).where(
            SupportTicketMessage.id == message_id
        )
    ) == 1
    assert await db_session.scalar(
        select(func.count(SupportTicketEvent.id)).where(
            SupportTicketEvent.id == event_id
        )
    ) == 1


@pytest.mark.asyncio
async def test_runtime_migration_of_technical_request_is_lossless_and_idempotent(
    db_session,
    auth_context,
):
    legacy = TechnicalRequest(
        workspace_id=auth_context.workspace_id,
        created_by_user_id=auth_context.user_id,
        title="Erro legado no protocolo",
        description="Solicitacao tecnica criada antes da Central de SAC.",
        route="/app/protocolos",
        severity=TechnicalRequestSeverity.critical,
        status=TechnicalRequestStatus.resolved,
        evidence=[{"requestId": "legacy-1", "status": 500}],
        resolution="Patch validado pela equipe.",
    )
    db_session.add(legacy)
    await db_session.flush()
    service = SupportTicketService(db_session)

    assert await service.migrate_legacy_requests(
        workspace_id=auth_context.workspace_id
    ) == 1
    assert await service.migrate_legacy_requests(
        workspace_id=auth_context.workspace_id
    ) == 0

    ticket = await db_session.scalar(
        select(SupportTicket).where(
            SupportTicket.legacy_technical_request_id == legacy.id
        )
    )
    assert ticket is not None
    assert ticket.id == legacy.id
    assert ticket.status is SupportTicketStatus.resolved
    assert ticket.severity is SupportTicketSeverity.critical
    assert ticket.queue is SupportTicketQueue.technical
    assert ticket.subject == legacy.title
    assert ticket.description == legacy.description
    assert ticket.route == legacy.route
    assert ticket.resolved_at is not None
    assert await db_session.get(TechnicalRequest, legacy.id) is legacy

    migration_event = await db_session.scalar(
        select(SupportTicketEvent).where(
            SupportTicketEvent.ticket_id == ticket.id,
            SupportTicketEvent.event_type == "legacy_technical_request_migrated",
        )
    )
    assert migration_event is not None
    assert migration_event.after_data["legacyEvidence"] == legacy.evidence
    assert migration_event.after_data["legacyResolution"] == legacy.resolution


def _metric_ticket(
    *,
    workspace_id,
    reporter_user_id,
    created_at: datetime,
    status: SupportTicketStatus,
    sequence: int,
    ticket_type: SupportTicketType = SupportTicketType.problem,
    queue: SupportTicketQueue = SupportTicketQueue.support,
    severity: SupportTicketSeverity = SupportTicketSeverity.medium,
    first_response_due_at: datetime | None = None,
    first_responded_at: datetime | None = None,
    resolved_at: datetime | None = None,
    reopened_at: datetime | None = None,
    satisfaction_score: int | None = None,
    linked_incident_id=None,
    technical_context: dict | None = None,
    route: str = "/app/teste",
) -> SupportTicket:
    return SupportTicket(
        public_number=f"SAC-METRIC-{sequence:04d}",
        workspace_id=workspace_id,
        reporter_user_id=reporter_user_id,
        ticket_type=ticket_type,
        priority=SupportTicketPriority.normal,
        severity=severity,
        queue=queue,
        status=status,
        subject=f"Metric ticket {sequence}",
        description="Registro sintetico para validar metricas.",
        route=route,
        technical_context=technical_context or {},
        idempotency_key=f"metric-{sequence}",
        version=1,
        first_response_due_at=first_response_due_at or created_at + timedelta(hours=4),
        resolution_due_at=created_at + timedelta(days=3),
        first_responded_at=first_responded_at,
        resolved_at=resolved_at,
        reopened_at=reopened_at,
        satisfaction_score=satisfaction_score,
        linked_incident_id=linked_incident_id,
        created_at=created_at,
        updated_at=created_at,
    )


@pytest.mark.asyncio
async def test_metrics_compute_period_comparison_durations_and_distributions(
    db_session,
    auth_context,
    monkeypatch,
):
    now = datetime(2026, 7, 24, 20, 0, tzinfo=UTC)
    monkeypatch.setattr(support_service_module, "_now", lambda: now)
    incident_id = uuid4()
    current_resolved_at = now - timedelta(hours=1)
    current_created_at = now - timedelta(hours=2)
    rows = [
        _metric_ticket(
            workspace_id=auth_context.workspace_id,
            reporter_user_id=auth_context.user_id,
            created_at=current_created_at,
            status=SupportTicketStatus.resolved,
            sequence=1,
            ticket_type=SupportTicketType.instability,
            queue=SupportTicketQueue.technical,
            severity=SupportTicketSeverity.high,
            first_responded_at=current_created_at + timedelta(minutes=10),
            resolved_at=current_resolved_at,
            reopened_at=now - timedelta(minutes=30),
            satisfaction_score=4,
            linked_incident_id=incident_id,
            technical_context={
                "frontendVersion": "2026.07.24",
                "browser": "Chrome 138",
            },
            route="/app/agenda",
        ),
        _metric_ticket(
            workspace_id=auth_context.workspace_id,
            reporter_user_id=auth_context.user_id,
            created_at=now - timedelta(hours=3),
            status=SupportTicketStatus.in_progress,
            sequence=2,
            first_response_due_at=now - timedelta(minutes=1),
        ),
        _metric_ticket(
            workspace_id=auth_context.workspace_id,
            reporter_user_id=auth_context.user_id,
            created_at=now - timedelta(minutes=10),
            status=SupportTicketStatus.new,
            sequence=3,
            ticket_type=SupportTicketType.suggestion,
            first_response_due_at=now + timedelta(minutes=30),
        ),
        _metric_ticket(
            workspace_id=auth_context.workspace_id,
            reporter_user_id=auth_context.user_id,
            created_at=now - timedelta(days=31),
            status=SupportTicketStatus.resolved,
            sequence=4,
            resolved_at=now - timedelta(days=30, hours=23),
        ),
    ]
    db_session.add_all(rows)
    await db_session.flush()

    metrics = await SupportTicketService(db_session).metrics(period_days=30)

    assert metrics["new"] == 3
    assert metrics["resolved"] == 1
    assert metrics["reopened"] == 1
    assert metrics["open"] == 2
    assert metrics["overdue"] == 1
    assert metrics["near_due"] == 1
    assert metrics["reopen_rate_pct"] == 33.33
    assert metrics["satisfaction_average"] == 4.0
    assert metrics["first_response"] == {
        "average_seconds": 600.0,
        "median_seconds": 600.0,
        "p90_seconds": 600.0,
        "p95_seconds": 600.0,
    }
    assert metrics["resolution"] == {
        "average_seconds": 3600.0,
        "median_seconds": 3600.0,
        "p90_seconds": 3600.0,
        "p95_seconds": 3600.0,
    }
    assert metrics["previous_period_change_pct"] == {
        "new": 200.0,
        "resolved": 0.0,
        "reopened": 100.0,
    }
    assert metrics["performance_by_queue"] == [
        {"label": "technical", "value": 1}
    ]
    assert metrics["performance_by_assignee"] == [
        {"label": "Sem responsável", "value": 1}
    ]
    assert metrics["instability_by_version"] == [
        {"label": "2026.07.24", "value": 1}
    ]
    assert metrics["instability_by_page"] == [{"label": "/app/agenda", "value": 1}]
    assert metrics["instability_by_browser"] == [
        {"label": "Chrome 138", "value": 1}
    ]
    assert metrics["suggestions_by_destination"] == [
        {"label": "support", "value": 1}
    ]
    assert metrics["recurrence_by_workspace"] == [
        {"label": "Workspace Teste", "value": 3}
    ]
    assert metrics["related_incidents"] == 1


@pytest.mark.asyncio
async def test_metrics_backlog_includes_open_tickets_older_than_comparison_window(
    db_session,
    auth_context,
    monkeypatch,
):
    now = datetime(2026, 7, 24, 21, 0, tzinfo=UTC)
    monkeypatch.setattr(support_service_module, "_now", lambda: now)
    db_session.add_all(
        [
            _metric_ticket(
                workspace_id=auth_context.workspace_id,
                reporter_user_id=auth_context.user_id,
                created_at=now - timedelta(days=1),
                status=SupportTicketStatus.new,
                sequence=10,
                first_response_due_at=now - timedelta(hours=1),
            ),
            _metric_ticket(
                workspace_id=auth_context.workspace_id,
                reporter_user_id=auth_context.user_id,
                created_at=now - timedelta(days=90),
                status=SupportTicketStatus.in_progress,
                sequence=11,
                first_response_due_at=now - timedelta(days=89),
            ),
        ]
    )
    await db_session.flush()

    metrics = await SupportTicketService(db_session).metrics(period_days=30)

    assert metrics["new"] == 1
    assert metrics["open"] == 2
    assert metrics["overdue"] == 2
    assert {"label": "Mais de 7 dias", "value": 1} in metrics["backlog_by_age"]


@pytest.mark.asyncio
async def test_lifecycle_reminds_closes_and_records_operational_alerts_once(
    db_session,
    auth_context,
    support_factory,
):
    reference_now = datetime(2026, 7, 24, 18, 0, tzinfo=UTC)
    await support_factory.user(
        name="Responsável SAC",
        platform_role_package="support",
    )
    service = SupportTicketService(db_session)

    overdue, _ = await service.create_ticket(
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=_payload(),
        idempotency_key="lifecycle-overdue",
    )
    overdue.first_response_due_at = reference_now - timedelta(hours=2)
    overdue.resolution_due_at = reference_now - timedelta(hours=1)
    created_delivery = await db_session.scalar(
        select(SupportNotificationOutbox).where(
            SupportNotificationOutbox.ticket_id == overdue.id
        )
    )
    assert created_delivery is not None
    created_delivery.status = SupportDeliveryStatus.failed
    created_delivery.last_error_code = "provider_rejected"
    created_delivery.attempts = 1

    reminded, _ = await service.create_ticket(
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=_payload(),
        idempotency_key="lifecycle-reminder",
    )
    reminded.status = SupportTicketStatus.resolved
    reminded.resolved_at = reference_now - timedelta(days=4)

    auto_closed, _ = await service.create_ticket(
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=_payload(),
        idempotency_key="lifecycle-auto-close",
    )
    auto_closed.status = SupportTicketStatus.resolved
    auto_closed.resolved_at = reference_now - timedelta(days=8)
    await db_session.flush()

    first = await service.process_lifecycle(
        workspace_id=auth_context.workspace_id,
        now=reference_now,
    )
    second = await service.process_lifecycle(
        workspace_id=auth_context.workspace_id,
        now=reference_now,
    )

    assert first == {
        "processed": 3,
        "reminded": 1,
        "auto_closed": 1,
        "alerts": 4,
    }
    assert second == {
        "processed": 2,
        "reminded": 0,
        "auto_closed": 0,
        "alerts": 0,
    }
    assert auto_closed.status == SupportTicketStatus.closed
    assert auto_closed.closed_at == reference_now
    event_types = set(
        (
            await db_session.scalars(
                select(SupportTicketEvent.event_type).where(
                    SupportTicketEvent.ticket_id.in_(
                        (overdue.id, reminded.id, auto_closed.id)
                    )
                )
            )
        ).all()
    )
    assert {
        "unassigned_alert",
        "first_response_overdue_alert",
        "resolution_overdue_alert",
        "delivery_failure_alert",
        "resolution_reminder_sent",
        "auto_closed_without_customer_response",
    } <= event_types
