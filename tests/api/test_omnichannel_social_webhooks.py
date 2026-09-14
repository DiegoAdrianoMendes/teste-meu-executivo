from __future__ import annotations

import hashlib
import hmac
import json
import time
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.klm_operations import OmnichannelThread
from app.models.omnichannel_v6 import (
    OmnichannelActionIntent,
    OmnichannelAutomationRun,
    OmnichannelChannelEvent,
    OmnichannelEndpoint,
    OmnichannelIdentity,
    OmnichannelPublicIngressReceipt,
)

TEST_CIPHER_KEY = "test-only-social-cipher-key-7Hf9pQ2wX4mK8cR6vN3z"
META_SECRET = "meta-social-app-secret-at-least-32-characters"
META_VERIFY_TOKEN = "meta-social-verify-token"

pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.fixture(autouse=True)
def _configure_runtime(monkeypatch):
    monkeypatch.setattr(settings, "INTEGRATION_SECRET_ENCRYPTION_KEY", TEST_CIPHER_KEY)
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "production")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", True)


def _body(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()


def _meta_headers(body: bytes) -> dict[str, str]:
    signature = hmac.new(META_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": f"sha256={signature}",
    }


def _tiktok_headers(body: bytes, secret: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "TikTok-Signature": f"t={timestamp},s={signature}",
    }


async def _connection(
    client,
    *,
    provider: str,
    channel: str,
    connection_key: str,
    secret: str,
    verify_token: str | None = None,
    external_account_id: str | None = None,
) -> tuple[dict, dict]:
    created = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json={
            "channel": channel,
            "provider": provider,
            "connectionKey": connection_key,
            "displayName": f"Canal {provider}",
            "externalAccountId": external_account_id or f"account:{provider}",
            "transportMode": "official_api",
            "configuration": {"mappingVersion": "v7-social"},
        },
    )
    assert created.status_code == 201, created.text
    credential = {
        "expectedVersion": 0,
        "action": "save",
        "signingSecret": secret,
        "timestampToleranceSeconds": 300,
    }
    if verify_token:
        credential["verifyToken"] = verify_token
    configured = await client.put(
        f"/api/v1/klm/omnichannel/connections/{created.json()['id']}/webhook-credential",
        json=credential,
    )
    assert configured.status_code == 200, configured.text
    return created.json(), configured.json()


