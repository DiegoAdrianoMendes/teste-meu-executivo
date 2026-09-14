from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_auth_context
from app.core.security import (
    create_access_token,
    create_admin_session_token,
    create_support_access_token,
    verify_token,
)
from app.db.tenant import WORKSPACE_SESSION_KEY
from app.main import app as main_app
from app.models.auth_security import (
    AdministrativeSession,
    MfaChallenge,
    MfaChallengePurpose,
    MfaVerificationMethod,
)
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.support_access import (
    SupportAccessEvent,
    SupportAccessGrant,
    SupportAccessScope,
    SupportAccessState,
)
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.services.platform_audit_event_service import PlatformAuditEventService


@dataclass
class SupportActors:
    workspace: Workspace
    other_workspace: Workspace
    platform_admin: User
    staff: User
    owner: User
    other_owner: User
    viewer: User
    owner_membership: WorkspaceMembership
    other_owner_membership: WorkspaceMembership
    viewer_membership: WorkspaceMembership


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _request(path: str = "/api/v1/workspaces/plan-summary") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "root_path": "",
        }
    )


def test_platform_support_access_commands_publish_canonical_contracts() -> None:
    operations = main_app.openapi()["paths"]
    for path in (
        "/api/v1/platform/support-access/root-direct/{workspace_id}",
        "/api/v1/platform/support-access/requests",
        "/api/v1/platform/support-access/requests/{grant_id}/activate",
    ):
        descriptor = operations[path]["post"]["x-platform-mutation"]
        assert descriptor["mutationKind"] == "command", path
        assert descriptor["snapshotAdapter"] == "support_access_grant", path


def _access_token(
    user: User,
    *,
    workspace: Workspace | None = None,
    membership: WorkspaceMembership | None = None,
    role: WorkspaceRole | None = None,
) -> str:
    return create_access_token(
        user.id,
        workspace_id=workspace.id if workspace else None,
        membership_id=membership.id if membership else None,
        role=role.value if role else None,
        is_internal_staff=user.is_internal_staff,
        auth_epoch=user.auth_epoch,
    )


