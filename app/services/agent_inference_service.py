"""Provider-agnostic inference boundary for agent-backed flow nodes."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.ai.base import AIMessage, AIProvider
from app.models.agent_run import AgentRun, AgentRunStatus
from app.services.agent_node_executor import AgentExecutionContext, AgentNodeOutput


class AgentInferenceService:
    def __init__(self, provider: AIProvider, db=None):
        self.provider = provider
        self.db = db

    async def execute(
        self,
        ctx: AgentExecutionContext,
        *,
        operation: str,
        payload: dict[str, Any],
        output_schema: dict[str, Any] | None = None,
    ) -> AgentNodeOutput:
        if not ctx.workspace_id or not ctx.context_type:
            raise ValueError("contexto de inferência inválido")
        run = None
        if self.db is not None:
            run = AgentRun(
                workspace_id=ctx.workspace_id,
                matter_id=ctx.context_id if ctx.context_type == "matter" else None,
                context_type=ctx.context_type,
                context_id=ctx.context_id,
                deployment_id=ctx.deployment_id,
                agent_name=operation,
                trigger_source="agent_node",
                status=AgentRunStatus.running,
                input_payload=payload,
                dedupe_key=f"{ctx.correlation_id or uuid4()}:{operation}",
            )
            self.db.add(run)
            await self.db.flush()
        result = await self.provider.complete(
            [AIMessage(role="user", content=str(payload))],
            response_schema=output_schema,
            workspace_id=str(ctx.workspace_id),
            agent_name=operation,
            idempotent=True,
            idempotency_key=f"agent-node:{ctx.correlation_id or uuid4()}",
        )
        data = {"text": result.text}
        if run is not None:
            run.status = AgentRunStatus.completed
            run.output_payload = data
            run.input_tokens = result.input_tokens
            run.output_tokens = result.output_tokens
            run.cost_usd = result.cost_usd
            # The flow worker/command bus owns the transaction that also
            # persists trace, audit and any human handoff.  Committing here
            # would make accounting survive even if that enclosing operation
            # rolls back, leaving an orphaned successful AgentRun.
            await self.db.flush()
        return AgentNodeOutput(
            status="completed",
            data=data,
            idempotency_key=f"agent-node:{ctx.correlation_id or ''}",
        )
