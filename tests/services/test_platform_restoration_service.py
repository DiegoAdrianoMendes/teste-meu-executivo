from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.configuration import PlatformConfigDocument
from app.models.identity import UserSession
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.schemas.commercial_catalog import (
    CommercialContractStatus,
    WorkspaceContractCreateRequest,
    WorkspaceContractLineWrite,
)
from app.schemas.platform_audit_event import PlatformAuditEventCreate
from app.services.commercial_catalog_service import CommercialCatalogService
from app.services.commercial_legacy_bridge_service import CanonicalContractCommand
from app.services.platform_audit_event_service import PlatformAuditEventService
from app.services.platform_restoration_service import (
    PlatformRestorationError,
    PlatformRestorationService,
)


async def _workspace(db_session, suffix: str) -> Workspace:
    workspace = Workspace(
        name=f"Restore workspace {suffix}",
        slug=f"restore-{suffix}-{uuid4().hex[:8]}",
        status=WorkspaceStatus.suspended,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=False,
        onboarding_completed=False,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    return workspace


async def _customer_user(db_session, suffix: str) -> User:
    user = User(
        name=f"Restore customer {suffix}",
        email=f"restore-customer-{suffix}-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="RC",
        color="#111827",
        is_active=True,
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _staff_user(db_session, suffix: str) -> User:
    user = User(
        name=f"Restore staff {suffix}",
        email=f"restore-staff-{suffix}-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="RS",
        color="#111827",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=False,
        platform_role_package="marketing_content",
        platform_access_active=True,
        platform_permission_grants=[],
        platform_permission_denials=[],
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _plan(db_session, *, name: str, tier: PlanTier, price: int) -> Plan:
    plan = Plan(
        name=name,
        tier=tier,
        description=f"Descrição {name}",
        price_monthly_cents=price,
        price_annually_cents=price * 10,
        max_users=10,
        max_matters=500,
        max_tokens_per_month=2_000_000,
        max_ai_agents=12,
        features={"audit": True},
        is_active=True,
        is_public=True,
        sort_order=1,
    )
    db_session.add(plan)
    await db_session.flush()
    return plan


@pytest.mark.asyncio
async def test_workspace_restoration_requires_preview_and_creates_new_audit_event(
    db_session, platform_auth_context
):
    actor = await db_session.get(User, platform_auth_context.user_id)
    assert actor is not None
    workspace = await _workspace(db_session, "success")
    source = await PlatformAuditEventService(db_session).record(PlatformAuditEventCreate(
        actor_id=actor.id, actor_role_snapshot=actor.platform_role_package or "platform_owner",
        is_platform_root=True, session_type="normal", workspace_id=workspace.id,
        module="customers", action="bulk.suspend", resource_type="workspace",
        resource_id=str(workspace.id), resource_label_snapshot=workspace.name,
        result="success",
        before={"name": workspace.name, "status": "active", "isActive": True, "onboardingCompleted": False, "planId": None},
        after={"name": workspace.name, "status": "suspended", "isActive": False, "onboardingCompleted": False, "planId": None},
        reason="Suspend for restoration test", reason_source="user",
        correlation_id=str(uuid4()), operation_id=str(uuid4()),
        resource_version_before=1, resource_version_after=2,
        route="test", method="POST",
    ))
    service = PlatformRestorationService(db_session)
    preview = await service.create_preview(
        actor=actor, source_audit_event_id=source.id,
        resource_type=None, resource_id=None, target_version=None,
    )
    assert preview.eligible is True
    assert {item["path"] for item in preview.diff} == {"isActive", "status"}
    assert {item["label"] for item in preview.diff} == {"Ativo", "Status"}

    job = await service.create_job(
        actor=actor, preview_id=preview.id,
        expected_resource_version=preview.current_version,
        reason="Restore account after confirmed operational correction",
        confirmation=f"RESTAURAR {workspace.id}",
        idempotency_key="restore-workspace-success",
    )
    result = await service.run_job(job.id)
    await db_session.refresh(workspace)

    assert result.status == "succeeded", (result.error_code, result.error_detail)
    assert workspace.status == WorkspaceStatus.active
    assert workspace.is_active is True


@pytest.mark.asyncio
async def test_restoration_rejects_resource_changed_after_preview(
    db_session, platform_auth_context
):
    actor = await db_session.get(User, platform_auth_context.user_id)
    assert actor is not None
    workspace = await _workspace(db_session, "conflict")
    source = await PlatformAuditEventService(db_session).record(PlatformAuditEventCreate(
        actor_id=actor.id, actor_role_snapshot=actor.platform_role_package or "platform_owner",
        is_platform_root=True, session_type="normal", workspace_id=workspace.id,
        module="customers", action="suspended", resource_type="workspace",
        resource_id=str(workspace.id), resource_label_snapshot=workspace.name,
        result="success", before={"status": "active", "isActive": True},
        after={"status": "suspended", "isActive": False}, reason="Source event",
        reason_source="user", correlation_id=str(uuid4()), operation_id=str(uuid4()),
        route="test", method="POST",
    ))
    service = PlatformRestorationService(db_session)
    preview = await service.create_preview(
        actor=actor, source_audit_event_id=source.id,
        resource_type=None, resource_id=None, target_version=None,
    )
    workspace.name = "Changed after preview"
    await db_session.flush()

    with pytest.raises(PlatformRestorationError) as captured:
        await service.create_job(
            actor=actor, preview_id=preview.id,
            expected_resource_version=preview.current_version,
            reason="Attempt stale restoration after concurrent edit",
            confirmation=f"RESTAURAR {workspace.id}",
            idempotency_key="restore-workspace-conflict",
        )
    assert captured.value.code == "PLATFORM_RESTORE_PREVIEW_STALE"


@pytest.mark.asyncio
async def test_staff_authority_restoration_revokes_all_sessions_and_audits(
    db_session, platform_auth_context
):
    actor = await db_session.get(User, platform_auth_context.user_id)
    assert actor is not None
    staff = await _staff_user(db_session, "authority")
    now = datetime.now(UTC)
    session = UserSession(
        user_id=staff.id,
        refresh_jti_digest=uuid4().hex + uuid4().hex,
        device_label="Staff authority session",
        device_type="desktop",
        browser="Chrome",
        operating_system="macOS",
        last_workspace_id=None,
        last_seen_at=now,
        expires_at=now + timedelta(hours=1),
    )
    db_session.add(session)
    await db_session.flush()
    source = await PlatformAuditEventService(db_session).record(
        PlatformAuditEventCreate(
            actor_id=actor.id,
            actor_role_snapshot=actor.platform_role_package or "platform_owner",
            is_platform_root=True,
            session_type="normal",
            module="staff",
            action="authority.changed",
            resource_type="staff",
            resource_id=str(staff.id),
            resource_label_snapshot=staff.name,
            result="success",
            before={
                "rolePackage": "support",
                "grants": [{"module": "support", "action": "export"}],
                "denials": [],
                "platformAccessActive": True,
                "isActive": True,
            },
            after={
                "rolePackage": "marketing_content",
                "grants": [],
                "denials": [],
                "platformAccessActive": True,
                "isActive": True,
            },
            reason="Mudança de autoridade anterior ao teste de restauração.",
            reason_source="user",
            correlation_id=str(uuid4()),
            operation_id=str(uuid4()),
            route="test",
            method="PATCH",
        )
    )
    service = PlatformRestorationService(db_session)
    preview = await service.create_preview(
        actor=actor,
        source_audit_event_id=source.id,
        resource_type=None,
        resource_id=None,
        target_version=None,
    )
    assert preview.eligible is True
    assert preview.impact["sessionsWillBeRevoked"] is True

    epoch_before = staff.auth_epoch
    job = await service.create_job(
        actor=actor,
        preview_id=preview.id,
        expected_resource_version=preview.current_version,
        reason="Restaurar pacote de autoridade aprovado do funcionário.",
        confirmation=f"RESTAURAR {staff.id}",
        idempotency_key="restore-staff-authority-success",
    )
    result = await service.run_job(job.id)
    await db_session.refresh(staff)
    await db_session.refresh(session)

    assert result.status == "succeeded"
    assert staff.platform_role_package == "support"
    assert staff.platform_permission_grants == [
        {"module": "support", "action": "export"}
    ]
    assert staff.auth_epoch == epoch_before + 1
    assert session.revoked_at is not None
    assert session.revocation_reason == "platform_staff_authority_restored"

    restored = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_type == "staff",
            PlatformAuditEvent.resource_id == str(staff.id),
            PlatformAuditEvent.action == "restored",
        )
    )
    assert restored is not None
    assert restored.restoration_of_event_id == source.id


@pytest.mark.asyncio
async def test_plan_catalog_restoration_reapplies_reversible_bundle(
    db_session, platform_auth_context
):
    actor = await db_session.get(User, platform_auth_context.user_id)
    assert actor is not None
    plan = await _plan(db_session, name="Plano atual", tier=PlanTier.pro, price=69700)
    source = await PlatformAuditEventService(db_session).record(
        PlatformAuditEventCreate(
            actor_id=actor.id,
            actor_role_snapshot="platform_owner",
            is_platform_root=True,
            session_type="normal",
            module="plans",
            action="plan.updated",
            resource_type="plan",
            resource_id=str(plan.id),
            resource_label_snapshot=plan.name,
            result="success",
            before={
                "name": "Plano anterior",
                "description": "Configuração comercial anterior",
                "priceMonthlyCents": 59700,
                "priceAnnuallyCents": 597000,
                "maxUsers": 8,
                "maxMatters": 400,
                "maxTokensPerMonth": 1_500_000,
                "maxAiAgents": 10,
                "features": {"audit": False},
                "isActive": True,
                "isPublic": False,
                "sortOrder": 2,
            },
            after={"name": plan.name, "priceMonthlyCents": plan.price_monthly_cents},
            reason="Atualização anterior do catálogo.",
            reason_source="user",
            correlation_id=str(uuid4()),
            operation_id=str(uuid4()),
            route="test",
            method="PATCH",
        )
    )
    service = PlatformRestorationService(db_session)
    preview = await service.create_preview(
        actor=actor, source_audit_event_id=source.id,
        resource_type=None, resource_id=None, target_version=None,
    )
    assert preview.eligible is True
    job = await service.create_job(
        actor=actor, preview_id=preview.id,
        expected_resource_version=preview.current_version,
        reason="Restaurar catálogo comercial anterior aprovado.",
        confirmation=f"RESTAURAR {plan.id}",
        idempotency_key="restore-plan-catalog-success",
    )
    result = await service.run_job(job.id)
    await db_session.refresh(plan)

    assert result.status == "succeeded", (result.error_code, result.error_detail)
    assert plan.name == "Plano anterior"
    assert plan.price_monthly_cents == 59700
    assert plan.features == {"audit": False}
    assert plan.is_public is False


@pytest.mark.asyncio
async def test_subscription_restoration_is_local_and_preserves_provider_status(
    db_session, platform_auth_context
):
    actor = await db_session.get(User, platform_auth_context.user_id)
    assert actor is not None
    workspace = await _workspace(db_session, "subscription")
    previous_plan = await _plan(
        db_session, name="Plano anterior", tier=PlanTier.starter, price=29700
    )
    current_plan = await _plan(
        db_session, name="Plano atual", tier=PlanTier.pro, price=69700
    )
    trial_before = datetime.now(UTC) + timedelta(days=10)
    subscription = Subscription(
        workspace_id=workspace.id,
        plan_id=current_plan.id,
        status=SubscriptionStatus.past_due,
        interval=BillingInterval.monthly,
        trial_end=None,
        amount_cents=69700,
        currency="BRL",
    )
    db_session.add(subscription)
    await db_session.flush()
    source = await PlatformAuditEventService(db_session).record(
        PlatformAuditEventCreate(
            actor_id=actor.id,
            actor_role_snapshot="platform_owner",
            is_platform_root=True,
            session_type="normal",
            workspace_id=workspace.id,
            module="billing",
            action="subscription.changed",
            resource_type="subscription",
            resource_id=str(subscription.id),
            resource_label_snapshot=workspace.name,
            result="success",
            before={
                "planId": str(previous_plan.id),
                "trialEnd": trial_before.isoformat(),
                "amountCents": 29700,
                "status": "active",
            },
            after={
                "planId": str(current_plan.id),
                "trialEnd": None,
                "amountCents": 69700,
                "status": "past_due",
            },
            reason="Alteração comercial anterior.",
            reason_source="user",
            correlation_id=str(uuid4()),
            operation_id=str(uuid4()),
            route="test",
            method="PATCH",
        )
    )
    service = PlatformRestorationService(db_session)
    preview = await service.create_preview(
        actor=actor, source_audit_event_id=source.id,
        resource_type=None, resource_id=None, target_version=None,
    )
    assert any("provedor" in warning for warning in preview.warnings)
    job = await service.create_job(
        actor=actor, preview_id=preview.id,
        expected_resource_version=preview.current_version,
        reason="Restaurar termos locais sem simular efeito no provedor.",
        confirmation=f"RESTAURAR {subscription.id}",
        idempotency_key="restore-subscription-terms-success",
    )
    result = await service.run_job(job.id)
    await db_session.refresh(subscription)

    assert result.status == "succeeded"
    assert subscription.plan_id == previous_plan.id
    assert subscription.amount_cents == 29700
    assert subscription.status == SubscriptionStatus.past_due
    assert "pendente de reconciliação" in (subscription.notes or "")


@pytest.mark.asyncio
async def test_generic_restore_cannot_overwrite_canonical_subscription_projection(
    db_session, platform_auth_context
):
    actor = await db_session.get(User, platform_auth_context.user_id)
    assert actor is not None
    workspace = await _workspace(db_session, "canonical-subscription")
    await CommercialCatalogService(db_session).bootstrap_default_catalog(
        actor_user_id=actor.id,
        idempotency_key="restore-canonical-bootstrap",
    )
    contract, _ = await CanonicalContractCommand(
        db_session,
        control_plane=True,
    ).create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=workspace.id,
            status=CommercialContractStatus.active,
            reason="Contrato canônico criado para provar restauração segura.",
            lines=[WorkspaceContractLineWrite(sku="crm_start")],
        ),
        actor_user_id=actor.id,
        idempotency_key="restore-canonical-contract",
    )
    subscription = await db_session.scalar(
        select(Subscription).where(Subscription.workspace_id == workspace.id)
    )
    assert subscription is not None
    advanced_plan = await db_session.scalar(
        select(Plan).where(Plan.features["canonicalSku"].as_string() == "crm_advanced")
    )
    assert advanced_plan is not None
    source = await PlatformAuditEventService(db_session).record(
        PlatformAuditEventCreate(
            actor_id=actor.id,
            actor_role_snapshot="platform_owner",
            is_platform_root=True,
            session_type="normal",
            workspace_id=workspace.id,
            module="billing",
            action="subscription.changed",
            resource_type="subscription",
            resource_id=str(subscription.id),
            resource_label_snapshot=workspace.name,
            result="success",
            before={
                "planId": str(advanced_plan.id),
                "trialEnd": None,
                "amountCents": 67900,
                "status": "active",
            },
            after={
                "planId": str(subscription.plan_id),
                "trialEnd": None,
                "amountCents": subscription.amount_cents,
                "status": subscription.status.value,
            },
            reason="Snapshot legado que não pode vencer o contrato canônico.",
            reason_source="user",
            correlation_id=str(uuid4()),
            operation_id=str(uuid4()),
            route="test",
            method="PATCH",
        )
    )

    preview = await PlatformRestorationService(db_session).create_preview(
        actor=actor,
        source_audit_event_id=source.id,
        resource_type=None,
        resource_id=None,
        target_version=None,
    )

    assert preview.eligible is False
    assert str(contract.id) in " ".join(preview.ineligible_reasons)
    assert any(
        "Estúdio de Planos" in reason for reason in preview.ineligible_reasons
    )


