"""Testes unitários do PlatformBillingService (Sprint B1 Centro de Comando)."""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.commercial_catalog import WorkspaceCommercialContract
from app.models.subscription_commercial import (
    WorkspaceSubscription,
    WorkspaceSubscriptionStatus,
)
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.commercial_legacy_bridge_service import CanonicalContractCommand
from app.services.platform_billing_service import (
    CanonicalCommercialContractConflictError,
    InvalidSubscriptionActionError,
    PlatformBillingService,
    SubscriptionNotFoundError,
)


def _workspace(name: str) -> Workspace:
    return Workspace(
        name=name,
        slug=f"pbs-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )


def _staff_user(role_package: str = "platform_admin") -> User:
    return User(
        name="Staff Root",
        email=f"staff-{uuid4().hex[:8]}@mv.com.br",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="SR",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=True,
        platform_access_active=True,
        platform_role_package=role_package,
    )


@pytest.mark.asyncio
async def test_apply_discount_updates_mrr(db_session):
    workspace = _workspace("Billing Service WS")
    db_session.add(workspace)
    await db_session.flush()
    sub = WorkspaceSubscription(
        workspace_id=workspace.id,
        plano="escritorio",
        status=WorkspaceSubscriptionStatus.ativa,
        valor_mensal_cents=49700,
        ciclo="mensal",
        origem="test",
    )
    db_session.add(sub)
    actor = _staff_user("finance_billing")
    db_session.add(actor)
    await db_session.commit()

    svc = PlatformBillingService(db_session)
    result = await svc.apply_discount(
        workspace.id,
        desconto_pct=Decimal("20"),
        cortesia=False,
        actor=actor,
        motivo="Negociação comercial.",
    )
    assert result["desconto_pct"] == 20.0
    assert result["mrr_cents"] == int(49700 * 0.8)

    overview = await svc.get_overview()
    assert overview["mrr_cents"] >= int(49700 * 0.8)


@pytest.mark.asyncio
async def test_extend_trial_rejects_non_trial_subscription(db_session):
    workspace = _workspace("Billing Service WS Ativa")
    db_session.add(workspace)
    await db_session.flush()
    sub = WorkspaceSubscription(
        workspace_id=workspace.id,
        plano="essencial",
        status=WorkspaceSubscriptionStatus.ativa,
        valor_mensal_cents=19700,
        ciclo="mensal",
        origem="test",
    )
    db_session.add(sub)
    actor = _staff_user("finance_billing")
    db_session.add(actor)
    await db_session.commit()

    svc = PlatformBillingService(db_session)
    with pytest.raises(InvalidSubscriptionActionError):
        await svc.extend_trial(workspace.id, dias=7, actor=actor, motivo="teste")


@pytest.mark.asyncio
async def test_actions_on_unknown_workspace_raise_not_found(db_session):
    actor = _staff_user()
    db_session.add(actor)
    await db_session.commit()

    svc = PlatformBillingService(db_session)
    with pytest.raises(SubscriptionNotFoundError):
        await svc.suspend(uuid4(), actor=actor, motivo="teste")


@pytest.mark.asyncio
async def test_workspace_pnl_computes_margin(db_session):
    workspace = _workspace("Billing Service WS PnL")
    db_session.add(workspace)
    await db_session.flush()
    sub = WorkspaceSubscription(
        workspace_id=workspace.id,
        plano="essencial",
        status=WorkspaceSubscriptionStatus.ativa,
        valor_mensal_cents=19700,
        ciclo="mensal",
        origem="test",
    )
    db_session.add(sub)
    await db_session.commit()

    svc = PlatformBillingService(db_session)
    pnl = await svc.get_workspace_pnl(workspace.id)
    assert pnl["receita_mensal_cents"] == 19700
    assert pnl["margem_cents"] == pnl["receita_mensal_cents"] - pnl["custo_total_cents"]
    assert pnl["margem_pct"] is not None


@pytest.mark.asyncio
async def test_plan_change_routes_through_canonical_contract_and_other_legacy_writes_fail(
    db_session,
):
    workspace = _workspace("Contrato canônico")
    actor = _staff_user("finance_billing")
    db_session.add_all([workspace, actor])
    await db_session.flush()
    original = await CanonicalContractCommand(db_session).ensure_core_plan(
        workspace_id=workspace.id,
        plan="crm_start",
        actor_user_id=actor.id,
        idempotency_key="platform-billing-canonical-start",
        reason="Contrato Start antes da troca canônica.",
    )

    service = PlatformBillingService(db_session)
    changed = await service.change_plan(
        workspace.id,
        novo_plano="escritorio",
        actor=actor,
        motivo="Troca canônica para o plano Advanced.",
    )
    assert changed["plano"] == "crm_advanced"
    canonical = await service.db.get(WorkspaceCommercialContract, original.id)
    assert canonical is not None and canonical.version == 2
    contract = await CanonicalContractCommand(db_session).catalog.get_contract(original.id)
    assert [line.sku for line in contract.lines if line.status.value == "active"] == [
        "crm_advanced"
    ]

    with pytest.raises(CanonicalCommercialContractConflictError, match="contrato canônico"):
        await service.apply_discount(
            workspace.id,
            desconto_pct=Decimal("10"),
            cortesia=False,
            actor=actor,
            motivo="Não deve criar duas verdades.",
        )
    with pytest.raises(CanonicalCommercialContractConflictError, match="contrato canônico"):
        await service.extend_trial(
            workspace.id,
            dias=7,
            actor=actor,
            motivo="Não deve criar duas verdades.",
        )
    with pytest.raises(CanonicalCommercialContractConflictError, match="contrato canônico"):
        await service.suspend(
            workspace.id,
            actor=actor,
            motivo="Não deve criar duas verdades.",
        )
    with pytest.raises(CanonicalCommercialContractConflictError, match="contrato canônico"):
        await service.reactivate(
            workspace.id,
            actor=actor,
            motivo="Não deve criar duas verdades.",
        )
