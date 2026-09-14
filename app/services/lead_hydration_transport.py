"""Typed provider boundary for retrieving full Lead Ads submissions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True)
class LeadHydrationRequest:
    workspace_id: UUID
    hydration_job_id: UUID
    connection_id: UUID
    provider: str
    connection_key: str
    external_lead_id: str
    external_form_id: str | None
    locator_payload: dict[str, Any]
    request_key: str


@dataclass(frozen=True)
class LeadHydrationReceipt:
    provider: str
    external_lead_id: str
    raw_payload: dict[str, Any]
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    provider_request_id: str | None = None


class LeadHydrationError(RuntimeError):
    def __init__(self, message: str, *, code: str, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class LeadHydrationFetcher(Protocol):
    async def fetch(self, request: LeadHydrationRequest) -> LeadHydrationReceipt: ...


__all__ = [
    "LeadHydrationError",
    "LeadHydrationFetcher",
    "LeadHydrationReceipt",
    "LeadHydrationRequest",
]
