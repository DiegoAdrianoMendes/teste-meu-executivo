from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.event_store import (
    InMemoryAiGatewayEventStore,
    SqlAlchemyAiGatewayEventStore,
)
from app.ai.telemetry import ModelTelemetryEvent, workspace_ref
from app.models.ai_gateway_event import AiGatewayEvent


def _event(workspace_id: str) -> ModelTelemetryEvent:
    return ModelTelemetryEvent(
        request_id="request-1",
        workspace_ref=workspace_ref(workspace_id),
        agent_name="crm_precision",
        task_class="medium",
        provider="openai",
        model="gpt-test",
        outcome="success",
        latency_ms=125,
        input_tokens=50,
        output_tokens=25,
        cost_usd="0.00125",
        route_key="openai:gpt-test",
        operation="flow_copilot",
        attribution_complete=True,
    )


async def test_sql_event_store_persists_safe_queryable_metadata(
    db_session: AsyncSession,
    auth_context,
):
    await db_session.commit()
    sessions = async_sessionmaker(
        db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    store = SqlAlchemyAiGatewayEventStore(
        retention_days=90,
        session_factory=sessions,
    )
    workspace_id = str(auth_context.workspace_id)

    await store.append(
        workspace_id=workspace_id,
        event=_event(workspace_id),
    )

    async with sessions() as verification:
        record = await verification.scalar(
            select(AiGatewayEvent).where(
                AiGatewayEvent.workspace_id == auth_context.workspace_id,
                AiGatewayEvent.request_id == "request-1",
            )
        )
    assert record is not None
    assert record.outcome == "success"
    assert record.input_tokens == 50
    assert record.metadata_json["operation"] == "flow_copilot"
    assert "messages" not in record.metadata_json
    assert "response" not in record.metadata_json


async def test_event_store_rejects_cross_workspace_reference(auth_context):
    store = InMemoryAiGatewayEventStore()

    try:
        await store.append(
            workspace_id=str(auth_context.workspace_id),
            event=_event("11111111-1111-1111-1111-111111111111"),
        )
    except ValueError as error:
        assert "workspace reference" in str(error)
    else:
        raise AssertionError("Mismatched workspace reference should be rejected.")
