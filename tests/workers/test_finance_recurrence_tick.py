"""Idempotência e regras de parada do tick de recorrência (F3) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §5 F3.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.workers.finance_tasks as finance_tasks_module
from app.models.client import Client
from app.models.finance import FinanceEntry, FinanceEntryType
from app.models.finance_charge import FinanceCharge
from app.models.finance_recurrence import (
    FinanceRecurrence,
    FinanceRecurrenceCycle,
    FinanceRecurrenceKind,
    FinanceRecurrenceRun,
    FinanceRecurrenceStatus,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.workers.finance_tasks import _async_finance_recurrence_workspace


def _session_factory(db_session: AsyncSession):
    assert db_session.bind is not None
    return async_sessionmaker(db_session.bind, class_=AsyncSession, expire_on_commit=False)


async def _seed_workspace(db_session: AsyncSession) -> Workspace:
    workspace = Workspace(
        name=f"Escritório {uuid4().hex[:6]}",
        slug=f"escritorio-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    return workspace


async def _seed_client(db_session: AsyncSession, workspace: Workspace) -> Client:
    client = Client(workspace_id=workspace.id, name="Cliente Recorrente")
    db_session.add(client)
    await db_session.flush()
    return client


async def _seed_recurrence(
    db_session: AsyncSession,
    workspace: Workspace,
    client: Client,
    *,
    next_run: date,
    status: FinanceRecurrenceStatus = FinanceRecurrenceStatus.ativa,
    max_count: int | None = None,
    end_date: date | None = None,
) -> FinanceRecurrence:
    recurrence = FinanceRecurrence(
        workspace_id=workspace.id,
        client_id=client.id,
        entry_title="Honorários mensais",
        entry_value=500,
        entry_type=FinanceEntryType.receivable,
        kind=FinanceRecurrenceKind.geracao_programada,
        cycle=FinanceRecurrenceCycle.mensal,
        next_run=next_run,
        end_date=end_date,
        max_count=max_count,
        status=status,
    )
    db_session.add(recurrence)
    await db_session.flush()
    await db_session.commit()
    return recurrence


@pytest.mark.asyncio
async def test_tick_run_ten_times_same_competence_generates_exactly_one_entry(
    db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(finance_tasks_module, "external_delivery_allowed", lambda: False)
    workspace = await _seed_workspace(db_session)
    client = await _seed_client(db_session, workspace)
    today = date(2026, 7, 25)
    recurrence = await _seed_recurrence(db_session, workspace, client, next_run=today)
    workspace_id: UUID = workspace.id
    recurrence_id: UUID = recurrence.id

    session_factory = _session_factory(db_session)
    for _ in range(10):
        await _async_finance_recurrence_workspace(
            workspace_id, reference_date=today.isoformat(), session_factory=session_factory
        )

    db_session.expire_all()
    entries = (
        await db_session.execute(select(FinanceEntry).where(FinanceEntry.workspace_id == workspace_id))
    ).scalars().all()
    assert len(entries) == 1
    assert entries[0].amount == 500

    charges = (
        await db_session.execute(select(FinanceCharge).where(FinanceCharge.workspace_id == workspace_id))
    ).scalars().all()
    assert len(charges) == 1

    runs = (
        await db_session.execute(
            select(FinanceRecurrenceRun).where(FinanceRecurrenceRun.recurrence_id == recurrence_id)
        )
    ).scalars().all()
    assert len(runs) == 1
    assert runs[0].competence == today

    refreshed = await db_session.get(FinanceRecurrence, recurrence_id)
    assert refreshed.generated_count == 1
    assert refreshed.next_run == date(2026, 8, 25)


@pytest.mark.asyncio
async def test_tick_direct_call_ten_times_same_competence_is_idempotent(
    db_session: AsyncSession, monkeypatch
):
    """Chama o tick 10x explicitamente na MESMA competência (sem deixar o
    next_run avançar entre chamadas), simulando execuções concorrentes que
    colidem no `UNIQUE(recurrence_id, competence)`."""
    monkeypatch.setattr(finance_tasks_module, "external_delivery_allowed", lambda: False)
    workspace = await _seed_workspace(db_session)
    client = await _seed_client(db_session, workspace)
    today = date(2026, 7, 25)
    recurrence = await _seed_recurrence(db_session, workspace, client, next_run=today)
    workspace_id: UUID = workspace.id

    for _ in range(10):
        await finance_tasks_module._generate_local_entry_for_competence(db_session, recurrence, today)
        await db_session.commit()

    entries = (
        await db_session.execute(select(FinanceEntry).where(FinanceEntry.workspace_id == workspace_id))
    ).scalars().all()
    assert len(entries) == 1


@pytest.mark.asyncio
async def test_paused_recurrence_never_generates(db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(finance_tasks_module, "external_delivery_allowed", lambda: False)
    workspace = await _seed_workspace(db_session)
    client = await _seed_client(db_session, workspace)
    today = date(2026, 7, 25)
    await _seed_recurrence(
        db_session, workspace, client, next_run=today, status=FinanceRecurrenceStatus.pausada
    )
    workspace_id: UUID = workspace.id

    session_factory = _session_factory(db_session)
    await _async_finance_recurrence_workspace(
        workspace_id, reference_date=today.isoformat(), session_factory=session_factory
    )

    db_session.expire_all()
    entries = (
        await db_session.execute(select(FinanceEntry).where(FinanceEntry.workspace_id == workspace_id))
    ).scalars().all()
    assert entries == []


@pytest.mark.asyncio
async def test_max_count_ends_recurrence_after_last_generation(
    db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(finance_tasks_module, "external_delivery_allowed", lambda: False)
    workspace = await _seed_workspace(db_session)
    client = await _seed_client(db_session, workspace)
    today = date(2026, 7, 25)
    recurrence = await _seed_recurrence(
        db_session, workspace, client, next_run=today, max_count=1
    )
    workspace_id: UUID = workspace.id
    recurrence_id: UUID = recurrence.id

    session_factory = _session_factory(db_session)
    await _async_finance_recurrence_workspace(
        workspace_id, reference_date=today.isoformat(), session_factory=session_factory
    )

    db_session.expire_all()
    refreshed = await db_session.get(FinanceRecurrence, recurrence_id)
    assert refreshed.generated_count == 1
    assert refreshed.status == FinanceRecurrenceStatus.encerrada

    entries = (
        await db_session.execute(select(FinanceEntry).where(FinanceEntry.workspace_id == workspace_id))
    ).scalars().all()
    assert len(entries) == 1

    # Rodar o tick de novo (mesmo com a data avançando) não gera mais nada:
    # a recorrência está encerrada.
    await _async_finance_recurrence_workspace(
        workspace_id,
            reference_date=(today.replace(month=9)).isoformat(),
            session_factory=session_factory,
    )
    db_session.expire_all()
    entries_after = (
        await db_session.execute(select(FinanceEntry).where(FinanceEntry.workspace_id == workspace_id))
    ).scalars().all()
    assert len(entries_after) == 1


@pytest.mark.asyncio
async def test_gate_closed_generates_only_local_draft_charge(
    db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(finance_tasks_module, "external_delivery_allowed", lambda: False)
    workspace = await _seed_workspace(db_session)
    client = await _seed_client(db_session, workspace)
    today = date(2026, 7, 25)
    await _seed_recurrence(db_session, workspace, client, next_run=today)
    workspace_id: UUID = workspace.id

    session_factory = _session_factory(db_session)
    await _async_finance_recurrence_workspace(
        workspace_id, reference_date=today.isoformat(), session_factory=session_factory
    )

    db_session.expire_all()
    charges = (
        await db_session.execute(select(FinanceCharge).where(FinanceCharge.workspace_id == workspace_id))
    ).scalars().all()
    assert len(charges) == 1
    assert charges[0].status.value == "rascunho"
    assert charges[0].external_id is None
