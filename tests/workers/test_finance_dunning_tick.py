"""Idempotência, aprovação e gates da régua de cobrança (dunning, F4) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §5 F4.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.workers.finance_tasks as finance_tasks_module
from app.models.client import Client
from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.finance_charge import FinanceCharge, FinanceChargeStatus
from app.models.finance_dunning import (
    FinanceDunningLog,
    FinanceDunningLogStatus,
    FinanceDunningStep,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.workers.finance_tasks import _async_dunning_workspace, approve_dunning_log


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


async def _seed_overdue_charge(
    db_session: AsyncSession,
    workspace: Workspace,
    *,
    days_overdue: int,
    charge_status: FinanceChargeStatus = FinanceChargeStatus.vencida,
    entry_status: FinanceEntryStatus = FinanceEntryStatus.overdue,
) -> tuple[FinanceEntry, FinanceCharge]:
    client = Client(workspace_id=workspace.id, name="Cliente Devedor", phone="+5561999990000")
    db_session.add(client)
    await db_session.flush()

    # Ancorado na DATA local (a mesma régua usada pelo tick via date.today()):
    # usar now(UTC) aqui tornava o teste dependente da hora do dia — após as
    # 21h BRT o UTC já virou o dia e o atraso calculado caía para 0.
    due_date = datetime.combine(
        date.today() - timedelta(days=days_overdue), datetime.min.time(), tzinfo=UTC
    )
    entry = FinanceEntry(
        workspace_id=workspace.id,
        title="Honorários — parcela única",
        entry_type=FinanceEntryType.receivable,
        status=entry_status,
        amount=1000,
        due_date=due_date,
        entry_metadata={"clientId": str(client.id)},
    )
    db_session.add(entry)
    await db_session.flush()

    charge = FinanceCharge(
        workspace_id=workspace.id,
        entry_id=entry.id,
        status=charge_status,
        value=1000,
        due_date=due_date,
    )
    db_session.add(charge)
    await db_session.flush()
    await db_session.commit()
    return entry, charge


@pytest.mark.asyncio
async def test_step_dispatches_once_even_with_ten_ticks(db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(finance_tasks_module, "external_delivery_allowed", lambda: False)
    workspace = await _seed_workspace(db_session)
    entry, charge = await _seed_overdue_charge(db_session, workspace, days_overdue=2)

    session_factory = _session_factory(db_session)
    for _ in range(10):
        await _async_dunning_workspace(
            workspace.id, reference_date=date.today().isoformat(), session_factory=session_factory
        )

    result = await db_session.execute(
        select(FinanceDunningLog).where(FinanceDunningLog.charge_id == charge.id)
    )
    rows = list(result.scalars().all())
    d1_rows = [row for row in rows if row.step == FinanceDunningStep.d1_lembrete]
    assert len(d1_rows) == 1
    # Gates fechados (default fail-closed) => registrado como rascunho local.
    assert d1_rows[0].status == FinanceDunningLogStatus.local_draft
    assert d1_rows[0].reason_code == "gates_closed"


@pytest.mark.asyncio
async def test_gates_closed_produces_local_draft_not_send(db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(finance_tasks_module, "external_delivery_allowed", lambda: False)
    workspace = await _seed_workspace(db_session)
    entry, charge = await _seed_overdue_charge(db_session, workspace, days_overdue=1)

    session_factory = _session_factory(db_session)
    await _async_dunning_workspace(
        workspace.id, reference_date=date.today().isoformat(), session_factory=session_factory)

    row = await db_session.scalar(
        select(FinanceDunningLog).where(
            FinanceDunningLog.charge_id == charge.id,
            FinanceDunningLog.step == FinanceDunningStep.d1_lembrete,
        )
    )
    assert row is not None
    assert row.status == FinanceDunningLogStatus.local_draft
    assert row.requires_approval is False


@pytest.mark.asyncio
async def test_final_step_requires_approval_and_blocks_until_human(
    db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(finance_tasks_module, "external_delivery_allowed", lambda: False)
    workspace = await _seed_workspace(db_session)
    entry, charge = await _seed_overdue_charge(db_session, workspace, days_overdue=15)

    session_factory = _session_factory(db_session)
    await _async_dunning_workspace(
        workspace.id, reference_date=date.today().isoformat(), session_factory=session_factory)

    d15_row = await db_session.scalar(
        select(FinanceDunningLog).where(
            FinanceDunningLog.charge_id == charge.id,
            FinanceDunningLog.step == FinanceDunningStep.d15_formal,
        )
    )
    assert d15_row is not None
    assert d15_row.status == FinanceDunningLogStatus.pending_approval
    assert d15_row.sent_at is None

    # Rodar o tick de novo não deve criar um segundo registro nem disparar
    # o degrau enquanto não houver aprovação humana.
    await _async_dunning_workspace(
        workspace.id, reference_date=date.today().isoformat(), session_factory=session_factory)
    result = await db_session.execute(
        select(FinanceDunningLog).where(
            FinanceDunningLog.charge_id == charge.id,
            FinanceDunningLog.step == FinanceDunningStep.d15_formal,
        )
    )
    assert len(list(result.scalars().all())) == 1

    approved = await approve_dunning_log(db_session, workspace.id, d15_row.id, uuid4())
    await db_session.commit()
    # Gates fechados => aprovação dispara a tentativa, mas cai em rascunho
    # local (fail-closed), nunca em "sent".
    assert approved.status == FinanceDunningLogStatus.local_draft
    assert approved.approved_at is not None


@pytest.mark.asyncio
async def test_paid_charge_never_receives_a_new_step(db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(finance_tasks_module, "external_delivery_allowed", lambda: False)
    workspace = await _seed_workspace(db_session)
    entry, charge = await _seed_overdue_charge(
        db_session,
        workspace,
        days_overdue=20,
        charge_status=FinanceChargeStatus.recebida,
        entry_status=FinanceEntryStatus.paid,
    )

    session_factory = _session_factory(db_session)
    await _async_dunning_workspace(
        workspace.id, reference_date=date.today().isoformat(), session_factory=session_factory)

    result = await db_session.execute(
        select(FinanceDunningLog).where(FinanceDunningLog.charge_id == charge.id)
    )
    assert list(result.scalars().all()) == []


# ── Central de Exceções — falha real de envio da régua ─────────────────────


@pytest.mark.asyncio
async def test_dunning_send_failure_opens_exception_center_item(
    db_session: AsyncSession, monkeypatch
):
    """Antes disto, uma falha real de envio (WhatsApp indisponível, etc.)
    só virava log + `local_draft` — nenhum humano era avisado até o degrau
    final (D+15). Agora abre uma exceção na Central de Exceções (Plano
    Mestre §6/§3) em QUALQUER degrau que falhar o envio real."""
    from unittest.mock import AsyncMock

    from app.models.legal_exception import (
        ExceptionCenterStatus,
        ExceptionKind,
        LegalException,
    )
    from app.services.whatsapp_service import WhatsAppService

    monkeypatch.setattr(finance_tasks_module, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(
        finance_tasks_module,
        "_resolve_dunning_phone",
        AsyncMock(return_value="+5561999990000"),
    )
    monkeypatch.setattr(
        finance_tasks_module,
        "_resolve_dunning_last_inbound_at",
        AsyncMock(return_value=datetime.now(UTC)),
    )
    monkeypatch.setattr(
        WhatsAppService, "_send_text_direct", AsyncMock(return_value=False)
    )

    workspace = await _seed_workspace(db_session)
    entry, charge = await _seed_overdue_charge(db_session, workspace, days_overdue=1)

    log_entry = FinanceDunningLog(
        workspace_id=workspace.id,
        charge_id=charge.id,
        step=FinanceDunningStep.d1_lembrete,
        status=FinanceDunningLogStatus.local_draft,
        requires_approval=False,
        viewed_before_dispatch=False,
        message_preview="Lembrete de cobrança.",
    )
    db_session.add(log_entry)
    await db_session.flush()

    await finance_tasks_module._dispatch_dunning_log(db_session, log_entry, charge, entry)
    await db_session.flush()

    assert log_entry.status == FinanceDunningLogStatus.local_draft
    assert log_entry.reason_code == "send_failed"

    exceptions = (
        await db_session.scalars(
            select(LegalException).where(LegalException.workspace_id == workspace.id)
        )
    ).all()
    assert len(exceptions) == 1
    exception = exceptions[0]
    assert exception.exception_kind == ExceptionKind.acao_externa_nao_autorizada
    assert exception.status == ExceptionCenterStatus.aberta
    assert exception.origin == "finance_dunning.dispatch"
    assert exception.dedupe_key == str(log_entry.id)
    assert exception.payload["chargeId"] == str(charge.id)

    # Reprocessar a mesma linha de log não deve duplicar a exceção.
    await finance_tasks_module._dispatch_dunning_log(db_session, log_entry, charge, entry)
    await db_session.flush()
    exceptions_after_retry = (
        await db_session.scalars(
            select(LegalException).where(LegalException.workspace_id == workspace.id)
        )
    ).all()
    assert len(exceptions_after_retry) == 1
