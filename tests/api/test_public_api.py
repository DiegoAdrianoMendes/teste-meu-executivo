from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select

from app.core.deps import get_db
from app.db.tenant import get_workspace_context
from app.main import app as fastapi_app
from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.client import Client
from app.models.entitlement import EntitlementKey, PlanEntitlement
from app.models.matter import Matter
from app.models.public_api import (
    PublicApiMutationReceipt,
    PublicApiMutationState,
    WorkspacePublicApiToken,
)
from app.models.subscription_access import (
    SubscriptionAccessState,
    SubscriptionAccessStatus,
    SubscriptionAccessTransition,
)
from app.models.user import User
from app.models.whatsapp import (
    ConnectionType,
    WaConnectionStatus,
    WaMessageStatus,
    WhatsAppConnection,
    WhatsAppContact,
    WhatsAppMessage,
)
from app.models.whatsapp_campaign import MetaTemplateStatus, WhatsAppMetaTemplate
from app.schemas.public_api import PublicApiTemplateSendRequest
from app.services.public_api_service import PublicApiService
from app.services.whatsapp.secrets import WhatsAppSecretCipher


class _FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, _key: str, _seconds: int) -> bool:
        return True


async def _seed_subscription_access(
    db_session,
    workspace_id,
    *,
    access_status: SubscriptionAccessStatus,
    now: datetime,
    trial_end: datetime | None = None,
    grace_end: datetime | None = None,
    subscription: Subscription | None = None,
):
    subscription_status = (
        SubscriptionStatus.trialing
        if access_status == SubscriptionAccessStatus.trial
        else (
            SubscriptionStatus.past_due
            if access_status == SubscriptionAccessStatus.grace
            else SubscriptionStatus.active
        )
    )
    if subscription is None:
        plan = Plan(
            name="Public API access plan",
            tier=PlanTier.free,
            price_monthly_cents=0,
            price_annually_cents=0,
            features={},
        )
        db_session.add(plan)
        await db_session.flush()
        subscription = Subscription(
            workspace_id=workspace_id,
            plan_id=plan.id,
            interval=BillingInterval.monthly,
            amount_cents=0,
        )
        db_session.add(subscription)
    subscription.status = subscription_status
    subscription.current_period_start = now - timedelta(days=2)
    subscription.current_period_end = now + timedelta(days=30)
    subscription.trial_end = trial_end
    await db_session.flush()
    # The authenticated setup request may have bootstrapped the access projection.
    # These transition tests replace that projection with a controlled historical
    # baseline, so its bootstrap event must not become the temporal floor for the
    # expiry event under test.
    await db_session.execute(
        delete(SubscriptionAccessTransition).where(
            SubscriptionAccessTransition.workspace_id == workspace_id
        )
    )
    state = await db_session.scalar(
        select(SubscriptionAccessState).where(
            SubscriptionAccessState.workspace_id == workspace_id
        )
    )
    if state is None:
        state = SubscriptionAccessState(
            workspace_id=workspace_id,
            subscription_id=subscription.id,
            status=access_status,
            state_version=1,
            status_changed_at=now - timedelta(days=2),
        )
        db_session.add(state)
    else:
        state.subscription_id = subscription.id
        state.status = access_status
        state.state_version += 1
        state.status_changed_at = now - timedelta(days=2)
    state.trial_ends_at = (
        trial_end if access_status == SubscriptionAccessStatus.trial else None
    )
    state.grace_started_at = (
        now - timedelta(days=2)
        if access_status == SubscriptionAccessStatus.grace
        else None
    )
    state.grace_ends_at = (
        grace_end if access_status == SubscriptionAccessStatus.grace else None
    )
    await db_session.commit()
    return subscription, state


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_public_api_can_create_lead_read_conversation_and_note(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    fake_redis = _FakeRedis()
    monkeypatch.setattr(
        "app.services.public_api_service.get_redis",
        lambda: _async_value(fake_redis),
    )
    cipher = WhatsAppSecretCipher()
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn_public_api",
        phone_number="5511999997777",
        display_phone_number="+55 11 99999-7777",
        access_token_encrypted=cipher.encrypt("meta-public-token"),
        status=WaConnectionStatus.connected,
        connected_at=datetime.now(UTC),
    )
    db_session.add(connection)
    await db_session.commit()

    token_response = await client.post(
        "/api/v1/integrations/public-api-tokens",
        json={
            "label": "Parceiro CRM",
            "scopes": [
                "contacts:read",
                "conversations:read",
                "leads:write",
                "notes:write",
            ],
            "rateLimitPerMinute": 120,
        },
    )
    assert token_response.status_code == 201, token_response.text
    token_payload = token_response.json()
    api_headers = {"X-KLM-API-Key": token_payload["plainTextToken"]}

    lead_response = await client.post(
        "/api/v1/public-api/leads",
        headers={**api_headers, "Idempotency-Key": "lead-marina-001"},
        json={
            "name": "Marina Costa",
            "phone": "+55 11 98888-7777",
            "email": "marina@example.com",
            "demandType": "Ação trabalhista",
            "practiceArea": "trabalhista",
            "leadSource": "crm_parceiro",
            "executiveSummary": "Lead veio do site parceiro.",
            "nextAction": "Enviar proposta inicial",
        },
    )
    assert lead_response.status_code == 201, lead_response.text
    assert get_workspace_context(db_session) == auth_context.workspace_id
    lead_payload = lead_response.json()
    assert lead_payload["matter"]["leadSource"] == "crm_parceiro"
    assert lead_payload["contact"] is not None
    contact_id = lead_payload["contact"]["id"]

    contacts_response = await client.get(
        "/api/v1/public-api/contacts",
        headers=api_headers,
        params={"search": "Marina", "limit": 10},
    )
    assert contacts_response.status_code == 200, contacts_response.text
    contacts_payload = contacts_response.json()
    assert any(item["id"] == contact_id for item in contacts_payload)

    conversation_response = await client.get(
        f"/api/v1/public-api/conversations/{contact_id}",
        headers=api_headers,
    )
    assert conversation_response.status_code == 200, conversation_response.text
    conversation_payload = conversation_response.json()
    assert conversation_payload["conversation"]["contact"]["id"] == contact_id

    note_response = await client.post(
        f"/api/v1/public-api/conversations/{contact_id}/notes",
        headers={**api_headers, "Idempotency-Key": "note-marina-001"},
        json={"body": "Lead importado via parceiro e qualificado pelo time."},
    )
    assert note_response.status_code == 201, note_response.text
    note_payload = note_response.json()
    assert note_payload["note"]["body"] == "Lead importado via parceiro e qualificado pelo time."

    logs_response = await client.get("/api/v1/integrations/public-api-tokens/logs")
    assert logs_response.status_code == 200, logs_response.text
    logs_payload = logs_response.json()
    paths = {item["path"] for item in logs_payload}
    assert "/api/v1/public-api/leads" in paths
    assert f"/api/v1/public-api/conversations/{contact_id}" in paths


