"""Anti-replay and atomic idempotency contracts for OpenClaw webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints import webhooks as webhooks_module
from app.core.config import settings
from app.models.matter import Matter
from app.models.timeline_event import TimelineEvent


pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.fixture(autouse=True)
def _allow_external_webhook_delivery(monkeypatch):
    monkeypatch.setattr(webhooks_module, "external_delivery_allowed", lambda: True)


def _request_body(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _signed_headers(
    body: bytes,
    secret: str,
    delivery_id: str,
    *,
    timestamp: int | None = None,
    workspace_slug: str = "workspace-teste",
) -> dict[str, str]:
    timestamp_text = str(int(time.time()) if timestamp is None else timestamp)
    signature = hmac.new(
        secret.encode("utf-8"),
        webhooks_module._openclaw_canonical_message(
            body,
            timestamp=timestamp_text,
            delivery_id=delivery_id,
            workspace_slug=workspace_slug,
        ),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-OpenClaw-Delivery": delivery_id,
        "X-OpenClaw-Timestamp": timestamp_text,
        "X-OpenClaw-Signature": f"sha256={signature}",
        "X-Workspace-Slug": workspace_slug,
    }


async def _matter_with_case_number(client, db_session, case_number: str) -> Matter:
    created_client = await client.post(
        "/api/v1/clients",
        json={
            "name": "Cliente OpenClaw",
            "email": f"openclaw-{uuid4().hex[:8]}@example.com",
        },
    )
    assert created_client.status_code == 201, created_client.text
    created_matter = await client.post(
        "/api/v1/matters",
        json={
            "clientId": created_client.json()["id"],
            "practiceArea": "saude_suplementar",
            "demandType": "Atualização processual",
            "caseNumber": case_number,
        },
    )
    assert created_matter.status_code == 201, created_matter.text
    matter = await db_session.get(Matter, UUID(created_matter.json()["id"]))
    assert matter is not None
    return matter


async def _openclaw_events(db_session, matter_id: UUID) -> list[TimelineEvent]:
    events = (
        await db_session.scalars(
            select(TimelineEvent).where(TimelineEvent.matter_id == matter_id)
        )
    ).all()
    return [
        event
        for event in events
        if isinstance(event.event_data, dict) and event.event_data.get("source") == "openclaw"
    ]


@pytest.mark.asyncio
async def test_openclaw_webhook_requires_delivery_timestamp_and_timestamp_body_signature(
    client,
    monkeypatch,
):
    secret = "openclaw-test-secret"
    body = _request_body({"description": "sem identificadores"})
    timestamp = str(int(time.time()))
    legacy_signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    monkeypatch.setattr(settings, "OPENCLAW_WEBHOOK_SECRET", secret)

    missing_delivery = await client.post(
        "/api/v1/webhooks/openclaw",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-OpenClaw-Timestamp": timestamp,
            "X-OpenClaw-Signature": f"sha256={legacy_signature}",
        },
    )
    missing_timestamp = await client.post(
        "/api/v1/webhooks/openclaw",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-OpenClaw-Delivery": "delivery-missing-timestamp",
            "X-OpenClaw-Signature": f"sha256={legacy_signature}",
        },
    )
    body_only_signature = await client.post(
        "/api/v1/webhooks/openclaw",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-OpenClaw-Delivery": "delivery-body-only",
            "X-OpenClaw-Timestamp": timestamp,
            "X-OpenClaw-Signature": f"sha256={legacy_signature}",
        },
    )

    assert missing_delivery.status_code == 403
    assert missing_timestamp.status_code == 403
    assert body_only_signature.status_code == 403


@pytest.mark.asyncio
async def test_openclaw_webhook_is_blocked_while_external_channels_are_off(
    client,
    monkeypatch,
):
    monkeypatch.setattr(webhooks_module, "external_delivery_allowed", lambda: False)
    response = await client.post("/api/v1/webhooks/openclaw", json={})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "KLM-EXT-001"


@pytest.mark.asyncio
async def test_openclaw_webhook_rejects_expired_timestamp(client, monkeypatch):
    secret = "openclaw-test-secret"
    body = _request_body(
        {"workspace_slug": "workspace-teste", "description": "evento expirado"}
    )
    expired_at = int(time.time()) - webhooks_module.OPENCLAW_WEBHOOK_MAX_AGE_SECONDS - 1
    monkeypatch.setattr(settings, "OPENCLAW_WEBHOOK_SECRET", secret)

    response = await client.post(
        "/api/v1/webhooks/openclaw",
        content=body,
        headers=_signed_headers(body, secret, "delivery-expired", timestamp=expired_at),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Webhook do OpenClaw expirado."


@pytest.mark.asyncio
async def test_openclaw_hmac_binds_delivery_id_and_tenant(client, monkeypatch):
    secret = "openclaw-test-secret"
    body = _request_body(
        {"workspace_slug": "workspace-teste", "description": "evento vinculado"}
    )
    monkeypatch.setattr(settings, "OPENCLAW_WEBHOOK_SECRET", secret)
    signed = _signed_headers(body, secret, "delivery-original")

    changed_delivery = await client.post(
        "/api/v1/webhooks/openclaw",
        content=body,
        headers={**signed, "X-OpenClaw-Delivery": "delivery-forged"},
    )
    changed_tenant = await client.post(
        "/api/v1/webhooks/openclaw",
        content=body,
        headers=_signed_headers(
            body,
            secret,
            "delivery-original",
            workspace_slug="outro-workspace",
        ),
    )

    assert changed_delivery.status_code == 403
    assert changed_tenant.status_code == 403
    assert "diverge" in changed_tenant.json()["detail"]


@pytest.mark.asyncio
async def test_openclaw_delivery_replay_is_deduplicated(
    client,
    db_session,
    monkeypatch,
):
    secret = "openclaw-test-secret"
    case_number = "0001234-56.2026.8.26.0100"
    matter = await _matter_with_case_number(client, db_session, case_number)
    payload = {
        "case_number": case_number,
        "workspace_slug": "workspace-teste",
        "title": "Movimentação processual",
        "description": "Conclusos para decisão.",
    }
    body = _request_body(payload)
    delivery_id = "delivery-idempotent"
    monkeypatch.setattr(settings, "OPENCLAW_WEBHOOK_SECRET", secret)

    first = await client.post(
        "/api/v1/webhooks/openclaw",
        content=body,
        headers=_signed_headers(body, secret, delivery_id),
    )
    replay = await client.post(
        "/api/v1/webhooks/openclaw",
        content=body,
        headers=_signed_headers(body, secret, delivery_id),
    )

    assert first.status_code == replay.status_code == 200
    assert first.json()["deduplicated"] is False
    assert replay.json()["deduplicated"] is True
    assert first.json()["event_id"] == replay.json()["event_id"]
    assert first.json()["matter_id"] == str(matter.id)
    events = await _openclaw_events(db_session, matter.id)
    assert len(events) == 1
    assert events[0].event_data["delivery_id"] == delivery_id
    assert events[0].event_data["payload_sha256"] == hashlib.sha256(body).hexdigest()


@pytest.mark.asyncio
async def test_openclaw_delivery_id_reuse_with_changed_body_is_rejected(
    client,
    db_session,
    monkeypatch,
):
    secret = "openclaw-test-secret"
    case_number = "0002234-56.2026.8.26.0100"
    matter = await _matter_with_case_number(client, db_session, case_number)
    original_body = _request_body(
        {
            "case_number": case_number,
            "workspace_slug": "workspace-teste",
            "description": "Despacho publicado.",
        }
    )
    changed_body = _request_body(
        {
            "case_number": case_number,
            "workspace_slug": "workspace-teste",
            "description": "Payload alterado.",
        }
    )
    delivery_id = "delivery-conflicting"
    monkeypatch.setattr(settings, "OPENCLAW_WEBHOOK_SECRET", secret)

    first = await client.post(
        "/api/v1/webhooks/openclaw",
        content=original_body,
        headers=_signed_headers(original_body, secret, delivery_id),
    )
    conflict = await client.post(
        "/api/v1/webhooks/openclaw",
        content=changed_body,
        headers=_signed_headers(changed_body, secret, delivery_id),
    )

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert len(await _openclaw_events(db_session, matter.id)) == 1


@pytest.mark.asyncio
async def test_openclaw_concurrent_insert_conflict_returns_deduplicated(
    client,
    db_session,
    monkeypatch,
):
    secret = "openclaw-test-secret"
    case_number = "0003234-56.2026.8.26.0100"
    matter = await _matter_with_case_number(client, db_session, case_number)
    body = _request_body(
        {
            "case_number": case_number,
            "workspace_slug": "workspace-teste",
            "description": "Evento concorrente.",
        }
    )
    delivery_id = "delivery-concurrent"
    monkeypatch.setattr(settings, "OPENCLAW_WEBHOOK_SECRET", secret)
    first = await client.post(
        "/api/v1/webhooks/openclaw",
        content=body,
        headers=_signed_headers(body, secret, delivery_id),
    )
    assert first.status_code == 200

    original_scalar = AsyncSession.scalar
    stale_read_pending = True

    async def scalar_with_stale_precheck(self, statement, *args, **kwargs):
        nonlocal stale_read_pending
        if self is db_session and stale_read_pending:
            stale_read_pending = False
            return None
        return await original_scalar(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "scalar", scalar_with_stale_precheck)
    concurrent_loser = await client.post(
        "/api/v1/webhooks/openclaw",
        content=body,
        headers=_signed_headers(body, secret, delivery_id),
    )

    assert concurrent_loser.status_code == 200, concurrent_loser.text
    assert concurrent_loser.json()["deduplicated"] is True
    assert concurrent_loser.json()["event_id"] == first.json()["event_id"]
    assert len(await _openclaw_events(db_session, matter.id)) == 1
    assert await db_session.scalar(
        select(func.count(TimelineEvent.id)).where(
            TimelineEvent.id == UUID(first.json()["event_id"])
        )
    ) == 1
