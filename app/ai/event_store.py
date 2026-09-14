"""Best-effort SQL fallback for safe AI gateway telemetry metadata."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.telemetry import ModelTelemetryEvent, workspace_ref
from app.db.tenant import set_workspace_context
from app.models.ai_gateway_event import AiGatewayEvent

SessionFactory = Callable[[], AsyncSession]


class AiGatewayEventStore(Protocol):
    async def append(
        self,
        *,
        workspace_id: str,
        event: ModelTelemetryEvent,
    ) -> None: ...


class InMemoryAiGatewayEventStore:
    """Test double that retains the same tenant validation as the SQL store."""

    def __init__(self) -> None:
        self.events: list[tuple[UUID, ModelTelemetryEvent]] = []

    async def append(
        self,
        *,
        workspace_id: str,
        event: ModelTelemetryEvent,
    ) -> None:
        tenant_id = _validated_workspace_id(workspace_id, event)
        self.events.append((tenant_id, event))


class SqlAlchemyAiGatewayEventStore:
    """Persist metadata and prune expired rows without touching model-call success."""

    def __init__(
        self,
        *,
        retention_days: int,
        session_factory: SessionFactory | None = None,
        cleanup_interval_seconds: float = 3_600,
        tracked_workspaces: int = 512,
    ) -> None:
        if retention_days <= 0:
            raise ValueError("AI gateway event retention must be positive.")
        if cleanup_interval_seconds <= 0:
            raise ValueError("AI gateway event cleanup interval must be positive.")
        if tracked_workspaces <= 0:
            raise ValueError("AI gateway tracked workspace bound must be positive.")
        self._retention_days = retention_days
        self._session_factory = session_factory
        self._cleanup_interval_seconds = cleanup_interval_seconds
        self._tracked_workspaces = tracked_workspaces
        self._last_cleanup: OrderedDict[UUID, float] = OrderedDict()

    def _session(self) -> AsyncSession:
        if self._session_factory is not None:
            return self._session_factory()
        from app.db.session import model_usage_session_factory

        return model_usage_session_factory()()

    def _cleanup_due(self, workspace_id: UUID, now: float) -> bool:
        previous = self._last_cleanup.get(workspace_id)
        return previous is None or now - previous >= self._cleanup_interval_seconds

    def _mark_cleaned(self, workspace_id: UUID, now: float) -> None:
        self._last_cleanup[workspace_id] = now
        self._last_cleanup.move_to_end(workspace_id)
        while len(self._last_cleanup) > self._tracked_workspaces:
            self._last_cleanup.popitem(last=False)

    async def append(
        self,
        *,
        workspace_id: str,
        event: ModelTelemetryEvent,
    ) -> None:
        tenant_id = _validated_workspace_id(workspace_id, event)
        now = time.monotonic()
        cleanup_due = self._cleanup_due(tenant_id, now)
        async with self._session() as db:
            try:
                await set_workspace_context(db, tenant_id)
                db.add(
                    AiGatewayEvent(
                        workspace_id=tenant_id,
                        request_id=_bounded(event.request_id, 180),
                        agent_name=_bounded(event.agent_name, 120),
                        task_class=_bounded(event.task_class, 30),
                        provider=_bounded(event.provider, 60),
                        model=_bounded(event.model, 200),
                        outcome=_bounded(event.outcome, 180),
                        route_key=_bounded(event.route_key, 260),
                        latency_ms=max(0, event.latency_ms),
                        input_tokens=max(0, event.input_tokens),
                        output_tokens=max(0, event.output_tokens),
                        cached_input_tokens=max(0, event.cached_input_tokens),
                        cost_usd=_decimal_or_none(event.cost_usd),
                        estimated_cost_usd=_decimal_or_none(event.estimated_cost_usd),
                        request_started=event.request_started,
                        billing_unknown=event.billing_unknown,
                        fallback_used=event.fallback_used,
                        cache_hit=event.cache_hit,
                        operation=_bounded(event.operation, 180),
                        metadata_json=asdict(event),
                    )
                )
                if cleanup_due:
                    cutoff = datetime.now(UTC) - timedelta(days=self._retention_days)
                    await db.execute(
                        delete(AiGatewayEvent).where(
                            AiGatewayEvent.workspace_id == tenant_id,
                            AiGatewayEvent.created_at < cutoff,
                        )
                    )
                await db.commit()
                if cleanup_due:
                    self._mark_cleaned(tenant_id, now)
            except BaseException:
                await db.rollback()
                raise


def _validated_workspace_id(
    workspace_id: str,
    event: ModelTelemetryEvent,
) -> UUID:
    tenant_id = UUID(str(workspace_id))
    if event.workspace_ref != workspace_ref(str(tenant_id)):
        raise ValueError("AI gateway event workspace reference is inconsistent.")
    return tenant_id


def _bounded(value: object, length: int) -> str:
    return str(value or "").strip()[:length]


def _decimal_or_none(value: str | None) -> Decimal | None:
    if value is None or not str(value).strip():
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("AI gateway event cost metadata is invalid.") from error
    if not parsed.is_finite() or parsed < 0:
        raise ValueError("AI gateway event cost metadata must be finite and nonnegative.")
    return parsed
