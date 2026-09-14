"""
Modelo: AsaasWebhookReceipt
Recibo idempotente de eventos de webhook recebidos do Asaas, por workspace.
Molde de StripeWebhookEventReceipt (app/models/billing.py) — aqui o dedupe é
por workspace já que cada workspace tem sua própria conta/credencial Asaas.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base, FlexibleJSON, UUIDMixin

if TYPE_CHECKING:
    from app.models.workspace import Workspace


class AsaasWebhookReceiptStatus(str, enum.Enum):
    recebido = "recebido"
    processado = "processado"
    erro = "erro"


class AsaasWebhookReceipt(UUIDMixin, Base):
    """Recibo append-mostly de cada evento de webhook do Asaas.

    `event_id` dedupe por workspace: evento duplicado (at-least-once delivery
    do gateway) vira no-op no processamento assíncrono.
    """

    __tablename__ = "asaas_webhook_receipts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "event_id", name="uq_asaas_webhook_receipts_workspace_event_id"),
        Index("ix_asaas_webhook_receipts_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[AsaasWebhookReceiptStatus] = mapped_column(
        Enum(AsaasWebhookReceiptStatus, name="asaas_webhook_receipt_status_enum"),
        nullable=False,
        default=AsaasWebhookReceiptStatus.recebido,
        index=True,
    )
    payload: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)

    workspace: Mapped["Workspace"] = relationship("Workspace")
