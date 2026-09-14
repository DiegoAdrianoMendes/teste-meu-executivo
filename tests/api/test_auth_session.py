import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Optional
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from jose import jwt
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.api.v1.endpoints.auth as auth_endpoints
import app.core.deps as auth_deps
import app.core.token_store as token_store
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    parse_session_token_id,
    verify_password,
    verify_token,
)
from app.models.auth_security import MfaCredential
from app.models.identity import (
    AuthRevocationIntent,
    AuthRevocationIntentStatus,
    UserSession,
)
from app.models.password_reset import PasswordResetToken
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.services import auth_revocation_service, mfa_service
from app.services.auth_revocation_service import AuthRevocationService
from app.services.integration_secrets import IntegrationSecretCipher


async def _create_authenticated_user(db: AsyncSession):
    workspace = Workspace(
        name="Workspace Sessao",
        slug=f"workspace-sessao-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        settings={},
    )
    db.add(workspace)
    await db.flush()

    user = User(
        name="Sessao Teste",
        email=f"sessao-{uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("senha-segura"),
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="ST",
        color="#111827",
        is_active=True,
        is_internal_staff=False,
        email_verified_at=datetime.now(UTC),
    )
    db.add(user)
    await db.flush()

    membership = WorkspaceMembership(
        workspace_id=workspace.id,
        user_id=user.id,
        role=WorkspaceRole.workspace_owner,
        status=MembershipStatus.active,
        is_default=True,
    )
    db.add(membership)
    await db.commit()
    await db.refresh(user)
    await db.refresh(workspace)
    return user, workspace, membership


async def _create_platform_user(db: AsyncSession):
    user = User(
        name="Equipe Blackbox",
        email=f"blackbox-{uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("senha-blackbox"),
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="BX",
        color="#0f172a",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=True,
        platform_role_package="platform_admin",
        platform_access_active=True,
        email_verified_at=datetime.now(UTC),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _refresh_cookie_header(response) -> str:
    cookie_prefix = f"{auth_endpoints.settings.AUTH_REFRESH_COOKIE_NAME}="
    return next(
        value
        for value in response.headers.get_list("set-cookie")
        if value.startswith(cookie_prefix)
    )


@pytest.mark.asyncio
async def test_login_sets_http_only_refresh_cookie_without_exposing_it_to_javascript(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
):
    user, workspace, _membership = await _create_authenticated_user(db_session)
    monkeypatch.setattr(auth_endpoints.settings, "APP_ENV", "development")
    monkeypatch.setattr(auth_endpoints.settings, "AUTH_REFRESH_COOKIE_SECURE", False)
    monkeypatch.setattr(auth_endpoints.settings, "AUTH_REFRESH_COOKIE_SAMESITE", "lax")

    response = await unauthenticated_client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "senha-segura"},
    )

    assert response.status_code == 200
    assert response.json()["refreshToken"] is None
    assert response.json()["session"]["activeWorkspace"]["id"] == str(workspace.id)
    cookie_header = _refresh_cookie_header(response)
    assert "HttpOnly" in cookie_header
    assert "SameSite=lax" in cookie_header
    assert "Path=/api/v1/auth" in cookie_header
    assert "Max-Age=604800" in cookie_header
    assert "Secure" not in cookie_header
    cookie_token = response.cookies.get(auth_endpoints.settings.AUTH_REFRESH_COOKIE_NAME)
    assert verify_token(cookie_token)["token_type"] == "refresh"


