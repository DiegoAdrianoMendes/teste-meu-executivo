from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.models.ai_token_package import (
    AiTokenPackage,
    AiTokenPackageRequestStatus,
    WorkspaceAiTokenPackageRequest,
)
from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
    TokenBudget,
)
from app.models.entitlement import EntitlementKey, PlanEntitlement
from app.models.plan_policy import (
    PlanPolicyRolloutMode,
    WorkspacePlanPolicyAssignment,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.plan_policy import (
    FEATURE_ENTITLEMENTS,
    PlanPolicyEntitlementSetting,
    PlanPolicyPublishRequest,
    PlanPolicyRolloutRule,
    PlanPolicySnapshot,
)
from app.services.entitlement_service import EntitlementLimitSource, EntitlementResolver
from app.services.plan_policy_service import PlanPolicyService


def _snapshot(*, users: int, tokens: int = 1_000) -> PlanPolicySnapshot:
    entitlements: dict[EntitlementKey, PlanPolicyEntitlementSetting] = {}
    for key in EntitlementKey:
        if key in FEATURE_ENTITLEMENTS:
            entitlements[key] = PlanPolicyEntitlementSetting(
                enabled=False,
                limit_quantity=None,
                hard_limit=True,
            )
        else:
            if key is EntitlementKey.tokens:
                limit = tokens
            elif key is EntitlementKey.users:
                limit = users
            else:
                limit = 10
            entitlements[key] = PlanPolicyEntitlementSetting(
                enabled=True,
                limit_quantity=limit,
                hard_limit=True,
            )
    return PlanPolicySnapshot(
        name="Runtime plan policy",
        price_monthly_cents=10_000,
        price_annually_cents=100_000,
        entitlements=entitlements,
    )


async def _plan(db_session) -> Plan:
    plan = Plan(
        name="Runtime legacy plan",
        tier=PlanTier.pro,
        price_monthly_cents=10_000,
        price_annually_cents=100_000,
        max_users=2,
        max_matters=20,
        max_tokens_per_month=100,
        max_ai_agents=2,
        features={},
        is_active=True,
        is_public=True,
        sort_order=1,
    )
    db_session.add(plan)
    await db_session.flush()
    db_session.add_all(
        [
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=EntitlementKey.users,
                enabled=True,
                limit_quantity=2,
                hard_limit=True,
            ),
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=EntitlementKey.tokens,
                enabled=True,
                limit_quantity=100,
                hard_limit=True,
            ),
        ]
    )
    await db_session.flush()
    return plan


async def _workspace(
    db_session,
    *,
    plan_id: UUID,
    label: str,
    created_at: datetime,
    setup_mode: WorkspaceSetupMode = WorkspaceSetupMode.self_service,
) -> Workspace:
    workspace = Workspace(
        name=f"Runtime {label}",
        slug=f"runtime-{label}-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=setup_mode,
        is_active=True,
        settings={},
        created_at=created_at,
        updated_at=created_at,
    )
    db_session.add(workspace)
    await db_session.flush()
    db_session.add(
        Subscription(
            workspace_id=workspace.id,
            plan_id=plan_id,
            status=SubscriptionStatus.active,
            interval=BillingInterval.monthly,
            amount_cents=10_000,
            currency="BRL",
            created_at=created_at,
            updated_at=created_at,
        )
    )
    await db_session.flush()
    return workspace


async def _publish(
    db_session,
    *,
    plan_id: UUID,
    actor_user_id: UUID,
    mode: PlanPolicyRolloutMode,
    effective_at: datetime,
    users: int,
    key: str,
    rule: PlanPolicyRolloutRule | None = None,
) -> None:
    await PlanPolicyService(db_session).publish(
        plan_id=plan_id,
        payload=PlanPolicyPublishRequest(
            rollout_mode=mode,
            effective_at=effective_at,
            rule=rule,
            snapshot=_snapshot(users=users),
            reason=f"Runtime test {mode.value}",
        ),
        actor_user_id=actor_user_id,
        idempotency_key=key,
    )


