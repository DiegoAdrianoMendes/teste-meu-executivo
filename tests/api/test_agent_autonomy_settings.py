"""Autonomia configurável por workspace+agente (human-in-the-loop).

Cobre o contrato do painel administrativo: default fail-closed (hil_total,
sem banda), leitura/escrita versionada e auditada via
ConfigurationService (mesmo mecanismo já usado para vínculos de
ferramentas/agentes customizados — não um sistema paralelo), e isolamento
por workspace.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.configuration import ConfigDocument
from app.models.klm_operations import KlmAuditEvent
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.klm_agent_service import KlmAgentService

pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.mark.asyncio
async def test_agent_autonomy_defaults_to_hil_total_with_no_band(client):
    response = await client.get("/api/v1/klm/agents/autonomy")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload, "expected the native agent catalog to be listed"

    orquestrador = next(item for item in payload if item["agentKey"] == "orquestrador")
    assert orquestrador["autonomyMode"] == "hil_total"
    assert orquestrador["negotiationFloorPct"] is None
    assert orquestrador["negotiationBandPct"] is None


@pytest.mark.asyncio
async def test_agent_autonomy_update_is_workspace_scoped_and_audited(
    client, db_session, auth_context
):
    updated = await client.put(
        "/api/v1/klm/agents/closer/autonomy",
        json={
            "autonomyMode": "autonomo",
            "negotiationFloorPct": 0.85,
            "negotiationBandPct": 0.10,
            "reason": "Banda de desconto aprovada pelo sócio responsável.",
        },
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["agentKey"] == "closer"
    assert body["autonomyMode"] == "autonomo"
    assert body["negotiationFloorPct"] == 0.85
    assert body["negotiationBandPct"] == 0.10

    resolved = await client.get("/api/v1/klm/agents/autonomy")
    closer = next(item for item in resolved.json() if item["agentKey"] == "closer")
    assert closer["autonomyMode"] == "autonomo"
    assert closer["negotiationFloorPct"] == 0.85
    assert closer["negotiationBandPct"] == 0.10

    document = await db_session.scalar(
        select(ConfigDocument).where(
            ConfigDocument.workspace_id == auth_context.workspace_id,
            ConfigDocument.namespace == "agent.autonomy_settings",
        )
    )
    assert document is not None
    assert document.current_version == 1
    assert document.payload["agents"]["closer"]["autonomyMode"] == "autonomo"

    event = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.workspace_id == auth_context.workspace_id,
            KlmAuditEvent.event_type == "agent_autonomy_changed",
        )
    )
    assert event is not None
    assert event.after_data["agentKey"] == "closer"
    assert event.after_data["autonomyMode"] == "autonomo"
    assert event.after_data["negotiationFloorPct"] == 0.85


@pytest.mark.asyncio
async def test_agent_autonomy_rejects_out_of_range_band(client):
    response = await client.put(
        "/api/v1/klm/agents/closer/autonomy",
        json={
            "autonomyMode": "autonomo",
            "negotiationFloorPct": 1.5,
            "negotiationBandPct": 0.1,
        },
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_agent_autonomy_rejects_unknown_mode(client):
    response = await client.put(
        "/api/v1/klm/agents/closer/autonomy",
        json={"autonomyMode": "totalmente_livre"},
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_agent_autonomy_settings_are_isolated_per_workspace(db_session, auth_context):
    other_workspace = Workspace(
        name="Workspace Autonomia Externa",
        slug=f"autonomy-other-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()

    service = KlmAgentService(db_session)
    await service.set_agent_autonomy(
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        agent_key="closer",
        autonomy_mode="autonomo",
        negotiation_floor_pct=0.9,
        negotiation_band_pct=0.05,
        reason="Banda aprovada.",
        expected_etag=None,
    )
    await db_session.flush()

    own_settings = await service._resolve_agent_autonomy(auth_context.workspace_id, "closer")
    other_settings = await service._resolve_agent_autonomy(other_workspace.id, "closer")

    assert own_settings["autonomyMode"] == "autonomo"
    assert own_settings["negotiationFloorPct"] == 0.9
    # The other workspace never wrote an override: it resolves to the
    # fail-closed default, proving the config is scoped to workspace_id
    # (the same predicate Postgres RLS enforces on config_documents).
    assert other_settings["autonomyMode"] == "hil_total"
    assert other_settings["negotiationFloorPct"] is None
    assert other_settings["negotiationBandPct"] is None