@pytest.mark.asyncio
async def test_public_api_lead_contact_limit_rolls_back_the_whole_mutation(
    client,
    db_session,
    auth_context,
    active_subscription,
    monkeypatch,
):
    fake_redis = _FakeRedis()
    monkeypatch.setattr(
        "app.services.public_api_service.get_redis",
        lambda: _async_value(fake_redis),
    )
    contact_entitlement = await db_session.scalar(
        select(PlanEntitlement).where(
            PlanEntitlement.plan_id == active_subscription.plan_id,
            PlanEntitlement.entitlement == EntitlementKey.contacts,
        )
    )
    assert contact_entitlement is not None
    contact_entitlement.limit_quantity = 0
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn_public_contact_limit",
        status=WaConnectionStatus.connected,
        connected_at=datetime.now(UTC),
    )
    db_session.add(connection)
    await db_session.commit()

    token_response = await client.post(
        "/api/v1/integrations/public-api-tokens",
        json={
            "label": "CRM limitado",
            "scopes": ["leads:write"],
            "rateLimitPerMinute": 120,
        },
    )
    assert token_response.status_code == 201, token_response.text
    blocked = await client.post(
        "/api/v1/public-api/leads",
        headers={
            "X-KLM-API-Key": token_response.json()["plainTextToken"],
            "Idempotency-Key": "lead-contact-limit-001",
        },
        json={
            "name": "Contato acima do limite",
            "phone": "+55 61 99999-0001",
            "demandType": "Consulta",
            "practiceArea": "trabalhista",
        },
    )

    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"]["code"] == "ENT-003"
    assert await db_session.scalar(select(func.count(Client.id))) == 0
    assert await db_session.scalar(select(func.count(Matter.id))) == 0
    assert await db_session.scalar(select(func.count(WhatsAppContact.id))) == 0


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_public_api_respects_scope_and_revoked_tokens(client):
    token_response = await client.post(
        "/api/v1/integrations/public-api-tokens",
        json={
            "label": "Somente leitura",
            "scopes": ["contacts:read"],
            "rateLimitPerMinute": 60,
        },
    )
    assert token_response.status_code == 201, token_response.text
    token_payload = token_response.json()
    token_id = token_payload["token"]["id"]
    api_headers = {"X-KLM-API-Key": token_payload["plainTextToken"]}

    forbidden_response = await client.post(
        "/api/v1/public-api/leads",
        headers={**api_headers, "Idempotency-Key": "forbidden-lead-001"},
        json={
            "name": "Lead Bloqueado",
            "demandType": "Consulta",
            "practiceArea": "trabalhista",
        },
    )
    assert forbidden_response.status_code == 403, forbidden_response.text

    revoke_response = await client.delete(f"/api/v1/integrations/public-api-tokens/{token_id}")
    assert revoke_response.status_code == 204, revoke_response.text

    unauthorized_response = await client.get("/api/v1/public-api/contacts", headers=api_headers)
    assert unauthorized_response.status_code == 401, unauthorized_response.text


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_public_api_auto_revocation_survives_request_rollback(
    client,
    db_session,
    auth_context,
):
    token_response = await client.post(
        "/api/v1/integrations/public-api-tokens",
        json={
            "label": "Revogação automática",
            "scopes": ["contacts:read"],
            "rateLimitPerMinute": 60,
        },
    )
    assert token_response.status_code == 201, token_response.text
    token_payload = token_response.json()
    token_id = UUID(token_payload["token"]["id"])
    api_headers = {"X-KLM-API-Key": token_payload["plainTextToken"]}

    creator = await db_session.get(User, auth_context.user_id)
    assert creator is not None
    creator.is_active = False
    await db_session.commit()

    async def request_scoped_db():
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    fastapi_app.dependency_overrides[get_db] = request_scoped_db

    rejected = await client.get("/api/v1/public-api/contacts", headers=api_headers)
    assert rejected.status_code == 401, rejected.text
    assert rejected.json()["detail"]["code"] == "PUBAPI-404"

    db_session.expire_all()
    persisted_token = await db_session.get(WorkspacePublicApiToken, token_id)
    assert persisted_token is not None
    assert persisted_token.is_active is False
    assert persisted_token.revoked_at is not None

    rejected_again = await client.get(
        "/api/v1/public-api/contacts",
        headers=api_headers,
    )
    assert rejected_again.status_code == 401, rejected_again.text
    assert rejected_again.json()["detail"]["code"] == "PUBAPI-401"


