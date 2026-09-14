"""Tenant-scoped event ledger for exact OSS FunctionUnit contracts."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db.append_only import register_append_only_model
from app.db.base import Base, FlexibleUUID, UUIDMixin
from app.models.oss_native import SecretFreeJSON, assert_secret_free_json


class OSSFunctionUnitEventStatus(enum.StrEnum):
    succeeded = "succeeded"
    configured = "configured"
    simulated = "simulated"
    blocked_external = "blocked_external"


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


class OSSFunctionUnitEvent(UUIDMixin, Base):
    """Append-only local execution evidence for one exact licensed FunctionUnit."""

    __tablename__ = "oss_function_unit_events"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "unit_id",
            "idempotency_key",
            name="uq_oss_function_unit_event_idempotency",
        ),
        Index(
            "ix_oss_function_unit_event_workspace_unit_occurred",
            "workspace_id",
            "unit_id",
            "occurred_at",
        ),
        Index(
            "ix_oss_function_unit_event_workspace_resource",
            "workspace_id",
            "resource_id",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    unit_id: Mapped[str] = mapped_column(String(240), nullable=False)
    contract_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    capability: Mapped[str] = mapped_column(String(128), nullable=False)
    semantic_profile: Mapped[str] = mapped_column(String(80), nullable=False)
    operation: Mapped[str] = mapped_column(String(80), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(180), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        SecretFreeJSON(field_name="FunctionUnit event payload"),
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    result: Mapped[dict[str, Any]] = mapped_column(
        SecretFreeJSON(field_name="FunctionUnit event result"),
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    status: Mapped[OSSFunctionUnitEventStatus] = mapped_column(
        _string_enum(OSSFunctionUnitEventStatus, "oss_function_unit_event_status_enum"),
        nullable=False,
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    external_network_executed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    @validates("payload")
    def _validate_payload(self, _key: str, value: Any) -> dict[str, Any]:
        return assert_secret_free_json(value, field_name="FunctionUnit event payload")

    @validates("result")
    def _validate_result(self, _key: str, value: Any) -> dict[str, Any]:
        return assert_secret_free_json(value, field_name="FunctionUnit event result")


register_append_only_model(OSSFunctionUnitEvent)


__all__ = ["OSSFunctionUnitEvent", "OSSFunctionUnitEventStatus"]
