"""Typed plan entitlements and durable monthly usage reservations."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.billing import Plan
    from app.models.user import User
    from app.models.workspace import Workspace


class EntitlementKey(StrEnum):
    tokens = "tokens"
    ai_executions = "ai_executions"
    ai_conversations = "ai_conversations"
    campaign_recipients = "campaign_recipients"
    contacts = "contacts"
    audio_minutes = "audio_minutes"
    storage_bytes = "storage_bytes"
    files = "files"
    users = "users"
    workspaces = "workspaces"
    whatsapp_numbers = "whatsapp_numbers"
    agents = "agents"
    legal_suite = "legal_suite"
    legal_areas = "legal_areas"
    legal_cases = "legal_cases"
    djen_terms = "djen_terms"
    legal_ai_generations = "legal_ai_generations"
    legal_documents = "legal_documents"
    legal_automations = "legal_automations"
    legal_templates = "legal_templates"
    legal_signatures = "legal_signatures"
    protocol_folders = "protocol_folders"
    legal_bulk_operations = "legal_bulk_operations"
    legal_audit = "legal_audit"
    legal_integrations = "legal_integrations"
    automations = "automations"
    active_tasks = "active_tasks"
    task_participants = "task_participants"
    task_reviews = "task_reviews"
    review_rounds = "review_rounds"
    task_reminder_rules = "task_reminder_rules"
    meeting_participants = "meeting_participants"
    digital_signature = "digital_signature"
    calendar = "calendar"
    email = "email"
    webhooks = "webhooks"
    advanced_audit = "advanced_audit"
    success_manager = "success_manager"
    # Módulo processual contratável à parte (add-on, não é core): quantidade
    # de processos monitorados simultaneamente ativos por workspace. 0/ausente
    # desliga todas as superfícies do módulo (ver EntitlementDisabledError).
    monitored_processes = "monitored_processes"


class UsageReservationStatus(StrEnum):
    reserved = "reserved"
    committed = "committed"
    released = "released"


class OverrideMode(StrEnum):
    grant = "grant"
    revoke = "revoke"
    limit = "limit"


def _entitlement_enum() -> Enum:
    return Enum(
        EntitlementKey,
        name="entitlement_key_enum",
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


def _reservation_status_enum() -> Enum:
    return Enum(
        UsageReservationStatus,
        name="usage_reservation_status_enum",
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


def _override_mode_enum() -> Enum:
    return Enum(
        OverrideMode,
        name="workspace_entitlement_override_mode_enum",
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


class PlanEntitlement(UUIDMixin, TimestampMixin, Base):
    """A typed quota configured for one plan and one resource."""

    __tablename__ = "plan_entitlements"
    __table_args__ = (
        UniqueConstraint("plan_id", "entitlement", name="uq_plan_entitlement_key"),
        CheckConstraint(
            "limit_quantity IS NULL OR limit_quantity >= 0",
            name="ck_plan_entitlement_limit_nonnegative",
        ),
        Index("ix_plan_entitlement_key", "entitlement"),
    )

    plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("plans.id", ondelete="CASCADE"), nullable=False
    )
    entitlement: Mapped[EntitlementKey] = mapped_column(_entitlement_enum(), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    limit_quantity: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    hard_limit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    plan: Mapped[Plan] = relationship("Plan")


class WorkspaceMonthlyUsage(UUIDMixin, TimestampMixin, Base):
    """Committed and reserved usage for one workspace resource and UTC month."""

    __tablename__ = "workspace_monthly_entitlement_usage"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "entitlement",
            "period_start",
            name="uq_workspace_monthly_usage_key",
        ),
        CheckConstraint(
            "consumed_quantity >= 0",
            name="ck_workspace_monthly_usage_consumed_nonnegative",
        ),
        CheckConstraint(
            "reserved_quantity >= 0",
            name="ck_workspace_monthly_usage_reserved_nonnegative",
        ),
        CheckConstraint(
            "limit_quantity IS NULL OR limit_quantity >= 0",
            name="ck_workspace_monthly_usage_limit_nonnegative",
        ),
        Index(
            "ix_workspace_monthly_usage_workspace_period",
            "workspace_id",
            "period_start",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    entitlement: Mapped[EntitlementKey] = mapped_column(_entitlement_enum(), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    consumed_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    limit_quantity: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    hard_limit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    limit_source: Mapped[str | None] = mapped_column(String(32), nullable=True)

    workspace: Mapped[Workspace] = relationship("Workspace")
    reservations: Mapped[list[EntitlementUsageReservation]] = relationship(
        "EntitlementUsageReservation",
        back_populates="usage",
        cascade="all, delete-orphan",
    )


class EntitlementUsageReservation(UUIDMixin, TimestampMixin, Base):
    """Idempotent permit that must be acquired before a metered write."""

    __tablename__ = "entitlement_usage_reservations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "entitlement",
            "period_start",
            "idempotency_key",
            name="uq_usage_reservation_idempotency",
        ),
        CheckConstraint(
            "reserved_quantity > 0",
            name="ck_usage_reservation_quantity_positive",
        ),
        CheckConstraint(
            "committed_quantity IS NULL OR committed_quantity >= 0",
            name="ck_usage_reservation_committed_nonnegative",
        ),
        CheckConstraint(
            "((status = 'committed' AND committed_quantity IS NOT NULL) OR "
            "(status <> 'committed' AND committed_quantity IS NULL))",
            name="ck_usage_reservation_committed_state",
        ),
        Index(
            "ix_usage_reservation_workspace_period",
            "workspace_id",
            "period_start",
        ),
        Index("ix_usage_reservation_usage_status", "usage_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    usage_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_monthly_entitlement_usage.id", ondelete="CASCADE"),
        nullable=False,
    )
    entitlement: Mapped[EntitlementKey] = mapped_column(_entitlement_enum(), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    reserved_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    committed_quantity: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[UsageReservationStatus] = mapped_column(
        _reservation_status_enum(),
        nullable=False,
        default=UsageReservationStatus.reserved,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    usage: Mapped[WorkspaceMonthlyUsage] = relationship(
        "WorkspaceMonthlyUsage", back_populates="reservations"
    )


class WorkspaceEntitlementOverride(UUIDMixin, TimestampMixin, Base):
    """Platform-only per-account exception that wins over the resolved plan.

    The plan/package defines the default entitlement; the platform admin can
    grant, revoke, or re-limit one entitlement for a single workspace without
    touching the plan catalog. Precedence at resolution time is always
    override > plan > default. Every write is paired with a ``KlmAuditEvent``
    by the owning service (see ``WorkspaceEntitlementOverrideService``).
    """

    __tablename__ = "workspace_entitlement_overrides"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "entitlement", name="uq_workspace_entitlement_override_key"
        ),
        CheckConstraint(
            "quantity IS NULL OR quantity >= 0",
            name="ck_workspace_entitlement_override_quantity_nonnegative",
        ),
        CheckConstraint(
            "mode <> 'limit' OR quantity IS NOT NULL",
            name="ck_workspace_entitlement_override_limit_requires_quantity",
        ),
        CheckConstraint(
            "length(trim(reason)) > 0",
            name="ck_workspace_entitlement_override_reason_required",
        ),
        Index("ix_workspace_entitlement_override_workspace", "workspace_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    entitlement: Mapped[EntitlementKey] = mapped_column(_entitlement_enum(), nullable=False)
    mode: Mapped[OverrideMode] = mapped_column(_override_mode_enum(), nullable=False)
    quantity: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    workspace: Mapped[Workspace] = relationship("Workspace")
    created_by: Mapped[User | None] = relationship("User")
