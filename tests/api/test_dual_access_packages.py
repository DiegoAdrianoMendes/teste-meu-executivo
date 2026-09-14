"""Contratos dos pacotes separados entre plataforma SaaS e escritórios clientes."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.platform_rbac import has_platform_permission, is_platform_root
from app.core.security import hash_password, verify_password
from app.models.auth_security import AuthSecurityEvent
from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.schemas.workspace import MembershipCreate
from app.services.auth_service import AuthService
from app.services.platform_audit_event_service import PlatformAuditEventService
from app.services.platform_service import PlatformService
from app.services.team_member_service import TeamMemberService
from app.services.workspace_service import WorkspaceService


def test_suspended_platform_staff_remains_a_blackbox_identity() -> None:
    user = User(
        name="Suporte suspenso",
        email="suporte-suspenso@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="FS",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=False,
        platform_role_package="support",
        platform_access_active=False,
    )

    assert AuthService.is_platform_identity(user) is True
    assert AuthService.has_active_platform_access(user) is False


def test_only_platform_root_bypasses_permission_denials() -> None:
    root = User(
        name="Titular",
        email="root-permission@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="RT",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=True,
        platform_role_package="platform_owner",
        platform_access_active=True,
        platform_permission_denials=[{"module": "finance", "action": "manage"}],
    )
    operational_admin = User(
        name="Admin operacional",
        email="operational-admin@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="AO",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=False,
        platform_role_package="platform_admin",
        platform_access_active=True,
        platform_permission_denials=[{"module": "finance", "action": "manage"}],
    )

    assert has_platform_permission(root, "finance", "manage") is True
    assert has_platform_permission(operational_admin, "finance", "manage") is False


def test_legacy_flag_or_owner_package_alone_never_creates_root() -> None:
    common = {
        "agent_type": AgentType.human,
        "role": UserRole.admin,
        "is_active": True,
        "is_internal_staff": True,
        "platform_access_active": True,
    }
    legacy_flag = User(
        name="Flag legado",
        email="legacy-root-flag@example.com",
        initials="FL",
        is_platform_admin=True,
        platform_role_package="platform_admin",
        **common,
    )
    owner_label_only = User(
        name="Pacote isolado",
        email="owner-label-only@example.com",
        initials="PO",
        is_platform_admin=False,
        platform_role_package="platform_owner",
        **common,
    )

    assert is_platform_root(legacy_flag) is False
    assert is_platform_root(owner_label_only) is False
    assert has_platform_permission(legacy_flag, "finance", "manage") is True
    assert has_platform_permission(owner_label_only, "finance", "manage") is False


@pytest.mark.asyncio
async def test_workspace_rejects_any_blackbox_identity(db_session):
    workspace = Workspace(
        name="Conta sem identidade dupla",
        slug=f"surface-boundary-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        settings={},
    )
    employee = User(
        name="Funcionário do SaaS",
        email=f"employee-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="FS",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=False,
        platform_role_package="support",
        platform_access_active=False,
        email_verified_at=datetime.now(UTC),
    )
    db_session.add_all([workspace, employee])
    await db_session.flush()

    with pytest.raises(HTTPException) as conflict:
        await WorkspaceService(db_session).create_membership(
            workspace.id,
            MembershipCreate(
                user_id=employee.id,
                role=WorkspaceRole.operator,
                status=MembershipStatus.invited,
            ),
        )

    assert conflict.value.status_code == 409
    assert conflict.value.detail["code"] == "IDENTITY-SURFACE-CONFLICT"


def test_workspace_role_delegation_blocks_package_escalation() -> None:
    managing_partner = WorkspaceMembership(
        role=WorkspaceRole.legal_manager,
        status=MembershipStatus.active,
    )

    TeamMemberService._assert_role_assignment(managing_partner, WorkspaceRole.operator)
    with pytest.raises(HTTPException) as escalation:
        TeamMemberService._assert_role_assignment(
            managing_partner,
            WorkspaceRole.workspace_admin,
        )

    assert escalation.value.status_code == 403
    assert escalation.value.detail["code"] == "TEAM-ROLE-ESCALATION"


@pytest.mark.asyncio
async def test_workspace_access_packages_apply_role_permissions_and_data_policy(
    client,
    active_subscription,
):
    catalog_response = await client.get("/api/v1/admin/access-packages")

    assert catalog_response.status_code == 200, catalog_response.text
    catalog = {item["key"]: item for item in catalog_response.json()}
    assert len(catalog) == 28
    assert catalog["lawyer"]["workspaceRole"] == "operator"
    assert catalog["secretary"]["sensitiveDataPolicy"] == "view"
    assert catalog["marketing"]["sensitiveDataPolicy"] == "hidden"
    assert catalog["practice_coordinator"]["workspaceRole"] == "legal_manager"
    assert catalog["litigation_lawyer"]["workspaceRole"] == "operator"
    assert catalog["case_controller"]["sensitiveDataPolicy"] == "view"
    assert catalog["people_hr"]["sensitiveDataPolicy"] == "hidden"

    invited_response = await client.post(
        "/api/v1/admin/team-members",
        headers={"Idempotency-Key": f"package-secretary-{uuid4()}"},
        json={
            "name": "Secretária do escritório",
            "email": f"secretaria-{uuid4().hex[:8]}@example.com",
            "accessPackage": "secretary",
        },
    )

    assert invited_response.status_code == 202, invited_response.text
    invited = invited_response.json()
    assert invited["accessPackage"] == "secretary"
    assert invited["workspaceRole"] == "operator"
    assert invited["canViewSensitiveLeadData"] is True
    assert invited["canEditSensitiveLeadData"] is False
    assert not any(
        item["module"] == "aprovacoes" and item["action"] == "view"
        for item in invited["permissions"]
    )

    updated_response = await client.patch(
        f"/api/v1/admin/team-members/{invited['membershipId']}",
        json={"accessPackage": "finance"},
    )

    assert updated_response.status_code == 200, updated_response.text
    updated = updated_response.json()
    assert updated["accessPackage"] == "finance"
    assert updated["workspaceRole"] == "finance_manager"
    assert updated["canViewSensitiveLeadData"] is False
    assert any(
        item["module"] == "finance" and item["action"] == "manage"
        for item in updated["permissions"]
    )


@pytest.mark.asyncio
async def test_workspace_package_rejects_unknown_key_and_role_conflict(
    client,
    active_subscription,
):
    unknown = await client.post(
        "/api/v1/admin/team-members",
        headers={"Idempotency-Key": f"package-unknown-{uuid4()}"},
        json={
            "name": "Pessoa inválida",
            "email": f"invalid-{uuid4().hex[:8]}@example.com",
            "accessPackage": "pacote_inexistente",
        },
    )
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["code"] == "TEAM-PACKAGE-UNKNOWN"

    conflicting = await client.post(
        "/api/v1/admin/team-members",
        headers={"Idempotency-Key": f"package-conflict-{uuid4()}"},
        json={
            "name": "Pessoa em conflito",
            "email": f"conflict-{uuid4().hex[:8]}@example.com",
            "accessPackage": "lawyer",
            "workspaceRole": "finance_manager",
        },
    )
    assert conflicting.status_code == 422
    assert conflicting.json()["detail"]["code"] == "TEAM-PACKAGE-ROLE-CONFLICT"


@pytest.mark.asyncio
async def test_platform_staff_packages_are_global_scoped_and_permission_guarded(
    db_session,
    platform_client,
    platform_auth_context,
):
    packages_response = await platform_client.get("/api/v1/platform/staff/packages")
    assert packages_response.status_code == 200, packages_response.text
    packages = {item["key"]: item for item in packages_response.json()}
    assert packages["platform_owner"]["assignable"] is False
    assert packages["support"]["assignable"] is True
    assert any(
        item == {"module": "support_access", "action": "request"}
        for item in packages["support"]["permissions"]
    )

    created_response = await platform_client.post(
        "/api/v1/platform/staff",
        json={
            "name": "Suporte SaaS",
            "email": f"support-saas-{uuid4().hex[:8]}@example.com",
            "rolePackage": "support",
        },
    )
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    assert created["rolePackage"] == "support"
    assert created["isPlatformRoot"] is False
    assert created["requiresPasswordSetup"] is True
    assert created["passwordSetupDeliveryId"]
    assert created["activeSessionCount"] == 0
    assert created["lastLoginAt"] is None

    temporary_password = "SenhaProvisoriaForte#2026"
    temporary_response = await platform_client.post(
        "/api/v1/platform/staff",
        json={
            "name": "Suporte com senha provisoria",
            "email": f"support-temporary-{uuid4().hex[:8]}@example.com",
            "rolePackage": "support",
            "temporaryPassword": temporary_password,
        },
    )
    assert temporary_response.status_code == 201, temporary_response.text
    temporary_staff = temporary_response.json()
    assert temporary_staff["requiresPasswordSetup"] is False
    assert temporary_staff["requiresPasswordChange"] is True
    assert temporary_staff["passwordSetupDeliveryId"] is None
    temporary_user = await db_session.get(User, UUID(temporary_staff["id"]))
    assert temporary_user is not None
    assert temporary_user.hashed_password != temporary_password
    assert temporary_user.password_change_required is True
    assert temporary_user.temporary_password_issued_at is not None

    created_audit = await db_session.scalar(
        select(PlatformAuditEvent)
        .where(
            PlatformAuditEvent.resource_type == "staff",
            PlatformAuditEvent.resource_id == created["id"],
            PlatformAuditEvent.action == "create_platform_staff",
            PlatformAuditEvent.result == "success",
        )
        .order_by(PlatformAuditEvent.occurred_at.desc())
    )
    assert created_audit is not None
    assert created_audit.before is None
    assert created_audit.after["rolePackage"] == "support"
    assert created_audit.after["platformAccessActive"] is True
    assert created_audit.reason_source == "system"


    updated_response = await platform_client.patch(
        f"/api/v1/platform/staff/{created['id']}",
        json={
            "permissionDenials": [{"module": "support", "action": "manage"}],
            "reason": "Restrição temporária para validar a auditoria canônica.",
        },
    )
    assert updated_response.status_code == 200, updated_response.text
    assert updated_response.json()["accessActive"] is True

    canonical = await db_session.scalar(
        select(PlatformAuditEvent)
        .where(
            PlatformAuditEvent.resource_type == "staff",
            PlatformAuditEvent.resource_id == created["id"],
            PlatformAuditEvent.action == "update_platform_staff",
            PlatformAuditEvent.result == "success",
        )
        .order_by(PlatformAuditEvent.occurred_at.desc())
    )
    assert canonical is not None
    assert canonical.before["platformAccessActive"] is True
    assert canonical.after["platformAccessActive"] is True
    assert canonical.after["denials"] == [{"module": "support", "action": "manage"}]
    assert "denials" in canonical.changed_paths
    assert "platformAccessActive" not in canonical.changed_paths
    assert canonical.reason == "Restrição temporária para validar a auditoria canônica."

    revoke_sessions = await platform_client.post(
        f"/api/v1/platform/staff/{created['id']}/sessions/revoke",
        json={"reason": "Revogação preventiva das sessões internas."},
    )
    assert revoke_sessions.status_code == 200
    assert revoke_sessions.json() == {"ok": True, "revokedCount": 0}
    revoked_audit = await db_session.scalar(
        select(PlatformAuditEvent)
        .where(
            PlatformAuditEvent.resource_type == "staff",
            PlatformAuditEvent.resource_id == created["id"],
            PlatformAuditEvent.action == "revoke_platform_staff_sessions",
            PlatformAuditEvent.result == "success",
        )
        .order_by(PlatformAuditEvent.occurred_at.desc())
    )
    assert revoked_audit is not None
    assert revoked_audit.after["authEpoch"] > revoked_audit.before["authEpoch"]
    assert "authEpoch" in revoked_audit.changed_paths
    assert revoked_audit.reason == "Revogação preventiva das sessões internas."

    immutable_root = await platform_client.patch(
        f"/api/v1/platform/staff/{platform_auth_context.user_id}",
        json={"accessActive": False, "reason": "Suspensão operacional de teste."},
    )
    assert immutable_root.status_code == 409
    assert immutable_root.json()["detail"]["code"] == "PLATFORM-STAFF-ROOT-IMMUTABLE"

    platform_auth_context.user_id = UUID(created["id"])
    manifest = await platform_client.get("/api/v1/platform/session-manifest")
    assert manifest.status_code == 200, manifest.text
    assert manifest.json()["isPlatformRoot"] is False
    assert "support" in manifest.json()["availableModules"]
    assert "internal_costs" in manifest.json()["maskedFields"]

    overview = await platform_client.get("/api/v1/platform/overview")
    assert overview.status_code == 200, overview.text
    assert set(overview.json()) == {"workspaces", "workspace_health"}

    finance = await platform_client.get("/api/v1/platform/mrr")
    staff = await platform_client.get("/api/v1/platform/staff")
    settings = await platform_client.get("/api/v1/platform/configurations/brand.identity")
    assert finance.status_code == 403
    assert staff.status_code == 403
    assert settings.status_code == 403
    assert finance.json()["detail"]["code"] == "PLATFORM-PERMISSION-DENIED"


@pytest.mark.asyncio
async def test_root_sets_temporary_password_for_staff_and_workspace_user_without_secret_leak(
    db_session,
    platform_client,
):
    staff_response = await platform_client.post(
        "/api/v1/platform/staff",
        json={
            "name": "TI não root",
            "email": f"ti-{uuid4().hex[:8]}@example.com",
            "rolePackage": "technical_support",
        },
    )
    assert staff_response.status_code == 201, staff_response.text
    staff_id = UUID(staff_response.json()["id"])

    staff_password = await platform_client.post(
        f"/api/v1/platform/staff/{staff_id}/temporary-password",
        json={
            "temporaryPassword": "12345",
            "reason": "Acesso inicial controlado pela titularidade.",
        },
    )
    assert staff_password.status_code == 200, staff_password.text
    assert staff_password.json() == {
        "userId": str(staff_id),
        "requiresPasswordChange": True,
        "revokedSessionCount": 0,
    }
    staff = await db_session.get(User, staff_id)
    assert staff is not None
    assert verify_password("12345", staff.hashed_password)
    assert staff.password_change_required is True
    assert staff.temporary_password_issued_at is not None
    assert staff.platform_role_package == "technical_support"
    assert staff.platform_role_package != "platform_owner"

    workspace = Workspace(
        name="Escritório senha provisória",
        slug=f"temporary-password-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
    )
    person = User(
        name="Titular do escritório",
        email=f"workspace-owner-{uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("senha-atual-forte"),
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="TE",
        is_active=True,
        email_verified_at=datetime.now(UTC),
    )
    db_session.add_all([workspace, person])
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=person.id,
            role=WorkspaceRole.workspace_owner,
            status=MembershipStatus.active,
            is_default=True,
            access_package="office_owner",
        )
    )
    await db_session.commit()

    person_password = await platform_client.post(
        f"/api/v1/platform/people/{workspace.id}/{person.id}/temporary-password",
        json={
            "temporaryPassword": "12345",
            "reason": "Acesso inicial controlado do escritório piloto.",
        },
    )
    assert person_password.status_code == 200, person_password.text
    await db_session.refresh(person)
    assert verify_password("12345", person.hashed_password)
    assert person.password_change_required is True

    security_events = list(
        await db_session.scalars(
            select(AuthSecurityEvent).where(
                AuthSecurityEvent.event_type == "temporary_password_issued"
            )
        )
    )
    audit_events = list(
        await db_session.scalars(
            select(PlatformAuditEvent).where(
                PlatformAuditEvent.action == "set_temporary_password"
            )
        )
    )
    assert len(security_events) == 2
    assert len(audit_events) == 2
    serialized = repr(
        [event.details for event in security_events]
        + [[event.before, event.after] for event in audit_events]
    )
    assert "12345" not in serialized
    assert "hashed_password" not in serialized


@pytest.mark.asyncio
async def test_platform_staff_page_has_real_total_filters_and_stable_pages(
    db_session,
    platform_client,
):
    marker = uuid4().hex[:8]
    db_session.add_all(
        [
            User(
                name=f"Equipe Paginada {marker} {index:03d}",
                email=f"staff-page-{marker}-{index:03d}@example.com",
                agent_type=AgentType.human,
                role=UserRole.admin,
                initials=f"{index % 100:02d}",
                is_active=True,
                is_internal_staff=True,
                is_platform_admin=False,
                platform_role_package="support",
                platform_access_active=index % 2 == 0,
            )
            for index in range(55)
        ]
    )
    await db_session.commit()

    first = await platform_client.get(
        "/api/v1/platform/staff/page",
        params={"search": f"Equipe Paginada {marker}", "limit": 20, "offset": 0},
    )
    second = await platform_client.get(
        "/api/v1/platform/staff/page",
        params={"search": f"Equipe Paginada {marker}", "limit": 20, "offset": 20},
    )
    active = await platform_client.get(
        "/api/v1/platform/staff/page",
        params={
            "search": f"Equipe Paginada {marker}",
            "rolePackage": "support",
            "accessActive": True,
            "limit": 50,
            "offset": 0,
        },
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert active.status_code == 200, active.text
    first_page = first.json()
    second_page = second.json()
    active_page = active.json()

    assert first_page["total"] == 55
    assert len(first_page["items"]) == 20
    assert first_page["hasMore"] is True
    assert second_page["total"] == 55
    assert len(second_page["items"]) == 20
    assert second_page["offset"] == 20
    assert {item["id"] for item in first_page["items"]}.isdisjoint(
        {item["id"] for item in second_page["items"]}
    )
    assert [item["name"] for item in first_page["items"]] == sorted(
        item["name"] for item in first_page["items"]
    )
    assert active_page["total"] == 28
    assert all(item["accessActive"] is True for item in active_page["items"])
    assert all(item["rolePackage"] == "support" for item in active_page["items"])


@pytest.mark.asyncio
async def test_platform_staff_update_rolls_back_when_canonical_audit_cannot_persist(
    db_session,
    platform_client,
    monkeypatch,
):
    created_response = await platform_client.post(
        "/api/v1/platform/staff",
        json={
            "name": "Suporte atômico",
            "email": f"support-atomic-{uuid4().hex[:8]}@example.com",
            "rolePackage": "support",
        },
    )
    assert created_response.status_code == 201, created_response.text
    staff_id = UUID(created_response.json()["id"])
    persisted_before = await db_session.get(User, staff_id)
    assert persisted_before is not None
    auth_epoch_before = persisted_before.auth_epoch

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(PlatformAuditEventService, "record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        await platform_client.patch(
            f"/api/v1/platform/staff/{staff_id}",
            json={
                "accessActive": False,
                "reason": "Teste de rollback integral da autoridade interna.",
            },
        )

    db_session.expire_all()
    persisted_after = await db_session.get(User, staff_id)
    assert persisted_after is not None
    assert persisted_after.platform_access_active is True
    assert persisted_after.auth_epoch == auth_epoch_before


@pytest.mark.asyncio
async def test_blackbox_manifest_declares_root_without_requiring_permission_catalog(
    platform_client,
    platform_auth_context,
):
    response = await platform_client.get("/api/v1/platform/session-manifest")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["userId"] == str(platform_auth_context.user_id)
    assert payload["isPlatformRoot"] is True
    assert "finance" in payload["availableModules"]
    assert "security" in payload["availableModules"]
    assert payload["maskedFields"] == []
    assert payload["backendVersion"]


@pytest.mark.asyncio
async def test_only_root_administers_platform_admin_and_staff_cannot_edit_own_authority(
    platform_client,
    platform_auth_context,
):
    operational = await platform_client.post(
        "/api/v1/platform/staff",
        json={
            "name": "Admin operacional",
            "email": f"admin-operacional-{uuid4().hex[:8]}@example.com",
            "rolePackage": "platform_admin",
        },
    )
    assert operational.status_code == 201, operational.text
    operational_id = UUID(operational.json()["id"])
    platform_auth_context.user_id = operational_id

    provisional_password_bypass = await platform_client.post(
        "/api/v1/platform/staff",
        json={
            "name": "Suporte com senha por admin operacional",
            "email": f"support-password-bypass-{uuid4().hex[:8]}@example.com",
            "rolePackage": "support",
            "temporaryPassword": "SenhaProvisoriaForte#2026",
        },
    )
    assert provisional_password_bypass.status_code == 403
    assert provisional_password_bypass.json()["detail"]["code"] == "PLATFORM-ROOT-REQUIRED"

    self_change = await platform_client.patch(
        f"/api/v1/platform/staff/{operational_id}",
        json={
            "permissionDenials": [{"module": "finance", "action": "manage"}],
            "reason": "Tentativa de autoalteração para validar proteção.",
        },
    )
    assert self_change.status_code == 409
    assert self_change.json()["detail"]["code"] == "PLATFORM-STAFF-SELF-AUTHORITY"

    create_peer = await platform_client.post(
        "/api/v1/platform/staff",
        json={
            "name": "Outro admin",
            "email": f"outro-admin-{uuid4().hex[:8]}@example.com",
            "rolePackage": "platform_admin",
        },
    )
    assert create_peer.status_code == 403
    assert create_peer.json()["detail"]["code"] == "PLATFORM-ADMIN-ROOT-ONLY"


@pytest.mark.asyncio
async def test_mrr_normalizes_annual_subscriptions_before_aggregation(db_session):
    plan = Plan(
        name="Plano de teste MRR",
        tier=PlanTier.pro,
        price_monthly_cents=10_000,
        price_annually_cents=120_000,
        max_users=5,
        max_matters=None,
        max_tokens_per_month=1_000_000,
        max_ai_agents=5,
        features={},
        is_active=True,
        is_public=True,
        sort_order=1,
    )
    db_session.add(plan)
    await db_session.flush()

    now = datetime.now(UTC)
    for index, interval in enumerate((BillingInterval.monthly, BillingInterval.annually)):
        workspace = Workspace(
            name=f"Workspace MRR {index}",
            slug=f"workspace-mrr-{index}-{uuid4().hex[:6]}",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.assisted,
            is_active=True,
            settings={},
        )
        db_session.add(workspace)
        await db_session.flush()
        db_session.add(
            Subscription(
                workspace_id=workspace.id,
                plan_id=plan.id,
                status=SubscriptionStatus.active,
                interval=interval,
                current_period_start=now,
                current_period_end=now
                + timedelta(days=365 if interval == BillingInterval.annually else 30),
                amount_cents=120_000 if interval == BillingInterval.annually else 10_000,
                currency="BRL",
            )
        )
    await db_session.flush()

    result = await PlatformService(db_session).get_mrr_breakdown()

    assert result["total_mrr_cents"] == 20_000
    assert result["total_arr_cents"] == 240_000
    assert result["breakdown"][0]["workspace_count"] == 2
    assert result["breakdown"][0]["mrr_cents"] == 20_000
