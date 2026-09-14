from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.db.append_only import ImmutableRecordError
from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.entitlement import EntitlementKey, PlanEntitlement
from app.models.plan_policy import (
    PlanPolicyAuditEvent,
    PlanPolicyDraft,
    PlanPolicyRolloutMode,
    PlanPolicyVersion,
    WorkspacePlanPolicyAssignment,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.commercial_catalog import (
    WorkspaceContractCreateRequest,
    WorkspaceContractLineWrite,
)
from app.schemas.plan_policy import (
    FEATURE_ENTITLEMENTS,
    PlanPolicyDraftWrite,
    PlanPolicyEntitlementSetting,
    PlanPolicyParameters,
    PlanPolicyPreviewRequest,
    PlanPolicyPublishRequest,
    PlanPolicyRolloutRule,
    PlanPolicySnapshot,
)
from app.services import plan_policy_service as plan_policy_service_module
from app.services.commercial_catalog_service import CommercialCatalogService
from app.services.plan_policy_service import (
    CATALOG_AUTHORITY_LOCK_NAMESPACE,
    CATALOG_AUTHORITY_LOCK_VALUE,
    PlanPolicyConflictError,
    PlanPolicyService,
)
from app.services.workspace_service import WorkspaceService

NOW = datetime(2026, 7, 22, 15, 0, tzinfo=UTC)


def _snapshot(
    *,
    token_limit: int = 500_000,
    calendar: bool = False,
    name: str = "Plano governado",
    campaign_recipient_limit: int | None = 1_000,
) -> PlanPolicySnapshot:
    entitlements: dict[EntitlementKey, PlanPolicyEntitlementSetting] = {}
    for key in EntitlementKey:
        if key in FEATURE_ENTITLEMENTS:
            entitlements[key] = PlanPolicyEntitlementSetting(
                enabled=(calendar if key is EntitlementKey.calendar else False),
                limit_quantity=None,
                hard_limit=True,
            )
        else:
            entitlements[key] = PlanPolicyEntitlementSetting(
                enabled=True,
                limit_quantity=(token_limit if key is EntitlementKey.tokens else 10),
                hard_limit=True,
            )
    return PlanPolicySnapshot(
        name=name,
        description="Snapshot integral de teste",
        price_monthly_cents=29_700,
        price_annually_cents=285_120,
        integrations=["whatsapp"],
        highlights=["Política versionada"],
        parameters=PlanPolicyParameters(
            max_campaign_recipients_per_month=campaign_recipient_limit,
        ),
        entitlements=entitlements,
    )


async def _plan(db_session) -> Plan:
    plan = Plan(
        name="Starter legado",
        tier=PlanTier.starter,
        description="Plano antes da política versionada",
        price_monthly_cents=29_700,
        price_annually_cents=285_120,
        max_users=3,
        max_matters=200,
        max_tokens_per_month=500_000,
        max_ai_agents=2,
        features={},
        is_active=True,
        is_public=True,
        sort_order=1,
    )
    db_session.add(plan)
    await db_session.flush()
    return plan


@pytest.mark.asyncio
async def test_legacy_policy_publish_is_blocked_after_canonical_catalog_activation(
    db_session,
    monkeypatch,
    platform_auth_context,
) -> None:
    plan = await _plan(db_session)
    await CommercialCatalogService(db_session).bootstrap_default_catalog(
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="policy-canonical-bootstrap",
    )
    lock_calls: list[tuple[str, str]] = []

    async def capture_catalog_fence(_db, *, namespace: str, value: str) -> None:
        lock_calls.append((namespace, value))

    monkeypatch.setattr(
        plan_policy_service_module,
        "acquire_transaction_advisory_lock",
        capture_catalog_fence,
    )

    with pytest.raises(PlanPolicyConflictError, match="catálogo canônico"):
        await PlanPolicyService(db_session, clock=lambda: NOW).publish(
            plan_id=plan.id,
            payload=PlanPolicyPublishRequest(
                rollout_mode=PlanPolicyRolloutMode.existing_and_new,
                effective_at=NOW,
                snapshot=_snapshot(),
                reason="Publicação legada deve falhar fechada.",
            ),
            actor_user_id=platform_auth_context.user_id,
            idempotency_key="policy-legacy-blocked",
        )

    with pytest.raises(PlanPolicyConflictError, match="catálogo canônico"):
        await PlanPolicyService(db_session, clock=lambda: NOW).save_draft(
            plan_id=plan.id,
            payload=PlanPolicyDraftWrite(snapshot=_snapshot()),
            actor_user_id=platform_auth_context.user_id,
        )
    with pytest.raises(PlanPolicyConflictError, match="catálogo canônico"):
        await PlanPolicyService(
            db_session, clock=lambda: NOW
        ).materialize_due_assignments(
            plan_id=plan.id,
            actor_user_id=platform_auth_context.user_id,
        )
    assert (
        int(
            await db_session.scalar(
                select(func.count(PlanPolicyDraft.id)).where(
                    PlanPolicyDraft.plan_id == plan.id
                )
            )
            or 0
        )
        == 0
    )
    assert lock_calls == [
        (CATALOG_AUTHORITY_LOCK_NAMESPACE, CATALOG_AUTHORITY_LOCK_VALUE),
        (CATALOG_AUTHORITY_LOCK_NAMESPACE, CATALOG_AUTHORITY_LOCK_VALUE),
        (CATALOG_AUTHORITY_LOCK_NAMESPACE, CATALOG_AUTHORITY_LOCK_VALUE),
    ]


async def _account(
    db_session,
    *,
    plan_id: UUID,
    label: str,
    created_at: datetime,
    setup_mode: WorkspaceSetupMode = WorkspaceSetupMode.self_service,
    status: WorkspaceStatus = WorkspaceStatus.active,
) -> Workspace:
    workspace = Workspace(
        name=f"Conta {label}",
        slug=f"conta-{label}-{uuid4().hex[:8]}",
        status=status,
        setup_mode=setup_mode,
        is_active=status is WorkspaceStatus.active,
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
            amount_cents=29_700,
            currency="BRL",
            created_at=created_at,
            updated_at=created_at,
        )
    )
    await db_session.flush()
    return workspace


