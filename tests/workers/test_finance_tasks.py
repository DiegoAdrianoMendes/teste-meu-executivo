"""Task Celery de processamento do webhook Asaas (F1 — Tarefa C).

Cobre a máquina de estados do charge (docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md
§4.3):
  - PAYMENT_RECEIVED dá baixa na FinanceEntry exatamente uma vez, mesmo
    entregue 3 vezes (reprocessamento idempotente por recibo já processado +
    checagem de status já pago);
  - BANK_SLIP_VIEWED/CHECKOUT_VIEWED nunca regride uma cobrança já recebida;
  - evento desconhecido é registrado no recibo, nunca levanta erro.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.asaas_webhook_receipt import AsaasWebhookReceipt, AsaasWebhookReceiptStatus
from app.models.client import Client
from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.finance_charge import FinanceCharge, FinanceChargeGateway, FinanceChargeKind, FinanceChargeStatus
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import (
    ClientClassification,
    KanbanColumnId,
    Matter,
    MicroStatus,
    PracticeArea,
    Priority,
    Urgency,
)
from app.models.timeline_event import TimelineEvent
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.workers import finance_tasks


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


async def _seed_entry_with_charge(
    db_session: AsyncSession,
    workspace: Workspace,
    *,
    external_id: str,
    charge_status: FinanceChargeStatus = FinanceChargeStatus.criada,
    with_matter: bool = False,
) -> tuple[FinanceEntry, FinanceCharge]:
    matter = None
    if with_matter:
        client = Client(
            workspace_id=workspace.id,
            name="Cliente Teste Financeiro",
            cpf="000.000.000-00",
        )
        db_session.add(client)
        await db_session.flush()
        matter = Matter(
            workspace_id=workspace.id,
            client_id=client.id,
            demand_type="Cobrança de honorários",
            practice_area=PracticeArea.trabalhista,
            column_id=KanbanColumnId.redacao_peca,
            micro_status=MicroStatus.minuta_em_elaboracao,
            priority=Priority.alta,
            urgency=Urgency.urgente,
            classification=ClientClassification.Premium,
        )
        db_session.add(matter)
        await db_session.flush()

    entry = FinanceEntry(
        workspace_id=workspace.id,
        matter_id=matter.id if matter else None,
        title="Honorários — cobrança Asaas",
        entry_type=FinanceEntryType.receivable,
        status=FinanceEntryStatus.pending,
        amount=1500.0,
        currency="BRL",
        due_date=datetime.now(timezone.utc),
    )
    db_session.add(entry)
    await db_session.flush()

    charge = FinanceCharge(
        workspace_id=workspace.id,
        entry_id=entry.id,
        gateway=FinanceChargeGateway.asaas,
        external_id=external_id,
        charge_kind=FinanceChargeKind.avulsa,
        status=charge_status,
        value=1500.0,
    )
    db_session.add(charge)
    await db_session.flush()
    await db_session.commit()
    return entry, charge


async def _seed_receipt(
    db_session: AsyncSession,
    workspace: Workspace,
    *,
    event_id: str,
    event_type: str,
    payment_id: str,
    status: AsaasWebhookReceiptStatus = AsaasWebhookReceiptStatus.recebido,
) -> AsaasWebhookReceipt:
    receipt = AsaasWebhookReceipt(
        workspace_id=workspace.id,
        event_id=event_id,
        event_type=event_type,
        status=status,
        payload={
            "id": event_id,
            "event": event_type,
            "payment": {"id": payment_id, "status": event_type.replace("PAYMENT_", "")},
        },
    )
    db_session.add(receipt)
    await db_session.commit()
    await db_session.refresh(receipt)
    return receipt


@pytest.mark.asyncio
async def test_received_event_pays_entry_exactly_once_even_with_three_deliveries(db_session):
    workspace = await _seed_workspace(db_session)
    entry, charge = await _seed_entry_with_charge(
        db_session, workspace, external_id="pay_received_1", charge_status=FinanceChargeStatus.confirmada
    )
    receipt = await _seed_receipt(
        db_session, workspace, event_id="evt-received-1", event_type="PAYMENT_RECEIVED", payment_id="pay_received_1"
    )

    session_factory = _session_factory(db_session)
    receipt_id, charge_id, entry_id = receipt.id, charge.id, entry.id
    # Simula 3 entregas do mesmo webhook (at-least-once do Asaas).
    for _ in range(3):
        await finance_tasks._async_process_asaas_webhook_receipt(
            receipt_id, session_factory=session_factory
        )

    # A escrita aconteceu em sessões separadas (molde da task Celery real);
    # o identity map desta sessão de teste ainda tem os objetos antigos em
    # memória, então é preciso invalidar antes de reler.
    db_session.expire_all()
    charge = await db_session.get(FinanceCharge, charge_id)
    entry = await db_session.get(FinanceEntry, entry_id)
    assert charge.status == FinanceChargeStatus.recebida
    assert entry.status == FinanceEntryStatus.paid
    assert entry.paid_at is not None

    receipts = (
        await db_session.execute(
            select(AsaasWebhookReceipt).where(AsaasWebhookReceipt.id == receipt_id)
        )
    ).scalar_one()
    assert receipts.status == AsaasWebhookReceiptStatus.processado

    # Auditoria gravada exatamente uma vez para esta transição.
    audit_events = (
        await db_session.execute(
            select(KlmAuditEvent).where(KlmAuditEvent.entity_id == charge_id)
        )
    ).scalars().all()
    assert len(audit_events) == 1


@pytest.mark.asyncio
async def test_received_event_writes_matter_timeline_event(db_session):
    workspace = await _seed_workspace(db_session)
    entry, charge = await _seed_entry_with_charge(
        db_session,
        workspace,
        external_id="pay_received_matter",
        charge_status=FinanceChargeStatus.confirmada,
        with_matter=True,
    )
    receipt = await _seed_receipt(
        db_session,
        workspace,
        event_id="evt-received-matter-1",
        event_type="PAYMENT_RECEIVED",
        payment_id="pay_received_matter",
    )

    matter_id = entry.matter_id
    await finance_tasks._async_process_asaas_webhook_receipt(
        receipt.id, session_factory=_session_factory(db_session)
    )
    db_session.expire_all()

    timeline_events = (
        await db_session.execute(
            select(TimelineEvent).where(TimelineEvent.matter_id == matter_id)
        )
    ).scalars().all()
    assert len(timeline_events) == 1
    assert "recebida" in timeline_events[0].title


@pytest.mark.asyncio
async def test_viewed_event_never_regresses_a_received_charge(db_session):
    workspace = await _seed_workspace(db_session)
    entry, charge = await _seed_entry_with_charge(
        db_session, workspace, external_id="pay_viewed_1", charge_status=FinanceChargeStatus.recebida
    )
    entry.status = FinanceEntryStatus.paid
    entry.paid_at = datetime.now(timezone.utc)
    await db_session.commit()

    receipt = await _seed_receipt(
        db_session,
        workspace,
        event_id="evt-viewed-late-1",
        event_type="PAYMENT_BANK_SLIP_VIEWED",
        payment_id="pay_viewed_1",
    )

    receipt_id, charge_id, entry_id = receipt.id, charge.id, entry.id
    await finance_tasks._async_process_asaas_webhook_receipt(
        receipt_id, session_factory=_session_factory(db_session)
    )

    db_session.expire_all()
    charge = await db_session.get(FinanceCharge, charge_id)
    entry = await db_session.get(FinanceEntry, entry_id)
    assert charge.status == FinanceChargeStatus.recebida
    assert entry.status == FinanceEntryStatus.paid


@pytest.mark.asyncio
async def test_unknown_event_type_is_processed_without_error(db_session):
    workspace = await _seed_workspace(db_session)
    await _seed_entry_with_charge(db_session, workspace, external_id="pay_unknown_1")
    receipt = await _seed_receipt(
        db_session,
        workspace,
        event_id="evt-unknown-1",
        event_type="PAYMENT_TOTALLY_NEW_EVENT_FROM_ASAAS",
        payment_id="pay_unknown_1",
    )

    receipt_id = receipt.id
    await finance_tasks._async_process_asaas_webhook_receipt(
        receipt_id, session_factory=_session_factory(db_session)
    )
    db_session.expire_all()

    updated = (
        await db_session.execute(
            select(AsaasWebhookReceipt).where(AsaasWebhookReceipt.id == receipt_id)
        )
    ).scalar_one()
    assert updated.status == AsaasWebhookReceiptStatus.processado
    assert updated.payload.get("_processingNote") == "evento_desconhecido_ignorado"


@pytest.mark.asyncio
async def test_reprocessing_an_already_processed_receipt_is_a_no_op(db_session):
    workspace = await _seed_workspace(db_session)
    entry, charge = await _seed_entry_with_charge(
        db_session, workspace, external_id="pay_reprocess_1", charge_status=FinanceChargeStatus.confirmada
    )
    receipt = await _seed_receipt(
        db_session, workspace, event_id="evt-reprocess-1", event_type="PAYMENT_RECEIVED", payment_id="pay_reprocess_1"
    )

    session_factory = _session_factory(db_session)
    receipt_id, charge_id, entry_id = receipt.id, charge.id, entry.id
    await finance_tasks._async_process_asaas_webhook_receipt(receipt_id, session_factory=session_factory)
    db_session.expire_all()
    entry = await db_session.get(FinanceEntry, entry_id)
    first_paid_at = entry.paid_at

    # Retry do broker sobre um recibo já concluído.
    await finance_tasks._async_process_asaas_webhook_receipt(receipt_id, session_factory=session_factory)
    db_session.expire_all()
    entry = await db_session.get(FinanceEntry, entry_id)
    assert entry.paid_at == first_paid_at
    audit_events = (
        await db_session.execute(select(KlmAuditEvent).where(KlmAuditEvent.entity_id == charge_id))
    ).scalars().all()
    assert len(audit_events) == 1
