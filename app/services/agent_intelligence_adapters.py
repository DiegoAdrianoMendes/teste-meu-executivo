"""Provider-backed local intelligence node adapters."""

from app.services.agent_inference_service import AgentInferenceService
from app.services.agent_node_executor import (
    AgentExecutionContext,
    AgentNodeOutput,
    agent_node_executors,
)


async def _run(ctx: AgentExecutionContext, payload: dict, operation: str) -> AgentNodeOutput:
    provider = payload.pop("_provider", None)
    db = payload.pop("_db", None) or ctx.db
    if provider is None:
        return AgentNodeOutput(
            status="waiting_tool", data={"reason": "provider_not_configured"}, requires_human=True
        )
    return await AgentInferenceService(provider, db=db).execute(
        ctx, operation=operation, payload=payload, output_schema={"type": "object"}
    )


async def _consensus(ctx: AgentExecutionContext, payload: dict) -> AgentNodeOutput:
    """Run three independent opinions, then one provider-backed synthesis."""
    provider = payload.pop("_provider", None)
    db = payload.pop("_db", None) or ctx.db
    if provider is None:
        return AgentNodeOutput(
            status="waiting_tool", data={"reason": "provider_not_configured"}, requires_human=True
        )
    opinions: list[str] = []
    for index in range(3):
        result = await AgentInferenceService(provider, db=db).execute(
            ctx,
            operation=f"multi_agent_consensus:opinion:{index + 1}",
            payload={**payload, "opinionIndex": index + 1},
            output_schema={"type": "object"},
        )
        if result.status != "completed":
            return result
        opinions.append(str(result.data.get("text", "")))
    return await AgentInferenceService(provider, db=db).execute(
        ctx,
        operation="multi_agent_consensus:synthesis",
        payload={**payload, "opinions": opinions},
        output_schema={"type": "object"},
    )


def register_intelligence_adapters() -> None:
    for node_type in (
        "summarize",
        "classify_intent",
        "extract_data",
        "evaluate_risk",
        "evaluate_confidence",
        "draft_content",
        "review_content",
        "generate_alternatives",
        "compare_scenarios",
    ):
        if not agent_node_executors.has(node_type):

            async def adapter(ctx, payload, _operation=node_type):
                return await _run(ctx, payload, _operation)

            agent_node_executors.register(node_type, adapter)
    if not agent_node_executors.has("multi_agent_consensus"):
        agent_node_executors.register("multi_agent_consensus", _consensus)


register_intelligence_adapters()