@pytest.mark.asyncio
async def test_blackbox_login_uses_isolated_platform_cookie_and_surface(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
):
    user = await _create_platform_user(db_session)
    monkeypatch.setattr(auth_endpoints.settings, "AUTH_REFRESH_COOKIE_SECURE", False)

    response = await unauthenticated_client.post(
        "/api/v1/auth/login",
        json={
            "email": user.email,
            "password": "senha-blackbox",
            "audience": "platform",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["session"]["surface"] == "platform"
    assert auth_endpoints.settings.AUTH_PLATFORM_REFRESH_COOKIE_NAME in response.cookies
    assert auth_endpoints.settings.AUTH_REFRESH_COOKIE_NAME not in response.cookies

    refreshed = await unauthenticated_client.post(
        "/api/v1/auth/refresh",
        headers={"X-Auth-Audience": "platform"},
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["session"]["surface"] == "platform"
    assert auth_endpoints.settings.AUTH_PLATFORM_REFRESH_COOKIE_NAME in refreshed.cookies


@pytest.mark.asyncio
async def test_blackbox_login_requires_mfa_enrollment_in_production(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
):
    user = await _create_platform_user(db_session)
    monkeypatch.setattr(auth_endpoints.settings, "APP_ENV", "production")

    response = await unauthenticated_client.post(
        "/api/v1/auth/login",
        json={
            "email": user.email,
            "password": "senha-blackbox",
            "audience": "platform",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "AUTH-BLACKBOX-MFA-ENROLLMENT-REQUIRED"
    assert auth_endpoints.settings.AUTH_PLATFORM_REFRESH_COOKIE_NAME not in response.cookies


@pytest.mark.asyncio
async def test_temporary_password_only_authorizes_mandatory_password_change(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
):
    user = await _create_platform_user(db_session)
    user.password_change_required = True
    user.temporary_password_issued_at = datetime.now(UTC)
    await db_session.commit()
    monkeypatch.setattr(auth_endpoints.settings, "APP_ENV", "production")
    monkeypatch.setattr(auth_endpoints.settings, "AUTH_REFRESH_COOKIE_SECURE", False)

    login = await unauthenticated_client.post(
        "/api/v1/auth/login",
        json={
            "email": user.email,
            "password": "senha-blackbox",
            "audience": "platform",
        },
    )

    assert login.status_code == 200, login.text
    assert login.json()["user"]["requiresPasswordChange"] is True
    access_token = login.json()["accessToken"]
    headers = {"Authorization": f"Bearer {access_token}"}

    protected = await unauthenticated_client.get("/api/v1/auth/me", headers=headers)
    assert protected.status_code == 403
    assert protected.json()["detail"]["code"] == "AUTH-PASSWORD-CHANGE-REQUIRED"

    mfa_status = await unauthenticated_client.get("/api/v1/auth/mfa", headers=headers)
    assert mfa_status.status_code == 200, mfa_status.text
    assert mfa_status.json()["enabled"] is False

    enrollment = await unauthenticated_client.post(
        "/api/v1/auth/mfa/enroll",
        headers=headers,
    )
    assert enrollment.status_code == 200, enrollment.text
    challenge_id = enrollment.json()["challengeId"]
    enrollment_code = enrollment.json()["simulatedCode"]
    verified = await unauthenticated_client.post(
        f"/api/v1/auth/mfa/enroll/{challenge_id}/verify",
        headers=headers,
        json={"code": enrollment_code},
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["recoveryCodes"]

    refreshed = await unauthenticated_client.post(
        "/api/v1/auth/refresh",
        headers={
            "X-Auth-Audience": "platform",
            "Cookie": (
                f"{auth_endpoints.settings.AUTH_PLATFORM_REFRESH_COOKIE_NAME}="
                f"{login.cookies[auth_endpoints.settings.AUTH_PLATFORM_REFRESH_COOKIE_NAME]}"
            ),
        },
    )
    assert refreshed.status_code == 403
    assert refreshed.json()["detail"]["code"] == "AUTH-PASSWORD-CHANGE-REQUIRED"

    changed = await unauthenticated_client.post(
        "/api/v1/auth/password",
        headers=headers,
        json={
            "currentPassword": "senha-blackbox",
            "newPassword": "SenhaDefinitivaForte#2026",
        },
    )
    assert changed.status_code == 200, changed.text
    await db_session.refresh(user)
    assert user.password_change_required is False
    assert user.temporary_password_issued_at is None

    monkeypatch.setattr(auth_endpoints.settings, "APP_ENV", "development")
    unauthenticated_client.cookies.clear()
    second_login = await unauthenticated_client.post(
        "/api/v1/auth/login",
        json={
            "email": user.email,
            "password": "SenhaDefinitivaForte#2026",
            "audience": "platform",
        },
    )
    assert second_login.status_code == 200, second_login.text
    assert second_login.json()["requiresMfa"] is True
    assert "accessToken" not in second_login.json()


@pytest.mark.asyncio
async def test_blackbox_login_completes_real_totp_in_production(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
):
    user = await _create_platform_user(db_session)
    secret = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
    monkeypatch.setattr(auth_endpoints.settings, "APP_ENV", "production")
    monkeypatch.setattr(auth_endpoints.settings, "KLM_RUNTIME_MODE", "production")
    monkeypatch.setattr(
        auth_endpoints.settings,
        "INTEGRATION_SECRET_ENCRYPTION_KEY",
        "blackbox-totp-test-key-that-is-dedicated-and-strong-2026",
    )
    db_session.add(
        MfaCredential(
            user_id=user.id,
            is_enabled=True,
            enabled_at=datetime.now(UTC),
            recovery_generation=1,
            totp_secret_encrypted=IntegrationSecretCipher().encrypt(secret),
        )
    )
    await db_session.commit()

    login = await unauthenticated_client.post(
        "/api/v1/auth/login",
        json={
            "email": user.email,
            "password": "senha-blackbox",
            "audience": "platform",
        },
    )
    assert login.status_code == 200, login.text
    challenge = login.json()
    assert challenge["requiresMfa"] is True
    assert challenge["simulatedCode"] is None
    code = mfa_service._totp_code(secret, mfa_service._totp_counter(datetime.now(UTC)))

    verified = await unauthenticated_client.post(
        f"/api/v1/auth/mfa/login/{challenge['challengeId']}/verify",
        json={"code": code},
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["session"]["surface"] == "platform"
    assert auth_endpoints.settings.AUTH_PLATFORM_REFRESH_COOKIE_NAME in verified.cookies


@pytest.mark.asyncio
async def test_refresh_cookie_cannot_cross_authentication_surfaces(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
):
    user = await _create_platform_user(db_session)
    monkeypatch.setattr(auth_endpoints.settings, "AUTH_REFRESH_COOKIE_SECURE", False)
    login = await unauthenticated_client.post(
        "/api/v1/auth/login",
        json={
            "email": user.email,
            "password": "senha-blackbox",
            "audience": "platform",
        },
    )
    assert login.status_code == 200, login.text

    rejected = await unauthenticated_client.post(
        "/api/v1/auth/refresh",
        headers={"X-Auth-Audience": "workspace"},
    )

    assert rejected.status_code == 401
    assert rejected.json()["detail"]["code"] == "AUTH-001"


@pytest.mark.asyncio
async def test_login_audience_rejects_platform_identity_on_workspace_surface(
    unauthenticated_client,
    db_session: AsyncSession,
):
    user = await _create_platform_user(db_session)

    response = await unauthenticated_client.post(
        "/api/v1/auth/login",
        json={
            "email": user.email,
            "password": "senha-blackbox",
            "audience": "workspace",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == {
        "code": "AUTH-003",
        "message": "Email ou senha incorretos.",
    }


@pytest.mark.asyncio
async def test_login_marks_refresh_cookie_secure_in_production(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
):
    user, _workspace, _membership = await _create_authenticated_user(db_session)
    monkeypatch.setattr(auth_endpoints.settings, "APP_ENV", "production")
    monkeypatch.setattr(auth_endpoints.settings, "AUTH_REFRESH_COOKIE_SECURE", False)

    response = await unauthenticated_client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "senha-segura"},
    )

    assert response.status_code == 200
    assert "Secure" in _refresh_cookie_header(response)


@pytest.mark.asyncio
async def test_refresh_token_returns_new_session_tokens(unauthenticated_client, db_session: AsyncSession, monkeypatch):
    user, workspace, membership = await _create_authenticated_user(db_session)

    async def not_revoked(_: str | None) -> bool:
        return False

    monkeypatch.setattr(auth_deps, "is_token_revoked", not_revoked)

    refresh_token = create_refresh_token(
        subject=str(user.id),
        workspace_id=workspace.id,
        role=membership.role.value,
        membership_id=membership.id,
        is_internal_staff=user.is_internal_staff,
    )
    original_epoch = parse_session_token_id(verify_token(refresh_token)["jti"])[1]

    response = await unauthenticated_client.post(
        "/api/v1/auth/refresh",
        headers={"Authorization": f"Bearer {refresh_token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["accessToken"]
    assert payload["refreshToken"]
    assert payload["session"]["activeWorkspace"]["id"] == str(workspace.id)
    assert payload["user"]["id"] == str(user.id)
    assert auth_endpoints.settings.AUTH_REFRESH_COOKIE_NAME in response.cookies
    assert parse_session_token_id(verify_token(payload["accessToken"])["jti"])[1] == original_epoch
    assert parse_session_token_id(verify_token(payload["refreshToken"])["jti"])[1] == original_epoch


@pytest.mark.asyncio
async def test_refresh_jti_consumption_is_atomic(monkeypatch):
    monkeypatch.setattr(token_store.settings, "KLM_RUNTIME_MODE", "local_simulation")
    token_id = str(uuid4())
    expires_at = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())

    results = await asyncio.gather(
        *(token_store.consume_token(token_id, expires_at) for _ in range(8))
    )

    assert results.count(True) == 1
    assert results.count(False) == 7
    assert await token_store.is_token_revoked(token_id) is True


@pytest.mark.asyncio
async def test_user_session_revocation_uses_redis_and_preserves_new_rotation(monkeypatch):
    class FakeRedis:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        async def get(self, key: str):
            return self.values.get(key)

        async def set(self, key: str, value: str, *, ex: int, nx: bool = False):
            if nx and key in self.values:
                return None
            self.values[key] = str(value)
            return True

        async def aclose(self) -> None:
            return None

    fake_redis = FakeRedis()

    async def fake_from_url(*_args, **_kwargs):
        return fake_redis

    monkeypatch.setattr(token_store.settings, "KLM_RUNTIME_MODE", "production")
    monkeypatch.setattr(token_store.aioredis, "from_url", fake_from_url)
    user_id = uuid4()
    access_token = create_access_token(subject=str(user_id))
    refresh_token = create_refresh_token(subject=str(user_id))
    access_payload = verify_token(access_token)
    refresh_payload = verify_token(refresh_token)

    await token_store.revoke_user_tokens(str(user_id))

    assert await token_store.is_token_revoked(access_payload["jti"]) is True
    assert await token_store.is_token_revoked(refresh_payload["jti"]) is True

    new_refresh = create_refresh_token(subject=str(user_id))
    new_payload = verify_token(new_refresh)
    assert await token_store.is_token_revoked(new_payload["jti"]) is False
    assert await token_store.consume_token(new_payload["jti"], new_payload["exp"]) is True
    assert await token_store.consume_token(new_payload["jti"], new_payload["exp"]) is False


@pytest.mark.asyncio
async def test_legacy_identity_jwt_without_auth_epoch_is_rejected():
    user_id = uuid4()
    legacy_jti = str(uuid4())
    issued_at = datetime.now(UTC) - timedelta(minutes=1)
    expires_at = issued_at + timedelta(minutes=30)
    legacy_token = jwt.encode(
        {
            "sub": str(user_id),
            "jti": legacy_jti,
            "iat": issued_at,
            "exp": expires_at,
            "token_type": "refresh",
        },
        auth_endpoints.settings.SECRET_KEY,
        algorithm="HS256",
    )

    assert verify_token(legacy_token) is None


@pytest.mark.asyncio
async def test_refresh_rotation_revokes_presented_jti_and_rejects_replay(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
):
    user, workspace, membership = await _create_authenticated_user(db_session)

    async def not_revoked(_: str | None) -> bool:
        return False

    monkeypatch.setattr(auth_deps, "is_token_revoked", not_revoked)
    monkeypatch.setattr(token_store.settings, "KLM_RUNTIME_MODE", "local_simulation")
    original_refresh = create_refresh_token(
        subject=str(user.id),
        workspace_id=workspace.id,
        role=membership.role.value,
        membership_id=membership.id,
        is_internal_staff=user.is_internal_staff,
    )
    original_payload = verify_token(original_refresh)

    first_response = await unauthenticated_client.post(
        "/api/v1/auth/refresh",
        headers={"Authorization": f"Bearer {original_refresh}"},
    )

    assert first_response.status_code == 200
    assert original_payload is not None
    assert await token_store.is_token_revoked(original_payload["jti"]) is True

    unauthenticated_client.cookies.clear()
    replay_response = await unauthenticated_client.post(
        "/api/v1/auth/refresh",
        headers={"Authorization": f"Bearer {original_refresh}"},
    )

    assert replay_response.status_code == 401
    assert replay_response.json()["detail"]["code"] == "AUTH-009"


@pytest.mark.asyncio
async def test_refresh_keeps_original_epoch_when_reset_checks_race(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
):
    user, workspace, membership = await _create_authenticated_user(db_session)
    monkeypatch.setattr(token_store.settings, "KLM_RUNTIME_MODE", "local_simulation")
    original_refresh = create_refresh_token(
        subject=str(user.id),
        workspace_id=workspace.id,
        role=membership.role.value,
        membership_id=membership.id,
        is_internal_staff=user.is_internal_staff,
    )
    original_payload = verify_token(original_refresh)
    original_epoch = parse_session_token_id(original_payload["jti"])[1]
    await token_store.revoke_user_tokens(str(user.id))

    async def stale_not_revoked(_token_id: str | None) -> bool:
        return False

    async def stale_consume(_token_id: str | None, _expires_at: int | None) -> bool:
        return True

    monkeypatch.setattr(auth_deps, "is_token_revoked", stale_not_revoked)
    monkeypatch.setattr(auth_endpoints, "is_user_session_revoked", stale_not_revoked)
    monkeypatch.setattr(auth_endpoints, "consume_token", stale_consume)

    response = await unauthenticated_client.post(
        "/api/v1/auth/refresh",
        headers={"Authorization": f"Bearer {original_refresh}"},
    )

    assert response.status_code == 200
    access_payload = verify_token(response.json()["accessToken"])
    refresh_payload = verify_token(response.json()["refreshToken"])
    assert parse_session_token_id(access_payload["jti"])[1] == original_epoch
    assert parse_session_token_id(refresh_payload["jti"])[1] == original_epoch
    assert await token_store.is_token_revoked(access_payload["jti"]) is True
    assert await token_store.is_token_revoked(refresh_payload["jti"]) is True


@pytest.mark.asyncio
async def test_inactive_user_cannot_refresh(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
):
    user, workspace, membership = await _create_authenticated_user(db_session)
    refresh_token = create_refresh_token(
        subject=str(user.id),
        workspace_id=workspace.id,
        role=membership.role.value,
        membership_id=membership.id,
        is_internal_staff=user.is_internal_staff,
    )
    user.is_active = False
    await db_session.commit()

    async def not_revoked(_: str | None) -> bool:
        return False

    monkeypatch.setattr(auth_deps, "is_token_revoked", not_revoked)
    monkeypatch.setattr(token_store.settings, "KLM_RUNTIME_MODE", "local_simulation")
    for _ in range(2):
        response = await unauthenticated_client.post(
            "/api/v1/auth/refresh",
            headers={"Authorization": f"Bearer {refresh_token}"},
        )
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "AUTH-009"


@pytest.mark.asyncio
async def test_unverified_human_cannot_refresh(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
):
    user, workspace, membership = await _create_authenticated_user(db_session)
    refresh_token = create_refresh_token(
        subject=str(user.id),
        workspace_id=workspace.id,
        role=membership.role.value,
        membership_id=membership.id,
        auth_epoch=user.auth_epoch,
    )
    user.email_verified_at = None
    await db_session.commit()

    async def not_revoked(_: str | None) -> bool:
        return False

    monkeypatch.setattr(auth_deps, "is_token_revoked", not_revoked)
    response = await unauthenticated_client.post(
        "/api/v1/auth/refresh",
        headers={"Authorization": f"Bearer {refresh_token}"},
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTH-009"


@pytest.mark.asyncio
async def test_suspended_membership_cannot_refresh_or_fall_back(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
):
    user, workspace, membership = await _create_authenticated_user(db_session)
    fallback_workspace = Workspace(
        name="Workspace Alternativo",
        slug=f"workspace-alternativo-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        settings={},
    )
    db_session.add(fallback_workspace)
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=fallback_workspace.id,
            user_id=user.id,
            role=WorkspaceRole.operator,
            status=MembershipStatus.active,
            is_default=False,
        )
    )
    await db_session.flush()
    refresh_token = create_refresh_token(
        subject=str(user.id),
        workspace_id=workspace.id,
        role=membership.role.value,
        membership_id=membership.id,
        is_internal_staff=user.is_internal_staff,
    )
    refresh_payload = verify_token(refresh_token)
    membership.status = MembershipStatus.suspended
    await db_session.commit()

    async def not_revoked(_: str | None) -> bool:
        return False

    monkeypatch.setattr(auth_deps, "is_token_revoked", not_revoked)
    monkeypatch.setattr(token_store.settings, "KLM_RUNTIME_MODE", "local_simulation")
    response = await unauthenticated_client.post(
        "/api/v1/auth/refresh",
        headers={"Authorization": f"Bearer {refresh_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "AUTH-010"
    assert refresh_payload is not None
    assert await token_store.is_token_revoked(refresh_payload["jti"]) is True


@pytest.mark.asyncio
async def test_refresh_uses_http_only_cookie_and_hides_rotated_token(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
):
    user, workspace, _membership = await _create_authenticated_user(db_session)

    async def not_revoked(_: Optional[str]) -> bool:
        return False

    monkeypatch.setattr(auth_deps, "is_token_revoked", not_revoked)
    monkeypatch.setattr(auth_endpoints.settings, "APP_ENV", "development")
    monkeypatch.setattr(auth_endpoints.settings, "AUTH_REFRESH_COOKIE_SECURE", False)
    login_response = await unauthenticated_client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "senha-segura"},
    )
    original_refresh = login_response.cookies.get(
        auth_endpoints.settings.AUTH_REFRESH_COOKIE_NAME
    )

    response = await unauthenticated_client.post("/api/v1/auth/refresh")

    assert response.status_code == 200
    payload = response.json()
    assert payload["accessToken"]
    assert payload["refreshToken"] is None
    assert payload["session"]["activeWorkspace"]["id"] == str(workspace.id)
    rotated_refresh = response.cookies.get(auth_endpoints.settings.AUTH_REFRESH_COOKIE_NAME)
    assert rotated_refresh != original_refresh


@pytest.mark.asyncio
async def test_cookie_refresh_preserves_selected_workspace(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
):
    user, _default_workspace, _membership = await _create_authenticated_user(db_session)
    selected_workspace = Workspace(
        name="Workspace Selecionado",
        slug=f"workspace-selecionado-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        settings={},
    )
    db_session.add(selected_workspace)
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=selected_workspace.id,
            user_id=user.id,
            role=WorkspaceRole.operator,
            status=MembershipStatus.active,
            is_default=False,
        )
    )
    await db_session.commit()

    async def not_revoked(_: Optional[str]) -> bool:
        return False

    monkeypatch.setattr(auth_deps, "is_token_revoked", not_revoked)
    monkeypatch.setattr(auth_endpoints.settings, "APP_ENV", "development")
    monkeypatch.setattr(auth_endpoints.settings, "AUTH_REFRESH_COOKIE_SECURE", False)
    login_response = await unauthenticated_client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "senha-segura"},
    )
    access_token = login_response.json()["accessToken"]
    login_refresh = login_response.cookies.get(auth_endpoints.settings.AUTH_REFRESH_COOKIE_NAME)
    session_epoch = parse_session_token_id(verify_token(access_token)["jti"])[1]
    assert parse_session_token_id(verify_token(login_refresh)["jti"])[1] == session_epoch
    select_response = await unauthenticated_client.post(
        "/api/v1/auth/select-workspace",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"workspaceId": str(selected_workspace.id)},
    )

    assert select_response.status_code == 200
    assert select_response.json()["refreshToken"] is None
    selected_access = select_response.json()["accessToken"]
    selected_refresh = select_response.cookies.get(auth_endpoints.settings.AUTH_REFRESH_COOKIE_NAME)
    assert parse_session_token_id(verify_token(selected_access)["jti"])[1] == session_epoch
    assert parse_session_token_id(verify_token(selected_refresh)["jti"])[1] == session_epoch
    refresh_response = await unauthenticated_client.post("/api/v1/auth/refresh")
    assert refresh_response.status_code == 200
    assert refresh_response.json()["session"]["activeWorkspace"]["id"] == str(
        selected_workspace.id
    )
    refreshed_access = refresh_response.json()["accessToken"]
    refreshed_token = refresh_response.cookies.get(auth_endpoints.settings.AUTH_REFRESH_COOKIE_NAME)
    assert parse_session_token_id(verify_token(refreshed_access)["jti"])[1] == session_epoch
    assert parse_session_token_id(verify_token(refreshed_token)["jti"])[1] == session_epoch


@pytest.mark.asyncio
async def test_select_workspace_keeps_revoked_epoch_when_reset_check_races(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
):
    user, _workspace, _membership = await _create_authenticated_user(db_session)
    selected_workspace = Workspace(
        name="Workspace Concorrente",
        slug=f"workspace-concorrente-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        settings={},
    )
    db_session.add(selected_workspace)
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=selected_workspace.id,
            user_id=user.id,
            role=WorkspaceRole.operator,
            status=MembershipStatus.active,
            is_default=False,
        )
    )
    await db_session.commit()
    login_response = await unauthenticated_client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "senha-segura"},
    )
    original_access = login_response.json()["accessToken"]
    original_epoch = parse_session_token_id(verify_token(original_access)["jti"])[1]
    await token_store.revoke_user_tokens(str(user.id))

    async def stale_not_revoked(_token_id: str | None) -> bool:
        return False

    monkeypatch.setattr(auth_deps, "is_token_revoked", stale_not_revoked)
    response = await unauthenticated_client.post(
        "/api/v1/auth/select-workspace",
        headers={"Authorization": f"Bearer {original_access}"},
        json={"workspaceId": str(selected_workspace.id)},
    )

    assert response.status_code == 200
    selected_access = verify_token(response.json()["accessToken"])
    selected_refresh = verify_token(
        response.cookies.get(auth_endpoints.settings.AUTH_REFRESH_COOKIE_NAME)
    )
    assert parse_session_token_id(selected_access["jti"])[1] == original_epoch
    assert parse_session_token_id(selected_refresh["jti"])[1] == original_epoch
    assert await token_store.is_token_revoked(selected_access["jti"]) is True
    assert await token_store.is_token_revoked(selected_refresh["jti"]) is True


