"""
Service: ReferralService
Indicações com comissão (F13) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §F13.

Regras:
- `register_commission_for_payment` é o único ponto de acúmulo de comissão
  e é chamado a partir de `app.workers.finance_tasks._apply_asaas_event`,
  após a baixa do pagamento (PAYMENT_RECEIVED). Idempotente por construção:
  `referral_commissions` tem UniqueConstraint(workspace_id, charge_id), então
  reprocessar o mesmo webhook (retries/reentrega) nunca gera duas comissões
  para a mesma cobrança — a segunda chamada apenas retorna a comissão já
  existente sem criar nova despesa.
- A comissão nasce como despesa (`FinanceEntry` entry_type=expense) via
  `FinanceExpenseService.create_expense`, respeitando o fluxo de aprovação
  humana já existente (F8) — dinheiro nunca sai sem aprovação.
- Comissão fixa só é lançada na primeira cobrança paga do caso (verificado
  por ausência de qualquer `ReferralCommission` prévia para o `matter_id`).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.finance import FinanceExpenseApprovalStatus
from app.models.finance_charge import FinanceCharge
from app.models.matter import Matter
from app.models.referral import (
    MatterReferral,
    ReferralCommission,
    ReferralCommissionType,
    ReferralPartner,
)
from app.schemas.finance_expense import FinanceExpenseCreate
from app.schemas.referral import (
    MatterReferralCreate,
    ReferralPartnerCreate,
    ReferralPartnerUpdate,
)
from app.services.finance_expense_service import FinanceExpenseService


class ReferralService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Parceiros (CRUD) ──

    async def create_partner(
        self, workspace_id: UUID, payload: ReferralPartnerCreate
    ) -> ReferralPartner:
        partner = ReferralPartner(workspace_id=workspace_id, **payload.model_dump())
        self.db.add(partner)
        await self.db.flush()
        return partner

    async def _get_partner(self, workspace_id: UUID, partner_id: UUID) -> ReferralPartner:
        partner = await self.db.get(ReferralPartner, partner_id)
        if partner is None or partner.workspace_id != workspace_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "REF-001", "message": "Parceiro de indicação não encontrado."},
            )
        return partner

    async def update_partner(
        self, workspace_id: UUID, partner_id: UUID, payload: ReferralPartnerUpdate
    ) -> ReferralPartner:
        partner = await self._get_partner(workspace_id, partner_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(partner, field, value)
        await self.db.flush()
        return partner

    async def list_partners(
        self, workspace_id: UUID, *, ativo: Optional[bool] = None
    ) -> list[ReferralPartner]:
        query = select(ReferralPartner).where(ReferralPartner.workspace_id == workspace_id)
        if ativo is not None:
            query = query.where(ReferralPartner.ativo == ativo)
        query = query.order_by(ReferralPartner.nome.asc())
        result = await self.db.execute(query)
        return list(result.scalars().all())

    # ── Registro de indicação no caso ──

    async def register_matter_referral(
        self, workspace_id: UUID, matter_id: UUID, registrado_por: UUID, payload: MatterReferralCreate
    ) -> MatterReferral:
        matter = await self.db.get(Matter, matter_id)
        if matter is None or matter.workspace_id != workspace_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "REF-002", "message": "Caso não encontrado."},
            )
        await self._get_partner(workspace_id, payload.partner_id)

        existing = await self.db.scalar(
            select(MatterReferral).where(MatterReferral.matter_id == matter_id)
        )
        if existing is not None:
            existing.partner_id = payload.partner_id
            existing.registrado_por = registrado_por
            existing.data = payload.data or self._utc_now()
            await self.db.flush()
            return existing

        referral = MatterReferral(
            workspace_id=workspace_id,
            matter_id=matter_id,
            partner_id=payload.partner_id,
            registrado_por=registrado_por,
            data=payload.data or self._utc_now(),
        )
        self.db.add(referral)
        await self.db.flush()
        return referral

    async def get_matter_referral(
        self, workspace_id: UUID, matter_id: UUID
    ) -> Optional[MatterReferral]:
        return await self.db.scalar(
            select(MatterReferral).where(
                MatterReferral.workspace_id == workspace_id,
                MatterReferral.matter_id == matter_id,
            )
        )

    # ── Comissões ──

    async def list_commissions(
        self, workspace_id: UUID, *, partner_id: Optional[UUID] = None
    ) -> list[ReferralCommission]:
        query = select(ReferralCommission).where(ReferralCommission.workspace_id == workspace_id)
        if partner_id is not None:
            query = query.where(ReferralCommission.partner_id == partner_id)
        query = query.order_by(ReferralCommission.created_at.desc())
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def register_commission_for_payment(
        self,
        workspace_id: UUID,
        charge: FinanceCharge,
        *,
        paid_amount: Decimal,
    ) -> Optional[ReferralCommission]:
        """Ponto único de acúmulo de comissão, chamado pelo processador de
        webhook após a baixa de PAYMENT_RECEIVED. Idempotente por
        (workspace_id, charge_id)."""
        if charge.entry_id is None:
            return None

        from app.models.finance import FinanceEntry

        entry = await self.db.get(FinanceEntry, charge.entry_id)
        if entry is None or entry.matter_id is None:
            return None

        # Idempotência: se já existe comissão para esta cobrança, retorna-a
        # sem criar despesa nova (reentrega/retry do webhook).
        existing = await self.db.scalar(
            select(ReferralCommission).where(
                ReferralCommission.workspace_id == workspace_id,
                ReferralCommission.charge_id == charge.id,
            )
        )
        if existing is not None:
            return existing

        referral = await self.get_matter_referral(workspace_id, entry.matter_id)
        if referral is None:
            return None

        partner = await self.db.get(ReferralPartner, referral.partner_id)
        if partner is None or partner.workspace_id != workspace_id:
            return None

        today = charge.paid_at.date() if charge.paid_at else date.today()
        if not partner.vigente_em(today):
            return None

        if partner.comissao_tipo == ReferralCommissionType.fixo:
            # Fixo: só na primeira cobrança paga do caso.
            already_has_commission_for_matter = await self.db.scalar(
                select(ReferralCommission.id).where(
                    ReferralCommission.workspace_id == workspace_id,
                    ReferralCommission.matter_id == entry.matter_id,
                )
            )
            if already_has_commission_for_matter is not None:
                return None
            comissao_valor_calculado = Decimal(str(partner.comissao_valor))
        else:
            comissao_valor_calculado = (
                Decimal(str(paid_amount)) * Decimal(str(partner.comissao_valor)) / Decimal("100")
            )

        if comissao_valor_calculado <= 0:
            return None

        expense_service = FinanceExpenseService(self.db)
        expense_entry = await expense_service.create_expense(
            workspace_id,
            FinanceExpenseCreate(
                matter_id=entry.matter_id,
                title=f"Comissão de indicação — {partner.nome}",
                description=(
                    f"Comissão de indicação ({partner.comissao_tipo.value}) referente à "
                    f"cobrança {charge.id} do caso."
                ),
                amount=float(comissao_valor_calculado),
                currency=entry.currency,
                category="comissao_indicacao",
                entry_metadata={
                    "referralPartnerId": str(partner.id),
                    "referralChargeId": str(charge.id),
                    "kind": "comissao_indicacao",
                },
            ),
        )
        # Comissão de indicação é dinheiro saindo do escritório: sempre exige
        # aprovação humana explícita, mesmo abaixo do limite automático de
        # despesas (F8) — nunca sai sozinha por estar "not_required".
        if expense_entry.approval_status == FinanceExpenseApprovalStatus.not_required:
            expense_entry.approval_status = FinanceExpenseApprovalStatus.pending
            await self.db.flush()

        commission = ReferralCommission(
            workspace_id=workspace_id,
            matter_id=entry.matter_id,
            partner_id=partner.id,
            charge_id=charge.id,
            expense_entry_id=expense_entry.id,
            valor_base=paid_amount,
            comissao_tipo=partner.comissao_tipo,
            comissao_valor_calculado=comissao_valor_calculado,
            commission_metadata={"chargeExternalId": charge.external_id},
        )
        self.db.add(commission)
        try:
            await self.db.flush()
        except Exception:
            # Corrida entre entregas concorrentes do mesmo webhook: a
            # UniqueConstraint(workspace_id, charge_id) impede a segunda
            # linha. Recupera a existente em vez de propagar o erro.
            await self.db.rollback()
            existing = await self.db.scalar(
                select(ReferralCommission).where(
                    ReferralCommission.workspace_id == workspace_id,
                    ReferralCommission.charge_id == charge.id,
                )
            )
            return existing
        return commission

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)
