"""Backend contracts for budgets and versioned cost-centre administration."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.finance_dimensional import (
    FinanceAllocation,
    FinanceBudgetLine,
    FinanceCostCenter,
)
from app.schemas.finance_dimensional import (
    BudgetLineWrite,
    CostCenterUpdate,
    CostCenterWrite,
)
from app.services.finance_dimensional_service import (
    FinanceDimensionalService,
    FinanceDimensionValidationError,
)

PERIOD_START = date(2026, 8, 1)
PERIOD_END = date(2026, 8, 31)


@pytest.mark.asyncio
async def test_budget_line_create_and_list_is_period_and_center_scoped(db_session, auth_context):
    service = FinanceDimensionalService(db_session)
    center = await service.create_cost_center(
        auth_context.workspace_id,
        CostCenterWrite(code="OPS", name="Operação"),
    )
    line = await service.create_budget_line(
        auth_context.workspace_id,
        BudgetLineWrite(
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            cost_center_id=center.id,
            category="pessoal",
            entry_type="expense",
            amount=Decimal("2500.00"),
            assumptions={"source": "approved-budget-v1"},
        ),
    )

    rows = await service.list_budget_lines(
        auth_context.workspace_id,
        period_start=date(2026, 8, 15),
        period_end=date(2026, 8, 15),
        cost_center_id=center.id,
    )

    assert len(rows) == 1
    assert rows[0].id == line.id
    assert rows[0].amount == Decimal("2500.00")
    assert rows[0].assumptions == {"source": "approved-budget-v1"}


@pytest.mark.asyncio
async def test_dre_returns_budget_variance_for_realized_dimension(db_session, auth_context):
    service = FinanceDimensionalService(db_session)
    center = await service.create_cost_center(
        auth_context.workspace_id,
        CostCenterWrite(code="OPS", name="Operação"),
    )
    # Use a minimal object with the service's normal write shape; allocation
    # and entry fixtures are intentionally kept out of this contract test.
    await service.create_budget_line(
        auth_context.workspace_id,
        BudgetLineWrite(
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            cost_center_id=center.id,
            entry_type="expense",
            amount=Decimal("1000.00"),
        ),
    )

    result = await service.dre(
        auth_context.workspace_id,
        mode="cash",
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        cost_center_id=center.id,
    )

    assert result["budgetQuality"] == "configured"
    assert result["budget"]["expenses"] == "1000.00"
    # Actual result is zero while the planned expense result is -1,000;
    # therefore the favorable variance is +1,000.
    assert result["budget"]["variance"] == "1000.00"


@pytest.mark.asyncio
async def test_cost_center_update_requires_current_optimistic_version(db_session, auth_context):
    service = FinanceDimensionalService(db_session)
    center = await service.create_cost_center(
        auth_context.workspace_id,
        CostCenterWrite(code="OPS", name="Operação"),
    )

    original_version = center.version
    updated = await service.update_cost_center(
        auth_context.workspace_id,
        center.id,
        CostCenterUpdate(name="Operação atualizada", expected_version=original_version),
    )
    assert updated.version == original_version + 1

    with pytest.raises(FinanceDimensionValidationError, match="desatualizada"):
        await service.update_cost_center(
            auth_context.workspace_id,
            center.id,
            CostCenterUpdate(name="Escrita concorrente", expected_version=original_version),
        )


@pytest.mark.asyncio
async def test_cost_center_hierarchy_rejects_self_and_ancestor_cycles(db_session, auth_context):
    service = FinanceDimensionalService(db_session)
    parent = await service.create_cost_center(
        auth_context.workspace_id,
        CostCenterWrite(code="PARENT", name="Pai"),
    )
    child = await service.create_cost_center(
        auth_context.workspace_id,
        CostCenterWrite(code="CHILD", name="Filho", parent_id=parent.id),
    )

    with pytest.raises(FinanceDimensionValidationError, match="seu próprio pai"):
        await service.update_cost_center(
            auth_context.workspace_id,
            parent.id,
            CostCenterUpdate(parent_id=parent.id, expected_version=parent.version),
        )
    with pytest.raises(FinanceDimensionValidationError, match="ciclo"):
        await service.update_cost_center(
            auth_context.workspace_id,
            parent.id,
            CostCenterUpdate(parent_id=child.id, expected_version=parent.version),
        )
    with pytest.raises(FinanceDimensionValidationError, match="ciclo"):
        await service.cost_center_impact_preview(
            auth_context.workspace_id,
            parent.id,
            {"parent_id": child.id},
            parent.version,
        )


@pytest.mark.asyncio
async def test_finance_user_lookup_is_scoped_through_workspace_membership(
    db_session, auth_context
):
    service = FinanceDimensionalService(db_session)

    result = await service.resolve_lookups(
        auth_context.workspace_id,
        entity_type="user",
        ids=[auth_context.user_id],
    )

    assert [item["id"] for item in result["items"]] == [str(auth_context.user_id)]


@pytest.mark.asyncio
async def test_cost_center_impact_preview_reports_children_and_allocations(
    db_session, auth_context
):
    service = FinanceDimensionalService(db_session)
    parent = await service.create_cost_center(
        auth_context.workspace_id,
        CostCenterWrite(code="PARENT", name="Pai"),
    )
    child = await service.create_cost_center(
        auth_context.workspace_id,
        CostCenterWrite(code="CHILD", name="Filho", parent_id=parent.id),
    )
    allocation = FinanceAllocation(
        workspace_id=auth_context.workspace_id,
        finance_entry_id=uuid4(),
        cost_center_id=parent.id,
        percentage=Decimal("100"),
        amount=Decimal("80.00"),
    )
    # The impact query only needs the allocation foreign-key values and the
    # test database permits the lightweight fixture used by this contract.
    db_session.add(allocation)
    await db_session.flush()

    impact = await service.cost_center_impact(auth_context.workspace_id, parent.id)

    assert impact == {
        "costCenterId": str(parent.id),
        "version": parent.version,
        "childCount": 1,
        "allocationCount": 1,
        "allocatedAmount": "80.00",
        "requiresConfirmation": True,
    }
    assert child.parent_id == parent.id


@pytest.mark.asyncio
async def test_budget_and_cost_center_operations_are_workspace_isolated(db_session, auth_context):
    service = FinanceDimensionalService(db_session)
    foreign_workspace_id = uuid4()
    foreign_center = FinanceCostCenter(
        workspace_id=foreign_workspace_id,
        code="FOREIGN",
        name="Outro workspace",
    )
    foreign_budget = FinanceBudgetLine(
        workspace_id=foreign_workspace_id,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        cost_center_id=foreign_center.id,
        entry_type="expense",
        amount=Decimal("9999.00"),
        assumptions={},
    )
    db_session.add_all([foreign_center, foreign_budget])
    await db_session.flush()

    assert await service.list_cost_centers(auth_context.workspace_id) == []
    assert await service.list_budget_lines(auth_context.workspace_id) == []
    with pytest.raises(FinanceDimensionValidationError, match="não encontrado"):
        await service.cost_center_impact(auth_context.workspace_id, foreign_center.id)
