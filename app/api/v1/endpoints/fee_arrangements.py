"""
Endpoints de honorários jurídicos (F9) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md seção 5 (F9).
"""
from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_workspace_id, get_db, require_finance_access
from app.models.fee_arrangement import FeeArrangement, TimeEntry
from app.models.finance import FinanceEntry
from app.schemas.fee_arrangement import (
    CloseMonthlyInvoiceRequest,
    FeeArrangementCreate,
    FeeArrangementRead,
    FinanceEntrySummaryRead,
    GenerateChargeRequest,
    RegisterSuccessEventRequest,
    RenegotiateOverdueRequest,
    TimeEntryCreate,
    TimeEntryRead,
)
from app.schemas.finance_charge import FinanceChargeRead
from app.schemas.finance_recurrence import FinanceRecurrenceRead
from app.services.fee_service import FeeService

router = APIRouter(prefix="/finance", tags=["Financeiro — Honorários"])


def _entry_summary(entry: FinanceEntry) -> FinanceEntrySummaryRead:
    return FinanceEntrySummaryRead(
        id=entry.id,
        title=entry.title,
        amount=entry.amount,
        due_date=entry.due_date,
        status=entry.status.value,
        external_ref=entry.external_ref,
    )


@router.post(
    "/fee-arrangements",
    response_model=FeeArrangementRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar arranjo de honorários de um caso",
)
async def create_fee_arrangement(
    payload: FeeArrangementCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    arrangement = await FeeService(db).create_arrangement(workspace_id, payload)
    await db.commit()
    await db.refresh(arrangement)
    return FeeArrangementRead.model_validate(arrangement)


@router.get(
    "/fee-arrangements",
    response_model=List[FeeArrangementRead],
    summary="Listar arranjos de honorários",
)
async def list_fee_arrangements(
    matter_id: Optional[UUID] = Query(None),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    arrangements = await FeeService(db).list_arrangements(workspace_id, matter_id=matter_id)
    return [FeeArrangementRead.model_validate(item) for item in arrangements]


@router.get(
    "/fee-arrangements/{arrangement_id}",
    response_model=FeeArrangementRead,
    summary="Detalhe de um arranjo de honorários",
)
async def get_fee_arrangement(
    arrangement_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    arrangement: FeeArrangement = await FeeService(db).get_arrangement(workspace_id, arrangement_id)
    return FeeArrangementRead.model_validate(arrangement)


@router.post(
    "/fee-arrangements/{arrangement_id}/close",
    response_model=FeeArrangementRead,
    summary="Encerrar um arranjo de honorários",
)
async def close_fee_arrangement(
    arrangement_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    arrangement = await FeeService(db).end_arrangement(workspace_id, arrangement_id)
    await db.commit()
    await db.refresh(arrangement)
    return FeeArrangementRead.model_validate(arrangement)


@router.post(
    "/fee-arrangements/{arrangement_id}/generate",
    summary="Gerar cobrança de um arranjo fixo/entrada+parcelas/mensalidade",
)
async def generate_fee_arrangement_charge(
    arrangement_id: UUID,
    payload: GenerateChargeRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    result = await FeeService(db).generate_charge_from_arrangement(
        workspace_id,
        arrangement_id,
        installment_count=payload.installment_count,
        first_due_date=payload.first_due_date,
        billing_type=payload.billing_type,
        customer_external_id=payload.customer_external_id,
    )
    await db.commit()

    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], FinanceEntry):
        entry, charge = result
        await db.refresh(entry)
        await db.refresh(charge)
        return {
            "kind": "fixo",
            "entry": _entry_summary(entry).model_dump(mode="json", by_alias=True),
            "charge": FinanceChargeRead.model_validate(charge).model_dump(mode="json", by_alias=True),
        }
    if isinstance(result, tuple):
        installment_ref, entries = result
        for entry in entries:
            await db.refresh(entry)
        return {
            "kind": "entrada_exito",
            "installmentRef": installment_ref,
            "entries": [
                _entry_summary(entry).model_dump(mode="json", by_alias=True) for entry in entries
            ],
        }
    await db.refresh(result)
    return {
        "kind": "mensalidade",
        "recurrence": FinanceRecurrenceRead.model_validate(result).model_dump(mode="json", by_alias=True),
    }


@router.post(
    "/fee-arrangements/{arrangement_id}/time-entries",
    response_model=TimeEntryRead,
    status_code=status.HTTP_201_CREATED,
    summary="Lançar horas trabalhadas (arranjo por_hora)",
)
async def log_time_entry(
    arrangement_id: UUID,
    payload: TimeEntryCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    time_entry: TimeEntry = await FeeService(db).log_time(
        workspace_id,
        arrangement_id,
        user_id=payload.user_id,
        entry_date=payload.date,
        minutes=payload.minutes,
        description=payload.description,
    )
    await db.commit()
    await db.refresh(time_entry)
    return TimeEntryRead.model_validate(time_entry)


@router.post(
    "/fee-arrangements/{arrangement_id}/invoices/close",
    summary="Fechar fatura mensal consolidando horas não faturadas (idempotente por competência)",
)
async def close_monthly_invoice(
    arrangement_id: UUID,
    payload: CloseMonthlyInvoiceRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    entry = await FeeService(db).close_monthly_invoice(workspace_id, arrangement_id, payload.competence)
    await db.commit()
    await db.refresh(entry)
    return _entry_summary(entry)


@router.post(
    "/fee-arrangements/{arrangement_id}/success-events",
    summary="Registrar evento de êxito — cria aprovação humana (não cobra ainda)",
)
async def register_success_event(
    arrangement_id: UUID,
    payload: RegisterSuccessEventRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    approval = await FeeService(db).register_success_event(
        workspace_id,
        arrangement_id,
        payload.base_value,
        requested_by=payload.requested_by,
    )
    await db.commit()
    return {"approvalId": str(approval.id), "status": approval.status.value}


@router.post(
    "/fee-arrangements/success-events/{approval_id}/approve",
    summary="Aprovar evento de êxito — só aqui a cobrança nasce (entry + charge)",
)
async def approve_success_fee(
    approval_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    # O autor da aprovação é SEMPRE derivado do contexto autenticado — nunca do
    # payload (que o cliente pode forjar). Mesmo padrão de finance_dunning.py.
    entry, charge = await FeeService(db).approve_success_fee(
        workspace_id, approval_id, resolved_by=auth.user_id
    )
    await db.commit()
    await db.refresh(entry)
    await db.refresh(charge)
    return {
        "entry": _entry_summary(entry).model_dump(mode="json", by_alias=True),
        "charge": FinanceChargeRead.model_validate(charge).model_dump(mode="json", by_alias=True),
    }


@router.post(
    "/entries/{entry_id}/renegotiate",
    summary="Renegociar débito vencido — cancela a cobrança e cria nova série de parcelas",
)
async def renegotiate_overdue_entry(
    entry_id: UUID,
    payload: RenegotiateOverdueRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    installment_ref, entries = await FeeService(db).renegotiate_overdue(
        workspace_id,
        entry_id,
        installment_count=payload.installment_count,
        agreed_value=payload.agreed_value,
        first_due_date=payload.first_due_date,
        billing_type=payload.billing_type,
        requested_by=payload.requested_by,
    )
    await db.commit()
    for entry in entries:
        await db.refresh(entry)
    return {
        "installmentRef": installment_ref,
        "entries": [_entry_summary(entry).model_dump(mode="json", by_alias=True) for entry in entries],
    }
