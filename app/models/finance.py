"""
Modelos: Finance
Centro financeiro do workspace com cobranças, lembretes e integrações.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, SoftDeleteMixin, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.matter import Matter
    from app.models.workspace import Workspace


class FinanceEntryType(str, enum.Enum):
    receivable = "receivable"
    expense = "expense"
    refund = "refund"


class FinanceEntryStatus(str, enum.Enum):
    pending = "pending"
    scheduled = "scheduled"
    paid = "paid"
    overdue = "overdue"
    cancelled = "cancelled"


class FinanceExpenseApprovalStatus(str, enum.Enum):
    """Estado de aprovação humana para despesas acima do limite do workspace (F8).

    Coluna separada de ``FinanceEntryStatus`` para não alterar o enum de
    status já em uso (contrato compartilhado com F1/F2/webhooks).
    """

    not_required = "not_required"
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


# Categorias padrão sugeridas para despesas jurídicas (F8 §C.3).
# Campo `category` é string livre: esta tupla é só a sugestão de UI/seed.
DEFAULT_EXPENSE_CATEGORIES: tuple[str, ...] = (
    "custas",
    "diligencia",
    "pericia",
    "honorario_repassado",
    "pro_labore",
    "aluguel",
    "software",
    "salarios",
)


class FinanceReminderStatus(str, enum.Enum):
    pending = "pending"
    sent = "sent"
    dismissed = "dismissed"


class IntegrationProvider(str, enum.Enum):
    asaas = "asaas"
    conta_azul = "conta_azul"
    zapsign = "zapsign"
    docusign = "docusign"
    site_leads = "site_leads"
    custom = "custom"
    # F-Integrações (frente B) — catálogo de automação, ads, tracking e canais.
    pluga = "pluga"
    zapier = "zapier"
    google_ads = "google_ads"
    google_tag_manager = "google_tag_manager"
    ga4 = "ga4"
    meta_ads = "meta_ads"
    meta_messenger = "meta_messenger"
    meta_instagram = "meta_instagram"
    whatsapp_cloud = "whatsapp_cloud"


class IntegrationStatus(str, enum.Enum):
    disconnected = "disconnected"
    pending = "pending"
    connected = "connected"
    error = "error"


class FinanceEntry(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "finance_entries"
    __table_args__ = (
        UniqueConstraint("workspace_id", "external_ref", name="uq_finance_entries_workspace_external_ref"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    matter_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("matters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    entry_type: Mapped[FinanceEntryType] = mapped_column(
        Enum(FinanceEntryType, name="finance_entry_type_enum"), nullable=False, default=FinanceEntryType.receivable
    )
    status: Mapped[FinanceEntryStatus] = mapped_column(
        Enum(FinanceEntryStatus, name="finance_entry_status_enum"), nullable=False, default=FinanceEntryStatus.pending, index=True
    )
    provider: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    external_ref: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="BRL")
    due_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reminder_days_before: Mapped[int] = mapped_column(nullable=False, default=3)
    notify_client: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tags: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    entry_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)

    # ── Despesas completas (F8) ──
    category: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    cost_center: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    reimbursable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reimbursement_entry_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("finance_entries.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    receipt_document_ref: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("storage_objects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    recurrence_id: Mapped[Optional[UUID]] = mapped_column(nullable=True, index=True)
    approval_status: Mapped[FinanceExpenseApprovalStatus] = mapped_column(
        Enum(FinanceExpenseApprovalStatus, name="finance_expense_approval_status_enum"),
        nullable=False,
        default=FinanceExpenseApprovalStatus.not_required,
        index=True,
    )
    approved_by: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="finance_entries")
    matter: Mapped[Optional["Matter"]] = relationship("Matter", back_populates="finance_entries")
    reminders: Mapped[list["FinanceReminder"]] = relationship(
        "FinanceReminder",
        back_populates="entry",
        cascade="all, delete-orphan",
        order_by="FinanceReminder.remind_at.asc()",
    )
    reimbursement_entry: Mapped[Optional["FinanceEntry"]] = relationship(
        "FinanceEntry", remote_side="FinanceEntry.id", foreign_keys=[reimbursement_entry_id]
    )


class FinanceReminder(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "finance_reminders"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    finance_entry_id: Mapped[UUID] = mapped_column(
        ForeignKey("finance_entries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[FinanceReminderStatus] = mapped_column(
        Enum(FinanceReminderStatus, name="finance_reminder_status_enum"), nullable=False, default=FinanceReminderStatus.pending, index=True
    )
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(30), nullable=False, default="email")
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    entry: Mapped["FinanceEntry"] = relationship("FinanceEntry", back_populates="reminders")
    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="finance_reminders")


class IntegrationConnection(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "integration_connections"
    __table_args__ = (
        UniqueConstraint("workspace_id", "provider", "label", name="uq_integration_workspace_provider_label"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[IntegrationProvider] = mapped_column(
        Enum(IntegrationProvider, name="integration_provider_enum"), nullable=False, index=True
    )
    status: Mapped[IntegrationStatus] = mapped_column(
        Enum(IntegrationStatus, name="integration_status_enum"), nullable=False, default=IntegrationStatus.disconnected
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    base_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    external_account_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    webhook_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    api_version: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    capabilities: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    settings: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="integration_connections")
