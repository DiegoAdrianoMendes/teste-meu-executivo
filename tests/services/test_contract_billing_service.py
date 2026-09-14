from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.approval import ApprovalStatus
from app.models.client import Client
from app.models.contract_billing import ContractBillingProposal, ContractBillingProposalStatus
from app.models.fee_arrangement import FeeArrangement, FeeArrangementKind
from app.models.finance import FinanceEntry
from app.models.finance_recurrence import FinanceRecurrence
from app.models.matter import Matter, PracticeArea
from app.services.approval_service import ApprovalService
from app.services.contract_billing_service import ContractBillingService


async def _make_matter(db_session, workspace_id) -> Matter:
    client = Client(workspace_id=workspace_id, name="Cliente Contrato F11")
    db_session.add(client)
    await db_session.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client.id,
        practice_area=PracticeArea.civel,
        demand_type="Contrato de honorários F11",
    )
    db_session.add(matter)
    await db_session.flush()
    return matter


def _complete_items() -> list[dict]:
    return [
        {
            "kind": "fixo",
            "valor": 3000.0,
            "vencimento": "2026-08-01",
            "descricao": "Honorário fixo inicial",
            "confidence": 0.95,
        },
        {
            "kind": "entrada",
            "valor": 1000.0,
            "vencimento": "2026-08-01",
            "confidence": 0.9,
        },
        {
            "kind": "parcela",
            "valor": 500.0,
            "vencimento": "2026-09-01",
            "confidence": 0.9,
        },
        {
            "kind": "parcela",
            "valor": 500.0,
            "vencimento": "2026-10-01",
            "confidence": 0.9,
        },
        {
            "kind": "mensalidade",
            "valor": 800.0,
            "vencimento": "2026-08-05",
            "confidence": 0.9,
        },
        {
            "kind": "exito",
            "percentual": 15.0,
            "confidence": 0.9,
        },
        {
            "kind": "por_hora",
            "valor": 250.0,
            "confidence": 0.9,
        },
    ]


def _fake_extractor(items: list[dict], model: str = "fake-extractor-v1"):
    async def extractor(contract_text: str) -> dict:
        assert contract_text  # o texto do contrato sempre é passado ao extractor
        return {"model": model, "items": items}

    return extractor


# ── extração ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_billing_plan_creates_draft_and_no_finance_entries(db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    service = ContractBillingService(db_session, extractor=_fake_extractor(_complete_items()))

    proposal = await service.extract_billing_plan(
        auth_context.workspace_id, matter.id, "texto do contrato de honorários"
    )

    assert proposal.status == ContractBillingProposalStatus.draft
    assert proposal.extraction_model == "fake-extractor-v1"
    assert len(proposal.items) == 7
    assert all(not item["needs_review"] for item in proposal.items)

    entries = (
        await db_session.execute(select(FinanceEntry).where(FinanceEntry.matter_id == matter.id))
    ).scalars().all()
    assert entries == []


