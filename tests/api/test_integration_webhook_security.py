from __future__ import annotations

import hashlib
import hmac
import json
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.finance import IntegrationConnection, IntegrationProvider, IntegrationStatus
from app.models.timeline_event import TimelineEvent, TimelineEventType
from app.services.integration_secrets import protect_integration_settings

WEBHOOK_URL = "/api/v1/integrations/webhooks/workspace-teste"
TEST_INTEGRATION_SECRET_KEY = "test-only-integration-key-7Hf9pQ2wX4mK8cR6vN3z"


@pytest.fixture(autouse=True)
def _configure_integration_secret_key(monkeypatch):
    monkeypatch.setattr(
        settings,
        "INTEGRATION_SECRET_ENCRYPTION_KEY",
        TEST_INTEGRATION_SECRET_KEY,
    )


def _open_external_gate(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "production")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", True)


async def _connection(
    db_session,
    workspace_id,
    *,
    provider: IntegrationProvider = IntegrationProvider.custom,
    connection_status: IntegrationStatus = IntegrationStatus.connected,
    secret: str | None = "generic-webhook-secret",
) -> IntegrationConnection:
    integration_settings = (
        protect_integration_settings({"webhookSecret": secret}) if secret is not None else {}
    )
    connection = IntegrationConnection(
        workspace_id=workspace_id,
        provider=provider,
        status=connection_status,
        label=f"{provider.value}-{uuid4().hex[:8]}",
        capabilities=["webhooks"],
        settings=integration_settings,
    )
    db_session.add(connection)
    await db_session.flush()
    return connection


def _signed_request(
    payload: dict,
    secret: str,
    *,
    pretty: bool = False,
) -> tuple[bytes, dict[str, str]]:
    if pretty:
        body = json.dumps(payload, indent=2).encode("utf-8")
    else:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return body, {
        "Content-Type": "application/json",
        "X-KLM-Signature": f"sha256={signature}",
    }


async def _create_matter(client) -> UUID:
    created_client = await client.post(
        "/api/v1/clients",
        json={
            "name": "Cliente Webhook Genérico",
            "email": f"webhook-{uuid4().hex[:8]}@example.com",
        },
    )
    assert created_client.status_code == 201, created_client.text
    created_matter = await client.post(
        "/api/v1/matters",
        json={
            "clientId": created_client.json()["id"],
            "practiceArea": "saude_suplementar",
            "demandType": "Evento de integração",
            "columnId": "contrato",
            "microStatus": "contrato_enviado",
        },
    )
    assert created_matter.status_code == 201, created_matter.text
    return UUID(created_matter.json()["id"])


@pytest.mark.asyncio
async def test_generic_webhook_fails_at_gate_before_parsing(client, monkeypatch):
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)

    response = await client.post(
        f"{WEBHOOK_URL}/custom",
        content=b"not-json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "KLM-EXT-001"


@pytest.mark.asyncio
async def test_unknown_provider_cannot_fall_back_to_custom_connection(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    _open_external_gate(monkeypatch)
    secret = "generic-webhook-secret"
    await _connection(db_session, auth_context.workspace_id, secret=secret)
    body, headers = _signed_request({"id": "evt-unknown"}, secret)

    response = await client.post(f"{WEBHOOK_URL}/vendor-x", content=body, headers=headers)

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_generic_webhook_requires_an_exact_connected_connection(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    _open_external_gate(monkeypatch)
    secret = "generic-webhook-secret"
    await _connection(
        db_session,
        auth_context.workspace_id,
        connection_status=IntegrationStatus.disconnected,
        secret=secret,
    )
    body, headers = _signed_request({"id": "evt-disconnected"}, secret)

    response = await client.post(f"{WEBHOOK_URL}/custom", content=body, headers=headers)

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_generic_webhook_fails_closed_without_webhook_secret(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    _open_external_gate(monkeypatch)
    await _connection(db_session, auth_context.workspace_id, secret=None)
    body = b'{"id":"evt-no-secret"}'

    response = await client.post(
        f"{WEBHOOK_URL}/custom",
        content=body,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_generic_webhook_requires_valid_hmac(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    _open_external_gate(monkeypatch)
    await _connection(db_session, auth_context.workspace_id)
    body = b'{"id":"evt-bad-signature"}'

    missing = await client.post(
        f"{WEBHOOK_URL}/custom",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    invalid = await client.post(
        f"{WEBHOOK_URL}/custom",
        content=body,
        headers={"Content-Type": "application/json", "X-KLM-Signature": "sha256=invalid"},
    )

    assert missing.status_code == 403
    assert invalid.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "expected_status"),
    [("zapsign", 409), ("stripe", 404), ("meta", 404)],
)
async def test_dedicated_providers_cannot_use_generic_handler(
    client,
    monkeypatch,
    provider,
    expected_status,
):
    _open_external_gate(monkeypatch)
    response = await client.post(f"{WEBHOOK_URL}/{provider}", json={"id": "evt-bypass"})
    assert response.status_code == expected_status


@pytest.mark.asyncio
async def test_generic_webhook_accepts_valid_hmac_and_deduplicates_delivery_id(
    client,
    db_session,
    auth_context,
    monkeypatch,
    active_subscription,
):
    _open_external_gate(monkeypatch)
    secret = "generic-webhook-secret"
    await _connection(db_session, auth_context.workspace_id, secret=secret)
    matter_id = await _create_matter(client)
    payload = {
        "id": "evt-idempotent",
        "type": "matter.updated",
        "matterId": str(matter_id),
        "description": "Atualização assinada",
    }
    body, headers = _signed_request(payload, secret)
    replay_body, replay_headers = _signed_request(payload, secret, pretty=True)

    first = await client.post(f"{WEBHOOK_URL}/custom", content=body, headers=headers)
    replay = await client.post(
        f"{WEBHOOK_URL}/custom",
        content=replay_body,
        headers=replay_headers,
    )

    assert first.status_code == replay.status_code == 200
    event_count = await db_session.scalar(
        select(func.count(TimelineEvent.id)).where(
            TimelineEvent.matter_id == matter_id,
            TimelineEvent.event_type == TimelineEventType.webhook_received,
        )
    )
    assert int(event_count or 0) == 1
    event = await db_session.scalar(
        select(TimelineEvent).where(
            TimelineEvent.matter_id == matter_id,
            TimelineEvent.event_type == TimelineEventType.webhook_received,
        )
    )
    assert event is not None
    assert event.id == uuid5(
        NAMESPACE_URL,
        f"klm:integration:{matter_id}:custom:matter.updated:evt-idempotent",
    )

    changed_body, changed_headers = _signed_request(
        {**payload, "description": "Conteúdo divergente"},
        secret,
    )
    conflict = await client.post(
        f"{WEBHOOK_URL}/custom",
        content=changed_body,
        headers=changed_headers,
    )
    assert conflict.status_code == 409
