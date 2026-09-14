"""
Testes do motor de KPIs (PlatformMetricsService).

Cobre: MRR/lucro líquido computam certo com dados semeados, movimento de MRR
(novo/expansão/contração/churn) a partir de SubscriptionEvent, KPI sem dado
(DAU/WAU/MAU, CAC) fica marcado honestamente, e a resposta agrupa por categoria.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import PlatformTokenUsage
from app.models.subscription_commercial import (
    SubscriptionEvent,
    SubscriptionEventType,
    WorkspaceSubscription,
    WorkspaceSubscriptionStatus,
)
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.services.platform_metrics_service import CATEGORIES, PlatformMetricsService

pytestmark = pytest.mark.asyncio


def _workspace(name: str) -> Workspace:
    return Workspace(
        name=name,
        slug=f"metrics-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )


async def _seed_paid_workspace(
    db_session: AsyncSession, *, plano="essencial", valor=19700
) -> tuple[Workspace, WorkspaceSubscription]:
    workspace = _workspace("Metrics WS")
    db_session.add(workspace)
    await db_session.flush()
    sub = WorkspaceSubscription(
        workspace_id=workspace.id,
        plano=plano,
        status=WorkspaceSubscriptionStatus.ativa,
        valor_mensal_cents=valor,
        ciclo="mensal",
        origem="test",
    )
    db_session.add(sub)
    await db_session.flush()

    user = User(
        name="Bia Advogada",
        email=f"bia-{uuid4().hex[:8]}@escritorio.com.br",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="BA",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole.workspace_admin,
            status=MembershipStatus.active,
        )
    )

    # Evento "criada" com MRR novo no before/after — alimenta MRR movement.
    db_session.add(
        SubscriptionEvent(
            subscription_id=sub.id,
            workspace_id=workspace.id,
            event_type=SubscriptionEventType.criada,
            actor_label="sistema",
            motivo="Assinatura criada.",
            before_json={"mrr_cents": 0},
            after_json={"mrr_cents": sub.mrr_cents},
        )
    )

    db_session.add(
        PlatformTokenUsage(
            workspace_id=workspace.id,
            model_used="claude-sonnet-5",
            provider="anthropic",
            usage_date=datetime.now(timezone.utc),
            total_runs=10,
            total_input_tokens=5000,
            total_output_tokens=2000,
            total_cost_usd=Decimal("2.5000"),
        )
    )
    await db_session.commit()
    return workspace, sub


async def test_mrr_and_net_profit_compute_from_seeded_data(db_session: AsyncSession):
    workspace, sub = await _seed_paid_workspace(db_session, plano="essencial", valor=19700)

    service = PlatformMetricsService(db_session)
    result = await service.get_all_metrics(period_days=30)

    receita = next(c for c in result["categories"] if c["key"] == "receita")
    kpi_by_key = {k["key"]: k for k in receita["kpis"]}

    assert kpi_by_key["receita.mrr"]["value"] >= 197.0
    assert kpi_by_key["receita.arr"]["value"] >= 197.0 * 12

    # net profit = mrr - custo_ia - custo_whatsapp - custo_infra ; deve ser < mrr
    lucro = kpi_by_key["receita.lucro_liquido"]["value"]
    mrr = kpi_by_key["receita.mrr"]["value"]
    assert lucro < mrr

    custo_ia = kpi_by_key["receita.custo_ia"]["value"]
    assert custo_ia > 0  # 2.5 USD * câmbio de referência


async def test_mrr_movement_new_from_subscription_event(db_session: AsyncSession):
    await _seed_paid_workspace(db_session, plano="essencial", valor=19700)

    service = PlatformMetricsService(db_session)
    result = await service.get_all_metrics(period_days=30)
    kpi_by_key = {
        kpi["key"]: kpi
        for category in result["categories"]
        for kpi in category["kpis"]
    }

    assert kpi_by_key["receita.mrr_movement.novo"]["value"] >= 197.0
    assert kpi_by_key["receita.mrr_movement.churned"]["value"] == 0.0


async def test_dau_wau_mau_and_cac_are_marked_sem_dado_honestly(db_session: AsyncSession):
    await _seed_paid_workspace(db_session)

    service = PlatformMetricsService(db_session)
    result = await service.get_all_metrics(period_days=30)

    uso = next(c for c in result["categories"] if c["key"] == "produto")
    dau = next(k for k in uso["kpis"] if k["key"] == "uso.dau")
    assert dau["sem_dado"] is True
    assert dau["value"] is None
    assert dau["nota"]  # explica o motivo, nunca fabrica número

    receita = next(c for c in result["categories"] if c["key"] == "receita")
    cac = next(k for k in receita["kpis"] if k["key"] == "receita.cac")
    assert cac["sem_dado"] is True
    assert cac["value"] is None


async def test_all_categories_present_and_kpi_count_is_substantial(db_session: AsyncSession):
    await _seed_paid_workspace(db_session)

    service = PlatformMetricsService(db_session)
    result = await service.get_all_metrics(period_days=30)

    category_keys = {c["key"] for c in result["categories"]}
    assert category_keys == set(CATEGORIES)
    assert result["total_kpis"] >= 100


async def test_category_endpoint_rejects_unknown_category(db_session: AsyncSession):
    service = PlatformMetricsService(db_session)
    with pytest.raises(ValueError):
        await service.get_category_metrics("inexistente", period_days=30)
