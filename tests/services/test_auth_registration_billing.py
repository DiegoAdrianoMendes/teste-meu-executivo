from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models.billing import Subscription
from app.models.commercial_catalog import WorkspaceCommercialContract
from app.models.identity import (
    IdentityIdempotencyRecord,
    IdentityOutboxMessage,
    SignupVerification,
)
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.schemas.user import RegisterRequest
from app.services.auth_service import AuthService
from app.services.commercial_legacy_bridge_service import CanonicalContractCommand


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _registration_json(*, email: str, password: str = "Senha@12345") -> dict:
    return {
        "name": "Owner Local",
        "email": email,
        "workspaceName": f"Workspace {uuid4().hex[:8]}",
        "password": password,
        "plan": "basic",
    }


async def _request_registration(client, payload: dict, *, key: str):
    return await client.post(
        "/api/v1/auth/register",
        json=payload,
        headers={"Idempotency-Key": key},
    )


async def _verification_token(client, accepted: dict, headers: dict[str, str]) -> str:
    delivery = await client.get(
        f"/api/v1/auth/local-outbox/{accepted['deliveryId']}",
        headers=headers,
    )
    assert delivery.status_code == 200, delivery.text
    return parse_qs(urlsplit(delivery.json()["link"]).query)["token"][0]


@pytest.mark.asyncio
async def test_verification_is_atomic_when_local_billing_provisioning_fails(
    unauthenticated_client,
    db_session,
    monkeypatch,
    local_outbox_headers,
):
    email = f"atomic-{uuid4().hex[:8]}@example.com"
    payload = _registration_json(email=email)
    requested = await _request_registration(
        unauthenticated_client,
        payload,
        key=f"atomic-{uuid4().hex}",
    )
    assert requested.status_code == 202, requested.text
    token = await _verification_token(
        unauthenticated_client,
        requested.json(),
        local_outbox_headers,
    )

    async def fail_provisioning(*_args, **_kwargs):
        raise RuntimeError("local provisioning failed")

    monkeypatch.setattr(CanonicalContractCommand, "provision_trial", fail_provisioning)
    with pytest.raises(RuntimeError, match="local provisioning failed"):
        await unauthenticated_client.post(
            "/api/v1/auth/register/verify",
            json={"token": token},
        )
    await db_session.rollback()

    user = await db_session.scalar(select(User).where(User.email == email))
    verification = await db_session.scalar(
        select(SignupVerification).where(SignupVerification.user_id == user.id)
    )
    workspace = await db_session.get(Workspace, verification.workspace_id)
    membership = await db_session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.user_id == user.id,
            WorkspaceMembership.workspace_id == workspace.id,
        )
    )
    assert user.is_active is False
    assert user.email_verified_at is None
    assert workspace.status == WorkspaceStatus.provisioning
    assert workspace.is_active is False
    assert membership.status == MembershipStatus.invited
    assert verification.consumed_at is None
    assert await db_session.scalar(select(func.count(Subscription.id))) == 0
    assert (
        await db_session.scalar(select(func.count(WorkspaceCommercialContract.id)))
        == 0
    )


@pytest.mark.asyncio
async def test_registration_idempotency_replay_stays_pending_without_tokens(
    unauthenticated_client,
    db_session,
):
    payload = _registration_json(email=f"replay-{uuid4().hex[:8]}@example.com")
    key = f"register-replay-{uuid4().hex}"

    first = await _request_registration(unauthenticated_client, payload, key=key)
    replay = await _request_registration(unauthenticated_client, payload, key=key)

    assert first.status_code == replay.status_code == 202
    assert replay.json() == first.json()
    assert "accessToken" not in first.json()
    assert await db_session.scalar(select(func.count(User.id))) == 1
    assert await db_session.scalar(select(func.count(Workspace.id))) == 1
    assert await db_session.scalar(select(func.count(WorkspaceMembership.id))) == 1
    assert await db_session.scalar(select(func.count(Subscription.id))) == 0


@pytest.mark.asyncio
async def test_expired_idempotency_key_is_atomically_reusable_without_delivery_replay(
    unauthenticated_client,
    db_session,
):
    payload = _registration_json(email=f"ttl-{uuid4().hex[:8]}@example.com")
    key = f"register-ttl-{uuid4().hex}"
    first = await _request_registration(unauthenticated_client, payload, key=key)
    assert first.status_code == 202
    record = await db_session.scalar(
        select(IdentityIdempotencyRecord).where(
            IdentityIdempotencyRecord.operation == "signup"
        )
    )
    first_delivery = await db_session.get(
        IdentityOutboxMessage,
        first.json()["deliveryId"],
    )
    record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    first_delivery.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    reused = await _request_registration(unauthenticated_client, payload, key=key)

    assert reused.status_code == 202, reused.text
    assert reused.json()["deliveryId"] != first.json()["deliveryId"]
    await db_session.refresh(record)
    assert _as_utc(record.expires_at) > datetime.now(UTC)
    assert record.response_data["delivery_id"] == reused.json()["deliveryId"]


@pytest.mark.asyncio
async def test_registration_idempotency_key_rejects_changed_password_without_login(
    unauthenticated_client,
    db_session,
):
    payload = _registration_json(email=f"conflict-{uuid4().hex[:8]}@example.com")
    key = f"register-conflict-{uuid4().hex}"
    first = await _request_registration(unauthenticated_client, payload, key=key)
    changed = await _request_registration(
        unauthenticated_client,
        {**payload, "password": "OutraSenha@123"},
        key=key,
    )

    assert first.status_code == 202
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "AUTH-IDEMPOTENCY-CONFLICT"
    assert "accessToken" not in changed.json()
    assert await db_session.scalar(select(func.count(User.id))) == 1


@pytest.mark.asyncio
async def test_verified_email_can_log_in_with_different_letter_case(
    unauthenticated_client,
    local_outbox_headers,
):
    email = f"Mixed.Case-{uuid4().hex[:8]}@Example.com"
    payload = _registration_json(email=email)
    requested = await _request_registration(
        unauthenticated_client,
        payload,
        key=f"mixed-case-{uuid4().hex}",
    )
    token = await _verification_token(
        unauthenticated_client,
        requested.json(),
        local_outbox_headers,
    )
    verified = await unauthenticated_client.post(
        "/api/v1/auth/register/verify",
        json={"token": token},
    )
    assert verified.status_code == 200, verified.text

    logged_in = await unauthenticated_client.post(
        "/api/v1/auth/login",
        json={"email": email.upper(), "password": payload["password"]},
    )
    assert logged_in.status_code == 200, logged_in.text
    assert logged_in.json()["user"]["id"] == verified.json()["user"]["id"]


@pytest.mark.asyncio
async def test_legacy_auth_service_register_is_not_an_alternate_login(
    db_session,
):
    payload = RegisterRequest.model_validate(
        _registration_json(email=f"legacy-{uuid4().hex[:8]}@example.com")
    )

    with pytest.raises(HTTPException) as raised:
        await AuthService(db_session).register(payload)

    assert raised.value.status_code == 410
    assert raised.value.detail["code"] == "AUTH-VERIFICATION-REQUIRED"
    assert await db_session.scalar(select(func.count(User.id))) == 0
