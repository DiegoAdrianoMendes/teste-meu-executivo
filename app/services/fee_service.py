"""
Service: FeeService
Honorários jurídicos (F9) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md seção 5 (F9).

Reaproveita a fundação financeira já construída em vez de duplicá-la:
`FinanceChargeService` (cobrança avulsa/parcelada), `FinanceRecurrenceService`
(mensalidade/retainer), `ApprovalService` (aprovação humana do êxito) e
`KlmAuditEvent` (trilha de auditoria).

Fail-closed: nenhuma cobrança de êxito nasce sem aprovação humana — o valor
calculado em `register_success_event` fica congelado num `KlmAuditEvent`
(entity_type="fee_success_event", entity_id=approval.id) e só vira
FinanceEntry/FinanceCharge dentro de `approve_success_fee`, depois que
`ApprovalService.resolve` confirma a transição pending -> approved (exactly
once — uma segunda tentativa de aprovar recebe 409 do próprio ApprovalService,
o que já impede cobrar duas vezes).
"""
from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import ApprovalDecision, ApprovalQueueItem, ApprovalStatus
from app.models.fee_arrangement import FeeArrangement, FeeArrangementKind, FeeArrangementStatus, TimeEntry
from app.models.finance import FinanceEntry, FinanceEntryType
from app.models.finance_charge import FinanceCharge
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import Matter
from app.payments.port import BillingType
from app.schemas.approval import ApprovalCreate, ApprovalResolveRequest
from app.schemas.fee_arrangement import FeeArrangementCreate
from app.schemas.finance_charge import FinanceChargeCreate
from app.schemas.finance_recurrence import FinanceRecurrenceCreate
from app.services.approval_service import ApprovalService
from app.services.audit_actor_service import resolve_human_actor_label
from app.services.finance_charge_service import FinanceChargeService, _add_months
from app.services.finance_recurrence_service import FinanceRecurrenceService

CENTS = Decimal("0.01")

# Kinds cuja cobrança nasce direto de generate_charge_from_arrangement.
_DIRECT_GENERATION_KINDS = frozenset(
    {
        FeeArrangementKind.fixo,
        FeeArrangementKind.entrada_exito,
        FeeArrangementKind.mensalidade,
    }
)
# Kinds cujo percentual habilita o fluxo de êxito com aprovação humana.
_SUCCESS_BASED_KINDS = frozenset(
    {
        FeeArrangementKind.exito,
        FeeArrangementKind.quota_litis,
        FeeArrangementKind.hibrido,
    }
)

SUCCESS_EVENT_APPROVAL_TYPE = "fee_success_event"
SUCCESS_EVENT_AUDIT_ENTITY_TYPE = "fee_success_event"


def _quantize(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


def _month_end(reference: date) -> date:
    last_day = calendar.monthrange(reference.year, reference.month)[1]
    return date(reference.year, reference.month, last_day)


class FeeArrangementNotFoundError(HTTPException):
    def __init__(self, detail: str = "Arranjo de honorários não encontrado.") -> None:
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


class FeeArrangementConflictError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)


