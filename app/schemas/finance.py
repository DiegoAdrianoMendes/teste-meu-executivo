"""
Schemas do domínio financeiro.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import Field, field_validator

from app.models.finance import (
    FinanceEntryStatus,
    FinanceEntryType,
    FinanceReminderStatus,
    IntegrationProvider,
    IntegrationStatus,
)
from app.schemas.base import CamelCaseSchema, WriteSchema


class FinanceReminderWrite(WriteSchema):
    remind_at: datetime
    channel: str = "email"
    message: Optional[str] = None


class FinanceReminderUpdate(WriteSchema):
    status: Optional[FinanceReminderStatus] = None
    remind_at: Optional[datetime] = None
    channel: Optional[str] = None
    message: Optional[str] = None
    resolved_at: Optional[datetime] = None


class FinanceReminderRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    finance_entry_id: UUID
    status: FinanceReminderStatus
    remind_at: datetime
    channel: str
    message: Optional[str] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class FinanceEntryCreate(WriteSchema):
    matter_id: Optional[UUID] = None
    title: str
    description: Optional[str] = None
    entry_type: FinanceEntryType = FinanceEntryType.receivable
    status: FinanceEntryStatus = FinanceEntryStatus.pending
    provider: Optional[str] = None
    external_ref: Optional[str] = None
    amount: float
    currency: str = "BRL"
    due_date: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    reminder_days_before: int = 3
    notify_client: bool = False
    tags: List[str] = Field(default_factory=list)
    entry_metadata: dict = Field(default_factory=dict)
    reminders: List[FinanceReminderWrite] = Field(default_factory=list)


class FinanceEntryUpdate(WriteSchema):
    matter_id: Optional[UUID] = None
    title: Optional[str] = None
    description: Optional[str] = None
    entry_type: Optional[FinanceEntryType] = None
    status: Optional[FinanceEntryStatus] = None
    provider: Optional[str] = None
    external_ref: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    due_date: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    reminder_days_before: Optional[int] = None
    notify_client: Optional[bool] = None
    tags: Optional[List[str]] = None
    entry_metadata: Optional[dict] = None
    reminders: Optional[List[FinanceReminderWrite]] = None


class FinanceEntryRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    matter_id: Optional[UUID] = None
    title: str
    description: Optional[str] = None
    entry_type: FinanceEntryType
    status: FinanceEntryStatus
    provider: Optional[str] = None
    external_ref: Optional[str] = None
    amount: float
    currency: str
    due_date: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    reminder_days_before: int
    notify_client: bool
    tags: List[str] = Field(default_factory=list)
    entry_metadata: dict = Field(default_factory=dict)
    reminders: List[FinanceReminderRead] = Field(default_factory=list)
    category: Optional[str] = None
    cost_center: Optional[str] = None
    reimbursable: bool = False
    reimbursement_entry_id: Optional[UUID] = None
    receipt_document_ref: Optional[UUID] = None
    recurrence_id: Optional[UUID] = None
    approval_status: str = "not_required"
    approved_by: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class IntegrationConnectionCreate(WriteSchema):
    provider: IntegrationProvider
    label: str
    status: IntegrationStatus = IntegrationStatus.pending
    base_url: Optional[str] = None
    external_account_id: Optional[str] = None
    webhook_url: Optional[str] = None
    api_version: Optional[str] = None
    capabilities: List[str] = Field(default_factory=list)
    settings: dict = Field(default_factory=dict)
    notes: Optional[str] = None


class IntegrationConnectionUpdate(WriteSchema):
    label: Optional[str] = None
    status: Optional[IntegrationStatus] = None
    base_url: Optional[str] = None
    external_account_id: Optional[str] = None
    webhook_url: Optional[str] = None
    api_version: Optional[str] = None
    capabilities: Optional[List[str]] = None
    settings: Optional[dict] = None
    notes: Optional[str] = None
    last_sync_at: Optional[datetime] = None


class IntegrationConnectionRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    provider: IntegrationProvider
    status: IntegrationStatus
    label: str
    base_url: Optional[str] = None
    external_account_id: Optional[str] = None
    webhook_url: Optional[str] = None
    api_version: Optional[str] = None
    capabilities: List[str] = Field(default_factory=list)
    settings: dict = Field(default_factory=dict)
    last_sync_at: Optional[datetime] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    @field_validator("settings", mode="before")
    @classmethod
    def redact_credentials(cls, value: dict | None) -> dict:
        from app.services.integration_secrets import redact_integration_settings

        return redact_integration_settings(value)


class FinanceSettingsRead(CamelCaseSchema):
    reminders_enabled: bool = True
    default_reminder_days_before: int = 3
    default_reminder_channel: str = "whatsapp"
    allow_client_notification: bool = True
    auto_create_receivable_on_close: bool = True
    open_api_enabled: bool = True
    external_sync_mode: str = "manual"


class FinanceSettingsUpdate(WriteSchema):
    reminders_enabled: Optional[bool] = None
    default_reminder_days_before: Optional[int] = None
    default_reminder_channel: Optional[str] = None
    allow_client_notification: Optional[bool] = None
    auto_create_receivable_on_close: Optional[bool] = None
    open_api_enabled: Optional[bool] = None
    external_sync_mode: Optional[str] = None


class FinanceOverviewRead(CamelCaseSchema):
    total_open_receivables: float
    overdue_amount: float
    scheduled_amount: float
    paid_amount: float
    pending_reminders: int
    entries_due_soon: int
    upcoming_entries: List[FinanceEntryRead] = Field(default_factory=list)
    reminders: List[FinanceReminderRead] = Field(default_factory=list)
    integrations: List[IntegrationConnectionRead] = Field(default_factory=list)
    highlights: List[str] = Field(default_factory=list)