@pytest.mark.asyncio
async def test_extract_billing_plan_flags_needs_review(db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    items = [
        {"kind": "fixo", "vencimento": "2026-08-01", "confidence": 0.95},  # falta valor
        {"kind": "mensalidade", "valor": 800.0, "confidence": 0.2},  # baixa confiança
        {"kind": "rotativo_desconhecido", "valor": 1.0},  # kind inválido — descartado
    ]
    service = ContractBillingService(db_session, extractor=_fake_extractor(items))

    proposal = await service.extract_billing_plan(
        auth_context.workspace_id, matter.id, "texto do contrato"
    )

    assert len(proposal.items) == 2
    assert proposal.items[0]["needs_review"] is True  # falta valor
    assert proposal.items[1]["needs_review"] is True  # confiança baixa
    assert proposal.notes is not None and "rotativo_desconhecido" in proposal.notes


# ── submissão ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_for_approval_creates_pending_approval(db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    service = ContractBillingService(db_session, extractor=_fake_extractor(_complete_items()))
    proposal = await service.extract_billing_plan(
        auth_context.workspace_id, matter.id, "texto do contrato"
    )

    approval = await service.submit_for_approval(auth_context.workspace_id, proposal.id)

    assert approval.status == ApprovalStatus.pending
    await db_session.refresh(proposal)
    assert proposal.status == ContractBillingProposalStatus.pending_approval
    assert proposal.approval_id == approval.id


@pytest.mark.asyncio
async def test_submit_twice_is_rejected(db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    service = ContractBillingService(db_session, extractor=_fake_extractor(_complete_items()))
    proposal = await service.extract_billing_plan(
        auth_context.workspace_id, matter.id, "texto do contrato"
    )
    await service.submit_for_approval(auth_context.workspace_id, proposal.id)

    with pytest.raises(HTTPException) as exc_info:
        await service.submit_for_approval(auth_context.workspace_id, proposal.id)
    assert exc_info.value.status_code == 409


# ── aprovação → materialização ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_approve_proposal_materializes_each_kind_correctly(db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    service = ContractBillingService(db_session, extractor=_fake_extractor(_complete_items()))
    proposal = await service.extract_billing_plan(
        auth_context.workspace_id, matter.id, "texto do contrato"
    )
    approval = await service.submit_for_approval(auth_context.workspace_id, proposal.id)

    result = await service.approve_proposal(auth_context.workspace_id, approval.id)

    # fixo — 1 FinanceEntry + 1 FinanceCharge
    assert len(result["fixedEntries"]) == 1
    fixed_entry, fixed_charge = result["fixedEntries"][0]
    assert fixed_entry.amount == Decimal("3000.00")
    assert fixed_charge.entry_id == fixed_entry.id

    # entrada + 2 parcelas — 1 série de 3 parcelas somando 2000
    assert result["installments"] is not None
    installment_ref, installment_entries = result["installments"]
    assert installment_ref
    assert len(installment_entries) == 3
    assert sum((e.amount for e in installment_entries), Decimal("0")) == Decimal("2000.00")

    # mensalidade — 1 FinanceRecurrence
    assert len(result["recurrences"]) == 1
    recurrence = result["recurrences"][0]
    assert isinstance(recurrence, FinanceRecurrence)
    assert recurrence.entry_value == Decimal("800.00")

    # exito + por_hora — 2 FeeArrangement
    assert len(result["arrangements"]) == 2
    kinds = {a.kind for a in result["arrangements"]}
    assert kinds == {FeeArrangementKind.exito, FeeArrangementKind.por_hora}
    exito_arrangement = next(
        a for a in result["arrangements"] if a.kind == FeeArrangementKind.exito
    )
    assert exito_arrangement.percent == Decimal("15.000")
    hourly_arrangement = next(
        a for a in result["arrangements"] if a.kind == FeeArrangementKind.por_hora
    )
    assert hourly_arrangement.hourly_rate == Decimal("250.00")

    await db_session.refresh(proposal)
    assert proposal.status == ContractBillingProposalStatus.approved
    assert proposal.decided_at is not None

    all_arrangements = (
        await db_session.execute(
            select(FeeArrangement).where(FeeArrangement.matter_id == matter.id)
        )
    ).scalars().all()
    assert len(all_arrangements) == 2


@pytest.mark.asyncio
async def test_double_approve_returns_409_and_does_not_duplicate(db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    service = ContractBillingService(db_session, extractor=_fake_extractor(_complete_items()))
    proposal = await service.extract_billing_plan(
        auth_context.workspace_id, matter.id, "texto do contrato"
    )
    approval = await service.submit_for_approval(auth_context.workspace_id, proposal.id)

    await service.approve_proposal(auth_context.workspace_id, approval.id)

    with pytest.raises(HTTPException) as exc_info:
        await service.approve_proposal(auth_context.workspace_id, approval.id)
    assert exc_info.value.status_code == 409

    entries = (
        await db_session.execute(select(FinanceEntry).where(FinanceEntry.matter_id == matter.id))
    ).scalars().all()
    # fixo (1) + série de parcelamento (1 pai + 3 filhas) — nunca duplicado.
    assert len(entries) == 5


@pytest.mark.asyncio
async def test_approve_with_missing_required_field_is_rejected_and_keeps_approval_pending(
    db_session, auth_context
):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    items = [{"kind": "fixo", "vencimento": "2026-08-01", "confidence": 0.95}]  # falta valor
    service = ContractBillingService(db_session, extractor=_fake_extractor(items))
    proposal = await service.extract_billing_plan(
        auth_context.workspace_id, matter.id, "texto do contrato"
    )
    approval = await service.submit_for_approval(auth_context.workspace_id, proposal.id)

    with pytest.raises(HTTPException) as exc_info:
        await service.approve_proposal(auth_context.workspace_id, approval.id)
    assert exc_info.value.status_code == 422

    # A validação falhou ANTES do resolve — a aprovação continua pendente.
    reloaded_approval = await ApprovalService(
        db_session, auth_context.workspace_id
    ).get_by_id(approval.id)
    assert reloaded_approval.status == ApprovalStatus.pending

    entries = (
        await db_session.execute(select(FinanceEntry).where(FinanceEntry.matter_id == matter.id))
    ).scalars().all()
    assert entries == []


# ── rejeição ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reject_proposal_materializes_nothing(db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    service = ContractBillingService(db_session, extractor=_fake_extractor(_complete_items()))
    proposal = await service.extract_billing_plan(
        auth_context.workspace_id, matter.id, "texto do contrato"
    )
    approval = await service.submit_for_approval(auth_context.workspace_id, proposal.id)

    rejected = await service.reject_proposal(
        auth_context.workspace_id, approval.id, resolution_reason="Valores incorretos"
    )

    assert rejected.status == ContractBillingProposalStatus.rejected
    entries = (
        await db_session.execute(select(FinanceEntry).where(FinanceEntry.matter_id == matter.id))
    ).scalars().all()
    assert entries == []

    with pytest.raises(HTTPException) as exc_info:
        await service.approve_proposal(auth_context.workspace_id, approval.id)
    assert exc_info.value.status_code == 409


# ── RLS / isolamento por workspace ────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_proposal_is_isolated_by_workspace(db_session, auth_context):
    other_workspace_id = uuid4()
    other_client = Client(workspace_id=other_workspace_id, name="Cliente Outro Workspace")
    db_session.add(other_client)
    await db_session.flush()
    other_matter = Matter(
        workspace_id=other_workspace_id,
        client_id=other_client.id,
        practice_area=PracticeArea.civel,
        demand_type="Caso de outro workspace",
    )
    db_session.add(other_matter)
    await db_session.flush()

    other_proposal = ContractBillingProposal(
        workspace_id=other_workspace_id,
        matter_id=other_matter.id,
        items=[],
        status=ContractBillingProposalStatus.draft,
    )
    db_session.add(other_proposal)
    await db_session.flush()

    service = ContractBillingService(db_session)
    with pytest.raises(HTTPException) as exc_info:
        await service.get_proposal(auth_context.workspace_id, other_proposal.id)
    assert exc_info.value.status_code == 404
