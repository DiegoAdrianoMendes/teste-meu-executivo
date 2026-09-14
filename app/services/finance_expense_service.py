"""
Service: FinanceExpenseService
Despesas completas (F8) sobre `FinanceEntry` (entry_type=expense) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §C.3/F8.

Regras:
- Reutiliza `FinanceEntry`/`FinanceEntryStatus` (F1) e `FinanceRecurrence`
  (F3) — nenhuma tabela nova.
- Reembolso: `generate_reimbursement_charge` é idempotente por construção —
  `reimbursement_entry_id` tem UniqueConstraint 1:1, então uma segunda
  chamada apenas retorna a entry-espelho já existente (nunca cria duas).
- Aprovação: despesa com valor acima do limite do workspace nasce com
  `approval_status=pending`; só pode gerar reembolso depois de aprovada.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.finance import (
    DEFAULT_EXPENSE_CATEGORIES,
    FinanceEntry,
    FinanceEntryStatus,
    FinanceEntryType,
    FinanceExpenseApprovalStatus,
)
from app.models.finance_recurrence import (
    FinanceRecurrence,
    FinanceRecurrenceKind,
    FinanceRecurrenceStatus,
)
from app.models.workspace import Workspace
from app.schemas.finance_expense import FinanceExpenseApprove, FinanceExpenseCreate

# Limite padrão (BRL) acima do qual uma despesa exige aprovação humana.
# Configurável por workspace via settings["finance"]["expenseApprovalLimit"].
DEFAULT_EXPENSE_APPROVAL_LIMIT = 1000.0


class FinanceExpenseService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Leitura auxiliar ──

    async def _get_workspace(self, workspace_id: UUID) -> Workspace:
        workspace = await self.db.get(Workspace, workspace_id)
        if workspace is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace não encontrado.")
        return workspace

    async def _get_expense(self, workspace_id: UUID, entry_id: UUID) -> FinanceEntry:
        result = await self.db.execute(
            select(FinanceEntry)
            .options(selectinload(FinanceEntry.reminders))
            .where(
                FinanceEntry.id == entry_id,
                FinanceEntry.workspace_id == workspace_id,
                FinanceEntry.entry_type == FinanceEntryType.expense,
                FinanceEntry.deleted_at.is_(None),
            )
        )
        entry = result.scalar_one_or_none()
        if entry is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "FIN-EXP-001", "message": "Despesa não encontrada."},
            )
        return entry

    async def _reload_with_reminders(self, entry_id: UUID) -> FinanceEntry:
        result = await self.db.execute(
            select(FinanceEntry)
            .options(selectinload(FinanceEntry.reminders))
            .where(FinanceEntry.id == entry_id)
        )
        return result.scalar_one()

    def _approval_limit(self, workspace: Workspace) -> float:
        settings = workspace.settings or {}
        finance_settings = settings.get("finance", {}) if isinstance(settings, dict) else {}
        limit = finance_settings.get("expenseApprovalLimit", DEFAULT_EXPENSE_APPROVAL_LIMIT)
        try:
            return float(limit)
        except (TypeError, ValueError):
            return DEFAULT_EXPENSE_APPROVAL_LIMIT

    @staticmethod
    def default_categories() -> list[str]:
        return list(DEFAULT_EXPENSE_CATEGORIES)

    # ── Criação ──

    async def create_expense(self, workspace_id: UUID, payload: FinanceExpenseCreate) -> FinanceEntry:
        workspace = await self._get_workspace(workspace_id)
        limit = self._approval_limit(workspace)
        requires_approval = float(payload.amount) > limit

        recurrence_id: Optional[UUID] = None
        if payload.recurrence is not None:
            recurrence = FinanceRecurrence(
                workspace_id=workspace_id,
                client_id=payload.recurrence.client_id,
                matter_id=payload.matter_id,
                entry_title=payload.title,
                entry_description=payload.description,
                entry_value=payload.amount,
                entry_type=FinanceEntryType.expense,
                kind=FinanceRecurrenceKind.geracao_programada,
                cycle=payload.recurrence.cycle,
                next_run=payload.recurrence.next_run,
                end_date=payload.recurrence.end_date,
                max_count=payload.recurrence.max_count,
                status=FinanceRecurrenceStatus.ativa,
            )
            self.db.add(recurrence)
            await self.db.flush()
            recurrence_id = recurrence.id

        entry = FinanceEntry(
            workspace_id=workspace_id,
            matter_id=payload.matter_id,
            title=payload.title,
            description=payload.description,
            entry_type=FinanceEntryType.expense,
            status=FinanceEntryStatus.pending,
            amount=payload.amount,
            currency=payload.currency,
            due_date=payload.due_date,
            tags=payload.tags,
            entry_metadata=payload.entry_metadata,
            category=payload.category,
            cost_center=payload.cost_center,
            reimbursable=payload.reimbursable,
            receipt_document_ref=payload.receipt_document_ref,
            recurrence_id=recurrence_id,
            approval_status=(
                FinanceExpenseApprovalStatus.pending
                if requires_approval
                else FinanceExpenseApprovalStatus.not_required
            ),
        )
        self.db.add(entry)
        await self.db.flush()
        return await self._reload_with_reminders(entry.id)

    # ── Aprovação ──

    async def approve_expense(
        self,
        workspace_id: UUID,
        entry_id: UUID,
        approver_id: UUID,
        payload: FinanceExpenseApprove,
    ) -> FinanceEntry:
        entry = await self._get_expense(workspace_id, entry_id)
        if entry.approval_status not in (
            FinanceExpenseApprovalStatus.pending,
            FinanceExpenseApprovalStatus.not_required,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "FIN-EXP-002",
                    "message": f"Despesa já resolvida (status atual: {entry.approval_status.value}).",
                },
            )
        if payload.decision not in (
            FinanceExpenseApprovalStatus.approved,
            FinanceExpenseApprovalStatus.rejected,
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Decisão deve ser 'approved' ou 'rejected'.",
            )
        entry.approval_status = payload.decision
        entry.approved_by = approver_id
        entry.approved_at = self._utc_now()
        if payload.decision == FinanceExpenseApprovalStatus.rejected:
            entry.status = FinanceEntryStatus.cancelled
        await self.db.flush()
        return await self._reload_with_reminders(entry.id)

    # ── Reembolso ──

    async def generate_reimbursement_charge(self, workspace_id: UUID, entry_id: UUID) -> FinanceEntry:
        """Cria (ou retorna, se já existir) a `FinanceEntry` receivable-espelho
        de uma despesa reembolsável. Idempotente: `reimbursement_entry_id`
        é UNIQUE, então rodar 2x nunca gera duas cobranças de reembolso.
        """
        entry = await self._get_expense(workspace_id, entry_id)

        if entry.reimbursement_entry_id is not None:
            mirror = await self.db.get(FinanceEntry, entry.reimbursement_entry_id)
            if mirror is not None:
                return await self._reload_with_reminders(mirror.id)

        if not entry.reimbursable:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "FIN-EXP-003", "message": "Despesa não está marcada como reembolsável."},
            )
        if entry.matter_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "FIN-EXP-004", "message": "Reembolso exige despesa vinculada a um caso."},
            )
        if entry.approval_status == FinanceExpenseApprovalStatus.pending:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "FIN-EXP-005", "message": "Despesa aguarda aprovação antes do reembolso."},
            )

        mirror = FinanceEntry(
            workspace_id=workspace_id,
            matter_id=entry.matter_id,
            title=f"Reembolso: {entry.title}",
            description=entry.description,
            entry_type=FinanceEntryType.receivable,
            status=FinanceEntryStatus.pending,
            amount=entry.amount,
            currency=entry.currency,
            category=entry.category,
            cost_center=entry.cost_center,
            entry_metadata={"reimbursementOfEntryId": str(entry.id)},
        )
        self.db.add(mirror)
        await self.db.flush()

        entry.reimbursement_entry_id = mirror.id
        await self.db.flush()
        return await self._reload_with_reminders(mirror.id)

    # ── Listagem ──

    async def list_expenses(
        self,
        workspace_id: UUID,
        *,
        category: Optional[str] = None,
        cost_center: Optional[str] = None,
        limit: int = 100,
    ) -> list[FinanceEntry]:
        query = (
            select(FinanceEntry)
            .where(
                FinanceEntry.workspace_id == workspace_id,
                FinanceEntry.entry_type == FinanceEntryType.expense,
                FinanceEntry.deleted_at.is_(None),
            )
            .order_by(FinanceEntry.due_date.asc().nullslast(), FinanceEntry.created_at.desc())
            .limit(limit)
        )
        if category:
            query = query.where(FinanceEntry.category == category)
        if cost_center:
            query = query.where(FinanceEntry.cost_center == cost_center)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    def _utc_now():
        from datetime import datetime, timezone

        return datetime.now(timezone.utc)
