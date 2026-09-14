"""Blackbox billing reads from canonical contracts without double counting.

``WorkspaceSubscription`` remains an unreconciled legacy source and a
compatibility projection. Once a workspace has a canonical commercial
contract, every legacy mutation is blocked and the Account 360 commercial
workflow is the only write boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import PlatformTokenUsage
from app.models.commercial_catalog import (
    CommercialCatalogEvent,
    WorkspaceCommercialContract,
)
from app.models.subscription_commercial import (
    SubscriptionEvent,
    SubscriptionEventType,
    WorkspacePlanCode,
    WorkspaceSubscription,
    WorkspaceSubscriptionStatus,
)
from app.models.user import User
from app.services.commercial_catalog_service import CommercialCatalogNotFoundError
from app.services.commercial_legacy_bridge_service import (
    CanonicalContractCommand,
    CanonicalRevenueReadModel,
    CommercialBridgeError,
    CommercialRevenueRecord,
    canonical_sku,
)

# Canonical V1 boundary prices. Legacy aliases remain accepted only for rows
# that have not yet been reconciled into a commercial contract.
PLAN_PRICE_CENTS: dict[str, int] = {
    "crm_start": 26900,
    "crm_advanced": 67900,
    "crm_evolution": 124900,
    "essencial": 26900,
    "escritorio": 67900,
    "performance": 124900,
    "custom": 0,
}

# Custo de IA médio por plano na cota 100% (R$), usado apenas como piso de
# referência quando não há consumo real medido — docs §3.1.
PLAN_AI_COST_FLOOR_CENTS: dict[str, int] = {
    "crm_start": 9000,
    "crm_advanced": 22000,
    "crm_evolution": 56000,
    "essencial": 9000,
    "escritorio": 22000,
    "performance": 56000,
    "custom": 0,
}

# Estimativa simples de custo WhatsApp/mês por plano (utility, docs §3.2):
# número de conversas IA/mês do plano * fração que gera 1 utility message.
PLAN_WHATSAPP_COST_CENTS: dict[str, int] = {
    "crm_start": 800,
    "crm_advanced": 4000,
    "crm_evolution": 12000,
    "legal_360": 0,
    "essencial": 800,
    "escritorio": 4000,
    "performance": 12000,
    "custom": 0,
}

def _as_aware(value: datetime) -> datetime:
    """SQLite/testes podem devolver datetimes naive; normaliza para UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


ACTIVE_STATUS_VALUES = {"trial", "ativa", "inadimplente"}

LEGACY_PLAN_TO_SKU = {
    "essencial": "crm_start",
    "escritorio": "crm_advanced",
    "performance": "crm_evolution",
    "custom": "legacy_custom",
}


class SubscriptionNotFoundError(Exception):
    pass


class InvalidSubscriptionActionError(Exception):
    pass


class CanonicalCommercialContractConflictError(InvalidSubscriptionActionError):
    """Legacy billing command would diverge from the canonical contract."""


@dataclass
class BillingOverview:
    mrr_cents: int
    arr_cents: int
    mrr_brl: float
    arr_brl: float
    by_plan: list[dict[str, Any]]
    churn_rate_pct: float
    ltv_estimate_brl: float
    active_count: int
    trial_count: int
    at_risk_count: int
    series: list[dict[str, Any]]


