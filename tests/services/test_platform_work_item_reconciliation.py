from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.platform_work_item import PlatformWorkItem
from app.models.support_ticket import (
    SupportTicket,
    SupportTicketPriority,
    SupportTicketQueue,
    SupportTicketSeverity,
    SupportTicketStatus,
    SupportTicketType,
)
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.platform_work_item_service import PlatformWorkItemService


async def _workspace(db_session, *, label: str) -> Workspace:
    workspace = Workspace(
        name=f"Conta {label}",
        slug=f"reconcile-{label}-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        onboarding_completed=False,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    return workspace


async def _ticket(
    db_session,
    *,
    workspace: Workspace,
    actor: User,
    label: str,
) -> SupportTicket:
    now = datetime.now(UTC)
    ticket = SupportTicket(
        public_number=f"BATCH-{label}-{uuid4().hex[:6].upper()}",
        workspace_id=workspace.id,
        reporter_user_id=actor.id,
        ticket_type=SupportTicketType.problem,
        priority=SupportTicketPriority.normal,
        severity=SupportTicketSeverity.medium,
        queue=SupportTicketQueue.technical,
        status=SupportTicketStatus.new,
        subject=f"Ticket {label}",
        description="Conteúdo privado não projetado.",
        technical_context={},
        idempotency_key=uuid4().hex,
        version=1,
        first_response_due_at=now + timedelta(hours=1),
        resolution_due_at=now + timedelta(hours=8),
    )
    db_session.add(ticket)
    await db_session.flush()
    return ticket


@pytest.mark.asyncio
async def test_keyset_batches_cross_multiple_pages_without_truncation(
    db_session,
) -> None:
    workspaces = [await _workspace(db_session, label=f"keyset-{index}") for index in range(7)]
    expected_ids = sorted(workspace.id for workspace in workspaces)
    service = PlatformWorkItemService(db_session)

    batches = [
        [row.id for row in rows]
        async for rows in service._keyset_batches(
            Workspace,
            clauses=(Workspace.id.in_(expected_ids),),
            batch_size=2,
        )
    ]

    assert [len(batch) for batch in batches] == [2, 2, 2, 1]
    assert [item_id for batch in batches for item_id in batch] == expected_ids


@pytest.mark.asyncio
async def test_reconcile_acquires_source_locks_in_canonical_order(
    db_session,
    monkeypatch,
) -> None:
    acquired: list[tuple[str, str]] = []

    async def capture_lock(
        session,
        *,
        namespace: str,
        value: str,
    ) -> bool:
        assert session is db_session
        acquired.append((namespace, value))
        return True

    monkeypatch.setattr(
        "app.services.platform_work_item_service.acquire_transaction_advisory_lock",
        capture_lock,
    )
    service = PlatformWorkItemService(db_session)

    result = await service.reconcile(
        source_types=["configuration", "support_ticket"],
    )

    assert [row.status for row in result] == ["available", "available"]
    assert acquired == [
        ("platform-work-item-reconciliation", "support_ticket"),
        ("platform-work-item-reconciliation", "configuration"),
    ]


@pytest.mark.asyncio
async def test_ticket_reconciliation_processes_every_batch_and_replay_is_idempotent(
    db_session,
    platform_auth_context,
) -> None:
    actor = await db_session.get(User, platform_auth_context.user_id)
    workspace = await _workspace(db_session, label="tickets")
    tickets = [
        await _ticket(
            db_session,
            workspace=workspace,
            actor=actor,
            label=str(index),
        )
        for index in range(7)
    ]
    service = PlatformWorkItemService(db_session)
    service.reconciliation_batch_size = 2

    first = (await service.reconcile(source_types=["support_ticket"]))[0]
    second = (await service.reconcile(source_types=["support_ticket"]))[0]

    items = list(
        (
            await db_session.scalars(
                select(PlatformWorkItem).where(
                    PlatformWorkItem.source_type == "support_ticket",
                    PlatformWorkItem.source_id.in_({ticket.id for ticket in tickets}),
                )
            )
        ).all()
    )
    assert first.status == "available"
    assert first.projected == 7
    assert first.unchanged == 0
    assert second.status == "available"
    assert second.projected == 0
    assert second.unchanged == 7
    assert {item.source_id for item in items} == {ticket.id for ticket in tickets}
    assert {item.source_version for item in items} == {"1"}
    assert {item.projection_version for item in items} == {1}


@pytest.mark.asyncio
async def test_incomplete_onboarding_scan_rolls_back_and_never_retires_unseen(
    db_session,
    monkeypatch,
    caplog,
) -> None:
    workspaces = [await _workspace(db_session, label=f"retire-{index}") for index in range(5)]
    service = PlatformWorkItemService(db_session)
    service.reconciliation_batch_size = 2
    initial = (await service.reconcile(source_types=["workspace_onboarding"]))[0]
    assert initial.status == "available"

    removed_workspace = workspaces[-1]
    removed_workspace.deleted_at = datetime.now(UTC)
    await db_session.flush()
    removed_item = await db_session.scalar(
        select(PlatformWorkItem).where(
            PlatformWorkItem.dedupe_key == f"workspace_onboarding:{removed_workspace.id}"
        )
    )
    assert removed_item is not None
    assert removed_item.state == "open"
    initial_projection_version = removed_item.projection_version

    original_batches = service._keyset_batches

    async def fail_after_first_workspace_page(
        model: Any,
        **kwargs: Any,
    ):
        async for rows in original_batches(model, **kwargs):
            yield rows
            if model is Workspace:
                raise RuntimeError("synthetic partial source scan")

    monkeypatch.setattr(service, "_keyset_batches", fail_after_first_workspace_page)
    with caplog.at_level(
        logging.ERROR,
        logger="app.services.platform_work_item_service",
    ):
        failed = (await service.reconcile(source_types=["workspace_onboarding"]))[0]

    await db_session.refresh(removed_item)
    assert failed.status == "failed"
    assert failed.reason_code == "source_reconciliation_failed"
    assert "source_type=workspace_onboarding" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "synthetic partial source scan" not in caplog.text
    assert removed_item.state == "open"
    assert removed_item.projection_version == initial_projection_version

    locked_models: list[Any] = []

    async def observe_locked_retirement(
        model: Any,
        **kwargs: Any,
    ):
        if kwargs.get("for_update"):
            locked_models.append(model)
        async for rows in original_batches(model, **kwargs):
            yield rows

    monkeypatch.setattr(service, "_keyset_batches", observe_locked_retirement)
    completed = (await service.reconcile(source_types=["workspace_onboarding"]))[0]

    await db_session.refresh(removed_item)
    assert completed.status == "available"
    assert locked_models == [PlatformWorkItem]
    assert removed_item.state == "stale"
    assert removed_item.allowed_commands == []
    assert removed_item.projection_version == initial_projection_version + 1
