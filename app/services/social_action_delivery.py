"""Typed transport boundary for outbound social actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True)
class SocialActionDispatchRequest:
    workspace_id: UUID
    action_intent_id: UUID
    connection_id: UUID
    provider: str
    connection_key: str
    action_type: str
    target_external_id: str | None
    body: str | None
    action_payload: dict[str, Any]
    idempotency_key: str


@dataclass(frozen=True)
class SocialActionDispatchReceipt:
    provider: str
    external_action_id: str
    provider_status: str = "accepted"
    accepted_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = field(default_factory=dict)
    cost_amount: Decimal | None = None
    cost_currency: str | None = None


class SocialActionDispatchError(RuntimeError):
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


class SocialActionDispatcher(Protocol):
    supports_idempotency: bool

    async def dispatch(
        self, request: SocialActionDispatchRequest
    ) -> SocialActionDispatchReceipt: ...
