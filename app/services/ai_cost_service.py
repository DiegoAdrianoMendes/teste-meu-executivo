"""Tenant-aware AI cost aggregation and editable token-budget policy."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.klm_runtime import get_runtime_status
from app.db.tenant import set_workspace_context
from app.models.billing import PlatformTokenUsage, PlatformTokenUsageByTask, TokenBudget
from app.models.entitlement import EntitlementKey, WorkspaceMonthlyUsage
from app.models.workspace import Workspace
from app.schemas.ai_costs import (
    AiCostAlertLevel,
    AiCostModelBreakdownRead,
    AiCostRuntimeGatesRead,
    AiCostTaskBreakdownRead,
    AiQuotaState,
    WorkspaceAiCostPolicyUpdate,
    WorkspaceAiCostSummaryRead,
)
from app.services.entitlement_service import (
    EntitlementLimitSource,
    EntitlementNotConfiguredError,
    EntitlementResolver,
)
from app.services.klm_operations_service import KlmOperationsService

DEFAULT_ALERT_AT_PCT = 80
CRITICAL_ALERT_AT_PCT = 95


class AiCostWorkspaceNotFoundError(LookupError):
    """Raised when a platform request targets a missing workspace."""


@dataclass(frozen=True, slots=True)
class _EffectivePolicy:
    monthly_limit: int | None
    alert_at_pct: int
    hard_limit: bool
    effective_monthly_limit: int | None
    effective_hard_limit: bool
    limit_source: str | None
    quota_state: AiQuotaState
    ledger_tokens: int
    budget: TokenBudget | None


class AiCostService:
    """Read measured usage and mutate only the local workspace token policy."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.entitlements = EntitlementResolver(db)

    async def get_summary(
        self,
        *,
        workspace_id: UUID | None,
        days: int | None = None,
    ) -> WorkspaceAiCostSummaryRead:
        if workspace_id is not None:
            await self._require_workspace(workspace_id)

        now = datetime.now(UTC)
        period_start, period_end = self._period_bounds(now, days=days)
        filters = [
            PlatformTokenUsage.usage_date >= period_start,
            PlatformTokenUsage.usage_date < period_end,
        ]
        if workspace_id is not None:
            filters.append(PlatformTokenUsage.workspace_id == workspace_id)

        totals = (
            await self.db.execute(
                select(
                    func.sum(PlatformTokenUsage.total_runs).label("runs"),
                    func.sum(PlatformTokenUsage.total_input_tokens).label("input_tokens"),
                    func.sum(PlatformTokenUsage.total_output_tokens).label("output_tokens"),
                    func.sum(PlatformTokenUsage.total_cost_usd).label("cost_usd"),
                ).where(*filters)
            )
        ).one()
        breakdown_rows = (
            await self.db.execute(
                select(
                    PlatformTokenUsage.model_used.label("model"),
                    PlatformTokenUsage.provider.label("provider"),
                    func.sum(PlatformTokenUsage.total_runs).label("runs"),
                    func.sum(PlatformTokenUsage.total_input_tokens).label("input_tokens"),
                    func.sum(PlatformTokenUsage.total_output_tokens).label("output_tokens"),
                    func.sum(PlatformTokenUsage.total_cost_usd).label("cost_usd"),
                )
                .where(*filters)
                .group_by(PlatformTokenUsage.model_used, PlatformTokenUsage.provider)
                .order_by(
                    func.sum(PlatformTokenUsage.total_cost_usd).desc(),
                    PlatformTokenUsage.provider.asc(),
                    PlatformTokenUsage.model_used.asc(),
                )
            )
        ).all()
        task_filters = [
            PlatformTokenUsageByTask.usage_date >= period_start,
            PlatformTokenUsageByTask.usage_date < period_end,
        ]
        if workspace_id is not None:
            task_filters.append(PlatformTokenUsageByTask.workspace_id == workspace_id)
        task_rows = (
            await self.db.execute(
                select(
                    PlatformTokenUsageByTask.task_class.label("task_class"),
                    func.sum(PlatformTokenUsageByTask.total_runs).label("runs"),
                    func.sum(PlatformTokenUsageByTask.total_input_tokens).label(
                        "input_tokens"
                    ),
                    func.sum(PlatformTokenUsageByTask.total_output_tokens).label(
                        "output_tokens"
                    ),
                    func.sum(PlatformTokenUsageByTask.total_cost_usd).label("cost_usd"),
                )
                .where(*task_filters)
                .group_by(PlatformTokenUsageByTask.task_class)
                .order_by(
                    (
                        func.sum(PlatformTokenUsageByTask.total_input_tokens)
                        + func.sum(PlatformTokenUsageByTask.total_output_tokens)
                    ).desc(),
                    PlatformTokenUsageByTask.task_class.asc(),
                )
            )
        ).all()

        total_runs = int(totals.runs or 0)
        input_tokens = int(totals.input_tokens or 0)
        output_tokens = int(totals.output_tokens or 0)
        total_tokens = input_tokens + output_tokens
        cost_usd = self._cost_float(totals.cost_usd)
        by_model = [
            AiCostModelBreakdownRead(
                model=row.model,
                provider=row.provider,
                runs=int(row.runs or 0),
                input_tokens=int(row.input_tokens or 0),
                output_tokens=int(row.output_tokens or 0),
                cost_usd=self._cost_float(row.cost_usd),
            )
            for row in breakdown_rows
        ]
        by_task = [
            AiCostTaskBreakdownRead(
                task_class=row.task_class,
                runs=int(row.runs or 0),
                input_tokens=int(row.input_tokens or 0),
                output_tokens=int(row.output_tokens or 0),
                cost_usd=self._cost_float(row.cost_usd),
            )
            for row in task_rows
        ]
        gates = self._runtime_gates()

        if workspace_id is None:
            return WorkspaceAiCostSummaryRead(
                workspace_id=None,
                period_start=period_start,
                period_end=period_end,
                total_runs=total_runs,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                cost_usd=cost_usd,
                monthly_limit=None,
                policy_monthly_limit=None,
                alert_at_pct=DEFAULT_ALERT_AT_PCT,
                hard_limit=False,
                used_percent=None,
                remaining_tokens=None,
                alert_level="none",
                quota_state="not_applicable",
                by_model=by_model,
                by_task=by_task,
                gates=gates,
            )

        policy = await self._effective_policy(workspace_id, now=now)
        month_start, month_end = self._month_bounds(now)
        budget_usage = total_tokens
        if period_start == month_start and period_end == month_end:
            budget_usage = max(budget_usage, policy.ledger_tokens)
        used_percent, remaining_tokens = self._budget_progress(
            used_tokens=budget_usage,
            monthly_limit=policy.effective_monthly_limit,
        )
        alert_level = self._alert_level(
            monthly_limit=policy.effective_monthly_limit,
            used_percent=used_percent,
            alert_at_pct=policy.alert_at_pct,
            hard_limit=policy.effective_hard_limit,
        )
        return WorkspaceAiCostSummaryRead(
            workspace_id=workspace_id,
            period_start=period_start,
            period_end=period_end,
            total_runs=total_runs,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            monthly_limit=policy.effective_monthly_limit,
            policy_monthly_limit=policy.monthly_limit,
            alert_at_pct=policy.alert_at_pct,
            hard_limit=policy.effective_hard_limit,
            used_percent=used_percent,
            remaining_tokens=remaining_tokens,
            alert_level=alert_level,
            quota_state=policy.quota_state,
            by_model=by_model,
            by_task=by_task,
            gates=gates,
        )

    async def update_policy(
        self,
        *,
        workspace_id: UUID,
        payload: WorkspaceAiCostPolicyUpdate,
        actor_user_id: UUID,
    ) -> WorkspaceAiCostSummaryRead:
        await self._require_workspace(workspace_id, for_update=True)
        now = datetime.now(UTC)
        before_policy = await self._effective_policy(workspace_id, now=now)
        before = self._audit_snapshot(before_policy)

        budget = before_policy.budget
        if budget is None:
            budget = TokenBudget(
                workspace_id=workspace_id,
                monthly_limit=None,
                alert_at_pct=DEFAULT_ALERT_AT_PCT,
                hard_limit=False,
            )
            self.db.add(budget)

        changes = payload.model_dump(exclude_unset=True)
        for field_name, value in changes.items():
            setattr(budget, field_name, value)
        await self.db.flush()

        after_policy = await self._effective_policy(workspace_id, now=now)
        await self._sync_current_usage_limit(
            workspace_id=workspace_id,
            period_start=date(now.year, now.month, 1),
            policy=after_policy,
        )
        await KlmOperationsService(self.db).record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label="human",
            event_type="ai_token_budget_updated",
            entity_type="token_budget",
            entity_id=budget.id,
            summary="Limite mensal de tokens de IA atualizado.",
            before_data=before,
            after_data=self._audit_snapshot(after_policy),
        )
        await self.db.flush()
        return await self.get_summary(workspace_id=workspace_id)

    async def _effective_policy(
        self,
        workspace_id: UUID,
        *,
        now: datetime,
    ) -> _EffectivePolicy:
        budget = await self.db.scalar(
            select(TokenBudget).where(TokenBudget.workspace_id == workspace_id)
        )
        ledger = await self.db.scalar(
            select(WorkspaceMonthlyUsage).where(
                WorkspaceMonthlyUsage.workspace_id == workspace_id,
                WorkspaceMonthlyUsage.entitlement == EntitlementKey.tokens,
                WorkspaceMonthlyUsage.period_start == date(now.year, now.month, 1),
            )
        )
        ledger_tokens = (
            int(ledger.consumed_quantity) + int(ledger.reserved_quantity)
            if ledger is not None
            else 0
        )
        try:
            resolved = await self.entitlements.resolve(workspace_id, EntitlementKey.tokens)
        except EntitlementNotConfiguredError:
            legacy_limit = budget.monthly_limit if budget is not None else None
            return _EffectivePolicy(
                monthly_limit=budget.monthly_limit if budget is not None else None,
                alert_at_pct=(
                    int(budget.alert_at_pct) if budget is not None else DEFAULT_ALERT_AT_PCT
                ),
                hard_limit=bool(budget.hard_limit) if budget is not None else False,
                effective_monthly_limit=legacy_limit,
                effective_hard_limit=(
                    bool(budget.hard_limit) if budget is not None else False
                ),
                limit_source=(
                    EntitlementLimitSource.token_budget.value if budget is not None else None
                ),
                quota_state=("local_legacy" if legacy_limit is not None else "missing"),
                ledger_tokens=ledger_tokens,
                budget=budget,
            )
        quota_state: AiQuotaState = (
            "configured" if resolved.limit_quantity is not None else "missing"
        )
        return _EffectivePolicy(
            monthly_limit=budget.monthly_limit if budget is not None else None,
            alert_at_pct=(
                int(budget.alert_at_pct) if budget is not None else DEFAULT_ALERT_AT_PCT
            ),
            hard_limit=bool(budget.hard_limit) if budget is not None else False,
            effective_monthly_limit=resolved.limit_quantity,
            effective_hard_limit=resolved.hard_limit,
            limit_source=resolved.source.value,
            quota_state=quota_state,
            ledger_tokens=ledger_tokens,
            budget=budget,
        )

    async def _sync_current_usage_limit(
        self,
        *,
        workspace_id: UUID,
        period_start: date,
        policy: _EffectivePolicy,
    ) -> None:
        await self.db.execute(
            update(WorkspaceMonthlyUsage)
            .where(
                WorkspaceMonthlyUsage.workspace_id == workspace_id,
                WorkspaceMonthlyUsage.entitlement == EntitlementKey.tokens,
                WorkspaceMonthlyUsage.period_start == period_start,
            )
            .values(
                limit_quantity=policy.effective_monthly_limit,
                hard_limit=policy.effective_hard_limit,
                limit_source=policy.limit_source,
            )
            .execution_options(synchronize_session=False)
        )

    async def _require_workspace(
        self,
        workspace_id: UUID,
        *,
        for_update: bool = False,
    ) -> None:
        statement = select(Workspace.id).where(
            Workspace.id == workspace_id,
            Workspace.deleted_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        if await self.db.scalar(statement) is None:
            raise AiCostWorkspaceNotFoundError("Workspace não encontrado.")
        await set_workspace_context(self.db, workspace_id)

    @staticmethod
    def _period_bounds(now: datetime, *, days: int | None) -> tuple[datetime, datetime]:
        if days is None:
            return AiCostService._month_bounds(now)
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
            raise ValueError("days deve estar entre 1 e 365.")
        period_end = datetime(now.year, now.month, now.day, tzinfo=UTC) + timedelta(days=1)
        return period_end - timedelta(days=days), period_end

    @staticmethod
    def _month_bounds(now: datetime) -> tuple[datetime, datetime]:
        period_start = datetime(now.year, now.month, 1, tzinfo=UTC)
        if now.month == 12:
            period_end = datetime(now.year + 1, 1, 1, tzinfo=UTC)
        else:
            period_end = datetime(now.year, now.month + 1, 1, tzinfo=UTC)
        return period_start, period_end

    @staticmethod
    def _budget_progress(
        *,
        used_tokens: int,
        monthly_limit: int | None,
    ) -> tuple[float | None, int | None]:
        if monthly_limit is None:
            return None, None
        if monthly_limit == 0:
            return 100.0, 0
        return round(used_tokens / monthly_limit * 100, 2), max(monthly_limit - used_tokens, 0)

    @staticmethod
    def _alert_level(
        *,
        monthly_limit: int | None,
        used_percent: float | None,
        alert_at_pct: int,
        hard_limit: bool,
    ) -> AiCostAlertLevel:
        if monthly_limit is None or used_percent is None:
            return "none"
        if used_percent >= 100:
            return "blocked" if hard_limit else "critical"
        if used_percent >= max(CRITICAL_ALERT_AT_PCT, alert_at_pct):
            return "critical"
        if used_percent >= alert_at_pct:
            return "warning"
        return "ok"

    @staticmethod
    def _runtime_gates() -> AiCostRuntimeGatesRead:
        runtime = get_runtime_status()
        return AiCostRuntimeGatesRead(
            model_apis_enabled=bool(runtime["modelApisEnabled"]),
            online_approved=bool(runtime["onlineApproved"]),
            external_actions_enabled=bool(runtime["externalActionsEnabled"]),
        )

    @staticmethod
    def _cost_float(value: Decimal | int | float | None) -> float:
        return round(float(value or 0), 6)

    @staticmethod
    def _audit_snapshot(policy: _EffectivePolicy) -> dict[str, object]:
        budget = policy.budget
        return {
            "monthlyLimit": budget.monthly_limit if budget is not None else None,
            "alertAtPct": (
                int(budget.alert_at_pct) if budget is not None else DEFAULT_ALERT_AT_PCT
            ),
            "hardLimit": bool(budget.hard_limit) if budget is not None else False,
            "effectiveMonthlyLimit": policy.effective_monthly_limit,
            "effectiveHardLimit": policy.effective_hard_limit,
            "limitSource": policy.limit_source,
            "quotaState": policy.quota_state,
        }


__all__ = ["AiCostService", "AiCostWorkspaceNotFoundError"]
