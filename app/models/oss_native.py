"""Tenant-scoped persistence for native KLM experiences backed by OSS products."""

from __future__ import annotations

import enum
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db.append_only import register_append_only_model
from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin


class OSSNativeSecretError(ValueError):
    """Raised when a native OSS document contains credential material."""


_SENSITIVE_KEYS = {
    "authorization",
    "accesskeyid",
    "clientsecret",
    "cookie",
    "credential",
    "encryptionkey",
    "backupcode",
    "onetimecode",
    "otpcode",
    "password",
    "passwd",
    "privatekey",
    "pwd",
    "refreshtoken",
    "secret",
    "secretkey",
    "sessioncookie",
    "sessionid",
    "sessionkey",
    "sessiontoken",
    "signingkey",
    "token",
    "webhooksecret",
    "webhooktoken",
    "verificationcode",
}
_SENSITIVE_KEY_FRAGMENTS = (
    "accesskey",
    "accesstoken",
    "apikey",
    "backupcode",
    "clientsecret",
    "onetimecode",
    "otpcode",
    "privatekey",
    "refreshtoken",
    "secretkey",
    "sessiontoken",
    "signingkey",
    "webhooksecret",
    "webhooktoken",
    "verificationcode",
)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"^\s*(?:basic|bearer)\s+\S+", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"^(?:gh[pousr]_|sk-)[A-Za-z0-9_-]{16,}$"),
    re.compile(r"^AKIA[A-Z0-9]{16}$"),
    re.compile(r"^[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$"),
    re.compile(r"^[a-z][a-z0-9+.-]*://[^/@\s:]+:[^/@\s]+@", re.IGNORECASE),
    re.compile(
        r"(?:^|[?&;])(?:api[_-]?key|password|secret|token)=[^&;\s]+",
        re.IGNORECASE,
    ),
)


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _is_sensitive_key(value: str) -> bool:
    normalized = _normalized_key(value)
    return (
        normalized in _SENSITIVE_KEYS
        or normalized.endswith("token")
        or any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS)
    )


def _looks_like_secret(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS)