@pytest.mark.asyncio
async def test_logout_revokes_current_access_token(unauthenticated_client, db_session: AsyncSession, monkeypatch):
    user, workspace, membership = await _create_authenticated_user(db_session)
    revoked_tokens: set[str] = set()

    async def fake_is_revoked(token_id: Optional[str]) -> bool:
        return bool(token_id and token_id in revoked_tokens)

    async def fake_revoke(token_id: str, _expires_at: Optional[int]) -> None:
        revoked_tokens.add(token_id)

    monkeypatch.setattr(auth_deps, "is_token_revoked", fake_is_revoked)
    monkeypatch.setattr(auth_endpoints, "revoke_token", fake_revoke)
    disconnect = AsyncMock()
    monkeypatch.setattr(auth_endpoints.ws_manager, "disconnect_user_sessions", disconnect)

    access_token = create_access_token(
        subject=str(user.id),
        workspace_id=workspace.id,
        role=membership.role.value,
        membership_id=membership.id,
        is_internal_staff=user.is_internal_staff,
    )
    token_payload = verify_token(access_token)
    assert token_payload is not None

    logout_response = await unauthenticated_client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert logout_response.status_code == 204
    assert token_payload["jti"] in revoked_tokens
    disconnect.assert_awaited_once_with(user.id, code=4401)
    clear_cookie = _refresh_cookie_header(logout_response)
    assert "Max-Age=0" in clear_cookie
    assert "HttpOnly" in clear_cookie

    me_response = await unauthenticated_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me_response.status_code == 401
    assert me_response.json()["detail"]["code"] == "AUTH-009"