@pytest.mark.asyncio
async def test_entitlement_resolver_uses_existing_and_new_policy_then_token_overlays(
    db_session,
    platform_auth_context,
    auth_context,
) -> None:
    now = datetime.now(UTC)
    plan = await _plan(db_session)
    existing_workspace = await _workspace(
        db_session,
        plan_id=plan.id,
        label="existing-before-publication",
        created_at=now - timedelta(days=1),
    )
    await _publish(
        db_session,
        plan_id=plan.id,
        actor_user_id=platform_auth_context.user_id,
        mode=PlanPolicyRolloutMode.existing_and_new,
        effective_at=now - timedelta(minutes=1),
        users=9,
        key="runtime-existing-new-v1",
    )
    workspace = await _workspace(
        db_session,
        plan_id=plan.id,
        label="created-after-publication",
        created_at=now,
    )
    resolver = EntitlementResolver(db_session)

    existing_users = await resolver.resolve(existing_workspace.id, EntitlementKey.users)
    users = await resolver.resolve(workspace.id, EntitlementKey.users)
    assert existing_users.limit_quantity == 9
    assert existing_users.source == EntitlementLimitSource.plan_policy_version
    assert users.limit_quantity == 9
    assert users.source == EntitlementLimitSource.plan_policy_version
    assignment_count = await db_session.scalar(
        select(func.count(WorkspacePlanPolicyAssignment.id)).where(
            WorkspacePlanPolicyAssignment.plan_id == plan.id
        )
    )
    # Publication materializes the pre-existing account. Runtime entitlement
    # reads for accounts created later are intentionally read-only and resolve
    # the matching version without writing a new assignment.
    assert assignment_count == 1

    package = AiTokenPackage(
        key="runtime-extra-tokens",
        name="Runtime extra tokens",
        token_quantity=250,
        commercial_price_cents=1_000,
        currency="BRL",
        is_active=True,
    )
    db_session.add(package)
    await db_session.flush()
    today = datetime.now(UTC).date()
    db_session.add(
        WorkspaceAiTokenPackageRequest(
            workspace_id=workspace.id,
            package_id=package.id,
            requested_by_user_id=auth_context.user_id,
            decided_by_user_id=platform_auth_context.user_id,
            idempotency_key="runtime-extra-token-grant",
            status=AiTokenPackageRequestStatus.approved,
            package_key_snapshot=package.key,
            package_name_snapshot=package.name,
            token_quantity_snapshot=250,
            commercial_price_cents_snapshot=1_000,
            currency_snapshot="BRL",
            period_start=today,
            period_end=today + timedelta(days=30),
            decided_at=now,
        )
    )
    await db_session.flush()

    package_overlay = await resolver.resolve(workspace.id, EntitlementKey.tokens)
    assert package_overlay.limit_quantity == 1_250
    assert package_overlay.source == EntitlementLimitSource.token_package

    db_session.add(
        TokenBudget(
            workspace_id=workspace.id,
            monthly_limit=900,
            alert_at_pct=80,
            hard_limit=True,
        )
    )
    await db_session.flush()
    budget_overlay = await resolver.resolve(workspace.id, EntitlementKey.tokens)
    assert budget_overlay.limit_quantity == 900
    assert budget_overlay.source == EntitlementLimitSource.token_budget
    assert (
        await db_session.scalar(
            select(func.count(WorkspacePlanPolicyAssignment.id)).where(
                WorkspacePlanPolicyAssignment.workspace_id == workspace.id
            )
        )
        == 0
    )


