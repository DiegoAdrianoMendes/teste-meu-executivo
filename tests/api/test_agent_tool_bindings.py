"""Contratos de governança para vínculos de ferramentas por agente."""

import pytest
from sqlalchemy import select

from app.models.configuration import ConfigDocument
from app.models.klm_operations import KlmAuditEvent

pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.mark.asyncio
async def test_agent_tools_are_workspace_scoped_and_audited(client, db_session, auth_context):
    initial = await client.get("/api/v1/klm/agents")
    assert initial.status_code == 200, initial.text
    original = next(item for item in initial.json()["agents"] if item["key"] == "orquestrador")

    updated = await client.patch(
        "/api/v1/klm/agents/orquestrador/tools",
        json={"tools": ["agent_routing", "flow_runtime"], "reason": "Teste de menor privilégio."},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["tools"] == ["agent_routing", "flow_runtime"]

    resolved = await client.get("/api/v1/klm/agents")
    effective = next(item for item in resolved.json()["agents"] if item["key"] == "orquestrador")
    assert effective["tools"] == ["agent_routing", "flow_runtime"]
    assert effective["tools"] != original["tools"]

    document = await db_session.scalar(
        select(ConfigDocument).where(
            ConfigDocument.workspace_id == auth_context.workspace_id,
            ConfigDocument.namespace == "agent.tool_bindings",
        )
    )
    assert document is not None
    assert document.current_version == 1
    assert document.payload["agents"]["orquestrador"] == ["agent_routing", "flow_runtime"]
    event = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.workspace_id == auth_context.workspace_id,
            KlmAuditEvent.event_type == "agent_tools_changed",
        )
    )
    assert event is not None
    assert event.after_data["tools"] == ["agent_routing", "flow_runtime"]


@pytest.mark.asyncio
async def test_agent_tools_reject_unknown_and_external_keys(client, db_session):
    # The API rolls back rejected mutations; commit fixture identities first so
    # both rejection paths can be asserted in the same request context.
    await db_session.commit()

    for tools in (["not-in-local-catalog"], ["external:whatsapp"]):
        response = await client.patch(
            "/api/v1/klm/agents/orquestrador/tools",
            json={"tools": list(tools), "reason": "Tentativa inválida."},
        )
        assert response.status_code == 422, response.text