@pytest.mark.asyncio
async def test_logout_without_access_token_revokes_and_clears_refresh_cookie(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
):
    user, _workspace, _membership = await _create_authenticated_user(db_session)
    revoked_tokens: set[str] = set()

    async def fake_revoke(token_id: str, _expires_at: Optional[int]) -> None:
        revoked_tokens.add(token_id)

    monkeypatch.setattr(auth_endpoints, "revoke_token", fake_revoke)
    disconnect = AsyncMock()
    monkeypatch.setattr(auth_endpoints.ws_manager, "disconnect_session", disconnect)
    monkeypatch.setattr(auth_endpoints.settings, "APP_ENV", "development")
    monkeypatch.setattr(auth_endpoints.settings, "AUTH_REFRESH_COOKIE_SECURE", False)
    login_response = await unauthenticated_client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "senha-segura"},
    )
    refresh_token = login_response.cookies.get(
        auth_endpoints.settings.AUTH_REFRESH_COOKIE_NAME
    )
    refresh_payload = verify_token(refresh_token)

    logout_response = await unauthenticated_client.post("/api/v1/auth/logout")

    assert logout_response.status_code == 204
    assert refresh_payload["jti"] in revoked_tokens
    disconnect.assert_awaited_once_with(UUID(refresh_payload["session_id"]), code=4401)
    assert "Max-Age=0" in _refresh_cookie_header(logout_response)