@pytest.mark.asyncio
async def test_workspace_noncommercial_restore_preserves_canonical_plan(
    db_session, platform_auth_context
):
    actor = await db_session.get(User, platform_auth_context.user_id)
    assert actor is not None
    workspace = await _workspace(db_session, "canonical-onboarding")
    workspace.status = WorkspaceStatus.active
    workspace.is_active = True
    workspace.onboarding_completed = True
    await CommercialCatalogService(db_session).bootstrap_default_catalog(
        actor_user_id=actor.id,
        idempotency_key="restore-onboarding-canonical-bootstrap",
    )
    await CanonicalContractCommand(db_session, control_plane=True).create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=workspace.id,
            status=CommercialContractStatus.active,
            reason="Contrato canônico para restauração não comercial.",
            lines=[WorkspaceContractLineWrite(sku="crm_start")],
        ),
        actor_user_id=actor.id,
        idempotency_key="restore-onboarding-canonical-contract",
    )
    subscription = await db_session.scalar(
        select(Subscription).where(Subscription.workspace_id == workspace.id)
    )
    assert subscription is not None
    plan_id = subscription.plan_id
    source = await PlatformAuditEventService(db_session).record(
        PlatformAuditEventCreate(
            actor_id=actor.id,
            actor_role_snapshot="platform_owner",
            is_platform_root=True,
            session_type="normal",
            workspace_id=workspace.id,
            module="customers",
            action="workspace.updated",
            resource_type="workspace",
            resource_id=str(workspace.id),
            resource_label_snapshot=workspace.name,
            result="success",
            before={
                "name": workspace.name,
                "status": "active",
                "isActive": True,
                "onboardingCompleted": False,
                "planId": str(plan_id),
            },
            after={
                "name": workspace.name,
                "status": "active",
                "isActive": True,
                "onboardingCompleted": True,
                "planId": str(plan_id),
            },
            reason="Concluir onboarding sem alterar contrato.",
            reason_source="user",
            correlation_id=str(uuid4()),
            operation_id=str(uuid4()),
            route="test",
            method="PATCH",
        )
    )
    service = PlatformRestorationService(db_session)
    preview = await service.create_preview(
        actor=actor,
        source_audit_event_id=source.id,
        resource_type=None,
        resource_id=None,
        target_version=None,
    )
    assert preview.eligible is True
    assert {item["path"] for item in preview.diff} == {"onboardingCompleted"}
    job = await service.create_job(
        actor=actor,
        preview_id=preview.id,
        expected_resource_version=preview.current_version,
        reason="Restaurar onboarding sem tocar no plano canônico.",
        confirmation=f"RESTAURAR {workspace.id}",
        idempotency_key="restore-onboarding-canonical-job",
    )
    result = await service.run_job(job.id)
    await db_session.refresh(workspace)
    await db_session.refresh(subscription)

    assert result.status == "succeeded", (result.error_code, result.error_detail)
    assert workspace.onboarding_completed is False
    assert subscription.plan_id == plan_id


