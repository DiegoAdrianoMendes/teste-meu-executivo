from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.client import Client
from app.models.legal_controladoria import SlaEscalationLevel
from app.models.legal_demand import LegalDemand, LegalDemandNature, LegalDemandPhase
from app.services.legal_sla_service import (
    LegalSlaDemandNotFoundError,
    LegalSlaLogNotFoundError,
    LegalSlaService,
    compute_effective_deadline,
    escalation_level_for,
)

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


async def _make_client(db_session, workspace_id) -> Client:
    client = Client(workspace_id=workspace_id, name="Cliente SLA")
    db_session.add(client)
    await db_session.flush()
    return client


async def _make_demand(db_session, workspace_id, client_id, **overrides) -> LegalDemand:
    defaults = dict(
        workspace_id=workspace_id,
        nature=LegalDemandNature.judicial,
        area="trabalhista",
        client_id=client_id,
        parties=[],
        phase=LegalDemandPhase.em_andamento,
        sla={},
        documents_required=[],
    )
    defaults.update(overrides)
    demand = LegalDemand(**defaults)
    db_session.add(demand)
    await db_session.flush()
    return demand


# ── compute_effective_deadline: pura, sem I/O ────────────────────────────


def test_compute_effective_deadline_picks_earliest_layer():
    demand = LegalDemand(
        nature=LegalDemandNature.judicial,
        prazo_legal_at=NOW + timedelta(days=20),
        prazo_fatal_at=NOW + timedelta(days=10),
        sla_cliente_at=NOW + timedelta(days=15),
        margem_seguranca_dias=None,
    )
    effective, layer = compute_effective_deadline(demand)
    assert layer == "prazo_fatal"
    assert effective == NOW + timedelta(days=10)


def test_compute_effective_deadline_applies_safety_margin():
    demand = LegalDemand(
        nature=LegalDemandNature.judicial,
        prazo_fatal_at=NOW + timedelta(days=10),
        margem_seguranca_dias=3,
    )
    effective, layer = compute_effective_deadline(demand)
    assert layer == "prazo_fatal"
    assert effective == NOW + timedelta(days=7)


def test_compute_effective_deadline_none_when_no_layer_filled():
    demand = LegalDemand(nature=LegalDemandNature.judicial)
    effective, layer = compute_effective_deadline(demand)
    assert effective is None
    assert layer is None


@pytest.mark.parametrize(
    "remaining_days,expected",
    [
        (-1, SlaEscalationLevel.atrasado),
        (0, SlaEscalationLevel.d0),
        (1, SlaEscalationLevel.d1),
        (3, SlaEscalationLevel.d3),
        (7, SlaEscalationLevel.d7),
        (8, None),
    ],
)
def test_escalation_level_thresholds(remaining_days, expected):
    effective_at = NOW + timedelta(days=remaining_days)
    assert escalation_level_for(effective_at, now=NOW) == expected


# ── LegalSlaService.recompute: integração com banco + alerta ────────────


@pytest.mark.asyncio
async def test_recompute_writes_effective_deadline_without_escalating_when_far(
    db_session, auth_context, monkeypatch
):
    client = await _make_client(db_session, auth_context.workspace_id)
    demand = await _make_demand(
        db_session, auth_context.workspace_id, client.id, prazo_fatal_at=NOW + timedelta(days=20)
    )
    service = LegalSlaService(db_session, auth_context.workspace_id)
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(service.alert_service, "notify_sla_escalation", notify)

    updated = await service.recompute(demand.id, now=NOW)

    assert updated.sla_effective_at == NOW + timedelta(days=20)
    assert updated.sla_effective_layer == "prazo_fatal"
    assert updated.sla_escalation_level is None
    notify.assert_not_called()
    escalations = await service.list_escalations(demand.id)
    assert escalations == []


@pytest.mark.asyncio
async def test_recompute_escalates_and_logs_when_within_d7(db_session, auth_context, monkeypatch):
    client = await _make_client(db_session, auth_context.workspace_id)
    demand = await _make_demand(
        db_session, auth_context.workspace_id, client.id, prazo_fatal_at=NOW + timedelta(days=5)
    )
    service = LegalSlaService(db_session, auth_context.workspace_id)
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(service.alert_service, "notify_sla_escalation", notify)

    updated = await service.recompute(demand.id, now=NOW)

    assert updated.sla_escalation_level == SlaEscalationLevel.d7.value
    notify.assert_awaited_once()
    escalations = await service.list_escalations(demand.id)
    assert len(escalations) == 1
    assert escalations[0].level == SlaEscalationLevel.d7
    assert escalations[0].channel_results == {"dispatched": True}
    assert escalations[0].acknowledged_at is None


