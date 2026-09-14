from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from app.ai.base import AICompletionResult
from app.ai.errors import ProviderCallError
from app.ai.factory import TaskComplexity
from app.models.meta_channels import MetaChannelType
from app.services import conversation_insight_service as conversation_module
from app.services import meta_ai_service as meta_module
from app.services import reports_service as reports_module


class RecordingProvider:
    def __init__(
        self,
        result: AICompletionResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict] = []

    async def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def _assert_draft_2020_strict(schema: dict) -> None:
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)

    def walk(node) -> None:
        if isinstance(node, dict):
            schema_type = node.get("type")
            if isinstance(schema_type, str):
                assert schema_type == schema_type.lower()
            elif isinstance(schema_type, list):
                assert all(item == item.lower() for item in schema_type)
            if schema_type == "object":
                assert node.get("additionalProperties") is False
                assert set(node.get("required", [])) == set(node.get("properties", {}))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)


def _conversation() -> SimpleNamespace:
    return SimpleNamespace(
        workspace_id=uuid4(),
        is_public_thread=False,
        event_type=SimpleNamespace(value="direct_message"),
        follow_status=SimpleNamespace(value="unknown"),
        source_title=None,
        source_url=None,
        queue_status=SimpleNamespace(value="ai"),
        human_control_state=SimpleNamespace(value="copilot"),
        active_flow_label=None,
        participant_name="Lead",
        participant_handle="lead",
        messages=[],
    )


def _matter() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        workspace_id=uuid4(),
        conversations=[],
        column_id=SimpleNamespace(value="entrada"),
        micro_status=SimpleNamespace(value="novo"),
        demand_type="Consumidor",
        lead_source="organico",
        executive_summary="Resumo local",
        next_action="Revisar documentos",
        documents=[],
        campaign_attribution=None,
        revenue_projection=None,
        honorarium_estimate=None,
        sdr_qualification={},
        urgency=SimpleNamespace(value="normal"),
    )


def _conversation_completion() -> AICompletionResult:
    return AICompletionResult(
        text="not-json",
        structured_data={
            "summary": "Resumo estruturado",
            "funnel_signal": "warming_up",
            "close_intent": "medium",
            "document_readiness": "low",
            "urgency_level": "medium",
            "sentiment": "neutral",
            "trust_level": "medium",
            "no_show_risk": "low",
            "objections": [],
            "requested_documents": [],
            "missing_information": ["qualificacao_sdr"],
            "recommended_action": "Continuar triagem",
            "next_best_message": None,
            "estimated_conversion_value": None,
            "closed_signal": False,
            "conversion_platforms": [],
        },
    )


def _prepare_conversation_service(monkeypatch, provider: RecordingProvider):
    matter = _matter()
    db = SimpleNamespace(flush=AsyncMock())
    service = conversation_module.ConversationInsightService(db)
    service._load_matter_context = AsyncMock(return_value=matter)
    service._load_whatsapp_history = AsyncMock(return_value=[])
    service._load_card_conversations = AsyncMock(return_value=[])
    service.get_for_matter = AsyncMock(return_value=None)
    service._upsert_snapshot = AsyncMock(return_value=SimpleNamespace(id=uuid4()))

    knowledge = SimpleNamespace(
        build_context_bundle=AsyncMock(return_value={"chunks": [], "rendered_context": ""}),
        upsert_chunk=AsyncMock(),
    )
    policy = SimpleNamespace(resolve_stage_policy=AsyncMock(return_value={}))
    acquisition = SimpleNamespace(
        build_for_matter=AsyncMock(
            return_value=conversation_module.AcquisitionContextSnapshot(
                card_data={},
                evidence_content=None,
                source_id=f"matter:{matter.id}:acquisition",
                source_version=None,
                query_terms=(),
                event_count=0,
            )
        )
    )
    monkeypatch.setattr(conversation_module, "KnowledgeService", lambda _db: knowledge)
    monkeypatch.setattr(conversation_module, "WorkspacePolicyService", lambda _db: policy)
    monkeypatch.setattr(
        conversation_module,
        "AcquisitionContextService",
        lambda _db: acquisition,
    )
    monkeypatch.setattr(conversation_module, "get_ai_provider", lambda: provider)
    return service, matter