@pytest.mark.asyncio
async def test_reset_password_with_valid_token_changes_password(
    unauthenticated_client,
    db_session: AsyncSession,
):
    user, _workspace, _membership = await _create_authenticated_user(db_session)
    raw_token = f"reset-{uuid4().hex}"
    reset_token = PasswordResetToken(
        user_id=user.id,
        token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(reset_token)
    await db_session.commit()

    response = await unauthenticated_client.post(
        "/api/v1/auth/reset-password",
        json={"token": raw_token, "new_password": "nova-senha-segura"},
    )

    assert response.status_code == 200
    await db_session.refresh(user)
    await db_session.refresh(reset_token)
    assert verify_password("nova-senha-segura", user.hashed_password) is True
    assert verify_password("senha-segura", user.hashed_password) is False
    assert reset_token.used_at is not None
    user_email = user.email

    replay_response = await unauthenticated_client.post(
        "/api/v1/auth/reset-password",
        json={"token": raw_token, "new_password": "outra-senha-segura"},
    )
    assert replay_response.status_code == 400
    assert replay_response.json()["detail"]["code"] == "AUTH-VERIFICATION-INVALID"

    old_password_login = await unauthenticated_client.post(
        "/api/v1/auth/login",
        json={"email": user_email, "password": "senha-segura"},
    )
    new_password_login = await unauthenticated_client.post(
        "/api/v1/auth/login",
        json={"email": user_email, "password": "nova-senha-segura"},
    )
    assert old_password_login.status_code == 401
    assert new_password_login.status_code == 200


@pytest.mark.asyncio
async def test_reset_password_revokes_existing_access_and_refresh_sessions(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
):
    user, _workspace, _membership = await _create_authenticated_user(db_session)
    monkeypatch.setattr(token_store.settings, "KLM_RUNTIME_MODE", "local_simulation")
    disconnect_user_sessions = AsyncMock()
    monkeypatch.setattr(
        auth_endpoints.ws_manager,
        "disconnect_user_sessions",
        disconnect_user_sessions,
    )
    login_response = await unauthenticated_client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "senha-segura"},
    )
    assert login_response.status_code == 200
    old_access = login_response.json()["accessToken"]
    old_refresh = login_response.cookies.get(auth_endpoints.settings.AUTH_REFRESH_COOKIE_NAME)
    raw_token = f"reset-session-{uuid4().hex}"
    db_session.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    await db_session.commit()

    reset_response = await unauthenticated_client.post(
        "/api/v1/auth/reset-password",
        json={"token": raw_token, "new_password": "senha-nova-segura"},
    )

    assert reset_response.status_code == 200
    assert "Max-Age=0" in _refresh_cookie_header(reset_response)
    disconnect_user_sessions.assert_awaited_once_with(user.id, code=4401)
    old_access_response = await unauthenticated_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {old_access}"},
    )
    unauthenticated_client.cookies.clear()
    old_refresh_response = await unauthenticated_client.post(
        "/api/v1/auth/refresh",
        headers={"Authorization": f"Bearer {old_refresh}"},
    )
    assert old_access_response.status_code == 401
    assert old_access_response.json()["detail"]["code"] == "AUTH-009"
    assert old_refresh_response.status_code == 401
    assert old_refresh_response.json()["detail"]["code"] == "AUTH-009"

    new_login = await unauthenticated_client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "senha-nova-segura"},
    )
    assert new_login.status_code == 200
    new_access = new_login.json()["accessToken"]
    new_refresh = new_login.cookies.get(auth_endpoints.settings.AUTH_REFRESH_COOKIE_NAME)
    new_access_response = await unauthenticated_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {new_access}"},
    )
    rotated_response = await unauthenticated_client.post("/api/v1/auth/refresh")
    assert new_access_response.status_code == 200
    assert rotated_response.status_code == 200

    unauthenticated_client.cookies.clear()
    replay_response = await unauthenticated_client.post(
        "/api/v1/auth/refresh",
        headers={"Authorization": f"Bearer {new_refresh}"},
    )
    assert replay_response.status_code == 401
    assert replay_response.json()["detail"]["code"] == "AUTH-009"


