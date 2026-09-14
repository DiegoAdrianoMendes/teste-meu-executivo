from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.client import Client
from app.models.matter import Matter
from app.models.omnichannel_v6 import (
    LeadCaptureReceipt,
    LeadHydrationEvent,
    LeadHydrationJob,
    OmnichannelPublicIngressReceipt,
    OmnichannelWebhookCredential,
)

TEST_CIPHER_KEY = "test-only-omnichannel-cipher-key-7Hf9pQ2wX4mK8cR6vN3z"
META_SECRET = "meta-app-secret-2026-at-least-32-characters"
META_VERIFY_TOKEN = "meta-verify-token-2026"

pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.fixture(autouse=True)
def _configure_cipher(monkeypatch):
    monkeypatch.setattr(settings, "INTEGRATION_SECRET_ENCRYPTION_KEY", TEST_CIPHER_KEY)


def _open_external_gate(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "production")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", True)


def _close_external_gate(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)


async def _configure_connection(
    client,
    *,
    provider: str,
    connection_key: str,
    signing_secret: str,
    verify_token: str | None = None,
) -> tuple[dict, dict]:
    created = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json={
            "channel": "whatsapp",
            "provider": provider,
            "connectionKey": connection_key,
            "displayName": f"Fonte {provider}",
            "transportMode": "webhook",
            "configuration": {"mappingVersion": "v7"},
        },
    )
    assert created.status_code == 201, created.text
    payload = {
        "expectedVersion": 0,
        "action": "save",
        "signingSecret": signing_secret,
        "timestampToleranceSeconds": 300,
    }
    if verify_token is not None:
        payload["verifyToken"] = verify_token
    configured = await client.put(
        f"/api/v1/klm/omnichannel/connections/{created.json()['id']}/webhook-credential",
        json=payload,
    )
    assert configured.status_code == 200, configured.text
    return created.json(), configured.json()


@pytest.mark.asyncio
async def test_webhook_credentials_reject_weak_secrets_before_persistence(client):
    created = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json={
            "channel": "webchat",
            "provider": "site",
            "connectionKey": "site:weak-secret-check",
            "displayName": "Site weak secret check",
            "transportMode": "webhook",
            "configuration": {},
        },
    )
    assert created.status_code == 201, created.text

    rejected = await client.put(
        f"/api/v1/klm/omnichannel/connections/{created.json()['id']}/webhook-credential",
        json={
            "expectedVersion": 0,
            "action": "save",
            "signingSecret": "too-short",
        },
    )

    assert rejected.status_code == 422, rejected.text
    assert "signingSecret" in rejected.text


def _meta_payload(*, lead_id: str = "meta-lead-001", insurer: str = "Plano Exemplo") -> dict:
    return {
        "object": "page",
        "entry": [
            {
                "id": "page-001",
                "changes": [
                    {
                        "field": "leadgen",
                        "value": {
                            "leadgen_id": lead_id,
                            "form_id": "form-saude-001",
                            "campaign_name": "Negativa urgente",
                            "adset_name": "Oncologia",
                            "ad_name": "Medicamento negado",
                            "field_data": [
                                {"name": "full_name", "values": ["Bruna Santos"]},
                                {"name": "phone_number", "values": ["+5511988887777"]},
                                {
                                    "name": "plano_de_saude",
                                    "values": [insurer],
                                },
                                {
                                    "name": "medicamento",
                                    "values": ["Pembrolizumabe"],
                                },
                            ],
                        },
                    }
                ],
            }
        ],
    }


def _json_body(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _meta_headers(body: bytes, *, secret: str = META_SECRET) -> dict[str, str]:
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": f"sha256={signature}",
    }