@pytest.mark.asyncio
async def test_recompute_never_reescalates_same_level_twice(db_session, auth_context, monkeypatch):
    client = await _make_client(db_session, auth_context.workspace_id)
    demand = await _make_demand(
        db_session, auth_context.workspace_id, client.id, prazo_fatal_at=NOW + timedelta(days=5)
    )
    service = LegalSlaService(db_session, auth_context.workspace_id)
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(service.alert_service, "notify_sla_escalation", notify)

    await service.recompute(demand.id, now=NOW)
    await service.recompute(demand.id, now=NOW + timedelta(hours=1))

    notify.assert_awaited_once()
    escalations = await service.list_escalations(demand.id)
    assert len(escalations) == 1


@pytest.mark.asyncio
async def test_recompute_escalates_again_when_severity_increases(db_session, auth_context, monkeypatch):
    client = await _make_client(db_session, auth_context.workspace_id)
    demand = await _make_demand(
        db_session, auth_context.workspace_id, client.id, prazo_fatal_at=NOW + timedelta(days=5)
    )
    service = LegalSlaService(db_session, auth_context.workspace_id)
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(service.alert_service, "notify_sla_escalation", notify)

    await service.recompute(demand.id, now=NOW)  # D-7
    demand.prazo_fatal_at = NOW  # agora vence hoje — D-0, mais severo
    await service.recompute(demand.id, now=NOW)

    assert notify.await_count == 2
    escalations = await service.list_escalations(demand.id)
    levels = {item.level for item in escalations}
    assert levels == {SlaEscalationLevel.d7, SlaEscalationLevel.d0}


@pytest.mark.asyncio
async def test_recompute_skips_terminal_phase_demands(db_session, auth_context, monkeypatch):
    client = await _make_client(db_session, auth_context.workspace_id)
    demand = await _make_demand(
        db_session,
        auth_context.workspace_id,
        client.id,
        prazo_fatal_at=NOW + timedelta(days=1),
        phase=LegalDemandPhase.concluido,
    )
    service = LegalSlaService(db_session, auth_context.workspace_id)
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(service.alert_service, "notify_sla_escalation", notify)

    await service.recompute(demand.id, now=NOW)

    notify.assert_not_called()


@pytest.mark.asyncio
async def test_recompute_unknown_demand_raises(db_session, auth_context):
    service = LegalSlaService(db_session, auth_context.workspace_id)
    with pytest.raises(LegalSlaDemandNotFoundError):
        await service.recompute(uuid4())


@pytest.mark.asyncio
async def test_recompute_due_sweeps_all_workspace_demands(db_session, auth_context, monkeypatch):
    client = await _make_client(db_session, auth_context.workspace_id)
    await _make_demand(
        db_session, auth_context.workspace_id, client.id, prazo_fatal_at=NOW + timedelta(days=5)
    )
    await _make_demand(
        db_session, auth_context.workspace_id, client.id, prazo_fatal_at=NOW + timedelta(days=30)
    )
    service = LegalSlaService(db_session, auth_context.workspace_id)
    monkeypatch.setattr(
        service.alert_service, "notify_sla_escalation", AsyncMock(return_value=True)
    )

    touched = await service.recompute_due(now=NOW)
    assert touched == 2


@pytest.mark.asyncio
async def test_acknowledge_marks_confirmation_of_reading(db_session, auth_context, monkeypatch):
    client = await _make_client(db_session, auth_context.workspace_id)
    demand = await _make_demand(
        db_session, auth_context.workspace_id, client.id, prazo_fatal_at=NOW + timedelta(days=1)
    )
    service = LegalSlaService(db_session, auth_context.workspace_id)
    monkeypatch.setattr(
        service.alert_service, "notify_sla_escalation", AsyncMock(return_value=True)
    )
    await service.recompute(demand.id, now=NOW)
    escalations = await service.list_escalations(demand.id)
    log = escalations[0]
    assert log.acknowledged_at is None

    acknowledged = await service.acknowledge(
        log.id, acknowledged_by_user_id=auth_context.user_id, at=NOW
    )
    assert acknowledged.acknowledged_by_user_id == auth_context.user_id
    assert acknowledged.acknowledged_at == NOW


@pytest.mark.asyncio
async def test_acknowledge_unknown_log_raises(db_session, auth_context):
    service = LegalSlaService(db_session, auth_context.workspace_id)
    with pytest.raises(LegalSlaLogNotFoundError):
        await service.acknowledge(uuid4(), acknowledged_by_user_id=auth_context.user_id)
