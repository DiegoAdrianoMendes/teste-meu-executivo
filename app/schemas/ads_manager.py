"""Contracts for the native KLM Ads Manager."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import CamelCaseSchema, WriteSchema

AdsProvider = Literal["meta_ads", "tiktok_ads"]
AdsEntityType = Literal["account", "campaign", "ad_set", "ad", "creative", "form"]


class AdsMetricIngest(WriteSchema):
    metric_date: date
    attribution_window: str | None = Field(default=None, max_length=80)
    currency: str = Field(default="BRL", min_length=3, max_length=3)
    spend: Decimal = Field(default=Decimal("0"), ge=0)
    impressions: int = Field(default=0, ge=0)
    reach: int = Field(default=0, ge=0)
    clicks: int = Field(default=0, ge=0)
    conversations: int = Field(default=0, ge=0)
    comments: int = Field(default=0, ge=0)
    leads: int = Field(default=0, ge=0)
    qualified_leads: int = Field(default=0, ge=0)
    opportunities: int = Field(default=0, ge=0)
    contracts: int = Field(default=0, ge=0)
    revenue: Decimal = Field(default=Decimal("0"), ge=0)
    provider_metrics: dict[str, Any] = Field(default_factory=dict)


class AdsEntityIngest(WriteSchema):
    entity_type: AdsEntityType
    external_id: str = Field(min_length=1, max_length=255)
    parent_external_id: str | None = Field(default=None, max_length=255)
    display_name: str = Field(min_length=1, max_length=255)
    effective_status: str = Field(default="unknown", min_length=1, max_length=40)
    configured_status: str | None = Field(default=None, max_length=40)
    objective: str | None = Field(default=None, max_length=120)
    buying_type: str | None = Field(default=None, max_length=80)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    timezone: str | None = Field(default=None, max_length=80)
    hierarchy_path: list[str] = Field(default_factory=list, max_length=8)
    metadata: dict[str, Any] = Field(default_factory=dict)
    metrics: list[AdsMetricIngest] = Field(default_factory=list, max_length=366)


class AdsSnapshotIngest(WriteSchema):
    external_sync_id: str = Field(min_length=1, max_length=255)
    source: Literal["local_fixture", "manual", "provider_sync"] = "local_fixture"
    observed_at: datetime
    entities: list[AdsEntityIngest] = Field(min_length=1, max_length=5000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AdsManagerImportReceiptRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    connection_id: UUID
    provider: str
    external_sync_id: str
    payload_sha256: str
    source: str
    entity_count: int
    observation_count: int
    observed_at: datetime
    received_at: datetime
    import_metadata: dict[str, Any]


class AdsMetricObservationRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    entity_id: UUID
    import_receipt_id: UUID
    provider: str
    external_sync_id: str
    metric_date: date
    attribution_window: str | None = None
    currency: str
    spend: Decimal
    impressions: int
    reach: int
    clicks: int
    conversations: int
    comments: int
    leads: int
    qualified_leads: int
    opportunities: int
    contracts: int
    revenue: Decimal
    provider_metrics: dict[str, Any]
    payload_sha256: str
    observed_at: datetime
    received_at: datetime


class AdsManagerEntityRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    connection_id: UUID
    provider: str
    entity_type: str
    external_id: str
    parent_external_id: str | None = None
    display_name: str
    effective_status: str
    configured_status: str | None = None
    objective: str | None = None
    buying_type: str | None = None
    currency: str | None = None
    timezone: str | None = None
    hierarchy_path: list[str]
    entity_metadata: dict[str, Any]
    version: int
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime


class AdsSnapshotIngestRead(CamelCaseSchema):
    receipt: AdsManagerImportReceiptRead
    entities: list[AdsManagerEntityRead]
    observations: list[AdsMetricObservationRead]
    deduplicated: bool = False


class AdsCampaignPerformanceRead(CamelCaseSchema):
    entity_id: UUID
    external_id: str
    display_name: str
    provider: str
    effective_status: str
    objective: str | None = None
    currency: str
    spend: Decimal
    impressions: int
    reach: int
    clicks: int
    conversations: int
    comments: int
    provider_leads: int
    captured_leads: int
    qualified_leads: int
    opportunities: int
    contracts: int
    revenue: Decimal
    ctr: Decimal | None = None
    cpl: Decimal | None = None
    cac: Decimal | None = None
    roas: Decimal | None = None


class AdsManagerSummaryRead(CamelCaseSchema):
    date_from: date
    date_to: date
    provider: str | None = None
    currency: str
    spend: Decimal
    impressions: int
    reach: int
    clicks: int
    conversations: int
    comments: int
    provider_leads: int
    captured_leads: int
    hydration_pending: int
    hydration_failed: int
    qualified_leads: int
    opportunities: int
    contracts: int
    revenue: Decimal
    ctr: Decimal | None = None
    cpl: Decimal | None = None
    cac: Decimal | None = None
    roas: Decimal | None = None
    campaigns: list[AdsCampaignPerformanceRead]
    generated_at: datetime


__all__ = [
    "AdsCampaignPerformanceRead",
    "AdsEntityIngest",
    "AdsManagerEntityRead",
    "AdsManagerImportReceiptRead",
    "AdsManagerSummaryRead",
    "AdsMetricIngest",
    "AdsMetricObservationRead",
    "AdsSnapshotIngest",
    "AdsSnapshotIngestRead",
]