async def _async_value(value):
    return value


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_public_api_requires_and_replays_idempotency_key(
    client,
    db_session,
    monkeypatch,
):
    fake_redis = _FakeRedis()
    monkeypatch.setattr(
        "app.services.public_api_service.get_redis",
        lambda: _async_value(fake_redis),
    )
    token_response = await client.post(
        "/api/v1/integrations/public-api-tokens",
        json={
            "label": "CRM idempotente",
            "scopes": ["leads:write"],
            "rateLimitPerMinute": 120,
        },
    )
    assert token_response.status_code == 201, token_response.text
    api_key = token_response.json()["plainTextToken"]
    payload = {
        "name": "Lead Idempotente",
        "demandType": "Consulta contratual",
        "practiceArea": "trabalhista",
    }

    missing = await client.post(
        "/api/v1/public-api/leads",
        headers={"X-KLM-API-Key": api_key},
        json=payload,
    )
    assert missing.status_code == 400
    assert missing.json()["detail"]["code"] == "PUBAPI-IDEMPOTENCY-REQUIRED"

    headers = {
        "X-KLM-API-Key": api_key,
        "Idempotency-Key": "lead-idempotente-001",
    }
    created = await client.post("/api/v1/public-api/leads", headers=headers, json=payload)
    replay = await client.post("/api/v1/public-api/leads", headers=headers, json=payload)

    assert created.status_code == 201, created.text
    assert replay.status_code == 201, replay.text
    assert replay.json() == created.json()

    divergent = await client.post(
        "/api/v1/public-api/leads",
        headers=headers,
        json={**payload, "name": "Payload divergente"},
    )
    assert divergent.status_code == 409
    assert divergent.json()["detail"]["code"] == "PUBAPI-IDEMPOTENCY-CONFLICT"

    client_count = await db_session.scalar(select(func.count(Client.id)))
    receipts = (
        await db_session.scalars(select(PublicApiMutationReceipt))
    ).all()
    assert client_count == 1
    assert len(receipts) == 1
    assert receipts[0].state == PublicApiMutationState.completed
    assert receipts[0].response_body == created.json()


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_mutable_public_api_fails_closed_when_redis_is_unavailable(
    client,
    db_session,
    monkeypatch,
):
    async def unavailable_redis():
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(
        "app.services.public_api_service.get_redis",
        unavailable_redis,
    )
    token_response = await client.post(
        "/api/v1/integrations/public-api-tokens",
        json={
            "label": "Leitura e escrita",
            "scopes": ["contacts:read", "leads:write"],
            "rateLimitPerMinute": 120,
        },
    )
    assert token_response.status_code == 201, token_response.text
    headers = {"X-KLM-API-Key": token_response.json()["plainTextToken"]}

    readable = await client.get("/api/v1/public-api/contacts", headers=headers)
    assert readable.status_code == 200, readable.text

    blocked = await client.post(
        "/api/v1/public-api/leads",
        headers={**headers, "Idempotency-Key": "redis-down-001"},
        json={
            "name": "Não deve persistir",
            "demandType": "Consulta",
            "practiceArea": "trabalhista",
        },
    )
    assert blocked.status_code == 503
    assert blocked.json()["detail"]["code"] == "PUBAPI-RATE-LIMITER-UNAVAILABLE"
    assert await db_session.scalar(select(func.count(Client.id))) == 0
    assert await db_session.scalar(select(func.count(PublicApiMutationReceipt.id))) == 0


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_external_timeout_is_marked_uncertain_and_never_resent(
    db_session,
    auth_context,
    monkeypatch,
):
    cipher = WhatsAppSecretCipher()
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn_public_uncertain",
        phone_number="5511999990001",
        access_token_encrypted=cipher.encrypt("meta-public-token"),
        status=WaConnectionStatus.connected,
        connected_at=datetime.now(UTC),
    )
    db_session.add(connection)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511988880001",
        name="Entrega incerta",
    )
    template = WhatsAppMetaTemplate(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        name="template_uncertain",
        language="pt_BR",
        status=MetaTemplateStatus.approved,
        components=[],
        raw_payload={},
    )
    token = WorkspacePublicApiToken(
        workspace_id=auth_context.workspace_id,
        created_by_id=auth_context.user_id,
        label="Token entrega incerta",
        token_prefix="klm_uncertain_test",
        token_hash="uncertain-test-hash",
        scopes=["templates:send"],
        rate_limit_per_minute=120,
        is_active=True,
    )
    db_session.add_all((contact, template, token))
    await db_session.commit()

    service = PublicApiService(db_session)
    service.meta_client.send_template_message = AsyncMock(
        side_effect=TimeoutError("provider outcome unknown")
    )
    monkeypatch.setattr(
        "app.services.public_api_service.external_delivery_allowed",
        lambda: True,
    )
    payload = PublicApiTemplateSendRequest(
        meta_template_id=template.id,
        contact_id=contact.id,
    )
    call = {
        "token": token,
        "scope": "templates:send",
        "idempotency_key": "template-uncertain-001",
        "payload": payload,
        "path": "/api/v1/public-api/templates/send",
        "method": "POST",
        "ip_address": "127.0.0.1",
        "user_agent": "pytest",
    }

    with pytest.raises(HTTPException) as first_error:
        await service.execute_send_template_mutation(**call)
    with pytest.raises(HTTPException) as replay_error:
        await service.execute_send_template_mutation(**call)

    assert first_error.value.status_code == 503
    assert replay_error.value.status_code == 503
    assert service.meta_client.send_template_message.await_count == 1
    receipt = await db_session.scalar(select(PublicApiMutationReceipt))
    message = await db_session.scalar(select(WhatsAppMessage))
    assert receipt is not None
    assert receipt.state == PublicApiMutationState.uncertain
    assert receipt.outbox_payload["pendingMessageId"] == str(message.id)
    assert receipt.uncertain_at is not None
    assert message.status == WaMessageStatus.pending


