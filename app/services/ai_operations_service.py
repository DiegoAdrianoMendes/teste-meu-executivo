"""Leitura agregada da telemetria do gateway de IA (ai_gateway_events).

Alimenta o painel "Operação de IA" do super admin e o bloco de operação do
workspace. Só expõe metadados agregados (custo, rota, fallback, latência) —
nunca prompt, resposta ou credencial, que sequer são persistidos.

`workspace_id=None` habilita a visão da plataforma (todos os workspaces);
`workspace_id` definido escopa ao próprio workspace. O filtro é sempre
explícito na cláusula WHERE, além da proteção RLS da tabela.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Integer, Select, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_gateway_event import AiGatewayEvent


def _int_sum(column) -> object:
    """Soma portável de coluna booleana (SQLite e Postgres) como inteiro."""
    return func.coalesce(func.sum(cast(column, Integer)), 0)

_ZERO = Decimal("0")
_MAX_WINDOW_DAYS = 90
_ROUTE_ALERT_WINDOW_HOURS = 24
_SUCCESS_OUTCOMES = frozenset({"success", "ok", "completed", "cache_hit"})


def _clamp_days(days: int) -> int:
    if days < 1:
        return 1
    return min(days, _MAX_WINDOW_DAYS)


def _cost(value: object) -> Decimal:
    if value is None:
        return _ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


class AiOperationsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _scope(self, stmt: Select, workspace_id: UUID | None, since: datetime) -> Select:
        conditions = [AiGatewayEvent.created_at >= since]
        if workspace_id is not None:
            conditions.append(AiGatewayEvent.workspace_id == workspace_id)
        return stmt.where(and_(*conditions))

    async def summary(
        self,
        *,
        workspace_id: UUID | None,
        days: int = 7,
    ) -> dict:
        window = _clamp_days(days)
        now = datetime.now(UTC)
        since = now - timedelta(days=window)

        cost_col = func.coalesce(AiGatewayEvent.cost_usd, AiGatewayEvent.estimated_cost_usd)

        totals_row = (
            await self.db.execute(
                self._scope(
                    select(
                        func.count().label("executions"),
                        func.coalesce(func.sum(cost_col), _ZERO).label("cost_usd"),
                        _int_sum(AiGatewayEvent.fallback_used).label("fallback_count"),
                        _int_sum(AiGatewayEvent.billing_unknown).label("billing_unknown"),
                    ),
                    workspace_id,
                    since,
                )
            )
        ).one()

        executions = int(totals_row.executions or 0)
        fallback_count = int(totals_row.fallback_count or 0)

        by_class_provider = [
            {
                "task_class": r.task_class or "",
                "provider": r.provider or "",
                "executions": int(r.executions or 0),
                "fallback_count": int(r.fallback_count or 0),
                "cost_usd": _cost(r.cost_usd),
                "billing_unknown": int(r.billing_unknown or 0),
            }
            for r in (
                await self.db.execute(
                    self._scope(
                        select(
                            AiGatewayEvent.task_class,
                            AiGatewayEvent.provider,
                            func.count().label("executions"),
                            _int_sum(AiGatewayEvent.fallback_used).label("fallback_count"),
                            func.coalesce(func.sum(cost_col), _ZERO).label("cost_usd"),
                            _int_sum(AiGatewayEvent.billing_unknown).label("billing_unknown"),
                        ),
                        workspace_id,
                        since,
                    )
                    .group_by(AiGatewayEvent.task_class, AiGatewayEvent.provider)
                    .order_by(func.count().desc())
                )
            ).all()
        ]

        by_agent = [
            {
                "agent_name": r.agent_name or "",
                "executions": int(r.executions or 0),
                "cost_usd": _cost(r.cost_usd),
            }
            for r in (
                await self.db.execute(
                    self._scope(
                        select(
                            AiGatewayEvent.agent_name,
                            func.count().label("executions"),
                            func.coalesce(func.sum(cost_col), _ZERO).label("cost_usd"),
                        ),
                        workspace_id,
                        since,
                    )
                    .group_by(AiGatewayEvent.agent_name)
                    .order_by(func.count().desc())
                )
            ).all()
        ]

        day_col = func.date(AiGatewayEvent.created_at)
        by_day = [
            {
                "day": str(r.day),
                "executions": int(r.executions or 0),
                "cost_usd": _cost(r.cost_usd),
                "fallback_count": int(r.fallback_count or 0),
            }
            for r in (
                await self.db.execute(
                    self._scope(
                        select(
                            day_col.label("day"),
                            func.count().label("executions"),
                            func.coalesce(func.sum(cost_col), _ZERO).label("cost_usd"),
                            _int_sum(AiGatewayEvent.fallback_used).label("fallback_count"),
                        ),
                        workspace_id,
                        since,
                    )
                    .group_by(day_col)
                    .order_by(day_col.asc())
                )
            ).all()
        ]

        by_workspace: list[dict] = []
        if workspace_id is None:
            by_workspace = [
                {
                    "workspace_id": r.workspace_id,
                    "executions": int(r.executions or 0),
                    "cost_usd": _cost(r.cost_usd),
                    "fallback_count": int(r.fallback_count or 0),
                }
                for r in (
                    await self.db.execute(
                        self._scope(
                            select(
                                AiGatewayEvent.workspace_id,
                                func.count().label("executions"),
                                func.coalesce(func.sum(cost_col), _ZERO).label("cost_usd"),
                                _int_sum(AiGatewayEvent.fallback_used).label("fallback_count"),
                            ),
                            workspace_id,
                            since,
                        )
                        .group_by(AiGatewayEvent.workspace_id)
                        .order_by(func.coalesce(func.sum(cost_col), _ZERO).desc())
                    )
                ).all()
            ]

        # Alertas de rota: falhas recentes (proxy durável do circuit breaker).
        alert_since = now - timedelta(hours=_ROUTE_ALERT_WINDOW_HOURS)
        route_alerts = [
            {
                "route_key": r.route_key or "",
                "provider": r.provider or "",
                "model": r.model or "",
                "failures": int(r.failures or 0),
                "last_failure_at": r.last_failure_at,
            }
            for r in (
                await self.db.execute(
                    select(
                        AiGatewayEvent.route_key,
                        AiGatewayEvent.provider,
                        AiGatewayEvent.model,
                        func.count().label("failures"),
                        func.max(AiGatewayEvent.created_at).label("last_failure_at"),
                    )
                    .where(
                        and_(
                            AiGatewayEvent.created_at >= alert_since,
                            AiGatewayEvent.outcome.notin_(tuple(_SUCCESS_OUTCOMES)),
                            *(
                                (AiGatewayEvent.workspace_id == workspace_id,)
                                if workspace_id is not None
                                else ()
                            ),
                        )
                    )
                    .group_by(
                        AiGatewayEvent.route_key,
                        AiGatewayEvent.provider,
                        AiGatewayEvent.model,
                    )
                    .order_by(func.count().desc())
                    .limit(20)
                )
            ).all()
        ]

        fallback_rate = round(fallback_count / executions, 4) if executions else 0.0

        return {
            "generated_at": now,
            "window_days": window,
            "executions": executions,
            "cost_usd": _cost(totals_row.cost_usd),
            "fallback_count": fallback_count,
            "fallback_rate": fallback_rate,
            "billing_unknown": int(totals_row.billing_unknown or 0),
            "by_class_provider": by_class_provider,
            "by_agent": by_agent,
            "by_day": by_day,
            "by_workspace": by_workspace,
            "route_alerts": route_alerts,
        }

    async def events(
        self,
        *,
        workspace_id: UUID | None,
        days: int = 7,
        limit: int = 50,
        offset: int = 0,
        only_fallbacks: bool = False,
        agent_name: str | None = None,
        provider: str | None = None,
    ) -> dict:
        window = _clamp_days(days)
        since = datetime.now(UTC) - timedelta(days=window)
        limit = max(1, min(limit, 200))
        offset = max(0, offset)

        filters = [AiGatewayEvent.created_at >= since]
        if workspace_id is not None:
            filters.append(AiGatewayEvent.workspace_id == workspace_id)
        if only_fallbacks:
            filters.append(AiGatewayEvent.fallback_used.is_(True))
        if agent_name:
            filters.append(AiGatewayEvent.agent_name == agent_name)
        if provider:
            filters.append(AiGatewayEvent.provider == provider)

        total = (
            await self.db.execute(
                select(func.count()).select_from(AiGatewayEvent).where(and_(*filters))
            )
        ).scalar_one()

        rows = (
            await self.db.execute(
                select(AiGatewayEvent)
                .where(and_(*filters))
                .order_by(AiGatewayEvent.created_at.desc(), AiGatewayEvent.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()

        return {
            "total": int(total or 0),
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(rows) < int(total or 0),
            "items": list(rows),
        }
