"""Native provider-neutral Ads Manager projections and immutable evidence."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.append_only import register_append_only_model
from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin


class AdsManagerEntity(UUIDMixin, TimestampMixin, Base):
    """Current projection of one provider account, campaign, ad or form."""

    __tablename__ = "klm_ads_manager_entities"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "connection_id",
            "entity_type",
            "external_id",
            name="uq_klm_ads_entity_external",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_klm_ads_entity_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_ads_entity_workspace_connection",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "entity_type IN ('account','campaign','ad_set','ad','creative','form')",
            name="ck_klm_ads_entity_type",
        ),
        Index(
            "ix_klm_ads_entity_workspace_provider_type_status",
            "workspace_id",
            "provider",
            "entity_type",
            "effective_status",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    parent_external_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    effective_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="unknown", server_default="unknown", index=True
    )
    configured_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    objective: Mapped[str | None] = mapped_column(String(120), nullable=True)
    buying_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(80), nullable=True)
    hierarchy_path: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    entity_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class AdsManagerImportReceipt(UUIDMixin, Base):
    """Append-only proof of one local or provider snapshot import."""

    __tablename__ = "klm_ads_manager_import_receipts"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "connection_id",
            "external_sync_id",
            name="uq_klm_ads_import_external_sync",
        ),
        UniqueConstraint(
            "workspace_id", "id", name="uq_klm_ads_import_workspace_id"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_ads_import_workspace_connection",
            ondelete="RESTRICT",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    external_sync_id: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entity_count: Mapped[int] = mapped_column(Integer, nullable=False)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    import_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)


class AdsMetricObservation(UUIDMixin, Base):
    """Append-only daily metric observation; latest observation wins in projections."""

    __tablename__ = "klm_ads_metric_observations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "entity_id",
            "metric_date",
            "external_sync_id",
            name="uq_klm_ads_metric_observation",
        ),
        UniqueConstraint(
            "workspace_id", "id", name="uq_klm_ads_metric_workspace_id"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "entity_id"],
            ["klm_ads_manager_entities.workspace_id", "klm_ads_manager_entities.id"],
            name="fk_klm_ads_metric_workspace_entity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "import_receipt_id"],
            [
                "klm_ads_manager_import_receipts.workspace_id",
                "klm_ads_manager_import_receipts.id",
            ],
            name="fk_klm_ads_metric_workspace_import",
            ondelete="RESTRICT",
        ),
        CheckConstraint("spend >= 0", name="ck_klm_ads_metric_spend_nonnegative"),
        CheckConstraint("revenue >= 0", name="ck_klm_ads_metric_revenue_nonnegative"),
        CheckConstraint(
            "impressions >= 0 AND reach >= 0 AND clicks >= 0 AND leads >= 0",
            name="ck_klm_ads_metric_counts_nonnegative",
        ),
        Index(
            "ix_klm_ads_metric_workspace_provider_date",
            "workspace_id",
            "provider",
            "metric_date",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    import_receipt_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    external_sync_id: Mapped[str] = mapped_column(String(255), nullable=False)
    metric_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    attribution_window: Mapped[str | None] = mapped_column(String(80), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    spend: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), nullable=False, default=Decimal("0"), server_default="0"
    )
    impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    reach: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    conversations: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    comments: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    leads: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    qualified_leads: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    opportunities: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    contracts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    revenue: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), nullable=False, default=Decimal("0"), server_default="0"
    )
    provider_metrics: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


register_append_only_model(AdsManagerImportReceipt)
register_append_only_model(AdsMetricObservation)


__all__ = [
    "AdsManagerEntity",
    "AdsManagerImportReceipt",
    "AdsMetricObservation",
]