@pytest.mark.asyncio
async def test_meta_challenge_signed_ingress_and_exact_replay_are_native_and_idempotent(
    client, db_session, monkeypatch
):
    connection, configured = await _configure_connection(
        client,
        provider="meta_ads",
        connection_key="meta-leads:principal",
        signing_secret=META_SECRET,
        verify_token=META_VERIFY_TOKEN,
    )
    assert configured["signingSecretConfigured"] is True
    assert configured["verifyTokenConfigured"] is True
    assert META_SECRET not in json.dumps(configured)
    assert META_VERIFY_TOKEN not in json.dumps(configured)

    _open_external_gate(monkeypatch)
    path = configured["webhookPath"]
    challenge = await client.get(
        path,
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": META_VERIFY_TOKEN,
            "hub.challenge": "challenge-123",
        },
    )
    assert challenge.status_code == 200, challenge.text
    assert challenge.text == "challenge-123"

    body = _json_body(_meta_payload())
    first = await client.post(path, content=body, headers=_meta_headers(body))
    replay = await client.post(path, content=body, headers=_meta_headers(body))
    changed_body = _json_body(_meta_payload(insurer="Outro Plano"))
    conflict = await client.post(
        path, content=changed_body, headers=_meta_headers(changed_body)
    )

    assert first.status_code == 200, first.text
    assert first.json()["status"] == "accepted"
    assert first.json()["deduplicated"] is False
    assert replay.status_code == 200, replay.text
    assert replay.json()["deduplicated"] is True
    assert replay.json()["ingressReceiptId"] == first.json()["ingressReceiptId"]
    assert conflict.status_code == 409, conflict.text

    assert await db_session.scalar(select(func.count()).select_from(Client)) == 1
    assert await db_session.scalar(select(func.count()).select_from(Matter)) == 1
    assert await db_session.scalar(select(func.count()).select_from(LeadCaptureReceipt)) == 1
    assert (
        await db_session.scalar(
            select(func.count()).select_from(OmnichannelPublicIngressReceipt)
        )
        == 1
    )
    credential = await db_session.scalar(
        select(OmnichannelWebhookCredential).where(
            OmnichannelWebhookCredential.connection_id == connection["id"]
        )
    )
    assert credential is not None
    assert credential.signing_secret_encrypted != META_SECRET
    assert credential.verify_token_encrypted != META_VERIFY_TOKEN
    assert credential.last_verified_at is not None
    receipt = await db_session.scalar(select(OmnichannelPublicIngressReceipt))
    assert receipt is not None
    assert "signature" not in json.dumps(receipt.request_metadata).lower()
    assert receipt.signature_sha256 != hashlib.sha256(META_SECRET.encode()).hexdigest()