@pytest.mark.asyncio
async def test_platform_configuration_restoration_publishes_a_new_version(
    db_session, platform_auth_context
):
    actor = await db_session.get(User, platform_auth_context.user_id)
    assert actor is not None
    namespace = f"restore.config.{uuid4().hex[:8]}"
    document = PlatformConfigDocument(
        namespace=namespace,
        schema_version="1.0",
        current_version=1,
        etag=uuid4().hex,
        payload={"mode": "current"},
        updated_by_user_id=actor.id,
    )
    db_session.add(document)
    await db_session.flush()
    source = await PlatformAuditEventService(db_session).record(
        PlatformAuditEventCreate(
            actor_id=actor.id,
            actor_role_snapshot="platform_owner",
            is_platform_root=True,
            session_type="normal",
            module="settings",
            action="configuration.published",
            resource_type="platform_configuration",
            resource_id=namespace,
            resource_label_snapshot=namespace,
            result="success",
            before={"namespace": namespace, "payload": {"mode": "previous"}, "schemaVersion": "1.0"},
            after={"namespace": namespace, "payload": {"mode": "current"}, "schemaVersion": "1.0"},
            reason="Publicação anterior da configuração.",
            reason_source="user",
            correlation_id=str(uuid4()),
            operation_id=str(uuid4()),
            route="test",
            method="PUT",
        )
    )
    service = PlatformRestorationService(db_session)
    preview = await service.create_preview(
        actor=actor, source_audit_event_id=source.id,
        resource_type=None, resource_id=None, target_version=None,
    )
    job = await service.create_job(
        actor=actor, preview_id=preview.id,
        expected_resource_version=preview.current_version,
        reason="Republicar configuração anterior como nova versão.",
        confirmation=f"RESTAURAR {namespace}",
        idempotency_key="restore-platform-configuration-success",
    )
    result = await service.run_job(job.id)
    await db_session.refresh(document)

    assert result.status == "succeeded"
    assert document.payload == {"mode": "previous"}
    assert document.current_version == 2