@pytest.mark.asyncio
async def test_publication_is_immutable_idempotent_and_resolves_new_only_per_workspace(
    db_session,
    platform_auth_context,
) -> None:
    plan = await _plan(db_session)
    existing_a = await _account(
        db_session,
        plan_id=plan.id,
        label="existente-a",
        created_at=NOW - timedelta(days=30),
    )
    existing_b = await _account(
        db_session,
        plan_id=plan.id,
        label="existente-b",
        created_at=NOW - timedelta(days=10),
    )
    service = PlanPolicyService(db_session, clock=lambda: NOW)

    draft = await service.save_draft(
        plan_id=plan.id,
        payload=PlanPolicyDraftWrite(snapshot=_snapshot(token_limit=500_000)),
        actor_user_id=platform_auth_context.user_id,
    )
    replayed_draft = await service.save_draft(
        plan_id=plan.id,
        payload=PlanPolicyDraftWrite(snapshot=_snapshot(token_limit=500_000)),
        actor_user_id=platform_auth_context.user_id,
        expected_etag="stale-etag-is-ignored-for-an-identical-put",
    )
    assert replayed_draft.id == draft.id
    assert replayed_draft.revision == 1

    preview = await service.preview(
        plan_id=plan.id,
        rollout=PlanPolicyPreviewRequest(
            rollout_mode=PlanPolicyRolloutMode.existing_and_new,
            effective_at=NOW,
        ),
    )
    assert preview.affected_count == 2
    assert {item.workspace_id for item in preview.accounts} == {
        existing_a.id,
        existing_b.id,
    }

    first_request = PlanPolicyPublishRequest(
        rollout_mode=PlanPolicyRolloutMode.existing_and_new,
        effective_at=NOW,
        draft_etag=draft.etag,
        reason="Publicação inicial controlada",
    )
    first = await service.publish(
        plan_id=plan.id,
        payload=first_request,
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="publish-starter-v1",
    )
    replay = await service.publish(
        plan_id=plan.id,
        payload=first_request,
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="publish-starter-v1",
    )
    assert first.policy.version == 1
    assert first.assignments_created == 2
    assert replay.policy.id == first.policy.id
    assert replay.idempotent_replay is True
    assert (
        await db_session.scalar(
            select(func.count(PlanPolicyVersion.id)).where(PlanPolicyVersion.plan_id == plan.id)
        )
        == 1
    )

    # The local catalog is the read model used by catalog, quote, and checkout
    # endpoints. A global immediate publication must keep it in lockstep with
    # the immutable policy snapshot.
    published_plan = await db_session.get(Plan, plan.id)
    assert published_plan is not None
    assert published_plan.name == "Plano governado"
    assert published_plan.description == "Snapshot integral de teste"
    assert published_plan.max_users == 10
    published_tokens = await db_session.scalar(
        select(PlanEntitlement).where(
            PlanEntitlement.plan_id == plan.id,
            PlanEntitlement.entitlement == EntitlementKey.tokens,
        )
    )
    assert published_tokens is not None
    assert published_tokens.limit_quantity == 500_000

    with pytest.raises(PlanPolicyConflictError, match="Idempotency-Key"):
        await service.publish(
            plan_id=plan.id,
            payload=first_request.model_copy(update={"reason": "Payload divergente"}),
            actor_user_id=platform_auth_context.user_id,
            idempotency_key="publish-starter-v1",
        )

    second = await service.publish(
        plan_id=plan.id,
        payload=PlanPolicyPublishRequest(
            rollout_mode=PlanPolicyRolloutMode.new_only,
            effective_at=NOW,
            snapshot=_snapshot(token_limit=900_000, calendar=True),
            reason="Nova franquia apenas para contas novas",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="publish-starter-v2-new",
    )
    assert second.policy.version == 2
    assert second.affected_count == 0
    assert second.assignments_created == 0

    new_account = await _account(
        db_session,
        plan_id=plan.id,
        label="nova",
        created_at=NOW + timedelta(seconds=1),
    )
    new_resolution = await service.resolve_workspace(
        workspace_id=new_account.id,
        effective_at=NOW + timedelta(seconds=2),
    )
    existing_resolution = await service.resolve_workspace(
        workspace_id=existing_a.id,
        effective_at=NOW + timedelta(seconds=2),
    )
    assert new_resolution.policy_version == 2
    assert new_resolution.entitlements[EntitlementKey.tokens].limit_quantity == 900_000
    assert new_resolution.entitlements[EntitlementKey.calendar].enabled is True
    assert existing_resolution.policy_version == 1
    assert existing_resolution.entitlements[EntitlementKey.tokens].limit_quantity == 500_000

    # A new-only publication changes the versioned resolution for future
    # accounts, but must not overwrite the global billing read model.
    unchanged_plan = await db_session.get(Plan, plan.id)
    assert unchanged_plan is not None
    assert unchanged_plan.name == "Plano governado"
    unchanged_tokens = await db_session.scalar(
        select(PlanEntitlement).where(
            PlanEntitlement.plan_id == plan.id,
            PlanEntitlement.entitlement == EntitlementKey.tokens,
        )
    )
    assert unchanged_tokens is not None
    assert unchanged_tokens.limit_quantity == 500_000

    assignments = list(
        (
            await db_session.scalars(
                select(WorkspacePlanPolicyAssignment).where(
                    WorkspacePlanPolicyAssignment.plan_id == plan.id
                )
            )
        ).all()
    )
    assert len(assignments) == 3
    assert len({(row.workspace_id, row.policy_version_id) for row in assignments}) == 3
    assert (
        await db_session.scalar(
            select(func.count(PlanPolicyAuditEvent.id)).where(
                PlanPolicyAuditEvent.plan_id == plan.id,
                PlanPolicyAuditEvent.event_type == "policy_published",
            )
        )
        == 2
    )


@pytest.mark.asyncio
async def test_scheduled_and_rule_rollouts_materialize_real_assignments(
    db_session,
    platform_auth_context,
) -> None:
    plan = await _plan(db_session)
    assisted = await _account(
        db_session,
        plan_id=plan.id,
        label="assistida",
        created_at=NOW - timedelta(days=20),
        setup_mode=WorkspaceSetupMode.assisted,
    )
    self_service = await _account(
        db_session,
        plan_id=plan.id,
        label="self-service",
        created_at=NOW - timedelta(days=20),
    )
    service = PlanPolicyService(db_session, clock=lambda: NOW)

    baseline = await service.publish(
        plan_id=plan.id,
        payload=PlanPolicyPublishRequest(
            rollout_mode=PlanPolicyRolloutMode.existing_and_new,
            effective_at=NOW,
            snapshot=_snapshot(token_limit=100),
            reason="Baseline",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="scheduled-baseline-v1",
    )
    scheduled_at = NOW + timedelta(days=1)
    scheduled = await service.publish(
        plan_id=plan.id,
        payload=PlanPolicyPublishRequest(
            rollout_mode=PlanPolicyRolloutMode.scheduled,
            effective_at=scheduled_at,
            snapshot=_snapshot(token_limit=200),
            reason="Mudança agendada",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="scheduled-policy-v2",
    )
    assert baseline.assignments_created == 2
    assert scheduled.assignments_created == 0

    before = await service.resolve_workspace(
        workspace_id=assisted.id,
        effective_at=scheduled_at - timedelta(seconds=1),
    )
    after = await service.resolve_workspace(
        workspace_id=assisted.id,
        effective_at=scheduled_at + timedelta(seconds=1),
    )
    assert before.policy_version == 1
    assert after.policy_version == 2

    rule = PlanPolicyRolloutRule(
        setup_modes=[WorkspaceSetupMode.assisted],
        workspace_statuses=[WorkspaceStatus.active],
    )
    rule_preview = await service.preview(
        plan_id=plan.id,
        rollout=PlanPolicyPreviewRequest(
            rollout_mode=PlanPolicyRolloutMode.rule,
            effective_at=scheduled_at + timedelta(seconds=2),
            rule=rule,
        ),
    )
    assert rule_preview.affected_count == 1
    assert rule_preview.accounts[0].workspace_id == assisted.id

    ruled = await service.publish(
        plan_id=plan.id,
        payload=PlanPolicyPublishRequest(
            rollout_mode=PlanPolicyRolloutMode.rule,
            effective_at=scheduled_at + timedelta(seconds=2),
            rule=rule,
            snapshot=_snapshot(token_limit=300),
            reason="Cohort assistido",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="rule-policy-v3-assisted",
    )
    assert ruled.assignments_created == 0

    materialized = await service.materialize_due_assignments(
        plan_id=plan.id,
        actor_user_id=platform_auth_context.user_id,
        effective_at=scheduled_at + timedelta(seconds=3),
    )
    assert materialized.evaluated_accounts == 2
    assert materialized.assignments_created == 2

    assisted_resolution = await service.resolve_workspace(
        workspace_id=assisted.id,
        effective_at=scheduled_at + timedelta(seconds=3),
    )
    self_resolution = await service.resolve_workspace(
        workspace_id=self_service.id,
        effective_at=scheduled_at + timedelta(seconds=3),
    )
    assert assisted_resolution.policy_version == 3
    assert self_resolution.policy_version == 2


@pytest.mark.asyncio
async def test_canonical_workspace_never_materializes_legacy_assignment_while_unreconciled_reads(
    db_session,
    platform_auth_context,
) -> None:
    plan = await _plan(db_session)
    reconciled = await _account(
        db_session,
        plan_id=plan.id,
        label="reconciliada",
        created_at=NOW - timedelta(days=3),
    )
    unreconciled = await _account(
        db_session,
        plan_id=plan.id,
        label="ainda-legada",
        created_at=NOW - timedelta(days=2),
    )
    effective_at = NOW + timedelta(days=1)
    legacy = PlanPolicyService(db_session, clock=lambda: NOW)
    published = await legacy.publish(
        plan_id=plan.id,
        payload=PlanPolicyPublishRequest(
            rollout_mode=PlanPolicyRolloutMode.scheduled,
            effective_at=effective_at,
            snapshot=_snapshot(token_limit=321),
            reason="Política agendada antes da ativação canônica.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="scheduled-before-canonical",
    )
    assert published.assignments_created == 0

    catalog = CommercialCatalogService(db_session)
    offers, _ = await catalog.bootstrap_default_catalog(
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="canonical-after-scheduled-policy",
    )
    start = next(item for item in offers if item.sku == "crm_start")
    await catalog.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=reconciled.id,
            lines=[
                WorkspaceContractLineWrite(
                    sku=start.sku,
                    policy_version_id=start.id,
                )
            ],
            reason="Conta reconciliada no contrato canônico.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="canonical-reconciled-workspace",
    )

    canonical_resolution = await legacy.resolve_versioned_workspace(
        workspace_id=reconciled.id,
        effective_at=effective_at + timedelta(seconds=1),
    )
    assert canonical_resolution is None
    with pytest.raises(PlanPolicyConflictError, match="manifesto efetivo"):
        await legacy.resolve_workspace(
            workspace_id=reconciled.id,
            effective_at=effective_at + timedelta(seconds=1),
        )

    legacy_resolution = await legacy.resolve_versioned_workspace(
        workspace_id=unreconciled.id,
        effective_at=effective_at + timedelta(seconds=1),
    )
    assert legacy_resolution is not None
    assert legacy_resolution.policy_version == 1
    assert legacy_resolution.assignment_id is None
    assert (
        int(
            await db_session.scalar(
                select(func.count(WorkspacePlanPolicyAssignment.id)).where(
                    WorkspacePlanPolicyAssignment.workspace_id.in_(
                        [reconciled.id, unreconciled.id]
                    )
                )
            )
            or 0
        )
        == 0
    )


@pytest.mark.asyncio
async def test_resolution_falls_back_to_real_legacy_entitlements_before_first_policy(
    db_session,
) -> None:
    plan = await _plan(db_session)
    workspace = await _account(
        db_session,
        plan_id=plan.id,
        label="legada",
        created_at=NOW - timedelta(days=1),
    )
    db_session.add_all(
        [
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=key,
                enabled=True,
                limit_quantity=(123 if key is EntitlementKey.tokens else None),
                hard_limit=True,
            )
            for key in EntitlementKey
        ]
    )
    await db_session.flush()

    resolution = await PlanPolicyService(db_session, clock=lambda: NOW).resolve_workspace(
        workspace_id=workspace.id
    )

    assert resolution.source == "legacy_plan_entitlement"
    assert resolution.policy_version is None
    assert resolution.assignment_id is None
    assert resolution.entitlements[EntitlementKey.tokens].limit_quantity == 123


@pytest.mark.asyncio
async def test_published_versions_reject_mutation(db_session, platform_auth_context) -> None:
    plan = await _plan(db_session)
    service = PlanPolicyService(db_session, clock=lambda: NOW)
    result = await service.publish(
        plan_id=plan.id,
        payload=PlanPolicyPublishRequest(
            rollout_mode=PlanPolicyRolloutMode.existing_and_new,
            effective_at=NOW,
            snapshot=_snapshot(),
            reason="Histórico protegido",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="immutable-policy-v1",
    )
    await db_session.flush()

    result.policy.publication_reason = "Tentativa de sobrescrita"
    with pytest.raises(ImmutableRecordError, match="immutable"):
        await db_session.flush()


@pytest.mark.asyncio
async def test_workspace_plan_summary_uses_the_effective_published_policy(
    db_session,
    platform_auth_context,
) -> None:
    plan = await _plan(db_session)
    workspace = await _account(
        db_session,
        plan_id=plan.id,
        label="resumo-versionado",
        created_at=NOW - timedelta(days=2),
    )
    service = PlanPolicyService(db_session, clock=lambda: NOW)
    await service.publish(
        plan_id=plan.id,
        payload=PlanPolicyPublishRequest(
            rollout_mode=PlanPolicyRolloutMode.existing_and_new,
            effective_at=NOW,
            snapshot=_snapshot(token_limit=700_000, calendar=True),
            reason="Resumo da conta deve refletir o pacote publicado",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="summary-versioned-v1",
    )

    summary = await WorkspaceService(db_session)._build_plan_summary(workspace)

    assert summary is not None
    assert summary.max_users == 10
    assert summary.max_ai_conversations_per_month == 10
    assert summary.max_campaign_recipients_per_month == 1_000
    assert summary.campaign_recipients_used_this_month == 0
    assert summary.campaign_recipients_remaining_this_month == 1_000
    assert summary.has_calendar is True
