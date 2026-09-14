from __future__ import annotations

from sqlalchemy import event

from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.workspace import Workspace, WorkspaceStatus
from app.services.platform_service import PlatformService


async def test_workspace_page_plan_summary_has_bounded_query_count(db_session):
    plan = Plan(
        name="Pro escalável",
        tier=PlanTier.pro,
        price_monthly_cents=69700,
        price_annually_cents=669120,
        max_users=10,
        max_matters=None,
        max_tokens_per_month=2_000_000,
        max_ai_agents=12,
        features={},
    )
    db_session.add(plan)
    await db_session.flush()

    workspaces = [
        Workspace(
            name=f"Conta escalável {index:02d}",
            slug=f"conta-escalavel-{index:02d}",
            status=WorkspaceStatus.active,
        )
        for index in range(25)
    ]
    db_session.add_all(workspaces)
    await db_session.flush()
    db_session.add_all(
        [
            Subscription(
                workspace_id=workspace.id,
                plan_id=plan.id,
                status=SubscriptionStatus.active,
                interval=BillingInterval.monthly,
                amount_cents=69700,
                currency="BRL",
            )
            for workspace in workspaces
        ]
    )
    await db_session.commit()
    db_session.expire_all()

    select_statements: list[str] = []

    def count_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            select_statements.append(statement)

    engine = db_session.bind
    assert engine is not None
    event.listen(engine.sync_engine, "before_cursor_execute", count_selects)
    try:
        items = await PlatformService(db_session).list_platform_workspaces(limit=25)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_selects)

    assert len(items) == 25
    assert len(select_statements) <= 7
    assert all(
        item["plan_summary"]
        == {
            "tier": "pro",
            "name": "Pro escalável",
            "subscription_status": "active",
        }
        for item in items
    )