class PlatformBillingService:
    """KPIs de receita + gestão auditada de assinaturas para o Super Admin."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Leitura ──────────────────────────────────────────────────────────

    async def get_overview(self, *, period_days: int = 30) -> dict[str, Any]:
        records = await CanonicalRevenueReadModel(self.db).list_records()

        mrr_cents = 0
        by_plan: dict[str, dict[str, Any]] = {}
        active_count = 0
        trial_count = 0
        at_risk_count = 0

        for record in records:
            plan_bucket = by_plan.setdefault(
                record.sku,
                {"plano": record.sku, "workspace_count": 0, "mrr_cents": 0},
            )
            if record.status in ACTIVE_STATUS_VALUES:
                active_count += 1
                mrr = record.mrr_cents
                mrr_cents += mrr
                plan_bucket["workspace_count"] += 1
                plan_bucket["mrr_cents"] += mrr
            if record.status == "trial":
                trial_count += 1
            if record.status == "inadimplente":
                at_risk_count += 1

        breakdown = [
            {
                **item,
                "plano": item["plano"].value if hasattr(item["plano"], "value") else item["plano"],
                "mrr_brl": round(item["mrr_cents"] / 100, 2),
            }
            for item in by_plan.values()
        ]

        churn_rate = self._churn_rate_pct(records, period_days=period_days)
        ltv = self._ltv_estimate_brl(mrr_cents=mrr_cents, active_count=active_count, churn_rate_pct=churn_rate)
        series = self._mrr_series(records, months=6)

        return {
            "mrr_cents": mrr_cents,
            "arr_cents": mrr_cents * 12,
            "mrr_brl": round(mrr_cents / 100, 2),
            "arr_brl": round(mrr_cents * 12 / 100, 2),
            "by_plan": breakdown,
            "churn_rate_pct": churn_rate,
            "ltv_estimate_brl": ltv,
            "active_count": active_count,
            "trial_count": trial_count,
            "at_risk_count": at_risk_count,
            "series": series,
        }

    def _churn_rate_pct(
        self,
        records: list[CommercialRevenueRecord],
        *,
        period_days: int,
    ) -> float:
        since = datetime.now(timezone.utc) - timedelta(days=period_days)
        cancelled = sum(
            1
            for record in records
            if record.status == "cancelada"
            and record.cancelled_at is not None
            and _as_aware(record.cancelled_at) >= since
        )
        active_now = sum(1 for record in records if record.status in ACTIVE_STATUS_VALUES)
        denom = active_now + cancelled
        if denom == 0:
            return 0.0
        return round(cancelled / denom * 100, 2)

    def _ltv_estimate_brl(
        self, *, mrr_cents: int, active_count: int, churn_rate_pct: float
    ) -> float:
        if active_count == 0:
            return 0.0
        arpa_cents = mrr_cents / active_count
        monthly_churn = max(churn_rate_pct / 100, 0.01)  # piso 1% para não dividir por ~0
        ltv_cents = arpa_cents / monthly_churn
        return round(ltv_cents / 100, 2)

    def _mrr_series(
        self,
        records: list[CommercialRevenueRecord],
        *,
        months: int = 6,
    ) -> list[dict[str, Any]]:
        """Série simplificada: MRR atual reprojetado para trás pela data de criação
        das assinaturas ativas (proxy honesto até termos snapshot mensal real)."""
        now = datetime.now(timezone.utc)
        active_records = [
            record for record in records if record.status in ACTIVE_STATUS_VALUES
        ]

        points: list[dict[str, Any]] = []
        for i in range(months - 1, -1, -1):
            month_marker = now - timedelta(days=30 * i)
            mrr_at_month = sum(
                record.mrr_cents
                for record in active_records
                if _as_aware(record.created_at) <= month_marker
            )
            points.append(
                {
                    "month": month_marker.strftime("%Y-%m"),
                    "mrr_cents": mrr_at_month,
                    "mrr_brl": round(mrr_at_month / 100, 2),
                }
            )
        return points

    async def list_subscriptions(
        self,
        *,
        plano: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        records = await CanonicalRevenueReadModel(self.db).list_records()
        normalized_plan = LEGACY_PLAN_TO_SKU.get(plano or "", plano)
        normalized_search = (search or "").strip().casefold()
        filtered = [
            record
            for record in records
            if (normalized_plan is None or record.sku == normalized_plan)
            and (status is None or record.status == status)
            and (
                not normalized_search
                or normalized_search in (record.workspace_name or "").casefold()
            )
        ]
        filtered.sort(
            key=lambda record: (_as_aware(record.updated_at), str(record.id)),
            reverse=True,
        )
        total = len(filtered)
        items = [
            self._serialize_record(record) for record in filtered[offset : offset + limit]
        ]
        return {
            "items": items,
            "limit": limit,
            "offset": offset,
            "count": total,
            "total": total,
            "hasMore": offset + len(items) < total,
        }

    @staticmethod
    def _serialize_record(record: CommercialRevenueRecord) -> dict[str, Any]:
        return {
            "id": str(record.id),
            "workspace_id": str(record.workspace_id),
            "workspace_name": record.workspace_name,
            "plano": record.sku,
            "status": record.status,
            "valor_mensal_cents": record.recurring_price_cents,
            "valor_mensal_brl": round(record.recurring_price_cents / 100, 2),
            "mrr_cents": record.mrr_cents,
            "ciclo": "mensal",
            "desconto_pct": None if record.source == "canonical_contract" else 0.0,
            "cortesia": record.courtesy,
            "trial_ends_at": record.trial_ends_at.isoformat()
            if record.trial_ends_at
            else None,
            "current_period_start": None,
            "current_period_end": None,
            "cancelled_at": record.cancelled_at.isoformat()
            if record.cancelled_at
            else None,
            "updated_at": record.updated_at.isoformat(),
            "commercial_source": record.source,
            "canonical_managed": record.source == "canonical_contract",
            "contract_id": str(record.contract_id) if record.contract_id else None,
            "contract_version": record.contract_version,
        }

    def _serialize(self, sub: WorkspaceSubscription, *, workspace_name: str | None = None) -> dict[str, Any]:
        legacy_plan = sub.plano.value if hasattr(sub.plano, "value") else str(sub.plano)
        return {
            "id": str(sub.id),
            "workspace_id": str(sub.workspace_id),
            "workspace_name": workspace_name,
            "plano": LEGACY_PLAN_TO_SKU.get(legacy_plan, legacy_plan),
            "status": sub.status.value if hasattr(sub.status, "value") else sub.status,
            "valor_mensal_cents": sub.valor_mensal_cents,
            "valor_mensal_brl": round(sub.valor_mensal_cents / 100, 2),
            "mrr_cents": sub.mrr_cents,
            "ciclo": sub.ciclo,
            "desconto_pct": float(sub.desconto_pct),
            "cortesia": sub.cortesia,
            "trial_ends_at": sub.trial_ends_at.isoformat() if sub.trial_ends_at else None,
            "current_period_start": (
                sub.current_period_start.isoformat() if sub.current_period_start else None
            ),
            "current_period_end": (
                sub.current_period_end.isoformat() if sub.current_period_end else None
            ),
            "cancelled_at": sub.cancelled_at.isoformat() if sub.cancelled_at else None,
            "updated_at": sub.updated_at.isoformat() if sub.updated_at else None,
        }

    async def _get_subscription(self, workspace_id: UUID) -> WorkspaceSubscription:
        sub = (
            await self.db.execute(
                select(WorkspaceSubscription).where(
                    WorkspaceSubscription.workspace_id == workspace_id
                )
            )
        ).scalar_one_or_none()
        if sub is None:
            raise SubscriptionNotFoundError(f"Workspace {workspace_id} não tem assinatura.")
        return sub

    async def _canonical_contract_id(self, workspace_id: UUID) -> UUID | None:
        return await self.db.scalar(
            select(WorkspaceCommercialContract.id).where(
                WorkspaceCommercialContract.workspace_id == workspace_id
            )
        )

    async def _reject_canonical_legacy_mutation(self, workspace_id: UUID) -> None:
        if await self._canonical_contract_id(workspace_id) is not None:
            raise CanonicalCommercialContractConflictError(
                "Esta conta possui contrato canônico. Use o Estúdio Comercial da "
                "Conta 360; a assinatura legada é somente uma projeção de leitura."
            )

    async def get_subscription(self, workspace_id: UUID) -> dict[str, Any]:
        """Load canonical state first and an unreconciled legacy row second."""
        try:
            record = await CanonicalRevenueReadModel(self.db).get(workspace_id)
        except CommercialCatalogNotFoundError as error:
            raise SubscriptionNotFoundError(str(error)) from error
        return self._serialize_record(record)

    async def _write_event(
        self,
        sub: WorkspaceSubscription,
        *,
        event_type: SubscriptionEventType,
        actor: User,
        motivo: str,
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> None:
        event = SubscriptionEvent(
            subscription_id=sub.id,
            workspace_id=sub.workspace_id,
            event_type=event_type,
            actor_user_id=actor.id,
            actor_label=actor.name or actor.email or "staff",
            motivo=motivo,
            before_json=before,
            after_json=after,
        )
        self.db.add(event)

    # ── Ações auditadas ──────────────────────────────────────────────────

    async def change_plan(
        self,
        workspace_id: UUID,
        *,
        novo_plano: str,
        actor: User,
        motivo: str,
        valor_mensal_cents: Optional[int] = None,
    ) -> dict[str, Any]:
        if novo_plano not in PLAN_PRICE_CENTS:
            raise InvalidSubscriptionActionError(f"Plano inválido: {novo_plano}")
        contract_id = await self._canonical_contract_id(workspace_id)
        if contract_id is not None:
            if valor_mensal_cents is not None:
                raise InvalidSubscriptionActionError(
                    "Preço negociado deve ser registrado como ajuste comercial, "
                    "separado da troca de plano."
                )
            contract_version = await self.db.scalar(
                select(WorkspaceCommercialContract.version).where(
                    WorkspaceCommercialContract.id == contract_id
                )
            )
            try:
                await CanonicalContractCommand(
                    self.db,
                    control_plane=True,
                ).ensure_core_plan(
                    workspace_id=workspace_id,
                    plan=canonical_sku(novo_plano),
                    actor_user_id=actor.id,
                    idempotency_key=(
                        f"platform-billing-plan:{workspace_id}:{canonical_sku(novo_plano)}:"
                        f"v{contract_version or 0}"
                    ),
                    reason=motivo,
                    project_legacy=True,
                )
            except CommercialBridgeError as error:
                raise CanonicalCommercialContractConflictError(str(error)) from error
            return await self.get_subscription(workspace_id)

        sub = await self._get_subscription(workspace_id)
        before = self._serialize(sub)
        legacy_plan = {
            "crm_start": WorkspacePlanCode.essencial,
            "crm_advanced": WorkspacePlanCode.escritorio,
            "crm_evolution": WorkspacePlanCode.performance,
        }.get(novo_plano)
        if legacy_plan is None:
            legacy_plan = WorkspacePlanCode(novo_plano)
        sub.plano = legacy_plan
        sub.valor_mensal_cents = (
            valor_mensal_cents if valor_mensal_cents is not None else PLAN_PRICE_CENTS[novo_plano]
        )
        if sub.status == WorkspaceSubscriptionStatus.trial:
            pass  # troca de plano durante trial não força ativação
        await self._write_event(
            sub,
            event_type=SubscriptionEventType.plano_alterado,
            actor=actor,
            motivo=motivo,
            before=before,
            after=self._serialize(sub),
        )
        await self.db.flush()
        await self.db.refresh(sub)
        return self._serialize(sub)

    async def apply_discount(
        self,
        workspace_id: UUID,
        *,
        desconto_pct: Decimal,
        cortesia: bool,
        actor: User,
        motivo: str,
    ) -> dict[str, Any]:
        await self._reject_canonical_legacy_mutation(workspace_id)
        if desconto_pct < 0 or desconto_pct > 100:
            raise InvalidSubscriptionActionError("Desconto deve estar entre 0 e 100%.")
        sub = await self._get_subscription(workspace_id)
        before = self._serialize(sub)
        sub.desconto_pct = desconto_pct
        sub.cortesia = cortesia
        await self._write_event(
            sub,
            event_type=SubscriptionEventType.desconto_aplicado,
            actor=actor,
            motivo=motivo,
            before=before,
            after=self._serialize(sub),
        )
        await self.db.flush()
        await self.db.refresh(sub)
        return self._serialize(sub)

    async def extend_trial(
        self,
        workspace_id: UUID,
        *,
        dias: int,
        actor: User,
        motivo: str,
    ) -> dict[str, Any]:
        await self._reject_canonical_legacy_mutation(workspace_id)
        if dias <= 0:
            raise InvalidSubscriptionActionError("Dias deve ser positivo.")
        sub = await self._get_subscription(workspace_id)
        if sub.status != WorkspaceSubscriptionStatus.trial:
            raise InvalidSubscriptionActionError("Só é possível estender trial de assinaturas em trial.")
        before = self._serialize(sub)
        base = sub.trial_ends_at or datetime.now(timezone.utc)
        sub.trial_ends_at = base + timedelta(days=dias)
        await self._write_event(
            sub,
            event_type=SubscriptionEventType.trial_estendido,
            actor=actor,
            motivo=motivo,
            before=before,
            after=self._serialize(sub),
        )
        await self.db.flush()
        await self.db.refresh(sub)
        return self._serialize(sub)

    async def suspend(self, workspace_id: UUID, *, actor: User, motivo: str) -> dict[str, Any]:
        await self._reject_canonical_legacy_mutation(workspace_id)
        sub = await self._get_subscription(workspace_id)
        before = self._serialize(sub)
        sub.status = WorkspaceSubscriptionStatus.pausada
        await self._write_event(
            sub,
            event_type=SubscriptionEventType.suspensa,
            actor=actor,
            motivo=motivo,
            before=before,
            after=self._serialize(sub),
        )
        await self.db.flush()
        await self.db.refresh(sub)
        return self._serialize(sub)

    async def reactivate(self, workspace_id: UUID, *, actor: User, motivo: str) -> dict[str, Any]:
        await self._reject_canonical_legacy_mutation(workspace_id)
        sub = await self._get_subscription(workspace_id)
        if sub.status != WorkspaceSubscriptionStatus.pausada:
            raise InvalidSubscriptionActionError("Só é possível reativar assinaturas pausadas.")
        before = self._serialize(sub)
        sub.status = WorkspaceSubscriptionStatus.ativa
        await self._write_event(
            sub,
            event_type=SubscriptionEventType.reativada,
            actor=actor,
            motivo=motivo,
            before=before,
            after=self._serialize(sub),
        )
        await self.db.flush()
        await self.db.refresh(sub)
        return self._serialize(sub)

    # ── Margem por conta (P&L) ───────────────────────────────────────────

    async def get_workspace_pnl(self, workspace_id: UUID, *, days: int = 30) -> dict[str, Any]:
        try:
            record = await CanonicalRevenueReadModel(self.db).get(workspace_id)
        except CommercialCatalogNotFoundError as error:
            raise SubscriptionNotFoundError(str(error)) from error
        since = datetime.now(timezone.utc) - timedelta(days=days)

        ai_cost_usd = await self.db.scalar(
            select(func.coalesce(func.sum(PlatformTokenUsage.total_cost_usd), 0)).where(
                PlatformTokenUsage.workspace_id == workspace_id,
                PlatformTokenUsage.usage_date >= since,
            )
        )
        ai_cost_cents_brl = int(float(ai_cost_usd or 0) * 550 * 100 / 100)  # câmbio ref R$5,50

        plano_key = record.sku
        whatsapp_cost_cents = PLAN_WHATSAPP_COST_CENTS.get(plano_key, 0)

        receita_cents = record.mrr_cents
        custo_total_cents = ai_cost_cents_brl + whatsapp_cost_cents
        margem_cents = receita_cents - custo_total_cents
        margem_pct = round(margem_cents / receita_cents * 100, 2) if receita_cents else None

        return {
            "workspace_id": str(workspace_id),
            "plano": plano_key,
            "receita_mensal_cents": receita_cents,
            "receita_mensal_brl": round(receita_cents / 100, 2),
            "custo_ia_cents": ai_cost_cents_brl,
            "custo_ia_brl": round(ai_cost_cents_brl / 100, 2),
            "custo_whatsapp_estimado_cents": whatsapp_cost_cents,
            "custo_whatsapp_estimado_brl": round(whatsapp_cost_cents / 100, 2),
            "custo_total_cents": custo_total_cents,
            "custo_total_brl": round(custo_total_cents / 100, 2),
            "margem_cents": margem_cents,
            "margem_brl": round(margem_cents / 100, 2),
            "margem_pct": margem_pct,
            "janela_dias": days,
        }

    @staticmethod
    def _serialize_event(event: SubscriptionEvent) -> dict[str, Any]:
        return {
            "id": str(event.id),
            "event_type": event.event_type.value
            if hasattr(event.event_type, "value")
            else event.event_type,
            "actor_label": event.actor_label,
            "motivo": event.motivo,
            "occurred_at": event.occurred_at,
            "before": event.before_json,
            "after": event.after_json,
        }

    async def page_events(
        self,
        workspace_id: UUID,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        canonical_contract_id = await self._canonical_contract_id(workspace_id)
        if canonical_contract_id is not None:
            where_clause = CommercialCatalogEvent.workspace_id == workspace_id
            total = int(
                await self.db.scalar(
                    select(func.count(CommercialCatalogEvent.id)).where(where_clause)
                )
                or 0
            )
            events = list(
                (
                    await self.db.scalars(
                        select(CommercialCatalogEvent)
                        .where(where_clause)
                        .order_by(
                            CommercialCatalogEvent.occurred_at.desc(),
                            CommercialCatalogEvent.id.desc(),
                        )
                        .offset(offset)
                        .limit(limit)
                    )
                ).all()
            )
            items = [
                {
                    "id": str(event.id),
                    "event_type": event.event_type,
                    "actor_label": None,
                    "motivo": event.reason,
                    "occurred_at": event.occurred_at,
                    "before": event.before,
                    "after": event.after,
                }
                for event in events
            ]
            return {
                "items": items,
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(items) < total,
            }

        sub = await self._get_subscription(workspace_id)
        where_clause = SubscriptionEvent.subscription_id == sub.id
        total = int(
            await self.db.scalar(
                select(func.count(SubscriptionEvent.id)).where(where_clause)
            )
            or 0
        )
        events = (
            await self.db.execute(
                select(SubscriptionEvent)
                .where(where_clause)
                .order_by(
                    SubscriptionEvent.occurred_at.desc(),
                    SubscriptionEvent.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()
        items = [self._serialize_event(event) for event in events]
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(items) < total,
        }

    async def list_events(self, workspace_id: UUID, *, limit: int = 20) -> list[dict[str, Any]]:
        """Compatibilidade temporária; consumidores novos devem usar ``page_events``."""
        page = await self.page_events(workspace_id, limit=limit, offset=0)
        return page["items"]