async def _seed_actors(db: AsyncSession) -> SupportActors:
    workspace = Workspace(
        name="Workspace Principal",
        slug=f"workspace-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    other_workspace = Workspace(
        name="Workspace Externo",
        slug=f"workspace-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db.add_all([workspace, other_workspace])
    await db.flush()

    email_verified_at = datetime.now(UTC)
    platform_admin = User(
        name="Platform Admin",
        email=f"platform-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="PA",
        color="#123456",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=True,
        platform_access_active=True,
        platform_role_package="platform_owner",
        email_verified_at=email_verified_at,
    )
    staff = User(
        name="Internal Staff",
        email=f"staff-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="IS",
        color="#123456",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=False,
        email_verified_at=email_verified_at,
    )
    owner = User(
        name="Workspace Owner",
        email=f"owner-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="WO",
        color="#123456",
        is_active=True,
        email_verified_at=email_verified_at,
    )
    other_owner = User(
        name="Other Owner",
        email=f"other-owner-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="OO",
        color="#123456",
        is_active=True,
        email_verified_at=email_verified_at,
    )
    viewer = User(
        name="Workspace Viewer",
        email=f"viewer-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="WV",
        color="#123456",
        is_active=True,
        email_verified_at=email_verified_at,
    )
    db.add_all([platform_admin, staff, owner, other_owner, viewer])
    await db.flush()

    owner_membership = WorkspaceMembership(
        workspace_id=workspace.id,
        user_id=owner.id,
        role=WorkspaceRole.workspace_owner,
        status=MembershipStatus.active,
        is_default=True,
    )
    other_owner_membership = WorkspaceMembership(
        workspace_id=other_workspace.id,
        user_id=other_owner.id,
        role=WorkspaceRole.workspace_owner,
        status=MembershipStatus.active,
        is_default=True,
    )
    viewer_membership = WorkspaceMembership(
        workspace_id=workspace.id,
        user_id=viewer.id,
        role=WorkspaceRole.viewer,
        status=MembershipStatus.active,
        is_default=True,
    )
    db.add_all([owner_membership, other_owner_membership, viewer_membership])
    await db.commit()
    return SupportActors(
        workspace=workspace,
        other_workspace=other_workspace,
        platform_admin=platform_admin,
        staff=staff,
        owner=owner,
        other_owner=other_owner,
        viewer=viewer,
        owner_membership=owner_membership,
        other_owner_membership=other_owner_membership,
        viewer_membership=viewer_membership,
    )


async def _request_grant(
    client,
    actors: SupportActors,
    *,
    ticket: str,
    scopes: list[str],
    duration: int = 15,
):
    platform_token = _access_token(actors.platform_admin)
    return await client.post(
        "/api/v1/platform/support-access/requests",
        headers=_bearer(platform_token),
        json={
            "workspaceId": str(actors.workspace.id),
            "reason": "Diagnóstico local necessário para restaurar o acesso tenant.",
            "ticketReference": ticket,
            "requestedScopes": scopes,
            "durationMinutes": duration,
        },
    )


async def _approve_grant(
    client,
    actors: SupportActors,
    grant_id: str,
    *,
    scopes: list[str],
    duration: int = 15,
):
    owner_token = _access_token(
        actors.owner,
        workspace=actors.workspace,
        membership=actors.owner_membership,
        role=WorkspaceRole.workspace_owner,
    )
    return await client.post(
        f"/api/v1/workspaces/support-access/requests/{grant_id}/approve",
        headers=_bearer(owner_token),
        json={"approvedScopes": scopes, "durationMinutes": duration},
    )


async def _administrative_session(
    db: AsyncSession,
    platform_admin: User,
    *,
    workspace_id: UUID | None = None,
    include_challenge: bool = True,
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
    token_expires_at: datetime | None = None,
) -> tuple[str, UUID]:
    now = datetime.now(UTC)
    session_id = uuid4()
    issued_at = created_at or now
    session_expires_at = expires_at or (now + timedelta(minutes=15))
    token = create_admin_session_token(
        platform_admin.id,
        session_id=session_id,
        expires_at=token_expires_at or session_expires_at,
        mfa_method=MfaVerificationMethod.challenge.value,
        auth_epoch=platform_admin.auth_epoch,
    )
    payload = verify_token(token)
    assert payload is not None
    challenge_id = None
    if include_challenge:
        challenge = MfaChallenge(
            user_id=platform_admin.id,
            workspace_id=workspace_id,
            purpose=MfaChallengePurpose.admin_session,
            code_digest="test-consumed-administrative-challenge",
            attempts_remaining=5,
            expires_at=issued_at + timedelta(minutes=5),
            consumed_at=issued_at,
            created_at=issued_at,
        )
        db.add(challenge)
        await db.flush()
        challenge_id = challenge.id
    db.add(
        AdministrativeSession(
            id=session_id,
            user_id=platform_admin.id,
            challenge_id=challenge_id,
            token_jti=payload["jti"],
            mfa_method=MfaVerificationMethod.challenge,
            expires_at=session_expires_at,
            last_verified_at=issued_at,
            created_at=issued_at,
        )
    )
    await db.commit()
    return token, session_id


async def _admin_session_token(db: AsyncSession, platform_admin: User) -> str:
    token, _session_id = await _administrative_session(db, platform_admin)
    return token


async def _activate_grant(
    client,
    db: AsyncSession,
    actors: SupportActors,
    grant_id: str,
):
    admin_session = await _admin_session_token(db, actors.platform_admin)
    return await client.post(
        f"/api/v1/platform/support-access/requests/{grant_id}/activate",
        headers={
            **_bearer(_access_token(actors.platform_admin)),
            "X-Administrative-Session": admin_session,
        },
    )


@pytest.fixture(autouse=True)
def disable_token_store_io(monkeypatch):
    # Default this module to the isolated local contract. Tests that exercise
    # the AWS gate override these values explicitly below.
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_ADMINISTRATIVE_ACCESS_ENABLED", False)
    monkeypatch.setattr(
        "app.core.deps.is_token_revoked",
        AsyncMock(return_value=False),
    )


async def test_platform_root_enters_workspace_without_approval_after_fresh_mfa(
    unauthenticated_client,
    db_session: AsyncSession,
) -> None:
    actors = await _seed_actors(db_session)
    admin_session_token, admin_session_id = await _administrative_session(
        db_session,
        actors.platform_admin,
    )

    response = await unauthenticated_client.post(
        f"/api/v1/platform/support-access/root-direct/{actors.workspace.id}",
        headers={
            **_bearer(_access_token(actors.platform_admin)),
            "X-Administrative-Session": admin_session_token,
        },
        json={"reason": "Diagnóstico root solicitado pela Central de Comandos."},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["tokenType"] == "support_access"
    assert payload["grant"]["state"] == "active"
    assert payload["grant"]["requestedAt"].endswith("+00:00")
    assert payload["grant"]["approvedAt"].endswith("+00:00")
    assert payload["grant"]["activatedAt"].endswith("+00:00")
    assert payload["grant"]["expiresAt"].endswith("+00:00")
    assert payload["grant"]["createdAt"].endswith("+00:00")
    assert payload["grant"]["updatedAt"].endswith("+00:00")
    assert payload["grant"]["approverUserId"] is None
    assert payload["grant"]["workspaceId"] == str(actors.workspace.id)
    assert len(payload["grant"]["approvedScopes"]) == len(SupportAccessScope)
    event = await db_session.scalar(
        select(SupportAccessEvent).where(
            SupportAccessEvent.grant_id == UUID(payload["grant"]["id"]),
            SupportAccessEvent.event_type == "support_access_root_direct",
        )
    )
    assert event is not None
    assert event.actor_user_id == actors.platform_admin.id
    assert event.details["approval_required"] is False
    assert event.details["mfa_required"] is True
    assert event.details["mfa_verified"] is True
    assert (
        event.details["reason"]
        == "Diagnóstico root solicitado pela Central de Comandos."
    )
    grant = await db_session.get(SupportAccessGrant, UUID(payload["grant"]["id"]))
    assert grant is not None
    assert grant.reason == "Diagnóstico root solicitado pela Central de Comandos."
    assert grant.activation_admin_session_id == admin_session_id
    canonical = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_id == payload["grant"]["id"],
            PlatformAuditEvent.action == "support_access.root_direct",
        )
    )
    assert canonical is not None
    assert canonical.session_type == "root_direct"
    assert canonical.administrative_session_id == admin_session_id
    assert canonical.is_platform_root is True
    assert canonical.before is None
    assert canonical.after["state"] == "active"
    assert canonical.reason == "Diagnóstico root solicitado pela Central de Comandos."
    assert canonical.reason_source == "user"
    assert "supportAccessToken" not in str(canonical.after)

    auth_context = await get_current_auth_context(
        _request(),
        HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials=payload["supportAccessToken"],
        ),
        db_session,
    )
    assert auth_context.administrative_session_type == "root_direct"
    assert auth_context.administrative_session_id == admin_session_id

    support_headers = _bearer(payload["supportAccessToken"])
    tasks = await unauthenticated_client.get(
        "/api/v1/tasks",
        params={"scope": "workspace", "limit": 50},
        headers=support_headers,
    )
    assert tasks.status_code == 200, tasks.text
    assert tasks.json() == {"items": [], "nextCursor": None, "hasMore": False}
    areas = await unauthenticated_client.get(
        "/api/v1/klm/areas",
        headers=support_headers,
    )
    assert areas.status_code == 200, areas.text
    health_area = next(
        area for area in areas.json() if area["areaKey"] == "saude_suplementar"
    )
    activated = await unauthenticated_client.patch(
        f"/api/v1/klm/areas/{health_area['id']}/activation",
        headers=support_headers,
        json={"enabled": True},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["isActive"] is True


async def test_platform_root_direct_access_requires_a_specific_reason(
    unauthenticated_client,
    db_session: AsyncSession,
) -> None:
    actors = await _seed_actors(db_session)
    admin_session_token, _ = await _administrative_session(
        db_session,
        actors.platform_admin,
    )

    response = await unauthenticated_client.post(
        f"/api/v1/platform/support-access/root-direct/{actors.workspace.id}",
        headers={
            **_bearer(_access_token(actors.platform_admin)),
            "X-Administrative-Session": admin_session_token,
        },
    )
    short_reason = await unauthenticated_client.post(
        f"/api/v1/platform/support-access/root-direct/{actors.workspace.id}",
        headers={
            **_bearer(_access_token(actors.platform_admin)),
            "X-Administrative-Session": admin_session_token,
        },
        json={"reason": "a       "},
    )

    assert response.status_code == 422, response.text
    assert short_reason.status_code == 422, short_reason.text
    assert (
        await db_session.scalar(
            select(func.count()).select_from(SupportAccessGrant)
        )
        == 0
    )


async def test_platform_root_direct_access_works_in_aws_with_explicit_gate(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    actors = await _seed_actors(db_session)
    admin_session_token, admin_session_id = await _administrative_session(
        db_session,
        actors.platform_admin,
    )
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "production")
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", True)
    monkeypatch.setattr(settings, "KLM_ADMINISTRATIVE_ACCESS_ENABLED", True)

    response = await unauthenticated_client.post(
        f"/api/v1/platform/support-access/root-direct/{actors.workspace.id}",
        headers={
            **_bearer(_access_token(actors.platform_admin)),
            "X-Administrative-Session": admin_session_token,
        },
        json={"reason": "Diagnóstico root em ambiente AWS explicitamente autorizado."},
    )

    assert response.status_code == 200, response.text
    assert response.json()["grant"]["state"] == "active"
    canonical = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_id == response.json()["grant"]["id"],
            PlatformAuditEvent.action == "support_access.root_direct",
        )
    )
    assert canonical is not None
    assert canonical.session_type == "root_direct"
    assert canonical.is_platform_root is True
    assert canonical.administrative_session_id == admin_session_id


