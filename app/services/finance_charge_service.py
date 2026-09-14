"""
Service: FinanceChargeService
Serviço de cobrança (F2) — cria/consulta/cancela `FinanceCharge` para uma
`FinanceEntry`, via `PaymentGatewayPort` (Asaas real ou `FakeGatewayAdapter`).

Fail-closed: com o gate `external_delivery_allowed()` fechado, a cobrança é
persistida localmente em status `rascunho` e NADA é enviado ao gateway —
comportamento local-first (plano §4.5, §5 F2). `get_gateway_for_workspace`
é importado como nome de módulo para permitir monkeypatch direto em teste,
no mesmo molde de `app/services/platform_official_whatsapp_service.py`.
"""
from __future__ import annotations

import calendar
import uuid as uuid_module
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.klm_runtime import external_delivery_allowed
from app.core.logging import logger
from app.models.finance import FinanceEntry
from app.models.finance_charge import FinanceCharge, FinanceChargeKind, FinanceChargeStatus
from app.payments.factory import GatewayCredentialError, get_gateway_for_workspace
from app.payments.port import (
    BillingType,
    ChargeRequest,
    DiscountTerms,
    FineTerms,
    InterestTerms,
    PixQrCodeResult,
)
from app.schemas.finance_charge import FinanceChargeCreate

DRAFT_NOTICE = (
    "Cobrança salva como rascunho local: o envio ao gateway de pagamento está "
    "desabilitado neste ambiente. Nenhuma chamada externa foi feita."
)

# Estados que impedem cancelamento (a cobrança já saiu do ciclo ativo).
_NON_CANCELLABLE_STATUSES = frozenset(
    {
        FinanceChargeStatus.recebida,
        FinanceChargeStatus.estornada,
        FinanceChargeStatus.chargeback,
        FinanceChargeStatus.cancelada,
    }
)

GATEWAY_CUSTOMER_METADATA_KEY = "gatewayCustomerId"


class FinanceChargeConflictError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)