@pytest.mark.asyncio
async def test_password_change_epoch_is_authoritative_when_fanout_needs_retry(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
):
    user, _workspace, _membership = await _create_authenticated_user(db_session)
    login = await unauthenticated_client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "senha-segura"},
    )
    assert login.status_code == 200
    old_access = login.json()["accessToken"]
    revoke_cache = AsyncMock()
    failed_disconnect = AsyncMock(side_effect=RuntimeError("fanout unavailable"))
    monkeypatch.setattr(auth_revocation_service, "revoke_user_tokens", revoke_cache)
    monkeypatch.setattr(
        auth_revocation_service.ws_manager,
        "disconnect_user_sessions",
        failed_disconnect,
    )

    changed = await unauthenticated_client.post(
        "/api/v1/auth/password",
        json={
            "currentPassword": "senha-segura",
            "newPassword": "SenhaNovaComEpoch#2026",
        },
        headers={"Authorization": f"Bearer {old_access}"},
    )

    assert changed.status_code == 200, changed.text
    await db_session.refresh(user)
    assert user.auth_epoch == 1
    intent = await db_session.scalar(
        select(AuthRevocationIntent).where(AuthRevocationIntent.user_id == user.id)
    )
    assert intent is not None
    assert intent.status == AuthRevocationIntentStatus.pending
    assert intent.attempt_count == 1
    assert intent.last_error_type == "RuntimeError"
    intent_id = intent.id
    old_access_response = await unauthenticated_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {old_access}"},
    )
    assert old_access_response.status_code == 401
    assert old_access_response.json()["detail"]["code"] == "AUTH-009"

    successful_disconnect = AsyncMock()
    monkeypatch.setattr(
        auth_revocation_service.ws_manager,
        "disconnect_user_sessions",
        successful_disconnect,
    )
    assert await AuthRevocationService(db_session).fanout_after_commit(intent_id) is True
    await db_session.refresh(intent)
    assert intent.status == AuthRevocationIntentStatus.completed
    assert intent.attempt_count == 2


