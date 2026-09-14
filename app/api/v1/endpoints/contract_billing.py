"""
Endpoints: Contrato → nós financeiros com aprovação (F11) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md seção F11.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_workspace_id, get_db, require_finance_access
from app.models.contract_billing import ContractBillingProposal
from app.schemas.contract_billing import (
    ContractBillingProposalRead,
    ContractBillingProposalUpdate,
    ExtractBillingPlanRequest,
    RejectProposalRequest,
    SubmitProposalRequest,
)
from app.schemas.fee_arrangement import FinanceEntrySummaryRead
from app.schemas.finance_charge import FinanceChargeRead
from app.schemas.finance_recurrence import FinanceRecurrenceRead
from app.services.contract_billing_service import ContractBillingService, ContractExtractor
from app.services.contract_command_service import ContractCommandService

router = APIRouter(
    prefix="/finance/contract-billing-proposals",
    tags=["Financeiro — Contrato → Cobrança (F11)"],
)


def _proposal_read(proposal: ContractBillingProposal) -> ContractBillingProposalRead:
    return ContractBillingProposalRead.model_validate(proposal)


async def get_contract_billing_extractor() -> ContractExtractor | None:
    """Ponto de override para teste — em produção resolve `None` e o service
    usa o gateway de IA real (`ContractBillingService._extract_via_gateway`).
    Testes fazem `app.dependency_overrides[get_contract_billing_extractor] = ...`
    para injetar um extractor fake, sem tocar o gateway/gate."""
    return None


@router.post(
    "/extract",
    response_model=ContractBillingProposalRead,
    status_code=status.HTTP_201_CREATED,
    summary="Extrair plano de cobrança de um contrato — sempre nasce em draft",
)
async def extract_billing_plan(
    payload: ExtractBillingPlanRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
    extractor: ContractExtractor | None = Depends(get_contract_billing_extractor),
):
    proposal = await ContractBillingService(db, extractor=extractor).extract_billing_plan(
        workspace_id,
        payload.matter_id,
        payload.contract_text,
        source_document_name=payload.source_document_name,
        requested_by=payload.requested_by,
    )
    await db.commit()
    await db.refresh(proposal)
    return _proposal_read(proposal)


@router.get(
    "/{proposal_id}",
    response_model=ContractBillingProposalRead,
    summary="Detalhe de uma proposta de cobrança extraída",
)
async def get_billing_proposal(
    proposal_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    proposal = await ContractBillingService(db).get_proposal(workspace_id, proposal_id)
    return _proposal_read(proposal)


@router.patch(
    "/{proposal_id}",
    response_model=ContractBillingProposalRead,
    summary="Atualizar proposta em rascunho por comando auditável",
)
async def update_billing_proposal(
    proposal_id: UUID,
    payload: ContractBillingProposalUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    values = payload.model_dump(exclude_unset=True)
    expected_updated_at = values.pop("expected_updated_at", None)
    proposal = await ContractCommandService(db).update(
        workspace_id=workspace_id,
        proposal_id=proposal_id,
        changes=values,
        actor_user_id=auth.user_id,
        expected_updated_at=expected_updated_at,
        idempotency_key=idempotency_key,
        source="contract_billing_endpoint",
    )
    await db.commit()
    await db.refresh(proposal)
    return _proposal_read(proposal)


@router.post(
    "/{proposal_id}/submit",
    summary="Submeter proposta para aprovação humana do financeiro",
)
async def submit_billing_proposal(
    proposal_id: UUID,
    payload: SubmitProposalRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    approval = await ContractBillingService(db).submit_for_approval(
        workspace_id, proposal_id, requested_by=payload.requested_by
    )
    await db.commit()
    return {"approvalId": str(approval.id), "status": approval.status.value}


@router.post(
    "/approvals/{approval_id}/approve",
    summary="Aprovar plano de cobrança — só aqui os lançamentos nascem",
)
async def approve_billing_proposal(
    approval_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    # O autor da aprovação é SEMPRE derivado do contexto autenticado — nunca do
    # payload (que o cliente pode forjar). Mesmo padrão de finance_dunning.py.
    materialized = await ContractBillingService(db).approve_proposal(
        workspace_id, approval_id, resolved_by=auth.user_id
    )
    await db.commit()

    fixed_out = []
    for entry, charge in materialized["fixedEntries"]:
        await db.refresh(entry)
        await db.refresh(charge)
        fixed_out.append(
            {
                "entry": FinanceEntrySummaryRead(
                    id=entry.id,
                    title=entry.title,
                    amount=entry.amount,
                    due_date=entry.due_date,
                    status=entry.status.value,
                    external_ref=entry.external_ref,
                ).model_dump(mode="json", by_alias=True),
                "charge": FinanceChargeRead.model_validate(charge).model_dump(
                    mode="json", by_alias=True
                ),
            }
        )

    installments_out = None
    if materialized["installments"] is not None:
        installment_ref, entries = materialized["installments"]
        for entry in entries:
            await db.refresh(entry)
        installments_out = {
            "installmentRef": installment_ref,
            "entries": [
                FinanceEntrySummaryRead(
                    id=entry.id,
                    title=entry.title,
                    amount=entry.amount,
                    due_date=entry.due_date,
                    status=entry.status.value,
                    external_ref=entry.external_ref,
                ).model_dump(mode="json", by_alias=True)
                for entry in entries
            ],
        }

    recurrences_out = []
    for recurrence in materialized["recurrences"]:
        await db.refresh(recurrence)
        recurrences_out.append(
            FinanceRecurrenceRead.model_validate(recurrence).model_dump(mode="json", by_alias=True)
        )

    arrangements_out = []
    for arrangement in materialized["arrangements"]:
        await db.refresh(arrangement)
        arrangements_out.append({"id": str(arrangement.id), "kind": arrangement.kind.value})

    return {
        "fixedEntries": fixed_out,
        "installments": installments_out,
        "recurrences": recurrences_out,
        "arrangements": arrangements_out,
    }


@router.post(
    "/approvals/{approval_id}/reject",
    response_model=ContractBillingProposalRead,
    summary="Rejeitar plano de cobrança — nada é materializado",
)
async def reject_billing_proposal(
    approval_id: UUID,
    payload: RejectProposalRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    # Autor da rejeição SEMPRE do contexto autenticado — nunca do payload
    # (forjável). Mesmo padrão de approve_billing_proposal e finance_dunning.
    proposal = await ContractBillingService(db).reject_proposal(
        workspace_id,
        approval_id,
        resolved_by=auth.user_id,
        resolution_reason=payload.resolution_reason,
    )
    await db.commit()
    await db.refresh(proposal)
    return _proposal_read(proposal)