def _instagram_batch(*, comment_text: str = "Preciso de ajuda com meu plano") -> dict:
    return {
        "object": "instagram",
        "entry": [
            {
                "id": "ig-business-001",
                "time": 1_784_564_800,
                "messaging": [
                    {
                        "sender": {"id": "ig-user-001", "username": "ana.saude"},
                        "recipient": {"id": "ig-business-001"},
                        "timestamp": 1_784_564_801_000,
                        "message": {"mid": "ig-mid-001", "text": "Meu tratamento foi negado"},
                    }
                ],
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "id": "ig-comment-001",
                            "text": comment_text,
                            "from": {"id": "ig-user-001", "username": "ana.saude"},
                            "media": {"id": "ig-media-001"},
                            "permalink_url": "https://www.instagram.com/p/example/",
                            "created_time": 1_784_564_802,
                        },
                    }
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_instagram_batch_is_one_native_thread_and_materializes_comment_flow(
    client, db_session, auth_context
):
    connection, configured = await _connection(
        client,
        provider="meta_instagram",
        channel="instagram",
        connection_key="instagram:principal",
        secret=META_SECRET,
        verify_token=META_VERIFY_TOKEN,
        external_account_id="ig-business-001",
    )
    assert configured["webhookPath"].startswith(
        "/api/v1/webhooks/omnichannel/social/"
    )
    challenge = await client.get(
        configured["webhookPath"],
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": META_VERIFY_TOKEN,
            "hub.challenge": "ig-challenge",
        },
    )
    assert challenge.status_code == 200, challenge.text
    assert challenge.text == "ig-challenge"

    rule = await client.post(
        "/api/v1/klm/omnichannel/automation-rules",
        json={
            "name": "Comentário sobre negativa",
            "slug": "comentario-negativa",
            "channel": "instagram",
            "triggerType": "keyword_comment",
            "status": "active",
            "triggerConfig": {"keywords": ["plano", "tratamento"]},
            "actions": [
                {
                    "type": "public_reply",
                    "body": "Vamos entender o seu caso. Enviamos uma mensagem privada.",
                    "delaySeconds": 3,
                }
            ],
        },
    )
    assert rule.status_code == 201, rule.text

    body = _body(_instagram_batch())
    first = await client.post(
        configured["webhookPath"], content=body, headers=_meta_headers(body)
    )
    replay = await client.post(
        configured["webhookPath"], content=body, headers=_meta_headers(body)
    )
    assert first.status_code == 200, first.text
    assert first.json()["acceptedCount"] == 2
    assert first.json()["ignoredCount"] == 0
    assert first.json()["deduplicatedCount"] == 0
    assert replay.status_code == 200, replay.text
    assert replay.json()["deduplicatedCount"] == 2
    assert {item["threadId"] for item in first.json()["items"]} == {
        first.json()["items"][0]["threadId"]
    }
    assert {item["identityId"] for item in first.json()["items"]} == {
        first.json()["items"][0]["identityId"]
    }

    assert await db_session.scalar(select(func.count()).select_from(OmnichannelThread)) == 1
    assert await db_session.scalar(select(func.count()).select_from(OmnichannelIdentity)) == 1
    assert await db_session.scalar(select(func.count()).select_from(OmnichannelEndpoint)) == 1
    assert await db_session.scalar(select(func.count()).select_from(OmnichannelChannelEvent)) == 2
    assert (
        await db_session.scalar(
            select(func.count()).select_from(OmnichannelPublicIngressReceipt)
        )
        == 2
    )
    assert await db_session.scalar(select(func.count()).select_from(OmnichannelAutomationRun)) == 2
    intent = await db_session.scalar(select(OmnichannelActionIntent))
    assert intent is not None
    assert str(intent.connection_id) == connection["id"]
    assert intent.action_type == "public_reply"
    assert intent.state == "blocked_external"
    assert intent.provider_receipt["externalRequests"] == 0

    center = await client.get(
        "/api/v1/klm/omnichannel/webhook-center",
        params={"provider": "meta_instagram"},
    )
    assert center.status_code == 200, center.text
    center_payload = center.json()
    assert center_payload["receiptTotalCount"] == 2
    assert center_payload["processedCount"] == 2
    assert center_payload["blockedCount"] == 0
    assert center_payload["failedCount"] == 0
    assert center_payload["connections"][0]["setupProgress"] == 100
    processing_step = next(
        step
        for step in center_payload["connections"][0]["setupSteps"]
        if step["key"] == "processing"
    )
    assert processing_step["detail"] == (
        "Conversa social e identidade vinculadas sem ação externa nesta fase."
    )

    processed = await client.get(
        "/api/v1/klm/omnichannel/webhook-center",
        params={"provider": "meta_instagram", "state": "processed"},
    )
    assert processed.status_code == 200, processed.text
    assert processed.json()["receiptWindowCount"] == 2
    assert processed.json()["receiptMatchCount"] == 2
    assert {
        item["processingState"] for item in processed.json()["receipts"]
    } == {"processed"}
    assert {
        item["threadId"] for item in processed.json()["receipts"]
    } == {first.json()["items"][0]["threadId"]}
    assert {
        item["identityId"] for item in processed.json()["receipts"]
    } == {first.json()["items"][0]["identityId"]}

    lead_connection, _lead_credential = await _connection(
        client,
        provider="meta_ads",
        channel="facebook",
        connection_key="meta_ads:collision-check",
        secret="meta-lead-secret-at-least-32-characters",
        verify_token="meta-lead-verify-token",
    )
    collision_event_id = "lead-event-with-channel-projection"
    db_session.add_all(
        (
            OmnichannelPublicIngressReceipt(
                workspace_id=auth_context.workspace_id,
                connection_id=UUID(lead_connection["id"]),
                provider="meta_ads",
                connection_key="meta_ads:collision-check",
                external_event_id=collision_event_id,
                external_lead_id="lead-collision-001",
                payload_sha256="a" * 64,
                signature_sha256="b" * 64,
                signature_scheme="hmac_sha256",
                request_metadata={"fixture": "non-social-collision"},
            ),
            OmnichannelChannelEvent(
                workspace_id=auth_context.workspace_id,
                identity_id=UUID(first.json()["items"][0]["identityId"]),
                thread_id=UUID(first.json()["items"][0]["threadId"]),
                connection_id=UUID(lead_connection["id"]),
                provider="meta_ads",
                connection_key="meta_ads:collision-check",
                event_type="lead.received",
                external_event_id=collision_event_id,
                payload_sha256="c" * 64,
                correlation={},
                event_payload={},
                event_metadata={"fixture": "must-not-project-social-state"},
            ),
        )
    )
    await db_session.commit()

    non_social = await client.get(
        "/api/v1/klm/omnichannel/webhook-center",
        params={"provider": "meta_ads"},
    )
    assert non_social.status_code == 200, non_social.text
    assert non_social.json()["processedCount"] == 0
    assert non_social.json()["receipts"][0]["processingState"] == "received"
    assert non_social.json()["receipts"][0]["threadId"] is None
    assert non_social.json()["receipts"][0]["identityId"] is None


