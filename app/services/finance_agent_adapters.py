"""Native, read-only finance flow-node adapters.

Adapters delegate to the canonical finance services; they never post payments,
settle entries, or invoke external providers.
"""
# Compact adapter contracts intentionally keep several schema payloads on one
# line; the surrounding registry uses the same convention.
# ruff: noqa: E501
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from app.services.agent_node_executor import (
    AgentExecutionContext,
    AgentNodeOutput,
    agent_node_executors,
)

FINANCE_READ = "finance:view"
FINANCE_MANAGE = "finance:manage"


def _denied(ctx: AgentExecutionContext, required: str) -> AgentNodeOutput | None:
    if required not in ctx.permissions and "platform:root" not in ctx.permissions:
        return AgentNodeOutput(status="denied", data={"reason": "permission_denied", "requiredPermission": required})
    return None


def _serialize(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    if hasattr(value, "id") and hasattr(value, "name"):
        return {"id": str(value.id), "label": value.name}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


async def _execute(ctx: AgentExecutionContext, payload: dict[str, Any], node_type: str) -> AgentNodeOutput:
    required = (
        FINANCE_MANAGE
        if node_type in {"finance_allocation_suggest", "finance_reconcile"}
        else FINANCE_READ
    )
    denied = _denied(ctx, required)
    if denied:
        return denied
    if ctx.db is None:
        return AgentNodeOutput(status="waiting_data", data={"reason": "finance_database_unavailable"}, requires_human=True)
    from app.services.finance_dimensional_service import FinanceDimensionalService
    from app.services.finance_executive_service import FinanceExecutiveService

    dimensional = FinanceDimensionalService(ctx.db)
    executive = FinanceExecutiveService(ctx.db)
    as_of = date.fromisoformat(str(payload.get("asOf"))) if payload.get("asOf") else datetime.now(UTC).date()
    if node_type == "finance_ledger_query":
        result = await dimensional.drilldown(ctx.workspace_id, limit=min(int(payload.get("limit", 100)), 200), offset=max(int(payload.get("offset", 0)), 0))
    elif node_type == "finance_cost_center_query":
        result = await dimensional.list_cost_centers(ctx.workspace_id)
    elif node_type in {"finance_forecast", "finance_valuation", "finance_insight", "finance_anomaly"}:
        analytics = await executive.analytics(ctx.workspace_id, as_of=as_of)
        result = {"analytics": analytics, "node": node_type}
    elif node_type == "finance_allocation_suggest":
        ledger = await dimensional.drilldown(ctx.workspace_id, limit=50, offset=0)
        result = {
            "status": "draft",
            "requiresHuman": True,
            "reason": "allocation_suggestion_requires_review",
            "suggestions": [
                {"entryId": item.get("entryId"), "currentCostCenter": item.get("costCenterLabel"), "proposed": "Revisar classificação antes de aplicar"}
                for item in ledger.get("items", [])
                if item.get("source") == "unallocated_residual"
            ],
        }
    elif node_type == "finance_reconcile":
        ledger = await dimensional.drilldown(ctx.workspace_id, limit=50, offset=0)
        changes = []
        for item in ledger.get("items", []):
            if item.get("entryId"):
                closure = await dimensional.allocation_closure(
                    ctx.workspace_id, finance_entry_id=UUID(str(item["entryId"]))
                )
                if not closure.get("closed"):
                    changes.append(closure)
        result = {
            "status": "draft",
            "requiresHuman": True,
            "reason": "reconciliation_requires_review",
            "changes": changes,
        }
    else:
        return AgentNodeOutput(status="failed", data={"reason": "unsupported_finance_node"})
    serialized = _serialize(result)
    if isinstance(serialized, dict) and serialized.get("analytics", {}).get("contribution_margin", {}).get("quality") == "insufficient_data":
        return AgentNodeOutput(status="waiting_human", data={"reason": "insufficient_finance_data", "result": serialized}, requires_human=True)
    return AgentNodeOutput(status="completed", data={"node": node_type, "result": serialized}, idempotency_key=ctx.correlation_id)


def register_finance_adapters() -> None:
    for node_type in (
        "finance_ledger_query", "finance_cost_center_query", "finance_forecast",
        "finance_valuation", "finance_insight", "finance_anomaly",
        "finance_allocation_suggest", "finance_reconcile",
    ):
        if not agent_node_executors.has(node_type):
            async def adapter(ctx, payload, _node_type=node_type):
                return await _execute(ctx, payload, _node_type)
            agent_node_executors.register(node_type, adapter)


register_finance_adapters()