@pytest.mark.asyncio
async def test_authenticated_session_issues_workspace_bound_websocket_ticket(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
):
    user, workspace, membership = await _create_authenticated_user(db_session)

    login_response = await unauthenticated_client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "senha-segura"},
    )
    assert login_response.status_code == 200
    access_token = login_response.json()["accessToken"]
    access_payload = verify_token(access_token)
    response = await unauthenticated_client.post(
        "/api/v1/auth/ws-ticket",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 200
    payload = response.json()
    ticket_payload = verify_token(payload["ticket"])
    assert payload["expiresIn"] == 60
    assert ticket_payload["token_type"] == "websocket"
    assert ticket_payload["workspace_id"] == str(workspace.id)
    assert ticket_payload["membership_id"] == str(membership.id)
    assert parse_session_token_id(ticket_payload["jti"])[1] == parse_session_token_id(
        access_payload["jti"]
    )[1]


@pytest.mark.asyncio
async def test_public_register_stays_pending_until_verification_then_initializes_session(
    unauthenticated_client,
    local_outbox_headers,
):
    email = f"owner-{uuid4().hex[:8]}@example.com"
    response = await unauthenticated_client.post(
        "/api/v1/auth/register",
        json={
            "name": "Owner Novo",
            "email": email,
            "workspaceName": "Workspace Novo",
            "password": "Senha@12345",
            "plan": "basic",
        },
        headers={"Idempotency-Key": f"register-{uuid4().hex}"},
    )

    assert response.status_code == 202
    pending = response.json()
    assert pending["status"] == "accepted"
    assert "accessToken" not in pending
    assert auth_endpoints.settings.AUTH_REFRESH_COOKIE_NAME not in response.cookies

    delivery = await unauthenticated_client.get(
        f"/api/v1/auth/local-outbox/{pending['deliveryId']}",
        headers=local_outbox_headers,
    )
    assert delivery.status_code == 200, delivery.text
    token = parse_qs(urlsplit(delivery.json()["link"]).query)["token"][0]
    verified = await unauthenticated_client.post(
        "/api/v1/auth/register/verify",
        json={"token": token},
    )
    assert verified.status_code == 200, verified.text
    payload = verified.json()
    assert payload["verificationStatus"] == "verified"
    assert payload["refreshToken"] is None
    assert auth_endpoints.settings.AUTH_REFRESH_COOKIE_NAME in verified.cookies
    assert payload["session"]["activeWorkspace"]["onboardingCompleted"] is False
    assert payload["session"]["activeWorkspace"]["onboardingProgress"] == 0
    assert payload["session"]["activeWorkspace"]["nextOnboardingStep"] == "welcome"

    replay = await unauthenticated_client.post(
        "/api/v1/auth/register/verify",
        json={"token": token},
    )
    assert replay.status_code == 400
    assert "accessToken" not in replay.json()


@pytest.mark.asyncio
async def test_local_reset_rotates_to_one_active_secret_and_accepts_delivery_code(
    unauthenticated_client,
    db_session: AsyncSession,
    local_outbox_headers,
):
    user, _workspace, _membership = await _create_authenticated_user(db_session)

    first = await unauthenticated_client.post(
        "/api/v1/auth/forgot-password",
        json={"email": user.email},
    )
    second = await unauthenticated_client.post(
        "/api/v1/auth/forgot-password",
        json={"email": user.email},
    )
    assert first.status_code == second.status_code == 202
    active_count = await db_session.scalar(
        select(func.count(PasswordResetToken.id)).where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.revoked_at.is_(None),
        )
    )
    assert active_count == 1

    first_delivery = await unauthenticated_client.get(
        f"/api/v1/auth/local-outbox/requests/{first.json()['requestId']}",
        headers=local_outbox_headers,
    )
    stale = await unauthenticated_client.post(
        "/api/v1/auth/reset-password",
        json={
            "deliveryId": first_delivery.json()["id"],
            "code": first_delivery.json()["code"],
            "newPassword": "SenhaRotacionada#2026",
        },
    )
    assert stale.status_code == 400
    assert stale.json()["detail"]["code"] == "AUTH-VERIFICATION-INVALID"

    second_delivery = await unauthenticated_client.get(
        f"/api/v1/auth/local-outbox/requests/{second.json()['requestId']}",
        headers=local_outbox_headers,
    )
    reset = await unauthenticated_client.post(
        "/api/v1/auth/reset-password",
        json={
            "deliveryId": second_delivery.json()["id"],
            "code": second_delivery.json()["code"],
            "newPassword": "SenhaRotacionada#2026",
        },
    )
    assert reset.status_code == 200, reset.text
    await db_session.refresh(user)
    assert verify_password("SenhaRotacionada#2026", user.hashed_password)


