"""Contratos da home operacional por funcao da Central Blackbox."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import CamelCaseSchema

DashboardScope = Literal["mine", "team", "platform"]
DashboardQuality = Literal[
    "updated",
    "partial",
    "estimated",
    "stale",
    "unavailable",
    "insufficient_data",
]


class PlatformDashboardMetric(CamelCaseSchema):
    key: str
    label: str
    value: int | float | None
    unit: str
    quality: DashboardQuality
    source: str
    updated_at: datetime
    href: str | None = None
    description: str | None = None


class PlatformDashboardWorkItem(CamelCaseSchema):
    id: str
    source_type: str
    priority: Literal["P0", "P1", "P2", "P3"]
    safe_title: str
    safe_summary: str
    href: str
    due_at: datetime | None = None


class PlatformDashboardAlert(CamelCaseSchema):
    id: str
    severity: Literal["critical", "warning", "info"]
    title: str
    description: str
    href: str | None = None


class PlatformDashboardAction(CamelCaseSchema):
    key: str
    label: str
    href: str
    module: str
    action: str


class PlatformDashboardSourceHealth(CamelCaseSchema):
    key: str
    label: str
    status: Literal["available", "partial", "unavailable"]
    updated_at: datetime
    detail: str | None = None


class PlatformDashboardLayout(CamelCaseSchema):
    template_key: str
    template_version: int = 1
    mandatory_widgets: list[str]
    optional_widgets: list[str]
    hidden_widgets: list[str] = Field(default_factory=list)
    widget_order: list[str] = Field(default_factory=list)
    preference_etag: str | None = None


class PlatformDashboardHome(CamelCaseSchema):
    role: str
    role_label: str
    is_platform_root: bool
    scope: DashboardScope
    available_scopes: list[DashboardScope]
    team_id: UUID | None = None
    team_name: str | None = None
    period_days: int
    generated_at: datetime
    layout: PlatformDashboardLayout
    metrics: list[PlatformDashboardMetric]
    work_items: list[PlatformDashboardWorkItem]
    alerts: list[PlatformDashboardAlert]
    actions: list[PlatformDashboardAction]
    source_health: list[PlatformDashboardSourceHealth]
