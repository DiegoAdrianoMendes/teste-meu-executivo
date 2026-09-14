"""Endpoint POST /api/v1/webhooks/google-leads (auditoria 2026-07-26, item 4).

Cobre o contrato `WebhookLead`: google_key errada -> 401; sem lead_id -> 400;
lead completo cria Client+Matter+thread com gclid persistido no
AcquisitionEvent; retry do mesmo lead_id não duplica (dedupe); is_test=true
não cria caso real.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.client import Client
from app.models.klm_operations import AcquisitionEvent
from app.models.matter import Matter
from app.services.google_lead_webhook_service import generate_or_rotate_google_key

GOOGLE_LEADS_URL = "/api/v1/webhooks/google-leads"
TEST_INTEGRATION_SECRET_KEY = "test-only-integration-key-7Hf9pQ2wX4mK8cR6vN3z"


@pytest.fixture(autouse=True)
def _configure_integration_secret_key(monkeypatch):
    monkeypatch.setattr(settings, "INTEGRATION_SECRET_ENCRYPTION_KEY", TEST_INTEGRATION_SECRET_KEY)


@pytest.fixture(autouse=True)
def _isolate_from_sdr_dispatch(monkeypatch):
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr("app.workers.event_bus.dispatch_new_lead", _noop)
    return None


async def _make_google_key(db_session, workspace_id) -> str:
    key = await generate_or_rotate_google_key(db_session, workspace_id=workspace_id)
    await db_session.commit()
    return key


def _lead_payload(google_key: str, lead_id: str, **overrides) -> dict:
    payload = {
        "google_key": google_key,
        "api_version": "1",
        "lead_id": lead_id,
        "form_id": "form-123",
        "campaign_id": "campaign-456",
        "adgroup_id": "adgroup-789",
        "creative_id": "creative-321",
        "gcl_id": "EAIaIQobChMI1234567890",
        "is_test": False,
        "lead_submit_time": "2026-07-26 10:00:00",
        "user_column_data": [
            {"column_id": "FULL_NAME", "string_value": "Maria da Silva"},
            {"column_id": "EMAIL", "string_value": f"lead-{uuid.uuid4().hex[:8]}@example.com"},
            {"column_id": "PHONE_NUMBER", "string_value": "+55 61 99999-1234"},
        ],
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_wrong_google_key_returns_401(client, db_session, auth_context):
    await _make_google_key(db_session, auth_context.workspace_id)

    response = await client.post(
        GOOGLE_LEADS_URL,
        json=_lead_payload("chave-errada", str(uuid.uuid4())),
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "GADS-WH-001"


@pytest.mark.asyncio
async def test_missing_lead_id_returns_400(client, db_session, auth_context):
    key = await _make_google_key(db_session, auth_context.workspace_id)

    payload = _lead_payload(key, "will-be-removed")
    del payload["lead_id"]

    response = await client.post(GOOGLE_LEADS_URL, json=payload)

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "GADS-WH-004"
    assert response.json()["detail"]["message"] == "lead_id_ausente"


@pytest.mark.asyncio
async def test_full_lead_creates_client_matter_and_persists_gclid(
    client, db_session, auth_context
):
    key = await _make_google_key(db_session, auth_context.workspace_id)
    lead_id = str(uuid.uuid4())

    response = await client.post(GOOGLE_LEADS_URL, json=_lead_payload(key, lead_id))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "accepted"
    assert body["reason"] == "created"

    matter = (
        await db_session.execute(
            select(Matter).where(
                Matter.workspace_id == auth_context.workspace_id,
                Matter.lead_source == "google_ads",
            )
        )
    ).scalars().first()
    assert matter is not None

    client_row = (
        await db_session.execute(
            select(Client).where(Client.id == matter.client_id)
        )
    ).scalars().first()
    assert client_row is not None
    assert client_row.name == "Maria da Silva"

    event = (
        await db_session.execute(
            select(AcquisitionEvent).where(
                AcquisitionEvent.workspace_id == auth_context.workspace_id,
                AcquisitionEvent.dedupe_key == f"google-lead:{lead_id}",
            )
        )
    ).scalars().first()
    assert event is not None
    assert event.matter_id == matter.id
    assert event.click_ids["gclid"] == "EAIaIQobChMI1234567890"
    assert event.channel == "google_ads"
    assert event.campaign == "campaign-456"


@pytest.mark.asyncio
async def test_retry_of_same_lead_id_does_not_duplicate_matter(
    client, db_session, auth_context
):
    key = await _make_google_key(db_session, auth_context.workspace_id)
    lead_id = str(uuid.uuid4())
    payload = _lead_payload(key, lead_id)

    first = await client.post(GOOGLE_LEADS_URL, json=payload)
    second = await client.post(GOOGLE_LEADS_URL, json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["reason"] == "dedupe_reused"

    matters = (
        await db_session.execute(
            select(Matter).where(
                Matter.workspace_id == auth_context.workspace_id,
                Matter.lead_source == "google_ads",
            )
        )
    ).scalars().all()
    assert len(matters) == 1


@pytest.mark.asyncio
async def test_test_lead_is_accepted_but_creates_no_real_case(
    client, db_session, auth_context
):
    key = await _make_google_key(db_session, auth_context.workspace_id)
    lead_id = str(uuid.uuid4())

    response = await client.post(
        GOOGLE_LEADS_URL, json=_lead_payload(key, lead_id, is_test=True)
    )

    assert response.status_code == 200
    assert response.json()["reason"] == "test_lead_ignored"

    matters = (
        await db_session.execute(
            select(Matter).where(
                Matter.workspace_id == auth_context.workspace_id,
                Matter.lead_source == "google_ads",
            )
        )
    ).scalars().all()
    assert matters == []
