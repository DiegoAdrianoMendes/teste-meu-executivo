"""Immutable evidence for ads conversion feedback preparation and delivery."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.append_only import register_append_only_model
from app.db.base import Base, FlexibleJSON, FlexibleUUID, UUIDMixin


class AdsConversionFeedbackRecord(UUIDMixin, Base):
    """Append-only proof; future attempts and provider receipts append new rows."""

    __tablename__ = "klm_ads_conversion_feedback_records"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_klm_ads_feedback_record_workspace_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "intent_id",
            "record_type",
            "attempt_number",
            name="uq_klm_ads_feedback_record_attempt",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "intent_id"],
            ["klm_external_action_intents.workspace_id", "klm_external_action_intents.id"],
            name="fk_klm_ads_feedback_record_workspace_intent",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "record_type IN ('prepared', 'dispatch_attempt', 'provider_receipt', 'reconciliation')",
            name="ck_klm_ads_feedback_record_type",
        ),
        CheckConstraint(
            "state IN ('prepared_local', 'blocked', 'queued', 'accepted', "
            "'partial_failure', 'rejected', 'retryable', 'reconciled')",
            name="ck_klm_ads_feedback_record_state",
        ),
        CheckConstraint(
            "attempt_number >= 0 AND external_actions_executed >= 0",
            name="ck_klm_ads_feedback_record_counts",
        ),
        Index(
            "ix_klm_ads_feedback_workspace_provider_recorded",
            "workspace_id",
            "provider",
            "recorded_at",
        ),
        Index(
            "ix_klm_ads_feedback_workspace_state_recorded",
            "workspace_id",
            "state",
            "recorded_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    intent_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    record_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_event_id: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    request_envelope: Mapped[dict] = mapped_column(
        FlexibleJSON(), nullable=False, default=dict
    )
    provider_receipt: Mapped[dict] = mapped_column(
        FlexibleJSON(), nullable=False, default=dict
    )
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(160), nullable=True)
    external_actions_executed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


register_append_only_model(AdsConversionFeedbackRecord)


__all__ = ["AdsConversionFeedbackRecord"]
