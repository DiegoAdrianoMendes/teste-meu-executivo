from uuid import uuid4

import pytest
from sqlalchemy import select

from app.ai.base import AICompletionResult
from app.models.agent_run import AgentRun
from app.services.agent_inference_service import AgentInferenceService
from app.services.agent_intelligence_adapters import _consensus, _run
from app.services.agent_node_executor import AgentExecutionContext


class _Provider:
    def __init__(self):
        self.calls = []

    async def complete(self, messages, **kwargs):
        self.calls.append(kwargs["agent_name"])
        assert kwargs["workspace_id"]
        return AICompletionResult(
            text='{"ok": true}', input_tokens=4, output_tokens=3, cost_usd=0.12
        )


class _FailingProvider:
    async def complete(self, messages, **kwargs):
        raise RuntimeError("gateway unavailable")


@pytest.mark.asyncio
async def test_inference_provider_fake_returns_structured_accounting():
    result = await AgentInferenceService(_Provider()).execute(
        AgentExecutionContext(uuid4(), "finance", uuid4(), frozenset({"finance:view"})),
        operation="summarize",
        payload={"text": "x"},
        output_schema={"type": "object"},
    )
    assert result.status == "completed"
    assert result.data["text"]


@pytest.mark.asyncio
async def test_inference_rejects_missing_context():
    with pytest.raises(ValueError):
        await AgentInferenceService(_Provider()).execute(
            AgentExecutionContext(None, "finance", None, frozenset()),
            operation="summarize",
            payload={},
        )


@pytest.mark.asyncio
async def test_adapter_without_provider_returns_handoff_state():
    result = await _run(
        AgentExecutionContext(uuid4(), "finance", uuid4(), frozenset()), {}, "summarize"
    )
    assert result.status == "waiting_tool"
    assert result.requires_human is True


@pytest.mark.asyncio
async def test_adapter_gateway_failure_is_visible_to_worker():
    with pytest.raises(RuntimeError, match="gateway unavailable"):
        await _run(
            AgentExecutionContext(uuid4(), "finance", uuid4(), frozenset()),
            {"_provider": _FailingProvider()},
            "summarize",
        )


@pytest.mark.asyncio
async def test_consensus_executes_three_opinions_and_synthesis():
    provider = _Provider()
    result = await _consensus(
        AgentExecutionContext(uuid4(), "finance", uuid4(), frozenset()),
        {"_provider": provider, "question": "q"},
    )
    assert result.status == "completed"
    assert len(provider.calls) == 4
    assert provider.calls[-1].endswith("synthesis")


@pytest.mark.asyncio
async def test_inference_flushes_but_does_not_commit_enclosing_transaction(
    db_session, auth_context
):
    operation = f"atomic-inference-{uuid4()}"
    await AgentInferenceService(_Provider(), db=db_session).execute(
        AgentExecutionContext(
            auth_context.workspace_id,
            "workspace",
            auth_context.workspace_id,
            frozenset({"agents:run"}),
            correlation_id=str(uuid4()),
        ),
        operation=operation,
        payload={"text": "transaction-owned-by-worker"},
    )
    assert await db_session.scalar(
        select(AgentRun.id).where(AgentRun.agent_name == operation)
    ) is not None

    await db_session.rollback()

    assert await db_session.scalar(
        select(AgentRun.id).where(AgentRun.agent_name == operation)
    ) is None
