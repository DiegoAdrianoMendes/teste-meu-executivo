"""Executable contracts for the Financeiro Executivo 360 release.

The tests in this module describe the public contract from the V1 plan.  Every
case is an ordinary blocking assertion: this module contains no ``skip`` or
``xfail`` escape hatch.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.client import Client
from app.models.contract_billing import ContractBillingProposal
from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.finance_dimensional import (
    FinanceAllocation,
    FinanceCostCenter,
    FinanceExportJob,
)
from app.models.matter import Matter
from app.schemas.finance_dimensional import TaxReformSimulationWrite
from app.services.finance_dimensional_service import (
    FinanceDimensionalService,
    FinanceDimensionValidationError,
)
from app.services.finance_export_service import FinanceExportService
from app.services.finance_reports_service import FinanceReportsService

AS_OF = date(2026, 8, 2)


def _dt(day: int, month: int = 8) -> datetime:
    return datetime(2026, month, day, 12, tzinfo=UTC)


async def _seed_dimensional_data(db_session, workspace_id):
    client = Client(workspace_id=workspace_id, name="Cliente Executivo 360")
    parent = FinanceCostCenter(workspace_id=workspace_id, code="OPS", name="Operação")
    child = FinanceCostCenter(
        workspace_id=workspace_id,
        code="JUR",
        name="Jurídico",
        parent_id=parent.id,
    )
    db_session.add_all([client, parent])
    await db_session.flush()
    child.parent_id = parent.id
    db_session.add(child)
    await db_session.flush()

    entry = FinanceEntry(
        workspace_id=workspace_id,
        title="Honorários Caso 360",
        entry_type=FinanceEntryType.receivable,
        status=FinanceEntryStatus.paid,
        amount=Decimal("1000.00"),
        due_date=_dt(1),
        paid_at=_dt(2),
    )
    db_session.add(entry)
    await db_session.flush()
    return client, parent, child, entry


@pytest.mark.asyncio
async def test_drilldown_preserves_hierarchy_and_server_total(db_session, auth_context):
    """A page must expose aggregated hierarchy, not only the visible rows."""
    client, parent, child, entry = await _seed_dimensional_data(
        db_session, auth_context.workspace_id
    )
    db_session.add_all(
        [
            FinanceAllocation(
                workspace_id=auth_context.workspace_id,
                finance_entry_id=entry.id,
                cost_center_id=child.id,
                client_id=client.id,
                percentage=Decimal("60"),
                amount=Decimal("600.00"),
            ),
            FinanceAllocation(
                workspace_id=auth_context.workspace_id,
                finance_entry_id=entry.id,
                cost_center_id=child.id,
                client_id=client.id,
                percentage=Decimal("40"),
                amount=Decimal("400.00"),
            ),
        ]
    )
    await db_session.flush()

    result = await FinanceDimensionalService(db_session).drilldown(
        auth_context.workspace_id, limit=1, offset=0
    )

    assert result["total"] == "1000.00"
    assert result["totalCount"] == 2
    assert result["hasMore"] is True
    assert result["items"][0]["costCenterLabel"] == "JUR · Jurídico"
    # The approved V1 contract requires aggregate nodes and a server total
    # independent of the current page.  Current candidate returns flat rows.
    assert result["groups"][0]["costCenterId"] == str(child.id)
    assert result["groups"][0]["amount"] == "1000.00"


@pytest.mark.asyncio
async def test_drilldown_exposes_opaque_evidence_reference_only_when_document_exists(
    db_session, auth_context
):
    _, _, child, entry = await _seed_dimensional_data(db_session, auth_context.workspace_id)
    document_id = uuid4()
    entry.receipt_document_ref = document_id
    db_session.add(
        FinanceAllocation(
            workspace_id=auth_context.workspace_id,
            finance_entry_id=entry.id,
            cost_center_id=child.id,
            percentage=Decimal("100"),
            amount=Decimal("1000.00"),
        )
    )
    await db_session.flush()

    result = await FinanceDimensionalService(db_session).drilldown(
        auth_context.workspace_id, limit=10, offset=0
    )
    item = result["items"][0]
    assert item["evidenceAvailable"] is True
    assert item["evidenceRef"]
    assert str(document_id) not in item["evidenceRef"]
    assert FinanceDimensionalService.resolve_evidence_ref(item["evidenceRef"]) == document_id

    with pytest.raises(FinanceDimensionValidationError):
        FinanceDimensionalService.resolve_evidence_ref("not-a-reference")


@pytest.mark.asyncio
async def test_contribution_margin_is_derived_when_all_paid_costs_are_classified(
    db_session, auth_context
):
    from app.services.finance_executive_service import FinanceExecutiveService

    client, _, center, entry = await _seed_dimensional_data(
        db_session, auth_context.workspace_id
    )
    expense = FinanceEntry(
        workspace_id=auth_context.workspace_id,
        title="Custas variáveis",
        entry_type=FinanceEntryType.expense,
        status=FinanceEntryStatus.paid,
        amount=Decimal("200.00"),
        category="custas",
        due_date=_dt(3),
        paid_at=_dt(4),
    )
    db_session.add(expense)
    await db_session.flush()
    db_session.add_all(
        [
            FinanceAllocation(
                workspace_id=auth_context.workspace_id,
                finance_entry_id=entry.id,
                cost_center_id=center.id,
                client_id=client.id,
                percentage=Decimal("100"),
                amount=Decimal("1000.00"),
            ),
            FinanceAllocation(
                workspace_id=auth_context.workspace_id,
                finance_entry_id=expense.id,
                cost_center_id=center.id,
                percentage=Decimal("100"),
                amount=Decimal("200.00"),
            ),
        ]
    )
    await db_session.flush()
    analytics = await FinanceExecutiveService(db_session).analytics(
        auth_context.workspace_id, as_of=AS_OF
    )
    assert analytics.contribution_margin.value == Decimal("80.0")
    assert analytics.contribution_margin.quality == "derived"


@pytest.mark.asyncio
async def test_parent_cost_center_consolidates_all_descendants_without_duplication(
    db_session, auth_context
):
    client, parent, child, entry = await _seed_dimensional_data(
        db_session, auth_context.workspace_id
    )
    grandchild = FinanceCostCenter(
        workspace_id=auth_context.workspace_id,
        code="CONT",
        name="Contencioso",
        parent_id=child.id,
    )
    second_entry = FinanceEntry(
        workspace_id=auth_context.workspace_id,
        title="Honorários do neto",
        entry_type=FinanceEntryType.receivable,
        status=FinanceEntryStatus.paid,
        amount=Decimal("250.00"),
        due_date=_dt(3),
        paid_at=_dt(4),
    )
    db_session.add_all([grandchild, second_entry])
    await db_session.flush()
    db_session.add_all(
        [
            FinanceAllocation(
                workspace_id=auth_context.workspace_id,
                finance_entry_id=entry.id,
                cost_center_id=child.id,
                client_id=client.id,
                percentage=Decimal("100"),
                amount=Decimal("1000.00"),
            ),
            FinanceAllocation(
                workspace_id=auth_context.workspace_id,
                finance_entry_id=second_entry.id,
                cost_center_id=grandchild.id,
                client_id=client.id,
                percentage=Decimal("100"),
                amount=Decimal("250.00"),
            ),
        ]
    )
    await db_session.flush()

    service = FinanceDimensionalService(db_session)
    parent_result = await service.drilldown(
        auth_context.workspace_id, cost_center_id=parent.id
    )
    child_result = await service.drilldown(
        auth_context.workspace_id, cost_center_id=child.id
    )
    parent_dre = await service.dre(
        auth_context.workspace_id,
        mode="cash",
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        cost_center_id=parent.id,
    )

    assert parent_result["total"] == "1250.00"
    assert parent_result["totalCount"] == 2
    assert len({item["allocationId"] for item in parent_result["items"]}) == 2
    assert parent_result["coverage"]["consolidatesDescendants"] is True
    assert parent_result["coverage"]["costCenterScopeCount"] == 3
    assert child_result["total"] == "1250.00"
    assert child_result["coverage"]["costCenterScopeCount"] == 2
    assert parent_dre["result"] == "1250.00"


@pytest.mark.asyncio
async def test_dre_supports_cash_accrual_and_dimension_filter(db_session, auth_context):
    """Cash and accrual DREs must be explicit and filterable by dimension."""
    service = FinanceReportsService(db_session)
    center = FinanceCostCenter(
        workspace_id=auth_context.workspace_id, code="DRE", name="Centro DRE"
    )
    db_session.add(center)
    await db_session.flush()
    cash = await service.dre(auth_context.workspace_id, competencia=AS_OF, basis="cash")
    accrual = await service.dre(
        auth_context.workspace_id,
        competencia=AS_OF,
        basis="accrual",
        cost_center_id=center.id,
        compare_budget=True,
    )
    assert cash.basis == "cash"
    assert accrual.basis == "accrual"
    assert hasattr(accrual, "budget_variance")


@pytest.mark.asyncio
async def test_dimensional_dre_exposes_monthly_accumulated_and_previous_period(
    db_session, auth_context
):
    db_session.add_all(
        [
            FinanceEntry(
                workspace_id=auth_context.workspace_id,
                title="Receita junho",
                entry_type=FinanceEntryType.receivable,
                status=FinanceEntryStatus.paid,
                amount=Decimal("400.00"),
                due_date=_dt(10, 6),
                paid_at=_dt(10, 6),
            ),
            FinanceEntry(
                workspace_id=auth_context.workspace_id,
                title="Receita julho",
                entry_type=FinanceEntryType.receivable,
                status=FinanceEntryStatus.paid,
                amount=Decimal("600.00"),
                due_date=_dt(10, 7),
                paid_at=_dt(10, 7),
            ),
            FinanceEntry(
                workspace_id=auth_context.workspace_id,
                title="Receita agosto",
                entry_type=FinanceEntryType.receivable,
                status=FinanceEntryStatus.paid,
                amount=Decimal("1000.00"),
                due_date=_dt(1, 8),
                paid_at=_dt(1, 8),
            ),
        ]
    )
    await db_session.flush()

    result = await FinanceDimensionalService(db_session).dre(
        auth_context.workspace_id,
        mode="cash",
        period_start=date(2026, 7, 1),
        period_end=date(2026, 8, 31),
        granularity="monthly",
        accumulated=True,
        compare_previous=True,
    )

    assert [row["result"] for row in result["series"]] == ["600.00", "1000.00"]
    assert [row["accumulatedResult"] for row in result["series"]] == [
        "600.00",
        "1600.00",
    ]
    assert result["comparison"]["previousResult"] == "400.00"
    assert result["comparison"]["currentResult"] == "1600.00"
    assert result["comparison"]["variance"] == "1200.00"
    assert result["comparison"]["variancePercent"] == "300.00"


@pytest.mark.asyncio
async def test_forecast_scenarios_are_generated_listed_and_reproducible(db_session, auth_context):
    """Conservative/base/optimistic forecasts must be reproducible from inputs."""
    service = FinanceDimensionalService(db_session)
    generated = await service.generate_forecast(
        auth_context.workspace_id,
        as_of=AS_OF,
        horizon="90d",
        scenarios=["conservative", "base", "optimistic"],
    )
    repeated = await service.generate_forecast(
        auth_context.workspace_id,
        as_of=AS_OF,
        horizon="90d",
        scenarios=["conservative", "base", "optimistic"],
    )
    assert [item.scenario for item in generated] == ["conservative", "base", "optimistic"]
    assert [item.revenue for item in generated] == [item.revenue for item in repeated]


@pytest.mark.asyncio
async def test_valuation_is_calculated_from_versioned_premises(db_session, auth_context):
    client, _, center, entry = await _seed_dimensional_data(
        db_session, auth_context.workspace_id
    )
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client.id,
        demand_type="Caso que sustenta o valuation",
    )
    db_session.add(matter)
    await db_session.flush()
    contract = ContractBillingProposal(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        source_document_name="Contrato Alfa",
        items=[],
    )
    db_session.add(contract)
    await db_session.flush()
    db_session.add(
        FinanceAllocation(
            workspace_id=auth_context.workspace_id,
            finance_entry_id=entry.id,
            cost_center_id=center.id,
            client_id=client.id,
            contract_id=contract.id,
            matter_id=matter.id,
            percentage=Decimal("100"),
            amount=Decimal("1000.00"),
        )
    )
    await db_session.flush()
    service = FinanceDimensionalService(db_session)
    first = await service.calculate_valuation(
        auth_context.workspace_id,
        method="revenue_multiple",
        as_of=AS_OF,
        assumptions={"multiple": "3.0", "source": "manual"},
    )
    second = await service.calculate_valuation(
        auth_context.workspace_id,
        method="revenue_multiple",
        as_of=AS_OF,
        assumptions={"multiple": "3.0", "source": "manual"},
    )
    assert first.enterprise_value == second.enterprise_value
    assert first.assumptions["version"] == 1
    assert first.assumptions["range"] == {
        "low": "2400.00",
        "base": "3000.00",
        "high": "3600.00",
    }
    assert first.assumptions["decomposition"]["method"] == "revenue_multiple"
    assert first.assumptions["supportingContracts"] == [
        {
            "contractId": str(contract.id),
            "matterId": str(matter.id),
            "label": "Contrato Alfa",
            "allocatedRevenue": "1000.00",
        }
    ]
    assert await service.list_valuations(auth_context.workspace_id)


@pytest.mark.asyncio
async def test_tax_reform_simulation_returns_impact_and_human_gate(db_session, auth_context):
    service = FinanceDimensionalService(db_session)
    simulation = await service.simulate_tax_reform(
        auth_context.workspace_id,
        as_of=AS_OF,
        horizon_years=3,
        scenarios=["current", "transition", "post_reform"],
    )
    assert simulation["requiresHuman"] is True
    assert simulation["humanRole"] == "contador"
    assert {"priceImpact", "marginImpact", "cashImpact"} <= set(simulation["impact"])
    assert simulation["alerts"]


@pytest.mark.asyncio
async def test_tax_reform_simulation_applies_requested_price_pass_through(db_session, auth_context):
    service = FinanceDimensionalService(db_session)
    base = {
        "annual_revenue": Decimal("100000.00"),
        "annual_expense": Decimal("30000.00"),
        "current_effective_rate": Decimal("10"),
        "transition_rates": {"2026": Decimal("20")},
    }
    without_pass_through = await service.simulate_tax_reform(
        auth_context.workspace_id,
        TaxReformSimulationWrite(**base, price_pass_through_percent=Decimal("0")),
        as_of=date(2026, 1, 1),
        horizon_years=1,
    )
    with_pass_through = await service.simulate_tax_reform(
        auth_context.workspace_id,
        TaxReformSimulationWrite(**base, price_pass_through_percent=Decimal("50")),
        as_of=date(2026, 1, 1),
        horizon_years=1,
    )
    assert without_pass_through["impact"]["priceImpact"] == "0.00"
    assert with_pass_through["impact"]["priceImpact"] == "5000.00"


@pytest.mark.asyncio
async def test_export_snapshot_contains_contextual_finance_360_data(db_session, auth_context):
    center = FinanceCostCenter(
        workspace_id=auth_context.workspace_id, code="EXP", name="Centro Export"
    )
    db_session.add(center)
    await db_session.flush()
    job = FinanceExportJob(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        format="xlsx",
        status="queued",
        drill_context={"costCenterId": str(center.id), "clientId": str(uuid4())},
    )
    snapshot = await FinanceExportService(db_session)._snapshot(job)
    assert snapshot["filters"] == {"scope": "dimensional", "dimensions": ["cost_center", "client"]}
    assert all("Id" not in key and "id" not in key.lower() for key in snapshot["filters"])
    assert {"executive", "forecasts", "valuations", "tax", "insights"} <= set(snapshot)
    assert snapshot["scope"] == "dimensional"
    assert snapshot["executive"]["status"] == "not_applicable_to_dimensional_scope"
    assert snapshot["cashFlow"]["status"] == "not_applicable_to_dimensional_scope"
    assert snapshot["kpis"]["status"] == "derived_from_scoped_dre"


@pytest.mark.asyncio
async def test_allocation_closure_exposes_explicit_unallocated_residual(db_session, auth_context):
    _, _, child, entry = await _seed_dimensional_data(db_session, auth_context.workspace_id)
    await FinanceDimensionalService(db_session).create_allocation(
        auth_context.workspace_id,
        type(
            "Payload",
            (),
            {
                "model_dump": lambda self: {
                    "finance_entry_id": entry.id,
                    "cost_center_id": child.id,
                    "percentage": Decimal("60"),
                    "amount": Decimal("600.00"),
                    "audit_metadata": {},
                    "client_id": None,
                    "contract_id": None,
                    "matter_id": None,
                    "team_id": None,
                    "user_id": None,
                    "source": "manual",
                }
            },
        )(),
    )
    closure = await FinanceDimensionalService(db_session).allocation_closure(
        auth_context.workspace_id, finance_entry_id=entry.id
    )
    assert closure["allocatedPercentage"] == "60.0000"
    assert closure["unallocatedPercentage"] == "40.0000"
    assert closure["unallocatedAmount"] == "400.00"

    drilldown = await FinanceDimensionalService(db_session).drilldown(auth_context.workspace_id)
    assert drilldown["total"] == "1000.00"
    assert drilldown["coverage"] == {
        "allocatedAmount": "600.00",
        "unallocatedAmount": "400.00",
        "reconciled": True,
        "consolidatesDescendants": False,
        "costCenterScopeCount": 0,
    }
    assert any(
        group["label"] == "Não alocado" and group["amount"] == "400.00"
        for group in drilldown["groups"]
    )

    consolidated = await FinanceDimensionalService(db_session).dre(
        auth_context.workspace_id,
        mode="cash",
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
    )
    assert consolidated["result"] == "1000.00"
