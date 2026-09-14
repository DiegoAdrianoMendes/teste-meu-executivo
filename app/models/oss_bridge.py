"""Portable persistence models for local OSS integration bridges."""

from __future__ import annotations

import enum
import math
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin

REDACTED_SECRET = "[REDACTED]"

_SENSITIVE_KEY_FRAGMENTS = (
    "accesstoken",
    "apikey",
    "authorization",
    "backupcode",
    "clientsecret",
    "cookie",
    "credential",
    "onetimecode",
    "otpcode",
    "password",
    "privatekey",
    "refreshtoken",
    "secret",
    "sessionid",
    "sessiontoken",
    "signingkey",
    "webhooktoken",
    "verificationcode",
)
_SENSITIVE_KEYS = {
    "auth",
    "cookie",
    "passwd",
    "proxyauthorization",
    "pwd",
    "setcookie",
    "token",
}
_SECRET_VALUE_PATTERNS = (
    re.compile(r"^\s*(?:basic|bearer)\s+\S+", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"^(?:gh[pousr]_|sk-)[A-Za-z0-9_-]{16,}$"),
)


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return (
        normalized in _SENSITIVE_KEYS
        or normalized.endswith("token")
        or any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS)
    )


def _looks_like_secret(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS)


def sanitize_oss_json(value: Any) -> Any:
    """Return a JSON-safe deep copy with basic credential redaction."""
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("OSS bridge JSON object keys must be strings.")
            sanitized[key] = REDACTED_SECRET if _is_sensitive_key(key) else sanitize_oss_json(item)
        return sanitized
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [sanitize_oss_json(item) for item in value]
    if isinstance(value, str):
        return REDACTED_SECRET if _looks_like_secret(value) else value
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("OSS bridge JSON cannot contain NaN or infinite numbers.")
    if value is None or isinstance(value, bool | int | float):
        return value
    raise TypeError(f"Unsupported OSS bridge JSON value: {type(value).__name__}.")


class SanitizedJSON(FlexibleJSON):
    """Flexible JSON that redacts credentials at the database boundary."""

    cache_ok = True

    def process_bind_param(self, value: Any, dialect) -> Any:
        return None if value is None else sanitize_oss_json(value)


class OSSBridgeProvider(enum.StrEnum):
    chatwoot = "chatwoot"
    rocketchat = "rocketchat"
    chattermate = "chattermate"
    bottlecrm = "bottlecrm"


class ExternalEntitySyncState(enum.StrEnum):
    pending = "pending"
    synced = "synced"
    stale = "stale"
    failed = "failed"
    disabled = "disabled"


class IntegrationEventReceiptStatus(enum.StrEnum):
    received = "received"
    processing = "processing"
    processed = "processed"
    failed = "failed"
    ignored = "ignored"


def _provider_enum() -> Enum:
    return Enum(
        OSSBridgeProvider,
        name="oss_bridge_provider_enum",
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        length=32,
    )


class ExternalEntityLink(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "oss_external_entity_links"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider",
            "external_account_id",
            "klm_entity_type",
            "klm_entity_id",
            "external_entity_type",
            name="uq_oss_link_workspace_provider_klm_entity",
        ),
        UniqueConstraint(
            "workspace_id",
            "provider",
            "external_account_id",
            "external_entity_type",
            "external_entity_id",
            name="uq_oss_link_workspace_provider_external_entity",
        ),
        Index(
            "uq_oss_chatwoot_active_account_binding",
            "provider",
            "external_account_id",
            unique=True,
            postgresql_where=text(
                "provider = 'chatwoot' AND klm_entity_type = 'workspace' "
                "AND external_entity_type = 'account' AND sync_state = 'synced'"
            ),
            sqlite_where=text(
                "provider = 'chatwoot' AND klm_entity_type = 'workspace' "
                "AND external_entity_type = 'account' AND sync_state = 'synced'"
            ),
        ),
        Index(
            "uq_oss_bottlecrm_active_organization_binding",
            "provider",
            "external_account_id",
            unique=True,
            postgresql_where=text(
                "provider = 'bottlecrm' AND klm_entity_type = 'workspace' "
                "AND external_entity_type = 'organization' AND sync_state = 'synced'"
            ),
            sqlite_where=text(
                "provider = 'bottlecrm' AND klm_entity_type = 'workspace' "
                "AND external_entity_type = 'organization' AND sync_state = 'synced'"
            ),
        ),
        Index(
            "uq_oss_rocketchat_active_server_binding",
            "provider",
            "external_account_id",
            unique=True,
            postgresql_where=text(
                "provider = 'rocketchat' AND klm_entity_type = 'workspace' "
                "AND external_entity_type = 'account' AND sync_state = 'synced'"
            ),
            sqlite_where=text(
                "provider = 'rocketchat' AND klm_entity_type = 'workspace' "
                "AND external_entity_type = 'account' AND sync_state = 'synced'"
            ),
        ),
        Index(
            "uq_oss_chattermate_active_organization_binding",
            "provider",
            "external_account_id",
            unique=True,
            postgresql_where=text(
                "provider = 'chattermate' AND klm_entity_type = 'workspace' "
                "AND external_entity_type = 'organization' AND sync_state = 'synced'"
            ),
            sqlite_where=text(
                "provider = 'chattermate' AND klm_entity_type = 'workspace' "
                "AND external_entity_type = 'organization' AND sync_state = 'synced'"
            ),
        ),
        Index("ix_oss_links_workspace_sync_state", "workspace_id", "provider", "sync_state"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[OSSBridgeProvider] = mapped_column(_provider_enum(), nullable=False)
    klm_entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    klm_entity_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    external_entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    link_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        SanitizedJSON(),
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    sync_state: Mapped[ExternalEntitySyncState] = mapped_column(
        Enum(
            ExternalEntitySyncState,
            name="oss_bridge_sync_state_enum",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=32,
        ),
        nullable=False,
        default=ExternalEntitySyncState.pending,
        server_default=ExternalEntitySyncState.pending.value,
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    @validates("link_metadata")
    def _sanitize_link_metadata(
        self,
        _key: str,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        return sanitize_oss_json(value)


class IntegrationEventReceipt(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "oss_integration_event_receipts"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider",
            "external_event_id",
            name="uq_oss_receipt_workspace_provider_event",
        ),
        Index(
            "ix_oss_receipts_workspace_status_received",
            "workspace_id",
            "status",
            "received_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[OSSBridgeProvider] = mapped_column(_provider_enum(), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[IntegrationEventReceiptStatus] = mapped_column(
        Enum(
            IntegrationEventReceiptStatus,
            name="oss_bridge_receipt_status_enum",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=32,
        ),
        nullable=False,
        default=IntegrationEventReceiptStatus.received,
        server_default=IntegrationEventReceiptStatus.received.value,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        SanitizedJSON(),
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @validates("payload")
    def _sanitize_payload(
        self,
        _key: str,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        return sanitize_oss_json(value)
