"""Typed contracts for deterministic local workspace provisioning."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import CamelCaseSchema, WriteSchema


class LocalProvisioningResourceRead(CamelCaseSchema):
    key: str
    provider: str
    resource_type: str
    logical_key: str
    action: Literal["create", "update", "noop"]
    status: Literal["pending", "drift", "ready"]
    desired_hash: str
    observed_hash: str | None = None
    adapter: Literal["fake_local"] = "fake_local"
    external_effects: int = 0


class LocalProvisioningPlanRead(CamelCaseSchema):
    workspace_id: UUID
    digest: str
    schema_version: str = "1.0"
    generated_at: datetime
    resources: list[LocalProvisioningResourceRead]
    creates: int
    updates: int
    noops: int
    ready: bool
    external_effects: int = 0


class LocalProvisioningStateRead(CamelCaseSchema):
    workspace_id: UUID
    configured: bool
    document_version: int | None = None
    etag: str | None = None
    desired_digest: str
    last_command: str | None = None
    last_run_at: datetime | None = None
    last_actor_user_id: UUID | None = None
    resources: list[LocalProvisioningResourceRead]
    ready: bool
    external_effects: int = 0


class LocalProvisioningCommandRequest(WriteSchema):
    idempotency_key: str = Field(min_length=1, max_length=160)
    reason: str | None = Field(default=None, max_length=240)


class LocalProvisioningCommandResult(CamelCaseSchema):
    action: Literal["apply", "reconcile"]
    replayed: bool
    intent_id: UUID
    plan: LocalProvisioningPlanRead
    state: LocalProvisioningStateRead
    external_effects: int = 0
