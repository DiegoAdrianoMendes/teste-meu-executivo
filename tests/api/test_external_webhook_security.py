from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.stripe_webhook import _verify_stripe_signature
from app.core.config import settings
from app.db.tenant import get_workspace_context
from app.models.finance import (
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
)
from app.services.integration_secrets import protect_integration_settings

GENERIC_WEBHOOK_PATH = "/api/v1/integrations/webhooks/workspace-teste"
TEST_INTEGRATION_SECRET_KEY = "test-only-integration-key-7Hf9pQ2wX4mK8cR6vN3z"


@pytest.fixture(autouse=True)
def _configure_integration_secret_key(monkeypatch):
    monkeypatch.setattr(
        settings,
        "INTEGRATION_SECRET_ENCRYPTION_KEY",
        TEST_INTEGRATION_SECRET_KEY,
    )


def _enable_external_gate(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "online")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", True)


def _disable_external_gate(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "online")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)


def _generic_signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _stripe_signature(secret: str, body: bytes, timestamp: int | None = None) -> str:
    timestamp = timestamp or int(time.time())
    signed_payload = f"{timestamp}.".encode() + body
    digest = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


async def _add_connection(
    db_session,
    auth_context,
    *,
    provider: IntegrationProvider,
    connection_status: IntegrationStatus = IntegrationStatus.connected,
    webhook_secret: str | None = "generic-webhook-secret",
) -> IntegrationConnection:
    settings_payload = {}
    if webhook_secret is not None:
        settings_payload = protect_integration_settings({"webhookSecret": webhook_secret})
    connection = IntegrationConnection(
        workspace_id=auth_context.workspace_id,
        provider=provider,
        status=connection_status,
        label=f"Webhook {provider.value}",
        settings=settings_payload,
        capabilities=["webhooks"],
    )
    db_session.add(connection)
    await db_session.flush()
    return connection