@pytest.mark.asyncio
async def test_conversation_insight_passes_context_and_uses_structured_data(monkeypatch):
    provider = RecordingProvider(result=_conversation_completion())
    service, matter = _prepare_conversation_service(monkeypatch, provider)

    await service.generate_snapshot(matter=matter)

    call = provider.calls[0]
    assert call["workspace_id"] == str(matter.workspace_id)
    assert call["agent_name"] == conversation_module.CONVERSATION_INSIGHT_AGENT_NAME
    _assert_draft_2020_strict(call["response_schema"])
    envelope = json.loads("\n".join(call["messages"][1].content.splitlines()[1:-1]))
    compiled = envelope["data"]["compiled_context"]
    assert compiled["scope"] == {
        "matter_id": str(matter.id),
        "workspace_id": str(matter.workspace_id),
    }
    assert compiled["selection"]["recent_window_limit"] == 12
    saved = service._upsert_snapshot.await_args.kwargs["data"]
    assert saved["summary"] == "Resumo estruturado"


@pytest.mark.asyncio
async def test_conversation_insight_preserves_local_fallback(monkeypatch):
    provider = RecordingProvider(error=RuntimeError("gateway unavailable"))
    service, matter = _prepare_conversation_service(monkeypatch, provider)

    await service.generate_snapshot(matter=matter)

    saved = service._upsert_snapshot.await_args.kwargs["data"]
    assert saved["summary"] == matter.executive_summary
    assert saved["recommended_action"] == matter.next_action


@pytest.mark.asyncio
async def test_conversation_insight_fails_closed_when_billing_is_uncertain(monkeypatch):
    provider = RecordingProvider(
        error=ProviderCallError(
            "timeout after request",
            request_started=True,
            billing_unknown=True,
        )
    )
    service, matter = _prepare_conversation_service(monkeypatch, provider)

    with pytest.raises(ProviderCallError):
        await service.generate_snapshot(matter=matter)

    service._upsert_snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_meta_copilot_passes_context_and_uses_structured_data(monkeypatch):
    conversation = _conversation()
    provider = RecordingProvider(
        result=AICompletionResult(
            text="not-json",
            provider="openai",
            model_used="gpt-test",
            structured_data={
                "summary": "Resumo social",
                "recommended_action": "Responder o lead",
                "reply_suggestions": ["Resposta curta"],
                "suggested_public_reply": False,
            },
        )
    )
    monkeypatch.setattr(meta_module, "get_provider_for_task", lambda _complexity: provider)
    monkeypatch.setattr(meta_module, "get_model_for_task", lambda _complexity: None)

    result = await meta_module.MetaAIService(SimpleNamespace()).generate_copilot(
        channel_type=MetaChannelType.instagram,
        conversation=conversation,
    )

    assert result.summary == "Resumo social"
    call = provider.calls[0]
    assert call["workspace_id"] == str(conversation.workspace_id)
    assert call["agent_name"] == meta_module.META_COPILOT_AGENT_NAME
    _assert_draft_2020_strict(call["response_schema"])
    envelope = json.loads("\n".join(call["messages"][1].content.splitlines()[1:-1]))
    compiled = envelope["data"]["compiled_context"]
    assert compiled["selection"]["recent_window_limit"] == 12
    assert compiled["scope"]["workspace_id"] == str(conversation.workspace_id)


@pytest.mark.asyncio
async def test_meta_compiler_sends_only_twelve_of_twenty_four_recent_messages(monkeypatch):
    conversation = _conversation()
    base_time = datetime(2026, 7, 20, tzinfo=UTC)
    conversation.messages = [
        SimpleNamespace(
            id=uuid4(),
            direction=SimpleNamespace(value="inbound"),
            status=SimpleNamespace(value="delivered"),
            body=f"social-message-{index:02d}",
            timestamp=base_time + timedelta(minutes=index),
        )
        for index in range(meta_module.META_CONTEXT_QUERY_LIMIT)
    ]
    provider = RecordingProvider(
        result=AICompletionResult(
            text="not-json",
            structured_data={
                "summary": "Resumo social",
                "recommended_action": "Responder",
                "reply_suggestions": ["Resposta"],
                "suggested_public_reply": False,
            },
        )
    )
    monkeypatch.setattr(meta_module, "get_provider_for_task", lambda _complexity: provider)
    monkeypatch.setattr(meta_module, "get_model_for_task", lambda _complexity: None)

    await meta_module.MetaAIService(SimpleNamespace()).generate_copilot(
        channel_type=MetaChannelType.instagram,
        conversation=conversation,
    )

    call = provider.calls[0]
    envelope = json.loads("\n".join(call["messages"][1].content.splitlines()[1:-1]))
    compiled = envelope["data"]["compiled_context"]
    assert compiled["selection"]["recent_available"] == meta_module.META_CONTEXT_QUERY_LIMIT
    assert len(compiled["recent_messages"]) == meta_module.META_CONTEXT_WINDOW_SIZE
    assert compiled["recent_messages"][0]["content"] == "social-message-12"


