"""S6-C: os três ticks que gravavam na sessão de descoberta agora só descobrem
e despacham uma task por workspace, que roda no contexto do workspace.

Cada teste prova três coisas: o tick abre o escopo da família certa, despacha
exatamente os workspaces com trabalho pendente (e nenhum outro), e não grava
nada por conta própria.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.workers.finance_tasks as finance_tasks
import app.workers.whatsapp_campaign_tasks as campaign_tasks
from app.models.client import Client
from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.finance_charge import FinanceCharge, FinanceChargeStatus
from app.models.finance_dunning import FinanceDunningLog
from app.models.finance_recurrence import (
    FinanceRecurrence,
    FinanceRecurrenceCycle,
    FinanceRecurrenceKind,
    FinanceRecurrenceStatus,
)
from app.models.whatsapp import WaConnectionStatus, WhatsAppConnection
from app.models.whatsapp_campaign import (
    CampaignRecipientStatus,
    CampaignSendJobStatus,
    CampaignStatus,
    WhatsAppCampaign,
    WhatsAppCampaignRecipient,
    WhatsAppCampaignSendJob,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus


def _session_factory(db_session: AsyncSession):
    assert db_session.bind is not None
    return async_sessionmaker(db_session.bind, class_=AsyncSession, expire_on_commit=False)


async def _workspace(db_session: AsyncSession) -> Workspace:
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


def _espiao_de_escopo(monkeypatch, modulo) -> list[str]:
    abertos: list[str] = []

    async def espiao(session, *, family):
        abertos.append(family)

    monkeypatch.setattr(modulo, "set_worker_discovery_context", espiao)
    return abertos


def _gravador(monkeypatch, task) -> list[dict]:
    chamadas: list[dict] = []
    monkeypatch.setattr(task, "apply_async", lambda **kw: chamadas.append(kw))
    return chamadas


# --- recorrência ---------------------------------------------------------


async def _recorrencia(db_session, workspace, *, next_run: date, status) -> FinanceRecurrence:
    client = Client(workspace_id=workspace.id, name="Cliente Recorrente")
    db_session.add(client)
    await db_session.flush()
    recurrence = FinanceRecurrence(
        workspace_id=workspace.id,
        client_id=client.id,
        entry_title="Honorários mensais",
        entry_value=500,
        entry_type=FinanceEntryType.receivable,
        kind=FinanceRecurrenceKind.geracao_programada,
        cycle=FinanceRecurrenceCycle.mensal,
        next_run=next_run,
        status=status,
    )
    db_session.add(recurrence)
    await db_session.flush()
    return recurrence


@pytest.mark.asyncio
async def test_tick_de_recorrencia_so_descobre_e_despacha_por_workspace(db_session, monkeypatch):
    hoje = date(2026, 9, 4)
    vencida = await _workspace(db_session)
    pausada = await _workspace(db_session)
    futura = await _workspace(db_session)
    await _recorrencia(db_session, vencida, next_run=hoje, status=FinanceRecurrenceStatus.ativa)
    await _recorrencia(db_session, pausada, next_run=hoje, status=FinanceRecurrenceStatus.pausada)
    await _recorrencia(
        db_session, futura, next_run=hoje + timedelta(days=1), status=FinanceRecurrenceStatus.ativa
    )
    await db_session.commit()

    escopos = _espiao_de_escopo(monkeypatch, finance_tasks)
    despachos = _gravador(monkeypatch, finance_tasks.finance_recurrence_workspace)

    resultado = await finance_tasks._async_finance_recurrence_tick(
        reference_date=hoje.isoformat(), session_factory=_session_factory(db_session)
    )

    assert escopos == ["finance_recurrence"]
    assert resultado == {"candidates": 1, "dispatched": 1}
    assert despachos == [
        {"kwargs": {"workspace_id": str(vencida.id), "reference_date": "2026-09-04"}}
    ]
    entradas = (await db_session.execute(select(FinanceEntry))).scalars().all()
    assert entradas == [], "o tick não gera lançamento: isso é da task por workspace"


@pytest.mark.asyncio
async def test_tick_de_recorrencia_sem_trabalho_nao_despacha(db_session, monkeypatch):
    despachos = _gravador(monkeypatch, finance_tasks.finance_recurrence_workspace)
    resultado = await finance_tasks._async_finance_recurrence_tick(
        reference_date="2026-09-04", session_factory=_session_factory(db_session)
    )
    assert resultado == {"candidates": 0, "dispatched": 0}
    assert despachos == []


# --- cobrança (dunning) --------------------------------------------------


async def _cobranca(db_session, workspace, *, dias_de_atraso: int, paga: bool = False):
    client = Client(workspace_id=workspace.id, name="Cliente Devedor", phone="+5561999990000")
    db_session.add(client)
    await db_session.flush()
    vencimento = datetime.combine(
        date(2026, 9, 4) - timedelta(days=dias_de_atraso), datetime.min.time(), tzinfo=UTC
    )
    entry = FinanceEntry(
        workspace_id=workspace.id,
        title="Honorários",
        entry_type=FinanceEntryType.receivable,
        status=FinanceEntryStatus.paid if paga else FinanceEntryStatus.overdue,
        amount=1000,
        due_date=vencimento,
        entry_metadata={"clientId": str(client.id)},
    )
    db_session.add(entry)
    await db_session.flush()
    charge = FinanceCharge(
        workspace_id=workspace.id,
        entry_id=entry.id,
        status=FinanceChargeStatus.recebida if paga else FinanceChargeStatus.vencida,
        value=1000,
        due_date=vencimento,
    )
    db_session.add(charge)
    await db_session.flush()


@pytest.mark.asyncio
async def test_tick_de_cobranca_so_descobre_e_despacha_por_workspace(db_session, monkeypatch):
    devedor = await _workspace(db_session)
    quitado = await _workspace(db_session)
    await _cobranca(db_session, devedor, dias_de_atraso=3)
    await _cobranca(db_session, devedor, dias_de_atraso=10)  # duas cobranças, um despacho
    await _cobranca(db_session, quitado, dias_de_atraso=3, paga=True)
    await db_session.commit()

    escopos = _espiao_de_escopo(monkeypatch, finance_tasks)
    despachos = _gravador(monkeypatch, finance_tasks.dunning_workspace)

    resultado = await finance_tasks._async_dunning_tick(
        reference_date="2026-09-04", session_factory=_session_factory(db_session)
    )

    assert escopos == ["dunning"]
    assert resultado == {"candidates": 1, "dispatched": 1}
    assert despachos == [
        {"kwargs": {"workspace_id": str(devedor.id), "reference_date": "2026-09-04"}}
    ]
    logs = (await db_session.execute(select(FinanceDunningLog))).scalars().all()
    assert logs == [], "o tick não roda a régua: isso é da task por workspace"


# --- claims órfãos de campanha -------------------------------------------


async def _job_em_processing(db_session, workspace, *, last_error: str | None = None):
    connection = WhatsAppConnection(
        workspace_id=workspace.id,
        phone_number_id=f"sweep-{uuid4().hex[:8]}",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    campaign = WhatsAppCampaign(
        workspace_id=workspace.id,
        whatsapp_connection_id=connection.id,
        title="Campanha",
        status=CampaignStatus.running,
    )
    db_session.add(campaign)
    await db_session.flush()
    recipient = WhatsAppCampaignRecipient(
        campaign_id=campaign.id,
        workspace_id=workspace.id,
        phone=f"5511{uuid4().int % 10**9:09d}",
        status=CampaignRecipientStatus.processing,
        send_context={"claim": {"claimedAt": (datetime.now(UTC) - timedelta(hours=2)).isoformat()}},
    )
    db_session.add(recipient)
    await db_session.flush()
    job = WhatsAppCampaignSendJob(
        campaign_id=campaign.id,
        recipient_id=recipient.id,
        workspace_id=workspace.id,
        whatsapp_connection_id=connection.id,
        status=CampaignSendJobStatus.processing,
        last_error=last_error,
    )
    db_session.add(job)
    await db_session.flush()
    return job


@pytest.mark.asyncio
async def test_sweep_de_claims_so_descobre_e_despacha_por_workspace(db_session, monkeypatch):
    preso = await _workspace(db_session)
    ja_marcado = await _workspace(db_session)
    job_preso = await _job_em_processing(db_session, preso)
    await _job_em_processing(db_session, ja_marcado, last_error="já sinalizado")
    await db_session.commit()

    escopos = _espiao_de_escopo(monkeypatch, campaign_tasks)
    despachos = _gravador(monkeypatch, campaign_tasks.sweep_orphan_campaign_claims_workspace)

    despachados = await campaign_tasks._sweep_orphan_campaign_claims(
        session_factory=_session_factory(db_session)
    )

    assert escopos == ["whatsapp_campaign_claims"]
    assert despachados == 1
    assert despachos == [{"kwargs": {"workspace_id": str(preso.id)}}]
    async with _session_factory(db_session)() as leitura:
        atual = await leitura.get(WhatsAppCampaignSendJob, job_preso.id)
        assert atual is not None
        assert atual.status == CampaignSendJobStatus.processing, "o sweep global não marca nada"


def test_task_do_beat_descobre_e_task_por_workspace_varre(monkeypatch):
    chamadas: list[tuple[str, tuple]] = []

    async def descobre(**kw):
        chamadas.append(("descoberta", ()))
        return 0

    async def varre(workspace_id, **kw):
        chamadas.append(("workspace", (workspace_id,)))
        return 0

    monkeypatch.setattr(campaign_tasks, "_sweep_orphan_campaign_claims", descobre)
    monkeypatch.setattr(campaign_tasks, "_sweep_orphan_campaign_claims_for_workspace", varre)
    campaign_tasks.sweep_orphan_campaign_claims.run()
    campaign_tasks.sweep_orphan_campaign_claims_workspace.run(workspace_id="ws-1")
    assert chamadas == [("descoberta", ()), ("workspace", ("ws-1",))]
    assert (
        campaign_tasks.sweep_orphan_campaign_claims.name
        != campaign_tasks.sweep_orphan_campaign_claims_workspace.name
    ), "a telemetria do beat conta por nome: a variante por workspace precisa do seu"


@pytest.mark.asyncio
async def test_workspace_inativo_ou_deletado_nao_e_despachado(db_session, monkeypatch):
    hoje = date(2026, 9, 4)
    inativo = await _workspace(db_session)
    inativo.is_active = False
    deletado = await _workspace(db_session)
    deletado.deleted_at = datetime.now(UTC)
    for workspace in (inativo, deletado):
        await _recorrencia(
            db_session, workspace, next_run=hoje, status=FinanceRecurrenceStatus.ativa
        )
        await _cobranca(db_session, workspace, dias_de_atraso=3)
        await _job_em_processing(db_session, workspace)
    await db_session.commit()

    recorrencias = _gravador(monkeypatch, finance_tasks.finance_recurrence_workspace)
    cobrancas = _gravador(monkeypatch, finance_tasks.dunning_workspace)
    sweeps = _gravador(monkeypatch, campaign_tasks.sweep_orphan_campaign_claims_workspace)
    fabrica = _session_factory(db_session)
    await finance_tasks._async_finance_recurrence_tick(
        reference_date=hoje.isoformat(), session_factory=fabrica
    )
    await finance_tasks._async_dunning_tick(
        reference_date=hoje.isoformat(), session_factory=fabrica
    )
    await campaign_tasks._sweep_orphan_campaign_claims(session_factory=fabrica)
    assert recorrencias == [] and cobrancas == [] and sweeps == []
