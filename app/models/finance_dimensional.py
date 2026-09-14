"""Dimensional financial planning models (tenant scoped)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin


class FinanceCostCenter(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "finance_cost_centers"
    __table_args__ = (
        UniqueConstraint("workspace_id", "code", name="uq_finance_cost_centers_workspace_code"),
    )
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("finance_cost_centers.id", ondelete="SET NULL")
    )
    type: Mapped[str] = mapped_column(String(40), default="operational", nullable=False)
    owner_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)


class FinanceAllocation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "finance_allocations"
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    finance_entry_id: Mapped[UUID] = mapped_column(
        ForeignKey("finance_entries.id", ondelete="CASCADE"), index=True
    )
    cost_center_id: Mapped[UUID] = mapped_column(
        ForeignKey("finance_cost_centers.id", ondelete="RESTRICT"), index=True
    )
    percentage: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    audit_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), default=dict, nullable=False)
    client_id: Mapped[UUID | None] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"))
    contract_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contract_billing_proposals.id", ondelete="SET NULL")
    )
    matter_id: Mapped[UUID | None] = mapped_column(ForeignKey("matters.id", ondelete="SET NULL"))
    team_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("klm_workspace_teams.id", ondelete="SET NULL")
    )
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(40), default="manual", nullable=False)


class FinanceForecast(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "finance_forecasts"
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    revenue: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0, nullable=False)
    expense: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0, nullable=False)
    assumptions: Mapped[dict] = mapped_column(FlexibleJSON(), default=dict, nullable=False)
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    scenario: Mapped[str] = mapped_column(String(30), default="base", nullable=False)
    horizon: Mapped[str] = mapped_column(String(20), default="monthly", nullable=False)


class FinanceValuation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "finance_valuations"
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    method: Mapped[str] = mapped_column(String(40), nullable=False)
    valuation_date: Mapped[date] = mapped_column(Date, nullable=False)
    enterprise_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    assumptions: Mapped[dict] = mapped_column(FlexibleJSON(), default=dict, nullable=False)
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    premise_quality: Mapped[str] = mapped_column(String(20), default="unverified", nullable=False)


class FinanceTaxProfile(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "finance_tax_profiles"
    __table_args__ = (
        Index("ix_finance_tax_profiles_workspace_effective", "workspace_id", "effective_from"),
    )
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    regime: Mapped[str] = mapped_column(String(40), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False, default=0)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    settings: Mapped[dict] = mapped_column(FlexibleJSON(), default=dict, nullable=False)
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    municipality: Mapped[str | None] = mapped_column(String(120))
    sources: Mapped[list] = mapped_column(FlexibleJSON(), default=list, nullable=False)
    reform_calendar: Mapped[dict] = mapped_column(FlexibleJSON(), default=dict, nullable=False)


class FinanceTaxReformAlert(UUIDMixin, TimestampMixin, Base):
    """One immutable scheduling outcome for a tax-reform review window.

    The unique schedule key is deliberately independent from a worker run: a
    daily scheduler may be retried safely without generating a new human wait
    or repeatedly calling the owner when the organisation is misconfigured.
    A tax-profile or official-catalog version change produces a new key.
    """

    __tablename__ = "finance_tax_reform_alerts"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "schedule_key", name="uq_finance_tax_reform_alert_workspace_key"
        ),
        Index(
            "ix_finance_tax_reform_alert_workspace_status",
            "workspace_id",
            "status",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tax_profile_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("finance_tax_profiles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    human_decision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("human_decisions.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    schedule_key: Mapped[str] = mapped_column(String(255), nullable=False)
    catalog_version: Mapped[int] = mapped_column(Integer, nullable=False)
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    milestone_key: Mapped[str] = mapped_column(String(80), nullable=False)
    milestone_date: Mapped[date] = mapped_column(Date, nullable=False)
    offset_days: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="called")
    failure_code: Mapped[str | None] = mapped_column(String(120), nullable=True)


class FinanceBudgetLine(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "finance_budget_lines"
    __table_args__ = (
        CheckConstraint("period_end >= period_start", name="ck_finance_budget_period"),
        CheckConstraint("amount >= 0", name="ck_finance_budget_amount"),
        CheckConstraint(
            "entry_type IN ('receivable', 'expense', 'refund')",
            name="ck_finance_budget_entry_type",
        ),
        Index(
            "ix_finance_budget_workspace_period",
            "workspace_id",
            "period_start",
            "period_end",
        ),
        Index("ix_finance_budget_workspace_center", "workspace_id", "cost_center_id"),
    )
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    cost_center_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("finance_cost_centers.id", ondelete="SET NULL")
    )
    category: Mapped[str | None] = mapped_column(String(80))
    entry_type: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    assumptions: Mapped[dict] = mapped_column(FlexibleJSON(), default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    version: Mapped[int] = mapped_column(default=1, nullable=False)


class FinanceExportJob(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "finance_export_jobs"
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)
    drill_context: Mapped[dict] = mapped_column(FlexibleJSON(), default=dict, nullable=False)
    storage_path: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_detail: Mapped[str | None] = mapped_column(Text)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    progress: Mapped[int] = mapped_column(default=0, nullable=False)
    row_count: Mapped[int | None] = mapped_column()
    snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def dataset(self) -> str:
        """Compatibility label used by the shared private export storage adapter."""
        return "finance"