@pytest.mark.asyncio
async def test_meta_automation_delegates_fallback_to_gateway_without_second_service_call(
    monkeypatch,
):
    conversation = _conversation()
    first_provider = RecordingProvider(error=RuntimeError("medium route failed"))
    fallback_provider = RecordingProvider(
        result=AICompletionResult(text='  "Resposta pelo fallback"  ', provider="openai")
    )
    providers = {
        TaskComplexity.MEDIUM: first_provider,
        TaskComplexity.SIMPLE: fallback_provider,
    }
    monkeypatch.setattr(meta_module, "get_provider_for_task", providers.__getitem__)
    monkeypatch.setattr(meta_module, "get_model_for_task", lambda _complexity: None)
    rule = SimpleNamespace(
        id=uuid4(),
        title="Responder lead",
        event_type=SimpleNamespace(value="direct_message"),
        keyword=None,
        action_config={},
    )

    result = await meta_module.MetaAIService(SimpleNamespace()).generate_automation_reply(
        channel_type=MetaChannelType.instagram,
        conversation=conversation,
        rule=rule,
    )

    assert first_provider.calls[0]["workspace_id"] == str(conversation.workspace_id)
    assert first_provider.calls[0]["agent_name"] == meta_module.META_AUTOMATION_AGENT_NAME
    assert fallback_provider.calls[0]["workspace_id"] == str(conversation.workspace_id)
    assert result["reply"] == "Resposta pelo fallback"


@pytest.mark.parametrize(
    ("request_started", "billing_unknown"),
    [(True, False), (False, True), (True, True)],
)
@pytest.mark.asyncio
async def test_meta_does_not_retry_when_request_started_or_billing_is_unknown(
    monkeypatch,
    request_started,
    billing_unknown,
):
    conversation = _conversation()
    first_provider = RecordingProvider(
        error=ProviderCallError(
            "provider receipt pending",
            request_started=request_started,
            billing_unknown=billing_unknown,
        )
    )
    fallback_provider = RecordingProvider(result=AICompletionResult(text="não deve executar"))
    providers = {
        TaskComplexity.MEDIUM: first_provider,
        TaskComplexity.SIMPLE: fallback_provider,
        TaskComplexity.COMPLEX: fallback_provider,
    }
    monkeypatch.setattr(meta_module, "get_provider_for_task", providers.__getitem__)
    monkeypatch.setattr(meta_module, "get_model_for_task", lambda _complexity: None)

    with pytest.raises(ProviderCallError):
        await meta_module.MetaAIService(SimpleNamespace()).generate_copilot(
            channel_type=MetaChannelType.instagram,
            conversation=conversation,
        )

    assert len(first_provider.calls) == 1
    assert fallback_provider.calls == []


REPORT_INSIGHT = {
    "id": "ai-1",
    "title": "Acompanhar conversao",
    "summary": "O funil requer acompanhamento.",
    "priority": "medium",
    "recommended_action": "Revisar o funil semanalmente.",
    "metric_refs": ["total_matters"],
}