async def test_platform_root_direct_requires_a_fresh_actor_bound_admin_session(
    unauthenticated_client,
    db_session: AsyncSession,
) -> None:
    actors = await _seed_actors(db_session)
    root_headers = _bearer(_access_token(actors.platform_admin))
    endpoint = f"/api/v1/platform/support-access/root-direct/{actors.workspace.id}"

    missing = await unauthenticated_client.post(
        endpoint,
        headers=root_headers,
        json={"reason": "Diagnóstico root que exige confirmação MFA recente."},
    )
    assert missing.status_code == 401
    assert missing.json()["detail"]["code"] == "SUPPORT-ROOT-STEP-UP-REQUIRED"

    now = datetime.now(UTC)
    expired_token, _ = await _administrative_session(
        db_session,
        actors.platform_admin,
        created_at=now - timedelta(minutes=16),
        expires_at=now - timedelta(seconds=1),
        token_expires_at=now + timedelta(minutes=5),
    )
    expired = await unauthenticated_client.post(
        endpoint,
        headers={
            **root_headers,
            "X-Administrative-Session": expired_token,
        },
        json={"reason": "Diagnóstico root com sessão administrativa expirada."},
    )
    assert expired.status_code == 401
    assert expired.json()["detail"]["code"] == "AUTH-018"

    stale_token, _ = await _administrative_session(
        db_session,
        actors.platform_admin,
        created_at=now - timedelta(minutes=16),
        expires_at=now + timedelta(minutes=5),
    )
    stale = await unauthenticated_client.post(
        endpoint,
        headers={
            **root_headers,
            "X-Administrative-Session": stale_token,
        },
        json={"reason": "Diagnóstico root com step-up administrativo antigo."},
    )
    assert stale.status_code == 401
    assert stale.json()["detail"]["code"] == "SUPPORT-ROOT-STEP-UP-STALE"

    other_admin = User(
        name="Other Platform Admin",
        email=f"other-platform-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="OA",
        color="#123456",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=True,
        platform_access_active=True,
        platform_role_package="platform_admin",
        email_verified_at=now,
    )
    db_session.add(other_admin)
    await db_session.commit()
    other_actor_token, _ = await _administrative_session(db_session, other_admin)
    mismatched = await unauthenticated_client.post(
        endpoint,
        headers={
            **root_headers,
            "X-Administrative-Session": other_actor_token,
        },
        json={"reason": "Diagnóstico root com sessão MFA de outro operador."},
    )
    assert mismatched.status_code == 401, mismatched.text
    assert mismatched.json()["detail"]["code"] == "AUTH-018"
    assert await db_session.scalar(select(func.count(SupportAccessGrant.id))) == 0


