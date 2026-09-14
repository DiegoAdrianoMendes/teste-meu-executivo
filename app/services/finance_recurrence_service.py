"""
Service: FinanceRecurrenceService
Recorrência (F3) — cria/pausa/retoma/encerra `FinanceRecurrence`.

Fail-closed: se `kind=assinatura_gateway` e o gate externo está aberto, a
assinatura é agendada no Asaas via `PaymentGatewayPort.create_subscription`
(Asaas passa a gerar as cobranças sozinho; nós só guardamos
`gateway_subscription_id` e os `FinanceCharge` chegam via webhook, já
tratado por `app/workers/finance_tasks.py`). Em qualquer outro caso —
`kind=geracao_programada` OU gate fechado — nada é enviado ao gateway na
criação; a geração periódica fica inteiramente a cargo da task
`finance_recurrence_workspace`, despachada pelo tick de descoberta
(ver docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md
§5 F3 e §3.4).
"""
from __future__ import annotations

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.klm_runtime import external_delivery_allowed
from app.core.logging import logger
from app.models.client import Client
from app.models.finance_recurrence import (
    ASAAS_CYCLE_BY_RECURRENCE_CYCLE,
    FinanceRecurrence,
    FinanceRecurrenceKind,
    FinanceRecurrenceStatus,
)
from app.payments.factory import GatewayCredentialError, get_gateway_for_workspace
from app.payments.port import BillingType, SubscriptionRequest
from app.schemas.finance_recurrence import FinanceRecurrenceCreate, FinanceRecurrenceUpdate


class FinanceRecurrenceNotFoundError(HTTPException):
    def __init__(self, detail: str = "Recorrência não encontrada.") -> None:
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


class FinanceRecurrenceConflictError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)


class FinanceRecurrenceService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _get_client(self, workspace_id: UUID, client_id: UUID) -> Client:
        result = await self.db.execute(
            select(Client).where(Client.id == client_id, Client.workspace_id == workspace_id)
        )
        client = result.scalar_one_or_none()
        if client is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Cliente não encontrado."
            )
        return client

    async def _get_recurrence(self, workspace_id: UUID, recurrence_id: UUID) -> FinanceRecurrence:
        result = await self.db.execute(
            select(FinanceRecurrence).where(
                FinanceRecurrence.id == recurrence_id,
                FinanceRecurrence.workspace_id == workspace_id,
            )
        )
        recurrence = result.scalar_one_or_none()
        if recurrence is None:
            raise FinanceRecurrenceNotFoundError()
        return recurrence

    async def create_recurrence(
        self, workspace_id: UUID, payload: FinanceRecurrenceCreate
    ) -> FinanceRecurrence:
        client = await self._get_client(workspace_id, payload.client_id)

        recurrence = FinanceRecurrence(
            workspace_id=workspace_id,
            client_id=client.id,
            matter_id=payload.matter_id,
            entry_title=payload.entry_title,
            entry_description=payload.entry_description,
            entry_value=payload.entry_value,
            entry_type=payload.entry_type,
            kind=payload.kind,
            cycle=payload.cycle,
            billing_type=payload.billing_type.value if payload.billing_type else None,
            next_run=payload.next_run,
            end_date=payload.end_date,
            max_count=payload.max_count,
            status=FinanceRecurrenceStatus.ativa,
            recurrence_metadata={},
        )
        self.db.add(recurrence)
        await self.db.flush()

        if payload.kind == FinanceRecurrenceKind.assinatura_gateway and external_delivery_allowed():
            customer_external_id = payload.customer_external_id
            if not customer_external_id:
                # Sem customer sincronizado: mantemos a recorrência ativa, mas
                # o worker fica responsável pela geração local (fail-closed
                # também vale para falta de credencial de cobrança).
                logger.info(
                    "finance_recurrence.subscription_skipped_no_customer",
                    workspace_id=str(workspace_id),
                    recurrence_id=str(recurrence.id),
                )
                return recurrence
            try:
                gateway = await get_gateway_for_workspace(self.db, workspace_id)
                result = await gateway.create_subscription(
                    SubscriptionRequest(
                        customer_external_id=customer_external_id,
                        billing_type=payload.billing_type or BillingType.UNDEFINED,
                        value=payload.entry_value,
                        cycle=ASAAS_CYCLE_BY_RECURRENCE_CYCLE[payload.cycle],
                        next_due_date=payload.next_run,
                        external_reference=str(recurrence.id),
                        description=payload.entry_title,
                        end_date=payload.end_date,
                        max_payments=payload.max_count,
                    )
                )
                recurrence.gateway_subscription_id = result.external_id
                await self.db.flush()
            except GatewayCredentialError as exc:
                raise HTTPException(
                    status_code=status.HTTP_424_FAILED_DEPENDENCY, detail=str(exc)
                ) from exc
            except Exception:
                logger.error(
                    "finance_recurrence.gateway_subscription_failed",
                    workspace_id=str(workspace_id),
                    recurrence_id=str(recurrence.id),
                )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Não foi possível criar a assinatura no gateway de pagamento.",
                )

        return recurrence

    async def list_recurrences(
        self,
        workspace_id: UUID,
        *,
        status_filter: Optional[FinanceRecurrenceStatus] = None,
        client_id: Optional[UUID] = None,
        limit: int = 100,
    ) -> list[FinanceRecurrence]:
        query = select(FinanceRecurrence).where(FinanceRecurrence.workspace_id == workspace_id)
        if status_filter is not None:
            query = query.where(FinanceRecurrence.status == status_filter)
        if client_id is not None:
            query = query.where(FinanceRecurrence.client_id == client_id)
        query = query.order_by(FinanceRecurrence.next_run.asc()).limit(limit)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_recurrence(self, workspace_id: UUID, recurrence_id: UUID) -> FinanceRecurrence:
        return await self._get_recurrence(workspace_id, recurrence_id)

    async def update_recurrence(
        self, workspace_id: UUID, recurrence_id: UUID, payload: FinanceRecurrenceUpdate
    ) -> FinanceRecurrence:
        recurrence = await self._get_recurrence(workspace_id, recurrence_id)
        update_data = payload.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(recurrence, field, value)
        await self.db.flush()
        return recurrence

    async def pause_recurrence(self, workspace_id: UUID, recurrence_id: UUID) -> FinanceRecurrence:
        recurrence = await self._get_recurrence(workspace_id, recurrence_id)
        if recurrence.status == FinanceRecurrenceStatus.encerrada:
            raise FinanceRecurrenceConflictError("Recorrência encerrada não pode ser pausada.")
        recurrence.status = FinanceRecurrenceStatus.pausada
        await self.db.flush()
        return recurrence

    async def resume_recurrence(self, workspace_id: UUID, recurrence_id: UUID) -> FinanceRecurrence:
        recurrence = await self._get_recurrence(workspace_id, recurrence_id)
        if recurrence.status == FinanceRecurrenceStatus.encerrada:
            raise FinanceRecurrenceConflictError("Recorrência encerrada não pode ser retomada.")
        recurrence.status = FinanceRecurrenceStatus.ativa
        await self.db.flush()
        return recurrence

    async def end_recurrence(self, workspace_id: UUID, recurrence_id: UUID) -> FinanceRecurrence:
        recurrence = await self._get_recurrence(workspace_id, recurrence_id)
        recurrence.status = FinanceRecurrenceStatus.encerrada
        await self.db.flush()
        return recurrence