@pytest.mark.parametrize(
    "completion",
    [
        AICompletionResult(text="not-json", structured_data={"insights": [REPORT_INSIGHT]}),
        AICompletionResult(text=f"```json\n{json.dumps({'insights': [REPORT_INSIGHT]})}\n```"),
    ],
    ids=["structured-data", "text-fallback"],
)
@pytest.mark.asyncio
async def test_reports_passes_context_and_consumes_completion(monkeypatch, completion):
    workspace_id = uuid4()
    provider = RecordingProvider(result=completion)
    monkeypatch.setattr(
        reports_module,
        "settings",
        SimpleNamespace(is_local_only=False, KLM_ONLINE_APPROVED=True),
    )
    monkeypatch.setattr(reports_module, "get_ai_provider", lambda: provider)

    result = await reports_module.ReportsService(SimpleNamespace()).generate_insights(
        workspace_id=workspace_id,
        kpis=[],
        funnel_overview=[],
        documents_by_status=[],
        conversation_signals=[],
        revenue_by_source=[],
    )

    assert result[0].id == "ai-1"
    call = provider.calls[0]
    assert call["workspace_id"] == str(workspace_id)
    assert call["agent_name"] == reports_module.REPORTS_INSIGHT_AGENT_NAME
    envelope = json.loads(call["messages"][1].content)
    assert envelope["trust"] == "untrusted_aggregate"
    prompt = envelope["data"]
    attribution = call["attribution"]
    assert attribution.operation == "reports.generate_insights"
    assert attribution.matter_id is None
    assert attribution.context_digest == reports_module._report_context_digest(prompt)
    assert attribution.playbook_version == reports_module.REPORTS_INSIGHT_POLICY_VERSION
    assert call["contains_sensitive_data"] is False
    _assert_draft_2020_strict(call["response_schema"])


@pytest.mark.asyncio
async def test_reports_ai_context_is_bounded_and_source_attributed(monkeypatch):
    workspace_id = uuid4()
    provider = RecordingProvider(
        result=AICompletionResult(
            text="not-json",
            structured_data={"insights": [REPORT_INSIGHT]},
        )
    )
    monkeypatch.setattr(
        reports_module,
        "settings",
        SimpleNamespace(is_local_only=False, KLM_ONLINE_APPROVED=True),
    )
    monkeypatch.setattr(reports_module, "get_ai_provider", lambda: provider)
    kpis = [
        reports_module.KPIMetric(
            id=f"metric-{index}",
            label=f"Metric {index}",
            value=index,
            unit="count",
        )
        for index in range(40)
    ]
    funnel = [
        reports_module.ChartDataPoint(name=f"Stage {index}", count=index) for index in range(30)
    ]
    revenue = [
        reports_module.ValueDataPoint(name=f"Source {index}", value=index) for index in range(30)
    ]

    await reports_module.ReportsService(SimpleNamespace()).generate_insights(
        workspace_id=workspace_id,
        kpis=kpis,
        funnel_overview=funnel,
        documents_by_status=funnel,
        conversation_signals=funnel,
        revenue_by_source=revenue,
    )

    call = provider.calls[0]
    envelope = json.loads(call["messages"][1].content)
    assert envelope["trust"] == "untrusted_aggregate"
    prompt = envelope["data"]
    assert len(prompt["kpis"]) == reports_module.REPORTS_INPUT_LIMITS["kpis"]
    assert len(prompt["funnel_overview"]) == reports_module.REPORTS_INPUT_LIMITS["funnel_overview"]
    assert (
        len(prompt["documents_by_status"])
        == reports_module.REPORTS_INPUT_LIMITS["documents_by_status"]
    )
    assert (
        len(prompt["conversation_signals"])
        == reports_module.REPORTS_INPUT_LIMITS["conversation_signals"]
    )
    assert (
        len(prompt["revenue_by_source"]) == reports_module.REPORTS_INPUT_LIMITS["revenue_by_source"]
    )
    assert call["attribution"].source_refs


@pytest.mark.asyncio
async def test_reports_offline_gate_returns_fallback_without_provider(monkeypatch):
    monkeypatch.setattr(
        reports_module,
        "settings",
        SimpleNamespace(is_local_only=True, KLM_ONLINE_APPROVED=True),
    )

    def unexpected_provider():
        raise AssertionError("provider must stay behind the offline gate")

    monkeypatch.setattr(reports_module, "get_ai_provider", unexpected_provider)
    result = await reports_module.ReportsService(SimpleNamespace()).generate_insights(
        workspace_id=uuid4(),
        kpis=[],
        funnel_overview=[],
        documents_by_status=[],
        conversation_signals=[],
        revenue_by_source=[],
    )

    assert result[0].source == "rule_based"
