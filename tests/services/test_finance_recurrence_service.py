from __future__ import annotations

from datetime import date

import pytest

from app.models.client import Client
from app.models.finance_recurrence import (
    FinanceRecurrenceCycle,
    FinanceRecurrenceKind,
    FinanceRecurrenceStatus,
)
from app.schemas.finance_recurrence import FinanceRecurrenceCreate
from app.services.finance_recurrence_service import (
    FinanceRecurrenceConflictError,
    FinanceRecurrenceService,
)


async def _make_client(db_session, workspace_id):
    client = Client(workspace_id=workspace_id, name="Cliente Recorrência")
    db_session.add(client)
    await db_session.flush()
    return client


@pytest.mark.asyncio
async def test_create_geracao_programada_never_calls_gateway(db_session, auth_context, monkeypatch):
    import app.services.finance_recurrence_service as recurrence_service_module

    monkeypatch.setattr(recurrence_service_module, "external_delivery_allowed", lambda: True)

    called = False

    async def _fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("get_gateway_for_workspace não deveria ser chamado")

    monkeypatch.setattr(recurrence_service_module, "get_gateway_for_workspace", _fail_if_called)

    client = await _make_client(db_session, auth_context.workspace_id)
    service = FinanceRecurrenceService(db_session)
    recurrence = await service.create_recurrence(
        auth_context.workspace_id,
        FinanceRecurrenceCreate(
            client_id=client.id,
            entry_title="Retainer mensal",
            entry_value=1000,
            kind=FinanceRecurrenceKind.geracao_programada,
            cycle=FinanceRecurrenceCycle.mensal,
            next_run=date(2026, 8, 1),
        ),
    )

    assert called is False
    assert recurrence.status == FinanceRecurrenceStatus.ativa
    assert recurrence.gateway_subscription_id is None


@pytest.mark.asyncio
async def test_pause_resume_end_lifecycle(db_session, auth_context, monkeypatch):
    import app.services.finance_recurrence_service as recurrence_service_module

    monkeypatch.setattr(recurrence_service_module, "external_delivery_allowed", lambda: False)

    client = await _make_client(db_session, auth_context.workspace_id)
    service = FinanceRecurrenceService(db_session)
    recurrence = await service.create_recurrence(
        auth_context.workspace_id,
        FinanceRecurrenceCreate(
            client_id=client.id,
            entry_title="Retainer mensal",
            entry_value=1000,
            next_run=date(2026, 8, 1),
        ),
    )

    paused = await service.pause_recurrence(auth_context.workspace_id, recurrence.id)
    assert paused.status == FinanceRecurrenceStatus.pausada

    resumed = await service.resume_recurrence(auth_context.workspace_id, recurrence.id)
    assert resumed.status == FinanceRecurrenceStatus.ativa

    ended = await service.end_recurrence(auth_context.workspace_id, recurrence.id)
    assert ended.status == FinanceRecurrenceStatus.encerrada

    with pytest.raises(FinanceRecurrenceConflictError):
        await service.pause_recurrence(auth_context.workspace_id, recurrence.id)
