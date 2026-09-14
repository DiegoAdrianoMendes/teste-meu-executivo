from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request

from app.core.security import hash_password, verify_password
from app.db.tenant import WORKSPACE_SESSION_KEY, get_workspace_context
from app.models.auth_security import AuthRateLimitScope
from app.models.identity import (
    IdentityDeliveryStatus,
    IdentityOutboxMessage,
    IdentityOutboxPurpose,
    SignupVerification,
)
from app.models.password_reset import PasswordResetToken
from app.models.user import AgentType, User, UserRole, normalize_email
from app.models.workspace_membership import WorkspaceMembership
from app.schemas.identity import (
    ForgotPasswordRequest,
    InvitationCreateRequest,
    ResetPasswordRequest,
)
from app.schemas.user import LoginRequest, RegisterRequest, UserCreate
from app.schemas.workspace import TeamMemberCreate
from app.services import identity_lifecycle_service
from app.services.auth_rate_limit_service import RATE_LIMIT_POLICIES, RateLimitPolicy
from app.services.auth_revocation_service import AuthRevocationService
from app.services.identity_lifecycle_service import IdentityLifecycleService
from app.services.identity_outbox_service import LocalIdentityOutboxService


def _user(*, email: str = "identity@example.com") -> User:
    return User(
        name="Identity User",
        email=email,
        hashed_password=hash_password("SenhaAtual#2026"),
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="IU",
        color="#123456",
        is_active=True,
        email_verified_at=datetime.now(UTC),
    )


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/reset-password",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
    )


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (LoginRequest, {"email": "usér@example.com", "password": "secret"}),
        (ForgotPasswordRequest, {"email": "user@exämple.com"}),
        (
            RegisterRequest,
            {
                "name": "Owner",
                "email": "usér@example.com",
                "workspaceName": "Workspace",
                "password": "SenhaForte#2026",
            },
        ),
        (UserCreate, {"name": "User", "email": "user@exämple.com"}),
        (InvitationCreateRequest, {"name": "Invitee", "email": "usér@example.com"}),
        (TeamMemberCreate, {"name": "Member", "email": "user@exämple.com"}),
    ],
)
def test_identity_schemas_reject_unicode_email(schema, payload) -> None:
    with pytest.raises(ValidationError, match="ASCII"):
        schema.model_validate(payload)


def test_email_normalizer_is_ascii_strict_and_canonical() -> None:
    assert normalize_email("  Owner.Tag@Example.COM  ") == "owner.tag@example.com"
    assert str(LoginRequest(email="Owner.Tag@Example.COM", password="secret").email) == (
        "owner.tag@example.com"
    )

    with pytest.raises(ValueError, match="ASCII"):
        User(name="Unicode", email="usér@example.com", initials="UN")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "email",
    [
        "Owner@example.com",
        "owner@exämple.com",
        " owner@example.com ",
    ],
)
async def test_user_check_constraint_rejects_noncanonical_core_writes(
    db_session,
    email: str,
) -> None:
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(
                User.__table__.insert().values(
                    id=uuid4(),
                    name="Direct Write",
                    email=email,
                    agent_type=AgentType.human,
                    role=UserRole.admin,
                    initials="DW",
                    color="#123456",
                    is_active=True,
                    auth_epoch=0,
                    is_internal_staff=False,
                    is_platform_admin=False,
                )
            )


@pytest.mark.asyncio
async def test_signup_binds_tenant_before_new_and_resumed_signup_access(
    db_session,
    monkeypatch,
) -> None:
    payload = RegisterRequest.model_validate(
        {
            "name": "Tenant Owner",
            "email": f"tenant-owner-{uuid4().hex[:8]}@example.com",
            "workspaceName": "Tenant First",
            "password": "SenhaTenant#2026",
            "plan": "basic",
        }
    )
    signup_flush_contexts: list[UUID | None] = []

    @event.listens_for(db_session.sync_session, "before_flush")
    def record_signup_flush_context(session, _flush_context, _instances) -> None:
        if any(
            isinstance(row, (WorkspaceMembership, SignupVerification))
            for row in session.new
        ):
            signup_flush_contexts.append(get_workspace_context(db_session))

    service = IdentityLifecycleService(db_session)
    first = await service.request_signup(
        payload,
        idempotency_key=f"new-signup-{uuid4().hex}",
    )
    verification = await db_session.scalar(
        select(SignupVerification).where(SignupVerification.last_delivery_id == first.delivery_id)
    )
    assert verification is not None
    assert signup_flush_contexts
    assert all(context == verification.workspace_id for context in signup_flush_contexts)

    db_session.sync_session.info.pop(WORKSPACE_SESSION_KEY, None)
    resolver = AsyncMock(return_value=verification.workspace_id)
    monkeypatch.setattr(
        identity_lifecycle_service,
        "resolve_signup_verification_workspace",
        resolver,
    )
    original_pending_signup = IdentityLifecycleService._pending_signup

    async def guarded_pending_signup(self, user_id, *, lock):
        assert get_workspace_context(self.db) == verification.workspace_id
        return await original_pending_signup(self, user_id, lock=lock)

    monkeypatch.setattr(
        IdentityLifecycleService,
        "_pending_signup",
        guarded_pending_signup,
    )
    resumed = await service.request_signup(
        payload,
        idempotency_key=f"resumed-signup-{uuid4().hex}",
    )

    assert resumed.delivery_id != first.delivery_id
    resolver.assert_awaited_once_with(
        db_session,
        token_digest=None,
        delivery_id=first.delivery_id,
    )


