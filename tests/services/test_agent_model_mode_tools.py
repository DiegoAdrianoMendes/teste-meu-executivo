"""S7-a: tool executors READ-ONLY no model mode dos agentes KLM.

Cobre: tool permitida executa e enriquece o output (provider fake); tool
fora da alçada -> TOOL_SCOPE_DENIED sem execução; loop para em
MAX_MODEL_MODE_TOOL_CALLS.
"""

from uuid import uuid4

import pytest

from app.ai.base import AICompletionResult
from app.services import agent_tool_executors
from app.services.klm_agent_service import (
    AGENT_DEFINITIONS,
    TOOL_PLAN_SCHEMA,
    KlmAgentService,
)


def _agent(key: str):
    return next(agent for agent in AGENT_DEFINITIONS if agent.key == key)


class _ToolPlanFakeGateway:
    """Fake gateway isolado só para a chamada de planejamento de tools."""

    def __init__(self, tool_calls):
        self.tool_calls = tool_calls
        self.calls = []

    async def generate(self, request, *, on_reservation=None):
        self.calls.append(request)
        assert request.response_schema is TOOL_PLAN_SCHEMA
        return AICompletionResult(
            text="{}",
            input_tokens=8,
            output_tokens=4,
            cached_input_tokens=0,
            cost_usd=0.0001,
            cost_known=True,
            model_used="model-test",
            provider="openai",
            request_id=f"toolplan-{uuid4()}",
            provider_request_id="provider-test",
            price_catalog_version="prices-test",
            route_catalog_version="routes-test",
            structured_data={"toolCalls": self.tool_calls},
        )


class _RaisingGateway:
    async def generate(self, request, *, on_reservation=None):
        raise RuntimeError("provider indisponível")


class _Client:
    name = "Cliente Teste"


class _PracticeArea:
    value = "saude_suplementar"


class _Enum:
    def __init__(self, value):
        self.value = value


class _Matter:
    id = uuid4()
    client = _Client()
    case_number = "0000000-00.2026.8.07.0001"
    practice_area = _PracticeArea()
    demand_type = "Negativa de medicamento"
    column_id = _Enum("novo_lead")
    micro_status = _Enum("lead_novo")
    priority = _Enum("alta")
    urgency = _Enum("alta")
    manual_context_revision = 3


class _CompiledContext:
    digest = "digest-test"


def _context():
    return {
        "matter": _Matter(),
        "documents": [],
        "missingDocuments": ["laudo"],
        "recentMessageCount": 4,
        "recentPublicMessageCount": 3,
        "recentInternalMessageCount": 1,
        "retrievalReceipt": {"chunks": 2},
        "knowledgeChunks": [],
        "knowledgeCount": 0,
    }


def test_read_only_registry_only_exposes_safe_read_tools():
    assert agent_tool_executors.is_safe_read_tool("crm_read") is True
    assert agent_tool_executors.is_safe_read_tool("crm_write_guarded") is False
    assert agent_tool_executors.is_safe_read_tool("owner_whatsapp_bridge") is False


def test_execute_tool_reads_from_already_compiled_context():
    context = _context()
    result = agent_tool_executors.execute_tool("crm_read", context)
    assert result["clientName"] == "Cliente Teste"
    assert result["manualContextRevision"] == 3

    processos = agent_tool_executors.execute_tool("processos_read", context)
    assert processos["missingDocuments"] == ["laudo"]

    with pytest.raises(KeyError):
        agent_tool_executors.execute_tool("crm_write_guarded", context)


def test_max_model_mode_tool_calls_is_bounded():
    assert agent_tool_executors.MAX_MODEL_MODE_TOOL_CALLS == 3


@pytest.mark.asyncio
async def test_allowed_tool_executes_and_enriches_output():
    agent = _agent("sdr")  # sdr declara "conversation_rag" -> READ-ONLY
    gateway = _ToolPlanFakeGateway(["conversation_rag"])
    service = KlmAgentService(db=None)
    log = await service._run_model_mode_tool_plan(
        agent=agent,
        context=_context(),
        gateway=gateway,
        workspace_id=uuid4(),
        compiled_context=_CompiledContext(),
        idempotency_key="test-key",
    )
    assert len(gateway.calls) == 1
    assert log == [
        {
            "tool": "conversation_rag",
            "status": "executed",
            "result": {"knowledgeChunkCount": 0, "knowledgeChunks": []},
        }
    ]


@pytest.mark.asyncio
async def test_out_of_scope_tool_is_denied_without_execution():
    agent = _agent("sdr")  # sdr não declara "crm_write_guarded" nem é READ-ONLY
    gateway = _ToolPlanFakeGateway(["crm_write_guarded"])
    service = KlmAgentService(db=None)
    log = await service._run_model_mode_tool_plan(
        agent=agent,
        context=_context(),
        gateway=gateway,
        workspace_id=uuid4(),
        compiled_context=_CompiledContext(),
        idempotency_key="test-key",
    )
    assert log == [
        {"tool": "crm_write_guarded", "status": "denied", "reason": "TOOL_SCOPE_DENIED"}
    ]


@pytest.mark.asyncio
async def test_loop_stops_at_max_tool_calls():
    agent = _agent("sdr")
    # sdr só declara "conversation_rag" como READ-ONLY; simula 5 pedidos da
    # mesma tool para provar que o executor nunca passa de MAX_MODEL_MODE_TOOL_CALLS.
    gateway = _ToolPlanFakeGateway(["conversation_rag"] * 5)
    service = KlmAgentService(db=None)
    log = await service._run_model_mode_tool_plan(
        agent=agent,
        context=_context(),
        gateway=gateway,
        workspace_id=uuid4(),
        compiled_context=_CompiledContext(),
        idempotency_key="test-key",
    )
    assert len(log) == agent_tool_executors.MAX_MODEL_MODE_TOOL_CALLS
    assert all(entry["status"] == "executed" for entry in log)


@pytest.mark.asyncio
async def test_agent_without_read_only_tools_skips_planning_call():
    agent = _agent("orquestrador")
    # Remove qualquer interseção com o registro READ-ONLY para este caso de teste.
    stripped_agent = agent.__class__(**{**agent.__dict__, "tools": ("agent_routing",)})
    gateway = _ToolPlanFakeGateway([])
    service = KlmAgentService(db=None)
    log = await service._run_model_mode_tool_plan(
        agent=stripped_agent,
        context=_context(),
        gateway=gateway,
        workspace_id=uuid4(),
        compiled_context=_CompiledContext(),
        idempotency_key="test-key",
    )
    assert log == []
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_provider_failure_during_planning_degrades_to_no_tools():
    agent = _agent("sdr")
    service = KlmAgentService(db=None)
    log = await service._run_model_mode_tool_plan(
        agent=agent,
        context=_context(),
        gateway=_RaisingGateway(),
        workspace_id=uuid4(),
        compiled_context=_CompiledContext(),
        idempotency_key="test-key",
    )
    assert log == []
