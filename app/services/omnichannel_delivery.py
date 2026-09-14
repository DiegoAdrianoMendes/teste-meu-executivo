"""Provider-neutral contracts for durable omnichannel delivery workers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID


class ScheduledDispatchError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool,
        outcome_uncertain: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.outcome_uncertain = outcome_uncertain


@dataclass(frozen=True)
class ScheduledDispatchRequest:
    workspace_id: UUID
    scheduled_message_id: UUID
    canonical_message_id: UUID
    connection_id: UUID
    connection_key: str
    provider: str
    transport_mode: str
    channel: str
    endpoint_id: UUID
    external_user_id: str
    address: str | None
    body: str | None
    template_id: str | None
    attachments: list[dict[str, Any]]
    message_payload: dict[str, Any]
    idempotency_key: str
    service_window_expires_at: datetime | None = None


@dataclass(frozen=True)
class ScheduledDispatchReceipt:
    provider: str
    external_message_id: str
    provider_status: str = "sent"
    external_event_id: str | None = None
    accepted_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = field(default_factory=dict)
    cost_amount: Decimal | None = None
    cost_currency: str | None = None
    pricing_category: str | None = None
    provider_invoice_reference: str | None = None


class ScheduledMessageDispatcher(Protocol):
    supports_idempotency: bool

    async def dispatch(self, request: ScheduledDispatchRequest) -> ScheduledDispatchReceipt: ...