async def test_root_direct_rejects_workspace_mismatch_and_session_without_mfa_proof(
    unauthenticated_client,
    db_session: AsyncSession,
) -> None:
    actors = await _seed_actors(db_session)
    endpoint = f"/api/v1/platform/support-access/root-direct/{actors.workspace.id}"
    headers = _bearer(_access_token(actors.platform_admin))

    other_workspace_token, _ = await _administrative_session(
        db_session,
        actors.platform_admin,
        workspace_id=actors.other_workspace.id,
    )
    mismatched_workspace = await unauthenticated_client.post(
        endpoint,
        headers={
            **headers,
            "X-Administrative-Session": other_workspace_token,
        },
        json={"reason": "Diagnóstico root com step-up de outro workspace."},
    )
    assert mismatched_workspace.status_code == 401
    assert mismatched_workspace.json()["detail"]["code"] == "AUTH-018"

    no_challenge_token, _ = await _administrative_session(
        db_session,
        actors.platform_admin,
        include_challenge=False,
    )
    missing_mfa_proof = await unauthenticated_client.post(
        endpoint,
        headers={
            **headers,
            "X-Administrative-Session": no_challenge_token,
        },
        json={"reason": "Diagnóstico root sem prova MFA persistida."},
    )
    assert missing_mfa_proof.status_code == 401
    assert missing_mfa_proof.json()["detail"]["code"] == "AUTH-018"
    assert await db_session.scalar(select(func.count(SupportAccessGrant.id))) == 0


async def test_derived_token_stops_immediately_when_admin_session_is_revoked_or_expired(
    unauthenticated_client,
    db_session: AsyncSession,
) -> None:
    actors = await _seed_actors(db_session)
    endpoint = f"/api/v1/platform/support-access/root-direct/{actors.workspace.id}"
    root_headers = _bearer(_access_token(actors.platform_admin))

    revoked_admin_token, revoked_admin_id = await _administrative_session(
        db_session,
        actors.platform_admin,
    )
    activated = await unauthenticated_client.post(
        endpoint,
        headers={
            **root_headers,
            "X-Administrative-Session": revoked_admin_token,
        },
        json={"reason": "Diagnóstico root antes da revogação do step-up."},
    )
    assert activated.status_code == 200, activated.text
    revoked_session = await db_session.get(AdministrativeSession, revoked_admin_id)
    assert revoked_session is not None
    revoked_session.revoked_at = datetime.now(UTC)
    revoked_session.revoked_by_user_id = actors.platform_admin.id
    revoked_session.revocation_reason = "security_test"
    await db_session.commit()

    denied_after_revoke = await unauthenticated_client.get(
        "/api/v1/workspaces/plan-summary",
        headers=_bearer(activated.json()["supportAccessToken"]),
    )
    assert denied_after_revoke.status_code == 401
    assert denied_after_revoke.json()["detail"]["code"] == "SUPPORT-TOKEN-INVALID"

    expiring_admin_token, expiring_admin_id = await _administrative_session(
        db_session,
        actors.platform_admin,
    )
    second = await unauthenticated_client.post(
        endpoint,
        headers={
            **root_headers,
            "X-Administrative-Session": expiring_admin_token,
        },
        json={"reason": "Diagnóstico root antes da expiração do step-up."},
    )
    assert second.status_code == 200, second.text
    expired_session = await db_session.get(AdministrativeSession, expiring_admin_id)
    assert expired_session is not None
    expired_session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    denied_after_expiry = await unauthenticated_client.get(
        "/api/v1/workspaces/plan-summary",
        headers=_bearer(second.json()["supportAccessToken"]),
    )
    assert denied_after_expiry.status_code == 401
    assert denied_after_expiry.json()["detail"]["code"] == "SUPPORT-TOKEN-INVALID"


async def test_support_approved_token_stops_when_activating_admin_session_is_revoked(
    unauthenticated_client,
    db_session: AsyncSession,
) -> None:
    actors = await _seed_actors(db_session)
    requested = await _request_grant(
        unauthenticated_client,
        actors,
        ticket="LOCAL-ADMIN-SESSION-REVOKED",
        scopes=["workspace:read"],
    )
    grant_id = requested.json()["id"]
    await _approve_grant(
        unauthenticated_client,
        actors,
        grant_id,
        scopes=["workspace:read"],
    )
    activated = await _activate_grant(
        unauthenticated_client,
        db_session,
        actors,
        grant_id,
    )
    assert activated.status_code == 200, activated.text
    grant = await db_session.get(SupportAccessGrant, UUID(grant_id))
    assert grant is not None and grant.activation_admin_session_id is not None
    administrative_session = await db_session.get(
        AdministrativeSession,
        grant.activation_admin_session_id,
    )
    assert administrative_session is not None
    administrative_session.revoked_at = datetime.now(UTC)
    administrative_session.revoked_by_user_id = actors.platform_admin.id
    administrative_session.revocation_reason = "security_test"
    await db_session.commit()

    denied = await unauthenticated_client.get(
        "/api/v1/workspaces/plan-summary",
        headers=_bearer(activated.json()["supportAccessToken"]),
    )
    assert denied.status_code == 401
    assert denied.json()["detail"]["code"] == "SUPPORT-TOKEN-INVALID"