class FinanceChargeNotFoundError(HTTPException):
    def __init__(self, detail: str = "Cobrança não encontrada.") -> None:
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _add_months(base: date, months: int) -> date:
    month_index = base.month - 1 + months
    year = base.year + month_index // 12
    month = month_index % 12 + 1
    day = min(base.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _split_installments(total: Decimal, count: int) -> list[Decimal]:
    """Divide `total` em `count` parcelas iguais em centavos, com a última
    parcela absorvendo o resíduo de arredondamento (ex.: R$100,00/3 =>
    33.33 + 33.33 + 33.34)."""
    cents_unit = Decimal("0.01")
    base_share = (total / count).quantize(cents_unit, rounding=ROUND_HALF_UP)
    shares = [base_share] * count
    remainder = total - (base_share * count)
    shares[-1] = shares[-1] + remainder
    return shares


def _to_gateway_terms(payload: FinanceChargeCreate):
    discount = None
    if payload.discount is not None:
        discount = DiscountTerms(
            value=payload.discount.value,
            due_date_limit_days=payload.discount.due_date_limit_days,
            type=payload.discount.type,
        )
    fine = None
    if payload.fine is not None:
        fine = FineTerms(value=payload.fine.value, type=payload.fine.type)
    interest = None
    if payload.interest is not None:
        interest = InterestTerms(value=payload.interest.value)
    return discount, fine, interest


class FinanceChargeService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _get_entry(self, workspace_id: UUID, entry_id: UUID) -> FinanceEntry:
        result = await self.db.execute(
            select(FinanceEntry).where(
                FinanceEntry.id == entry_id,
                FinanceEntry.workspace_id == workspace_id,
            )
        )
        entry = result.scalar_one_or_none()
        if entry is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Lançamento financeiro não encontrado.",
            )
        return entry

    async def _get_charge(self, workspace_id: UUID, charge_id: UUID) -> FinanceCharge:
        result = await self.db.execute(
            select(FinanceCharge).where(
                FinanceCharge.id == charge_id,
                FinanceCharge.workspace_id == workspace_id,
            )
        )
        charge = result.scalar_one_or_none()
        if charge is None:
            raise FinanceChargeNotFoundError()
        return charge

    async def create_charge_for_entry(
        self,
        workspace_id: UUID,
        entry_id: UUID,
        payload: FinanceChargeCreate,
    ) -> FinanceCharge:
        entry = await self._get_entry(workspace_id, entry_id)

        existing = await self.db.execute(
            select(FinanceCharge).where(FinanceCharge.entry_id == entry_id)
        )
        if existing.scalar_one_or_none() is not None:
            raise FinanceChargeConflictError(
                "Este lançamento já possui uma cobrança. Cancele a cobrança "
                "existente antes de criar outra."
            )

        due_date = payload.due_date or (entry.due_date.date() if entry.due_date else date.today())
        value = Decimal(str(entry.amount))
        billing_type = payload.billing_type or BillingType.UNDEFINED

        if not external_delivery_allowed():
            # Local-first: rascunho sem nenhuma chamada de rede (plano §4.5/§5 F2).
            charge = FinanceCharge(
                workspace_id=workspace_id,
                entry_id=entry_id,
                external_id=None,
                billing_type=billing_type.value,
                charge_kind=FinanceChargeKind.avulsa,
                status=FinanceChargeStatus.rascunho,
                value=value,
                due_date=datetime.combine(due_date, datetime.min.time(), tzinfo=timezone.utc),
                external_reference=str(entry.id),
                payload_snapshot={"aviso": DRAFT_NOTICE},
            )
            self.db.add(charge)
            await self.db.flush()
            return charge

        customer_external_id = payload.customer_external_id or (
            entry.entry_metadata or {}
        ).get(GATEWAY_CUSTOMER_METADATA_KEY)
        if not customer_external_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Cliente deste lançamento ainda não está sincronizado com o "
                    "gateway de pagamento. Informe customerExternalId ou "
                    "sincronize o cliente antes de cobrar."
                ),
            )

        try:
            gateway = await get_gateway_for_workspace(self.db, workspace_id)
        except GatewayCredentialError as exc:
            raise HTTPException(
                status_code=status.HTTP_424_FAILED_DEPENDENCY, detail=str(exc)
            ) from exc

        discount, fine, interest = _to_gateway_terms(payload)
        request = ChargeRequest(
            customer_external_id=customer_external_id,
            billing_type=billing_type,
            value=value,
            due_date=due_date,
            external_reference=str(entry.id),
            description=payload.description or entry.title,
            discount=discount,
            fine=fine,
            interest=interest,
        )
        try:
            result = await gateway.create_charge(request)
        except Exception:
            logger.error(
                "finance_charge.gateway_create_failed",
                extra={"workspace_id": str(workspace_id), "entry_id": str(entry_id)},
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Não foi possível criar a cobrança no gateway de pagamento.",
            )

        charge = FinanceCharge(
            workspace_id=workspace_id,
            entry_id=entry_id,
            external_id=result.external_id,
            billing_type=billing_type.value,
            charge_kind=FinanceChargeKind.avulsa,
            status=FinanceChargeStatus.criada,
            value=result.value if result.value is not None else value,
            due_date=datetime.combine(
                result.due_date or due_date, datetime.min.time(), tzinfo=timezone.utc
            ),
            invoice_url=result.invoice_url,
            bank_slip_url=result.bank_slip_url,
            external_reference=result.external_reference or str(entry.id),
            payload_snapshot=dict(result.raw or {}),
        )
        self.db.add(charge)
        await self.db.flush()
        return charge

    async def list_charges(
        self,
        workspace_id: UUID,
        status_filter: Optional[FinanceChargeStatus] = None,
        due_before: Optional[date] = None,
        due_after: Optional[date] = None,
        limit: int = 100,
    ) -> list[FinanceCharge]:
        query = select(FinanceCharge).where(FinanceCharge.workspace_id == workspace_id)
        if status_filter is not None:
            query = query.where(FinanceCharge.status == status_filter)
        if due_before is not None:
            query = query.where(
                FinanceCharge.due_date <= datetime.combine(due_before, datetime.max.time(), tzinfo=timezone.utc)
            )
        if due_after is not None:
            query = query.where(
                FinanceCharge.due_date >= datetime.combine(due_after, datetime.min.time(), tzinfo=timezone.utc)
            )
        query = query.order_by(FinanceCharge.due_date.asc()).limit(limit)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_charge(
        self, workspace_id: UUID, charge_id: UUID, with_pix: bool = False
    ) -> tuple[FinanceCharge, Optional[PixQrCodeResult]]:
        charge = await self._get_charge(workspace_id, charge_id)
        pix: Optional[PixQrCodeResult] = None
        if with_pix:
            pix = await self._get_or_cache_pix(workspace_id, charge)
        return charge, pix

    async def _get_or_cache_pix(
        self, workspace_id: UUID, charge: FinanceCharge
    ) -> Optional[PixQrCodeResult]:
        if charge.pix_payload:
            return PixQrCodeResult(payload=charge.pix_payload)
        if charge.status == FinanceChargeStatus.rascunho or not charge.external_id:
            return None
        if not external_delivery_allowed():
            return None
        try:
            gateway = await get_gateway_for_workspace(self.db, workspace_id)
            pix = await gateway.get_pix_qr(charge.external_id)
        except GatewayCredentialError as exc:
            raise HTTPException(
                status_code=status.HTTP_424_FAILED_DEPENDENCY, detail=str(exc)
            ) from exc
        except Exception:
            logger.error(
                "finance_charge.pix_qr_failed",
                extra={"workspace_id": str(workspace_id), "charge_id": str(charge.id)},
            )
            return None
        if pix.payload:
            charge.pix_payload = pix.payload
            await self.db.flush()
        return pix

    async def create_installment_series(
        self,
        workspace_id: UUID,
        entry_id: UUID,
        installment_count: int,
        *,
        total_value: Optional[Decimal] = None,
        first_due_date: Optional[date] = None,
        billing_type: BillingType = BillingType.UNDEFINED,
        customer_external_id: Optional[str] = None,
    ) -> tuple[str, list[FinanceEntry]]:
        """Parcela um lançamento em `installment_count` FinanceEntries filhas
        locais (plano §5 F3, item "PARCELAMENTO").

        Sempre cria N FinanceEntries locais (vencimentos mensais, última
        parcela absorve o resíduo de centavos do arredondamento). Com o gate
        externo aberto e um customer sincronizado, faz UMA chamada ao gateway
        usando `installmentCount`/`totalValue` nativos (o Asaas cria as N
        cobranças do lado dele); com o gate fechado, todas as N cobranças
        ficam como rascunho local — nenhuma chamada de rede é feita.
        """
        parent_entry = await self._get_entry(workspace_id, entry_id)
        value = total_value if total_value is not None else Decimal(str(parent_entry.amount))
        due_date = first_due_date or (
            parent_entry.due_date.date() if parent_entry.due_date else date.today()
        )
        installment_ref = f"series-{uuid_module.uuid4().hex[:12]}"
        installment_values = _split_installments(value, installment_count)

        gateway_result = None
        if external_delivery_allowed():
            resolved_customer_id = customer_external_id or (
                parent_entry.entry_metadata or {}
            ).get(GATEWAY_CUSTOMER_METADATA_KEY)
            if resolved_customer_id:
                try:
                    gateway = await get_gateway_for_workspace(self.db, workspace_id)
                    gateway_result = await gateway.create_charge(
                        ChargeRequest(
                            customer_external_id=resolved_customer_id,
                            billing_type=billing_type,
                            value=installment_values[0],
                            due_date=due_date,
                            external_reference=installment_ref,
                            description=parent_entry.title,
                            installment_count=installment_count,
                            total_value=value,
                        )
                    )
                except GatewayCredentialError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_424_FAILED_DEPENDENCY, detail=str(exc)
                    ) from exc
                except Exception:
                    logger.error(
                        "finance_charge.installment_series_gateway_failed",
                        extra={"workspace_id": str(workspace_id), "entry_id": str(entry_id)},
                    )
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail="Não foi possível criar o parcelamento no gateway de pagamento.",
                    )

        child_entries: list[FinanceEntry] = []
        for index, installment_value in enumerate(installment_values, start=1):
            installment_due_date = _add_months(due_date, index - 1)
            child_entry = FinanceEntry(
                workspace_id=workspace_id,
                matter_id=parent_entry.matter_id,
                title=f"{parent_entry.title} — parcela {index}/{installment_count}",
                description=parent_entry.description,
                entry_type=parent_entry.entry_type,
                amount=installment_value,
                currency=parent_entry.currency,
                due_date=datetime.combine(installment_due_date, datetime.min.time(), tzinfo=timezone.utc),
                entry_metadata={
                    "installmentRef": installment_ref,
                    "installmentIndex": index,
                    "installmentCount": installment_count,
                    "parentEntryId": str(parent_entry.id),
                },
            )
            self.db.add(child_entry)
            await self.db.flush()

            if gateway_result is not None and index == 1:
                charge = FinanceCharge(
                    workspace_id=workspace_id,
                    entry_id=child_entry.id,
                    external_id=gateway_result.external_id,
                    billing_type=billing_type.value,
                    charge_kind=FinanceChargeKind.parcela,
                    status=FinanceChargeStatus.criada,
                    value=installment_value,
                    due_date=datetime.combine(
                        gateway_result.due_date or installment_due_date,
                        datetime.min.time(),
                        tzinfo=timezone.utc,
                    ),
                    invoice_url=gateway_result.invoice_url,
                    bank_slip_url=gateway_result.bank_slip_url,
                    external_reference=gateway_result.external_reference or installment_ref,
                    installment_ref=installment_ref,
                    payload_snapshot=dict(gateway_result.raw or {}),
                )
            elif gateway_result is not None:
                # Demais parcelas da série: o Asaas já as criou do lado dele
                # (installmentCount na primeira chamada), mas a API só
                # devolve o payload da primeira. O `external_id` de cada
                # parcela seguinte é reconciliado pelo webhook/consulta de
                # extrato (plano §3.8) — aqui ficam com `external_id=None`
                # até isso acontecer, sinalizado no `payload_snapshot`.
                charge = FinanceCharge(
                    workspace_id=workspace_id,
                    entry_id=child_entry.id,
                    external_id=None,
                    billing_type=billing_type.value,
                    charge_kind=FinanceChargeKind.parcela,
                    status=FinanceChargeStatus.criada,
                    value=installment_value,
                    due_date=datetime.combine(installment_due_date, datetime.min.time(), tzinfo=timezone.utc),
                    external_reference=installment_ref,
                    installment_ref=installment_ref,
                    payload_snapshot={
                        "aviso": (
                            "Parcela criada no gateway junto com a série; "
                            "externalId ainda não reconciliado."
                        )
                    },
                )
            else:
                charge = FinanceCharge(
                    workspace_id=workspace_id,
                    entry_id=child_entry.id,
                    external_id=None,
                    billing_type=billing_type.value,
                    charge_kind=FinanceChargeKind.parcela,
                    status=FinanceChargeStatus.rascunho,
                    value=installment_value,
                    due_date=datetime.combine(installment_due_date, datetime.min.time(), tzinfo=timezone.utc),
                    external_reference=installment_ref,
                    installment_ref=installment_ref,
                    payload_snapshot={"aviso": DRAFT_NOTICE},
                )
            self.db.add(charge)
            child_entries.append(child_entry)

        await self.db.flush()
        return installment_ref, child_entries

    async def cancel_charge(self, workspace_id: UUID, charge_id: UUID) -> FinanceCharge:
        charge = await self._get_charge(workspace_id, charge_id)
        if charge.status in _NON_CANCELLABLE_STATUSES:
            raise FinanceChargeConflictError(
                f"Cobrança em status '{charge.status.value}' não pode ser cancelada."
            )

        if charge.external_id and external_delivery_allowed():
            try:
                gateway = await get_gateway_for_workspace(self.db, workspace_id)
                await gateway.cancel_charge(charge.external_id)
            except GatewayCredentialError as exc:
                raise HTTPException(
                    status_code=status.HTTP_424_FAILED_DEPENDENCY, detail=str(exc)
                ) from exc
            except Exception:
                logger.error(
                    "finance_charge.gateway_cancel_failed",
                    extra={"workspace_id": str(workspace_id), "charge_id": str(charge.id)},
                )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Não foi possível cancelar a cobrança no gateway de pagamento.",
                )

        charge.status = FinanceChargeStatus.cancelada
        await self.db.flush()
        return charge
