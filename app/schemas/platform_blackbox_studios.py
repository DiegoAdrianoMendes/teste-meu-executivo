"""Contratos dos painéis analíticos e da biblioteca master do Blackbox."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.schemas.base import CamelCaseSchema, WriteSchema

PerformanceAudience = Literal["workspace_team", "saas_team", "workspace_benchmark"]
CreativeKind = Literal["creative", "campaign"]
CreativeStatus = Literal["draft", "approved", "archived"]
CreativeChannel = Literal["meta", "google", "tiktok", "linkedin", "organic", "other"]


class PerformanceRow(CamelCaseSchema):
    id: str
    name: str
    scope_name: str
    role: str | None = None
    primary_count: int = 0
    completed_count: int = 0
    pending_count: int = 0
    quality_value: float | None = None
    score: float = 0


class PerformanceBoardRead(CamelCaseSchema):
    audience: PerformanceAudience
    period_days: int
    generated_at: datetime
    score_formula: str
    total: int
    limit: int
    offset: int
    has_more: bool
    rows: list[PerformanceRow]


class CreativeLibraryItemInput(WriteSchema):
    id: str | None = Field(default=None, max_length=80)
    name: str = Field(min_length=2, max_length=160)
    kind: CreativeKind
    channel: CreativeChannel
    objective: str = Field(min_length=2, max_length=240)
    status: CreativeStatus = "draft"
    tags: list[str] = Field(default_factory=list, max_length=30)
    asset_url: str | None = Field(default=None, max_length=2_000)
    copy_text: str | None = Field(default=None, max_length=20_000)
    notes: str | None = Field(default=None, max_length=8_000)
    expected_etag: str | None = Field(default=None, max_length=96)


class CreativeLibraryArchiveRequest(WriteSchema):
    expected_etag: str | None = Field(default=None, max_length=96)


class CreativeLibraryItemRead(CamelCaseSchema):
    id: str
    name: str
    kind: CreativeKind
    channel: CreativeChannel
    objective: str
    status: CreativeStatus
    tags: list[str]
    asset_url: str | None = None
    copy_text: str | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class CreativeLibraryRead(CamelCaseSchema):
    current_version: int
    etag: str | None = None
    updated_at: datetime | None = None
    items: list[CreativeLibraryItemRead]


class CreativeLibraryPageRead(CamelCaseSchema):
    current_version: int
    etag: str | None = None
    updated_at: datetime | None = None
    items: list[CreativeLibraryItemRead]
    total: int
    limit: int
    offset: int
    has_more: bool


class AdvancedReportSeriesPoint(CamelCaseSchema):
    date: str
    value: float


class AdvancedReportRow(CamelCaseSchema):
    key: str
    label: str
    category: str
    category_label: str
    value: Any = None
    unit: str | None = None
    delta_pct: float | None = None
    has_data: bool
    note: str | None = None
    series: list[AdvancedReportSeriesPoint] = Field(default_factory=list)


class AdvancedReportRead(CamelCaseSchema):
    generated_at: datetime
    period_days: int
    available_categories: list[dict[str, str]]
    applied_filters: dict[str, Any]
    total_rows: int
    rows_with_data: int
    limit: int
    offset: int
    has_more: bool
    rows: list[AdvancedReportRow]
    chart_rows: list[dict[str, Any]]
    trend_rows: list[dict[str, Any]]
