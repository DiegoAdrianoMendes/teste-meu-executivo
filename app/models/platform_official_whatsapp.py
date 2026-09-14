"""
Modelos globais para os números oficiais de WhatsApp da plataforma.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.models.whatsapp import ConnectionType, WaConnectionStatus

if TYPE_CHECKING:
    from app.models.user import User


class PlatformOfficialWhatsAppRole(str, Enum):
    notifications = "notifications"
    outbound = "outbound"
    support = "support"


class PlatformOfficialWhatsAppDeliveryState(str, Enum):
    prepared = "prepared"
    provider_call_started = "provider_call_started"
    sent = "sent"
    delivery_uncertain = "delivery_uncertain"
    failed = "failed"


class PlatformOfficialWhatsAppConnection(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "platform_official_whatsapp_connections"
    __table_args__ = (
        UniqueConstraint("role", name="uq_platform_official_whatsapp_role"),
        UniqueConstraint("phone_number_id", name="uq_platform_official_whatsapp_phone_number"),
    )

    role: Mapped[PlatformOfficialWhatsAppRole] = mapped_column(
        SAEnum(PlatformOfficialWhatsAppRole, name="platform_official_whatsapp_role_enum"),
        nullable=False,
        index=True,
    )
    connection_type: Mapped[ConnectionType] = mapped_column(
        SAEnum(ConnectionType, name="platform_official_wa_connection_type_enum"),
        nullable=False,
        default=ConnectionType.OFFICIAL_MANUAL,
    )
    transport_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="official_api", server_default="official_api"
    )
    coexistence_sync_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_applicable", server_default="not_applicable"
    )
    coexistence_contacts_sync_request_id: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    coexistence_history_sync_request_id: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    coexistence_sync_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    phone_number_id: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    phone_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    display_phone_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    business_account_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    waba_id: Mapped[str | None] = mapped_column(String(50), nullable=True)

    access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    business_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    registration_pin_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[WaConnectionStatus] = mapped_column(
        SAEnum(WaConnectionStatus, name="platform_official_wa_connection_status_enum"),
        nullable=False,
        default=WaConnectionStatus.disconnected,
        index=True,
    )
    webhook_registered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    updated_by_user: Mapped[User | None] = relationship("User", foreign_keys=[updated_by_user_id])


class PlatformOfficialWhatsAppDelivery(UUIDMixin, TimestampMixin, Base):
    """Global durable outbox for the SaaS-owned WhatsApp numbers.

    It intentionally has no ``workspace_id``: platform Inbox, test sends and
    operational notifications are control-plane effects. Reusing the tenant
    ``ExternalActionIntent`` would require a fake workspace and weaken both
    isolation and audit semantics.
    """

    __tablename__ = "platform_official_whatsapp_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key_hash",
            name="uq_platform_official_wa_delivery_idempotency",
        ),
        UniqueConstraint(
            "external_message_id",
            name="uq_platform_official_wa_delivery_external_message",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_platform_official_wa_delivery_attempt_count",
        ),
        CheckConstraint(
            "length(idempotency_key_hash) = 64 AND length(payload_digest) = 64",
            name="ck_platform_official_wa_delivery_hashes",
        ),
        CheckConstraint(
            "state != 'prepared' OR payload_ciphertext IS NOT NULL",
            name="ck_platform_official_wa_delivery_prepared_payload",
        ),
        CheckConstraint(
            "state != 'sent' OR external_message_id IS NOT NULL",
            name="ck_platform_official_wa_delivery_sent_receipt",
        ),
        Index(
            "ix_platform_official_wa_delivery_state_created",
            "state",
            "created_at",
        ),
        Index(
            "ix_platform_official_wa_delivery_role_state_created",
            "role",
            "state",
            "created_at",
        ),
        Index(
            "ix_platform_official_wa_delivery_source",
            "source_type",
            "source_id",
        ),
    )

    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("platform_official_whatsapp_connections.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    role: Mapped[PlatformOfficialWhatsAppRole] = mapped_column(
        SAEnum(
            PlatformOfficialWhatsAppRole,
            name="platform_official_whatsapp_role_enum",
        ),
        nullable=False,
    )
    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    destination_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    body_length: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[PlatformOfficialWhatsAppDeliveryState] = mapped_column(
        SAEnum(
            PlatformOfficialWhatsAppDeliveryState,
            name="platform_official_whatsapp_delivery_state_enum",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda values: [item.value for item in values],
            length=32,
        ),
        nullable=False,
        default=PlatformOfficialWhatsAppDeliveryState.prepared,
        server_default=PlatformOfficialWhatsAppDeliveryState.prepared.value,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_call_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_uncertain_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_type: Mapped[str | None] = mapped_column(String(120), nullable=True)

    connection: Mapped[PlatformOfficialWhatsAppConnection] = relationship(
        "PlatformOfficialWhatsAppConnection",
        foreign_keys=[connection_id],
    )
    requested_by_user: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[requested_by_user_id],
    )
