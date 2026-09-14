from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.finance_dimensional import FinanceAllocation, FinanceCostCenter
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.agent_node_executor import AgentExecutionContext
from app.services.finance_agent_adapters import _execute
from app.services.flow_node_registry import FLOW_NODE_SPEC_BY_TYPE

# compact contract fixtures
# ruff: noqa: E501


def test_all_finance_nodes_have_native_contracts():
    nodes = {
        "finance_ledger_query",
        "finance_cost_center_query",
        "finance_forecast",
        "finance_valuation",
        "finance_insight",
        "finance_anomaly",
        "finance_allocation_suggest",
        "finance_reconcile",
    }
    assert nodes <= FLOW_NODE_SPEC_BY_TYPE.keys()
    assert all(FLOW_NODE_SPEC_BY_TYPE[node]["availability"] == "available" for node in nodes)
    assert FLOW_NODE_SPEC_BY_TYPE["finance_ledger_query"]["required_permission"] == "finance:view"
    assert FLOW_NODE_SPEC_BY_TYPE["finance_reconcile"]["required_permission"] == "finance:manage"


@pytest.mark.asyncio
async def test_finance_adapter_permission_and_missing_db():
    denied = await _execute(
        AgentExecutionContext(uuid4(), "finance", None, frozenset()), {}, "finance_ledger_query"
    )
    assert denied.status == "denied"
    waiting = await _execute(
        AgentExecutionContext(uuid4(), "finance", None, frozenset({"finance:view"})),
        {},
        "finance_ledger_query",
    )
    assert waiting.status == "waiting_data"


@pytest.mark.asyncio
async def test_finance_draft_human_fallback_without_db():
    result = await _execute(
        AgentExecutionContext(uuid4(), "finance", None, frozenset({"finance:manage"})),
        {},
        "finance_reconcile",
    )
    assert result.status == "waiting_data"
    assert result.requires_human


