"""Commercial token packages and support-governed workspace grants."""
from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
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
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class AiTokenPackageRequestStatus(StrEnum):
    requested = "requested"
    approved = "approved"
    rejected = "rejected"
    revoked = "revoked"


def _request_status_enum() -> Enum:
    return Enum(
        AiTokenPackageRequestStatus,
        name="ai_token_package_request_status_enum",
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


class AiTokenPackage(UUIDMixin, TimestampMixin, Base):
    """Root-managed catalog item; its price is commercial, never provider cost."""

    __tablename__ = "ai_token_packages"
    __table_args__ = (
        UniqueConstraint("key", name="uq_ai_token_packages_key"),
        CheckConstraint("token_quantity > 0", name="ck_ai_token_package_tokens_positive"),
        CheckConstraint(
            "commercial_price_cents IS NULL OR commercial_price_cents >= 0",
            name="ck_ai_token_package_price_nonnegative",
        ),
        Index("ix_ai_token_packages_active_order", "is_active", "sort_order"),
    )

    key: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    commercial_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WorkspaceAiTokenPackageRequest(UUIDMixin, TimestampMixin, Base):
    """Tenant request that only becomes quota after an explicit support decision."""

    __tablename__ = "workspace_ai_token_package_requests"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_workspace_ai_token_package_request_idempotency",
        ),
        CheckConstraint(
            "token_quantity_snapshot > 0",
            name="ck_workspace_ai_token_package_tokens_positive",
        ),
        CheckConstraint(
            "commercial_price_cents_snapshot IS NULL OR commercial_price_cents_snapshot >= 0",
            name="ck_workspace_ai_token_package_price_nonnegative",
        ),
        CheckConstraint(
            "period_end > period_start",
            name="ck_workspace_ai_token_package_period",
        ),
        Index(
            "ix_workspace_ai_token_package_requests_status",
            "workspace_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_workspace_ai_token_package_requests_active_period",
            "workspace_id",
            "period_start",
            "period_end",
            "status",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    package_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_token_packages.id", ondelete="RESTRICT"), nullable=False
    )
    requested_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    decided_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[AiTokenPackageRequestStatus] = mapped_column(
        _request_status_enum(),
        nullable=False,
        default=AiTokenPackageRequestStatus.requested,
    )
    package_key_snapshot: Mapped[str] = mapped_column(String(80), nullable=False)
    package_name_snapshot: Mapped[str] = mapped_column(String(160), nullable=False)
    token_quantity_snapshot: Mapped[int] = mapped_column(BigInteger, nullable=False)
    commercial_price_cents_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency_snapshot: Mapped[str] = mapped_column(String(3), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    request_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    package: Mapped[AiTokenPackage] = relationship("AiTokenPackage")


__all__ = [
    "AiTokenPackage",
    "AiTokenPackageRequestStatus",
    "WorkspaceAiTokenPackageRequest",
]
