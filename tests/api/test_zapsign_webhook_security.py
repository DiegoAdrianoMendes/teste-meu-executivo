"""Fail-closed and idempotency contracts for ZapSign webhooks."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.api.v1.endpoints import webhooks as webhooks_module
from app.core.config import settings
from app.db.tenant import get_workspace_context
from app.models.finance import (
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
)
from app.models.klm_operations import ExternalActionIntent
from app.models.matter import Matter
from app.models.timeline_event import TimelineEvent
from app.services.integration_secrets import protect_integration_settings

TEST_INTEGRATION_SECRET_KEY = "test-only-integration-key-7Hf9pQ2wX4mK8cR6vN3z"


pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.fixture(autouse=True)
def _configure_integration_secret_key(monkeypatch):
    monkeypatch.setattr(
        settings,
        "INTEGRATION_SECRET_ENCRYPTION_KEY",
        TEST_INTEGRATION_SECRET_KEY,
    )


@pytest.fixture(autouse=True)
def _allow_external_webhook_delivery(monkeypatch):
    monkeypatch.setattr(webhooks_module, "external_delivery_allowed", lambda: True)


async def _matter_with_zapsign_token(
    client,
    db_session,
    token: str,
    *,
    webhook_secret: str | None = "webhook-secret",
) -> Matter:
    created_client = await client.post(
        "/api/v1/clients",
        json={"name": "Cliente ZapSign", "email": f"zapsign-{uuid4().hex[:8]}@example.com"},
    )
    assert created_client.status_code == 201, created_client.text
    created_matter = await client.post(
        "/api/v1/matters",
        json={
            "clientId": created_client.json()["id"],
            "practiceArea": "saude_suplementar",
            "demandType": "Negativa com contrato",
            "columnId": "contrato",
            "microStatus": "contrato_enviado",
        },
    )
    assert created_matter.status_code == 201, created_matter.text
    matter = await db_session.get(Matter, UUID(created_matter.json()["id"]))
    assert matter is not None
    matter.zapsign_doc_token = token
    matter.zapsign_status = "pending"
    db_session.add(
        IntegrationConnection(
            workspace_id=matter.workspace_id,
            provider=IntegrationProvider.zapsign,
            status=IntegrationStatus.connected,
            label="ZapSign",
            settings=(
                protect_integration_settings({"webhookSecret": webhook_secret})
                if webhook_secret is not None
                else {}
            ),
        )
    )
    await db_session.flush()
    return matter


def _payload(matter: Matter, token: str) -> dict:
    return {
        "event_type": "doc_signed",
        "token": token,
        "status": "signed",
        "external_id": str(matter.id),
        "name": "Contrato de honorários",
        "signed_file": "https://files.example.test/signed.pdf",
    }


@pytest.mark.asyncio
async def test_zapsign_webhook_is_unavailable_without_tenant_secret(
    client,
    db_session,
):
    matter = await _matter_with_zapsign_token(
        client,
        db_session,
        "doc-no-secret",
        webhook_secret=None,
    )
    response = await client.post(
        "/api/v1/webhooks/zapsign",
        json=_payload(matter, "doc-no-secret"),
        headers={"Authorization": "Bearer global-secret-must-not-work"},
    )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_zapsign_webhook_is_blocked_while_external_channels_are_off(
    client,
    monkeypatch,
):
    monkeypatch.setattr(webhooks_module, "external_delivery_allowed", lambda: False)
    response = await client.post("/api/v1/webhooks/zapsign", json={})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "KLM-EXT-001"


@pytest.mark.asyncio
async def test_zapsign_webhook_rejects_wrong_authorization(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "ZAPSIGN_WEBHOOK_SECRET", "webhook-secret")
    matter = await _matter_with_zapsign_token(client, db_session, "doc-secure")
    response = await client.post(
        "/api/v1/webhooks/zapsign",
        json=_payload(matter, "doc-secure"),
        headers={"Authorization": "Bearer wrong"},
    )
    assert response.status_code == 403
    await db_session.refresh(matter)
    assert matter.zapsign_status == "pending"


@pytest.mark.asyncio
async def test_zapsign_webhook_rejects_token_not_linked_to_matter(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "ZAPSIGN_WEBHOOK_SECRET", "webhook-secret")
    matter = await _matter_with_zapsign_token(client, db_session, "doc-expected")
    response = await client.post(
        "/api/v1/webhooks/zapsign",
        json=_payload(matter, "doc-forged"),
        headers={"Authorization": "Bearer webhook-secret"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    await db_session.refresh(matter)
    assert matter.zapsign_status == "pending"


@pytest.mark.asyncio
async def test_zapsign_webhook_is_idempotent_after_signature(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "ZAPSIGN_WEBHOOK_SECRET", "webhook-secret")
    matter = await _matter_with_zapsign_token(client, db_session, "doc-idempotent")
    headers = {"Authorization": "Bearer webhook-secret"}
    first = await client.post(
        "/api/v1/webhooks/zapsign",
        json=_payload(matter, "doc-idempotent"),
        headers=headers,
    )
    second = await client.post(
        "/api/v1/webhooks/zapsign",
        json=_payload(matter, "doc-idempotent"),
        headers=headers,
    )

    assert first.status_code == second.status_code == 200
    assert get_workspace_context(db_session) == matter.workspace_id
    assert first.json()["deduplicated"] is False
    assert second.json()["deduplicated"] is True
    await db_session.refresh(matter)
    assert matter.zapsign_status == "signed"
    assert matter.column_id.value == "assinado"
    event_count = await db_session.scalar(
        select(func.count(TimelineEvent.id)).where(
            TimelineEvent.matter_id == matter.id,
            TimelineEvent.title == "Contrato assinado via ZapSign",
        )
    )
    assert int(event_count or 0) == 1
    notification_intents = await db_session.scalar(
        select(func.count(ExternalActionIntent.id)).where(
            ExternalActionIntent.matter_id == matter.id,
            ExternalActionIntent.action_type == "contract_signed_client_notification",
        )
    )
    assert int(notification_intents or 0) == 1