@pytest.mark.asyncio
async def test_public_api_applies_subscription_read_write_policy_after_token_resolution(
    client,
    db_session,
    auth_context,
    active_subscription,
    monkeypatch,
):
    fake_redis = _FakeRedis()
    monkeypatch.setattr(
        "app.services.public_api_service.get_redis",
        lambda: _async_value(fake_redis),
    )
    token_response = await client.post(
        "/api/v1/integrations/public-api-tokens",
        json={
            "label": "Subscription gated",
            "scopes": ["contacts:read", "leads:write"],
            "rateLimitPerMinute": 120,
        },
    )
    assert token_response.status_code == 201, token_response.text
    headers = {"X-KLM-API-Key": token_response.json()["plainTextToken"]}
    _, access_state = await _seed_subscription_access(
        db_session,
        auth_context.workspace_id,
        access_status=SubscriptionAccessStatus.past_due,
        now=datetime.now(UTC),
        subscription=active_subscription,
    )

    readable = await client.get("/api/v1/public-api/contacts", headers=headers)
    blocked_write = await client.post(
        "/api/v1/public-api/leads",
        headers={**headers, "Idempotency-Key": "blocked-by-subscription"},
        json={
            "name": "Must not be created",
            "demandType": "Consulta",
            "practiceArea": "trabalhista",
        },
    )

    assert readable.status_code == 200, readable.text
    assert blocked_write.status_code == 403, blocked_write.text
    assert blocked_write.json()["detail"] == {
        "code": "SUBSCRIPTION_ACCESS_DENIED",
        "message": "A assinatura do workspace não permite esta operação.",
        "operation": "write",
        "state": "past_due",
        "reasonCode": "subscription_past_due_read_only",
    }
    assert await db_session.scalar(select(func.count(Client.id))) == 0

    access_state.status = SubscriptionAccessStatus.suspended
    await db_session.commit()
    blocked_read = await client.get("/api/v1/public-api/contacts", headers=headers)
    assert blocked_read.status_code == 403, blocked_read.text
    assert blocked_read.json()["detail"]["operation"] == "read"
    assert blocked_read.json()["detail"]["state"] == "suspended"


