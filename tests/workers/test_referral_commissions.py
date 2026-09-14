"""F13 — Indicações com comissão: acúmulo no processador de webhook Asaas.

Cobre docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §F13:
  - recebimento com indicação percentual gera despesa pending_approval
    exatamente 1x, mesmo com 3 entregas do webhook;
  - comissão fixa só é lançada na primeira cobrança paga do caso;
  - sem acordo de indicação ativo, nada é gerado;
  - a comissão nasce como despesa exigindo aprovação humana (F8).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.asaas_webhook_receipt import AsaasWebhookReceipt, AsaasWebhookReceiptStatus
from app.models.client import Client
from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType, FinanceExpenseApprovalStatus
from app.models.finance_charge import FinanceCharge, FinanceChargeGateway, FinanceChargeKind, FinanceChargeStatus
from app.models.matter import (
    ClientClassification,
    KanbanColumnId,
    Matter,
    MicroStatus,
    PracticeArea,
    Priority,
    Urgency,
)
from app.models.referral import (
    MatterReferral,
    ReferralCommission,
    ReferralCommissionType,
    ReferralPartner,
    ReferralPartnerType,
)
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


async def _seed_matter(db_session: AsyncSession, workspace: Workspace) -> Matter:
    client = Client(
        workspace_id=workspace.id,
        name="Cliente Indicado",
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
    return matter


async def _seed_partner(
    db_session: AsyncSession,
    workspace: Workspace,
    *,
    comissao_tipo: ReferralCommissionType = ReferralCommissionType.percentual,
    comissao_valor: float = 10.0,
) -> ReferralPartner:
    partner = ReferralPartner(
        workspace_id=workspace.id,
        nome="Parceiro Indicador",
        tipo=ReferralPartnerType.parceiro,
        comissao_tipo=comissao_tipo,
        comissao_valor=comissao_valor,
        vigencia_inicio=date(2020, 1, 1),
        ativo=True,
    )
    db_session.add(partner)
    await db_session.flush()
    return partner


async def _seed_referral(
    db_session: AsyncSession, workspace: Workspace, matter: Matter, partner: ReferralPartner
) -> MatterReferral:
    referral = MatterReferral(
        workspace_id=workspace.id,
        matter_id=matter.id,
        partner_id=partner.id,
        data=datetime.now(timezone.utc),
    )
    db_session.add(referral)
    await db_session.flush()
    return referral


async def _seed_entry_with_charge(
    db_session: AsyncSession,
    workspace: Workspace,
    matter: Matter,
    *,
    external_id: str,
    amount: float = 1000.0,
    charge_status: FinanceChargeStatus = FinanceChargeStatus.confirmada,
) -> tuple[FinanceEntry, FinanceCharge]:
    entry = FinanceEntry(
        workspace_id=workspace.id,
        matter_id=matter.id,
        title="Honorários — cobrança Asaas",
        entry_type=FinanceEntryType.receivable,
        status=FinanceEntryStatus.pending,
        amount=amount,
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
        value=amount,
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
    payment_id: str,
) -> AsaasWebhookReceipt:
    receipt = AsaasWebhookReceipt(
        workspace_id=workspace.id,
        event_id=event_id,
        event_type="PAYMENT_RECEIVED",
        status=AsaasWebhookReceiptStatus.recebido,
        payload={
            "id": event_id,
            "event": "PAYMENT_RECEIVED",
            "payment": {"id": payment_id, "status": "RECEIVED"},
        },
    )
    db_session.add(receipt)
    await db_session.commit()
    await db_session.refresh(receipt)
    return receipt


@pytest.mark.asyncio
async def test_percentual_commission_created_exactly_once_across_three_deliveries(db_session):
    workspace = await _seed_workspace(db_session)
    matter = await _seed_matter(db_session, workspace)
    partner = await _seed_partner(db_session, workspace, comissao_valor=10.0)
    await _seed_referral(db_session, workspace, matter, partner)
    entry, charge = await _seed_entry_with_charge(
        db_session, workspace, matter, external_id="pay_ref_1", amount=1000.0
    )

    session_factory = _session_factory(db_session)
    charge_id, entry_id, partner_id, matter_id = charge.id, entry.id, partner.id, matter.id

    # 3 entregas independentes do mesmo webhook (recibos distintos, mesmo
    # payment_id) — simula reentrega at-least-once do Asaas.
    for i in range(3):
        receipt = await _seed_receipt(
            db_session, workspace, event_id=f"evt-ref-{i}", payment_id="pay_ref_1"
        )
        await finance_tasks._async_process_asaas_webhook_receipt(
            receipt.id, session_factory=session_factory
        )

    db_session.expire_all()
    commissions = (
        await db_session.execute(
            select(ReferralCommission).where(ReferralCommission.charge_id == charge_id)
        )
    ).scalars().all()
    assert len(commissions) == 1
    commission = commissions[0]
    assert commission.partner_id == partner_id
    assert float(commission.comissao_valor_calculado) == pytest.approx(100.0)

    expense = await db_session.get(FinanceEntry, commission.expense_entry_id)
    assert expense.entry_type == FinanceEntryType.expense
    assert expense.approval_status == FinanceExpenseApprovalStatus.pending
    assert expense.matter_id == matter_id


@pytest.mark.asyncio
async def test_fixed_commission_only_on_first_charge_of_matter(db_session):
    workspace = await _seed_workspace(db_session)
    matter = await _seed_matter(db_session, workspace)
    partner = await _seed_partner(
        db_session, workspace, comissao_tipo=ReferralCommissionType.fixo, comissao_valor=500.0
    )
    await _seed_referral(db_session, workspace, matter, partner)

    entry1, charge1 = await _seed_entry_with_charge(
        db_session, workspace, matter, external_id="pay_fixed_1", amount=1000.0
    )
    entry2, charge2 = await _seed_entry_with_charge(
        db_session, workspace, matter, external_id="pay_fixed_2", amount=1000.0
    )

    session_factory = _session_factory(db_session)

    receipt1 = await _seed_receipt(db_session, workspace, event_id="evt-fixed-1", payment_id="pay_fixed_1")
    await finance_tasks._async_process_asaas_webhook_receipt(receipt1.id, session_factory=session_factory)

    receipt2 = await _seed_receipt(db_session, workspace, event_id="evt-fixed-2", payment_id="pay_fixed_2")
    await finance_tasks._async_process_asaas_webhook_receipt(receipt2.id, session_factory=session_factory)

    matter_id, charge1_id = matter.id, charge1.id
    db_session.expire_all()
    commissions = (
        await db_session.execute(
            select(ReferralCommission).where(ReferralCommission.matter_id == matter_id)
        )
    ).scalars().all()
    assert len(commissions) == 1
    assert commissions[0].charge_id == charge1_id
    assert float(commissions[0].comissao_valor_calculado) == pytest.approx(500.0)


@pytest.mark.asyncio
async def test_no_commission_without_active_referral_agreement(db_session):
    workspace = await _seed_workspace(db_session)
    matter = await _seed_matter(db_session, workspace)
    entry, charge = await _seed_entry_with_charge(
        db_session, workspace, matter, external_id="pay_no_ref", amount=1000.0
    )
    receipt = await _seed_receipt(db_session, workspace, event_id="evt-no-ref", payment_id="pay_no_ref")

    matter_id = matter.id
    await finance_tasks._async_process_asaas_webhook_receipt(
        receipt.id, session_factory=_session_factory(db_session)
    )

    db_session.expire_all()
    commissions = (
        await db_session.execute(
            select(ReferralCommission).where(ReferralCommission.matter_id == matter_id)
        )
    ).scalars().all()
    assert commissions == []


@pytest.mark.asyncio
async def test_commission_requires_human_approval_before_payment(db_session):
    workspace = await _seed_workspace(db_session)
    matter = await _seed_matter(db_session, workspace)
    partner = await _seed_partner(db_session, workspace, comissao_valor=20.0)
    await _seed_referral(db_session, workspace, matter, partner)
    entry, charge = await _seed_entry_with_charge(
        db_session, workspace, matter, external_id="pay_approval", amount=2000.0
    )
    receipt = await _seed_receipt(db_session, workspace, event_id="evt-approval", payment_id="pay_approval")

    matter_id = matter.id
    await finance_tasks._async_process_asaas_webhook_receipt(
        receipt.id, session_factory=_session_factory(db_session)
    )

    db_session.expire_all()
    commission = (
        await db_session.execute(
            select(ReferralCommission).where(ReferralCommission.matter_id == matter_id)
        )
    ).scalar_one()
    expense = await db_session.get(FinanceEntry, commission.expense_entry_id)
    # Dinheiro nunca sai sem aprovação humana: despesa nasce pending.
    assert expense.approval_status == FinanceExpenseApprovalStatus.pending
    assert expense.status != FinanceEntryStatus.paid