class FeeService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Arranjo ──────────────────────────────────────────────────────────

    async def _get_matter(self, workspace_id: UUID, matter_id: UUID) -> Matter:
        matter = await self.db.scalar(
            select(Matter).where(
                Matter.id == matter_id,
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
            )
        )
        if matter is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Caso não encontrado.")
        return matter

    async def _get_arrangement(self, workspace_id: UUID, arrangement_id: UUID) -> FeeArrangement:
        arrangement = await self.db.scalar(
            select(FeeArrangement).where(
                FeeArrangement.id == arrangement_id,
                FeeArrangement.workspace_id == workspace_id,
            )
        )
        if arrangement is None:
            raise FeeArrangementNotFoundError()
        return arrangement

    @staticmethod
    def _validate_kind_fields(payload: FeeArrangementCreate) -> None:
        kind = payload.kind
        if kind in (FeeArrangementKind.fixo, FeeArrangementKind.entrada_exito, FeeArrangementKind.mensalidade):
            if payload.fixed_value is None or payload.fixed_value <= 0:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Arranjo '{kind.value}' exige fixedValue maior que zero.",
                )
        if kind in _SUCCESS_BASED_KINDS:
            if payload.percent is None or payload.percent <= 0:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Arranjo '{kind.value}' exige percent maior que zero.",
                )
        if kind == FeeArrangementKind.hibrido and (payload.fixed_value is None or payload.fixed_value <= 0):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Arranjo híbrido exige fixedValue e percent.",
            )
        if kind == FeeArrangementKind.por_hora:
            if payload.hourly_rate is None or payload.hourly_rate <= 0:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Arranjo 'por_hora' exige hourlyRate maior que zero.",
                )

    async def create_arrangement(
        self, workspace_id: UUID, payload: FeeArrangementCreate
    ) -> FeeArrangement:
        await self._get_matter(workspace_id, payload.matter_id)
        self._validate_kind_fields(payload)

        arrangement = FeeArrangement(
            workspace_id=workspace_id,
            matter_id=payload.matter_id,
            kind=payload.kind,
            fixed_value=payload.fixed_value,
            percent=payload.percent,
            base_calculo_exito=payload.base_calculo_exito,
            hourly_rate=payload.hourly_rate,
            status=FeeArrangementStatus.ativo,
            notes=payload.notes,
        )
        self.db.add(arrangement)
        await self.db.flush()
        return arrangement

    async def list_arrangements(
        self, workspace_id: UUID, *, matter_id: Optional[UUID] = None
    ) -> list[FeeArrangement]:
        query = select(FeeArrangement).where(FeeArrangement.workspace_id == workspace_id)
        if matter_id is not None:
            query = query.where(FeeArrangement.matter_id == matter_id)
        query = query.order_by(FeeArrangement.created_at.desc())
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_arrangement(self, workspace_id: UUID, arrangement_id: UUID) -> FeeArrangement:
        return await self._get_arrangement(workspace_id, arrangement_id)

    async def end_arrangement(self, workspace_id: UUID, arrangement_id: UUID) -> FeeArrangement:
        arrangement = await self._get_arrangement(workspace_id, arrangement_id)
        arrangement.status = FeeArrangementStatus.encerrado
        await self.db.flush()
        return arrangement

    # ── Geração direta (fixo / entrada+parcelas / mensalidade) ─────────────

    async def generate_charge_from_arrangement(
        self,
        workspace_id: UUID,
        arrangement_id: UUID,
        *,
        installment_count: Optional[int] = None,
        first_due_date: Optional[date] = None,
        billing_type: BillingType = BillingType.UNDEFINED,
        customer_external_id: Optional[str] = None,
    ):
        arrangement = await self._get_arrangement(workspace_id, arrangement_id)
        if arrangement.status != FeeArrangementStatus.ativo:
            raise FeeArrangementConflictError("Arranjo encerrado não pode gerar cobrança.")
        if arrangement.kind not in _DIRECT_GENERATION_KINDS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Arranjo '{arrangement.kind.value}' não gera cobrança direta — use "
                    "log_time/close_monthly_invoice (por_hora) ou register_success_event "
                    "(exito/quota_litis/hibrido)."
                ),
            )
        matter = await self._get_matter(workspace_id, arrangement.matter_id)
        due_date = first_due_date or date.today()

        if arrangement.kind == FeeArrangementKind.fixo:
            entry = FinanceEntry(
                workspace_id=workspace_id,
                matter_id=matter.id,
                title=f"Honorários — {matter.demand_type}",
                entry_type=FinanceEntryType.receivable,
                amount=arrangement.fixed_value,
                due_date=datetime.combine(due_date, datetime.min.time(), tzinfo=timezone.utc),
                entry_metadata={"feeArrangementId": str(arrangement.id), "kind": arrangement.kind.value},
            )
            self.db.add(entry)
            await self.db.flush()
            charge = await FinanceChargeService(self.db).create_charge_for_entry(
                workspace_id,
                entry.id,
                FinanceChargeCreate(
                    billing_type=billing_type,
                    customer_external_id=customer_external_id,
                    due_date=due_date,
                ),
            )
            return entry, charge

        if arrangement.kind == FeeArrangementKind.entrada_exito:
            if installment_count is None or installment_count < 2:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Arranjo 'entrada_exito' exige installmentCount >= 2 (entrada + parcelas).",
                )
            parent_entry = FinanceEntry(
                workspace_id=workspace_id,
                matter_id=matter.id,
                title=f"Honorários — {matter.demand_type}",
                entry_type=FinanceEntryType.receivable,
                amount=arrangement.fixed_value,
                due_date=datetime.combine(due_date, datetime.min.time(), tzinfo=timezone.utc),
                entry_metadata={"feeArrangementId": str(arrangement.id), "kind": arrangement.kind.value},
            )
            self.db.add(parent_entry)
            await self.db.flush()
            installment_ref, child_entries = await FinanceChargeService(self.db).create_installment_series(
                workspace_id,
                parent_entry.id,
                installment_count,
                total_value=arrangement.fixed_value,
                first_due_date=due_date,
                billing_type=billing_type,
                customer_external_id=customer_external_id,
            )
            return installment_ref, child_entries

        # mensalidade
        recurrence = await FinanceRecurrenceService(self.db).create_recurrence(
            workspace_id,
            FinanceRecurrenceCreate(
                client_id=matter.client_id,
                matter_id=matter.id,
                entry_title=f"Honorários — {matter.demand_type}",
                entry_value=arrangement.fixed_value,
                entry_type=FinanceEntryType.receivable,
                next_run=due_date,
                customer_external_id=customer_external_id,
                billing_type=billing_type,
            ),
        )
        return recurrence

    # ── POR_HORA: timesheet + fatura mensal idempotente ─────────────────

    async def log_time(
        self,
        workspace_id: UUID,
        arrangement_id: UUID,
        *,
        user_id: UUID,
        entry_date: date,
        minutes: int,
        description: Optional[str] = None,
    ) -> TimeEntry:
        arrangement = await self._get_arrangement(workspace_id, arrangement_id)
        if arrangement.kind != FeeArrangementKind.por_hora:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Só é possível lançar horas em arranjos do tipo 'por_hora'.",
            )
        if arrangement.status != FeeArrangementStatus.ativo:
            raise FeeArrangementConflictError("Arranjo encerrado não aceita novos lançamentos de hora.")
        if minutes <= 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="minutes deve ser maior que zero.",
            )

        time_entry = TimeEntry(
            workspace_id=workspace_id,
            arrangement_id=arrangement.id,
            matter_id=arrangement.matter_id,
            user_id=user_id,
            date=entry_date,
            minutes=minutes,
            description=description,
            hourly_rate=arrangement.hourly_rate,
            billed=False,
        )
        self.db.add(time_entry)
        await self.db.flush()
        return time_entry

    async def close_monthly_invoice(
        self,
        workspace_id: UUID,
        arrangement_id: UUID,
        competence: date,
    ) -> FinanceEntry:
        """Consolida horas não faturadas em 1 FinanceEntry + 1 FinanceCharge.

        Idempotente por `(arrangement, competência)`: a chave usada é
        `FinanceEntry.external_ref` (já único por workspace no schema de F1),
        então rodar esta chamada 2x na mesma competência sempre retorna a
        MESMA fatura — a segunda chamada não encontra horas não faturadas
        (já marcadas `billed=True` na primeira) e cai direto no lookup por
        `external_ref`. Horas lançadas depois de uma competência já fechada
        só entram na consolidação da competência seguinte.
        """
        arrangement = await self._get_arrangement(workspace_id, arrangement_id)
        if arrangement.kind != FeeArrangementKind.por_hora:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Fatura mensal só se aplica a arranjos do tipo 'por_hora'.",
            )
        competence_key = date(competence.year, competence.month, 1)
        external_ref = f"fee-hours:{arrangement.id}:{competence_key.isoformat()}"

        existing = await self.db.scalar(
            select(FinanceEntry).where(
                FinanceEntry.workspace_id == workspace_id,
                FinanceEntry.external_ref == external_ref,
            )
        )
        if existing is not None:
            return existing

        period_end = _month_end(competence_key)
        unbilled = list(
            (
                await self.db.scalars(
                    select(TimeEntry)
                    .where(
                        TimeEntry.workspace_id == workspace_id,
                        TimeEntry.arrangement_id == arrangement.id,
                        TimeEntry.billed.is_(False),
                        TimeEntry.date <= period_end,
                    )
                    .order_by(TimeEntry.date.asc())
                )
            ).all()
        )
        if not unbilled:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Nenhum lançamento de hora não faturado para esta competência.",
            )

        matter = await self._get_matter(workspace_id, arrangement.matter_id)
        total_minutes = sum(te.minutes for te in unbilled)
        total_value = sum(
            (_quantize(Decimal(te.minutes) / Decimal(60) * Decimal(str(te.hourly_rate))) for te in unbilled),
            start=Decimal("0"),
        )
        due_date = _add_months(competence_key, 1)

        entry = FinanceEntry(
            workspace_id=workspace_id,
            matter_id=matter.id,
            title=f"Honorários por hora — competência {competence_key.strftime('%m/%Y')}",
            entry_type=FinanceEntryType.receivable,
            amount=total_value,
            due_date=datetime.combine(due_date, datetime.min.time(), tzinfo=timezone.utc),
            external_ref=external_ref,
            entry_metadata={
                "feeArrangementId": str(arrangement.id),
                "kind": "por_hora_invoice",
                "competence": competence_key.isoformat(),
                "totalMinutes": total_minutes,
                "timeEntryIds": [str(te.id) for te in unbilled],
            },
        )
        self.db.add(entry)
        try:
            async with self.db.begin_nested():
                await self.db.flush()
        except IntegrityError:
            existing = await self.db.scalar(
                select(FinanceEntry).where(
                    FinanceEntry.workspace_id == workspace_id,
                    FinanceEntry.external_ref == external_ref,
                )
            )
            if existing is None:
                raise
            return existing

        for time_entry in unbilled:
            time_entry.billed = True
            time_entry.invoice_entry_id = entry.id

        await FinanceChargeService(self.db).create_charge_for_entry(
            workspace_id, entry.id, FinanceChargeCreate()
        )

        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=None,
                actor_type="system",
                actor_label="Fechamento de fatura mensal (F9)",
                event_type="fee_hours_invoiced",
                entity_type="finance_entry",
                entity_id=entry.id,
                summary=(
                    f"Fatura mensal de horas consolidada para a competência "
                    f"{competence_key.strftime('%m/%Y')} — {total_minutes} minutos."
                ),
                after_data={
                    "arrangementId": str(arrangement.id),
                    "competence": competence_key.isoformat(),
                    "totalMinutes": total_minutes,
                    "totalValue": str(total_value),
                    "timeEntryIds": [str(te.id) for te in unbilled],
                },
                occurred_at=datetime.now(UTC),
            )
        )
        await self.db.flush()
        return entry

    # ── ÊXITO: aprovação humana obrigatória antes de qualquer cobrança ──

    async def register_success_event(
        self,
        workspace_id: UUID,
        arrangement_id: UUID,
        base_value: Decimal,
        *,
        requested_by: Optional[UUID] = None,
    ) -> ApprovalQueueItem:
        arrangement = await self._get_arrangement(workspace_id, arrangement_id)
        if arrangement.kind not in _SUCCESS_BASED_KINDS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Só arranjos 'exito', 'quota_litis' ou 'hibrido' registram evento de êxito.",
            )
        if arrangement.status != FeeArrangementStatus.ativo:
            raise FeeArrangementConflictError("Arranjo encerrado não pode registrar evento de êxito.")
        if not arrangement.percent:
            raise FeeArrangementConflictError("Arranjo sem percentual de êxito configurado.")

        base_value = Decimal(base_value)
        percent = Decimal(str(arrangement.percent))
        fee_value = _quantize(base_value * percent / Decimal("100"))

        intent_key = f"arrangement:{arrangement.id}:base:{_quantize(base_value)}"
        approval = await ApprovalService(self.db, workspace_id).create(
            ApprovalCreate(
                matter_id=arrangement.matter_id,
                approval_type=SUCCESS_EVENT_APPROVAL_TYPE,
                intent_key=intent_key,
                title=f"Aprovar honorário de êxito — {percent}% sobre R$ {_quantize(base_value)}",
                description=(
                    f"Base de cálculo: R$ {_quantize(base_value)}. Percentual contratado: "
                    f"{percent}%. Valor do honorário: R$ {fee_value}."
                    + (f" Base de cálculo contratual: {arrangement.base_calculo_exito}." if arrangement.base_calculo_exito else "")
                ),
                requested_by=requested_by,
            )
        )

        # Congela o valor calculado — só é lido de volta em approve_success_fee,
        # que nunca recalcula (a cobrança tem que refletir exatamente o que foi
        # submetido à aprovação humana).
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=requested_by,
                actor_type="human" if requested_by else "system",
                actor_label=(
                    await resolve_human_actor_label(self.db, requested_by)
                    if requested_by
                    else "Sistema — Registro de evento de êxito"
                ),
                event_type="fee_success_event_registered",
                entity_type=SUCCESS_EVENT_AUDIT_ENTITY_TYPE,
                entity_id=approval.id,
                correlation_id=str(arrangement.id),
                summary=f"Evento de êxito registrado para aprovação — R$ {fee_value}.",
                after_data={
                    "arrangementId": str(arrangement.id),
                    "matterId": str(arrangement.matter_id),
                    "approvalId": str(approval.id),
                    "baseValue": str(_quantize(base_value)),
                    "percent": str(percent),
                    "feeValue": str(fee_value),
                },
                occurred_at=datetime.now(UTC),
            )
        )
        await self.db.flush()
        return approval

    async def _load_success_event_snapshot(self, workspace_id: UUID, approval_id: UUID) -> dict:
        event = await self.db.scalar(
            select(KlmAuditEvent)
            .where(
                KlmAuditEvent.workspace_id == workspace_id,
                KlmAuditEvent.entity_type == SUCCESS_EVENT_AUDIT_ENTITY_TYPE,
                KlmAuditEvent.entity_id == approval_id,
                KlmAuditEvent.event_type == "fee_success_event_registered",
            )
            .order_by(KlmAuditEvent.occurred_at.asc())
            .limit(1)
        )
        if event is None or not event.after_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Evento de êxito não encontrado para esta aprovação.",
            )
        return event.after_data

    async def approve_success_fee(
        self,
        workspace_id: UUID,
        approval_id: UUID,
        *,
        resolved_by: Optional[UUID] = None,
    ) -> tuple[FinanceEntry, FinanceCharge]:
        snapshot = await self._load_success_event_snapshot(workspace_id, approval_id)

        # Resolve exactly-once: uma segunda tentativa de aprovar a mesma
        # approval recebe 409 do próprio ApprovalService — impede cobrar 2x.
        await ApprovalService(self.db, workspace_id).resolve(
            approval_id,
            ApprovalResolveRequest(status=ApprovalDecision.approved, resolved_by=resolved_by),
        )

        arrangement_id = UUID(snapshot["arrangementId"])
        arrangement = await self._get_arrangement(workspace_id, arrangement_id)
        matter = await self._get_matter(workspace_id, arrangement.matter_id)
        fee_value = Decimal(snapshot["feeValue"])

        entry = FinanceEntry(
            workspace_id=workspace_id,
            matter_id=matter.id,
            title=f"Honorários de êxito — {matter.demand_type}",
            entry_type=FinanceEntryType.receivable,
            amount=fee_value,
            due_date=datetime.now(timezone.utc),
            entry_metadata={
                "feeArrangementId": str(arrangement.id),
                "kind": arrangement.kind.value,
                "successApprovalId": str(approval_id),
                "baseValue": snapshot["baseValue"],
                "percent": snapshot["percent"],
            },
        )
        self.db.add(entry)
        await self.db.flush()

        charge = await FinanceChargeService(self.db).create_charge_for_entry(
            workspace_id, entry.id, FinanceChargeCreate()
        )

        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=resolved_by,
                actor_type="human" if resolved_by else "system",
                actor_label=(
                    await resolve_human_actor_label(self.db, resolved_by)
                    if resolved_by
                    else "Sistema — Aprovação de êxito"
                ),
                event_type="fee_success_event_billed",
                entity_type=SUCCESS_EVENT_AUDIT_ENTITY_TYPE,
                entity_id=approval_id,
                correlation_id=str(arrangement.id),
                summary=f"Cobrança de honorário de êxito emitida — R$ {fee_value}.",
                after_data={"entryId": str(entry.id), "chargeId": str(charge.id)},
                occurred_at=datetime.now(UTC),
            )
        )
        await self.db.flush()
        return entry, charge

    # ── Renegociação de vencidas ─────────────────────────────────────────

    async def renegotiate_overdue(
        self,
        workspace_id: UUID,
        entry_id: UUID,
        *,
        installment_count: int,
        agreed_value: Decimal,
        first_due_date: Optional[date] = None,
        billing_type: BillingType = BillingType.UNDEFINED,
        requested_by: Optional[UUID] = None,
    ) -> tuple[str, list[FinanceEntry]]:
        charge_service = FinanceChargeService(self.db)
        old_charge = await self.db.scalar(
            select(FinanceCharge).where(
                FinanceCharge.workspace_id == workspace_id,
                FinanceCharge.entry_id == entry_id,
            )
        )
        if old_charge is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Nenhuma cobrança encontrada para este lançamento.",
            )
        before_snapshot = {
            "chargeId": str(old_charge.id),
            "status": old_charge.status.value,
            "value": str(old_charge.value),
            "dueDate": old_charge.due_date.isoformat() if old_charge.due_date else None,
        }

        # Cancela a cobrança vencida (local; gateway também se o gate estiver aberto).
        await charge_service.cancel_charge(workspace_id, old_charge.id)

        due_date = first_due_date or date.today()
        installment_ref, child_entries = await charge_service.create_installment_series(
            workspace_id,
            entry_id,
            installment_count,
            total_value=Decimal(agreed_value),
            first_due_date=due_date,
            billing_type=billing_type,
        )

        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=requested_by,
                actor_type="human" if requested_by else "system",
                actor_label=(
                    await resolve_human_actor_label(self.db, requested_by)
                    if requested_by
                    else "Sistema — Renegociação de vencida"
                ),
                event_type="fee_overdue_renegotiated",
                entity_type="finance_entry",
                entity_id=entry_id,
                summary=(
                    f"Débito renegociado em {installment_count}x, valor acordado R$ "
                    f"{_quantize(Decimal(agreed_value))}."
                ),
                before_data=before_snapshot,
                after_data={
                    "installmentRef": installment_ref,
                    "installmentCount": installment_count,
                    "agreedValue": str(_quantize(Decimal(agreed_value))),
                    "childEntryIds": [str(child.id) for child in child_entries],
                },
                occurred_at=datetime.now(UTC),
            )
        )
        await self.db.flush()
        return installment_ref, child_entries
