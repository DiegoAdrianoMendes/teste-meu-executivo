"""Tenant-scoped metadata for immutable local object-storage versions."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, FlexibleUUID, TimestampMixin, UUIDMixin


class StorageObjectState(enum.StrEnum):
    pending = "pending"
    quarantined = "quarantined"
    available = "available"


class StorageAVStatus(enum.StrEnum):
    pending = "pending"
    simulated_unverified = "simulated_unverified"
    simulated_blocked = "simulated_blocked"
    clean = "clean"
    infected = "infected"
    error = "error"


def _string_enum(enum_type: type[enum.StrEnum], name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [value.value for value in values],
        length=32,
    )


class StorageObject(UUIDMixin, TimestampMixin, Base):
    """One immutable payload version plus its mutable lifecycle metadata."""

    __tablename__ = "storage_objects"
    __table_args__ = (
        Index("uq_storage_object_workspace_id", "workspace_id", "id", unique=True),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_storage_objects_workspace_matter",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_storage_objects_workspace_client",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "object_id",
            "version",
            name="uq_storage_object_workspace_object_version",
        ),
        UniqueConstraint(
            "object_id",
            "version",
            name="uq_storage_object_object_version",
        ),
        UniqueConstraint("storage_key", name="uq_storage_object_storage_key"),
        Index(
            "uq_storage_object_workspace_idempotency_key",
            "workspace_id",
            "idempotency_key_hash",
            unique=True,
        ),
        Index(
            "ix_storage_object_workspace_object_version",
            "workspace_id",
            "object_id",
            "version",
        ),
        Index(
            "ix_storage_object_workspace_state",
            "workspace_id",
            "state",
            "deleted_at",
        ),
        CheckConstraint("version > 0", name="ck_storage_object_version_positive"),
        CheckConstraint(
            "expected_byte_size IS NULL OR expected_byte_size >= 0",
            name="ck_storage_object_expected_size_nonnegative",
        ),
        CheckConstraint(
            "byte_size IS NULL OR byte_size >= 0",
            name="ck_storage_object_size_nonnegative",
        ),
        CheckConstraint(
            "expected_sha256 IS NULL OR length(expected_sha256) = 64",
            name="ck_storage_object_expected_sha256_length",
        ),
        CheckConstraint(
            "checksum_sha256 IS NULL OR length(checksum_sha256) = 64",
            name="ck_storage_object_sha256_length",
        ),
        CheckConstraint(
            "ticket_epoch >= 0",
            name="ck_storage_object_ticket_epoch_nonnegative",
        ),
        CheckConstraint(
            "reconciliation_attempts >= 0",
            name="ck_storage_object_reconciliation_attempts_nonnegative",
        ),
        CheckConstraint(
            "idempotency_key_hash IS NULL OR length(idempotency_key_hash) = 64",
            name="ck_storage_object_idempotency_key_hash_length",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    object_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        nullable=False,
        default=uuid.uuid4,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    matter_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        nullable=True,
        index=True,
    )
    client_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        nullable=True,
        index=True,
    )

    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    declared_content_type: Mapped[str] = mapped_column(String(127), nullable=False)
    detected_content_type: Mapped[str | None] = mapped_column(String(127), nullable=True)
    expected_byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expected_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Hash only: raw idempotency keys are request credentials and must not be
    # recoverable through metadata, audit, support exports, or database reads.
    idempotency_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    quarantine_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[StorageObjectState] = mapped_column(
        _string_enum(StorageObjectState, "storage_object_state_enum"),
        nullable=False,
        default=StorageObjectState.pending,
        server_default=StorageObjectState.pending.value,
    )
    av_status: Mapped[StorageAVStatus] = mapped_column(
        _string_enum(StorageAVStatus, "storage_av_status_enum"),
        nullable=False,
        default=StorageAVStatus.pending,
        server_default=StorageAVStatus.pending.value,
    )
    quarantine_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scan_method: Mapped[str | None] = mapped_column(String(64), nullable=True)

    retention_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    deleted_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    restored_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    upload_ticket_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    upload_ticket_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    upload_ticket_consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ticket_epoch: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    reconciliation_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    last_reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def is_retained(self) -> bool:
        if self.retention_until is None:
            return False
        now = datetime.now(UTC)
        if self.retention_until.tzinfo is None:
            now = now.replace(tzinfo=None)
        return bool(self.retention_until > now)