@pytest.mark.asyncio
async def test_entitlement_resolver_keeps_existing_accounts_legacy_for_new_only(
    db_session,
    platform_auth_context,
) -> None:
    now = datetime.now(UTC)
    effective_at = now - timedelta(hours=1)
    plan = await _plan(db_session)
    existing = await _workspace(
        db_session,
        plan_id=plan.id,
        label="new-only-existing",
        created_at=effective_at - timedelta(days=1),
    )
    new_account = await _workspace(
        db_session,
        plan_id=plan.id,
        label="new-only-new",
        created_at=effective_at + timedelta(minutes=1),
    )
    await _publish(
        db_session,
        plan_id=plan.id,
        actor_user_id=platform_auth_context.user_id,
        mode=PlanPolicyRolloutMode.new_only,
        effective_at=effective_at,
        users=11,
        key="runtime-new-only-v1",
    )
    resolver = EntitlementResolver(db_session)

    existing_value = await resolver.resolve(existing.id, EntitlementKey.users)
    new_value = await resolver.resolve(new_account.id, EntitlementKey.users)

    assert existing_value.limit_quantity == 2
    assert existing_value.source == EntitlementLimitSource.plan_entitlement
    assert new_value.limit_quantity == 11
    assert new_value.source == EntitlementLimitSource.plan_policy_version


@pytest.mark.asyncio
async def test_entitlement_resolver_activates_due_scheduled_policy(
    db_session,
    platform_auth_context,
) -> None:
    now = datetime.now(UTC)
    plan = await _plan(db_session)
    workspace = await _workspace(
        db_session,
        plan_id=plan.id,
        label="scheduled",
        created_at=now - timedelta(days=5),
    )
    await _publish(
        db_session,
        plan_id=plan.id,
        actor_user_id=platform_auth_context.user_id,
        mode=PlanPolicyRolloutMode.scheduled,
        effective_at=now + timedelta(days=1),
        users=12,
        key="runtime-scheduled-future-v1",
    )

    before_schedule = await EntitlementResolver(db_session).resolve(
        workspace.id, EntitlementKey.users
    )
    assert before_schedule.limit_quantity == 2
    assert before_schedule.source == EntitlementLimitSource.plan_entitlement

    await _publish(
        db_session,
        plan_id=plan.id,
        actor_user_id=platform_auth_context.user_id,
        mode=PlanPolicyRolloutMode.scheduled,
        effective_at=now - timedelta(seconds=1),
        users=14,
        key="runtime-scheduled-due-v2",
    )
    resolved = await EntitlementResolver(db_session).resolve(workspace.id, EntitlementKey.users)

    assert resolved.limit_quantity == 14
    assert resolved.source == EntitlementLimitSource.plan_policy_version


@pytest.mark.asyncio
async def test_entitlement_resolver_applies_rule_only_to_matching_workspace(
    db_session,
    platform_auth_context,
) -> None:
    now = datetime.now(UTC)
    plan = await _plan(db_session)
    assisted = await _workspace(
        db_session,
        plan_id=plan.id,
        label="rule-assisted",
        created_at=now - timedelta(days=5),
        setup_mode=WorkspaceSetupMode.assisted,
    )
    self_service = await _workspace(
        db_session,
        plan_id=plan.id,
        label="rule-self-service",
        created_at=now - timedelta(days=5),
    )
    await _publish(
        db_session,
        plan_id=plan.id,
        actor_user_id=platform_auth_context.user_id,
        mode=PlanPolicyRolloutMode.rule,
        effective_at=now - timedelta(minutes=1),
        users=13,
        key="runtime-rule-assisted-v1",
        rule=PlanPolicyRolloutRule(setup_modes=[WorkspaceSetupMode.assisted]),
    )
    resolver = EntitlementResolver(db_session)

    assisted_value = await resolver.resolve(assisted.id, EntitlementKey.users)
    self_service_value = await resolver.resolve(self_service.id, EntitlementKey.users)

    assert assisted_value.limit_quantity == 13
    assert assisted_value.source == EntitlementLimitSource.plan_policy_version
    assert self_service_value.limit_quantity == 2
    assert self_service_value.source == EntitlementLimitSource.plan_entitlement
