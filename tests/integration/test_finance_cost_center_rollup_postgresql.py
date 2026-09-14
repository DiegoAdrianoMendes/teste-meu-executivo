from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.client import Client
from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.finance_dimensional import FinanceAllocation, FinanceCostCenter
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.finance_dimensional_service import FinanceDimensionalService

MASTER_URL = os.getenv("KLM_TEST_POSTGRES_MASTER_URL")
if not MASTER_URL:
    raise RuntimeError(
        "PostgreSQL finance QA requires KLM_TEST_POSTGRES_MASTER_URL; "
        "run scripts/verify_postgresql_qa.sh."
    )


@pytest.mark.asyncio
async def test_postgresql_recursive_cost_center_rollup_has_no_duplicate_allocations():
    engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    workspace_id = None
    try:
        async with sessions() as db:
            workspace = Workspace(
                name="Finance recursive rollup QA",
                slug=f"finance-rollup-{os.getpid()}",
                status=WorkspaceStatus.active,
                setup_mode=WorkspaceSetupMode.self_service,
                is_active=True,
                settings={},
            )
            db.add(workspace)
            await db.flush()
            workspace_id = workspace.id
            client = Client(workspace_id=workspace.id, name="Cliente rollup PostgreSQL")
            root = FinanceCostCenter(workspace_id=workspace.id, code="ROOT", name="Raiz")
            db.add_all([client, root])
            await db.flush()
            child = FinanceCostCenter(
                workspace_id=workspace.id, code="CHILD", name="Filho", parent_id=root.id
            )
            db.add(child)
            await db.flush()
            grandchild = FinanceCostCenter(
                workspace_id=workspace.id,
                code="GRANDCHILD",
                name="Neto",
                parent_id=child.id,
            )
            first = FinanceEntry(
                workspace_id=workspace.id,
                title="Receita filho",
                entry_type=FinanceEntryType.receivable,
                status=FinanceEntryStatus.paid,
                amount=Decimal("100.00"),
                due_date=datetime(2026, 8, 1, tzinfo=UTC),
                paid_at=datetime(2026, 8, 1, tzinfo=UTC),
            )
            second = FinanceEntry(
                workspace_id=workspace.id,
                title="Receita neto",
                entry_type=FinanceEntryType.receivable,
                status=FinanceEntryStatus.paid,
                amount=Decimal("50.00"),
                due_date=datetime(2026, 8, 2, tzinfo=UTC),
                paid_at=datetime(2026, 8, 2, tzinfo=UTC),
            )
            db.add_all([grandchild, first, second])
            await db.flush()
            db.add_all(
                [
                    FinanceAllocation(
                        workspace_id=workspace.id,
                        finance_entry_id=first.id,
                        cost_center_id=child.id,
                        client_id=client.id,
                        percentage=Decimal("100"),
                        amount=Decimal("100.00"),
                    ),
                    FinanceAllocation(
                        workspace_id=workspace.id,
                        finance_entry_id=second.id,
                        cost_center_id=grandchild.id,
                        client_id=client.id,
                        percentage=Decimal("100"),
                        amount=Decimal("50.00"),
                    ),
                ]
            )
            await db.flush()

            service = FinanceDimensionalService(db)
            result = await service.drilldown(workspace.id, cost_center_id=root.id)
            dre = await service.dre(
                workspace.id,
                mode="cash",
                period_start=date(2026, 8, 1),
                period_end=date(2026, 8, 31),
                cost_center_id=root.id,
            )

            assert result["total"] == "150.00"
            assert result["totalCount"] == 2
            assert len({row["allocationId"] for row in result["items"]}) == 2
            assert result["coverage"]["costCenterScopeCount"] == 3
            assert dre["result"] == "150.00"
            await db.rollback()
    finally:
        if workspace_id is not None:
            async with sessions() as db:
                await db.execute(delete(Workspace).where(Workspace.id == workspace_id))
                await db.commit()
        await engine.dispose()