async def test_root_shaped_grant_without_append_only_root_event_fails_closed(
    unauthenticated_client,
    db_session: AsyncSession,
) -> None:
    actors = await _seed_actors(db_session)
    _admin_token, admin_session_id = await _administrative_session(
        db_session,
        actors.platform_admin,
    )
    now = datetime.now(UTC)
    token_jti = uuid4().hex
    scopes = [SupportAccessScope.workspace_read.value]
    grant = SupportAccessGrant(
        workspace_id=actors.workspace.id,
        requester_user_id=actors.platform_admin.id,
        reason="Grant root sintético sem a evidência append-only obrigatória.",
        ticket_reference=f"ROOT-{uuid4().hex}",
        requested_scopes=scopes,
        requested_duration_minutes=15,
        state=SupportAccessState.active,
        requested_at=now,
        approver_user_id=None,
        approved_scopes=scopes,
        approved_duration_minutes=15,
        approved_at=now,
        activated_by_user_id=actors.platform_admin.id,
        activation_admin_session_id=admin_session_id,
        active_token_jti=token_jti,
        activated_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    db_session.add(grant)
    await db_session.commit()
    forged = create_support_access_token(
        actors.platform_admin.id,
        grant_id=grant.id,
        workspace_id=actors.workspace.id,
        scopes=scopes,
        expires_at=grant.expires_at,
        token_jti=token_jti,
    )

    denied = await unauthenticated_client.get(
        "/api/v1/workspaces/plan-summary",
        headers=_bearer(forged),
    )
    assert denied.status_code == 401
    assert denied.json()["detail"]["code"] == "SUPPORT-TOKEN-INVALID"


async def test_non_root_cannot_use_root_direct_access(
    unauthenticated_client,
    db_session: AsyncSession,
) -> None:
    actors = await _seed_actors(db_session)
    actors.staff.platform_role_package = "platform_admin"
    actors.staff.platform_access_active = True
    await db_session.commit()

    response = await unauthenticated_client.post(
        f"/api/v1/platform/support-access/root-direct/{actors.workspace.id}",
        headers=_bearer(_access_token(actors.staff)),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "PLATFORM-ROOT-REQUIRED"


async def test_staff_and_normal_tokens_cannot_access_tenant_or_request_grant(
    unauthenticated_client,
    db_session: AsyncSession,
) -> None:
    actors = await _seed_actors(db_session)
    staff_token = _access_token(
        actors.staff,
        workspace=actors.workspace,
        role=WorkspaceRole.workspace_owner,
    )
    denied = await unauthenticated_client.get(
        "/api/v1/workspaces/plan-summary",
        headers=_bearer(staff_token),
    )
    assert denied.status_code == 401
    assert denied.json()["detail"]["code"] == "AUTH-009"

    owner_token = _access_token(
        actors.owner,
        workspace=actors.workspace,
        membership=actors.owner_membership,
        role=WorkspaceRole.workspace_owner,
    )
    non_admin_request = await unauthenticated_client.post(
        "/api/v1/platform/support-access/requests",
        headers=_bearer(owner_token),
        json={
            "workspaceId": str(actors.workspace.id),
            "reason": "Tentativa sem papel de platform admin ativo.",
            "ticketReference": "LOCAL-NON-ADMIN",
            "requestedScopes": ["workspace:read"],
            "durationMinutes": 10,
        },
    )
    assert non_admin_request.status_code == 403
    assert non_admin_request.json()["detail"]["code"] == (
        "SUPPORT-PLATFORM-ADMIN-REQUIRED"
    )


async def test_administrative_access_is_fail_closed_without_explicit_environment_gate(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    actors = await _seed_actors(db_session)

    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", True)
    external_enabled = await _request_grant(
        unauthenticated_client,
        actors,
        ticket="LOCAL-EXTERNAL-BLOCKED",
        scopes=["workspace:read"],
    )
    assert external_enabled.status_code == 404
    assert external_enabled.json()["detail"]["code"] == (
        "SUPPORT-ADMINISTRATIVE-ACCESS-DISABLED"
    )

    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", True)
    aws_enabled = await _request_grant(
        unauthenticated_client,
        actors,
        ticket="LOCAL-AWS-BLOCKED",
        scopes=["workspace:read"],
    )
    assert aws_enabled.status_code == 404
    assert aws_enabled.json()["detail"]["code"] == (
        "SUPPORT-ADMINISTRATIVE-ACCESS-DISABLED"
    )
    assert await db_session.scalar(select(func.count(SupportAccessGrant.id))) == 0


async def test_support_activation_requires_gate_and_then_works_in_aws_runtime(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    actors = await _seed_actors(db_session)
    requested = await _request_grant(
        unauthenticated_client,
        actors,
        ticket="LOCAL-ACTIVATION-BLOCKED",
        scopes=["workspace:read"],
    )
    assert requested.status_code == 201, requested.text
    grant_id = requested.json()["id"]
    approved = await _approve_grant(
        unauthenticated_client,
        actors,
        grant_id,
        scopes=["workspace:read"],
    )
    assert approved.status_code == 200, approved.text

    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "production")
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", True)
    activated = await _activate_grant(
        unauthenticated_client,
        db_session,
        actors,
        grant_id,
    )
    assert activated.status_code == 404
    assert activated.json()["detail"]["code"] == (
        "SUPPORT-ADMINISTRATIVE-ACCESS-DISABLED"
    )
    grant = await db_session.get(SupportAccessGrant, UUID(grant_id))
    assert grant is not None
    assert grant.state == SupportAccessState.approved

    monkeypatch.setattr(settings, "KLM_ADMINISTRATIVE_ACCESS_ENABLED", True)
    activated = await _activate_grant(
        unauthenticated_client,
        db_session,
        actors,
        grant_id,
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["grant"]["state"] == "active"


async def test_request_and_approval_are_idempotent_and_workspace_bound(
    unauthenticated_client,
    db_session: AsyncSession,
) -> None:
    actors = await _seed_actors(db_session)
    requested = await _request_grant(
        unauthenticated_client,
        actors,
        ticket="LOCAL-IDEMPOTENT",
        scopes=["workspace:read"],
    )
    repeated = await _request_grant(
        unauthenticated_client,
        actors,
        ticket="LOCAL-IDEMPOTENT",
        scopes=["workspace:read"],
    )
    assert requested.status_code == 201
    assert repeated.status_code == 201
    grant_id = requested.json()["id"]
    assert repeated.json()["id"] == grant_id

    paged = await unauthenticated_client.get(
        "/api/v1/platform/support-access/requests/page",
        headers=_bearer(_access_token(actors.platform_admin)),
        params={"workspace_id": str(actors.workspace.id), "limit": 1, "offset": 0},
    )
    assert paged.status_code == 200, paged.text
    page_payload = paged.json()
    assert page_payload["total"] == 1
    assert page_payload["items"][0]["id"] == grant_id
    assert page_payload["hasMore"] is False
    assert page_payload["sort"][-1] == {"field": "id", "direction": "desc"}

    approved = await _approve_grant(
        unauthenticated_client,
        actors,
        grant_id,
        scopes=["workspace:read"],
        duration=10,
    )
    repeated_approval = await _approve_grant(
        unauthenticated_client,
        actors,
        grant_id,
        scopes=["workspace:read"],
        duration=10,
    )
    assert approved.status_code == 200
    assert repeated_approval.status_code == 200
    assert approved.json()["state"] == "approved"

    assert await db_session.scalar(select(func.count(SupportAccessGrant.id))) == 1
    event_types = list(
        (
            await db_session.scalars(
                select(SupportAccessEvent.event_type).where(
                    SupportAccessEvent.grant_id == UUID(grant_id)
                )
            )
        ).all()
    )
    assert event_types.count("support_access_requested") == 1
    assert event_types.count("support_access_approved") == 1
    canonical_requests = list(
        await db_session.scalars(
            select(PlatformAuditEvent).where(
                PlatformAuditEvent.resource_id == grant_id,
                PlatformAuditEvent.action == "support_access.request",
            )
        )
    )
    assert len(canonical_requests) == 1
    assert canonical_requests[0].before is None
    assert canonical_requests[0].after["ticketReference"] == "LOCAL-IDEMPOTENT"
    assert canonical_requests[0].after["reasonPresent"] is True

    other_owner_token = _access_token(
        actors.other_owner,
        workspace=actors.other_workspace,
        membership=actors.other_owner_membership,
        role=WorkspaceRole.workspace_owner,
    )
    await db_session.rollback()
    db_session.sync_session.info.pop(WORKSPACE_SESSION_KEY, None)
    cross_tenant = await unauthenticated_client.post(
        f"/api/v1/workspaces/support-access/requests/{grant_id}/approve",
        headers=_bearer(other_owner_token),
        json={"approvedScopes": ["workspace:read"], "durationMinutes": 10},
    )
    assert cross_tenant.status_code == 404


async def test_support_request_rolls_back_when_canonical_audit_fails(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    actors = await _seed_actors(db_session)

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    before_count = int(
        (await db_session.scalar(select(func.count(SupportAccessGrant.id)))) or 0
    )
    monkeypatch.setattr(PlatformAuditEventService, "record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        await _request_grant(
            unauthenticated_client,
            actors,
            ticket="LOCAL-AUDIT-ROLLBACK",
            scopes=["workspace:read"],
        )

    db_session.expire_all()
    after_count = int(
        (await db_session.scalar(select(func.count(SupportAccessGrant.id)))) or 0
    )
    assert after_count == before_count


async def test_request_limits_approval_reduction_and_rejection_are_enforced(
    unauthenticated_client,
    db_session: AsyncSession,
) -> None:
    actors = await _seed_actors(db_session)

    invalid_duration = await _request_grant(
        unauthenticated_client,
        actors,
        ticket="LOCAL-TOO-LONG",
        scopes=["workspace:read"],
        duration=31,
    )
    assert invalid_duration.status_code == 422

    invalid_scope = await _request_grant(
        unauthenticated_client,
        actors,
        ticket="LOCAL-UNKNOWN-SCOPE",
        scopes=["workspace:delete"],
    )
    assert invalid_scope.status_code == 422

    requested = await _request_grant(
        unauthenticated_client,
        actors,
        ticket="LOCAL-REJECTED",
        scopes=["workspace:read"],
        duration=10,
    )
    assert requested.status_code == 201
    grant_id = requested.json()["id"]

    viewer_token = _access_token(
        actors.viewer,
        workspace=actors.workspace,
        membership=actors.viewer_membership,
        role=WorkspaceRole.viewer,
    )
    non_admin_approval = await unauthenticated_client.post(
        f"/api/v1/workspaces/support-access/requests/{grant_id}/approve",
        headers=_bearer(viewer_token),
        json={"approvedScopes": ["workspace:read"], "durationMinutes": 10},
    )
    assert non_admin_approval.status_code == 403

    escalated_approval = await _approve_grant(
        unauthenticated_client,
        actors,
        grant_id,
        scopes=["workspace:read", "workspace:write"],
        duration=10,
    )
    assert escalated_approval.status_code == 422
    assert escalated_approval.json()["detail"]["code"] == "SUPPORT-SCOPE-ESCALATION"

    owner_token = _access_token(
        actors.owner,
        workspace=actors.workspace,
        membership=actors.owner_membership,
        role=WorkspaceRole.workspace_owner,
    )
    rejected = await unauthenticated_client.post(
        f"/api/v1/workspaces/support-access/requests/{grant_id}/reject",
        headers=_bearer(owner_token),
        json={"reason": "Solicitação sem necessidade operacional atual."},
    )
    assert rejected.status_code == 200
    assert rejected.json()["state"] == "rejected"

    repeated_rejection = await unauthenticated_client.post(
        f"/api/v1/workspaces/support-access/requests/{grant_id}/reject",
        headers=_bearer(owner_token),
        json={"reason": "Solicitação sem necessidade operacional atual."},
    )
    assert repeated_rejection.status_code == 200
    assert repeated_rejection.json()["state"] == "rejected"

    event_types = list(
        (
            await db_session.scalars(
                select(SupportAccessEvent.event_type).where(
                    SupportAccessEvent.grant_id == UUID(grant_id)
                )
            )
        ).all()
    )
    assert event_types.count("support_access_rejected") == 1


async def test_activation_requires_step_up_and_scope_is_enforced_per_request(
    unauthenticated_client,
    db_session: AsyncSession,
) -> None:
    actors = await _seed_actors(db_session)
    requested = await _request_grant(
        unauthenticated_client,
        actors,
        ticket="LOCAL-SCOPED",
        scopes=["workspace:read", "workspace:write"],
    )
    grant_id = requested.json()["id"]
    await _approve_grant(
        unauthenticated_client,
        actors,
        grant_id,
        scopes=["workspace:read"],
        duration=10,
    )

    invalid_step_up = await unauthenticated_client.post(
        f"/api/v1/platform/support-access/requests/{grant_id}/activate",
        headers={
            **_bearer(_access_token(actors.platform_admin)),
            "X-Administrative-Session": "not-a-valid-admin-session",
        },
    )
    assert invalid_step_up.status_code == 401

    activated = await _activate_grant(
        unauthenticated_client,
        db_session,
        actors,
        grant_id,
    )
    assert activated.status_code == 200
    support_token = activated.json()["supportAccessToken"]
    assert activated.json()["tokenType"] == "support_access"
    activation_event = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_id == grant_id,
            PlatformAuditEvent.action == "support_access.activate",
        )
    )
    assert activation_event is not None
    assert activation_event.session_type == "support_approved"
    assert activation_event.administrative_session_id is not None
    assert activation_event.before["state"] == "approved"
    assert activation_event.after["state"] == "active"
    assert "supportAccessToken" not in str(activation_event.after)

    auth_context = await get_current_auth_context(
        _request(),
        HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials=support_token,
        ),
        db_session,
    )
    assert auth_context.administrative_session_type == "support_approved"
    activated_grant = await db_session.get(SupportAccessGrant, UUID(grant_id))
    assert activated_grant is not None
    assert (
        auth_context.administrative_session_id
        == activated_grant.activation_admin_session_id
    )

    allowed = await unauthenticated_client.get(
        "/api/v1/workspaces/plan-summary",
        headers=_bearer(support_token),
    )
    assert allowed.status_code == 200

    wrong_scope = await unauthenticated_client.patch(
        "/api/v1/workspaces/onboarding",
        headers=_bearer(support_token),
        json={"currentStep": "profile"},
    )
    assert wrong_scope.status_code == 403
    assert wrong_scope.json()["detail"]["code"] == "SUPPORT-SCOPE-DENIED"

    normal_platform_token = _access_token(
        actors.platform_admin,
        workspace=actors.workspace,
        role=WorkspaceRole.workspace_owner,
    )
    normal_still_denied = await unauthenticated_client.get(
        "/api/v1/workspaces/plan-summary",
        headers=_bearer(normal_platform_token),
    )
    assert normal_still_denied.status_code == 401
    assert normal_still_denied.json()["detail"]["code"] == "AUTH-009"

    second_activation = await _activate_grant(
        unauthenticated_client,
        db_session,
        actors,
        grant_id,
    )
    assert second_activation.status_code == 409


async def test_approved_members_write_updates_non_owner_and_protects_owner(
    unauthenticated_client,
    db_session: AsyncSession,
) -> None:
    actors = await _seed_actors(db_session)
    scopes = ["members:read", "members:write"]
    requested = await _request_grant(
        unauthenticated_client,
        actors,
        ticket="MEMBERS-WRITE",
        scopes=scopes,
    )
    assert requested.status_code == 201
    grant_id = requested.json()["id"]
    approved = await _approve_grant(
        unauthenticated_client,
        actors,
        grant_id,
        scopes=scopes,
    )
    assert approved.status_code == 200
    activated = await _activate_grant(
        unauthenticated_client,
        db_session,
        actors,
        grant_id,
    )
    assert activated.status_code == 200
    support_token = activated.json()["supportAccessToken"]

    listed = await unauthenticated_client.get(
        "/api/v1/admin/team-members",
        headers=_bearer(support_token),
    )
    assert listed.status_code == 200
    assert any(
        item["membershipId"] == str(actors.viewer_membership.id)
        for item in listed.json()
    )

    suspended = await unauthenticated_client.patch(
        f"/api/v1/admin/team-members/{actors.viewer_membership.id}",
        headers=_bearer(support_token),
        json={"status": "suspended"},
    )
    assert suspended.status_code == 200
    assert suspended.json()["status"] == "suspended"

    owner_blocked = await unauthenticated_client.patch(
        f"/api/v1/admin/team-members/{actors.owner_membership.id}",
        headers=_bearer(support_token),
        json={"status": "suspended"},
    )
    assert owner_blocked.status_code == 403
    assert owner_blocked.json()["detail"]["code"] == "SUPPORT-OWNER-PROTECTED"


async def test_cross_workspace_expired_and_revoked_grants_fail_closed(
    unauthenticated_client,
    db_session: AsyncSession,
) -> None:
    actors = await _seed_actors(db_session)
    requested = await _request_grant(
        unauthenticated_client,
        actors,
        ticket="LOCAL-BOUND",
        scopes=["workspace:read", "workspace:write"],
    )
    grant_id = requested.json()["id"]
    await _approve_grant(
        unauthenticated_client,
        actors,
        grant_id,
        scopes=["workspace:read", "workspace:write"],
        duration=10,
    )
    activated = await _activate_grant(
        unauthenticated_client,
        db_session,
        actors,
        grant_id,
    )
    support_token = activated.json()["supportAccessToken"]

    cross_workspace = await unauthenticated_client.patch(
        f"/api/v1/admin/workspaces/{actors.other_workspace.id}",
        headers=_bearer(support_token),
        json={"name": "Não alterar"},
    )
    assert cross_workspace.status_code == 403
    assert cross_workspace.json()["detail"]["code"] == "AUTH-013"

    grant = await db_session.get(SupportAccessGrant, UUID(grant_id))
    assert grant is not None
    grant.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    expired = await unauthenticated_client.get(
        "/api/v1/workspaces/plan-summary",
        headers=_bearer(support_token),
    )
    assert expired.status_code == 401
    grant = await db_session.get(SupportAccessGrant, UUID(grant_id))
    assert grant is not None
    assert grant.state == SupportAccessState.expired

    second = await _request_grant(
        unauthenticated_client,
        actors,
        ticket="LOCAL-REVOKED",
        scopes=["workspace:read"],
    )
    second_id = second.json()["id"]
    await _approve_grant(
        unauthenticated_client,
        actors,
        second_id,
        scopes=["workspace:read"],
        duration=10,
    )
    second_activation = await _activate_grant(
        unauthenticated_client,
        db_session,
        actors,
        second_id,
    )
    second_token = second_activation.json()["supportAccessToken"]
    owner_token = _access_token(
        actors.owner,
        workspace=actors.workspace,
        membership=actors.owner_membership,
        role=WorkspaceRole.workspace_owner,
    )
    revoked = await unauthenticated_client.post(
        f"/api/v1/workspaces/support-access/requests/{second_id}/revoke",
        headers=_bearer(owner_token),
        json={"reason": "Diagnóstico local concluído."},
    )
    assert revoked.status_code == 200
    assert revoked.json()["state"] == "revoked"
    denied_after_revoke = await unauthenticated_client.get(
        "/api/v1/workspaces/plan-summary",
        headers=_bearer(second_token),
    )
    assert denied_after_revoke.status_code == 401


async def test_token_claims_cannot_escape_grant_workspace(
    unauthenticated_client,
    db_session: AsyncSession,
) -> None:
    actors = await _seed_actors(db_session)
    requested = await _request_grant(
        unauthenticated_client,
        actors,
        ticket="LOCAL-FORGED-WORKSPACE",
        scopes=["workspace:read"],
    )
    grant_id = requested.json()["id"]
    await _approve_grant(
        unauthenticated_client,
        actors,
        grant_id,
        scopes=["workspace:read"],
        duration=10,
    )
    activated = await _activate_grant(
        unauthenticated_client,
        db_session,
        actors,
        grant_id,
    )
    grant = await db_session.get(SupportAccessGrant, UUID(grant_id))
    assert grant is not None and grant.expires_at and grant.active_token_jti
    forged = create_support_access_token(
        actors.platform_admin.id,
        grant_id=grant.id,
        workspace_id=actors.other_workspace.id,
        scopes=list(grant.approved_scopes or []),
        expires_at=grant.expires_at,
        token_jti=grant.active_token_jti,
    )
    assert activated.status_code == 200
    denied = await unauthenticated_client.get(
        "/api/v1/workspaces/plan-summary",
        headers=_bearer(forged),
    )
    assert denied.status_code == 401


async def test_support_event_history_returns_latest_500_in_chronological_order(
    unauthenticated_client,
    db_session: AsyncSession,
) -> None:
    actors = await _seed_actors(db_session)
    requested = await _request_grant(
        unauthenticated_client,
        actors,
        ticket="LOCAL-EVENT-WINDOW",
        scopes=["workspace:read"],
    )
    assert requested.status_code == 201, requested.text
    grant_id = UUID(requested.json()["id"])
    base = datetime(2030, 1, 1, tzinfo=UTC)
    db_session.add_all(
        [
            SupportAccessEvent(
                grant_id=grant_id,
                workspace_id=actors.workspace.id,
                actor_user_id=actors.platform_admin.id,
                event_type=f"event-{index:03d}",
                details={},
                occurred_at=base + timedelta(seconds=index),
            )
            for index in range(505)
        ]
    )
    await db_session.commit()

    workspace_requests = await unauthenticated_client.get(
        "/api/v1/workspaces/support-access/requests/page",
        params={"limit": 25, "offset": 0},
        headers=_bearer(
            _access_token(
                actors.owner,
                workspace=actors.workspace,
                membership=actors.owner_membership,
                role=WorkspaceRole.workspace_owner,
            )
        ),
    )
    assert workspace_requests.status_code == 200, workspace_requests.text
    assert workspace_requests.json()["total"] == 1
    assert workspace_requests.json()["items"][0]["id"] == str(grant_id)

    response = await unauthenticated_client.get(
        f"/api/v1/platform/support-access/requests/{grant_id}/events",
        headers=_bearer(_access_token(actors.platform_admin)),
    )

    assert response.status_code == 200, response.text
    events = response.json()
    assert len(events) == 500
    assert events[0]["eventType"] == "event-005"
    assert events[-1]["eventType"] == "event-504"

    platform_page = await unauthenticated_client.get(
        f"/api/v1/platform/support-access/requests/{grant_id}/events/page",
        params={"limit": 25, "offset": 0},
        headers=_bearer(_access_token(actors.platform_admin)),
    )
    assert platform_page.status_code == 200, platform_page.text
    page = platform_page.json()
    assert page["total"] == 506
    assert page["hasMore"] is True
    assert len(page["items"]) == 25
    assert page["items"][0]["eventType"] == "event-504"
    assert page["items"][-1]["eventType"] == "event-480"

    workspace_page = await unauthenticated_client.get(
        f"/api/v1/workspaces/support-access/requests/{grant_id}/events/page",
        params={"limit": 10, "offset": 500},
        headers=_bearer(
            _access_token(
                actors.owner,
                workspace=actors.workspace,
                membership=actors.owner_membership,
                role=WorkspaceRole.workspace_owner,
            )
        ),
    )
    assert workspace_page.status_code == 200, workspace_page.text
    workspace_payload = workspace_page.json()
    assert workspace_payload["total"] == 506
    assert workspace_payload["hasMore"] is False
    assert len(workspace_payload["items"]) == 6