@pytest.mark.asyncio
async def test_persistent_device_sessions_list_revoke_others_and_survive_new_db_session(
    unauthenticated_client,
    db_session: AsyncSession,
):
    user, _workspace, _membership = await _create_authenticated_user(db_session)
    first = await unauthenticated_client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "senha-segura"},
        headers={"User-Agent": "Mozilla/5.0 (Macintosh) Chrome/126.0"},
    )
    first_access = first.json()["accessToken"]
    first_refresh = first.cookies.get(auth_endpoints.settings.AUTH_REFRESH_COOKIE_NAME)
    second = await unauthenticated_client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "senha-segura"},
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Firefox/128.0"},
    )
    second_access = second.json()["accessToken"]

    listed = await unauthenticated_client.get(
        "/api/v1/auth/sessions",
        headers={"Authorization": f"Bearer {second_access}"},
    )
    assert listed.status_code == 200, listed.text
    assert len(listed.json()) == 2
    assert sum(item["current"] for item in listed.json()) == 1
    rows = list((await db_session.scalars(select(UserSession))).all())
    assert len(rows) == 2
    raw_refresh_jti = verify_token(first_refresh)["jti"]
    assert all(row.refresh_jti_digest != raw_refresh_jti for row in rows)
    assert all(len(row.refresh_jti_digest) == 64 for row in rows)
    user_id = user.id

    revoked = await unauthenticated_client.delete(
        "/api/v1/auth/sessions/others",
        headers={"Authorization": f"Bearer {second_access}"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["revokedCount"] == 1
    rejected_first = await unauthenticated_client.get(
        "/api/v1/auth/sessions",
        headers={"Authorization": f"Bearer {first_access}"},
    )
    assert rejected_first.status_code == 401
    assert rejected_first.json()["detail"]["code"] == "AUTH-009"

    logout = await unauthenticated_client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {second_access}"},
    )
    assert logout.status_code == 204
    session_factory = async_sessionmaker(
        db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as fresh_session:
        persisted = list(
            (
                await fresh_session.scalars(
                    select(UserSession).where(UserSession.user_id == user_id)
                )
            ).all()
        )
    assert len(persisted) == 2
    assert all(item.revoked_at is not None for item in persisted)
