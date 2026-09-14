"""API contracts for tenant-native OSS capability state and commands."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from pydantic import (
    Field,
    JsonValue,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.models.oss_native import (
    OSSNativeAuditAction,
    OSSNativeCommandStatus,
    OSSNativeExecutionMode,
    assert_secret_free_json,
)
from app.schemas.base import CamelCaseSchema, WriteSchema

ProviderKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_-]*$",
    ),
]
CapabilityKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_.:-]*$",
    ),
]
CommandType = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=1,
        max_length=120,
        pattern=r"^[a-z][a-z0-9_.:-]*$",
    ),
]
IdempotencyKey = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=8, max_length=180),
]
JSONObject = dict[str, JsonValue]
OSSNativeCommandEffectState = Literal[
    "not_invoked",
    "in_progress",
    "confirmed",
    "unknown",
]


class OSSNativeCommandOperation(enum.StrEnum):
    """Trusted service intent; request payloads cannot elevate this value."""

    execute = "execute"
    dry_run = "dry-run"
    configure = "configure"


def _secret_free_object(value: Any, field_name: str, max_bytes: int) -> JSONObject:
    if value is None:
        return {}
    return cast(
        JSONObject,
        assert_secret_free_json(value, field_name=field_name, max_bytes=max_bytes),
    )


class OSSNativeConfigPatch(WriteSchema):
    expected_version: int = Field(ge=0)
    configuration: JSONObject | None = None
    execution_mode: OSSNativeExecutionMode | None = None

    @field_validator("configuration", mode="before")
    @classmethod
    def validate_configuration(cls, value: Any) -> JSONObject | None:
        if value is None:
            return None
        return _secret_free_object(value, "configuration", 128 * 1024)

    @model_validator(mode="after")
    def require_change(self) -> OSSNativeConfigPatch:
        if self.configuration is None and self.execution_mode is None:
            raise ValueError("Informe configuration ou executionMode.")
        return self


class OSSNativeCommandCreate(WriteSchema):
    command_type: CommandType
    idempotency_key: IdempotencyKey
    expected_version: int = Field(ge=0)
    payload: JSONObject = Field(default_factory=dict)

    @field_validator("payload", mode="before")
    @classmethod
    def validate_payload(cls, value: Any) -> JSONObject:
        return _secret_free_object(value, "command payload", 256 * 1024)


class OSSNativeDocumentActionWrite(WriteSchema):
    action_id: CommandType
    values: JSONObject = Field(default_factory=dict)
    transient_secrets: dict[CommandType, SecretStr] = Field(default_factory=dict)
    mode: Literal["execute", "dry-run", "configure"] = "execute"
    version: str | int = "0"
    idempotency_key: IdempotencyKey
    context: JSONObject = Field(default_factory=dict)

    @field_validator("values", mode="before")
    @classmethod
    def validate_values(cls, value: Any) -> JSONObject:
        return _secret_free_object(value, "action values", 256 * 1024)

    @field_validator("context", mode="before")
    @classmethod
    def validate_context(cls, value: Any) -> JSONObject:
        return _secret_free_object(value, "action context", 32 * 1024)


class OSSNativeCapabilityStateRead(CamelCaseSchema):
    id: UUID | None = None
    workspace_id: UUID
    provider: str
    capability: str
    configured: bool = True
    configuration: JSONObject = Field(default_factory=dict)
    config_hash: str
    config_version: int = Field(ge=0)
    execution_mode: OSSNativeExecutionMode
    updated_by_user_id: UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class OSSNativeCommandRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    provider: str
    capability: str
    command_type: str
    idempotency_key: str
    payload_hash: str
    status: OSSNativeCommandStatus
    effect_state: OSSNativeCommandEffectState = "not_invoked"
    is_success: bool = False
    is_terminal: bool = False
    requires_reconciliation: bool = False
    automatic_retry_allowed: bool = False
    execution_mode: OSSNativeExecutionMode
    state_version: int = Field(ge=0)
    result: JSONObject = Field(default_factory=dict, validation_alias="command_result")
    error_code: str | None = None
    error_message: str | None = None
    requested_by_user_id: UUID | None = None
    dispatcher_invoked: bool = False
    external_network_allowed: bool = False
    row_version: int = Field(ge=1)
    started_at: datetime | None = None
    lease_expires_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    deduplicated: bool = False

    @model_validator(mode="after")
    def derive_execution_contract(self) -> OSSNativeCommandRead:
        terminal_statuses = {
            OSSNativeCommandStatus.succeeded,
            OSSNativeCommandStatus.failed,
            OSSNativeCommandStatus.blocked,
            OSSNativeCommandStatus.reconciliation_required,
        }
        if self.status == OSSNativeCommandStatus.succeeded:
            effect_state: OSSNativeCommandEffectState = "confirmed"
        elif self.status == OSSNativeCommandStatus.running:
            effect_state = "in_progress"
        elif self.status == OSSNativeCommandStatus.reconciliation_required or (
            self.status == OSSNativeCommandStatus.failed and self.dispatcher_invoked
        ):
            effect_state = "unknown"
        else:
            effect_state = "not_invoked"

        object.__setattr__(self, "effect_state", effect_state)
        object.__setattr__(
            self,
            "is_success",
            self.status == OSSNativeCommandStatus.succeeded,
        )
        object.__setattr__(self, "is_terminal", self.status in terminal_statuses)
        object.__setattr__(
            self,
            "requires_reconciliation",
            self.status == OSSNativeCommandStatus.reconciliation_required
            or (self.status == OSSNativeCommandStatus.failed and self.dispatcher_invoked),
        )
        object.__setattr__(self, "automatic_retry_allowed", False)
        return self


class OSSNativeAuditEventRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    state_id: UUID | None = None
    command_id: UUID | None = None
    actor_user_id: UUID | None = None
    provider: str
    capability: str
    action: OSSNativeAuditAction
    summary: str
    payload_hash: str | None = None
    state_version: int | None = None
    metadata: JSONObject = Field(default_factory=dict, validation_alias="audit_metadata")
    occurred_at: datetime


class OSSNativeSnapshotRead(CamelCaseSchema):
    state: OSSNativeCapabilityStateRead
    commands: list[OSSNativeCommandRead] = Field(default_factory=list)
    audit_trail: list[OSSNativeAuditEventRead] = Field(default_factory=list)