@pytest.mark.asyncio
async def test_limited_forgot_password_is_synthetic_without_identity_growth(
    unauthenticated_client,
    db_session,
    monkeypatch,
) -> None:
    monkeypatch.setitem(
        RATE_LIMIT_POLICIES,
        AuthRateLimitScope.forgot_password,
        RateLimitPolicy(60, 1, 20, 0),
    )
    user = _user(email="limited-reset@example.com")
    db_session.add(user)
    await db_session.commit()

    first = await unauthenticated_client.post(
        "/api/v1/auth/forgot-password",
        json={"email": user.email},
    )
    assert first.status_code == 202
    reset_count = await db_session.scalar(select(func.count(PasswordResetToken.id)))
    outbox_count = await db_session.scalar(
        select(func.count(IdentityOutboxMessage.id)).where(
            IdentityOutboxMessage.purpose == IdentityOutboxPurpose.password_reset
        )
    )
    lifecycle = AsyncMock(side_effect=AssertionError("blocked request reached lifecycle"))
    monkeypatch.setattr(IdentityLifecycleService, "request_password_reset", lifecycle)

    limited = await unauthenticated_client.post(
        "/api/v1/auth/forgot-password",
        json={"email": user.email},
    )

    assert limited.status_code == 429
    assert limited.json()["detail"]["code"] == "AUTH-RATE-LIMITED"
    lifecycle.assert_not_awaited()
    assert await db_session.scalar(select(func.count(PasswordResetToken.id))) == reset_count
    assert (
        await db_session.scalar(
            select(func.count(IdentityOutboxMessage.id)).where(
                IdentityOutboxMessage.purpose == IdentityOutboxPurpose.password_reset
            )
        )
        == outbox_count
    )


@pytest.mark.asyncio
async def test_outbox_purge_is_expiration_scoped_and_bounded(db_session) -> None:
    service = LocalIdentityOutboxService(db_session)
    now = datetime.now(UTC)
    expired_ids = []
    for index in range(3):
        message = await service.enqueue(
            purpose=IdentityOutboxPurpose.password_reset,
            recipient_email=f"expired-{index}@example.com",
            payload={"link": "/expired"},
            related_type="password_reset",
            related_id=None,
            expires_at=now - timedelta(minutes=index + 1),
        )
        expired_ids.append(message.id)
    live = await service.enqueue(
        purpose=IdentityOutboxPurpose.password_reset,
        recipient_email="live@example.com",
        payload={"link": "/live"},
        related_type="password_reset",
        related_id=None,
        expires_at=now + timedelta(hours=1),
    )
    await db_session.commit()

    assert await service.purge_expired(limit=2, now=now) == 2
    await db_session.commit()
    assert (
        await db_session.scalar(
            select(func.count(IdentityOutboxMessage.id)).where(
                IdentityOutboxMessage.id.in_(expired_ids)
            )
        )
        == 1
    )
    assert await db_session.get(IdentityOutboxMessage, live.id) is not None
    assert await service.purge_expired(limit=2, now=now) == 1
    await db_session.commit()
    assert await service.purge_expired(limit=2, now=now) == 0

    with pytest.raises(ValueError, match="limit"):
        await service.purge_expired(limit=0, now=now)