@pytest.mark.asyncio
async def test_customer_membership_restoration_revokes_scoped_sessions_and_audits(
    db_session, platform_auth_context
):
    actor = await db_session.get(User, platform_auth_context.user_id)
    assert actor is not None
    workspace = await _workspace(db_session, "membership")
    customer = await _customer_user(db_session, "membership")
    membership = WorkspaceMembership(
        workspace_id=workspace.id,
        user_id=customer.id,
        role=WorkspaceRole.viewer,
        status=MembershipStatus.active,
        access_package="marketing",
        permission_grants=[],
        permission_denials=[],
    )
    db_session.add(membership)
    now = datetime.now(UTC)
    session = UserSession(
        user_id=customer.id,
        refresh_jti_digest=uuid4().hex + uuid4().hex,
        device_label="Restore membership session",
        device_type="desktop",
        browser="Chrome",
        operating_system="macOS",
        last_workspace_id=workspace.id,
        last_seen_at=now,
        expires_at=now + timedelta(hours=1),
    )
    db_session.add(session)
    await db_session.flush()

    resource_id = f"{workspace.id}:{customer.id}"
    source = await PlatformAuditEventService(db_session).record(
        PlatformAuditEventCreate(
            actor_id=actor.id,
            actor_role_snapshot=actor.platform_role_package or "platform_owner",
            is_platform_root=True,
            session_type="normal",
            workspace_id=workspace.id,
            module="customers",
            action="access_package.changed",
            resource_type="customer_membership",
            resource_id=resource_id,
            resource_label_snapshot=customer.name,
            result="success",
            before={
                "workspaceId": str(workspace.id),
                "userId": str(customer.id),
                "status": "active",
                "role": "workspace_admin",
                "accessPackage": "office_admin",
                "grants": [],
                "denials": [],
            },
            after={
                "workspaceId": str(workspace.id),
                "userId": str(customer.id),
                "status": "active",
                "role": "viewer",
                "accessPackage": "marketing",
                "grants": [],
                "denials": [],
            },
            reason="Change package before restoration test",
            reason_source="user",
            correlation_id=str(uuid4()),
            operation_id=str(uuid4()),
            resource_version_before=1,
            resource_version_after=2,
            route="test",
            method="PATCH",
        )
    )
    service = PlatformRestorationService(db_session)
    preview = await service.create_preview(
        actor=actor,
        source_audit_event_id=source.id,
        resource_type=None,
        resource_id=None,
        target_version=None,
    )

    assert preview.eligible is True
    assert preview.impact["sessionsWillBeRevoked"] is True
    assert {item["path"] for item in preview.diff} == {"accessPackage", "role"}

    auth_epoch_before = customer.auth_epoch
    job = await service.create_job(
        actor=actor,
        preview_id=preview.id,
        expected_resource_version=preview.current_version,
        reason="Restore the approved customer membership authority bundle",
        confirmation=f"RESTAURAR {resource_id}",
        idempotency_key="restore-customer-membership-success",
    )
    result = await service.run_job(job.id)
    await db_session.refresh(membership)
    await db_session.refresh(customer)
    await db_session.refresh(session)

    assert result.status == "succeeded"
    assert membership.role == WorkspaceRole.workspace_admin
    assert membership.status == MembershipStatus.active
    assert membership.access_package == "office_admin"
    assert customer.auth_epoch == auth_epoch_before + 1
    assert session.revoked_at is not None
    assert session.revocation_reason == "platform_membership_restored"

    restored = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_type == "customer_membership",
            PlatformAuditEvent.resource_id == resource_id,
            PlatformAuditEvent.action == "restored",
        )
    )
    assert restored is not None
    assert restored.workspace_id == workspace.id
    assert restored.restoration_of_event_id == source.id