@pytest.mark.asyncio
async def test_finance_ledger_and_cost_center_success_are_tenant_scoped(db_session, auth_context):
    center = FinanceCostCenter(workspace_id=auth_context.workspace_id, code="ADP", name="Adapters")
    entry = FinanceEntry(
        workspace_id=auth_context.workspace_id,
        title="Receita adapter",
        entry_type=FinanceEntryType.receivable,
        status=FinanceEntryStatus.paid,
        amount=Decimal("250"),
        paid_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    db_session.add_all([center, entry])
    await db_session.flush()
    db_session.add(
        FinanceAllocation(
            workspace_id=auth_context.workspace_id,
            finance_entry_id=entry.id,
            cost_center_id=center.id,
            percentage=Decimal("100"),
            amount=Decimal("250"),
        )
    )
    await db_session.flush()
    foreign = Workspace(
        name="Foreign",
        slug=f"foreign-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(foreign)
    await db_session.flush()
    db_session.add(
        FinanceEntry(
            workspace_id=foreign.id,
            title="Não expor",
            entry_type=FinanceEntryType.receivable,
            status=FinanceEntryStatus.paid,
            amount=Decimal("999"),
        )
    )
    await db_session.flush()
    ctx = AgentExecutionContext(
        auth_context.workspace_id, "finance", None, frozenset({"finance:view"}), db=db_session
    )
    ledger = await _execute(ctx, {"limit": 20}, "finance_ledger_query")
    centers = await _execute(ctx, {}, "finance_cost_center_query")
    assert ledger.status == "completed"
    assert ledger.data["result"]["total"] == "250.00"
    assert all("Não expor" not in str(item) for item in ledger.data["result"]["items"])
    assert any(item["id"] == str(center.id) for item in centers.data["result"])


@pytest.mark.asyncio
async def test_finance_reconcile_proposes_without_mutating_entry(db_session, auth_context):
    center = FinanceCostCenter(workspace_id=auth_context.workspace_id, code="REC", name="Recon")
    entry = FinanceEntry(
        workspace_id=auth_context.workspace_id,
        title="Parcial",
        entry_type=FinanceEntryType.expense,
        status=FinanceEntryStatus.paid,
        amount=Decimal("100"),
        paid_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    db_session.add_all([center, entry])
    await db_session.flush()
    db_session.add(
        FinanceAllocation(
            workspace_id=auth_context.workspace_id,
            finance_entry_id=entry.id,
            cost_center_id=center.id,
            percentage=Decimal("50"),
            amount=Decimal("50"),
        )
    )
    await db_session.flush()
    ctx = AgentExecutionContext(
        auth_context.workspace_id, "finance", None, frozenset({"finance:manage"}), db=db_session
    )
    result = await _execute(ctx, {}, "finance_reconcile")
    assert result.status == "completed"
    assert result.data["result"]["requiresHuman"] is True
    assert result.data["result"]["changes"]
    await db_session.refresh(entry)
    assert entry.amount == Decimal("100")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "node", ["finance_forecast", "finance_valuation", "finance_insight", "finance_anomaly"]
)
async def test_executive_finance_nodes_report_insufficient_data_human_gate(
    db_session, auth_context, node
):
    ctx = AgentExecutionContext(
        auth_context.workspace_id, "finance", None, frozenset({"finance:view"}), db=db_session
    )
    result = await _execute(ctx, {}, node)
    assert result.status == "waiting_human"
    assert result.requires_human


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "node", ["finance_forecast", "finance_valuation", "finance_insight", "finance_anomaly"]
)
async def test_executive_finance_nodes_success_with_classified_seed(db_session, auth_context, node):
    center = FinanceCostCenter(
        workspace_id=auth_context.workspace_id, code="EXEC", name="Executive"
    )
    revenue = FinanceEntry(
        workspace_id=auth_context.workspace_id,
        title="Receita",
        entry_type=FinanceEntryType.receivable,
        status=FinanceEntryStatus.paid,
        amount=Decimal("1000"),
        paid_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    expense = FinanceEntry(
        workspace_id=auth_context.workspace_id,
        title="Custo variável",
        entry_type=FinanceEntryType.expense,
        status=FinanceEntryStatus.paid,
        amount=Decimal("100"),
        category="custas",
        paid_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    db_session.add_all([center, revenue, expense])
    await db_session.flush()
    db_session.add_all(
        [
            FinanceAllocation(
                workspace_id=auth_context.workspace_id,
                finance_entry_id=revenue.id,
                cost_center_id=center.id,
                percentage=Decimal("100"),
                amount=Decimal("1000"),
            ),
            FinanceAllocation(
                workspace_id=auth_context.workspace_id,
                finance_entry_id=expense.id,
                cost_center_id=center.id,
                percentage=Decimal("100"),
                amount=Decimal("100"),
            ),
        ]
    )
    await db_session.flush()
    ctx = AgentExecutionContext(
        auth_context.workspace_id, "finance", None, frozenset({"finance:view"}), db=db_session
    )
    result = await _execute(ctx, {"asOf": "2026-08-02"}, node)
    assert result.status == "completed"
    analytics = result.data["result"]["analytics"]
    assert analytics["period_start"] == "2026-08-01"
    assert analytics["contribution_margin"]["quality"] == "derived"


@pytest.mark.asyncio
async def test_allocation_suggest_returns_concrete_residual_proposal(db_session, auth_context):
    center = FinanceCostCenter(workspace_id=auth_context.workspace_id, code="SUG", name="Suggest")
    entry = FinanceEntry(
        workspace_id=auth_context.workspace_id,
        title="Residual",
        entry_type=FinanceEntryType.expense,
        status=FinanceEntryStatus.paid,
        amount=Decimal("80"),
        paid_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    db_session.add_all([center, entry])
    await db_session.flush()
    db_session.add(
        FinanceAllocation(
            workspace_id=auth_context.workspace_id,
            finance_entry_id=entry.id,
            cost_center_id=center.id,
            percentage=Decimal("50"),
            amount=Decimal("40"),
        )
    )
    await db_session.flush()
    ctx = AgentExecutionContext(
        auth_context.workspace_id, "finance", None, frozenset({"finance:manage"}), db=db_session
    )
    result = await _execute(ctx, {}, "finance_allocation_suggest")
    assert result.status == "completed"
    assert result.data["result"]["requiresHuman"] is True
    assert result.data["result"]["suggestions"]
