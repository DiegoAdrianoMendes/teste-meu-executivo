"""Tenant-safe dimensional finance commands and drill-down queries."""

from __future__ import annotations

import base64
import hashlib
import hmac
from calendar import monthrange
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.client import Client
from app.models.contract_billing import ContractBillingProposal
from app.models.finance import FinanceEntry
from app.models.finance_dimensional import (
    FinanceAllocation,
    FinanceBudgetLine,
    FinanceCostCenter,
    FinanceForecast,
    FinanceTaxProfile,
    FinanceValuation,
)
from app.models.matter import Matter
from app.models.omnichannel_v6 import WorkspaceTeam
from app.models.user import User
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership


class FinanceDimensionValidationError(ValueError):
    """A dimensional command violates a tenant or accounting invariant."""


def _contains_sensitive_key(value: Any) -> bool:
    markers = (
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "credential",
        "cookie",
        "private_key",
        "recovery_code",
    )
    if isinstance(value, dict):
        return any(
            any(marker in str(key).lower() for marker in markers) or _contains_sensitive_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


class FinanceDimensionalService:
    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def evidence_ref(document_id: UUID) -> str:
        raw = str(document_id).encode()
        signature = hmac.new(settings.SECRET_KEY.encode(), raw, hashlib.sha256).hexdigest()[:24]
        return base64.urlsafe_b64encode(raw).decode().rstrip("=") + "." + signature

    @staticmethod
    def resolve_evidence_ref(reference: str) -> UUID:
        try:
            encoded, signature = reference.split(".", 1)
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            expected = hmac.new(settings.SECRET_KEY.encode(), raw, hashlib.sha256).hexdigest()[:24]
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            return UUID(raw.decode())
        except Exception as exc:
            raise FinanceDimensionValidationError("Referência de comprovante inválida") from exc

    async def _tenant_object(self, model: Any, object_id: UUID, workspace_id: UUID):
        obj = await self.db.scalar(
            select(model).where(model.id == object_id, model.workspace_id == workspace_id)
        )
        if obj is None:
            raise FinanceDimensionValidationError(
                f"{model.__name__} não encontrado neste workspace"
            )
        return obj

    async def _tenant_user(self, user_id: UUID, workspace_id: UUID) -> User:
        user = await self.db.scalar(
            select(User)
            .join(WorkspaceMembership, WorkspaceMembership.user_id == User.id)
            .where(
                User.id == user_id,
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.status == MembershipStatus.active,
            )
        )
        if user is None:
            raise FinanceDimensionValidationError(
                "Usuário não possui vínculo ativo com este workspace"
            )
        return user

    async def _cost_center_scope(self, workspace_id: UUID, cost_center_id: UUID):
        """Return the selected cost center and every descendant exactly once."""
        await self._tenant_object(FinanceCostCenter, cost_center_id, workspace_id)
        scope = (
            select(FinanceCostCenter.id)
            .where(
                FinanceCostCenter.id == cost_center_id,
                FinanceCostCenter.workspace_id == workspace_id,
            )
            .cte("finance_cost_center_scope", recursive=True)
        )
        scope = scope.union_all(
            select(FinanceCostCenter.id)
            .join(scope, FinanceCostCenter.parent_id == scope.c.id)
            .where(FinanceCostCenter.workspace_id == workspace_id)
        )
        return scope

    async def list_cost_centers(self, workspace_id: UUID):
        return (
            await self.db.scalars(
                select(FinanceCostCenter)
                .where(FinanceCostCenter.workspace_id == workspace_id)
                .order_by(FinanceCostCenter.code, FinanceCostCenter.id)
            )
        ).all()

    async def lookup(
        self,
        workspace_id: UUID,
        *,
        entity_type: str,
        search: str = "",
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Return tenant-scoped human labels for dimensional selectors."""
        term = f"%{search.strip()}%"
        try:
            offset = int(base64.urlsafe_b64decode(cursor.encode()).decode()) if cursor else 0
        except Exception as exc:
            raise FinanceDimensionValidationError("Cursor de lookup inválido") from exc
        limit = min(max(limit, 1), 100)
        if entity_type == "entry":
            query = select(FinanceEntry).where(FinanceEntry.workspace_id == workspace_id)
            if search.strip():
                query = query.where(FinanceEntry.title.ilike(term))
            values = (
                await self.db.scalars(
                    query.order_by(FinanceEntry.created_at.desc(), FinanceEntry.id)
                    .offset(offset)
                    .limit(limit + 1)
                )
            ).all()
            items = [
                {
                    "id": str(item.id),
                    "label": item.title,
                    "matterId": str(item.matter_id) if item.matter_id else None,
                }
                for item in values
            ]
        elif entity_type == "client":
            query = select(Client).where(Client.workspace_id == workspace_id)
            if search.strip():
                query = query.where(Client.name.ilike(term))
            values = (
                await self.db.scalars(
                    query.order_by(Client.name, Client.id).offset(offset).limit(limit + 1)
                )
            ).all()
            items = [{"id": str(item.id), "label": item.name} for item in values]
        elif entity_type == "matter":
            query = select(Matter).where(Matter.workspace_id == workspace_id)
            if search.strip():
                query = query.where(
                    (Matter.case_number.ilike(term)) | (Matter.demand_type.ilike(term))
                )
            values = (
                await self.db.scalars(
                    query.order_by(Matter.updated_at.desc(), Matter.id)
                    .offset(offset)
                    .limit(limit + 1)
                )
            ).all()
            items = [
                {
                    "id": str(item.id),
                    "label": item.case_number or item.demand_type or "Caso sem identificação",
                    "clientId": str(item.client_id) if item.client_id else None,
                }
                for item in values
            ]
        elif entity_type == "contract":
            query = select(ContractBillingProposal).where(
                ContractBillingProposal.workspace_id == workspace_id
            )
            if search.strip():
                query = query.where(ContractBillingProposal.source_document_name.ilike(term))
            values = (
                await self.db.scalars(
                    query.order_by(
                        ContractBillingProposal.created_at.desc(), ContractBillingProposal.id
                    )
                    .offset(offset)
                    .limit(limit + 1)
                )
            ).all()
            items = [
                {
                    "id": str(item.id),
                    "label": item.source_document_name or "Contrato sem título",
                    "matterId": str(item.matter_id),
                }
                for item in values
            ]
        elif entity_type == "team":
            query = select(WorkspaceTeam).where(WorkspaceTeam.workspace_id == workspace_id)
            if search.strip():
                query = query.where(WorkspaceTeam.name.ilike(term))
            values = (
                await self.db.scalars(
                    query.order_by(WorkspaceTeam.name, WorkspaceTeam.id)
                    .offset(offset)
                    .limit(limit + 1)
                )
            ).all()
            items = [{"id": str(item.id), "label": item.name} for item in values]
        elif entity_type == "user":
            query = (
                select(User)
                .join(WorkspaceMembership, WorkspaceMembership.user_id == User.id)
                .where(
                    WorkspaceMembership.workspace_id == workspace_id,
                    WorkspaceMembership.status == MembershipStatus.active,
                    User.is_active.is_(True),
                )
            )
            if search.strip():
                query = query.where(User.name.ilike(term))
            values = (
                await self.db.scalars(
                    query.order_by(User.name, User.id).offset(offset).limit(limit + 1)
                )
            ).all()
            items = [{"id": str(item.id), "label": item.name} for item in values]
        else:
            raise FinanceDimensionValidationError("Tipo de seletor financeiro inválido")
        has_more = len(items) > limit
        items = items[:limit]
        return {
            "items": items,
            "entityType": entity_type,
            "search": search,
            "limit": limit,
            "nextCursor": base64.urlsafe_b64encode(str(offset + limit).encode()).decode()
            if has_more
            else None,
        }

    async def resolve_lookups(self, workspace_id: UUID, *, entity_type: str, ids: list[UUID]):
        if entity_type not in {"entry", "client", "matter", "contract", "team", "user"}:
            raise FinanceDimensionValidationError("Tipo de seletor financeiro inválido")
        unique_ids = list(dict.fromkeys(ids))
        model = {
            "entry": FinanceEntry,
            "client": Client,
            "matter": Matter,
            "contract": ContractBillingProposal,
            "team": WorkspaceTeam,
            "user": User,
        }[entity_type]
        if entity_type == "user":
            query = (
                select(User)
                .join(WorkspaceMembership, WorkspaceMembership.user_id == User.id)
                .where(
                    WorkspaceMembership.workspace_id == workspace_id,
                    WorkspaceMembership.status == MembershipStatus.active,
                    User.id.in_(unique_ids),
                    User.is_active.is_(True),
                )
            )
        else:
            query = select(model).where(
                model.workspace_id == workspace_id,
                model.id.in_(unique_ids),
            )
        objects = {str(obj.id): obj for obj in (await self.db.scalars(query)).all()}
        values = []
        for identifier in unique_ids:
            obj = objects.get(str(identifier))
            if obj is None:
                continue
            label = (
                getattr(obj, "title", None)
                or getattr(obj, "name", None)
                or getattr(obj, "source_document_name", None)
                or getattr(obj, "case_number", None)
                or getattr(obj, "demand_type", None)
                or "Registro"
            )
            inactive = getattr(obj, "is_active", True) is False or getattr(
                obj, "status", "active"
            ) not in {"active", None}
            values.append({"id": str(obj.id), "label": label, "inactive": inactive})
        return {"items": values, "entityType": entity_type}

    async def create_cost_center(self, workspace_id: UUID, data: Any):
        payload = data.model_dump()
        parent_id = payload.get("parent_id")
        if parent_id is not None:
            await self._tenant_object(FinanceCostCenter, parent_id, workspace_id)
        owner_id = payload.get("owner_user_id")
        if owner_id is not None:
            await self._tenant_user(owner_id, workspace_id)
        obj = FinanceCostCenter(workspace_id=workspace_id, **payload)
        self.db.add(obj)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def update_cost_center(self, workspace_id: UUID, cost_center_id: UUID, data: Any):
        obj = await self.db.scalar(
            select(FinanceCostCenter)
            .where(
                FinanceCostCenter.id == cost_center_id,
                FinanceCostCenter.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if obj is None:
            raise FinanceDimensionValidationError("Centro de custo não encontrado")
        payload = data.model_dump(exclude_unset=True)
        expected_version = payload.pop("expected_version")
        if obj.version != expected_version:
            raise FinanceDimensionValidationError("Versão do centro de custo desatualizada")
        parent_id = payload.get("parent_id")
        await self._validate_cost_center_parent(workspace_id, obj.id, parent_id)
        if payload.get("owner_user_id") is not None:
            await self._tenant_user(payload["owner_user_id"], workspace_id)
        for key, value in payload.items():
            setattr(obj, key, value)
        obj.active = obj.status == "active"
        obj.version += 1
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def cost_center_impact(self, workspace_id: UUID, cost_center_id: UUID):
        obj = await self._tenant_object(FinanceCostCenter, cost_center_id, workspace_id)
        children = await self.db.scalar(
            select(func.count(FinanceCostCenter.id)).where(
                FinanceCostCenter.workspace_id == workspace_id,
                FinanceCostCenter.parent_id == cost_center_id,
            )
        )
        allocations, amount = (
            await self.db.execute(
                select(
                    func.count(FinanceAllocation.id),
                    func.coalesce(func.sum(FinanceAllocation.amount), 0),
                ).where(
                    FinanceAllocation.workspace_id == workspace_id,
                    FinanceAllocation.cost_center_id == cost_center_id,
                )
            )
        ).one()
        return {
            "costCenterId": str(obj.id),
            "version": obj.version,
            "childCount": int(children or 0),
            "allocationCount": int(allocations or 0),
            "allocatedAmount": str(Decimal(str(amount or 0))),
            "requiresConfirmation": bool(children or allocations),
        }

    async def cost_center_impact_preview(
        self, workspace_id: UUID, cost_center_id: UUID, patch: dict[str, Any], expected_version: int
    ):
        """Validate a proposed change without mutating the hierarchy."""
        obj = await self._tenant_object(FinanceCostCenter, cost_center_id, workspace_id)
        if obj.version != expected_version:
            raise FinanceDimensionValidationError("Versão do centro de custo desatualizada")
        proposed_parent = patch.get("parent_id", obj.parent_id)
        await self._validate_cost_center_parent(workspace_id, obj.id, proposed_parent)
        return {
            **(await self.cost_center_impact(workspace_id, cost_center_id)),
            "proposed": patch,
            "expectedVersion": expected_version,
            "eligible": True,
        }

    async def _validate_cost_center_parent(
        self,
        workspace_id: UUID,
        cost_center_id: UUID,
        parent_id: UUID | None,
    ) -> None:
        if parent_id == cost_center_id:
            raise FinanceDimensionValidationError("Centro de custo não pode ser seu próprio pai")
        if parent_id is None:
            return
        await self._tenant_object(FinanceCostCenter, parent_id, workspace_id)
        visited = {cost_center_id}
        cursor: UUID | None = parent_id
        while cursor is not None:
            if cursor in visited:
                raise FinanceDimensionValidationError(
                    "Movimentação criaria ciclo na hierarquia de centros"
                )
            visited.add(cursor)
            cursor = await self.db.scalar(
                select(FinanceCostCenter.parent_id).where(
                    FinanceCostCenter.id == cursor,
                    FinanceCostCenter.workspace_id == workspace_id,
                )
            )

    async def create_budget_line(self, workspace_id: UUID, data: Any):
        payload = data.model_dump()
        if payload.get("cost_center_id") is not None:
            await self._tenant_object(FinanceCostCenter, payload["cost_center_id"], workspace_id)
        obj = FinanceBudgetLine(workspace_id=workspace_id, **payload)
        self.db.add(obj)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def list_budget_lines(
        self,
        workspace_id: UUID,
        *,
        period_start: date | None = None,
        period_end: date | None = None,
        cost_center_id: UUID | None = None,
    ):
        query = select(FinanceBudgetLine).where(
            FinanceBudgetLine.workspace_id == workspace_id,
            FinanceBudgetLine.status == "active",
        )
        if period_start is not None:
            query = query.where(FinanceBudgetLine.period_end >= period_start)
        if period_end is not None:
            query = query.where(FinanceBudgetLine.period_start <= period_end)
        if cost_center_id is not None:
            query = query.where(FinanceBudgetLine.cost_center_id == cost_center_id)
        return (
            await self.db.scalars(
                query.order_by(
                    FinanceBudgetLine.period_start,
                    FinanceBudgetLine.entry_type,
                    FinanceBudgetLine.id,
                )
            )
        ).all()

    async def create_allocation(self, workspace_id: UUID, data: Any):
        payload = data.model_dump()
        entry = await self._tenant_object(FinanceEntry, payload["finance_entry_id"], workspace_id)
        await self._tenant_object(FinanceCostCenter, payload["cost_center_id"], workspace_id)

        client = None
        if payload.get("client_id"):
            client = await self._tenant_object(Client, payload["client_id"], workspace_id)
        matter = None
        if payload.get("matter_id"):
            matter = await self._tenant_object(Matter, payload["matter_id"], workspace_id)
            if client is not None and matter.client_id != client.id:
                raise FinanceDimensionValidationError("Caso não pertence ao cliente informado")
            if entry.matter_id is not None and entry.matter_id != matter.id:
                raise FinanceDimensionValidationError("Lançamento pertence a outro caso")
        contract = None
        if payload.get("contract_id"):
            contract = await self._tenant_object(
                ContractBillingProposal, payload["contract_id"], workspace_id
            )
            if matter is not None and contract.matter_id != matter.id:
                raise FinanceDimensionValidationError("Contrato pertence a outro caso")
            contract_matter = await self._tenant_object(Matter, contract.matter_id, workspace_id)
            if client is not None and contract_matter.client_id != client.id:
                raise FinanceDimensionValidationError("Contrato pertence a outro cliente")
        if payload.get("team_id"):
            await self._tenant_object(WorkspaceTeam, payload["team_id"], workspace_id)
        if payload.get("user_id"):
            await self._tenant_user(payload["user_id"], workspace_id)

        existing_percentage = await self.db.scalar(
            select(func.coalesce(func.sum(FinanceAllocation.percentage), 0)).where(
                FinanceAllocation.workspace_id == workspace_id,
                FinanceAllocation.finance_entry_id == entry.id,
            )
        )
        percentage = Decimal(str(payload["percentage"]))
        if Decimal(str(existing_percentage or 0)) + percentage > Decimal("100"):
            raise FinanceDimensionValidationError(
                "A soma das alocações do lançamento ultrapassa 100%"
            )
        expected_amount = (Decimal(str(entry.amount)) * percentage / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        supplied_amount = payload.get("amount")
        if supplied_amount is not None and abs(
            Decimal(str(supplied_amount)) - expected_amount
        ) > Decimal("0.01"):
            raise FinanceDimensionValidationError(
                "O valor alocado não corresponde ao percentual do lançamento"
            )
        payload["amount"] = expected_amount
        obj = FinanceAllocation(workspace_id=workspace_id, **payload)
        self.db.add(obj)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def create_forecast(self, workspace_id: UUID, data: Any):
        payload = data.model_dump()
        if payload["period_end"] < payload["period_start"]:
            raise FinanceDimensionValidationError("Fim do período anterior ao início")
        obj = FinanceForecast(workspace_id=workspace_id, **payload)
        self.db.add(obj)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def list_forecasts(self, workspace_id: UUID, *, limit: int = 100):
        return (
            await self.db.scalars(
                select(FinanceForecast)
                .where(FinanceForecast.workspace_id == workspace_id)
                .order_by(FinanceForecast.created_at.desc(), FinanceForecast.id)
                .limit(limit)
            )
        ).all()

    async def generate_forecast(
        self,
        workspace_id: UUID,
        *,
        as_of: date,
        horizon: str,
        scenarios: list[str],
    ):
        horizon_days = {"30d": 30, "90d": 90, "180d": 180, "365d": 365, "3y": 1095}
        if horizon not in horizon_days:
            raise FinanceDimensionValidationError("Horizonte de forecast inválido")
        allowed = {
            "conservative": Decimal("0.85"),
            "base": Decimal("1"),
            "optimistic": Decimal("1.15"),
        }
        if not scenarios or any(item not in allowed for item in scenarios):
            raise FinanceDimensionValidationError("Cenário de forecast inválido")
        history_start = as_of - timedelta(days=90)
        rows = (
            await self.db.execute(
                select(
                    FinanceEntry.entry_type,
                    func.coalesce(func.sum(FinanceEntry.amount), 0),
                )
                .where(
                    FinanceEntry.workspace_id == workspace_id,
                    FinanceEntry.status == "paid",
                    FinanceEntry.paid_at
                    >= datetime.combine(history_start, datetime.min.time(), UTC),
                    FinanceEntry.paid_at
                    < datetime.combine(as_of + timedelta(days=1), datetime.min.time(), UTC),
                )
                .group_by(FinanceEntry.entry_type)
            )
        ).all()
        totals = {
            str(kind.value if hasattr(kind, "value") else kind): Decimal(str(amount or 0))
            for kind, amount in rows
        }
        scale = Decimal(str(horizon_days[horizon])) / Decimal("90")
        generated = []
        for scenario in scenarios:
            factor = allowed[scenario]
            obj = FinanceForecast(
                workspace_id=workspace_id,
                period_start=as_of + timedelta(days=1),
                period_end=as_of + timedelta(days=horizon_days[horizon]),
                revenue=(totals.get("receivable", Decimal("0")) * scale * factor).quantize(
                    Decimal("0.01")
                ),
                expense=(
                    totals.get("expense", Decimal("0")) * scale * (Decimal("2") - factor)
                ).quantize(Decimal("0.01")),
                scenario=scenario,
                horizon=horizon,
                assumptions={
                    "version": 1,
                    "asOf": as_of.isoformat(),
                    "historyDays": 90,
                    "factor": str(factor),
                    "source": "FinanceEntry paid trailing 90 days",
                },
            )
            self.db.add(obj)
            generated.append(obj)
        await self.db.flush()
        for obj in generated:
            await self.db.refresh(obj)
        return generated

    async def create_valuation(self, workspace_id: UUID, data: Any):
        payload = data.model_dump()
        if not payload.get("assumptions"):
            raise FinanceDimensionValidationError(
                "Valuation exige premissas explícitas e versionadas"
            )
        obj = FinanceValuation(workspace_id=workspace_id, **payload)
        self.db.add(obj)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def list_valuations(self, workspace_id: UUID, *, limit: int = 100):
        return (
            await self.db.scalars(
                select(FinanceValuation)
                .where(FinanceValuation.workspace_id == workspace_id)
                .order_by(FinanceValuation.valuation_date.desc(), FinanceValuation.id)
                .limit(limit)
            )
        ).all()

    async def calculate_valuation(
        self,
        workspace_id: UUID,
        *,
        method: str,
        as_of: date,
        assumptions: dict[str, Any],
    ):
        if method not in {"dcf_simplified", "revenue_multiple", "ebitda_multiple"}:
            raise FinanceDimensionValidationError("Método de valuation inválido")
        period_start = datetime.combine(as_of - timedelta(days=365), datetime.min.time(), UTC)
        period_end = datetime.combine(as_of + timedelta(days=1), datetime.min.time(), UTC)
        rows = (
            await self.db.execute(
                select(
                    FinanceEntry.entry_type,
                    func.coalesce(func.sum(FinanceEntry.amount), 0),
                )
                .where(
                    FinanceEntry.workspace_id == workspace_id,
                    FinanceEntry.status != "cancelled",
                    func.coalesce(FinanceEntry.due_date, FinanceEntry.created_at) >= period_start,
                    func.coalesce(FinanceEntry.due_date, FinanceEntry.created_at) < period_end,
                )
                .group_by(FinanceEntry.entry_type)
            )
        ).all()
        totals = {
            str(kind.value if hasattr(kind, "value") else kind): Decimal(str(amount or 0))
            for kind, amount in rows
        }
        revenue = totals.get("receivable", Decimal("0")) - totals.get("refund", Decimal("0"))
        ebitda_proxy = revenue - totals.get("expense", Decimal("0"))
        normalized = {**assumptions, "version": 1, "asOf": as_of.isoformat()}
        if method == "revenue_multiple":
            multiple = Decimal(str(assumptions.get("multiple", "1")))
            value = revenue * multiple
        elif method == "ebitda_multiple":
            multiple = Decimal(str(assumptions.get("multiple", "1")))
            value = max(ebitda_proxy, Decimal("0")) * multiple
        else:
            discount_rate = Decimal(str(assumptions.get("discountRate", "20"))) / Decimal("100")
            growth_rate = Decimal(str(assumptions.get("growthRate", "5"))) / Decimal("100")
            years = int(assumptions.get("years", 3))
            if discount_rate <= growth_rate or years < 1 or years > 10:
                raise FinanceDimensionValidationError("Premissas DCF inválidas")
            cash_flow = ebitda_proxy
            value = Decimal("0")
            for year in range(1, years + 1):
                cash_flow *= Decimal("1") + growth_rate
                value += cash_flow / ((Decimal("1") + discount_rate) ** year)
            terminal = cash_flow * (Decimal("1") + growth_rate) / (discount_rate - growth_rate)
            value += terminal / ((Decimal("1") + discount_rate) ** years)
        base_value = max(value, Decimal("0")).quantize(Decimal("0.01"))
        contract_rows = (
            await self.db.execute(
                select(
                    ContractBillingProposal.id,
                    ContractBillingProposal.source_document_name,
                    ContractBillingProposal.matter_id,
                    func.coalesce(func.sum(FinanceAllocation.amount), 0),
                )
                .join(
                    FinanceAllocation,
                    and_(
                        FinanceAllocation.contract_id == ContractBillingProposal.id,
                        FinanceAllocation.workspace_id == ContractBillingProposal.workspace_id,
                    ),
                )
                .join(
                    FinanceEntry,
                    and_(
                        FinanceEntry.id == FinanceAllocation.finance_entry_id,
                        FinanceEntry.workspace_id == FinanceAllocation.workspace_id,
                    ),
                )
                .where(
                    ContractBillingProposal.workspace_id == workspace_id,
                    FinanceEntry.status != "cancelled",
                    FinanceEntry.entry_type == "receivable",
                    func.coalesce(FinanceEntry.due_date, FinanceEntry.created_at) >= period_start,
                    func.coalesce(FinanceEntry.due_date, FinanceEntry.created_at) < period_end,
                )
                .group_by(
                    ContractBillingProposal.id,
                    ContractBillingProposal.source_document_name,
                    ContractBillingProposal.matter_id,
                )
                .order_by(func.sum(FinanceAllocation.amount).desc(), ContractBillingProposal.id)
                .limit(20)
            )
        ).all()
        normalized.update(
            {
                "range": {
                    "low": str((base_value * Decimal("0.80")).quantize(Decimal("0.01"))),
                    "base": str(base_value),
                    "high": str((base_value * Decimal("1.20")).quantize(Decimal("0.01"))),
                },
                "decomposition": {
                    "netRevenue": str(revenue.quantize(Decimal("0.01"))),
                    "ebitdaProxy": str(ebitda_proxy.quantize(Decimal("0.01"))),
                    "method": method,
                    "sourcePeriodStart": period_start.date().isoformat(),
                    "sourcePeriodEnd": as_of.isoformat(),
                },
                "supportingContracts": [
                    {
                        "contractId": str(contract_id),
                        "matterId": str(matter_id),
                        "label": label or "Contrato sem nome",
                        "allocatedRevenue": str(
                            Decimal(str(amount or 0)).quantize(Decimal("0.01"))
                        ),
                    }
                    for contract_id, label, matter_id, amount in contract_rows
                ],
            }
        )
        obj = FinanceValuation(
            workspace_id=workspace_id,
            method=method,
            valuation_date=as_of,
            enterprise_value=base_value,
            assumptions=normalized,
            premise_quality="partial",
        )
        self.db.add(obj)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def upsert_tax_profile(self, workspace_id: UUID, data: Any):
        payload = data.model_dump()
        expected_version = payload.pop("expected_version", None)
        if _contains_sensitive_key(
            {
                "sources": payload.get("sources"),
                "reform_calendar": payload.get("reform_calendar"),
                "settings": payload.get("settings"),
            }
        ):
            raise FinanceDimensionValidationError(
                "Perfil tributário não aceita segredos ou credenciais"
            )
        if not payload.get("sources"):
            raise FinanceDimensionValidationError(
                "Perfil tributário exige ao menos uma fonte identificada"
            )
        obj = await self.db.scalar(
            select(FinanceTaxProfile)
            .where(FinanceTaxProfile.workspace_id == workspace_id)
            .order_by(FinanceTaxProfile.effective_from.desc(), FinanceTaxProfile.version.desc())
            .with_for_update()
        )
        if obj is None:
            obj = FinanceTaxProfile(workspace_id=workspace_id, **payload)
            self.db.add(obj)
        else:
            if expected_version is not None and obj.version != expected_version:
                raise FinanceDimensionValidationError("Versão tributária desatualizada")
            # Preserve the previous effective policy as historical evidence.
            # A new row is the only safe representation of a changed tax regime.
            if payload != {key: getattr(obj, key) for key in payload}:
                obj.effective_until = payload["effective_from"] - timedelta(days=1)
                obj = FinanceTaxProfile(
                    workspace_id=workspace_id, version=obj.version + 1, **payload
                )
                self.db.add(obj)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def drilldown(
        self,
        workspace_id: UUID,
        *,
        cost_center_id: UUID | None = None,
        client_id: UUID | None = None,
        contract_id: UUID | None = None,
        matter_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        query = (
            select(
                FinanceAllocation,
                FinanceEntry,
                FinanceCostCenter,
                Client,
                Matter,
                ContractBillingProposal,
            )
            .join(
                FinanceEntry,
                and_(
                    FinanceEntry.id == FinanceAllocation.finance_entry_id,
                    FinanceEntry.workspace_id == FinanceAllocation.workspace_id,
                ),
            )
            .join(
                FinanceCostCenter,
                and_(
                    FinanceCostCenter.id == FinanceAllocation.cost_center_id,
                    FinanceCostCenter.workspace_id == FinanceAllocation.workspace_id,
                ),
            )
            .outerjoin(
                Matter,
                and_(
                    Matter.id == FinanceAllocation.matter_id,
                    Matter.workspace_id == FinanceAllocation.workspace_id,
                ),
            )
            .outerjoin(
                Client,
                and_(
                    Client.id == FinanceAllocation.client_id,
                    Client.workspace_id == FinanceAllocation.workspace_id,
                ),
            )
            .outerjoin(
                ContractBillingProposal,
                and_(
                    ContractBillingProposal.id == FinanceAllocation.contract_id,
                    ContractBillingProposal.workspace_id == FinanceAllocation.workspace_id,
                ),
            )
            .where(FinanceAllocation.workspace_id == workspace_id)
        )
        cost_center_scope = (
            await self._cost_center_scope(workspace_id, cost_center_id)
            if cost_center_id is not None
            else None
        )
        filter_expressions = []
        if cost_center_scope is not None:
            filter_expressions.append(
                FinanceAllocation.cost_center_id.in_(select(cost_center_scope.c.id))
            )
        for column, value in (
            (FinanceAllocation.client_id, client_id),
            (FinanceAllocation.contract_id, contract_id),
            (FinanceAllocation.matter_id, matter_id),
        ):
            if value is not None:
                filter_expressions.append(column == value)
        query = query.where(*filter_expressions)
        cost_center_scope_count = (
            int(await self.db.scalar(select(func.count()).select_from(cost_center_scope)) or 0)
            if cost_center_scope is not None
            else 0
        )
        allocation_count = int(
            await self.db.scalar(
                select(func.count(FinanceAllocation.id)).where(
                    FinanceAllocation.workspace_id == workspace_id,
                    *filter_expressions,
                )
            )
            or 0
        )
        allocated_amount = Decimal(
            str(
                await self.db.scalar(
                    select(func.coalesce(func.sum(FinanceAllocation.amount), 0)).where(
                        FinanceAllocation.workspace_id == workspace_id,
                        *filter_expressions,
                    )
                )
                or 0
            )
        )
        is_consolidated = not any((cost_center_id, client_id, contract_id, matter_id))
        allocated_for_entry = (
            select(func.coalesce(func.sum(FinanceAllocation.amount), 0))
            .where(
                FinanceAllocation.workspace_id == workspace_id,
                FinanceAllocation.finance_entry_id == FinanceEntry.id,
            )
            .correlate(FinanceEntry)
            .scalar_subquery()
        )
        residual_amount = FinanceEntry.amount - allocated_for_entry
        residual_count = 0
        residual_total = Decimal("0")
        residual_rows: list[Any] = []
        if is_consolidated:
            residual_count, raw_residual_total = (
                await self.db.execute(
                    select(
                        func.count(FinanceEntry.id),
                        func.coalesce(func.sum(residual_amount), 0),
                    ).where(
                        FinanceEntry.workspace_id == workspace_id,
                        residual_amount > 0,
                    )
                )
            ).one()
            residual_count = int(residual_count or 0)
            residual_total = Decimal(str(raw_residual_total or 0))

        allocation_page_limit = max(0, min(limit, allocation_count - offset))
        rows = []
        if allocation_page_limit:
            rows = (
                await self.db.execute(
                    query.order_by(FinanceEntry.created_at.desc(), FinanceEntry.id)
                    .offset(offset)
                    .limit(allocation_page_limit)
                )
            ).all()
        residual_page_limit = limit - len(rows)
        if is_consolidated and residual_page_limit > 0:
            residual_offset = max(0, offset - allocation_count)
            residual_rows = (
                await self.db.execute(
                    select(
                        FinanceEntry.id,
                        FinanceEntry.title,
                        residual_amount,
                        FinanceEntry.receipt_document_ref,
                    )
                    .where(
                        FinanceEntry.workspace_id == workspace_id,
                        residual_amount > 0,
                    )
                    .order_by(FinanceEntry.created_at.desc(), FinanceEntry.id)
                    .offset(residual_offset)
                    .limit(residual_page_limit)
                )
            ).all()
        total_count = allocation_count + residual_count
        total_amount = allocated_amount + residual_total
        items: list[dict[str, Any]] = []
        for allocation, entry, center, client, matter, contract in rows:
            amount = Decimal(str(allocation.amount or 0))
            items.append(
                {
                    "allocationId": str(allocation.id),
                    "entryId": str(entry.id),
                    "entryLabel": entry.title,
                    "costCenterId": str(center.id),
                    "costCenterLabel": f"{center.code} · {center.name}",
                    "clientId": str(client.id) if client else None,
                    "clientLabel": client.name if client else "Não vinculado",
                    "contractId": str(contract.id) if contract else None,
                    "contractLabel": contract.source_document_name if contract else "Não vinculado",
                    "matterId": str(matter.id) if matter else None,
                    "matterLabel": (
                        (matter.case_number or matter.demand_type) if matter else "Não vinculado"
                    ),
                    "percentage": str(allocation.percentage),
                    "amount": str(amount),
                    "source": allocation.source,
                    "evidenceAvailable": bool(entry.receipt_document_ref),
                    "evidenceRef": (
                        self.evidence_ref(entry.receipt_document_ref)
                        if entry.receipt_document_ref
                        else None
                    ),
                }
            )
        for entry_id, entry_label, amount, receipt_document_ref in residual_rows:
            items.append(
                {
                    "allocationId": None,
                    "entryId": str(entry_id),
                    "entryLabel": entry_label,
                    "costCenterId": None,
                    "costCenterLabel": "Não alocado",
                    "clientId": None,
                    "clientLabel": "Não vinculado",
                    "contractId": None,
                    "contractLabel": "Não vinculado",
                    "matterId": None,
                    "matterLabel": "Não vinculado",
                    "percentage": None,
                    "amount": str(Decimal(str(amount or 0))),
                    "source": "unallocated_residual",
                    "evidenceAvailable": bool(receipt_document_ref),
                    "evidenceRef": (
                        self.evidence_ref(receipt_document_ref) if receipt_document_ref else None
                    ),
                }
            )
        if cost_center_id is None:
            group_dimension = "cost_center"
            group_id = FinanceAllocation.cost_center_id
            group_label = func.concat(FinanceCostCenter.code, " · ", FinanceCostCenter.name)
            group_query = select(group_id, group_label, func.sum(FinanceAllocation.amount)).join(
                FinanceCostCenter,
                and_(
                    FinanceCostCenter.id == FinanceAllocation.cost_center_id,
                    FinanceCostCenter.workspace_id == FinanceAllocation.workspace_id,
                ),
            )
        elif client_id is None:
            group_dimension = "client"
            group_id = FinanceAllocation.client_id
            group_label = func.coalesce(Client.name, "Não alocado")
            group_query = select(
                group_id, group_label, func.sum(FinanceAllocation.amount)
            ).outerjoin(
                Client,
                and_(
                    Client.id == FinanceAllocation.client_id,
                    Client.workspace_id == FinanceAllocation.workspace_id,
                ),
            )
        elif contract_id is None:
            group_dimension = "contract"
            group_id = FinanceAllocation.contract_id
            group_label = func.coalesce(ContractBillingProposal.source_document_name, "Não alocado")
            group_query = select(
                group_id, group_label, func.sum(FinanceAllocation.amount)
            ).outerjoin(
                ContractBillingProposal,
                and_(
                    ContractBillingProposal.id == FinanceAllocation.contract_id,
                    ContractBillingProposal.workspace_id == FinanceAllocation.workspace_id,
                ),
            )
        elif matter_id is None:
            group_dimension = "matter"
            group_id = FinanceAllocation.matter_id
            group_label = func.coalesce(Matter.case_number, Matter.demand_type, "Não alocado")
            group_query = select(
                group_id, group_label, func.sum(FinanceAllocation.amount)
            ).outerjoin(
                Matter,
                and_(
                    Matter.id == FinanceAllocation.matter_id,
                    Matter.workspace_id == FinanceAllocation.workspace_id,
                ),
            )
        else:
            group_dimension = "entry"
            group_id = FinanceAllocation.finance_entry_id
            group_label = FinanceEntry.title
            group_query = select(group_id, group_label, func.sum(FinanceAllocation.amount)).join(
                FinanceEntry,
                and_(
                    FinanceEntry.id == FinanceAllocation.finance_entry_id,
                    FinanceEntry.workspace_id == FinanceAllocation.workspace_id,
                ),
            )
        group_rows = (
            await self.db.execute(
                group_query.where(
                    FinanceAllocation.workspace_id == workspace_id,
                    *filter_expressions,
                )
                .group_by(group_id, group_label)
                .order_by(func.sum(FinanceAllocation.amount).desc(), group_id)
            )
        ).all()
        groups = [
            {
                "dimension": group_dimension,
                "id": str(identifier) if identifier else None,
                f"{
                    ''.join(
                        word.capitalize() if index else word
                        for index, word in enumerate(group_dimension.split('_'))
                    )
                }Id": str(identifier) if identifier else None,
                "label": label,
                "amount": str(Decimal(str(amount or 0))),
            }
            for identifier, label, amount in group_rows
        ]
        if is_consolidated and residual_total > 0:
            groups.append(
                {
                    "dimension": "cost_center",
                    "id": None,
                    "costCenterId": None,
                    "label": "Não alocado",
                    "amount": str(residual_total),
                    "actionRequired": True,
                }
            )
        return {
            "workspaceId": str(workspace_id),
            "filters": {
                "costCenterId": str(cost_center_id) if cost_center_id else None,
                "clientId": str(client_id) if client_id else None,
                "contractId": str(contract_id) if contract_id else None,
                "matterId": str(matter_id) if matter_id else None,
            },
            "total": str(Decimal(str(total_amount or 0))),
            "groupDimension": group_dimension,
            "groups": groups,
            "items": items,
            "totalCount": total_count,
            "limit": limit,
            "offset": offset,
            "hasMore": offset + len(items) < total_count,
            "coverage": {
                "allocatedAmount": str(allocated_amount),
                "unallocatedAmount": str(residual_total),
                "reconciled": True,
                "consolidatesDescendants": cost_center_scope is not None,
                "costCenterScopeCount": cost_center_scope_count,
            },
            "generatedAt": datetime.now(UTC).isoformat(),
        }

    async def allocation_closure(
        self, workspace_id: UUID, *, finance_entry_id: UUID
    ) -> dict[str, Any]:
        entry = await self._tenant_object(FinanceEntry, finance_entry_id, workspace_id)
        allocated_percentage, allocated_amount = (
            await self.db.execute(
                select(
                    func.coalesce(func.sum(FinanceAllocation.percentage), 0),
                    func.coalesce(func.sum(FinanceAllocation.amount), 0),
                ).where(
                    FinanceAllocation.workspace_id == workspace_id,
                    FinanceAllocation.finance_entry_id == finance_entry_id,
                )
            )
        ).one()
        allocated_percentage = Decimal(str(allocated_percentage or 0)).quantize(Decimal("0.0001"))
        allocated_amount = Decimal(str(allocated_amount or 0)).quantize(Decimal("0.01"))
        entry_amount = Decimal(str(entry.amount or 0)).quantize(Decimal("0.01"))
        return {
            "financeEntryId": str(finance_entry_id),
            "allocatedPercentage": str(allocated_percentage),
            "unallocatedPercentage": str(
                (Decimal("100.0000") - allocated_percentage).quantize(Decimal("0.0001"))
            ),
            "allocatedAmount": str(allocated_amount),
            "unallocatedAmount": str((entry_amount - allocated_amount).quantize(Decimal("0.01"))),
            "closed": allocated_percentage == Decimal("100.0000")
            and allocated_amount == entry_amount,
        }

    async def dre(
        self,
        workspace_id: UUID,
        *,
        mode: str,
        period_start,
        period_end,
        cost_center_id: UUID | None = None,
        client_id: UUID | None = None,
        contract_id: UUID | None = None,
        matter_id: UUID | None = None,
        granularity: str | None = None,
        accumulated: bool = False,
        compare_previous: bool = False,
    ) -> dict[str, Any]:
        """Return a reproducible managerial DRE in cash or accrual mode."""
        if mode not in {"cash", "accrual"}:
            raise FinanceDimensionValidationError("Modo de DRE inválido")
        date_column = (
            FinanceEntry.paid_at
            if mode == "cash"
            else func.coalesce(FinanceEntry.due_date, FinanceEntry.created_at)
        )
        query = select(
            FinanceEntry.entry_type,
            func.coalesce(func.sum(FinanceAllocation.amount), 0),
        ).join(FinanceAllocation, FinanceAllocation.finance_entry_id == FinanceEntry.id)
        if mode == "cash":
            query = query.where(FinanceEntry.status == "paid")
        else:
            query = query.where(FinanceEntry.status != "cancelled")
        query = query.where(
            FinanceEntry.workspace_id == workspace_id,
            func.date(date_column) >= period_start,
            func.date(date_column) <= period_end,
        )
        cost_center_scope = (
            await self._cost_center_scope(workspace_id, cost_center_id)
            if cost_center_id is not None
            else None
        )
        if cost_center_scope is not None:
            query = query.where(
                FinanceAllocation.cost_center_id.in_(select(cost_center_scope.c.id))
            )
        for column, value in (
            (FinanceAllocation.client_id, client_id),
            (FinanceAllocation.contract_id, contract_id),
            (FinanceAllocation.matter_id, matter_id),
        ):
            if value is not None:
                query = query.where(column == value)
        rows = (await self.db.execute(query.group_by(FinanceEntry.entry_type))).all()
        values = {
            str(entry_type.value if hasattr(entry_type, "value") else entry_type): Decimal(
                str(amount or 0)
            )
            for entry_type, amount in rows
        }
        # The consolidated DRE is sourced from the canonical entry amounts.  This
        # includes both wholly unallocated entries and the residual of partially
        # allocated entries exactly once.  Dimension-specific views continue to use
        # allocations because an unattributed residual cannot truthfully belong to a
        # selected center/client/contract/case.
        if not any((cost_center_id, client_id, contract_id, matter_id)):
            entry_query = select(
                FinanceEntry.entry_type,
                func.coalesce(func.sum(FinanceEntry.amount), 0),
            ).where(
                FinanceEntry.workspace_id == workspace_id,
                func.date(date_column) >= period_start,
                func.date(date_column) <= period_end,
            )
            if mode == "cash":
                entry_query = entry_query.where(FinanceEntry.status == "paid")
            else:
                entry_query = entry_query.where(FinanceEntry.status != "cancelled")
            entry_rows = (
                await self.db.execute(entry_query.group_by(FinanceEntry.entry_type))
            ).all()
            values = {
                str(entry_type.value if hasattr(entry_type, "value") else entry_type): Decimal(
                    str(amount or 0)
                )
                for entry_type, amount in entry_rows
            }
        revenue = values.get("receivable", Decimal("0"))
        refunds = values.get("refund", Decimal("0"))
        expenses = values.get("expense", Decimal("0"))
        net_revenue = revenue - refunds
        result = net_revenue - expenses
        budget_rows = (
            await self.db.execute(
                select(
                    FinanceBudgetLine.entry_type,
                    func.coalesce(func.sum(FinanceBudgetLine.amount), 0),
                )
                .where(
                    FinanceBudgetLine.workspace_id == workspace_id,
                    FinanceBudgetLine.status == "active",
                    FinanceBudgetLine.period_end >= period_start,
                    FinanceBudgetLine.period_start <= period_end,
                    *(
                        [FinanceBudgetLine.cost_center_id.in_(select(cost_center_scope.c.id))]
                        if cost_center_scope is not None
                        else []
                    ),
                )
                .group_by(FinanceBudgetLine.entry_type)
            )
        ).all()
        budget_values = {str(kind): Decimal(str(amount or 0)) for kind, amount in budget_rows}
        budget_revenue = budget_values.get("receivable", Decimal("0")) - budget_values.get(
            "refund", Decimal("0")
        )
        budget_expenses = budget_values.get("expense", Decimal("0"))
        budget_result = budget_revenue - budget_expenses
        response = {
            "mode": mode,
            "periodStart": period_start.isoformat(),
            "periodEnd": period_end.isoformat(),
            "lines": [
                {"key": "gross_revenue", "label": "Receita bruta", "amount": str(revenue)},
                {"key": "refunds", "label": "Deduções e estornos", "amount": str(-refunds)},
                {"key": "net_revenue", "label": "Receita líquida", "amount": str(net_revenue)},
                {"key": "expenses", "label": "Despesas", "amount": str(-expenses)},
                {"key": "result", "label": "Resultado gerencial", "amount": str(result)},
            ],
            "result": str(result),
            "budget": (
                {
                    "revenue": str(budget_revenue),
                    "expenses": str(budget_expenses),
                    "result": str(budget_result),
                    "variance": str(result - budget_result),
                }
                if budget_rows
                else None
            ),
            "budgetQuality": (
                "not_comparable"
                if any((client_id, contract_id, matter_id))
                else ("configured" if budget_rows else "not_configured")
            ),
            "source": "FinanceEntry + FinanceAllocation",
            "generatedAt": datetime.now(UTC).isoformat(),
        }
        if granularity is not None:
            if granularity not in {"monthly", "annual"}:
                raise FinanceDimensionValidationError("Granularidade da DRE inválida")
            buckets: list[tuple[date, date]] = []
            cursor = period_start
            while cursor <= period_end:
                if granularity == "monthly":
                    bucket_end = date(
                        cursor.year, cursor.month, monthrange(cursor.year, cursor.month)[1]
                    )
                    next_cursor = (
                        date(cursor.year + 1, 1, 1)
                        if cursor.month == 12
                        else date(cursor.year, cursor.month + 1, 1)
                    )
                else:
                    bucket_end = date(cursor.year, 12, 31)
                    next_cursor = date(cursor.year + 1, 1, 1)
                buckets.append((cursor, min(bucket_end, period_end)))
                cursor = next_cursor
            running = Decimal("0")
            series = []
            for bucket_start, bucket_end in buckets:
                bucket = await self.dre(
                    workspace_id,
                    mode=mode,
                    period_start=bucket_start,
                    period_end=bucket_end,
                    cost_center_id=cost_center_id,
                    client_id=client_id,
                    contract_id=contract_id,
                    matter_id=matter_id,
                )
                bucket_result = Decimal(str(bucket["result"]))
                running += bucket_result
                series.append(
                    {
                        "periodStart": bucket_start.isoformat(),
                        "periodEnd": bucket_end.isoformat(),
                        "result": str(bucket_result),
                        "accumulatedResult": str(running if accumulated else bucket_result),
                    }
                )
            response["granularity"] = granularity
            response["accumulated"] = accumulated
            response["series"] = series
        if compare_previous:
            duration = (period_end - period_start).days + 1
            previous_end = period_start - timedelta(days=1)
            previous_start = previous_end - timedelta(days=duration - 1)
            previous = await self.dre(
                workspace_id,
                mode=mode,
                period_start=previous_start,
                period_end=previous_end,
                cost_center_id=cost_center_id,
                client_id=client_id,
                contract_id=contract_id,
                matter_id=matter_id,
            )
            previous_result = Decimal(str(previous["result"]))
            variance = result - previous_result
            response["comparison"] = {
                "periodStart": previous_start.isoformat(),
                "periodEnd": previous_end.isoformat(),
                "currentResult": str(result),
                "previousResult": str(previous_result),
                "variance": str(variance),
                "variancePercent": (
                    str(
                        (variance / abs(previous_result) * Decimal("100")).quantize(Decimal("0.01"))
                    )
                    if previous_result != 0
                    else None
                ),
            }
        return response

    async def simulate_tax_reform(
        self,
        workspace_id: UUID,
        data: Any | None = None,
        *,
        as_of: date | None = None,
        horizon_years: int = 8,
        scenarios: list[str] | None = None,
    ) -> dict[str, Any]:
        profile = await self.db.scalar(
            select(FinanceTaxProfile).where(FinanceTaxProfile.workspace_id == workspace_id)
        )
        if data is None:
            current_rate = Decimal(str(profile.tax_rate if profile else 0))
            revenue = Decimal("0")
            expense = Decimal("0")
            payload = {"transition_rates": {}}
        else:
            payload = data.model_dump()
            current_rate = Decimal(
                str(payload.get("current_effective_rate") or (profile.tax_rate if profile else 0))
            )
            revenue = Decimal(str(payload["annual_revenue"]))
            expense = Decimal(str(payload["annual_expense"]))
        current_tax = (revenue * current_rate / Decimal("100")).quantize(Decimal("0.01"))
        transition = []
        start_year = max(2026, (as_of or datetime.now(UTC).date()).year)
        for year in range(start_year, min(2034, start_year + horizon_years)):
            rate = Decimal(str(payload["transition_rates"].get(str(year), current_rate)))
            tax = (revenue * rate / Decimal("100")).quantize(Decimal("0.01"))
            margin = revenue - expense - tax
            transition.append(
                {
                    "year": year,
                    "rate": str(rate),
                    "estimatedTax": str(tax),
                    "taxDelta": str(tax - current_tax),
                    "estimatedMargin": str(margin),
                    "estimatedCashImpact": str(-(tax - current_tax)),
                }
            )
        deltas = [Decimal(item["taxDelta"]) for item in transition]
        pass_through = Decimal(
            str(
                payload.get(
                    "price_pass_through_percent",
                    (profile.settings if profile else {}).get("price_pass_through_percent", 0),
                )
            )
        )
        max_delta = max(deltas, default=Decimal("0"))
        price_impact = (max_delta * pass_through / Decimal("100")).quantize(Decimal("0.01"))
        return {
            "regime": profile.regime if profile else "not_configured",
            "currentRate": str(current_rate),
            "currentEstimatedTax": str(current_tax),
            "transition": transition,
            "quality": "simulation_requires_accountant_validation",
            "humanReviewRequired": True,
            "requiresHuman": True,
            "humanRole": "contador",
            "impact": {
                "priceImpact": str(price_impact),
                "marginImpact": transition[-1]["estimatedMargin"] if transition else "0",
                "cashImpact": transition[-1]["estimatedCashImpact"] if transition else "0",
            },
            "alerts": [
                {"code": marker, "ownerMustBeNotified": True}
                for marker in ["D-180", "D-90", "D-30", "D-7"]
            ],
            "ownerAlerts": ["D-180", "D-90", "D-30", "D-7"],
            "sources": profile.sources if profile else [],
            "generatedAt": datetime.now(UTC).isoformat(),
        }
