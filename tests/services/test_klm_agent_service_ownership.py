"""Enforcement de posse de conversa no ponto único de execução de agentes.

Cobre a integração real (`KlmAgentService._execute_one`, via o endpoint
`POST /api/v1/klm/agents/runs`) da harmonização multi-área: conversa órfã é
reivindicada pelo primeiro agente que responde (recepção), e um agente
diferente do dono é recusado deterministicamente — nunca gera uma segunda
resposta na mesma janela. Os testes puramente de serviço (CAS, transfer,
takeover, segunda área) vivem em `tests/agents/test_conversation_ownership.py`;
este arquivo cobre só a integração com o runtime de agentes.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.klm_operations import KlmAuditEvent, OmnichannelThread
from tests.agent_deployment_helpers import seed_verified_agent_deployments

pytestmark = pytest.mark.usefixtures("active_subscription")

CUSTOM_AREA_KEY = "harmonizacao_teste_area"


@pytest.fixture(autouse=True)
async def _explicit_agent_deployments(db_session, auth_context, monkeypatch):
    await seed_verified_agent_deployments(
        db_session, auth_context.workspace_id, {"crm", "sdr"}, monkeypatch
    )


async def _case(client, *, demand_type: str = "Demanda de teste"):
    """Cria cliente + caso + conversa canônica com uma área NÃO gerida pelo
    catálogo (`legalAreaKey` livre) — dispensa a cerimônia de ativar
    `AreaBlueprint`, já que `area_enabled` é automaticamente verdadeiro para
    áreas fora do catálogo gerido (`KlmAgentService._execute_one`)."""
    person_response = await client.post(
        "/api/v1/clients",
        json={
            "name": f"Lead {uuid4().hex[:8]}",
            "email": f"lead.{uuid4().hex[:8]}@example.com",
            "phone": "+5511988887777",
        },
    )
    assert person_response.status_code == 201, person_response.text
    person = person_response.json()

    matter_response = await client.post(
        "/api/v1/matters",
        json={
            "clientId": person["id"],
            "demandType": demand_type,
            "columnId": "novo_lead",
            "microStatus": "lead_novo",
            "legalAreaKey": CUSTOM_AREA_KEY,
        },
    )
    assert matter_response.status_code == 201, matter_response.text
    matter = matter_response.json()

    thread_response = await client.post(
        "/api/v1/klm/omnichannel/threads",
        json={
            "matterId": matter["id"],
            "clientId": person["id"],
            "primaryChannel": "whatsapp",
            "subject": demand_type,
        },
    )
    assert thread_response.status_code == 201, thread_response.text
    return person, matter, thread_response.json()


def _run_payload(matter, thread, agent, **overrides):
    payload = {
        "agentKey": agent,
        "matterId": matter["id"],
        "threadId": thread["id"],
        "requestHuman": False,
        "instructions": "Executar contrato local de teste.",
        "idempotencyKey": f"test-ownership:{agent}:{uuid4()}",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_first_agent_response_auto_claims_orphan_thread(client, db_session, auth_context):
    _, matter, thread = await _case(client)

    thread_before = await db_session.scalar(
        select(OmnichannelThread).where(OmnichannelThread.id == thread["id"])
    )
    assert thread_before.owning_agent_key is None
    assert thread_before.owner_generation == 0

    response = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(matter, thread, "sdr"),
    )
    assert response.status_code == 201, response.text

    await db_session.refresh(thread_before)
    assert thread_before.owning_agent_key == "sdr"
    assert thread_before.owning_area_key == CUSTOM_AREA_KEY
    assert thread_before.owner_generation == 1
    assert thread_before.owner_since is not None

    claimed_event = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.workspace_id == auth_context.workspace_id,
            KlmAuditEvent.event_type == "conversation_ownership_claimed",
            KlmAuditEvent.entity_id == thread_before.id,
        )
    )
    assert claimed_event is not None
    assert claimed_event.after_data["owningAgentKey"] == "sdr"


@pytest.mark.asyncio
async def test_non_owner_agent_is_denied_deterministically_and_audited(
    client, db_session, auth_context
):
    _, matter, thread = await _case(client)

    first = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(matter, thread, "sdr"),
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(matter, thread, "crm"),
    )
    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert detail["code"] == "CONVERSATION_OWNERSHIP_DENIED"

    denied_event = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.workspace_id == auth_context.workspace_id,
            KlmAuditEvent.event_type == "conversation_ownership_response_denied",
            KlmAuditEvent.entity_id == thread["id"],
        )
    )
    assert denied_event is not None
    assert denied_event.after_data["owningAgentKey"] == "sdr"
    assert denied_event.after_data["reason"] == "not_owner"

    thread_row = await db_session.scalar(
        select(OmnichannelThread).where(OmnichannelThread.id == thread["id"])
    )
    # The denied agent never claims — ownership (and generation) is untouched.
    assert thread_row.owning_agent_key == "sdr"
    assert thread_row.owner_generation == 1


@pytest.mark.asyncio
async def test_same_owner_agent_can_keep_responding(client, db_session, auth_context):
    _, matter, thread = await _case(client)

    first = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(matter, thread, "sdr", idempotencyKey="owner-run-1"),
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(matter, thread, "sdr", idempotencyKey="owner-run-2"),
    )
    assert second.status_code == 201, second.text

    thread_row = await db_session.scalar(
        select(OmnichannelThread).where(OmnichannelThread.id == thread["id"])
    )
    # Idempotent ownership: still the same owner, generation only bumped once
    # (by the auto-claim on the first run) — the second run never re-races.
    assert thread_row.owning_agent_key == "sdr"
    assert thread_row.owner_generation == 1