@pytest.mark.asyncio
async def test_customer_membership_restoration_cannot_remove_last_active_owner(
    db_session, platform_auth_context
):
    actor = await db_session.get(User, platform_auth_context.user_id)
    assert actor is not None
    workspace = await _workspace(db_session, "last-owner")
    customer = await _customer_user(db_session, "last-owner")
    membership = WorkspaceMembership(
        workspace_id=workspace.id,
        user_id=customer.id,
        role=WorkspaceRole.workspace_owner,
        status=MembershipStatus.active,
        access_package="office_owner",
    )
    db_session.add(membership)
    await db_session.flush()

    resource_id = f"{workspace.id}:{customer.id}"
    source = await PlatformAuditEventService(db_session).record(
        PlatformAuditEventCreate(
            actor_id=actor.id,
            actor_role_snapshot=actor.platform_role_package or "platform_owner",
            is_platform_root=True,
            session_type="normal",
            workspace_id=workspace.id,
            module="customers",
            action="ownership.changed",
            resource_type="customer_membership",
            resource_id=resource_id,
            resource_label_snapshot=customer.name,
            result="success",
            before={
                "workspaceId": str(workspace.id),
                "userId": str(customer.id),
                "status": "suspended",
                "role": "workspace_admin",
                "accessPackage": "office_admin",
            },
            after={
                "workspaceId": str(workspace.id),
                "userId": str(customer.id),
                "status": "active",
                "role": "workspace_owner",
                "accessPackage": "office_owner",
            },
            reason="Synthetic owner transition for invariant test",
            reason_source="user",
            correlation_id=str(uuid4()),
            operation_id=str(uuid4()),
            route="test",
            method="PATCH",
        )
    )

    preview = await PlatformRestorationService(db_session).create_preview(
        actor=actor,
        source_audit_event_id=source.id,
        resource_type=None,
        resource_id=None,
        target_version=None,
    )

    assert preview.eligible is False
    assert any("sem titular ativo" in reason for reason in preview.ineligible_reasons)
