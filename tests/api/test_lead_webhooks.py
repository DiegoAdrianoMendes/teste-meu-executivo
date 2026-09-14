"""Endpoint POST /api/v1/webhooks/leads/{token} (F12 — Webhook de leads de sites).

Cobre o contrato: token errado -> 401; honeypot -> 200 silencioso; dedupe 24h
reaproveita o lead; lead completo cria contato+card+thread+draft (gates
fechados); sem telefone -> flag+task; payload ilegível -> 200 registrado.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.client import Client
from app.models.finance import IntegrationConnection, IntegrationProvider, IntegrationStatus
from app.models.klm_operations import KlmAuditEvent, OmnichannelThread
from app.models.matter import KanbanColumnId, Matter
from app.models.task import Task
from app.services.lead_webhook_service import generate_or_rotate_token

LEADS_WEBHOOK_URL = "/api/v1/webhooks/leads/{token}"
TEST_INTEGRATION_SECRET_KEY = "test-only-integration-key-7Hf9pQ2wX4mK8cR6vN3z"


@pytest.fixture(autouse=True)
def _configure_integration_secret_key(monkeypatch):
    monkeypatch.setattr(settings, "INTEGRATION_SECRET_ENCRYPTION_KEY", TEST_INTEGRATION_SECRET_KEY)


@pytest.fixture(autouse=True)
def _isolate_from_sdr_dispatch(monkeypatch):
    """O disparo do agente SDR é best-effort/assíncrono; isolamos dos testes HTTP."""

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr("app.workers.event_bus.dispatch_new_lead", _noop)
    return None


async def _make_site_leads_token(db_session, workspace_id) -> str:
    token = await generate_or_rotate_token(db_session, workspace_id=workspace_id)
    await db_session.commit()
    return token


@pytest.mark.asyncio
async def test_wrong_token_returns_401(client, db_session, auth_context):
    await _make_site_leads_token(db_session, auth_context.workspace_id)

    response = await client.post(
        LEADS_WEBHOOK_URL.format(token="token-que-nao-existe"),
        json={"name": "Fulano", "email": "fulano@example.com"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_honeypot_filled_returns_200_silently_and_creates_nothing(
    client, db_session, auth_context
):
    token = await _make_site_leads_token(db_session, auth_context.workspace_id)

    response = await client.post(
        LEADS_WEBHOOK_URL.format(token=token),
        json={"name": "Bot", "email": "bot@example.com", "_hp": "preenchido-por-bot"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["reason"] == "honeypot"

    clients = (
        await db_session.execute(select(Client).where(Client.workspace_id == auth_context.workspace_id))
    ).scalars().all()
    assert clients == []


@pytest.mark.asyncio
async def test_unparseable_payload_returns_200_with_rejection_registered(
    client, db_session, auth_context
):
    token = await _make_site_leads_token(db_session, auth_context.workspace_id)

    response = await client.post(
        LEADS_WEBHOOK_URL.format(token=token),
        content=b"{not-valid-json::",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["reason"] == "payload_ilegivel"


@pytest.mark.asyncio
async def test_full_lead_creates_client_card_thread_and_pending_draft_with_gates_closed(
    client, db_session, auth_context
):
    token = await _make_site_leads_token(db_session, auth_context.workspace_id)
    email = f"lead-{uuid.uuid4().hex[:8]}@example.com"

    response = await client.post(
        LEADS_WEBHOOK_URL.format(token=token),
        json={
            "nome": "Maria da Silva",
            "email": email,
            "telefone": "(61) 99999-1234",
            "mensagem": "Quero saber sobre reconhecimento de vínculo.",
            "utm_source": "google",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"

    client_row = (
        await db_session.execute(
            select(Client).where(Client.workspace_id == auth_context.workspace_id, Client.email == email)
        )
    ).scalars().first()
    assert client_row is not None

    matter = (
        await db_session.execute(
            select(Matter).where(Matter.workspace_id == auth_context.workspace_id, Matter.client_id == client_row.id)
        )
    ).scalars().first()
    assert matter is not None
    assert matter.column_id == KanbanColumnId.novo_lead
    # Gates fechados (sem template/conexão configurados) -> falha nunca silenciosa.
    assert matter.sdr_qualification is not None
    assert matter.sdr_qualification["first_contact_failed"]["reason"] in {
        "sem_template_configurado",
        "sem_conexao_whatsapp",
    }

    thread = (
        await db_session.execute(
            select(OmnichannelThread).where(
                OmnichannelThread.workspace_id == auth_context.workspace_id,
                OmnichannelThread.matter_id == matter.id,
            )
        )
    ).scalars().first()
    assert thread is not None

    task = (
        await db_session.execute(select(Task).where(Task.matter_id == matter.id))
    ).scalars().first()
    assert task is not None
    assert "primeiro contato" in task.title.lower()

    audit_events = (
        await db_session.execute(
            select(KlmAuditEvent).where(
                KlmAuditEvent.workspace_id == auth_context.workspace_id,
                KlmAuditEvent.entity_id == matter.id,
            )
        )
    ).scalars().all()
    event_types = {event.event_type for event in audit_events}
    assert "lead_webhook.lead_created" in event_types
    assert "lead_webhook.first_contact_failed" in event_types


@pytest.mark.asyncio
async def test_lead_without_phone_flags_first_contact_failed_reason(client, db_session, auth_context):
    token = await _make_site_leads_token(db_session, auth_context.workspace_id)
    email = f"lead-{uuid.uuid4().hex[:8]}@example.com"

    response = await client.post(
        LEADS_WEBHOOK_URL.format(token=token),
        json={"name": "Sem Telefone", "email": email},
    )

    assert response.status_code == 200

    matter = (
        await db_session.execute(
            select(Matter)
            .join(Client, Matter.client_id == Client.id)
            .where(Client.workspace_id == auth_context.workspace_id, Client.email == email)
        )
    ).scalars().first()
    assert matter is not None
    assert matter.sdr_qualification["first_contact_failed"]["reason"] == "sem_telefone"


@pytest.mark.asyncio
async def test_duplicate_submission_within_24h_reuses_existing_lead(client, db_session, auth_context):
    token = await _make_site_leads_token(db_session, auth_context.workspace_id)
    email = f"lead-{uuid.uuid4().hex[:8]}@example.com"

    first = await client.post(
        LEADS_WEBHOOK_URL.format(token=token),
        json={"name": "Duplicado", "email": email, "phone": "61988887777"},
    )
    assert first.status_code == 200
    assert first.json()["reason"] == "created"

    second = await client.post(
        LEADS_WEBHOOK_URL.format(token=token),
        json={"name": "Duplicado", "email": email, "phone": "61988887777"},
    )
    assert second.status_code == 200
    assert second.json()["reason"] == "dedupe_reused"

    clients = (
        await db_session.execute(
            select(Client).where(Client.workspace_id == auth_context.workspace_id, Client.email == email)
        )
    ).scalars().all()
    assert len(clients) == 1


@pytest.mark.asyncio
async def test_gclid_fbclid_and_utms_create_acquisition_event_on_lead_creation(
    client, db_session, auth_context
):
    """Auditoria 2026-07-26, item 3: gclid/fbclid da landing (F12) precisam
    chegar ao AcquisitionEvent do lead, não só cair em `extras` genérico —
    sem isto o lead nunca entra no funil anúncio→caso→receita (item 9)."""
    from app.models.klm_operations import AcquisitionEvent

    token = await _make_site_leads_token(db_session, auth_context.workspace_id)
    email = f"lead-clickid-{uuid.uuid4().hex[:8]}@example.com"

    response = await client.post(
        LEADS_WEBHOOK_URL.format(token=token),
        json={
            "nome": "Joana Pereira",
            "email": email,
            "utm_source": "google",
            "utm_medium": "cpc",
            "utm_campaign": "negativa-cobertura",
            "gclid": "EAIaIQobChMI1234567890",
            "fbclid": "PAZXh0bgNhZW0CMTEAAab1234567890",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"

    matter = (
        await db_session.execute(
            select(Matter).where(
                Matter.workspace_id == auth_context.workspace_id,
                Matter.client_id.in_(select(Client.id).where(Client.email == email)),
            )
        )
    ).scalars().first()
    assert matter is not None

    event = (
        await db_session.execute(
            select(AcquisitionEvent).where(
                AcquisitionEvent.workspace_id == auth_context.workspace_id,
                AcquisitionEvent.dedupe_key == f"site-lead:{matter.id}",
            )
        )
    ).scalars().first()
    assert event is not None
    assert event.click_ids["gclid"] == "EAIaIQobChMI1234567890"
    assert event.click_ids["fbclid"] == "PAZXh0bgNhZW0CMTEAAab1234567890"
    assert event.source == "google"
    assert event.medium == "cpc"
    assert event.campaign == "negativa-cobertura"
