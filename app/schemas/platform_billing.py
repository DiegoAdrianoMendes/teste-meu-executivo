"""Contratos paginados do financeiro administrativo da plataforma."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from app.schemas.base import CamelCaseSchema


class PlatformSubscriptionEventRead(CamelCaseSchema):
    id: UUID
    event_type: str
    actor_label: str | None
    motivo: str | None
    occurred_at: datetime | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None


class PlatformSubscriptionEventPageRead(CamelCaseSchema):
    items: list[PlatformSubscriptionEventRead]
    total: int
    limit: int
    offset: int
    has_more: bool
