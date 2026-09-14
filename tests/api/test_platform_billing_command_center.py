"""
Testes dos endpoints do Centro de Comando (Sprint B1): billing/overview,
subscriptions (listar + ações auditadas), workspace-360, people.

Cobre:
- 403 para usuário não-staff em cada rota nova;
- ação de mudança de plano grava subscription_event e reflete no overview;
- workspace-360 agrega assinatura + P&L + eventos;
- people lista e busca por nome/e-mail.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth_security import AuthSecurityEvent
from app.models.identity import InvitationStatus, UserSession, WorkspaceInvitation
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.subscription_commercial import (
    SubscriptionEvent,
    WorkspaceSubscription,
    WorkspaceSubscriptionStatus,
)
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.services.platform_audit_event_service import PlatformAuditEventService
from app.services.platform_billing_service import PlatformBillingService

pytestmark = pytest.mark.asyncio


def _workspace(name: str) -> Workspace:
    return Workspace(
        name=name,
        slug=f"cc-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )


async def _seed_subscription(db_session: AsyncSession, *, plano="essencial", status=WorkspaceSubscriptionStatus.ativa, valor=19700):
    workspace = _workspace("Centro de Comando WS")
    db_session.add(workspace)
    await db_session.flush()
    sub = WorkspaceSubscription(
        workspace_id=workspace.id,
        plano=plano,
        status=status,
        valor_mensal_cents=valor,
        ciclo="mensal",
        origem="test",
    )
    db_session.add(sub)
    await db_session.flush()

    user = User(
        name="Ana Advogada",
        email=f"ana-{uuid4().hex[:8]}@escritorio.com.br",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="AA",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    membership = WorkspaceMembership(
        workspace_id=workspace.id,
        user_id=user.id,
        role=WorkspaceRole.workspace_admin,
        status=MembershipStatus.active,
    )
    db_session.add(membership)
    await db_session.commit()
    return workspace, sub, user


async def test_billing_overview_requires_platform_staff(client):
    response = await client.get("/api/v1/platform/billing/overview")
    assert response.status_code == 403


async def test_subscriptions_requires_platform_staff(client):
    response = await client.get("/api/v1/platform/subscriptions")
    assert response.status_code == 403


async def test_subscriptions_return_real_stable_pages(db_session, platform_client):
    prefix = f"Assinatura Paginada {uuid4().hex[:8]}"
    for index in range(55):
        workspace = _workspace(f"{prefix} {index:02d}")
        db_session.add(workspace)
        await db_session.flush()
        db_session.add(WorkspaceSubscription(
            workspace_id=workspace.id,
            plano="essencial",
            status=WorkspaceSubscriptionStatus.ativa,
            valor_mensal_cents=19700,
            ciclo="mensal",
            origem="test-pagination",
        ))
    await db_session.commit()

    first = await platform_client.get(
        "/api/v1/platform/subscriptions",
        params={"search": prefix, "limit": 25, "offset": 0},
    )
    second = await platform_client.get(
        "/api/v1/platform/subscriptions",
        params={"search": prefix, "limit": 25, "offset": 25},
    )
    last = await platform_client.get(
        "/api/v1/platform/subscriptions",
        params={"search": prefix, "limit": 25, "offset": 50},
    )

    assert first.status_code == second.status_code == last.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    last_payload = last.json()
    assert first_payload["total"] == first_payload["count"] == 55
    assert first_payload["hasMore"] is True
    assert second_payload["hasMore"] is True
    assert last_payload["hasMore"] is False
    assert len(first_payload["items"]) == len(second_payload["items"]) == 25
    assert len(last_payload["items"]) == 5
    assert {item["id"] for item in first_payload["items"]}.isdisjoint(
        {item["id"] for item in second_payload["items"]}
    )


async def test_billing_overview_reflects_seeded_subscription(db_session, platform_client):
    workspace, sub, _user = await _seed_subscription(db_session)

    response = await platform_client.get("/api/v1/platform/billing/overview")
    assert response.status_code == 200
    payload = response.json()
    assert payload["mrr_cents"] >= 19700
    assert payload["active_count"] >= 1
    plan_keys = {row["plano"] for row in payload["by_plan"]}
    assert "crm_start" in plan_keys


async def test_change_plan_action_is_audited_and_reflected(db_session, platform_client):
    workspace, sub, _user = await _seed_subscription(db_session, plano="essencial", valor=19700)

    response = await platform_client.patch(
        f"/api/v1/platform/subscriptions/{workspace.id}/plan",
        json={"novoPlano": "escritorio", "motivo": "Upgrade solicitado pelo cliente."},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["plano"] == "crm_advanced"
    assert payload["valor_mensal_cents"] == 67900

    events = (
        await db_session.execute(
            select(SubscriptionEvent).where(SubscriptionEvent.subscription_id == sub.id)
        )
    ).scalars().all()
    assert any(event.event_type == "plano_alterado" for event in events)

    canonical = await db_session.scalar(
        select(PlatformAuditEvent)
        .where(
            PlatformAuditEvent.resource_type == "subscription",
            PlatformAuditEvent.resource_id == str(workspace.id),
            PlatformAuditEvent.result == "success",
        )
        .order_by(PlatformAuditEvent.occurred_at.desc())
    )
    assert canonical is not None
    assert canonical.workspace_label_snapshot == workspace.name
    assert canonical.before["plan"] == "essencial"
    assert canonical.after["plan"] == "escritorio"
    assert canonical.before["amountCents"] == 19700
    assert canonical.after["amountCents"] == 67900
    assert {"plan", "amountCents"}.issubset(canonical.changed_paths)

    overview = await platform_client.get("/api/v1/platform/billing/overview")
    plan_keys = {row["plano"] for row in overview.json()["by_plan"]}
    assert "crm_advanced" in plan_keys


async def test_change_plan_rejects_invalid_plan(db_session, platform_client):
    workspace, _sub, _user = await _seed_subscription(db_session)

    response = await platform_client.patch(
        f"/api/v1/platform/subscriptions/{workspace.id}/plan",
        json={"novoPlano": "inexistente", "motivo": "teste"},
    )
    assert response.status_code == 422


async def test_subscription_change_rolls_back_when_canonical_audit_cannot_persist(
    db_session, platform_client, monkeypatch
):
    workspace, sub, _user = await _seed_subscription(
        db_session, plano="essencial", valor=19700
    )
    subscription_id = sub.id

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(PlatformAuditEventService, "record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        await platform_client.patch(
            f"/api/v1/platform/subscriptions/{workspace.id}/plan",
            json={"novoPlano": "escritorio", "motivo": "Teste de atomicidade integral."},
        )

    persisted = await db_session.get(WorkspaceSubscription, subscription_id)
    assert persisted is not None
    assert persisted.plano == "essencial"
    assert persisted.valor_mensal_cents == 19700
    events = list(
        await db_session.scalars(
            select(SubscriptionEvent).where(SubscriptionEvent.subscription_id == subscription_id)
        )
    )
    assert events == []


async def test_suspend_and_reactivate_subscription(db_session, platform_client):
    workspace, _sub, _user = await _seed_subscription(db_session)

    suspend = await platform_client.patch(
        f"/api/v1/platform/subscriptions/{workspace.id}/suspend",
        json={"motivo": "Inadimplência prolongada."},
    )
    assert suspend.status_code == 200
    assert suspend.json()["status"] == "pausada"

    reactivate = await platform_client.patch(
        f"/api/v1/platform/subscriptions/{workspace.id}/reactivate",
        json={"motivo": "Pagamento regularizado."},
    )
    assert reactivate.status_code == 200
    assert reactivate.json()["status"] == "ativa"

    events_resp = await platform_client.get(
        f"/api/v1/platform/subscriptions/{workspace.id}/events"
    )
    assert events_resp.status_code == 200
    event_types = {event["event_type"] for event in events_resp.json()}
    assert {"suspensa", "reativada"}.issubset(event_types)

    first_page = await platform_client.get(
        f"/api/v1/platform/subscriptions/{workspace.id}/events/page",
        params={"limit": 1, "offset": 0},
    )
    second_page = await platform_client.get(
        f"/api/v1/platform/subscriptions/{workspace.id}/events/page",
        params={"limit": 1, "offset": 1},
    )
    assert first_page.status_code == second_page.status_code == 200
    first_payload = first_page.json()
    second_payload = second_page.json()
    assert first_payload["total"] == 2
    assert first_payload["limit"] == 1
    assert first_payload["offset"] == 0
    assert first_payload["hasMore"] is True
    assert second_payload["offset"] == 1
    assert second_payload["hasMore"] is False
    assert first_payload["items"][0]["id"] != second_payload["items"][0]["id"]
    assert "eventType" in first_payload["items"][0]
    assert "occurredAt" in first_payload["items"][0]


async def test_workspace_360_aggregates_subscription_and_pnl(db_session, platform_client):
    workspace, _sub, _user = await _seed_subscription(db_session)

    response = await platform_client.get(f"/api/v1/platform/workspace-360/{workspace.id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["workspace_id"] == str(workspace.id)
    assert payload["subscription"]["plano"] == "crm_start"
    assert payload["pnl"]["receita_mensal_cents"] == 19700
    assert isinstance(payload["recent_events"], list)


async def test_workspace_360_loads_exact_subscription_without_global_window(
    db_session,
    platform_client,
    monkeypatch,
):
    workspace, _sub, _user = await _seed_subscription(db_session)

    async def reject_global_subscription_window(*_args, **_kwargs):
        raise AssertionError("Conta 360 não pode procurar assinatura numa janela global.")

    monkeypatch.setattr(
        PlatformBillingService,
        "list_subscriptions",
        reject_global_subscription_window,
    )

    response = await platform_client.get(f"/api/v1/platform/workspace-360/{workspace.id}")
    assert response.status_code == 200
    assert response.json()["subscription"]["workspace_id"] == str(workspace.id)


async def test_workspace_360_returns_404_for_unknown_workspace(platform_client):
    response = await platform_client.get(f"/api/v1/platform/workspace-360/{uuid4()}")
    assert response.status_code == 404


async def test_people_lists_and_searches(db_session, platform_client):
    workspace, _sub, user = await _seed_subscription(db_session)
    now = datetime.now(UTC)
    db_session.add(UserSession(
        user_id=user.id,
        refresh_jti_digest=uuid4().hex + uuid4().hex,
        device_label="Chrome de teste",
        device_type="desktop",
        browser="Chrome",
        operating_system="macOS",
        last_workspace_id=workspace.id,
        last_seen_at=now,
        expires_at=now + timedelta(days=1),
    ))
    await db_session.commit()

    response = await platform_client.get("/api/v1/platform/people")
    assert response.status_code == 200
    payload = response.json()
    assert any(item["user_id"] == str(user.id) for item in payload["items"])
    person = next(item for item in payload["items"] if item["user_id"] == str(user.id))
    assert person["active_session_count"] == 1
    assert person["last_seen_at"] is not None

    search_response = await platform_client.get(
        "/api/v1/platform/people", params={"search": "Ana Advogada"}
    )
    assert search_response.status_code == 200
    assert all("Ana" in item["name"] for item in search_response.json()["items"])

    no_match = await platform_client.get(
        "/api/v1/platform/people", params={"search": "Ninguem Com Esse Nome"}
    )
    assert no_match.json()["items"] == []


async def test_people_returns_real_stable_pages(db_session, platform_client):
    workspace = _workspace("Paginação de pessoas")
    db_session.add(workspace)
    await db_session.flush()
    prefix = f"Pessoa Paginada {uuid4().hex[:8]}"
    for index in range(55):
        user = User(
            name=f"{prefix} {index:02d}",
            email=f"people-page-{uuid4().hex}@example.com",
            agent_type=AgentType.human,
            role=UserRole.lawyer,
            initials="PP",
            is_active=True,
        )
        db_session.add(user)
        await db_session.flush()
        db_session.add(WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole.operator,
            status=MembershipStatus.active,
        ))
    await db_session.commit()

    first = await platform_client.get(
        "/api/v1/platform/people",
        params={"search": prefix, "limit": 25, "offset": 0},
    )
    second = await platform_client.get(
        "/api/v1/platform/people",
        params={"search": prefix, "limit": 25, "offset": 25},
    )
    last = await platform_client.get(
        "/api/v1/platform/people",
        params={"search": prefix, "limit": 25, "offset": 50},
    )

    assert first.status_code == second.status_code == last.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    last_payload = last.json()
    assert first_payload["total"] == first_payload["count"] == 55
    assert first_payload["hasMore"] is True
    assert second_payload["hasMore"] is True
    assert last_payload["hasMore"] is False
    assert len(first_payload["items"]) == len(second_payload["items"]) == 25
    assert len(last_payload["items"]) == 5
    assert {item["user_id"] for item in first_payload["items"]}.isdisjoint(
        {item["user_id"] for item in second_payload["items"]}
    )


async def test_people_block_and_unblock(db_session, platform_client):
    workspace, _sub, user = await _seed_subscription(db_session)
    now = datetime.now(UTC)
    session = UserSession(
        user_id=user.id,
        refresh_jti_digest=uuid4().hex + uuid4().hex,
        device_label="Safari de teste",
        device_type="desktop",
        browser="Safari",
        operating_system="macOS",
        last_workspace_id=workspace.id,
        last_seen_at=now,
        expires_at=now + timedelta(days=1),
    )
    db_session.add(session)
    await db_session.commit()

    block = await platform_client.patch(
        f"/api/v1/platform/people/{workspace.id}/{user.id}/block",
        json={"blocked": True, "reason": "Bloqueio preventivo solicitado pelo suporte."},
    )
    assert block.status_code == 200
    assert block.json()["membership_status"] == "suspended"
    await db_session.refresh(session)
    assert session.revoked_at is not None
    assert session.revocation_reason == "platform_membership_suspended"
    audit = await db_session.scalar(
        select(AuthSecurityEvent).where(
            AuthSecurityEvent.event_type == "platform_workspace_user_access_changed",
            AuthSecurityEvent.subject_user_id == user.id,
        )
    )
    assert audit is not None
    assert audit.details["revoked_sessions"] == 1
    canonical_block = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.action == "set_person_blocked",
            PlatformAuditEvent.resource_id == f"{workspace.id}:{user.id}",
        ).order_by(PlatformAuditEvent.occurred_at.desc())
    )
    assert canonical_block is not None
    assert canonical_block.before["status"] == "active"
    assert canonical_block.after["status"] == "suspended"
    assert canonical_block.before["activeSessions"] == 1
    assert canonical_block.after["activeSessions"] == 0
    assert {"status", "activeSessions"}.issubset(canonical_block.changed_paths)

    unblock = await platform_client.patch(
        f"/api/v1/platform/people/{workspace.id}/{user.id}/block",
        json={"blocked": False, "reason": "Reativação aprovada após validação do acesso."},
    )
    assert unblock.status_code == 200
    assert unblock.json()["membership_status"] == "active"


async def test_platform_root_revokes_all_person_sessions_without_suspending_membership(
    db_session,
    platform_client,
):
    workspace, _sub, user = await _seed_subscription(db_session)
    now = datetime.now(UTC)
    sessions = [
        UserSession(
            user_id=user.id,
            refresh_jti_digest=uuid4().hex + uuid4().hex,
            device_label=f"Dispositivo {index}",
            device_type="desktop",
            browser="Chrome",
            operating_system="macOS",
            last_workspace_id=workspace.id,
            last_seen_at=now,
            expires_at=now + timedelta(days=1),
        )
        for index in range(2)
    ]
    db_session.add_all(sessions)
    await db_session.commit()

    response = await platform_client.post(
        f"/api/v1/platform/people/{workspace.id}/{user.id}/sessions/revoke",
        json={"reason": "Revogação preventiva de todos os dispositivos ativos."},
    )
    assert response.status_code == 200
    assert response.json()["revoked_count"] == 2
    for session in sessions:
        await db_session.refresh(session)
        assert session.revoked_at is not None
        assert session.revocation_reason == "platform_workspace_user_sessions_revoked"
    membership = await db_session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace.id,
            WorkspaceMembership.user_id == user.id,
        )
    )
    assert membership is not None
    assert membership.status == MembershipStatus.active
    audit = await db_session.scalar(
        select(AuthSecurityEvent).where(
            AuthSecurityEvent.event_type == "platform_workspace_user_sessions_revoked",
            AuthSecurityEvent.subject_user_id == user.id,
        )
    )
    assert audit is not None
    assert audit.details["revoked_count"] == 2
    canonical = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.action == "revoke_person_sessions",
            PlatformAuditEvent.resource_id == f"{workspace.id}:{user.id}",
        ).order_by(PlatformAuditEvent.occurred_at.desc())
    )
    assert canonical is not None
    assert canonical.before["activeSessions"] == 2
    assert canonical.after["activeSessions"] == 0
    assert "activeSessions" in canonical.changed_paths


async def test_people_access_package_catalog_and_change(db_session, platform_client):
    workspace, _sub, user = await _seed_subscription(db_session)

    catalog = await platform_client.get("/api/v1/platform/people/access-packages")
    assert catalog.status_code == 200
    assert any(item["key"] == "office_admin" for item in catalog.json())
    assert all("workspaceRole" in item for item in catalog.json())

    update = await platform_client.patch(
        f"/api/v1/platform/people/{workspace.id}/{user.id}/access-package",
        json={"accessPackage": "finance", "reason": "Transferência autorizada para a equipe financeira."},
    )
    assert update.status_code == 200
    assert update.json()["access_package"] == "finance"
    assert update.json()["role"] == "finance_manager"

    membership = await db_session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace.id,
            WorkspaceMembership.user_id == user.id,
        )
    )
    assert membership is not None
    assert membership.access_package == "finance"
    assert membership.role == WorkspaceRole.finance_manager
    canonical = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.action == "set_person_access_package",
            PlatformAuditEvent.resource_id == f"{workspace.id}:{user.id}",
        ).order_by(PlatformAuditEvent.occurred_at.desc())
    )
    assert canonical is not None
    assert canonical.before["accessPackage"] is None
    assert canonical.after["accessPackage"] == "finance"
    assert {"accessPackage", "role"}.issubset(canonical.changed_paths)

    invalid = await platform_client.patch(
        f"/api/v1/platform/people/{workspace.id}/{user.id}/access-package",
        json={"accessPackage": "pacote_inexistente", "reason": "Validação de pacote administrativo inexistente."},
    )
    assert invalid.status_code == 404


async def test_platform_root_controls_workspace_invitation_lifecycle(
    db_session,
    platform_client,
):
    workspace, _sub, _user = await _seed_subscription(db_session)
    email = f"convidada-{uuid4().hex[:8]}@escritorio.com.br"

    invite = await platform_client.post(
        "/api/v1/platform/people/invitations",
        headers={"Idempotency-Key": f"people-invite-{uuid4()}"},
        json={
            "workspaceId": str(workspace.id),
            "name": "Pessoa Convidada",
            "email": email,
            "accessPackage": "finance",
        },
    )
    assert invite.status_code == 201
    payload = invite.json()
    assert payload["workspace_id"] == str(workspace.id)
    assert payload["status"] == "pending"
    invitation_id = payload["id"]

    people = await platform_client.get(
        "/api/v1/platform/people",
        params={"search": email},
    )
    assert people.status_code == 200
    person = people.json()["items"][0]
    assert person["invitation_id"] == invitation_id
    assert person["invitation_status"] == "pending"

    resend = await platform_client.post(
        f"/api/v1/platform/people/invitations/{workspace.id}/{invitation_id}/resend",
        headers={"Idempotency-Key": f"people-resend-{uuid4()}"},
    )
    assert resend.status_code == 200
    assert resend.json()["status"] == "pending"

    revoke = await platform_client.delete(
        f"/api/v1/platform/people/invitations/{workspace.id}/{invitation_id}",
    )
    assert revoke.status_code == 200
    assert revoke.json()["status"] == "revoked"

    invitation = await db_session.get(WorkspaceInvitation, invitation_id)
    assert invitation is not None
    assert invitation.status == InvitationStatus.revoked
    events = (
        await db_session.execute(
            select(AuthSecurityEvent.event_type).where(
                AuthSecurityEvent.subject_user_id == invitation.user_id,
                AuthSecurityEvent.event_type.in_({
                    "platform_workspace_user_invited",
                    "platform_workspace_invitation_resent",
                    "platform_workspace_invitation_revoked",
                }),
            )
        )
    ).scalars().all()
    assert set(events) == {
        "platform_workspace_user_invited",
        "platform_workspace_invitation_resent",
        "platform_workspace_invitation_revoked",
    }
    canonical_events = list(await db_session.scalars(
        select(PlatformAuditEvent)
        .where(PlatformAuditEvent.resource_id == invitation_id)
        .order_by(PlatformAuditEvent.occurred_at.asc())
    ))
    assert [item.action for item in canonical_events] == [
        "invite_person",
        "resend_person_invitation",
        "revoke_person_invitation",
    ]
    assert canonical_events[0].before is None
    assert canonical_events[-1].before["status"] == "pending"
    assert canonical_events[-1].after["status"] == "revoked"


async def test_non_platform_user_cannot_invite_workspace_person(client):
    response = await client.post(
        "/api/v1/platform/people/invitations",
        headers={"Idempotency-Key": f"people-invite-{uuid4()}"},
        json={
            "workspaceId": str(uuid4()),
            "name": "Pessoa Sem Permissão",
            "email": "sem-permissao@example.com",
            "accessPackage": "finance",
        },
    )
    assert response.status_code == 403


async def test_person_invitation_rolls_back_when_canonical_audit_fails(
    db_session,
    platform_client,
    monkeypatch,
):
    workspace, _sub, _user = await _seed_subscription(db_session)
    email = f"rollback-invite-{uuid4().hex[:8]}@example.com"

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(PlatformAuditEventService, "record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        await platform_client.post(
            "/api/v1/platform/people/invitations",
            headers={"Idempotency-Key": f"people-invite-{uuid4()}"},
            json={
                "workspaceId": str(workspace.id),
                "name": "Convite com rollback",
                "email": email,
                "accessPackage": "finance",
            },
        )

    db_session.expire_all()
    invitation = await db_session.scalar(
        select(WorkspaceInvitation).where(WorkspaceInvitation.invited_email == email)
    )
    invited_user = await db_session.scalar(select(User).where(User.email == email))
    assert invitation is None
    assert invited_user is None


async def test_platform_root_transfers_workspace_ownership_atomically(
    db_session,
    platform_client,
):
    workspace, _sub, old_owner = await _seed_subscription(db_session)
    old_membership = await db_session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace.id,
            WorkspaceMembership.user_id == old_owner.id,
        )
    )
    assert old_membership is not None
    old_membership.role = WorkspaceRole.workspace_owner
    old_membership.access_package = "office_owner"
    new_owner = User(
        name="Nova Titular",
        email=f"nova-titular-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="NT",
        is_active=True,
    )
    db_session.add(new_owner)
    await db_session.flush()
    new_membership = WorkspaceMembership(
        workspace_id=workspace.id,
        user_id=new_owner.id,
        role=WorkspaceRole.workspace_admin,
        status=MembershipStatus.active,
        access_package="office_admin",
    )
    db_session.add(new_membership)
    now = datetime.now(UTC)
    db_session.add_all([
        UserSession(
            user_id=user_id,
            refresh_jti_digest=uuid4().hex + uuid4().hex,
            device_label="Sessão de titularidade",
            device_type="desktop",
            browser="Chrome",
            operating_system="macOS",
            last_workspace_id=workspace.id,
            last_seen_at=now,
            expires_at=now + timedelta(days=1),
        )
        for user_id in (old_owner.id, new_owner.id)
    ])
    await db_session.commit()

    response = await platform_client.post(
        f"/api/v1/platform/people/{workspace.id}/{new_owner.id}/transfer-ownership",
        json={"reason": "Alteração formal solicitada no ticket SUP-100."},
    )
    assert response.status_code == 200
    assert response.json()["owner_user_id"] == str(new_owner.id)
    assert response.json()["demoted_user_ids"] == [str(old_owner.id)]
    assert response.json()["revoked_sessions"] == 2
    await db_session.refresh(old_membership)
    await db_session.refresh(new_membership)
    assert old_membership.role == WorkspaceRole.workspace_admin
    assert old_membership.access_package == "office_admin"
    assert new_membership.role == WorkspaceRole.workspace_owner
    assert new_membership.access_package == "office_owner"
    audit = await db_session.scalar(
        select(AuthSecurityEvent).where(
            AuthSecurityEvent.event_type == "platform_workspace_ownership_transferred",
            AuthSecurityEvent.subject_user_id == new_owner.id,
        )
    )
    assert audit is not None
    assert audit.details["reason"].startswith("Alteração formal")
    canonical = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.action == "transfer_workspace_ownership",
            PlatformAuditEvent.resource_id == str(workspace.id),
        ).order_by(PlatformAuditEvent.occurred_at.desc())
    )
    assert canonical is not None
    before_roles = {item["userId"]: item["role"] for item in canonical.before["members"]}
    after_roles = {item["userId"]: item["role"] for item in canonical.after["members"]}
    assert before_roles[str(old_owner.id)] == "workspace_owner"
    assert after_roles[str(old_owner.id)] == "workspace_admin"
    assert after_roles[str(new_owner.id)] == "workspace_owner"


async def test_person_access_package_rolls_back_when_canonical_audit_fails(
    db_session,
    platform_client,
    monkeypatch,
):
    workspace, _sub, user = await _seed_subscription(db_session)
    membership = await db_session.scalar(select(WorkspaceMembership).where(
        WorkspaceMembership.workspace_id == workspace.id,
        WorkspaceMembership.user_id == user.id,
    ))
    assert membership is not None
    role_before = membership.role
    package_before = membership.access_package

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(PlatformAuditEventService, "record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        await platform_client.patch(
            f"/api/v1/platform/people/{workspace.id}/{user.id}/access-package",
            json={
                "accessPackage": "finance",
                "reason": "Teste de rollback atômico do vínculo de usuário.",
            },
        )

    db_session.expire_all()
    persisted = await db_session.get(WorkspaceMembership, membership.id)
    assert persisted is not None
    assert persisted.role == role_before
    assert persisted.access_package == package_before