@pytest.mark.asyncio
async def test_identity_outbox_delivers_encrypted_recipient_and_persists_result(
    monkeypatch,
    db_session,
) -> None:
    from app.services.email_service import EmailDeliveryResult

    recipient = f"delivery-{uuid4().hex[:8]}@example.com"
    service = LocalIdentityOutboxService(db_session)
    message = await service.enqueue(
        purpose=IdentityOutboxPurpose.workspace_invitation,
        recipient_email=recipient,
        payload={"link": "/accept-invitation/example?token=secret"},
        related_type="workspace_invitation",
        related_id=None,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    await db_session.commit()
    assert recipient not in message.payload_ciphertext

    delivery = AsyncMock(
        return_value=EmailDeliveryResult(
            sent=True,
            blocked=False,
            provider="ses",
            message_ref="provider-message-id",
        )
    )
    monkeypatch.setattr("app.services.email_service.send_identity_outbox_email", delivery)

    result = await service.deliver_pending(limit=1)
    await db_session.commit()
    await db_session.refresh(message)

    assert result == {"processed": 1, "sent": 1, "failed": 0, "blocked": 0}
    delivery.assert_awaited_once_with(
        to_email=recipient,
        purpose="workspace_invitation",
        link="/accept-invitation/example?token=secret",
    )
    assert message.delivery_status == IdentityDeliveryStatus.sent
    assert message.delivery_provider == "ses"
    assert message.provider_message_ref == "provider-message-id"
    assert message.delivered_at is not None


@pytest.mark.asyncio
async def test_password_reset_never_sends_before_token_and_outbox_commit(
    monkeypatch,
    db_session,
) -> None:
    from app.core.config import settings

    user = _user(email=f"atomic-reset-{uuid4().hex[:8]}@example.com")
    db_session.add(user)
    await db_session.commit()
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "aws_private")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", True)
    delivery = AsyncMock()
    monkeypatch.setattr("app.services.email_service.send_identity_outbox_email", delivery)
    original_commit = db_session.commit
    monkeypatch.setattr(
        db_session,
        "commit",
        AsyncMock(side_effect=RuntimeError("commit failed before delivery")),
    )

    with pytest.raises(RuntimeError, match="commit failed before delivery"):
        await IdentityLifecycleService(db_session).request_password_reset(user.email)

    delivery.assert_not_awaited()
    monkeypatch.setattr(db_session, "commit", original_commit)
    await db_session.rollback()


async def _reset_fixture(db_session) -> tuple[User, PasswordResetToken, IdentityOutboxMessage, str]:
    user = _user(email=f"reset-{uuid4().hex[:8]}@example.com")
    db_session.add(user)
    await db_session.flush()
    raw_token = f"reset-{uuid4().hex}"
    reset = PasswordResetToken(
        user_id=user.id,
        token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        code_hash="c" * 64,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(reset)
    await db_session.flush()
    delivery = await LocalIdentityOutboxService(db_session).enqueue(
        purpose=IdentityOutboxPurpose.password_reset,
        recipient_email=user.email,
        payload={"code": "123456", "link": f"/reset-password?token={raw_token}"},
        related_type="password_reset",
        related_id=reset.id,
        expires_at=reset.expires_at,
    )
    reset.last_delivery_id = delivery.id
    await db_session.commit()
    return user, reset, delivery, raw_token


@pytest.mark.asyncio
async def test_reset_consumption_erases_secret_and_outbox(monkeypatch, db_session) -> None:
    user, reset, delivery, raw_token = await _reset_fixture(db_session)
    original_digest = reset.token_hash
    monkeypatch.setattr(
        AuthRevocationService,
        "fanout_after_commit",
        AsyncMock(return_value=True),
    )

    result = await IdentityLifecycleService(db_session).reset_password(
        ResetPasswordRequest(token=raw_token, new_password="SenhaNova#2026"),
        request=_request(),
    )

    await db_session.refresh(user)
    await db_session.refresh(reset)
    assert result.message.startswith("Senha redefinida")
    assert verify_password("SenhaNova#2026", user.hashed_password)
    assert reset.used_at is not None
    assert reset.token_hash != original_digest
    assert reset.code_hash is None
    assert reset.last_delivery_id is None
    assert await db_session.get(IdentityOutboxMessage, delivery.id) is None


@pytest.mark.asyncio
async def test_reset_secret_and_outbox_rollback_together(monkeypatch, db_session) -> None:
    user, reset, delivery, raw_token = await _reset_fixture(db_session)
    user_id = user.id
    reset_id = reset.id
    delivery_id = delivery.id
    original_password = user.hashed_password
    original_digest = reset.token_hash
    original_code_digest = reset.code_hash
    original_commit = db_session.commit
    monkeypatch.setattr(
        db_session,
        "commit",
        AsyncMock(side_effect=RuntimeError("commit failed")),
    )

    with pytest.raises(RuntimeError, match="commit failed"):
        await IdentityLifecycleService(db_session).reset_password(
            ResetPasswordRequest(token=raw_token, new_password="SenhaNova#2026"),
            request=_request(),
        )
    monkeypatch.setattr(db_session, "commit", original_commit)
    await db_session.rollback()

    persisted_user = await db_session.get(User, user_id)
    persisted_reset = await db_session.get(PasswordResetToken, reset_id)
    persisted_delivery = await db_session.get(IdentityOutboxMessage, delivery_id)
    assert persisted_user is not None
    assert persisted_user.hashed_password == original_password
    assert persisted_reset is not None
    assert persisted_reset.used_at is None
    assert persisted_reset.token_hash == original_digest
    assert persisted_reset.code_hash == original_code_digest
    assert persisted_reset.last_delivery_id == delivery_id
    assert persisted_delivery is not None
