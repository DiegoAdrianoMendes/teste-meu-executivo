from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.platform_rbac import PLATFORM_ROLE_PACKAGES, has_platform_permission
from app.models.platform_work_item import PlatformWorkItem
from app.models.subscription_commercial import (
    WorkspaceSubscription,
    WorkspaceSubscriptionStatus,
)
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.platform_dashboard_service import (
    ROLE_ACTIONS,
    ROLE_METRICS,
    PlatformDashboardService,
)

pytestmark = pytest.mark.asyncio


def _staff(role: str, *, root: bool = False) -> User:
    return User(
        name=f"Painel {role}",
        email=f"dashboard-{role}-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="PD",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=root,
        platform_role_package=role,
        platform_access_active=True,
    )


async def _seed_revenue(db_session: AsyncSession) -> None:
    workspace = Workspace(
        name="Conta do painel",
        slug=f"dashboard-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        onboarding_completed=False,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    db_session.add(
        WorkspaceSubscription(
            workspace_id=workspace.id,
            plano="essencial",
            status=WorkspaceSubscriptionStatus.ativa,
            valor_mensal_cents=19700,
            ciclo="mensal",
            origem="test",
        )
    )
    await db_session.commit()


async def test_all_fourteen_role_packages_have_operational_metric_templates() -> None:
    assert set(ROLE_METRICS) == set(PLATFORM_ROLE_PACKAGES)
    assert len(ROLE_METRICS) == 14
    assert all(3 <= len(metrics) <= 6 for metrics in ROLE_METRICS.values())


async def test_role_actions_point_to_canonical_governance_routes() -> None:
    compliance_hrefs = {item[2] for item in ROLE_ACTIONS["compliance_security"]}
    product_hrefs = {item[2] for item in ROLE_ACTIONS["product_operations"]}

    assert "/blackbox/governance/security" in compliance_hrefs
    assert "/blackbox/governance/feature_flags" in product_hrefs
    assert "/blackbox/governance" not in compliance_hrefs
    assert "/blackbox/feature-flags" not in product_hrefs


async def test_all_fourteen_roles_receive_authorized_operational_homes(
    db_session: AsyncSession,
) -> None:
    service = PlatformDashboardService(db_session)
    for role in PLATFORM_ROLE_PACKAGES:
        staff = _staff(role, root=role == "platform_owner")
        home = await service.home(user=staff, scope="mine", period_days=30)

        assert home.role == role
        assert 3 <= len(home.metrics) <= 6
        assert home.layout.widget_order
        assert all(metric.source for metric in home.metrics)
        assert all(
            metric.quality
            in {"updated", "partial", "estimated", "stale", "unavailable", "insufficient_data"}
            for metric in home.metrics
        )
        assert all(
            has_platform_permission(staff, action.module, action.action) for action in home.actions
        )


async def test_root_home_exposes_real_mrr_and_platform_scope(db_session: AsyncSession) -> None:
    await _seed_revenue(db_session)
    root = _staff("platform_owner", root=True)

    home = await PlatformDashboardService(db_session).home(
        user=root,
        scope="platform",
        period_days=30,
    )

    assert home.is_platform_root is True
    assert home.available_scopes == ["mine", "platform"]
    mrr = next(metric for metric in home.metrics if metric.key == "mrr")
    assert mrr.value is not None
    assert mrr.value >= 197
    assert mrr.quality == "updated"
    assert all("description" not in item.safe_summary.lower() for item in home.work_items)


async def test_platform_admin_does_not_receive_root_only_cost_metrics(
    db_session: AsyncSession,
) -> None:
    admin = _staff("platform_admin")

    home = await PlatformDashboardService(db_session).home(
        user=admin,
        scope="platform",
        period_days=30,
    )

    assert home.is_platform_root is False
    assert "mrr" not in {metric.key for metric in home.metrics}
    assert all("cost" not in metric.key and "margin" not in metric.key for metric in home.metrics)


async def test_unconfigured_team_scope_is_honest_not_zero(db_session: AsyncSession) -> None:
    support = _staff("support")

    home = await PlatformDashboardService(db_session).home(
        user=support,
        scope="team",
        period_days=30,
    )

    assert "team" not in home.available_scopes
    assert home.metrics
    assert all(metric.value is None for metric in home.metrics)
    assert all(metric.quality == "unavailable" for metric in home.metrics)
    assert any(source.key == "team_assignments" for source in home.source_health)


async def test_home_rebuilds_legacy_work_item_href_to_an_authorized_route(
    db_session: AsyncSession,
) -> None:
    support = _staff("support")
    workspace = Workspace(
        name="Conta do ticket",
        slug=f"dashboard-link-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        onboarding_completed=True,
        settings={},
    )
    db_session.add_all([support, workspace])
    await db_session.flush()
    now = datetime.now(UTC)
    source_id = uuid4()
    db_session.add(
        PlatformWorkItem(
            source_type="support_ticket",
            source_id=source_id,
            source_version="legacy-1",
            dedupe_key=f"support_ticket:{source_id}",
            kind="task",
            state="open",
            priority="P1",
            workspace_id=workspace.id,
            assigned_user_id=support.id,
            assigned_team_id=None,
            requester_user_id=None,
            reviewer_user_id=None,
            safe_title="Ticket com link legado",
            safe_summary="Resumo operacional seguro.",
            href=f"/blackbox/support/tickets/{source_id}",
            required_module="support",
            required_action="view",
            allowed_commands=[],
            opened_at=now,
            due_at=None,
            snoozed_until=None,
            closed_at=None,
            source_updated_at=now,
            projected_at=now,
            projection_version=1,
            payload_safe={},
        )
    )
    await db_session.commit()

    home = await PlatformDashboardService(db_session).home(
        user=support,
        scope="mine",
        period_days=30,
    )

    assert has_platform_permission(support, "support", "view")
    item = next(row for row in home.work_items if row.source_type == "support_ticket")
    parsed = urlsplit(item.href)
    params = parse_qs(parsed.query)
    assert parsed.path == "/blackbox/support-tickets"
    assert params == {
        "sourceType": ["support_ticket"],
        "sourceId": [str(source_id)],
        "tab": ["tickets"],
        "focus": ["context"],
        "workspaceId": [str(workspace.id)],
    }