@pytest.mark.asyncio
async def test_changed_meta_event_bytes_are_rejected_without_overwriting_evidence(client):
    _connection_data, configured = await _connection(
        client,
        provider="meta_instagram",
        channel="instagram",
        connection_key="instagram:evidence",
        secret=META_SECRET,
        verify_token=META_VERIFY_TOKEN,
        external_account_id="ig-business-001",
    )
    original = _body(_instagram_batch())
    changed = _body(_instagram_batch(comment_text="Conteúdo alterado"))
    accepted = await client.post(
        configured["webhookPath"], content=original, headers=_meta_headers(original)
    )
    conflict = await client.post(
        configured["webhookPath"], content=changed, headers=_meta_headers(changed)
    )
    assert accepted.status_code == 200, accepted.text
    assert conflict.status_code == 409, conflict.text


@pytest.mark.asyncio
async def test_tiktok_signed_event_joins_verified_phone_identity_across_channels(
    client, db_session
):
    phone = "+5511987654321"
    seeded = await client.post(
        "/api/v1/klm/omnichannel/events/ingest",
        json={
            "provider": "meta_cloud",
            "connectionKey": "whatsapp:fixture",
            "channel": "whatsapp",
            "eventType": "message.received",
            "externalEventId": "wa-strong-identity-001",
            "externalThreadId": "wa-thread-001",
            "externalUserId": phone,
            "displayName": "Maria Oliveira",
            "address": phone,
            "canonicalPhone": phone,
            "direction": "inbound",
            "authorType": "client",
            "body": "Olá",
        },
    )
    assert seeded.status_code == 201, seeded.text
    identity_id = seeded.json()["identity"]["id"]
    thread_id = seeded.json()["thread"]["id"]

    secret = "tiktok-business-secret-at-least-32-characters"
    _connection_data, configured = await _connection(
        client,
        provider="tiktok_business",
        channel="tiktok",
        connection_key="tiktok:principal",
        secret=secret,
    )
    payload = {
        "event": "business_message_received",
        "event_id": "tt-event-001",
        "create_time": int(time.time()),
        "user_openid": "tt-open-user-001",
        "content": json.dumps(
            {
                "message_id": "tt-message-001",
                "conversation_id": "tt-conversation-001",
                "text": "Vi o anúncio e preciso de ajuda",
                "phone": phone,
                "username": "maria.saude",
                "ad_id": "tt-ad-001",
            }
        ),
    }
    body = _body(payload)
    received = await client.post(
        configured["webhookPath"],
        content=body,
        headers=_tiktok_headers(body, secret),
    )
    assert received.status_code == 200, received.text
    assert received.json()["acceptedCount"] == 1
    item = received.json()["items"][0]
    assert item["identityId"] == identity_id
    assert item["threadId"] == thread_id
    assert await db_session.scalar(select(func.count()).select_from(OmnichannelIdentity)) == 1
    assert await db_session.scalar(select(func.count()).select_from(OmnichannelThread)) == 1
    assert await db_session.scalar(select(func.count()).select_from(OmnichannelEndpoint)) == 2


@pytest.mark.asyncio
async def test_unknown_tiktok_event_is_acknowledged_without_creating_customer_data(client):
    secret = "tiktok-ignore-secret-at-least-32-characters"
    _connection_data, configured = await _connection(
        client,
        provider="tiktok_business",
        channel="tiktok",
        connection_key="tiktok:ignored",
        secret=secret,
    )
    body = _body(
        {
            "event": "authorization.updated",
            "event_id": "tt-ignored-001",
            "create_time": int(time.time()),
            "content": {"status": "active"},
        }
    )
    response = await client.post(
        configured["webhookPath"],
        content=body,
        headers=_tiktok_headers(body, secret),
    )
    assert response.status_code == 200, response.text
    assert response.json()["inspectedCount"] == 1
    assert response.json()["acceptedCount"] == 0
    assert response.json()["ignoredCount"] == 1