@pytest.mark.asyncio
async def test_public_api_expired_trial_transition_survives_denied_request_rollback(
    client,
    db_session,
    auth_context,
    active_subscription,
    monkeypatch,
):
    fake_redis = _FakeRedis()
    monkeypatch.setattr(
        "app.services.public_api_service.get_redis",
        lambda: _async_value(fake_redis),
    )
    token_response = await client.post(
        "/api/v1/integrations/public-api-tokens",
        json={
            "label": "Expired trial",
            "scopes": ["contacts:read"],
            "rateLimitPerMinute": 120,
        },
    )
    assert token_response.status_code == 201, token_response.text
    headers = {"X-KLM-API-Key": token_response.json()["plainTextToken"]}
    now = datetime.now(UTC)
    subscription, access_state = await _seed_subscription_access(
        db_session,
        auth_context.workspace_id,
        access_status=SubscriptionAccessStatus.trial,
        now=now,
        trial_end=now - timedelta(days=1),
        subscription=active_subscription,
    )
    subscription_id = subscription.id
    access_state_id = access_state.id

    async def request_scoped_db():
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    fastapi_app.dependency_overrides[get_db] = request_scoped_db
    rejected = await client.get("/api/v1/public-api/contacts", headers=headers)

    assert rejected.status_code == 403, rejected.text
    assert rejected.json()["detail"]["reasonCode"] == "subscription_suspended"
    db_session.expire_all()
    persisted_state = await db_session.get(SubscriptionAccessState, access_state_id)
    persisted_subscription = await db_session.get(Subscription, subscription_id)
    transition = await db_session.scalar(select(SubscriptionAccessTransition))
    assert persisted_state is not None
    assert persisted_state.status == SubscriptionAccessStatus.suspended
    assert persisted_subscription is not None
    assert persisted_subscription.status == SubscriptionStatus.trialing
    assert transition is not None
    assert transition.reason == "subscription_trial_expired"