@pytest.mark.asyncio
async def test_generic_webhook_checks_external_gate_before_parsing(client, monkeypatch):
    _disable_external_gate(monkeypatch)

    response = await client.post(
        f"{GENERIC_WEBHOOK_PATH}/custom",
        content=b"not-json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "KLM-EXT-001"


@pytest.mark.asyncio
async def test_unknown_provider_does_not_fall_back_to_custom_connection(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    _enable_external_gate(monkeypatch)
    secret = "custom-secret"
    connection = await _add_connection(
        db_session,
        auth_context,
        provider=IntegrationProvider.custom,
        webhook_secret=secret,
    )
    body = b'{"event":"unknown-provider"}'

    response = await client.post(
        f"{GENERIC_WEBHOOK_PATH}/unknown-provider",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-KLM-Signature": _generic_signature(secret, body),
        },
    )

    assert response.status_code == 404
    await db_session.refresh(connection)
    assert connection.last_sync_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "connection_status",
    [IntegrationStatus.pending, IntegrationStatus.disconnected, IntegrationStatus.error],
)
async def test_generic_webhook_requires_connected_exact_connection(
    client,
    db_session,
    auth_context,
    monkeypatch,
    connection_status,
):
    _enable_external_gate(monkeypatch)
    secret = "inactive-secret"
    await _add_connection(
        db_session,
        auth_context,
        provider=IntegrationProvider.asaas,
        connection_status=connection_status,
        webhook_secret=secret,
    )
    body = b'{"event":"inactive"}'

    response = await client.post(
        f"{GENERIC_WEBHOOK_PATH}/asaas",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-KLM-Signature": _generic_signature(secret, body),
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "FINANCIAL_UPGRADE_PAUSED"


@pytest.mark.asyncio
@pytest.mark.parametrize("webhook_secret", [None, "   "])
async def test_generic_webhook_fails_closed_without_connection_secret(
    client,
    db_session,
    auth_context,
    monkeypatch,
    webhook_secret,
):
    _enable_external_gate(monkeypatch)
    await _add_connection(
        db_session,
        auth_context,
        provider=IntegrationProvider.asaas,
        webhook_secret=webhook_secret,
    )

    response = await client.post(
        f"{GENERIC_WEBHOOK_PATH}/asaas",
        content=b'{"event":"missing-secret"}',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "FINANCIAL_UPGRADE_PAUSED"


@pytest.mark.asyncio
@pytest.mark.parametrize("signature", ["", "sha256=invalid"])
async def test_generic_webhook_requires_valid_hmac(
    client,
    db_session,
    auth_context,
    monkeypatch,
    signature,
):
    _enable_external_gate(monkeypatch)
    await _add_connection(
        db_session,
        auth_context,
        provider=IntegrationProvider.asaas,
    )

    response = await client.post(
        f"{GENERIC_WEBHOOK_PATH}/asaas",
        content=b'{"event":"unsigned"}',
        headers={
            "Content-Type": "application/json",
            "X-KLM-Signature": signature,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "FINANCIAL_UPGRADE_PAUSED"


@pytest.mark.asyncio
async def test_generic_webhook_accepts_valid_hmac_for_connected_provider(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    _enable_external_gate(monkeypatch)
    secret = "valid-generic-secret"
    connection = await _add_connection(
        db_session,
        auth_context,
        provider=IntegrationProvider.asaas,
        webhook_secret=secret,
    )
    body = b'{"event":"payment.confirmed"}'

    response = await client.post(
        f"{GENERIC_WEBHOOK_PATH}/asaas",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-KLM-Signature": _generic_signature(secret, body),
        },
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "FINANCIAL_UPGRADE_PAUSED"


@pytest.mark.asyncio
async def test_dedicated_provider_cannot_use_generic_webhook(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    _enable_external_gate(monkeypatch)
    secret = "zapsign-generic-secret"
    connection = await _add_connection(
        db_session,
        auth_context,
        provider=IntegrationProvider.zapsign,
        webhook_secret=secret,
    )
    body = b'{"event":"doc_signed"}'

    response = await client.post(
        f"{GENERIC_WEBHOOK_PATH}/zapsign",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-KLM-Signature": _generic_signature(secret, body),
        },
    )

    assert response.status_code == 409
    await db_session.refresh(connection)
    assert connection.last_sync_at is None


@pytest.mark.asyncio
async def test_catalog_points_zapsign_to_dedicated_webhook(
    client,
    active_subscription,
):
    response = await client.get("/api/v1/integrations/catalog")

    assert response.status_code == 200, response.text
    zapsign = next(item for item in response.json() if item["key"] == "zapsign")
    assert zapsign["defaultWebhookPath"].endswith("/api/v1/webhooks/zapsign")
    assert "/integrations/webhooks/" not in zapsign["defaultWebhookPath"]


@pytest.mark.parametrize("app_env", ["development", "test", "production"])
def test_stripe_never_accepts_json_without_configured_secret(monkeypatch, app_env):
    monkeypatch.setattr(settings, "APP_ENV", app_env)

    with pytest.raises(HTTPException) as exc_info:
        _verify_stripe_signature(b'{"type":"test"}', "", "")

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_stripe_webhook_checks_external_gate_before_parsing(client, monkeypatch):
    _disable_external_gate(monkeypatch)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "stripe-secret")

    response = await client.post(
        "/api/v1/webhooks/stripe",
        content=b"not-json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "KLM-EXT-001"


@pytest.mark.asyncio
async def test_stripe_webhook_is_unavailable_without_secret(client, monkeypatch):
    _enable_external_gate(monkeypatch)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "   ")

    response = await client.post(
        "/api/v1/webhooks/stripe",
        content=b'{"type":"test"}',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_stripe_webhook_rejects_unsigned_json_with_secret(client, monkeypatch):
    _enable_external_gate(monkeypatch)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "stripe-secret")

    response = await client.post(
        "/api/v1/webhooks/stripe",
        content=b'{"type":"test"}',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_stripe_webhook_accepts_only_valid_signed_event(client, monkeypatch):
    _enable_external_gate(monkeypatch)
    secret = "stripe-secret"
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", secret)
    body = json.dumps(
        {
            "id": "evt_security",
            "type": "security.test",
            "created": int(time.time()),
            "data": {},
        },
        separators=(",", ":"),
    ).encode("utf-8")
    valid_signature = _stripe_signature(secret, body)

    invalid = await client.post(
        "/api/v1/webhooks/stripe",
        content=body,
        headers={
            "Content-Type": "application/json",
            "Stripe-Signature": valid_signature.replace("v1=", "v1=invalid"),
        },
    )
    valid = await client.post(
        "/api/v1/webhooks/stripe",
        content=body,
        headers={
            "Content-Type": "application/json",
            "Stripe-Signature": valid_signature,
        },
    )

    assert invalid.status_code == 400
    assert valid.status_code == 200, valid.text
    assert valid.json() == {"received": True}
