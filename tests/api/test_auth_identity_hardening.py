from dataclasses import replace
from datetime import UTC, datetime
from time import monotonic, sleep
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.auth_security import (
    AuthRateLimitEvent,
    AuthRateLimitScope,
)
from app.models.identity import IdentityOutboxMessage
from app.models.user import AgentType, User, UserRole
from app.services import auth_rate_limit_service, identity_lifecycle_service
from app.services.auth_rate_limit_service import (
    RATE_LIMIT_POLICIES,
    PersistentAuthRateLimiter,
    RateLimitPolicy,
    enforce_uniform_timing,
)


def _fast_policy(
    monkeypatch,
    scope: AuthRateLimitScope,
    *,
    identifier_limit: int,
    network_limit: int = 50,
) -> None:
    current = RATE_LIMIT_POLICIES[scope]
    monkeypatch.setitem(
        RATE_LIMIT_POLICIES,
        scope,
        replace(
            current,
            identifier_limit=identifier_limit,
            network_limit=network_limit,
            minimum_response_seconds=0,
        ),
    )


@pytest.mark.asyncio
async def test_login_rate_limit_persists_failed_requests(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    _fast_policy(monkeypatch, AuthRateLimitScope.login, identifier_limit=2)
    payload = {"email": "missing@example.com", "password": "senha-invalida"}

    responses = [
        await unauthenticated_client.post("/api/v1/auth/login", json=payload)
        for _ in range(3)
    ]

    assert [response.status_code for response in responses] == [401, 401, 429]
    assert responses[0].json() == responses[1].json()
    assert responses[2].json()["detail"]["code"] == "AUTH-014"
    event_count = await db_session.scalar(
        select(func.count(AuthRateLimitEvent.id)).where(
            AuthRateLimitEvent.scope == AuthRateLimitScope.login
        )
    )
    limited_count = await db_session.scalar(
        select(func.count(AuthRateLimitEvent.id)).where(
            AuthRateLimitEvent.scope == AuthRateLimitScope.login,
            AuthRateLimitEvent.was_limited.is_(True),
        )
    )
    assert event_count == 3
    assert limited_count == 1


@pytest.mark.asyncio
async def test_identity_limit_applies_across_networks(
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    _fast_policy(monkeypatch, AuthRateLimitScope.login, identifier_limit=1)
    limiter = PersistentAuthRateLimiter(db_session)

    first = await limiter.check(
        AuthRateLimitScope.login,
        identifier="target@example.com",
        network="192.0.2.10",
    )
    second = await limiter.check(
        AuthRateLimitScope.login,
        identifier="target@example.com",
        network="198.51.100.20",
    )

    assert first.allowed is True
    assert second.allowed is False


@pytest.mark.asyncio
async def test_login_hides_missing_and_inactive_accounts(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    _fast_policy(monkeypatch, AuthRateLimitScope.login, identifier_limit=10)
    inactive = User(
        name="Conta Inativa",
        email="inactive@example.com",
        hashed_password=hash_password("senha-correta"),
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="CI",
        color="#123456",
        is_active=False,
        email_verified_at=None,
    )
    unverified = User(
        name="Conta sem verificação",
        email="unverified@example.com",
        hashed_password=hash_password("senha-correta"),
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="SV",
        color="#123456",
        is_active=True,
        email_verified_at=None,
    )
    db_session.add_all([inactive, unverified])
    await db_session.commit()

    missing = await unauthenticated_client.post(
        "/api/v1/auth/login",
        json={"email": "missing@example.com", "password": "senha-correta"},
    )
    disabled = await unauthenticated_client.post(
        "/api/v1/auth/login",
        json={"email": inactive.email, "password": "senha-correta"},
    )
    pending = await unauthenticated_client.post(
        "/api/v1/auth/login",
        json={"email": unverified.email, "password": "senha-correta"},
    )

    assert missing.status_code == disabled.status_code == pending.status_code == 401
    assert missing.json() == disabled.json() == pending.json()
    assert missing.json()["detail"]["code"] == "AUTH-003"


@pytest.mark.asyncio
async def test_forgot_password_keeps_generic_response_but_reports_rate_limit(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    _fast_policy(monkeypatch, AuthRateLimitScope.forgot_password, identifier_limit=1)
    user = User(
        name="Usuária Recuperação",
        email="recovery@example.com",
        hashed_password=hash_password("senha-atual"),
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="UR",
        color="#123456",
        is_active=True,
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(user)
    await db_session.commit()
    first = await unauthenticated_client.post(
        "/api/v1/auth/forgot-password",
        json={"email": user.email},
    )
    limited = await unauthenticated_client.post(
        "/api/v1/auth/forgot-password",
        json={"email": user.email},
    )
    missing = await unauthenticated_client.post(
        "/api/v1/auth/forgot-password",
        json={"email": f"missing-{uuid4().hex}@example.com"},
    )

    assert first.status_code == missing.status_code == 202
    assert limited.status_code == 429
    assert limited.json()["detail"]["code"] == "AUTH-RATE-LIMITED"
    for response in (first, missing):
        assert response.json()["status"] == "accepted"
        assert response.json()["deliveryMode"] == "privileged_local_simulation"
        assert response.json()["message"] == first.json()["message"]
        assert "accessToken" not in response.json()
        assert "deliveryId" not in response.json()


@pytest.mark.asyncio
async def test_forgot_password_uses_transactional_delivery_and_reports_provider_failure(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    from app.core.config import settings
    from app.services.email_service import EmailDeliveryResult

    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "aws_private")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", True)
    user = User(
        name="Usuária Entrega Real",
        email=f"real-reset-{uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("senha-atual"),
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="ER",
        color="#123456",
        is_active=True,
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(user)
    await db_session.commit()

    delivery = AsyncMock(
        return_value=EmailDeliveryResult(
            sent=True,
            blocked=False,
            provider="ses",
            message_ref="message-1",
        )
    )
    monkeypatch.setattr("app.services.email_service.send_identity_outbox_email", delivery)
    sent = await unauthenticated_client.post(
        "/api/v1/auth/forgot-password",
        json={"email": user.email},
    )
    assert sent.status_code == 202, sent.text
    assert sent.json()["deliveryMode"] == "transactional_email"
    assert "deliveryId" not in sent.json()
    delivery.assert_awaited_once()
    delivery_count_before_failure = int(
        await db_session.scalar(select(func.count(IdentityOutboxMessage.id))) or 0
    )

    delivery.reset_mock()
    delivery.return_value = EmailDeliveryResult(
        sent=False,
        blocked=False,
        provider="ses",
        error_code="ProviderUnavailable",
    )
    failed = await unauthenticated_client.post(
        "/api/v1/auth/forgot-password",
        json={"email": user.email},
    )
    assert failed.status_code == 202
    assert failed.json()["message"] == sent.json()["message"]
    assert failed.json()["deliveryMode"] == sent.json()["deliveryMode"]
    assert int(await db_session.scalar(select(func.count(IdentityOutboxMessage.id))) or 0) == (
        delivery_count_before_failure + 1
    )
    failed_delivery = await db_session.scalar(
        select(IdentityOutboxMessage)
        .where(IdentityOutboxMessage.delivery_status == "failed")
    )
    assert failed_delivery is not None
    assert failed_delivery.delivery_status.value == "failed"

    delivery.reset_mock()
    outbox_count = int(
        await db_session.scalar(select(func.count(IdentityOutboxMessage.id))) or 0
    )
    missing = await unauthenticated_client.post(
        "/api/v1/auth/forgot-password",
        json={"email": f"missing-{uuid4().hex[:8]}@example.com"},
    )
    assert missing.status_code == 202
    delivery.assert_not_awaited()
    persisted_count = int(
        await db_session.scalar(select(func.count(IdentityOutboxMessage.id))) or 0
    )
    assert persisted_count == outbox_count


@pytest.mark.asyncio
async def test_signup_reports_transactional_delivery_and_attempts_it_after_persisting(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    from app.core.config import settings
    from app.services.email_service import EmailDeliveryResult

    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "aws_private")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", True)
    delivery = AsyncMock(
        return_value=EmailDeliveryResult(
            sent=True,
            blocked=False,
            provider="ses",
            message_ref="signup-message-1",
        )
    )
    monkeypatch.setattr("app.services.email_service.send_identity_outbox_email", delivery)
    signup_email = f"signup-email-{uuid4().hex[:8]}@example.com"

    response = await unauthenticated_client.post(
        "/api/v1/auth/register",
        headers={"Idempotency-Key": f"signup-email-{uuid4().hex}"},
        json={
            "name": "Owner Comercial",
            "email": signup_email,
            "workspaceName": "Escritorio Comercial",
            "password": "SenhaComercial#2026",
            "plan": "crm_start",
        },
    )

    assert response.status_code == 202, response.text
    assert response.json()["deliveryMode"] == "transactional_email"
    assert "local" not in response.json()["message"].lower()
    delivery.assert_awaited_once()
    message = await db_session.get(IdentityOutboxMessage, response.json()["deliveryId"])
    assert message is not None
    assert message.delivery_status.value == "sent"

    first_delivery_id = response.json()["deliveryId"]
    resend_key = f"signup-resend-{uuid4().hex}"
    resent = await unauthenticated_client.post(
        "/api/v1/auth/register/resend",
        headers={"Idempotency-Key": resend_key},
        json={"email": signup_email},
    )
    assert resent.status_code == 202, resent.text
    assert resent.json()["deliveryMode"] == "transactional_email"
    assert "deliveryId" not in resent.json()
    assert delivery.await_count == 2
    assert await db_session.get(IdentityOutboxMessage, first_delivery_id) is None

    replayed = await unauthenticated_client.post(
        "/api/v1/auth/register/resend",
        headers={"Idempotency-Key": resend_key},
        json={"email": signup_email},
    )
    assert replayed.status_code == 202, replayed.text
    assert replayed.json() == resent.json()
    assert delivery.await_count == 2

    delivery.reset_mock()
    missing = await unauthenticated_client.post(
        "/api/v1/auth/register/resend",
        headers={"Idempotency-Key": f"signup-resend-missing-{uuid4().hex}"},
        json={"email": f"missing-signup-{uuid4().hex[:8]}@example.com"},
    )
    assert missing.status_code == 202
    assert missing.json()["message"] == resent.json()["message"]
    delivery.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_outbox_fails_closed_without_platform_admin_mfa(
    unauthenticated_client,
) -> None:
    response = await unauthenticated_client.get(
        f"/api/v1/auth/local-outbox/{uuid4()}"
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTH-OUTBOX-PRIVILEGED"


@pytest.mark.asyncio
async def test_reset_rate_limit_uses_same_invalid_token_response(
    unauthenticated_client,
    monkeypatch,
) -> None:
    _fast_policy(monkeypatch, AuthRateLimitScope.reset_password, identifier_limit=1)
    payload = {"token": "invalid-reset-token", "new_password": "senha-nova-segura"}

    first = await unauthenticated_client.post(
        "/api/v1/auth/reset-password",
        json=payload,
    )
    limited = await unauthenticated_client.post(
        "/api/v1/auth/reset-password",
        json=payload,
    )

    assert first.status_code == limited.status_code == 400
    assert first.json() == limited.json()
    assert first.json()["detail"]["code"] == "AUTH-VERIFICATION-INVALID"


@pytest.mark.asyncio
async def test_uniform_timing_waits_until_policy_floor(monkeypatch) -> None:
    scope = AuthRateLimitScope.login
    monkeypatch.setitem(
        RATE_LIMIT_POLICIES,
        scope,
        RateLimitPolicy(60, 5, 10, 0.25),
    )
    sleep = AsyncMock()
    monkeypatch.setattr(auth_rate_limit_service, "monotonic", lambda: 10.10)
    monkeypatch.setattr(auth_rate_limit_service.asyncio, "sleep", sleep)

    await enforce_uniform_timing(10.0, scope)

    sleep.assert_awaited_once()
    assert sleep.await_args.args[0] == pytest.approx(0.15)


@pytest.mark.asyncio
async def test_register_existing_and_new_identity_share_bcrypt_and_timing_floor(
    unauthenticated_client,
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    monkeypatch.setitem(
        RATE_LIMIT_POLICIES,
        AuthRateLimitScope.register,
        RateLimitPolicy(60, 20, 40, 0.06),
    )
    existing = User(
        name="Identidade Existente",
        email="register-existing@example.com",
        hashed_password=hash_password("SenhaAnterior#2026"),
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="IE",
        color="#123456",
        is_active=True,
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(existing)
    await db_session.commit()
    calls: list[str] = []

    def controlled_bcrypt(password: str) -> str:
        calls.append(password)
        sleep(0.01)
        return "$2b$12$kexpl63tUisHm2fu/9zqdeOAiMvo4z0ljCPgrHDXRaPoKt7v6/yPi"

    monkeypatch.setattr(identity_lifecycle_service, "hash_password", controlled_bcrypt)

    async def request(email: str) -> tuple[int, float]:
        started = monotonic()
        response = await unauthenticated_client.post(
            "/api/v1/auth/register",
            json={
                "name": "Cadastro Temporal",
                "email": email,
                "workspaceName": "Workspace Temporal",
                "password": "SenhaTemporal#2026",
                "plan": "basic",
            },
            headers={"Idempotency-Key": f"timing-{uuid4().hex}"},
        )
        return response.status_code, monotonic() - started

    existing_status, existing_elapsed = await request(existing.email)
    new_status, new_elapsed = await request(f"new-{uuid4().hex}@example.com")

    assert existing_status == new_status == 202
    assert calls == ["SenhaTemporal#2026", "SenhaTemporal#2026"]
    assert min(existing_elapsed, new_elapsed) >= 0.05
    assert abs(existing_elapsed - new_elapsed) < 0.05
