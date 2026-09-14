"""Contratos do motor deterministico dos agentes do KLM Hub."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.ai.base import AICompletionResult
from app.ai.errors import ProviderCallError
from app.ai.limits import InMemoryDailyBudgetLedger
from app.core.deps import get_current_auth_context
from app.main import app as fastapi_app
from app.models.agent_platform import AgentDeployment, DeploymentStatus
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.approval import ApprovalQueueItem
from app.models.billing import PlatformTokenUsage
from app.models.document import Document
from app.models.klm_operations import (
    AreaBlueprint,
    AreaBlueprintStatus,
    ExternalActionIntent,
    KlmAuditEvent,
    MessageAuthorType,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
    OmnichannelThread,
)
from app.models.matter import Matter


@pytest.fixture(autouse=True)
async def _enable_health_area_for_agent_runtime(
    client,
    db_session,
    auth_context,
    active_subscription,
    monkeypatch,
):
    """Agent runtime tests use an enabled area and explicit active deployments."""
    response = await client.get("/api/v1/klm/areas")
    assert response.status_code == 200, response.text
    area = await db_session.scalar(
        select(AreaBlueprint).where(
            AreaBlueprint.workspace_id == auth_context.workspace_id,
            AreaBlueprint.area_key == "saude_suplementar",
        )
    )
    assert area is not None
    area.status = AreaBlueprintStatus.active
    area.is_active = True
    area.readiness = 100
    for agent_key in {
        "auditor",
        "crm",
        "hermes",
        "juridico",
        "juridico_saude",
        "orquestrador",
        "peticao",
        "saude_medicamentos",
        "sdr",
        "sdr_saude",
        "secretaria",
    }:
        db_session.add(
            AgentDeployment(
                workspace_id=auth_context.workspace_id,
                agent_key=agent_key,
                version=1,
                status=DeploymentStatus.active,
                config={
                    "definitionVersion": 1,
                    "model": "test",
                    "testedAt": "2026-08-03T00:00:00Z",
                },
                gates={"published": True},
                budget_policy={"monthlyTokenLimit": 100_000},
            )
        )
    monkeypatch.setattr(
        "app.services.agent_runtime_service.verify_deployment",
        AsyncMock(return_value={"testPassed": True}),
    )
    monkeypatch.setattr(
        "app.services.agent_runtime_service.validate_deployment_ready",
        lambda config, gates: None,
    )
    await db_session.flush()
    return area


def _use_platform_identity(platform_auth_context) -> None:
    async def override_auth_context():
        return platform_auth_context

    fastapi_app.dependency_overrides[get_current_auth_context] = override_auth_context


async def _case(
    client,
    *,
    name="Ana Paula R.",
    object_name="Zolgensma",
    health_subtheme="medicamento_alto_custo",
):
    person_response = await client.post(
        "/api/v1/clients",
        json={
            "name": name,
            "email": f"{name.lower().replace(' ', '.')}@example.com",
            "phone": "+5511999999999",
        },
    )
    assert person_response.status_code == 201, person_response.text
    person = person_response.json()
    matter_response = await client.post(
        "/api/v1/matters",
        json={
            "clientId": person["id"],
            "practiceArea": "saude_suplementar",
            "demandType": f"Negativa de {object_name}",
            "columnId": "novo_lead",
            "microStatus": "lead_novo",
            "healthSubtheme": health_subtheme,
            "healthObjectName": object_name,
            "healthInsurer": "Plano Exemplo",
            "medicalUrgencyHours": 72,
            "leadScore": 80,
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
            "subject": "Medicamento de alto custo",
            "objectName": object_name,
            "ownerAgentKey": "sdr",
            "score": 80,
        },
    )
    assert thread_response.status_code == 201, thread_response.text
    return person, matter, thread_response.json()


def _run_payload(matter, thread, agent="sdr", **overrides):
    payload = {
        "agentKey": agent,
        "matterId": matter["id"],
        "threadId": thread["id"],
        "requestHuman": False,
        "instructions": "Executar contrato local de teste.",
        "idempotencyKey": f"test:{agent}:{uuid4()}",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_registry_separates_orchestrator_from_hermes_human_bridge(client):
    response = await client.get("/api/v1/klm/agents")
    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime"] == "api_first_gated"
    assert payload["metrics"]["modelStrategy"] == "api_first"
    assert payload["metrics"]["modelApisEnabled"] is False
    assert payload["metrics"]["executionMode"] == "local_simulator"
    assert payload["metrics"]["modeled"] == 32
    assert payload["metrics"]["coordinationAgent"] == "orquestrador"
    assert payload["metrics"]["ownerBridgeAgent"] == "hermes"
    assert payload["metrics"]["externalActionsExecuted"] == 0
    assert len(payload["agents"]) == 32
    requesters = [
        agent["key"] for agent in payload["agents"] if agent["permissions"]["requestHuman"]
    ]
    assert requesters == ["hermes"]
    agents = {agent["key"]: agent for agent in payload["agents"]}
    assert agents["orquestrador"]["role"] == "Coordenação de conversas e sistema"
    assert agents["orquestrador"]["permissions"]["requestHuman"] is False
    assert agents["hermes"]["role"] == "Ponte executiva com o dono"
    assert "owner_whatsapp_bridge" in agents["hermes"]["tools"]
    assert {
        "orquestrador",
        "hermes",
        "orquestrador",
        "sdr",
        "juridico",
        "crm",
        "closer",
        "secretaria",
        "paralegal",
        "peticao",
        "revisor_juridico",
        "andamento",
        "pos_venda_cliente",
        "financeiro",
        "controladoria_financeira",
        "contas_receber",
        "conciliacao_financeira",
        "orcamento_forecast",
        "valuation",
        "tributario_reforma",
        "cobranca",
        "marketing",
        "trafego_google",
        "track",
        "tracking_engineer",
        "infoproduto",
        "audio",
        "auditor",
        "ti",
        "qualidade",
            "trafego_google",
        "tracking_engineer",
        "atendimento_meta",
        "disparos_meta",
        "sdr_saude_vendas",
} == set(agents)
    assert payload["policyVersion"] == "legal-agents-v2.0.0-plug-and-play"
    assert payload["governance"]["version"] == "agent-governance-v1.5.0"
    assert payload["governance"]["policyDigestAlgorithm"] == "sha256"
    assert len(payload["governance"]["policyDigest"]) == 64
    assert set(payload["governance"]["policyDigest"]).issubset(set("0123456789abcdef"))
    assert payload["governance"]["coordinationAgentKey"] == "orquestrador"
    assert payload["governance"]["ownerBridgeAgentKey"] == "hermes"
    assert payload["governance"]["ownerBridgeDelegatorKey"] == "secretaria"
    assert payload["governance"]["delegationRules"]["ownerBridgeOnlyFrom"] == "secretaria"
    assert payload["governance"]["humanQueueCoordinator"] == "orquestrador"
    assert payload["governance"]["hermesOnlyHumanBridge"] is False
    assert payload["governance"]["parentByAgentKey"]["hermes"] == "secretaria"
    assert payload["governance"]["crossWorkspaceAccessDenied"] is True
    assert payload["governance"]["crossClientAccessDenied"] is True
    assert payload["governance"]["crossProductAccessDisabled"] is True
    assert payload["governance"]["leastPrivilegeTools"] is True
    assert payload["governance"]["externalActionsAllowed"] is False
    assert len(payload["specializedAgents"]) >= 50
    medication_agent = next(
        agent for agent in payload["specializedAgents"] if agent["key"] == "saude_medicamentos"
    )
    assert medication_agent["areaKey"] == "saude_suplementar"
    assert medication_agent["productKey"] == "medicamento_alto_custo"
    assert medication_agent["parentAgentKey"] == "juridico_saude"
    assert medication_agent["state"] == "active"
    assert all(agent["permissions"]["sendExternal"] is False for agent in payload["agents"])
    assert all(agent["modelPolicy"]["strategy"] == "api_first" for agent in payload["agents"])
    assert all(agent["modelPolicy"]["localModelEnabled"] is False for agent in payload["agents"])
    assert all(agent["modelPolicy"]["modelApiEnabled"] is False for agent in payload["agents"])
    assert all(
        agent["modelPolicy"]["channelDeliveryEnabled"] is False for agent in payload["agents"]
    )
    assert all(agent["modelPolicy"]["externalEnabled"] is False for agent in payload["agents"])


@pytest.mark.asyncio
async def test_enabling_model_apis_does_not_enable_channel_delivery(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.klm_agent_service.settings.KLM_MODEL_APIS_ENABLED",
        True,
    )
    monkeypatch.setattr(
        "app.services.klm_agent_service.settings.KLM_ONLINE_APPROVED",
        True,
    )
    monkeypatch.setattr(
        "app.core.klm_runtime.settings.KLM_ONLINE_APPROVED",
        True,
    )
    monkeypatch.setattr(
        "app.core.klm_runtime.settings.KLM_EXTERNAL_ACTIONS_ENABLED",
        False,
    )

    payload = (await client.get("/api/v1/klm/agents")).json()

    assert payload["metrics"]["modelApisEnabled"] is True
    assert payload["metrics"]["executionMode"] == "model_api"
    assert all(agent["modelPolicy"]["modelApiEnabled"] is True for agent in payload["agents"])
    assert all(
        agent["modelPolicy"]["channelDeliveryEnabled"] is False for agent in payload["agents"]
    )
    assert all(agent["modelPolicy"]["externalEnabled"] is False for agent in payload["agents"])
    assert all(agent["permissions"]["sendExternal"] is False for agent in payload["agents"])


@pytest.mark.asyncio
async def test_agent_pause_and_resume_are_strictly_ordered(client):
    paused = await client.patch(
        "/api/v1/klm/agents/crm/state",
        json={"state": "paused", "reason": "Teste de pausa"},
    )
    resumed = await client.patch(
        "/api/v1/klm/agents/crm/state",
        json={"state": "active", "reason": "Teste de retomada"},
    )
    assert paused.status_code == resumed.status_code == 200
    registry = (await client.get("/api/v1/klm/agents")).json()
    crm = next(agent for agent in registry["agents"] if agent["key"] == "crm")
    assert crm["state"] == "active"
    assert registry["metrics"]["paused"] == 0


@pytest.mark.asyncio
async def test_paused_agent_cannot_execute(client):
    _, matter, thread = await _case(client)
    await client.patch(
        "/api/v1/klm/agents/sdr/state",
        json={"state": "paused", "reason": "Manutencao local"},
    )
    response = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(matter, thread),
    )
    assert response.status_code == 409
    assert "pausado" in response.json()["detail"]


@pytest.mark.asyncio
async def test_unknown_agent_is_rejected(client):
    _, matter, thread = await _case(client)
    response = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(matter, thread, agent="inexistente"),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_unknown_matter_is_rejected(client):
    response = await client.post(
        "/api/v1/klm/agents/runs",
        json={
            "agentKey": "sdr",
            "matterId": str(uuid4()),
            "requestHuman": False,
            "idempotencyKey": "unknown-matter",
        },
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_frozen_area_blocks_business_agent_but_keeps_independent_auditor_available(
    client,
    db_session,
    _enable_health_area_for_agent_runtime,
):
    area = _enable_health_area_for_agent_runtime
    area.status = AreaBlueprintStatus.blueprint
    area.is_active = False
    await db_session.flush()
    _, matter, thread = await _case(client, name="Area congelada")

    denied = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(
            matter,
            thread,
            agent="sdr",
            idempotencyKey="frozen-area-sdr-denied",
        ),
    )
    audited = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(
            matter,
            thread,
            agent="auditor",
            idempotencyKey="frozen-area-auditor-allowed",
        ),
    )

    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] == "AREA_FROZEN"
    assert audited.status_code == 201, audited.text
    assert audited.json()["runs"][0]["agentName"] == "auditor"


@pytest.mark.asyncio
async def test_frozen_area_is_denied_before_rag_retrieval_and_audited(
    client,
    db_session,
    monkeypatch,
    _enable_health_area_for_agent_runtime,
):
    area = _enable_health_area_for_agent_runtime
    area.status = AreaBlueprintStatus.blueprint
    area.is_active = False
    await db_session.flush()
    _, matter, thread = await _case(client, name="Sem leitura indevida")
    retrieval = AsyncMock(side_effect=AssertionError("RAG não pode ser lido antes do gate"))
    monkeypatch.setattr(
        "app.services.klm_agent_service.KnowledgeService.retrieve_hybrid",
        retrieval,
    )

    response = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(
            matter,
            thread,
            agent="sdr",
            idempotencyKey="frozen-area-before-rag",
        ),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "AREA_FROZEN"
    retrieval.assert_not_awaited()
    event = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.event_type == "agent_governance_denied",
            KlmAuditEvent.entity_id == UUID(matter["id"]),
        )
    )
    assert event is not None
    assert event.after_data["code"] == "AREA_FROZEN"
    assert event.after_data["agentKey"] == "sdr"


@pytest.mark.asyncio
async def test_specialized_subagent_executes_only_for_its_exact_product(client):
    _, medication_matter, medication_thread = await _case(
        client,
        name="Produto correto",
        object_name="Pembrolizumabe",
        health_subtheme="medicamento_alto_custo",
    )
    _, home_care_matter, home_care_thread = await _case(
        client,
        name="Produto distinto",
        object_name="Home care 24h",
        health_subtheme="home_care",
    )

    allowed = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(
            medication_matter,
            medication_thread,
            agent="saude_medicamentos",
            idempotencyKey="specialized-exact-product",
        ),
    )
    denied = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(
            home_care_matter,
            home_care_thread,
            agent="saude_medicamentos",
            idempotencyKey="specialized-wrong-product",
        ),
    )

    assert allowed.status_code == 201, allowed.text
    governance = allowed.json()["runs"][0]["outputPayload"]["governance"]
    assert governance["hierarchy_path"] == [
        "orquestrador",
        "juridico_saude",
        "saude_medicamentos",
    ]
    assert governance["product_key"] == "medicamento_alto_custo"
    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] == "AGENT_PRODUCT_SCOPE_MISMATCH"


@pytest.mark.asyncio
async def test_thread_must_belong_to_same_matter(client):
    _, matter_a, _ = await _case(client, name="Ana A.")
    _, _, thread_b = await _case(client, name="Bruna B.", object_name="Home Care")
    response = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(matter_a, thread_b),
    )
    assert response.status_code == 404
    assert "não pertence" in response.json()["detail"]


@pytest.mark.asyncio
async def test_simple_run_has_zero_tokens_and_external_actions(client):
    _, matter, thread = await _case(client)
    response = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(matter, thread),
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    run = payload["runs"][0]
    assert run["status"] == "completed"
    assert run["aiProvider"] == "klm_simulator"
    assert run["modelUsed"] == "deterministic-health-v1"
    assert run["inputTokens"] == run["outputTokens"] == 0
    assert "costUsd" not in run
    assert payload["externalActionsExecuted"] == 0
    assert run["outputPayload"]["guardrails"]["localOnly"] is True
    context_metadata = run["outputPayload"]["contextMetadata"]
    assert context_metadata["compilerVersion"] == "1.0.0"
    assert len(context_metadata["digest"]) == 64
    assert context_metadata["estimatedTokens"] <= context_metadata["tokenBudget"]
    assert run["inputPayload"]["contextDigest"] == context_metadata["digest"]
    assert payload["runtime"] == "local_simulator"


@pytest.mark.asyncio
async def test_agent_context_reports_public_conversation_as_traceable_rag_source(client):
    _, matter, thread = await _case(client, name="Conversa no RAG")
    public_message = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json={
            "channel": "instagram",
            "direction": "inbound",
            "authorType": "client",
            "authorLabel": "Lead do Instagram",
            "body": "O plano negou meu medicamento e o tratamento começa amanhã.",
            "idempotencyKey": "agent-context-public-conversation",
            "deliveryStatus": "received",
            "messageMetadata": {"origin": "instagram_comment"},
        },
    )
    assert public_message.status_code == 201, public_message.text
    internal_message = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json={
            "channel": "internal",
            "direction": "internal",
            "authorType": "human",
            "authorLabel": "ignorado pelo servidor",
            "body": "Nota reservada: confirmar estratégia antes de responder.",
            "audience": "internal",
            "idempotencyKey": "agent-context-internal-note",
            "deliveryStatus": "received",
        },
    )
    assert internal_message.status_code == 201, internal_message.text

    response = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(
            matter,
            thread,
            idempotencyKey="agent-context-conversation-rag",
        ),
    )

    assert response.status_code == 201, response.text
    output = response.json()["runs"][0]["outputPayload"]
    facts = output["observedFacts"]
    metadata = output["contextMetadata"]
    assert facts["conversationKnowledgeChunkCount"] == 1
    assert facts["internalKnowledgeChunkCount"] == 1
    assert facts["recentConversationMessageCount"] == 1
    assert facts["recentInternalMessageCount"] == 1
    assert metadata["knowledgeSourceCounts"]["conversation_message"] == 1
    assert metadata["knowledgeSourceCounts"]["internal_conversation"] == 1
    assert public_message.json()["id"] in output["evidenceRefs"]
    assert internal_message.json()["id"] not in output["evidenceRefs"]


@pytest.mark.asyncio
async def test_auditor_agent_scans_conversation_and_registers_unanswered_lead(client):
    _, matter, thread = await _case(client, name="Lead auditado")
    inbound = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json={
            "channel": "whatsapp",
            "direction": "inbound",
            "authorType": "client",
            "authorLabel": "Lead auditado",
            "body": "Ainda estou aguardando uma resposta sobre a negativa.",
            "occurredAt": "2026-07-19T12:00:00Z",
            "idempotencyKey": "auditor-unanswered-lead-message",
            "deliveryStatus": "received",
        },
    )
    assert inbound.status_code == 201, inbound.text

    response = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(
            matter,
            thread,
            agent="auditor",
            idempotencyKey="auditor-agent-scan",
        ),
    )

    assert response.status_code == 201, response.text
    output = response.json()["runs"][0]["outputPayload"]
    scan = output["auditScan"]
    assert scan["inspectedThreads"] == 1
    assert scan["openCount"] >= 1
    assert any(
        finding["findingType"] == "unanswered_lead" and finding["messageId"] == inbound.json()["id"]
        for finding in scan["findings"]
    )
    assert "conversa canônica" in output["summary"]


class _FakeGateway:
    def __init__(self, *, error=None, budget_ledger=None, before_result=None):
        self.error = error
        self.calls = []
        self.budget_ledger = budget_ledger or InMemoryDailyBudgetLedger()
        self.before_result = before_result

    async def generate(self, request, *, on_reservation=None):
        self.calls.append(request)
        if self.error:
            raise self.error
        if self.before_result:
            await self.before_result(request)
        return AICompletionResult(
            text='{"summary":"Análise API","recommendation":"Revisar internamente",'
            '"missingInformation":[],"riskFlags":["prazo"],"confidence":0.91}',
            input_tokens=120,
            output_tokens=45,
            cached_input_tokens=20,
            cost_usd=0.0042,
            cost_known=True,
            model_used="model-test",
            provider="openai",
            request_id="request-test",
            provider_request_id="provider-test",
            price_catalog_version="prices-test",
            route_catalog_version="routes-test",
            structured_data={
                "summary": "Análise API baseada nas evidências disponíveis.",
                "recommendation": "Revisar internamente antes de qualquer contato.",
                "missingInformation": [],
                "riskFlags": ["prazo"],
                "confidence": 0.91,
            },
        )


@pytest.mark.asyncio
async def test_approved_api_mode_persists_provider_usage_without_external_action(
    client, db_session, monkeypatch
):
    gateway = _FakeGateway()
    monkeypatch.setattr("app.services.klm_agent_service.settings.KLM_MODEL_APIS_ENABLED", True)
    monkeypatch.setattr("app.services.klm_agent_service.settings.KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr("app.services.klm_agent_service.get_model_gateway", lambda: gateway)
    _, matter, thread = await _case(client)
    persisted_matter = await db_session.get(Matter, UUID(matter["id"]))
    assert persisted_matter is not None
    db_session.add(
        OmnichannelMessage(
            workspace_id=persisted_matter.workspace_id,
            thread_id=UUID(thread["id"]),
            matter_id=persisted_matter.id,
            channel=OmnichannelChannel.whatsapp,
            direction=MessageDirection.inbound,
            author_type=MessageAuthorType.client,
            author_label="Cliente",
            body="A operadora negou o medicamento por escrito.",
            idempotency_key="context-client-message",
            delivery_status=MessageDeliveryStatus.received,
        )
    )

    _, foreign_matter, foreign_thread = await _case(
        client,
        name="Cliente Estrangeiro",
        object_name="Segredo de outro caso",
    )
    persisted_foreign_matter = await db_session.get(Matter, UUID(foreign_matter["id"]))
    assert persisted_foreign_matter is not None
    db_session.add(
        OmnichannelMessage(
            workspace_id=persisted_foreign_matter.workspace_id,
            thread_id=UUID(foreign_thread["id"]),
            matter_id=persisted_foreign_matter.id,
            channel=OmnichannelChannel.whatsapp,
            direction=MessageDirection.inbound,
            author_type=MessageAuthorType.client,
            author_label="Outro cliente",
            body="CONTEUDO-QUE-NAO-PODE-VAZAR",
            idempotency_key=f"foreign-context:{foreign_matter['id']}",
            delivery_status=MessageDeliveryStatus.received,
        )
    )
    await db_session.flush()

    response = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(matter, thread, idempotencyKey="api-first-success"),
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    run = payload["runs"][0]
    assert payload["runtime"] == "model_api"
    assert run["status"] == "completed"
    assert run["aiProvider"] == "openai"
    assert run["modelUsed"] == "model-test"
    assert run["inputTokens"] == 120
    assert run["outputTokens"] == 45
    assert "costUsd" not in run
    assert run["outputPayload"]["summary"].startswith("Análise API")
    assert run["outputPayload"]["guardrails"]["modelCanExecuteTools"] is False
    assert run["outputPayload"]["guardrails"]["externalActionsExecuted"] == 0
    assert gateway.calls[0].workspace_id
    assert gateway.calls[0].agent_name == "sdr"
    compiled_prompt = gateway.calls[0].messages[1].content
    assert "A operadora negou o medicamento por escrito." in compiled_prompt
    assert "CONTEUDO-QUE-NAO-PODE-VAZAR" not in compiled_prompt
    assert run["outputPayload"]["contextMetadata"]["recentItemsIncluded"] == 1
    usage = await db_session.scalar(select(PlatformTokenUsage))
    assert usage is not None
    assert usage.total_input_tokens == 120
    assert usage.total_output_tokens == 45
    assert int(await db_session.scalar(select(func.count(ExternalActionIntent.id))) or 0) == 0


@pytest.mark.asyncio
async def test_manual_edit_during_model_run_blocks_stale_crm_write(
    client,
    db_session,
    monkeypatch,
):
    _, matter, thread = await _case(client, name="Revisão manual concorrente")
    matter_id = UUID(matter["id"])

    async def apply_manual_change(_request):
        persisted = await db_session.get(Matter, matter_id)
        assert persisted is not None
        persisted.manual_context_revision = int(persisted.manual_context_revision or 0) + 1
        persisted.manual_context_updated_at = datetime.now(timezone.utc)
        persisted.next_action = "Instrução humana mais recente; não substituir."
        await db_session.flush()

    gateway = _FakeGateway(before_result=apply_manual_change)
    monkeypatch.setattr("app.services.klm_agent_service.settings.KLM_MODEL_APIS_ENABLED", True)
    monkeypatch.setattr("app.services.klm_agent_service.settings.KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr("app.services.klm_agent_service.get_model_gateway", lambda: gateway)

    response = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(
            matter,
            thread,
            agent="crm",
            idempotencyKey="crm-stale-manual-revision",
        ),
    )

    assert response.status_code == 201, response.text
    output = response.json()["runs"][0]["outputPayload"]
    assert output["guardrails"]["staleContextBlocked"] is True
    assert output["governance"]["manualContextStillCurrent"] is False
    assert output["crmUpdate"]["status"] == "blocked_stale_context"
    assert output["crmUpdate"]["changedFields"] == []
    persisted = await db_session.get(Matter, matter_id)
    assert persisted is not None
    assert persisted.next_action == "Instrução humana mais recente; não substituir."
    assert persisted.sdr_qualification in (None, {})
    blocked = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.event_type == "agent_stale_context_blocked",
            KlmAuditEvent.entity_id == matter_id,
        )
    )
    assert blocked is not None
    assert blocked.after_data["externalActionsExecuted"] == 0


@pytest.mark.asyncio
async def test_api_failure_is_audited_without_deterministic_fallback(client, monkeypatch):
    gateway = _FakeGateway(
        error=ProviderCallError(
            "provider indisponível",
            request_started=False,
            billing_unknown=False,
        )
    )
    monkeypatch.setattr("app.services.klm_agent_service.settings.KLM_MODEL_APIS_ENABLED", True)
    monkeypatch.setattr("app.services.klm_agent_service.settings.KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr("app.services.klm_agent_service.get_model_gateway", lambda: gateway)
    _, matter, thread = await _case(client)

    response = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(matter, thread, idempotencyKey="api-first-failure"),
    )

    assert response.status_code == 201, response.text
    run = response.json()["runs"][0]
    assert run["status"] == "failed"
    assert run["aiProvider"] == "api_gateway"
    assert run["outputPayload"]["errorCode"] == "ProviderCallError"
    assert run["outputPayload"]["guardrails"]["deterministicFallbackUsed"] is False
    assert "SDR Saúde confirmou" not in run["outputPayload"]["summary"]


@pytest.mark.asyncio
async def test_uncertain_provider_billing_is_durable_reconcilable_and_not_retried(
    client, db_session, monkeypatch
):
    error = ProviderCallError(
        "timeout sanitizado",
        request_started=True,
        billing_unknown=True,
        provider_request_id="provider-uncertain-42",
    )
    error.provider = "openai"
    error.model = "model-test"
    error.route_key = "openai:model-test"
    error.gateway_request_id = "gateway-42"
    error.estimated_cost_usd = "0.0075"
    error.estimated_input_tokens = 180
    error.estimated_output_tokens = 90
    error.price_catalog_version = "prices-test"
    error.route_catalog_version = "routes-test"
    gateway = _FakeGateway(error=error)
    monkeypatch.setattr("app.services.klm_agent_service.settings.KLM_MODEL_APIS_ENABLED", True)
    monkeypatch.setattr("app.services.klm_agent_service.settings.KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr("app.services.klm_agent_service.get_model_gateway", lambda: gateway)
    _, matter, thread = await _case(client)
    request = _run_payload(
        matter,
        thread,
        idempotencyKey="api-uncertain-billing",
    )

    first = await client.post("/api/v1/klm/agents/runs", json=request)
    second = await client.post("/api/v1/klm/agents/runs", json=request)

    assert first.status_code == second.status_code == 201
    first_run = first.json()["runs"][0]
    second_run = second.json()["runs"][0]
    assert first_run["id"] == second_run["id"]
    assert second.json()["deduplicated"] is True
    assert len(gateway.calls) == 1
    assert first_run["status"] == "failed"
    assert first_run["aiProvider"] == "openai"
    assert first_run["modelUsed"] == "model-test"
    assert "costUsd" not in first_run
    reconciliation = first_run["outputPayload"]["billingReconciliation"]
    assert reconciliation == {
        "status": "reconciliation_required",
        "requestStarted": True,
        "reconciliationRequired": True,
        "estimatedInputTokens": 180,
        "estimatedOutputTokens": 90,
        "provider": "openai",
        "model": "model-test",
        "routeKey": "openai:model-test",
        "gatewayRequestId": "gateway-42",
        "providerRequestId": "provider-uncertain-42",
        "priceCatalogVersion": "prices-test",
        "routeCatalogVersion": "routes-test",
    }
    assert "Não repetir" in first_run["outputPayload"]["recommendation"]
    assert first_run["outputPayload"]["modelCheckpoint"]["postProcessing"] == "completed"
    assert int(await db_session.scalar(select(func.count(AgentRun.id))) or 0) == 1
    assert await db_session.scalar(select(PlatformTokenUsage)) is None


@pytest.mark.asyncio
async def test_admin_reconciles_uncertain_provider_charge_and_replay_is_idempotent(
    client, db_session, auth_context, platform_auth_context, monkeypatch
):
    _, matter, thread = await _case(client)
    persisted_matter = await db_session.get(Matter, matter["id"])
    assert persisted_matter is not None
    workspace_id = str(persisted_matter.workspace_id)
    ledger = InMemoryDailyBudgetLedger()
    reservation = await ledger.reserve(workspace_id, Decimal("0.0075"), Decimal("10"))
    error = ProviderCallError(
        "timeout sanitizado",
        request_started=True,
        billing_unknown=True,
        provider_request_id="provider-reconcile-42",
    )
    error.provider = "openai"
    error.model = "model-test"
    error.route_key = "openai:model-test"
    error.gateway_request_id = "gateway-reconcile-42"
    error.estimated_cost_usd = "0.0075"
    error.estimated_input_tokens = 180
    error.estimated_output_tokens = 90
    error.price_catalog_version = "prices-test"
    error.route_catalog_version = "routes-test"
    error.budget_reservation_id = reservation.reservation_id
    error.budget_reservation_day = reservation.day
    error.budget_reserved_amount_usd = str(reservation.amount_usd)
    gateway = _FakeGateway(error=error, budget_ledger=ledger)
    monkeypatch.setattr("app.services.klm_agent_service.settings.KLM_MODEL_APIS_ENABLED", True)
    monkeypatch.setattr("app.services.klm_agent_service.settings.KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr("app.services.klm_agent_service.get_model_gateway", lambda: gateway)

    failed = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(
            matter,
            thread,
            idempotencyKey="api-billing-reconciliation",
        ),
    )
    assert failed.status_code == 201, failed.text
    run_id = failed.json()["runs"][0]["id"]
    request = {
        "decision": "charged",
        "actualCostUsd": "0.0062",
        "evidenceReference": "invoice-provider-2026-07-16",
        "providerRequestId": "provider-reconcile-42",
        "reason": "Cobrança confirmada no extrato do provider.",
    }

    denied = await client.post(
        f"/api/v1/platform/workspaces/{workspace_id}/agent-runs/{run_id}/billing-reconciliation",
        json=request,
    )
    assert denied.status_code == 403
    _use_platform_identity(platform_auth_context)

    reconciled = await client.post(
        f"/api/v1/platform/workspaces/{workspace_id}/agent-runs/{run_id}/billing-reconciliation",
        json=request,
    )
    replay = await client.post(
        f"/api/v1/platform/workspaces/{workspace_id}/agent-runs/{run_id}/billing-reconciliation",
        json=request,
    )

    assert reconciled.status_code == replay.status_code == 200
    payload = reconciled.json()
    billing = payload["outputPayload"]["billingReconciliation"]
    assert billing["status"] == "reconciled_charged"
    assert billing["reconciliationRequired"] is False
    assert "actualCostUsd" not in billing
    assert "budgetTotalAfterUsd" not in billing
    assert billing["ledgerReservationStatus"] == "settled"
    assert billing["evidenceReference"].startswith("billing_evidence:")
    assert "costUsd" not in payload
    assert await ledger.spent(workspace_id, reservation.day) == Decimal("0.0062")
    assert await ledger.has_reservation(reservation) is False
    usage = await db_session.scalar(select(PlatformTokenUsage))
    assert usage is not None
    assert usage.total_runs == 1
    assert usage.total_cost_usd == Decimal("0.0062")
    conflicting = await client.post(
        f"/api/v1/platform/workspaces/{workspace_id}/agent-runs/{run_id}/billing-reconciliation",
        json={**request, "decision": "not_charged", "actualCostUsd": "0"},
    )
    assert conflicting.status_code == 409
    audits = list(
        (
            await db_session.scalars(
                select(KlmAuditEvent).where(
                    KlmAuditEvent.event_type == "agent_model_billing_reconciled"
                )
            )
        ).all()
    )
    assert len(audits) == 1


@pytest.mark.asyncio
async def test_billing_reconciliation_recovers_after_ledger_settled_before_db_commit(
    client,
    db_session,
    auth_context,
    platform_auth_context,
    monkeypatch,
):
    _, matter, _ = await _case(client, name="Ledger Recovery")
    persisted_matter = await db_session.get(Matter, matter["id"])
    assert persisted_matter is not None
    workspace_id = str(persisted_matter.workspace_id)
    ledger = InMemoryDailyBudgetLedger()
    reservation = await ledger.reserve(
        workspace_id,
        Decimal("0.0075"),
        Decimal("10"),
    )
    await ledger.settle(reservation, Decimal("0.0062"))
    run = AgentRun(
        workspace_id=persisted_matter.workspace_id,
        matter_id=persisted_matter.id,
        agent_name="sdr",
        trigger_source="test:billing-ledger-recovery",
        status=AgentRunStatus.failed,
        ai_provider="openai",
        model_used="model-test",
        input_payload={},
        output_payload={
            "billingReconciliation": {
                "status": "reconciliation_required",
                "requestStarted": True,
                "reconciliationRequired": True,
                "providerRequestId": "provider-ledger-recovery",
                "reservationId": reservation.reservation_id,
                "reservationDay": reservation.day,
                "reservedAmountUsd": str(reservation.amount_usd),
                "estimatedCostUsd": str(reservation.amount_usd),
            },
            "modelCheckpoint": {"status": "execution_uncertain"},
        },
        error_message="Worker perdido após a resolução do ledger.",
    )
    db_session.add(run)
    await db_session.commit()
    gateway = _FakeGateway(budget_ledger=ledger)
    monkeypatch.setattr(
        "app.services.klm_agent_service.get_model_gateway",
        lambda: gateway,
    )
    _use_platform_identity(platform_auth_context)

    response = await client.post(
        f"/api/v1/platform/workspaces/{workspace_id}/agent-runs/{run.id}/billing-reconciliation",
        json={
            "decision": "charged",
            "actualCostUsd": "0.0062",
            "evidenceReference": "provider-ledger-recovery-evidence",
            "providerRequestId": "provider-ledger-recovery",
            "reason": "Recuperar commit do banco após resolução atômica do ledger.",
        },
    )

    assert response.status_code == 200, response.text
    billing = response.json()["outputPayload"]["billingReconciliation"]
    assert billing["ledgerReservationStatus"] == "already_settled"
    assert "actualCostUsd" not in billing
    assert await ledger.spent(workspace_id, reservation.day) == Decimal("0.0062")
    usage = await db_session.scalar(select(PlatformTokenUsage))
    assert usage is not None
    assert usage.total_runs == 1


@pytest.mark.asyncio
async def test_postprocessing_failure_never_repeats_billable_model_call(
    client, db_session, monkeypatch
):
    gateway = _FakeGateway()
    monkeypatch.setattr("app.services.klm_agent_service.settings.KLM_MODEL_APIS_ENABLED", True)
    monkeypatch.setattr("app.services.klm_agent_service.settings.KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr("app.services.klm_agent_service.get_model_gateway", lambda: gateway)

    async def fail_after_model(*args, **kwargs):
        raise RuntimeError("falha local depois do modelo")

    monkeypatch.setattr(
        "app.services.klm_agent_service.KlmAgentService._append_internal_message",
        fail_after_model,
    )
    _, matter, thread = await _case(client)
    request = _run_payload(
        matter,
        thread,
        idempotencyKey="api-postprocessing-failure",
    )

    first = await client.post("/api/v1/klm/agents/runs", json=request)
    second = await client.post("/api/v1/klm/agents/runs", json=request)

    assert first.status_code == second.status_code == 201
    first_run = first.json()["runs"][0]
    second_run = second.json()["runs"][0]
    assert first_run["id"] == second_run["id"]
    assert second.json()["deduplicated"] is True
    assert len(gateway.calls) == 1
    assert first_run["status"] == "failed"
    assert first_run["inputTokens"] == 120
    assert "costUsd" not in first_run
    assert first_run["outputPayload"]["modelCheckpoint"] == {
        "status": "completed",
        "postProcessing": "failed",
        "errorCode": "RuntimeError",
    }
    assert int(await db_session.scalar(select(func.count(AgentRun.id))) or 0) == 1
    usage = await db_session.scalar(select(PlatformTokenUsage))
    assert usage is not None
    assert usage.total_runs == 1


@pytest.mark.asyncio
async def test_health_simulation_never_consumes_model_api(client, monkeypatch):
    gateway = _FakeGateway(error=AssertionError("simulação não deve chamar modelo"))
    monkeypatch.setattr("app.services.klm_agent_service.settings.KLM_MODEL_APIS_ENABLED", True)
    monkeypatch.setattr("app.services.klm_agent_service.settings.KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr("app.services.klm_agent_service.get_model_gateway", lambda: gateway)
    _, matter, thread = await _case(client)

    response = await client.post(
        "/api/v1/klm/agents/simulations/health-intake",
        json={
            "matterId": matter["id"],
            "threadId": thread["id"],
            "idempotencyKey": "free-simulation",
        },
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["runtime"] == "local_simulator"
    assert gateway.calls == []
    assert all(run["aiProvider"] == "klm_simulator" for run in payload["runs"])
    queued_run = next(run for run in payload["runs"] if run["agentName"] == "orquestrador" and run["status"] == "waiting_human")
    assert queued_run["inputPayload"]["governance"]["requested_by_agent_key"] is None
    assert queued_run["inputPayload"]["governance"]["delegation_depth"] == 0
    assert queued_run["inputPayload"]["governance"]["policy_digest_algorithm"] == "sha256"
    assert len(queued_run["inputPayload"]["governance"]["policy_digest"]) == 64
    assert queued_run["inputPayload"]["governance"]["hierarchy_path"] == ["orquestrador"]


@pytest.mark.asyncio
async def test_run_idempotency_does_not_duplicate_run_or_internal_message(client, db_session):
    _, matter, thread = await _case(client)
    payload = _run_payload(matter, thread, idempotencyKey="agent-idempotent")
    first = await client.post("/api/v1/klm/agents/runs", json=payload)
    second = await client.post("/api/v1/klm/agents/runs", json=payload)
    assert first.status_code == second.status_code == 201
    assert second.json()["deduplicated"] is True
    assert first.json()["runs"][0]["id"] == second.json()["runs"][0]["id"]
    assert int(await db_session.scalar(select(func.count(AgentRun.id))) or 0) == 1
    assert int(await db_session.scalar(select(func.count(OmnichannelMessage.id))) or 0) == 1


@pytest.mark.asyncio
async def test_secretary_requests_human_and_orchestrator_queues_attendant(
    client,
    db_session,
    auth_context,
):
    _, matter, thread = await _case(client)
    db_session.add_all(
        [
            OmnichannelMessage(
                workspace_id=auth_context.workspace_id,
                thread_id=UUID(thread["id"]),
                matter_id=UUID(matter["id"]),
                channel=OmnichannelChannel.whatsapp,
                direction=MessageDirection.inbound,
                author_type=MessageAuthorType.client,
                author_label="Cliente",
                body="Precisamos revisar um contrato comercial que ameaça a operação da empresa.",
                idempotency_key="handoff-client-context",
                delivery_status=MessageDeliveryStatus.received,
                occurred_at=datetime.now(timezone.utc),
            ),
            OmnichannelMessage(
                workspace_id=auth_context.workspace_id,
                thread_id=UUID(thread["id"]),
                matter_id=UUID(matter["id"]),
                channel=OmnichannelChannel.whatsapp,
                direction=MessageDirection.outbound,
                author_type=MessageAuthorType.agent,
                author_label="SDR",
                body="Podemos agendar uma reunião online depois da análise dos documentos.",
                idempotency_key="handoff-agent-commitment",
                delivery_status=MessageDeliveryStatus.received,
                occurred_at=datetime.now(timezone.utc),
            ),
        ]
    )
    await db_session.flush()
    response = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(
            matter,
            thread,
            agent="secretaria",
            requestHuman=True,
            idempotencyKey="secretary-hermes",
        ),
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    orchestrator_run = next(run for run in payload["runs"] if run["agentName"] == "orquestrador")
    assert [run["agentName"] for run in payload["runs"]] == ["secretaria", "orquestrador"]
    assert [run["status"] for run in payload["runs"]] == ["completed", "waiting_human"]
    assert payload["runs"][0]["outputPayload"]["handoffTo"] == "orquestrador"
    assert orchestrator_run["outputPayload"]["handoffTo"] == "fila_humana"
    assert orchestrator_run["outputPayload"]["humanQueue"]["queue"] == "human"
    dossier = orchestrator_run["outputPayload"]["handoffDossier"]
    assert dossier["schemaVersion"] == "klm.handoff-dossier.v1"
    assert dossier["closingProbability"] == {
        "label": "alta",
        "score": 80,
        "source": "matter.lead_score",
    }
    assert dossier["painSummary"] == [
        "Precisamos revisar um contrato comercial que ameaça a operação da empresa."
    ]
    assert dossier["commitments"] == [
        "Podemos agendar uma reunião online depois da análise dos documentos."
    ]
    assert len(dossier["evidenceRefs"]) == 2
    assert payload["approvalId"] is None
    approvals = list((await db_session.execute(select(ApprovalQueueItem))).scalars().all())
    assert len(approvals) == 0
    persisted_thread = await db_session.get(OmnichannelThread, UUID(thread["id"]))
    assert persisted_thread is not None
    assert persisted_thread.thread_metadata["humanHandoff"]["dossier"] == dossier
    messages = list(
        (
            await db_session.scalars(
                select(OmnichannelMessage).where(
                    OmnichannelMessage.workspace_id == auth_context.workspace_id,
                    OmnichannelMessage.thread_id == UUID(thread["id"]),
                )
            )
        ).all()
    )
    dossier_message = next(
        message
        for message in messages
        if message.message_metadata.get("messageKind") == "instant_handoff_dossier"
    )
    assert dossier_message.message_metadata["handoffDossier"] == dossier
    assert "Dossiê Instantâneo do handoff" in dossier_message.body


@pytest.mark.asyncio
async def test_direct_hermes_request_is_rejected_and_must_pass_through_secretary(client):
    _, matter, thread = await _case(client)
    response = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(
            matter,
            thread,
            agent="hermes",
            requestHuman=True,
            idempotencyKey="direct-hermes",
        ),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "OWNER_BRIDGE_DELEGATOR_REQUIRED"


@pytest.mark.asyncio
async def test_orchestrator_has_waiting_human_status_and_queues_conversation(client):
    _, matter, thread = await _case(client)
    response = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(
            matter,
            thread,
            agent="peticao",
            requestHuman=True,
            idempotencyKey="petition-hermes",
        ),
    )
    payload = response.json()
    assert [run["agentName"] for run in payload["runs"]] == ["peticao", "orquestrador"]
    waiting = [run for run in payload["runs"] if run["status"] == "waiting_human"]
    assert [run["agentName"] for run in waiting] == ["orquestrador"]
    governance = [run["outputPayload"]["governance"] for run in payload["runs"]]
    assert [item["requested_by_agent_key"] for item in governance] == [None, None]
    assert [item["delegation_depth"] for item in governance] == [0, 0]
    assert all(run["aiProvider"] == "klm_simulator" for run in payload["runs"])


@pytest.mark.asyncio
async def test_petition_never_protocols_and_creates_no_document(client, db_session):
    _, matter, thread = await _case(client)
    response = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(
            matter,
            thread,
            agent="peticao",
            requestHuman=True,
            idempotencyKey="petition-never-protocols",
        ),
    )
    petition = response.json()["runs"][0]
    assert petition["outputPayload"]["guardrails"]["neverProtocols"] is True
    assert petition["outputPayload"]["handoffTo"] == "orquestrador"
    assert petition["outputPayload"]["humanEscalationPath"] == ["peticao", "orquestrador", "humano"]
    assert int(await db_session.scalar(select(func.count(Document.id))) or 0) == 0


@pytest.mark.asyncio
async def test_health_simulation_executes_orchestrated_six_stage_chain(client, db_session):
    _, matter, thread = await _case(client)
    response = await client.post(
        "/api/v1/klm/agents/simulations/health-intake",
        json={
            "matterId": matter["id"],
            "threadId": thread["id"],
            "idempotencyKey": "health-chain",
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert [run["agentName"] for run in payload["runs"]] == [
        "orquestrador",
        "sdr",
        "juridico",
        "crm",
        "secretaria",
        "orquestrador",
    ]
    orchestration = payload["runs"][0]["outputPayload"]["orchestrationPlan"]
    assert orchestration["contextRevisionChecked"] is True
    assert orchestration["manualOverridesTakePrecedence"] is True
    assert orchestration["humanEscalationBridge"] == "orquestrador"
    governance = [run["outputPayload"]["governance"] for run in payload["runs"]]
    assert governance[0]["requested_by_agent_key"] is None
    assert [item["requested_by_agent_key"] for item in governance[1:5]] == [
        "orquestrador",
        "orquestrador",
        "orquestrador",
        "orquestrador",
    ]
    assert [item["delegation_depth"] for item in governance[1:5]] == [1, 1, 1, 1]
    assert governance[5]["requested_by_agent_key"] is None
    assert governance[5]["delegation_depth"] == 0
    assert payload["runs"][-1]["status"] == "waiting_human"
    assert payload["externalActionsExecuted"] == 0
    assert int(await db_session.scalar(select(func.count(ExternalActionIntent.id))) or 0) == 0
    assert int(await db_session.scalar(select(func.count(OmnichannelMessage.id))) or 0) == 6


@pytest.mark.asyncio
async def test_generic_simulation_routes_exact_area_product_hierarchy(client, db_session):
    _, matter, thread = await _case(
        client,
        name="Cadeia por produto",
        object_name="Pembrolizumabe",
        health_subtheme="medicamento_alto_custo",
    )
    response = await client.post(
        "/api/v1/klm/agents/simulations/intake",
        json={
            "matterId": matter["id"],
            "threadId": thread["id"],
            "idempotencyKey": "generic-product-chain",
        },
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert [run["agentName"] for run in payload["runs"]] == [
        "orquestrador",
        "sdr_saude",
        "juridico_saude",
        "saude_medicamentos",
        "crm",
        "secretaria",
        "orquestrador",
    ]
    governance = [run["outputPayload"]["governance"] for run in payload["runs"]]
    assert [item["requested_by_agent_key"] for item in governance] == [
        None,
        "orquestrador",
        "orquestrador",
        "juridico_saude",
        "orquestrador",
        "orquestrador",
        None,
    ]
    assert [item["delegation_depth"] for item in governance] == [0, 1, 1, 2, 1, 1, 0]
    assert governance[3]["hierarchy_path"] == [
        "orquestrador",
        "juridico_saude",
        "saude_medicamentos",
    ]
    assert governance[3]["area_key"] == "saude_suplementar"
    assert governance[3]["product_key"] == "medicamento_alto_custo"
    assert all(item["workspace_id"] == governance[0]["workspace_id"] for item in governance)
    assert all(item["client_id"] == governance[0]["client_id"] for item in governance)
    assert payload["externalActionsExecuted"] == 0
    assert payload["runs"][-1]["status"] == "waiting_human"
    assert int(await db_session.scalar(select(func.count(ExternalActionIntent.id))) or 0) == 0
    assert int(await db_session.scalar(select(func.count(OmnichannelMessage.id))) or 0) == 7


@pytest.mark.asyncio
async def test_health_simulation_replay_is_fully_idempotent(client, db_session):
    _, matter, thread = await _case(client)
    payload = {
        "matterId": matter["id"],
        "threadId": thread["id"],
        "idempotencyKey": "health-chain-idem",
    }
    first = await client.post("/api/v1/klm/agents/simulations/health-intake", json=payload)
    second = await client.post("/api/v1/klm/agents/simulations/health-intake", json=payload)
    assert first.status_code == second.status_code == 201
    assert second.json()["deduplicated"] is True
    assert [run["id"] for run in first.json()["runs"]] == [
        run["id"] for run in second.json()["runs"]
    ]
    assert int(await db_session.scalar(select(func.count(AgentRun.id))) or 0) == 6
    assert int(await db_session.scalar(select(func.count(ApprovalQueueItem.id))) or 0) == 0


@pytest.mark.asyncio
async def test_crm_updates_only_evidence_backed_fields_and_audits(client, db_session):
    _, matter, thread = await _case(client)
    response = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(matter, thread, agent="crm", idempotencyKey="crm-evidence"),
    )
    assert response.status_code == 201
    update = response.json()["runs"][0]["outputPayload"]["crmUpdate"]
    assert update["changedFields"] == ["leadScore", "nextAction", "sdrQualification"]
    assert update["pendingLegalReview"] is True
    persisted = await db_session.get(Matter, matter["id"])
    assert persisted.sdr_qualification["source"] == "klm_crm_evidence_engine"
    assert persisted.sdr_qualification["pendingLegalReview"] is True
    audit = await db_session.scalar(
        select(KlmAuditEvent).where(KlmAuditEvent.event_type == "agent_crm_fields_updated")
    )
    assert audit is not None


@pytest.mark.asyncio
async def test_internal_agent_messages_are_private_and_attributed(client):
    _, matter, thread = await _case(client)
    await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(matter, thread, agent="sdr", idempotencyKey="internal-attribution"),
    )
    messages = (await client.get(f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages")).json()
    assert len(messages) == 1
    assert messages[0]["direction"] == "internal"
    assert messages[0]["authorType"] == "agent"
    assert messages[0]["authorLabel"] == "SDR Jurídico"
    assert messages[0]["messageMetadata"]["visibility"] == "internal_only"
    assert messages[0]["messageMetadata"]["externalActionsExecuted"] == 0


@pytest.mark.asyncio
async def test_agent_run_list_can_be_scoped_to_matter(client):
    _, matter_a, thread_a = await _case(client, name="Ana A.")
    _, matter_b, thread_b = await _case(client, name="Bruna B.", object_name="Home Care")
    await client.post(
        "/api/v1/klm/agents/runs", json=_run_payload(matter_a, thread_a, idempotencyKey="matter-a")
    )
    await client.post(
        "/api/v1/klm/agents/runs", json=_run_payload(matter_b, thread_b, idempotencyKey="matter-b")
    )
    scoped = (await client.get(f"/api/v1/klm/agents/runs?matter_id={matter_a['id']}")).json()
    assert len(scoped) == 1
    assert scoped[0]["matterId"] == matter_a["id"]


@pytest.mark.asyncio
async def test_reports_local_mode_never_initializes_ai_provider(client, monkeypatch):
    provider_factory = Mock(side_effect=AssertionError("AI provider must stay offline"))
    monkeypatch.setattr("app.services.reports_service.get_ai_provider", provider_factory)
    response = await client.get("/api/v1/reports/metrics")
    assert response.status_code == 200, response.text
    provider_factory.assert_not_called()


@pytest.mark.asyncio
async def test_delegated_runs_carry_a_structured_handoff_summary(client, db_session):
    """Harmonização de handoff: every agent-to-agent delegation attaches a
    structured resumo (tese, dossiê, objeções, próximo passo) so the lead
    never has to repeat their story to the next agent in the chain."""
    _, matter, thread = await _case(
        client,
        name="Cadeia com handoff",
        object_name="Pembrolizumabe",
        health_subtheme="medicamento_alto_custo",
    )
    response = await client.post(
        "/api/v1/klm/agents/simulations/intake",
        json={
            "matterId": matter["id"],
            "threadId": thread["id"],
            "idempotencyKey": "handoff-summary-chain",
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()

    governance_by_run = [run["outputPayload"]["governance"] for run in payload["runs"]]

    # The root run (orquestrador, requested_by_agent_key is None) is not a
    # delegation and must carry no handoff summary.
    assert governance_by_run[0]["requested_by_agent_key"] is None
    assert "handoffSummary" not in governance_by_run[0]

    # Every delegated run (requested_by_agent_key set) must carry one.
    delegated = [item for item in governance_by_run if item["requested_by_agent_key"]]
    assert delegated, "expected at least one delegated run in the intake chain"
    for item in delegated:
        summary = item["handoffSummary"]
        assert summary["schemaVersion"] == "agent-handoff-summary-v1"
        assert summary["thesis"]
        assert summary["dossierSummary"]
        assert "nextStep" in summary
        assert isinstance(summary["objectionsHandled"], list)

    # The specialist step (deepest delegation in the chain) should see the
    # case's area in its dossier summary — proving the summary is derived
    # from real case data, not a static placeholder.
    specialist_governance = max(delegated, key=lambda item: item["delegation_depth"])
    assert specialist_governance["area_key"] == "saude_suplementar"
    assert "saude_suplementar" in specialist_governance["handoffSummary"]["dossierSummary"]