@pytest.mark.asyncio
async def test_webhook_center_exposes_signed_processing_evidence_without_secrets(
    client, monkeypatch
):
    connection, configured = await _configure_connection(
        client,
        provider="meta_ads",
        connection_key="meta-leads:webhook-center",
        signing_secret=META_SECRET,
        verify_token=META_VERIFY_TOKEN,
    )
    _open_external_gate(monkeypatch)
    body = _json_body(_meta_payload(lead_id="meta-center-001"))
    first = await client.post(
        configured["webhookPath"], content=body, headers=_meta_headers(body)
    )
    replay = await client.post(
        configured["webhookPath"], content=body, headers=_meta_headers(body)
    )
    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert replay.json()["deduplicated"] is True

    center = await client.get(
        "/api/v1/klm/omnichannel/webhook-center",
        params={"provider": "meta_ads", "limit": 50},
    )
    assert center.status_code == 200, center.text
    payload = center.json()
    assert payload["connectionCount"] == 1
    assert payload["configuredConnectionCount"] == 1
    assert payload["activeCredentialCount"] == 1
    assert payload["receiptWindowCount"] == 1
    assert payload["receiptTotalCount"] == 1
    assert payload["receiptMatchCount"] == 1
    assert payload["idempotencyEnforced"] is True
    assert payload["connections"][0]["connection"]["id"] == connection["id"]
    assert payload["connections"][0]["setupProgress"] == 100
    assert all(step["completed"] for step in payload["connections"][0]["setupSteps"])

    receipt = payload["receipts"][0]
    assert receipt["id"] == first.json()["ingressReceiptId"]
    assert receipt["processingState"] == "completed"
    assert receipt["threadId"]
    assert receipt["matterId"]
    assert receipt["identityId"]
    assert receipt["payloadSha256"] == hashlib.sha256(body).hexdigest()
    serialized = json.dumps(payload)
    assert META_SECRET not in serialized
    assert META_VERIFY_TOKEN not in serialized
    assert "signatureSha256" not in serialized

    completed = await client.get(
        "/api/v1/klm/omnichannel/webhook-center",
        params={"provider": "meta_ads", "state": "completed"},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["receiptWindowCount"] == 1
    assert completed.json()["receiptTotalCount"] == 1
    assert completed.json()["receiptMatchCount"] == 1

    missing = await client.get(
        "/api/v1/klm/omnichannel/webhook-center",
        params={"provider": "meta_ads", "state": "failed"},
    )
    assert missing.status_code == 200, missing.text
    assert missing.json()["receiptWindowCount"] == 0
    assert missing.json()["receiptTotalCount"] == 1
    assert missing.json()["receiptMatchCount"] == 0
    assert missing.json()["processedCount"] == 1
    assert missing.json()["connections"][0]["setupProgress"] == 100


@pytest.mark.asyncio
async def test_meta_locator_only_callback_is_accepted_and_queued_without_fake_case(
    client, db_session, monkeypatch
):
    _connection, configured = await _configure_connection(
        client,
        provider="meta_ads",
        connection_key="meta-leads:locator-only",
        signing_secret=META_SECRET,
        verify_token=META_VERIFY_TOKEN,
    )
    _open_external_gate(monkeypatch)
    payload = _meta_payload(lead_id="meta-locator-001")
    payload["entry"][0]["changes"][0]["value"].pop("field_data")
    body = _json_body(payload)

    first = await client.post(
        configured["webhookPath"], content=body, headers=_meta_headers(body)
    )
    replay = await client.post(
        configured["webhookPath"], content=body, headers=_meta_headers(body)
    )

    assert first.status_code == 200, first.text
    assert first.json()["state"] == "awaiting_hydration"
    assert first.json()["leadCaptureReceiptId"] is None
    assert first.json()["hydrationJobId"]
    assert first.json()["items"] == [
        {
            "externalLeadId": "meta-locator-001",
            "state": "awaiting_hydration",
            "leadCaptureReceiptId": None,
            "hydrationJobId": first.json()["hydrationJobId"],
            "deduplicated": False,
        }
    ]
    assert replay.status_code == 200, replay.text
    assert replay.json()["deduplicated"] is True
    assert replay.json()["hydrationJobId"] == first.json()["hydrationJobId"]
    assert await db_session.scalar(select(func.count()).select_from(Client)) == 0
    assert await db_session.scalar(select(func.count()).select_from(Matter)) == 0
    assert await db_session.scalar(select(func.count()).select_from(LeadCaptureReceipt)) == 0
    assert await db_session.scalar(select(func.count()).select_from(LeadHydrationJob)) == 1
    assert await db_session.scalar(select(func.count()).select_from(LeadHydrationEvent)) == 1
    job = await db_session.scalar(select(LeadHydrationJob))
    assert job is not None
    assert job.locator_payload == {
        "externalLeadId": "meta-locator-001",
        "externalFormId": "form-saude-001",
    }
    assert "field_data" not in json.dumps(job.locator_payload)

    job.state = "blocked_external"
    job.last_error_code = "external_gate_closed"
    await db_session.flush()
    blocked = await client.get(
        "/api/v1/klm/omnichannel/webhook-center",
        params={"provider": "meta_ads", "state": "blocked_external"},
    )
    assert blocked.status_code == 200, blocked.text
    blocked_payload = blocked.json()
    assert blocked_payload["receiptMatchCount"] == 1
    assert blocked_payload["blockedCount"] == 1
    assert blocked_payload["failedCount"] == 0
    assert blocked_payload["processedCount"] == 0
    assert blocked_payload["connections"][0]["blockedCount"] == 1
    assert blocked_payload["receipts"][0]["processingState"] == "blocked_external"


@pytest.mark.asyncio
async def test_meta_batched_locator_callback_creates_one_job_per_lead(
    client, db_session, monkeypatch
):
    _connection, configured = await _configure_connection(
        client,
        provider="meta_ads",
        connection_key="meta-leads:batch",
        signing_secret=META_SECRET,
        verify_token=META_VERIFY_TOKEN,
    )
    _open_external_gate(monkeypatch)
    first_change = _meta_payload(lead_id="meta-batch-001")["entry"][0]["changes"][0]
    second_change = _meta_payload(lead_id="meta-batch-002")["entry"][0]["changes"][0]
    first_change["value"].pop("field_data")
    second_change["value"].pop("field_data")
    body = _json_body(
        {
            "object": "page",
            "entry": [{"id": "page-001", "changes": [first_change, second_change]}],
        }
    )
    response = await client.post(
        configured["webhookPath"], content=body, headers=_meta_headers(body)
    )

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "awaiting_hydration"
    assert response.json()["itemCount"] == 2
    assert response.json()["externalEventId"].startswith("batch:")
    assert {item["externalLeadId"] for item in response.json()["items"]} == {
        "meta-batch-001",
        "meta-batch-002",
    }
    assert await db_session.scalar(select(func.count()).select_from(LeadHydrationJob)) == 2


@pytest.mark.asyncio
async def test_public_gate_is_checked_before_body_or_signature(client, monkeypatch):
    _close_external_gate(monkeypatch)
    response = await client.post(
        "/api/v1/webhooks/omnichannel/workspace-teste/meta_ads/does-not-exist",
        content=b"not-json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "KLM-EXT-001"


@pytest.mark.asyncio
async def test_webhook_rotation_preserves_timestamp_tolerance_when_omitted(client):
    connection, configured = await _configure_connection(
        client,
        provider="tiktok_ads",
        connection_key="tiktok-leads:rotation",
        signing_secret="original-secret-for-webhook-rotation-2026",
    )
    configured_update = await client.put(
        f"/api/v1/klm/omnichannel/connections/{connection['id']}/webhook-credential",
        json={
            "expectedVersion": configured["version"],
            "action": "save",
            "timestampToleranceSeconds": 900,
        },
    )
    assert configured_update.status_code == 200, configured_update.text
    rotated = await client.put(
        f"/api/v1/klm/omnichannel/connections/{connection['id']}/webhook-credential",
        json={
            "expectedVersion": configured_update.json()["version"],
            "action": "rotate",
            "signingSecret": "rotated-secret-for-webhook-rotation-2026",
        },
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["timestampToleranceSeconds"] == 900


@pytest.mark.asyncio
async def test_invalid_meta_signature_creates_no_ingress_receipt(
    client, db_session, monkeypatch
):
    _connection, configured = await _configure_connection(
        client,
        provider="meta_ads",
        connection_key="meta-leads:invalid-signature",
        signing_secret=META_SECRET,
        verify_token=META_VERIFY_TOKEN,
    )
    _open_external_gate(monkeypatch)
    body = _json_body(_meta_payload(lead_id="meta-invalid-001"))
    response = await client.post(
        configured["webhookPath"],
        content=body,
        headers=_meta_headers(body, secret="wrong-secret"),
    )
    assert response.status_code == 401
    assert (
        await db_session.scalar(
            select(func.count()).select_from(OmnichannelPublicIngressReceipt)
        )
        == 0
    )


@pytest.mark.asyncio
async def test_tiktok_signature_uses_timestamp_and_rejects_stale_replay(
    client, monkeypatch
):
    secret = "tiktok-client-secret-at-least-32-characters"
    _connection, configured = await _configure_connection(
        client,
        provider="tiktok_ads",
        connection_key="tiktok-leads:principal",
        signing_secret=secret,
    )
    _open_external_gate(monkeypatch)
    payload = {
        "data": {
            "lead": {
                "lead_id": "tiktok-lead-001",
                "campaign_name": "TikTok saúde",
                "field_data": [
                    {"name": "full_name", "value": "Carlos Lima"},
                    {"name": "phone_number", "value": "+5511977776666"},
                ],
            }
        }
    }
    body = _json_body(payload)
    timestamp = int(time.time())
    signature = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    accepted = await client.post(
        configured["webhookPath"],
        content=body,
        headers={
            "Content-Type": "application/json",
            "TikTok-Signature": f"t={timestamp},s={signature}",
        },
    )
    assert accepted.status_code == 200, accepted.text

    stale_timestamp = timestamp - 1000
    stale_signature = hmac.new(
        secret.encode(), f"{stale_timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    stale = await client.post(
        configured["webhookPath"],
        content=body,
        headers={
            "Content-Type": "application/json",
            "TikTok-Signature": f"t={stale_timestamp},s={stale_signature}",
        },
    )
    assert stale.status_code == 401
    assert "replay" in stale.text.lower()


@pytest.mark.asyncio
async def test_lovable_signed_form_requires_event_id_and_preserves_attribution(
    client, db_session, monkeypatch
):
    secret = "lovable-shared-secret-at-least-32-characters"
    _connection, configured = await _configure_connection(
        client,
        provider="lovable",
        connection_key="lovable-site:principal",
        signing_secret=secret,
    )
    _open_external_gate(monkeypatch)
    body = _json_body(
        {
            "submission_id": "lovable-submission-001",
            "name": "Fernanda Alves",
            "phone": "+5511966665555",
            "campaign": "Google Ads saúde",
            "gclid": "gclid-fixture",
            "medicamento": "Nivolumabe",
        }
    )
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    base_headers = {
        "Content-Type": "application/json",
        "X-KLM-Timestamp": timestamp,
        "X-KLM-Signature": f"sha256={signature}",
    }
    missing_event = await client.post(
        configured["webhookPath"], content=body, headers=base_headers
    )
    assert missing_event.status_code == 400
    accepted = await client.post(
        configured["webhookPath"],
        content=body,
        headers={**base_headers, "X-KLM-Event-ID": "lovable-event-001"},
    )
    assert accepted.status_code == 200, accepted.text
    lead_receipt = await db_session.scalar(select(LeadCaptureReceipt))
    assert lead_receipt is not None
    assert lead_receipt.result["acquisition"]["clickIdKeys"] == ["gclid"]
