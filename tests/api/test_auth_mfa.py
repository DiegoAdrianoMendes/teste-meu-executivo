import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_token
from app.models.auth_security import (
    AdministrativeSession,
    AuthSecurityEvent,
    MfaRecoveryCode,
)
from app.models.password_reset import PasswordResetToken
from app.models.user import User
from app.models.workspace_membership import WorkspaceMembership, WorkspaceRole
from app.services import mfa_service


@pytest.mark.asyncio
async def test_mfa_login_recovery_and_administrative_session_lifecycle(
    client,
    db_session: AsyncSession,
    auth_context,
) -> None:
    auth_context.token_id = "test-access-token-family"
    user = await db_session.get(User, auth_context.user_id)
    user.hashed_password = hash_password("senha-segura")
    await db_session.commit()

    enrollment = await client.post("/api/v1/auth/mfa/enroll")
    assert enrollment.status_code == 200
    enrollment_payload = enrollment.json()
    assert enrollment_payload["purpose"] == "enrollment"
    assert enrollment_payload["simulatedCode"]

    enabled = await client.post(
        f"/api/v1/auth/mfa/enroll/{enrollment_payload['challengeId']}/verify",
        json={"code": enrollment_payload["simulatedCode"]},
    )
    assert enabled.status_code == 200
    recovery_codes = enabled.json()["recoveryCodes"]
    assert len(recovery_codes) == 8
    stored_codes = list((await db_session.scalars(select(MfaRecoveryCode))).all())
    assert len(stored_codes) == 8
    assert all(item.code_digest not in recovery_codes for item in stored_codes)

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "senha-segura"},
    )
    assert login.status_code == 200
    login_challenge = login.json()
    assert login_challenge["requiresMfa"] is True
    assert "accessToken" not in login_challenge

    completed_login = await client.post(
        f"/api/v1/auth/mfa/login/{login_challenge['challengeId']}/verify",
        json={"code": login_challenge["simulatedCode"]},
    )
    assert completed_login.status_code == 200
    assert verify_token(completed_login.json()["accessToken"])["token_type"] == "access"

    recovery_login = await client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "senha-segura"},
    )
    recovered = await client.post(
        f"/api/v1/auth/mfa/login/{recovery_login.json()['challengeId']}/verify",
        json={"recoveryCode": recovery_codes[0]},
    )
    assert recovered.status_code == 200

    replay_login = await client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "senha-segura"},
    )
    replay = await client.post(
        f"/api/v1/auth/mfa/login/{replay_login.json()['challengeId']}/verify",
        json={"recoveryCode": recovery_codes[0]},
    )
    assert replay.status_code == 401
    assert replay.json()["detail"]["code"] == "AUTH-016"

    admin_challenge = await client.post("/api/v1/auth/admin-sessions/challenge")
    assert admin_challenge.status_code == 200
    admin_payload = admin_challenge.json()
    issued = await client.post(
        f"/api/v1/auth/admin-sessions/challenge/{admin_payload['challengeId']}/verify",
        json={"code": admin_payload["simulatedCode"]},
    )
    assert issued.status_code == 200
    issued_payload = issued.json()
    admin_token = issued_payload["administrativeSessionToken"]
    admin_token_payload = verify_token(admin_token)
    assert admin_token_payload["aud"].endswith(":admin_session")

    current = await client.get(
        "/api/v1/auth/admin-sessions/current",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert current.status_code == 200
    assert current.json()["active"] is True

    session_id = issued_payload["session"]["id"]
    revoked = await client.delete(f"/api/v1/auth/admin-sessions/{session_id}")
    assert revoked.status_code == 204
    rejected = await client.get(
        "/api/v1/auth/admin-sessions/current",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert rejected.status_code == 401

    audit_events = await client.get("/api/v1/auth/security-events")
    assert audit_events.status_code == 200
    assert any(
        event["eventType"] == "administrative_session_revoked"
        for event in audit_events.json()
    )


@pytest.mark.asyncio
async def test_password_reset_revokes_administrative_session_with_audit(
    client,
    db_session: AsyncSession,
    auth_context,
) -> None:
    auth_context.token_id = "test-access-token-family"
    user = await db_session.get(User, auth_context.user_id)
    user.hashed_password = hash_password("senha-segura")
    await db_session.commit()
    enrollment = (await client.post("/api/v1/auth/mfa/enroll")).json()
    await client.post(
        f"/api/v1/auth/mfa/enroll/{enrollment['challengeId']}/verify",
        json={"code": enrollment["simulatedCode"]},
    )
    challenge = (await client.post("/api/v1/auth/admin-sessions/challenge")).json()
    issued = await client.post(
        f"/api/v1/auth/admin-sessions/challenge/{challenge['challengeId']}/verify",
        json={"code": challenge["simulatedCode"]},
    )
    admin_token = issued.json()["administrativeSessionToken"]
    session_id = issued.json()["session"]["id"]
    raw_reset_token = "reset-admin-session"
    db_session.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hashlib.sha256(raw_reset_token.encode()).hexdigest(),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    await db_session.commit()

    reset = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": raw_reset_token, "new_password": "senha-nova-segura"},
    )

    assert reset.status_code == 200
    rejected = await client.get(
        "/api/v1/auth/admin-sessions/current",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert rejected.status_code == 401
    session = await db_session.get(AdministrativeSession, session_id)
    assert session.revoked_at is not None
    assert session.revocation_reason == "password_reset"
    events = list(
        (
            await db_session.scalars(
                select(AuthSecurityEvent).where(
                    AuthSecurityEvent.subject_user_id == user.id,
                    AuthSecurityEvent.event_type
                    == "administrative_sessions_revoked",
                )
            )
        ).all()
    )
    assert any(event.details["reason"] == "password_reset" for event in events)


@pytest.mark.asyncio
async def test_active_non_admin_can_enroll_mfa_but_cannot_elevate(
    client,
    db_session: AsyncSession,
    auth_context,
) -> None:
    auth_context.token_id = "operator-access-token-family"
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    membership.role = WorkspaceRole.operator
    auth_context.role = WorkspaceRole.operator
    await db_session.commit()

    enrollment = await client.post("/api/v1/auth/mfa/enroll")
    assert enrollment.status_code == 200, enrollment.text
    enabled = await client.post(
        f"/api/v1/auth/mfa/enroll/{enrollment.json()['challengeId']}/verify",
        json={"code": enrollment.json()["simulatedCode"]},
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["enabled"] is True

    elevation = await client.post("/api/v1/auth/admin-sessions/challenge")
    assert elevation.status_code == 403
    assert elevation.json()["detail"]["code"] == "AUTH-012"


@pytest.mark.asyncio
async def test_production_totp_enrollment_and_login_do_not_expose_shared_secret(
    client,
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
) -> None:
    auth_context.token_id = "production-totp-access-family"
    user = await db_session.get(User, auth_context.user_id)
    user.hashed_password = hash_password("senha-totp-producao")
    await db_session.commit()
    monkeypatch.setattr(mfa_service.settings, "KLM_RUNTIME_MODE", "production")
    monkeypatch.setattr(
        mfa_service.settings,
        "INTEGRATION_SECRET_ENCRYPTION_KEY",
        "mfa-production-test-key-that-is-dedicated-and-strong-2026",
    )

    enrollment = await client.post("/api/v1/auth/mfa/enroll")
    assert enrollment.status_code == 200, enrollment.text
    payload = enrollment.json()
    assert payload["simulatedCode"] is None
    assert payload["provisioningUri"].startswith("otpauth://totp/")
    secret = payload["manualEntryKey"]
    code = mfa_service._totp_code(secret, mfa_service._totp_counter(datetime.now(UTC)))

    enabled = await client.post(
        f"/api/v1/auth/mfa/enroll/{payload['challengeId']}/verify",
        json={"code": code},
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["simulated"] is False

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "senha-totp-producao"},
    )
    assert login.status_code == 200, login.text
    challenge = login.json()
    assert challenge["requiresMfa"] is True
    assert challenge["simulatedCode"] is None
    assert "manualEntryKey" not in challenge or challenge["manualEntryKey"] is None

    login_code = mfa_service._totp_code(
        secret,
        mfa_service._totp_counter(datetime.now(UTC)),
    )
    completed = await client.post(
        f"/api/v1/auth/mfa/login/{challenge['challengeId']}/verify",
        json={"code": login_code},
    )
    assert completed.status_code == 200, completed.text
    assert verify_token(completed.json()["accessToken"])["token_type"] == "access"
