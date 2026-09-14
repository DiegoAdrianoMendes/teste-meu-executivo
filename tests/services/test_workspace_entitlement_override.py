"""Overrides de entitlement por conta: precedência override>plano>default,
auditoria de toda mudança, e validação fail-closed.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.entitlement import (
    EntitlementKey,
    OverrideMode,
    PlanEntitlement,
    WorkspaceEntitlementOverride,
)
from app.models.klm_operations import KlmAuditEvent
from app.models.user import AgentType, User, UserRole
from app.services.entitlement_service import (
    EntitlementLimitSource,
    EntitlementNotConfiguredError,
    EntitlementResolver,
)
from app.services.workspace_entitlement_override_service import (
    WorkspaceEntitlementOverrideNotFoundError,
    WorkspaceEntitlementOverrideService,
    WorkspaceEntitlementOverrideValidationError,
    WorkspaceEntitlementOverrideWorkspaceNotFoundError,
)

pytestmark = pytest.mark.asyncio


async def _seed_plan_and_subscription(
    db: AsyncSession,
    auth_context: AuthContext,
    *,
    tokens_limit: int = 100,
    digital_signature_enabled: bool = True,
) -> Plan:
    plan = Plan(
        name="Plano override local",
        tier=PlanTier.pro,
        price_monthly_cents=0,
        price_annually_cents=0,
        max_users=10,
        max_matters=None,
        max_tokens_per_month=100,
        max_ai_agents=10,
        features={},
        is_active=True,
        is_public=False,
        sort_order=1,
    )
    db.add(plan)
    await db.flush()
    db.add(
        Subscription(
            workspace_id=auth_context.workspace_id,
            plan_id=plan.id,
            status=SubscriptionStatus.active,
            interval=BillingInterval.monthly,
            amount_cents=0,
            currency="BRL",
        )
    )
    db.add_all(
        [
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=EntitlementKey.tokens,
                enabled=True,
                limit_quantity=tokens_limit,
                hard_limit=True,
            ),
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=EntitlementKey.digital_signature,
                enabled=digital_signature_enabled,
                limit_quantity=None,
                hard_limit=True,
            ),
        ]
    )
    await db.flush()
    return plan


async def _seed_platform_actor(db: AsyncSession) -> User:
    user = User(
        name="Titular Blackbox",
        email=f"root-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="TB",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=True,
    )
    db.add(user)
    await db.flush()
    return user


async def test_grant_override_enables_entitlement_the_plan_never_configured(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_plan_and_subscription(db_session, auth_context)
    actor = await _seed_platform_actor(db_session)

    # The seeded plan is "typed" (has PlanEntitlement rows) but never
    # configures EntitlementKey.users — plain resolution must fail closed.
    with pytest.raises(EntitlementNotConfiguredError):
        await EntitlementResolver(db_session).resolve(
            auth_context.workspace_id, EntitlementKey.users
        )

    await WorkspaceEntitlementOverrideService(db_session).upsert_override(
        workspace_id=auth_context.workspace_id,
        entitlement_key=EntitlementKey.users.value,
        mode="grant",
        quantity=7,
        reason="Cortesia comercial aprovada pelo titular.",
        actor_user_id=actor.id,
    )

    resolved = await EntitlementResolver(db_session).resolve(
        auth_context.workspace_id, EntitlementKey.users
    )
    assert resolved.enabled is True
    assert resolved.limit_quantity == 7
    assert resolved.source == EntitlementLimitSource.workspace_override


async def test_revoke_override_disables_entitlement_even_when_plan_enables_it(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_plan_and_subscription(db_session, auth_context, digital_signature_enabled=True)
    actor = await _seed_platform_actor(db_session)

    baseline = await EntitlementResolver(db_session).resolve(
        auth_context.workspace_id, EntitlementKey.digital_signature
    )
    assert baseline.enabled is True

    await WorkspaceEntitlementOverrideService(db_session).upsert_override(
        workspace_id=auth_context.workspace_id,
        entitlement_key=EntitlementKey.digital_signature.value,
        mode="revoke",
        quantity=None,
        reason="Inadimplência confirmada — suspender assinatura digital.",
        actor_user_id=actor.id,
    )

    resolved = await EntitlementResolver(db_session).resolve(
        auth_context.workspace_id, EntitlementKey.digital_signature
    )
    assert resolved.enabled is False
    assert resolved.limit_quantity == 0
    assert resolved.hard_limit is True
    assert resolved.source == EntitlementLimitSource.workspace_override


async def test_limit_override_replaces_the_plan_quantity(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_plan_and_subscription(db_session, auth_context, tokens_limit=100)
    actor = await _seed_platform_actor(db_session)

    await WorkspaceEntitlementOverrideService(db_session).upsert_override(
        workspace_id=auth_context.workspace_id,
        entitlement_key=EntitlementKey.tokens.value,
        mode="limit",
        quantity=5000,
        reason="Pacote extra negociado fora do plano padrão.",
        actor_user_id=actor.id,
    )

    resolved = await EntitlementResolver(db_session).resolve(
        auth_context.workspace_id, EntitlementKey.tokens
    )
    assert resolved.enabled is True
    assert resolved.limit_quantity == 5000
    assert resolved.source == EntitlementLimitSource.workspace_override


async def test_limit_override_accepts_explicit_zero(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_plan_and_subscription(db_session, auth_context, tokens_limit=100)
    actor = await _seed_platform_actor(db_session)

    created = await WorkspaceEntitlementOverrideService(db_session).upsert_override(
        workspace_id=auth_context.workspace_id,
        entitlement_key=EntitlementKey.tokens.value,
        mode="limit",
        quantity=0,
        reason="Manter contratado sem franquia disponível neste ciclo.",
        actor_user_id=actor.id,
    )

    assert created.quantity == 0
    resolved = await EntitlementResolver(db_session).resolve(
        auth_context.workspace_id, EntitlementKey.tokens
    )
    assert resolved.enabled is True
    assert resolved.limit_quantity == 0


async def test_upsert_and_delete_override_each_write_an_audit_event(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_plan_and_subscription(db_session, auth_context)
    actor = await _seed_platform_actor(db_session)
    service = WorkspaceEntitlementOverrideService(db_session)

    await service.upsert_override(
        workspace_id=auth_context.workspace_id,
        entitlement_key=EntitlementKey.automations.value,
        mode="grant",
        quantity=5,
        reason="Piloto comercial de automações.",
        actor_user_id=actor.id,
    )
    await service.delete_override(
        workspace_id=auth_context.workspace_id,
        entitlement_key=EntitlementKey.automations.value,
        reason="Piloto encerrado.",
        actor_user_id=actor.id,
    )

    events = (
        await db_session.scalars(
            select(KlmAuditEvent)
            .where(
                KlmAuditEvent.workspace_id == auth_context.workspace_id,
                KlmAuditEvent.entity_type == "workspace_entitlement_override",
            )
            .order_by(KlmAuditEvent.occurred_at)
        )
    ).all()
    assert [event.event_type for event in events] == [
        "workspace_entitlement_override_upserted",
        "workspace_entitlement_override_deleted",
    ]
    assert all(event.actor_user_id == actor.id for event in events)
    assert all(event.actor_label == actor.name for event in events)

    remaining = await service.list_overrides(auth_context.workspace_id)
    assert remaining == []


async def test_upsert_rejects_limit_mode_without_quantity(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_plan_and_subscription(db_session, auth_context)
    actor = await _seed_platform_actor(db_session)

    with pytest.raises(WorkspaceEntitlementOverrideValidationError):
        await WorkspaceEntitlementOverrideService(db_session).upsert_override(
            workspace_id=auth_context.workspace_id,
            entitlement_key=EntitlementKey.tokens.value,
            mode="limit",
            quantity=None,
            reason="Sem quantidade.",
            actor_user_id=actor.id,
        )


@pytest.mark.parametrize(
    "entitlement",
    [
        EntitlementKey.tokens,
        EntitlementKey.ai_executions,
        EntitlementKey.storage_bytes,
        EntitlementKey.active_tasks,
        EntitlementKey.campaign_recipients,
    ],
)
async def test_quantitative_grant_requires_explicit_finite_quantity(
    db_session: AsyncSession,
    auth_context: AuthContext,
    entitlement: EntitlementKey,
) -> None:
    await _seed_plan_and_subscription(db_session, auth_context)
    actor = await _seed_platform_actor(db_session)

    with pytest.raises(
        WorkspaceEntitlementOverrideValidationError,
        match=rf"{entitlement.value} exige quantity finita",
    ):
        await WorkspaceEntitlementOverrideService(db_session).upsert_override(
            workspace_id=auth_context.workspace_id,
            entitlement_key=entitlement.value,
            mode="grant",
            quantity=None,
            reason="Não permitir capacidade quantitativa implicitamente ilimitada.",
            actor_user_id=actor.id,
        )

    created = await WorkspaceEntitlementOverrideService(
        db_session
    ).upsert_override(
        workspace_id=auth_context.workspace_id,
        entitlement_key=entitlement.value,
        mode="grant",
        quantity=250,
        reason="Franquia finita aprovada explicitamente pelo titular.",
        actor_user_id=actor.id,
    )
    assert created.quantity == 250


@pytest.mark.parametrize(
    "quantity",
    [-1, True, 1.5, 9_007_199_254_740_992, 9_223_372_036_854_775_808],
)
async def test_quantitative_grant_rejects_invalid_quantity(
    db_session: AsyncSession,
    auth_context: AuthContext,
    quantity,
) -> None:
    await _seed_plan_and_subscription(db_session, auth_context)
    actor = await _seed_platform_actor(db_session)

    with pytest.raises(
        WorkspaceEntitlementOverrideValidationError,
        match="inteiro não negativo",
    ):
        await WorkspaceEntitlementOverrideService(db_session).upsert_override(
            workspace_id=auth_context.workspace_id,
            entitlement_key=EntitlementKey.storage_bytes.value,
            mode="grant",
            quantity=quantity,
            reason="Não aceitar quantidade inválida em grant quantitativo.",
            actor_user_id=actor.id,
        )


async def test_revoke_rejects_quantity(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_plan_and_subscription(db_session, auth_context)
    actor = await _seed_platform_actor(db_session)

    with pytest.raises(
        WorkspaceEntitlementOverrideValidationError,
        match="mode=revoke não aceita quantity",
    ):
        await WorkspaceEntitlementOverrideService(db_session).upsert_override(
            workspace_id=auth_context.workspace_id,
            entitlement_key=EntitlementKey.tokens.value,
            mode="revoke",
            quantity=5,
            reason="Não persistir uma quantidade contraditória com revogação.",
            actor_user_id=actor.id,
        )


async def test_boolean_grant_rejects_quantity(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_plan_and_subscription(db_session, auth_context)
    actor = await _seed_platform_actor(db_session)

    with pytest.raises(
        WorkspaceEntitlementOverrideValidationError,
        match="é booleano e não aceita quantity",
    ):
        await WorkspaceEntitlementOverrideService(db_session).upsert_override(
            workspace_id=auth_context.workspace_id,
            entitlement_key=EntitlementKey.calendar.value,
            mode="grant",
            quantity=1,
            reason="Não aceitar quantidade em capacidade booleana.",
            actor_user_id=actor.id,
        )


async def test_boolean_grant_remains_valid_without_quantity(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_plan_and_subscription(db_session, auth_context)
    actor = await _seed_platform_actor(db_session)

    created = await WorkspaceEntitlementOverrideService(
        db_session
    ).upsert_override(
        workspace_id=auth_context.workspace_id,
        entitlement_key=EntitlementKey.calendar.value,
        mode="grant",
        quantity=None,
        reason="Habilitar recurso booleano sem fabricar quota.",
        actor_user_id=actor.id,
    )

    assert created.quantity is None
    resolved = await EntitlementResolver(db_session).resolve(
        auth_context.workspace_id,
        EntitlementKey.calendar,
    )
    assert resolved.enabled is True
    assert resolved.limit_quantity is None


async def test_legacy_resolver_denies_historical_quantitative_grant_without_quantity(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_plan_and_subscription(db_session, auth_context)
    actor = await _seed_platform_actor(db_session)
    db_session.add(
        WorkspaceEntitlementOverride(
            workspace_id=auth_context.workspace_id,
            entitlement=EntitlementKey.tokens,
            mode=OverrideMode.grant,
            quantity=None,
            reason="Linha histórica inválida para provar defesa em profundidade.",
            created_by_user_id=actor.id,
        )
    )
    await db_session.flush()

    with pytest.raises(
        EntitlementNotConfiguredError,
        match="Override quantitativo histórico de tokens",
    ):
        await EntitlementResolver(db_session).resolve(
            auth_context.workspace_id,
            EntitlementKey.tokens,
        )


async def test_legacy_resolver_denies_enabled_quantitative_plan_without_limit(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    plan = await _seed_plan_and_subscription(db_session, auth_context)
    db_session.add(
        PlanEntitlement(
            plan_id=plan.id,
            entitlement=EntitlementKey.storage_bytes,
            enabled=True,
            limit_quantity=None,
            hard_limit=True,
        )
    )
    await db_session.flush()

    with pytest.raises(
        EntitlementNotConfiguredError,
        match="storage_bytes exige limite finito",
    ):
        await EntitlementResolver(db_session).resolve(
            auth_context.workspace_id,
            EntitlementKey.storage_bytes,
        )


async def test_upsert_rejects_unknown_entitlement_key(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_plan_and_subscription(db_session, auth_context)
    actor = await _seed_platform_actor(db_session)

    with pytest.raises(WorkspaceEntitlementOverrideValidationError):
        await WorkspaceEntitlementOverrideService(db_session).upsert_override(
            workspace_id=auth_context.workspace_id,
            entitlement_key="not_a_real_entitlement",
            mode="grant",
            quantity=None,
            reason="Teste.",
            actor_user_id=actor.id,
        )


async def test_upsert_requires_a_reason(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_plan_and_subscription(db_session, auth_context)
    actor = await _seed_platform_actor(db_session)

    with pytest.raises(WorkspaceEntitlementOverrideValidationError):
        await WorkspaceEntitlementOverrideService(db_session).upsert_override(
            workspace_id=auth_context.workspace_id,
            entitlement_key=EntitlementKey.tokens.value,
            mode="grant",
            quantity=None,
            reason="   ",
            actor_user_id=actor.id,
        )


async def test_delete_missing_override_raises_not_found(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_plan_and_subscription(db_session, auth_context)
    actor = await _seed_platform_actor(db_session)

    with pytest.raises(WorkspaceEntitlementOverrideNotFoundError):
        await WorkspaceEntitlementOverrideService(db_session).delete_override(
            workspace_id=auth_context.workspace_id,
            entitlement_key=EntitlementKey.tokens.value,
            reason="Nada para remover.",
            actor_user_id=actor.id,
        )


async def test_upsert_rejects_unknown_workspace(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    actor = await _seed_platform_actor(db_session)

    with pytest.raises(WorkspaceEntitlementOverrideWorkspaceNotFoundError):
        await WorkspaceEntitlementOverrideService(db_session).upsert_override(
            workspace_id=uuid4(),
            entitlement_key=EntitlementKey.tokens.value,
            mode="grant",
            quantity=None,
            reason="Workspace inexistente.",
            actor_user_id=actor.id,
        )


async def test_upsert_is_idempotent_per_workspace_and_entitlement_key(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_plan_and_subscription(db_session, auth_context)
    actor = await _seed_platform_actor(db_session)
    service = WorkspaceEntitlementOverrideService(db_session)

    await service.upsert_override(
        workspace_id=auth_context.workspace_id,
        entitlement_key=EntitlementKey.tokens.value,
        mode="limit",
        quantity=100,
        reason="Primeira definição.",
        actor_user_id=actor.id,
    )
    await service.upsert_override(
        workspace_id=auth_context.workspace_id,
        entitlement_key=EntitlementKey.tokens.value,
        mode="limit",
        quantity=200,
        reason="Ajuste do limite negociado.",
        actor_user_id=actor.id,
    )

    rows = (
        await db_session.scalars(
            select(WorkspaceEntitlementOverride).where(
                WorkspaceEntitlementOverride.workspace_id == auth_context.workspace_id,
                WorkspaceEntitlementOverride.entitlement == EntitlementKey.tokens,
            )
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].quantity == 200