@pytest.mark.asyncio
async def test_public_api_expired_grace_transition_survives_denied_mutation_rollback(
    client,
    db_session,
    auth_context,
    active_subscription,
    monkeypatch,
):
    fake_redis = _FakeRedis()
    monkeypatch.setattr(
        "app.services.public_api_service.get_redis",
        lambda: _async_value(fake_redis),
    )
    token_response = await client.post(
        "/api/v1/integrations/public-api-tokens",
        json={
            "label": "Expired grace",
            "scopes": ["leads:write"],
            "rateLimitPerMinute": 120,
        },
    )
    assert token_response.status_code == 201, token_response.text
    headers = {
        "X-KLM-API-Key": token_response.json()["plainTextToken"],
        "Idempotency-Key": "expired-grace-write",
    }
    now = datetime.now(UTC)
    subscription, access_state = await _seed_subscription_access(
        db_session,
        auth_context.workspace_id,
        access_status=SubscriptionAccessStatus.grace,
        now=now,
        grace_end=now - timedelta(days=1),
        subscription=active_subscription,
    )
    subscription_id = subscription.id
    access_state_id = access_state.id

    async def request_scoped_db():
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    fastapi_app.dependency_overrides[get_db] = request_scoped_db
    rejected = await client.post(
        "/api/v1/public-api/leads",
        headers=headers,
        json={
            "name": "Must not be created",
            "demandType": "Consulta",
            "practiceArea": "trabalhista",
        },
    )

    assert rejected.status_code == 403, rejected.text
    assert rejected.json()["detail"]["reasonCode"] == "subscription_past_due_read_only"
    db_session.expire_all()
    persisted_state = await db_session.get(SubscriptionAccessState, access_state_id)
    persisted_subscription = await db_session.get(Subscription, subscription_id)
    transition = await db_session.scalar(
        select(SubscriptionAccessTransition).where(
            SubscriptionAccessTransition.access_state_id == access_state_id
        )
    )
    assert persisted_state is not None
    assert persisted_state.status == SubscriptionAccessStatus.past_due
    assert persisted_subscription is not None
    assert persisted_subscription.status == SubscriptionStatus.past_due
    assert transition is not None
    assert transition.reason == "subscription_grace_expired"
    assert await db_session.scalar(select(func.count(Client.id))) == 0


@pytest.mark.asyncio
async def test_public_api_period_end_cancellation_survives_denied_mutation_rollback(
    client,
    db_session,
    auth_context,
    active_subscription,
    monkeypatch,
):
    fake_redis = _FakeRedis()
    monkeypatch.setattr(
        "app.services.public_api_service.get_redis",
        lambda: _async_value(fake_redis),
    )
    token_response = await client.post(
        "/api/v1/integrations/public-api-tokens",
        json={
            "label": "Period-end cancellation",
            "scopes": ["leads:write"],
            "rateLimitPerMinute": 120,
        },
    )
    assert token_response.status_code == 201, token_response.text
    headers = {
        "X-KLM-API-Key": token_response.json()["plainTextToken"],
        "Idempotency-Key": "period-end-canceled-write",
    }
    now = datetime.now(UTC)
    period_end = now - timedelta(seconds=1)
    subscription, access_state = await _seed_subscription_access(
        db_session,
        auth_context.workspace_id,
        access_status=SubscriptionAccessStatus.active,
        now=now,
        subscription=active_subscription,
    )
    subscription.current_period_end = period_end
    subscription.cancel_at_period_end = True
    subscription.amount_cents = 69_700
    await db_session.commit()
    subscription_id = subscription.id
    access_state_id = access_state.id

    async def request_scoped_db():
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    fastapi_app.dependency_overrides[get_db] = request_scoped_db
    rejected = await client.post(
        "/api/v1/public-api/leads",
        headers=headers,
        json={
            "name": "Must not be created",
            "demandType": "Consulta",
            "practiceArea": "trabalhista",
        },
    )

    assert rejected.status_code == 403, rejected.text
    assert rejected.json()["detail"]["reasonCode"] == (
        "subscription_canceled_read_only"
    )
    db_session.expire_all()
    persisted_state = await db_session.get(SubscriptionAccessState, access_state_id)
    persisted_subscription = await db_session.get(Subscription, subscription_id)
    transition = await db_session.scalar(
        select(SubscriptionAccessTransition).where(
            SubscriptionAccessTransition.access_state_id == access_state_id
        )
    )
    assert persisted_state is not None
    assert persisted_state.status == SubscriptionAccessStatus.canceled
    assert persisted_subscription is not None
    assert persisted_subscription.status == SubscriptionStatus.cancelled
    assert persisted_subscription.cancel_at_period_end is False
    assert persisted_subscription.cancelled_at is not None
    assert persisted_subscription.cancelled_at.replace(tzinfo=UTC) == period_end
    assert transition is not None
    assert transition.reason == "subscription_period_end_canceled"
    assert transition.idempotency_key == (
        f"subscription-policy:period-end-canceled:{subscription_id}:"
        f"{period_end.isoformat()}"
    )
    assert await db_session.scalar(select(func.count(Client.id))) == 0
