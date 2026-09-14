"""Contracts returned by the central SaaS subscription access policy."""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from app.models.subscription_access import SubscriptionAccessStatus
from app.schemas.base import CamelCaseSchema


class SubscriptionAccessOperation(enum.StrEnum):
    read = "read"
    write = "write"


class SubscriptionAccessDecision(CamelCaseSchema):
    workspace_id: UUID
    subscription_id: UUID | None = None
    state: SubscriptionAccessStatus | None = None
    state_version: int = 0
    read_allowed: bool
    write_allowed: bool
    reason_code: str
    evaluated_at: datetime
    trial_ends_at: datetime | None = None
    grace_ends_at: datetime | None = None

    def allows(self, operation: SubscriptionAccessOperation) -> bool:
        if operation == SubscriptionAccessOperation.read:
            return self.read_allowed
        return self.write_allowed


class SubscriptionAccessTransitionResult(CamelCaseSchema):
    event_id: UUID
    access_state_id: UUID
    workspace_id: UUID
    subscription_id: UUID | None = None
    from_state: SubscriptionAccessStatus | None = None
    to_state: SubscriptionAccessStatus
    from_version: int
    to_version: int
    applied: bool
    replayed: bool = False
    source: str
    reason: str
    actor_user_id: UUID | None = None
    occurred_at: datetime
    trial_ends_at: datetime | None = None
    grace_ends_at: datetime | None = None