def assert_secret_free_json(
    value: Any,
    *,
    field_name: str,
    max_bytes: int = 256 * 1024,
) -> dict[str, Any]:
    """Validate and copy a JSON object while failing closed on credential material."""

    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a JSON object.")

    node_count = 0

    def visit(item: Any, path: str, depth: int) -> Any:
        nonlocal node_count
        node_count += 1
        if node_count > 10_000:
            raise ValueError(f"{field_name} exceeds the JSON node limit.")
        if depth > 20:
            raise ValueError(f"{field_name} exceeds the JSON nesting limit.")
        if isinstance(item, Mapping):
            copied: dict[str, Any] = {}
            for key, child in item.items():
                if not isinstance(key, str):
                    raise TypeError(f"{field_name} object keys must be strings.")
                if _is_sensitive_key(key):
                    raise OSSNativeSecretError(
                        f"{field_name} contains a forbidden credential field at {path}.{key}."
                    )
                copied[key] = visit(child, f"{path}.{key}", depth + 1)
            return copied
        if isinstance(item, Sequence) and not isinstance(item, str | bytes | bytearray):
            return [visit(child, f"{path}[{index}]", depth + 1) for index, child in enumerate(item)]
        if isinstance(item, str):
            if _looks_like_secret(item):
                raise OSSNativeSecretError(
                    f"{field_name} contains a forbidden credential value at {path}."
                )
            return item
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError(f"{field_name} cannot contain NaN or infinite numbers.")
        if item is None or isinstance(item, bool | int | float):
            return item
        raise TypeError(f"{field_name} contains unsupported JSON value {type(item).__name__}.")

    copied = visit(value, "$", 0)
    encoded = json.dumps(
        copied,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError(f"{field_name} exceeds the {max_bytes}-byte limit.")
    return copied


class SecretFreeJSON(FlexibleJSON):
    """JSONB/JSON type that enforces the no-secrets invariant at persistence time."""

    cache_ok = True

    def __init__(self, *, field_name: str, max_bytes: int = 256 * 1024) -> None:
        super().__init__()
        self.field_name = field_name
        self.max_bytes = max_bytes

    def process_bind_param(self, value: Any, dialect) -> Any:
        if value is None:
            return None
        return assert_secret_free_json(
            value,
            field_name=self.field_name,
            max_bytes=self.max_bytes,
        )


class OSSNativeExecutionMode(enum.StrEnum):
    disabled = "disabled"
    local_native = "local_native"
    external_equivalent = "external_equivalent"
    enterprise_equivalent = "enterprise_equivalent"


class OSSNativeCommandStatus(enum.StrEnum):
    received = "received"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    blocked = "blocked"
    reconciliation_required = "reconciliation_required"


class OSSNativeRecordState(enum.StrEnum):
    active = "active"
    archived = "archived"


class OSSNativeOutboxStatus(enum.StrEnum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class OSSNativeAuditAction(enum.StrEnum):
    config_created = "config_created"
    config_updated = "config_updated"
    command_received = "command_received"
    command_started = "command_started"
    command_succeeded = "command_succeeded"
    command_failed = "command_failed"
    command_blocked = "command_blocked"
    command_reconciliation_required = "command_reconciliation_required"


def _string_enum(enum_type: type[enum.StrEnum], name: str, length: int = 40) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [value.value for value in values],
        length=length,
    )


class OSSNativeCapabilityState(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "oss_native_capability_states"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider",
            "capability",
            name="uq_oss_native_state_workspace_provider_capability",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_oss_native_state_workspace_id",
        ),
        Index(
            "ix_oss_native_state_workspace_provider",
            "workspace_id",
            "provider",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    capability: Mapped[str] = mapped_column(String(128), nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(
        SecretFreeJSON(field_name="configuration", max_bytes=128 * 1024),
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    execution_mode: Mapped[OSSNativeExecutionMode] = mapped_column(
        _string_enum(OSSNativeExecutionMode, "oss_native_execution_mode_enum"),
        nullable=False,
        default=OSSNativeExecutionMode.disabled,
        server_default=OSSNativeExecutionMode.disabled.value,
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    @validates("configuration")
    def _validate_configuration(self, _key: str, value: Any) -> dict[str, Any]:
        return assert_secret_free_json(
            value,
            field_name="configuration",
            max_bytes=128 * 1024,
        )


class OSSNativeCommand(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "oss_native_commands"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider",
            "capability",
            "idempotency_key",
            name="uq_oss_native_command_workspace_provider_capability_key",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_oss_native_command_workspace_id",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "state_id"],
            ["oss_native_capability_states.workspace_id", "oss_native_capability_states.id"],
            name="fk_oss_native_command_workspace_state",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_oss_native_command_workspace_capability_created",
            "workspace_id",
            "provider",
            "capability",
            "created_at",
        ),
        Index(
            "ix_oss_native_command_workspace_status",
            "workspace_id",
            "status",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    state_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        nullable=True,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    capability: Mapped[str] = mapped_column(String(128), nullable=False)
    command_type: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    command_payload: Mapped[dict[str, Any]] = mapped_column(
        SecretFreeJSON(field_name="command payload"),
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    status: Mapped[OSSNativeCommandStatus] = mapped_column(
        _string_enum(OSSNativeCommandStatus, "oss_native_command_status_enum"),
        nullable=False,
        default=OSSNativeCommandStatus.received,
        server_default=OSSNativeCommandStatus.received.value,
    )
    execution_mode: Mapped[OSSNativeExecutionMode] = mapped_column(
        _string_enum(OSSNativeExecutionMode, "oss_native_command_execution_mode_enum"),
        nullable=False,
    )
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    command_result: Mapped[dict[str, Any]] = mapped_column(
        SecretFreeJSON(field_name="command result"),
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    dispatcher_invoked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    external_network_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    row_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @validates("command_payload")
    def _validate_payload(self, _key: str, value: Any) -> dict[str, Any]:
        return assert_secret_free_json(value, field_name="command payload")

    @validates("command_result")
    def _validate_result(self, _key: str, value: Any) -> dict[str, Any]:
        return assert_secret_free_json(value, field_name="command result")


class OSSNativeRecord(UUIDMixin, TimestampMixin, Base):
    """Canonical tenant-owned state for extensible native capability records."""

    __tablename__ = "oss_native_records"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider",
            "capability",
            "scope_key",
            "resource_id",
            name="uq_oss_native_record_workspace_scope_resource",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "last_command_id"],
            ["oss_native_commands.workspace_id", "oss_native_commands.id"],
            name="fk_oss_native_record_workspace_last_command",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_oss_native_record_workspace_capability_state",
            "workspace_id",
            "provider",
            "capability",
            "scope_key",
            "state",
        ),
        Index(
            "ix_oss_native_record_workspace_type_updated",
            "workspace_id",
            "record_type",
            "updated_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    capability: Mapped[str] = mapped_column(String(128), nullable=False)
    scope_key: Mapped[str] = mapped_column(
        String(80), nullable=False, default="capability", server_default="capability"
    )
    resource_id: Mapped[str] = mapped_column(String(180), nullable=False)
    record_type: Mapped[str] = mapped_column(String(120), nullable=False)
    last_operation: Mapped[str] = mapped_column(String(180), nullable=False)
    state: Mapped[OSSNativeRecordState] = mapped_column(
        _string_enum(OSSNativeRecordState, "oss_native_record_state_enum"),
        nullable=False,
        default=OSSNativeRecordState.active,
        server_default=OSSNativeRecordState.active.value,
    )
    data: Mapped[dict[str, Any]] = mapped_column(
        SecretFreeJSON(field_name="native record", max_bytes=256 * 1024),
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    row_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    last_command_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        nullable=True,
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    @validates("data")
    def _validate_data(self, _key: str, value: Any) -> dict[str, Any]:
        return assert_secret_free_json(
            value,
            field_name="native record",
            max_bytes=256 * 1024,
        )


class OSSNativeOutboxEvent(UUIDMixin, TimestampMixin, Base):
    """Transactional hand-off for native asynchronous effects."""

    __tablename__ = "oss_native_outbox_events"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "unit_id",
            "dedupe_key",
            name="uq_oss_native_outbox_workspace_unit_dedupe",
        ),
        Index(
            "ix_oss_native_outbox_status_available",
            "status",
            "available_at",
        ),
        Index(
            "ix_oss_native_outbox_workspace_created",
            "workspace_id",
            "created_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    capability: Mapped[str] = mapped_column(String(128), nullable=False)
    unit_id: Mapped[str] = mapped_column(String(240), nullable=False)
    binding_id: Mapped[str] = mapped_column(String(64), nullable=False)
    topic: Mapped[str] = mapped_column(String(180), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(180), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        SecretFreeJSON(field_name="native outbox payload"),
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    status: Mapped[OSSNativeOutboxStatus] = mapped_column(
        _string_enum(OSSNativeOutboxStatus, "oss_native_outbox_status_enum"),
        nullable=False,
        default=OSSNativeOutboxStatus.pending,
        server_default=OSSNativeOutboxStatus.pending.value,
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(String(240), nullable=True)

    @validates("payload")
    def _validate_payload(self, _key: str, value: Any) -> dict[str, Any]:
        return assert_secret_free_json(value, field_name="native outbox payload")


class OSSNativeAuditEvent(UUIDMixin, Base):
    __tablename__ = "oss_native_audit_events"
    __table_args__ = (
        Index(
            "ix_oss_native_audit_workspace_capability_occurred",
            "workspace_id",
            "provider",
            "capability",
            "occurred_at",
        ),
        Index(
            "ix_oss_native_audit_workspace_action",
            "workspace_id",
            "action",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "state_id"],
            ["oss_native_capability_states.workspace_id", "oss_native_capability_states.id"],
            name="fk_oss_native_audit_workspace_state",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "command_id"],
            ["oss_native_commands.workspace_id", "oss_native_commands.id"],
            name="fk_oss_native_audit_workspace_command",
            ondelete="RESTRICT",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    state_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        nullable=True,
    )
    command_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), nullable=True
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    capability: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[OSSNativeAuditAction] = mapped_column(
        _string_enum(OSSNativeAuditAction, "oss_native_audit_action_enum"), nullable=False
    )
    summary: Mapped[str] = mapped_column(String(240), nullable=False)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audit_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        SecretFreeJSON(field_name="audit metadata", max_bytes=32 * 1024),
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    @validates("audit_metadata")
    def _validate_audit_metadata(self, _key: str, value: Any) -> dict[str, Any]:
        return assert_secret_free_json(
            value,
            field_name="audit metadata",
            max_bytes=32 * 1024,
        )


register_append_only_model(OSSNativeAuditEvent)
