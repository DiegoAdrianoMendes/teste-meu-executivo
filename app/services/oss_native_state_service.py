"""State, command, and dispatch orchestration for native OSS experiences."""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Protocol, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.tenant import set_workspace_context
from app.models.oss_native import (
    OSSNativeAuditAction,
    OSSNativeAuditEvent,
    OSSNativeCapabilityState,
    OSSNativeCommand,
    OSSNativeCommandStatus,
    OSSNativeExecutionMode,
    assert_secret_free_json,
)
from app.schemas.oss_native import (
    OSSNativeAuditEventRead,
    OSSNativeCapabilityStateRead,
    OSSNativeCommandCreate,
    OSSNativeCommandOperation,
    OSSNativeCommandRead,
    OSSNativeConfigPatch,
    OSSNativeSnapshotRead,
)

_PROVIDER_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_CAPABILITY_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
DEFAULT_OSS_NATIVE_COMMAND_LEASE = timedelta(minutes=5)
_EXTERNAL_EFFECT_MARKERS = {
    "externalactionsexecuted",
    "externaldispatchallowed",
    "externaldispatchexecuted",
    "externaleffects",
    "externalmutation",
    "externalnetworkallowed",
    "externalnetworkexecuted",
    "networkallowed",
    "networkcallperformed",
    "networkexecuted",
    "remotewriteperformed",
    "upstreamexecutionallowed",
}
_EXTERNAL_EFFECT_SOURCE_HINTS = ("external", "network", "remote", "upstream")
_EXTERNAL_EFFECT_ACTION_HINTS = (
    "call",
    "dispatch",
    "effect",
    "executed",
    "mutation",
    "performed",
    "request",
    "send",
    "sent",
    "write",
)
_NEGATIVE_EXTERNAL_EFFECT_STRINGS = {
    "",
    "0",
    "blocked",
    "closed",
    "denied",
    "disabled",
    "false",
    "local",
    "localonly",
    "no",
    "none",
    "notallowed",
    "notexecuted",
    "notperformed",
    "null",
}


class OSSNativeExternalEffectReportedError(RuntimeError):
    """Raised when a local-only dispatcher reports positive external-effect evidence."""

    def __init__(self, marker_path: str) -> None:
        super().__init__(
            f"Native dispatcher reported a forbidden external effect at {marker_path}."
        )
        self.marker_path = marker_path


def _looks_like_external_effect_marker(normalized: str) -> bool:
    return normalized in _EXTERNAL_EFFECT_MARKERS or (
        any(hint in normalized for hint in _EXTERNAL_EFFECT_SOURCE_HINTS)
        and any(hint in normalized for hint in _EXTERNAL_EFFECT_ACTION_HINTS)
    )


def _external_effect_value_is_positive(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str):
        normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
        return normalized not in _NEGATIVE_EXTERNAL_EFFECT_STRINGS
    return True


def _reject_positive_external_effect_markers(value: Any, *, path: tuple[str, ...] = ()) -> None:
    """Fail closed if a dispatcher contradicts its external-network prohibition."""

    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            child_path = (*path, str(key))
            marker_path = ".".join(child_path)
            normalized_path = re.sub(
                r"[^a-z0-9]",
                "",
                marker_path.casefold(),
            )
            if (
                _looks_like_external_effect_marker(normalized)
                or _looks_like_external_effect_marker(normalized_path)
            ) and _external_effect_value_is_positive(child):
                raise OSSNativeExternalEffectReportedError(marker_path)
            _reject_positive_external_effect_markers(child, path=child_path)
    elif isinstance(value, Sequence) and not isinstance(
        value, str | bytes | bytearray
    ):
        for index, child in enumerate(value):
            _reject_positive_external_effect_markers(child, path=(*path, str(index)))


class OSSNativeVersionConflictError(RuntimeError):
    def __init__(self, current_version: int) -> None:
        super().__init__("The native OSS capability changed since it was read.")
        self.current_version = current_version


class OSSNativeCommandConflictError(RuntimeError):
    """Raised when an idempotency key is replayed with another request."""


@dataclass(frozen=True)
class OSSNativeDispatchContext:
    db: AsyncSession
    workspace_id: UUID
    actor_user_id: UUID | None
    command_id: UUID
    provider: str
    capability: str
    command_type: str
    operation: OSSNativeCommandOperation
    payload: dict[str, Any]
    configuration: dict[str, Any]
    config_version: int
    external_network_allowed: bool = False


class OSSNativeProviderDispatcher(Protocol):
    def __call__(
        self,
        context: OSSNativeDispatchContext,
    ) -> Awaitable[dict[str, Any]] | dict[str, Any]: ...


DispatcherEntry = OSSNativeProviderDispatcher | str


@dataclass(frozen=True)
class OSSNativeServerExecutionPolicy:
    catalogued: bool
    native_mode: str
    external_execution_state: str
    execute_allowed: bool


OSSNativeExecutionPolicyResolver = Callable[
    [str, str],
    OSSNativeServerExecutionPolicy,
]


@lru_cache(maxsize=1)
def _catalog_execution_policies() -> dict[tuple[str, str], OSSNativeServerExecutionPolicy]:
    from app.services.oss_capability_registry import oss_capability_registry

    policies: dict[tuple[str, str], OSSNativeServerExecutionPolicy] = {}
    for item in oss_capability_registry()["capabilities"]:
        native_mode = str(item["nativeMode"])
        external_state = str(item["externalExecutionState"])
        source_disposition = str(item["sourceDisposition"])
        gated = native_mode == "gated_configuration" or source_disposition in {
            "blocked_external_gate",
            "blocked_dependency",
        }
        policies[(str(item["provider"]), str(item["key"]))] = OSSNativeServerExecutionPolicy(
            catalogued=True,
            native_mode=native_mode,
            external_execution_state=external_state,
            execute_allowed=not gated,
        )
    return policies


def resolve_oss_native_execution_policy(
    provider: str,
    capability: str,
) -> OSSNativeServerExecutionPolicy:
    """Resolve the immutable server catalog gate for one native capability."""

    return _catalog_execution_policies().get(
        (_normalize_provider(provider), _normalize_capability(capability)),
        OSSNativeServerExecutionPolicy(
            catalogued=False,
            native_mode="unknown",
            external_execution_state="blocked_not_catalogued",
            execute_allowed=False,
        ),
    )


def _normalize_provider(value: str) -> str:
    normalized = value.strip().lower()
    if not _PROVIDER_PATTERN.fullmatch(normalized):
        raise ValueError("Invalid native OSS provider identifier.")
    return normalized


def _normalize_capability(value: str) -> str:
    normalized = value.strip().lower()
    if not _CAPABILITY_PATTERN.fullmatch(normalized):
        raise ValueError("Invalid native OSS capability identifier.")
    return normalized


class OSSNativeDispatcherRegistry:
    """Provider registry whose string targets are imported only at dispatch time."""

    def __init__(self) -> None:
        self._entries: dict[str, DispatcherEntry] = {}

    def register(self, provider: str, dispatcher: DispatcherEntry) -> None:
        provider_key = _normalize_provider(provider)
        if isinstance(dispatcher, str):
            module_name, separator, attribute = dispatcher.partition(":")
            if not separator or not module_name or not attribute:
                raise ValueError("Dispatcher import paths must use 'module:attribute'.")
        elif not callable(dispatcher):
            raise TypeError("Dispatcher must be callable or a late import path.")
        self._entries[provider_key] = dispatcher

    def unregister(self, provider: str) -> None:
        self._entries.pop(_normalize_provider(provider), None)

    def resolve(self, provider: str) -> OSSNativeProviderDispatcher | None:
        provider_key = _normalize_provider(provider)
        entry = self._entries.get(provider_key)
        if entry is None:
            return None
        if not isinstance(entry, str):
            return entry

        module_name, _, attribute = entry.partition(":")
        target: Any = getattr(importlib.import_module(module_name), attribute)
        if not callable(target) and callable(getattr(target, "dispatch", None)):
            target = target.dispatch
        if not callable(target):
            raise TypeError(f"Dispatcher target for {provider_key} is not callable.")
        resolved = cast(OSSNativeProviderDispatcher, target)
        self._entries[provider_key] = resolved
        return resolved


provider_dispatchers = OSSNativeDispatcherRegistry()


def register_provider_dispatcher(provider: str, dispatcher: DispatcherEntry) -> None:
    provider_dispatchers.register(provider, dispatcher)


def unregister_provider_dispatcher(provider: str) -> None:
    provider_dispatchers.unregister(provider)


def _canonical_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _configuration_hash(configuration: dict[str, Any]) -> str:
    return _canonical_hash(configuration)


def _command_hash(
    data: OSSNativeCommandCreate,
    operation: OSSNativeCommandOperation,
) -> str:
    return _canonical_hash(
        {
            "operation": operation.value,
            "payload": data.payload,
        }
    )


def _same_command_request(
    command: OSSNativeCommand,
    data: OSSNativeCommandCreate,
    payload_hash: str,
) -> bool:
    return (
        command.payload_hash == payload_hash
        and command.command_type == data.command_type
        and command.state_version == data.expected_version
    )


_AUDIT_SUMMARIES = {
    OSSNativeAuditAction.config_created: "Native OSS capability configuration created.",
    OSSNativeAuditAction.config_updated: "Native OSS capability configuration updated.",
    OSSNativeAuditAction.command_received: "Native OSS command received.",
    OSSNativeAuditAction.command_started: "Native OSS command dispatcher started.",
    OSSNativeAuditAction.command_succeeded: "Native OSS command completed.",
    OSSNativeAuditAction.command_failed: "Native OSS command failed safely.",
    OSSNativeAuditAction.command_blocked: "Native OSS command persisted without execution.",
    OSSNativeAuditAction.command_reconciliation_required: (
        "Native OSS command requires reconciliation without automatic retry."
    ),
}


class OSSNativeStateService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        dispatchers: OSSNativeDispatcherRegistry = provider_dispatchers,
        running_lease: timedelta = DEFAULT_OSS_NATIVE_COMMAND_LEASE,
        execution_policy_resolver: OSSNativeExecutionPolicyResolver = (
            resolve_oss_native_execution_policy
        ),
    ) -> None:
        if running_lease <= timedelta(0):
            raise ValueError("The native OSS command lease must be positive.")
        self.db = db
        self.dispatchers = dispatchers
        self.running_lease = running_lease
        self.execution_policy_resolver = execution_policy_resolver

    def _execution_policy(
        self,
        provider: str,
        capability: str,
    ) -> OSSNativeServerExecutionPolicy:
        try:
            return self.execution_policy_resolver(provider, capability)
        except Exception:
            return OSSNativeServerExecutionPolicy(
                catalogued=False,
                native_mode="unknown",
                external_execution_state="blocked_catalog_unavailable",
                execute_allowed=False,
            )

    def _constrain_execution_mode(
        self,
        provider: str,
        capability: str,
        requested_mode: OSSNativeExecutionMode,
    ) -> tuple[OSSNativeExecutionMode, OSSNativeServerExecutionPolicy]:
        policy = self._execution_policy(provider, capability)
        if requested_mode != OSSNativeExecutionMode.local_native:
            return requested_mode, policy
        if not policy.catalogued:
            return OSSNativeExecutionMode.disabled, policy
        if not policy.execute_allowed:
            return OSSNativeExecutionMode.external_equivalent, policy
        return requested_mode, policy

    async def _bind(self, workspace_id: UUID) -> None:
        await set_workspace_context(self.db, workspace_id)

    async def _state(
        self,
        workspace_id: UUID,
        provider: str,
        capability: str,
    ) -> OSSNativeCapabilityState | None:
        return await self.db.scalar(
            select(OSSNativeCapabilityState).where(
                OSSNativeCapabilityState.workspace_id == workspace_id,
                OSSNativeCapabilityState.provider == provider,
                OSSNativeCapabilityState.capability == capability,
            )
        )

    async def _command(
        self,
        workspace_id: UUID,
        provider: str,
        capability: str,
        idempotency_key: str,
    ) -> OSSNativeCommand | None:
        return await self.db.scalar(
            select(OSSNativeCommand).where(
                OSSNativeCommand.workspace_id == workspace_id,
                OSSNativeCommand.provider == provider,
                OSSNativeCommand.capability == capability,
                OSSNativeCommand.idempotency_key == idempotency_key,
            )
        )

    async def _audit(
        self,
        *,
        workspace_id: UUID,
        provider: str,
        capability: str,
        action: OSSNativeAuditAction,
        actor_user_id: UUID | None,
        state: OSSNativeCapabilityState | None = None,
        command: OSSNativeCommand | None = None,
        payload_hash: str | None = None,
        state_version: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OSSNativeAuditEvent:
        safe_metadata = assert_secret_free_json(
            metadata or {},
            field_name="audit metadata",
            max_bytes=32 * 1024,
        )
        event = OSSNativeAuditEvent(
            workspace_id=workspace_id,
            state_id=state.id if state else None,
            command_id=command.id if command else None,
            actor_user_id=actor_user_id,
            provider=provider,
            capability=capability,
            action=action,
            summary=_AUDIT_SUMMARIES[action],
            payload_hash=payload_hash,
            state_version=state_version,
            audit_metadata=safe_metadata,
        )
        self.db.add(event)
        await self.db.flush()
        return event

    async def get_snapshot(
        self,
        workspace_id: UUID,
        provider: str,
        capability: str,
        *,
        command_limit: int = 25,
        audit_limit: int = 50,
    ) -> OSSNativeSnapshotRead:
        provider_key = _normalize_provider(provider)
        capability_key = _normalize_capability(capability)
        await self._bind(workspace_id)
        state = await self._state(workspace_id, provider_key, capability_key)
        commands = list(
            (
                await self.db.scalars(
                    select(OSSNativeCommand)
                    .where(
                        OSSNativeCommand.workspace_id == workspace_id,
                        OSSNativeCommand.provider == provider_key,
                        OSSNativeCommand.capability == capability_key,
                    )
                    .order_by(OSSNativeCommand.created_at.desc(), OSSNativeCommand.id.desc())
                    .limit(max(1, min(command_limit, 100)))
                )
            ).all()
        )
        audit_events = list(
            (
                await self.db.scalars(
                    select(OSSNativeAuditEvent)
                    .where(
                        OSSNativeAuditEvent.workspace_id == workspace_id,
                        OSSNativeAuditEvent.provider == provider_key,
                        OSSNativeAuditEvent.capability == capability_key,
                    )
                    .order_by(
                        OSSNativeAuditEvent.occurred_at.desc(),
                        OSSNativeAuditEvent.id.desc(),
                    )
                    .limit(max(1, min(audit_limit, 200)))
                )
            ).all()
        )

        if state is None:
            state_read = OSSNativeCapabilityStateRead(
                id=None,
                workspace_id=workspace_id,
                provider=provider_key,
                capability=capability_key,
                configured=False,
                configuration={},
                config_hash=_configuration_hash({}),
                config_version=0,
                execution_mode=OSSNativeExecutionMode.disabled,
            )
        else:
            state_read = OSSNativeCapabilityStateRead.model_validate(state)
        return OSSNativeSnapshotRead(
            state=state_read,
            commands=[self._command_read(command, deduplicated=False) for command in commands],
            audit_trail=[OSSNativeAuditEventRead.model_validate(event) for event in audit_events],
        )

    async def update_config(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        provider: str,
        capability: str,
        data: OSSNativeConfigPatch,
    ) -> OSSNativeSnapshotRead:
        provider_key = _normalize_provider(provider)
        capability_key = _normalize_capability(capability)
        await self._bind(workspace_id)
        state = await self._state(workspace_id, provider_key, capability_key)
        requested_mode = data.execution_mode or (
            state.execution_mode if state else OSSNativeExecutionMode.disabled
        )
        effective_mode, execution_policy = self._constrain_execution_mode(
            provider_key,
            capability_key,
            requested_mode,
        )

        if state is None:
            if data.expected_version != 0:
                raise OSSNativeVersionConflictError(0)
            configuration = assert_secret_free_json(
                data.configuration or {},
                field_name="configuration",
                max_bytes=128 * 1024,
            )
            state = OSSNativeCapabilityState(
                workspace_id=workspace_id,
                provider=provider_key,
                capability=capability_key,
                configuration=configuration,
                config_hash=_configuration_hash(configuration),
                config_version=1,
                execution_mode=effective_mode,
                updated_by_user_id=actor_user_id,
            )
            try:
                async with self.db.begin_nested():
                    self.db.add(state)
                    await self.db.flush()
            except IntegrityError:
                current = await self._state(workspace_id, provider_key, capability_key)
                raise OSSNativeVersionConflictError(
                    current.config_version if current else 0
                ) from None
            action = OSSNativeAuditAction.config_created
        else:
            if state.config_version != data.expected_version:
                raise OSSNativeVersionConflictError(state.config_version)
            configuration = assert_secret_free_json(
                data.configuration if data.configuration is not None else state.configuration,
                field_name="configuration",
                max_bytes=128 * 1024,
            )
            next_mode = effective_mode
            next_version = data.expected_version + 1
            result = await self.db.execute(
                update(OSSNativeCapabilityState)
                .where(
                    OSSNativeCapabilityState.id == state.id,
                    OSSNativeCapabilityState.workspace_id == workspace_id,
                    OSSNativeCapabilityState.config_version == data.expected_version,
                )
                .values(
                    configuration=configuration,
                    config_hash=_configuration_hash(configuration),
                    config_version=next_version,
                    execution_mode=next_mode,
                    updated_by_user_id=actor_user_id,
                    updated_at=datetime.now(UTC),
                )
                .execution_options(synchronize_session="fetch")
            )
            if result.rowcount != 1:
                await self.db.rollback()
                await self._bind(workspace_id)
                current = await self._state(workspace_id, provider_key, capability_key)
                raise OSSNativeVersionConflictError(current.config_version if current else 0)
            state.configuration = configuration
            state.config_hash = _configuration_hash(configuration)
            state.config_version = next_version
            state.execution_mode = next_mode
            state.updated_by_user_id = actor_user_id
            action = OSSNativeAuditAction.config_updated

        await self._audit(
            workspace_id=workspace_id,
            provider=provider_key,
            capability=capability_key,
            action=action,
            actor_user_id=actor_user_id,
            state=state,
            payload_hash=state.config_hash,
            state_version=state.config_version,
            metadata={
                "configVersion": state.config_version,
                "executionMode": state.execution_mode.value,
                "executionModeConstrained": requested_mode != effective_mode,
                "externalExecutionState": execution_policy.external_execution_state,
                "requestedExecutionMode": requested_mode.value,
            },
        )
        await self.db.commit()
        await self.db.refresh(state)
        return await self.get_snapshot(workspace_id, provider_key, capability_key)

    def _command_read(
        self,
        command: OSSNativeCommand,
        *,
        deduplicated: bool,
    ) -> OSSNativeCommandRead:
        lease_expires_at: datetime | None = None
        lease_expired = False
        if (
            command.status
            in {
                OSSNativeCommandStatus.running,
                OSSNativeCommandStatus.reconciliation_required,
            }
            and command.started_at is not None
        ):
            started_at = command.started_at
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=UTC)
            else:
                started_at = started_at.astimezone(UTC)
            lease_expires_at = started_at + self.running_lease
            lease_expired = lease_expires_at <= datetime.now(UTC)

        read = OSSNativeCommandRead.model_validate(command)
        updates: dict[str, Any] = {
            "deduplicated": deduplicated,
            "lease_expires_at": lease_expires_at,
        }
        if command.status == OSSNativeCommandStatus.running and (
            command.started_at is None or lease_expired
        ):
            updates.update(
                {
                    "effect_state": "unknown",
                    "requires_reconciliation": True,
                }
            )
        return read.model_copy(update=updates)

    async def _reconcile_expired_running_command(
        self,
        *,
        command: OSSNativeCommand,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        provider: str,
        capability: str,
    ) -> OSSNativeCommand:
        if command.status != OSSNativeCommandStatus.running:
            return command

        now = datetime.now(UTC)
        started_at = command.started_at
        if started_at is not None:
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=UTC)
            else:
                started_at = started_at.astimezone(UTC)
            if started_at + self.running_lease > now:
                return command
            error_code = "command_lease_expired"
        else:
            error_code = "command_lease_missing"

        expected_row_version = command.row_version
        result = await self.db.execute(
            update(OSSNativeCommand)
            .where(
                OSSNativeCommand.id == command.id,
                OSSNativeCommand.workspace_id == workspace_id,
                OSSNativeCommand.status == OSSNativeCommandStatus.running,
                OSSNativeCommand.row_version == expected_row_version,
            )
            .values(
                status=OSSNativeCommandStatus.reconciliation_required,
                error_code=error_code,
                error_message=(
                    "Execution could not be confirmed before its lease expired. "
                    "Reconcile the effect before issuing another command."
                ),
                completed_at=now,
                row_version=expected_row_version + 1,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            current = await self.db.scalar(
                select(OSSNativeCommand)
                .where(
                    OSSNativeCommand.id == command.id,
                    OSSNativeCommand.workspace_id == workspace_id,
                )
                .execution_options(populate_existing=True)
            )
            return cast(OSSNativeCommand, current)

        await self.db.refresh(command)
        state = await self._state(workspace_id, provider, capability)
        await self._audit(
            workspace_id=workspace_id,
            provider=provider,
            capability=capability,
            action=OSSNativeAuditAction.command_reconciliation_required,
            actor_user_id=actor_user_id,
            state=state,
            command=command,
            payload_hash=command.payload_hash,
            state_version=command.state_version,
            metadata={
                "automaticRetryAllowed": False,
                "effectState": "unknown",
                "errorCode": error_code,
                "leaseSeconds": int(self.running_lease.total_seconds()),
                "status": OSSNativeCommandStatus.reconciliation_required.value,
            },
        )
        await self.db.commit()
        await self.db.refresh(command)
        return command

    async def _reconcile_abandoned_received_command(
        self,
        *,
        command: OSSNativeCommand,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        provider: str,
        capability: str,
    ) -> OSSNativeCommand:
        """Close legacy committed `received` rows without replaying a dispatcher."""

        if command.status != OSSNativeCommandStatus.received:
            return command

        now = datetime.now(UTC)
        dispatcher_was_invoked = bool(command.dispatcher_invoked)
        next_status = (
            OSSNativeCommandStatus.reconciliation_required
            if dispatcher_was_invoked
            else OSSNativeCommandStatus.failed
        )
        error_code = (
            "received_command_effect_unknown"
            if dispatcher_was_invoked
            else "command_abandoned_before_dispatch"
        )
        safe_result = {
            "persistedLocally": True,
            "dispatcherInvoked": dispatcher_was_invoked,
            "effectState": "unknown" if dispatcher_was_invoked else "not_executed",
            "safeToRetryWithNewKey": not dispatcher_was_invoked,
        }
        expected_row_version = command.row_version
        result = await self.db.execute(
            update(OSSNativeCommand)
            .where(
                OSSNativeCommand.id == command.id,
                OSSNativeCommand.workspace_id == workspace_id,
                OSSNativeCommand.status == OSSNativeCommandStatus.received,
                OSSNativeCommand.row_version == expected_row_version,
            )
            .values(
                status=next_status,
                command_result=safe_result,
                error_code=error_code,
                error_message=(
                    "A committed received command claimed dispatcher execution and requires "
                    "human reconciliation."
                    if dispatcher_was_invoked
                    else (
                        "The command stopped before dispatch and was closed without "
                        "external effect."
                    )
                ),
                completed_at=now,
                row_version=expected_row_version + 1,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            current = await self.db.scalar(
                select(OSSNativeCommand)
                .where(
                    OSSNativeCommand.id == command.id,
                    OSSNativeCommand.workspace_id == workspace_id,
                )
                .execution_options(populate_existing=True)
            )
            return cast(OSSNativeCommand, current)

        await self.db.refresh(command)
        state = await self._state(workspace_id, provider, capability)
        await self._audit(
            workspace_id=workspace_id,
            provider=provider,
            capability=capability,
            action=(
                OSSNativeAuditAction.command_reconciliation_required
                if dispatcher_was_invoked
                else OSSNativeAuditAction.command_failed
            ),
            actor_user_id=actor_user_id,
            state=state,
            command=command,
            payload_hash=command.payload_hash,
            state_version=command.state_version,
            metadata={
                "automaticRetryAllowed": False,
                "effectState": safe_result["effectState"],
                "errorCode": error_code,
                "safeToRetryWithNewKey": safe_result["safeToRetryWithNewKey"],
                "status": next_status.value,
            },
        )
        await self.db.commit()
        await self.db.refresh(command)
        return command

    async def record_preflight_reconciliation_required(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        provider: str,
        capability: str,
        data: OSSNativeCommandCreate,
        marker_path: str,
        operation: OSSNativeCommandOperation,
    ) -> OSSNativeCommandRead:
        """Persist a validation-time external-effect report before returning an error."""

        provider_key = _normalize_provider(provider)
        capability_key = _normalize_capability(capability)
        operation_key = OSSNativeCommandOperation(operation)
        payload = assert_secret_free_json(
            data.payload,
            field_name="command payload",
            max_bytes=256 * 1024,
        )
        payload_hash = _command_hash(data, operation_key)
        await self._bind(workspace_id)

        existing = await self._command(
            workspace_id,
            provider_key,
            capability_key,
            data.idempotency_key,
        )
        if existing is not None:
            if not _same_command_request(existing, data, payload_hash):
                raise OSSNativeCommandConflictError(
                    "Idempotency key already belongs to another command payload."
                )
            return self._command_read(existing, deduplicated=True)

        state = await self._state(workspace_id, provider_key, capability_key)
        current_version = state.config_version if state else 0
        if current_version != data.expected_version:
            raise OSSNativeVersionConflictError(current_version)
        execution_mode = state.execution_mode if state else OSSNativeExecutionMode.disabled
        now = datetime.now(UTC)
        command = OSSNativeCommand(
            workspace_id=workspace_id,
            state_id=state.id if state else None,
            provider=provider_key,
            capability=capability_key,
            command_type=data.command_type,
            idempotency_key=data.idempotency_key,
            payload_hash=payload_hash,
            command_payload=payload,
            status=OSSNativeCommandStatus.reconciliation_required,
            execution_mode=execution_mode,
            state_version=current_version,
            command_result={
                "adapterEffectReport": {
                    "markerPath": marker_path,
                    "state": "positive",
                }
            },
            error_code="external_effect_reported",
            error_message=(
                "The validation adapter reported an external effect. "
                "Human reconciliation is required."
            ),
            requested_by_user_id=actor_user_id,
            dispatcher_invoked=False,
            external_network_allowed=False,
            completed_at=now,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(command)
                await self.db.flush()
        except IntegrityError:
            existing = await self._command(
                workspace_id,
                provider_key,
                capability_key,
                data.idempotency_key,
            )
            if existing is None:
                raise
            if not _same_command_request(existing, data, payload_hash):
                raise OSSNativeCommandConflictError(
                    "Idempotency key already belongs to another command payload."
                ) from None
            return self._command_read(existing, deduplicated=True)

        await self._audit(
            workspace_id=workspace_id,
            provider=provider_key,
            capability=capability_key,
            action=OSSNativeAuditAction.command_received,
            actor_user_id=actor_user_id,
            state=state,
            command=command,
            payload_hash=payload_hash,
            state_version=current_version,
            metadata={
                "commandType": data.command_type,
                "executionMode": execution_mode.value,
                "operation": operation_key.value,
                "status": OSSNativeCommandStatus.received.value,
                "validationPreflight": True,
            },
        )
        await self._audit(
            workspace_id=workspace_id,
            provider=provider_key,
            capability=capability_key,
            action=OSSNativeAuditAction.command_reconciliation_required,
            actor_user_id=actor_user_id,
            state=state,
            command=command,
            payload_hash=payload_hash,
            state_version=current_version,
            metadata={
                "automaticRetryAllowed": False,
                "effectState": "reported_external",
                "errorCode": "external_effect_reported",
                "reportedMarkerPath": marker_path,
                "requiresReconciliation": True,
                "status": OSSNativeCommandStatus.reconciliation_required.value,
                "validationPreflight": True,
            },
        )
        await self.db.commit()
        await self.db.refresh(command)
        return self._command_read(command, deduplicated=False)

    async def execute_command(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        provider: str,
        capability: str,
        data: OSSNativeCommandCreate,
        operation: OSSNativeCommandOperation = OSSNativeCommandOperation.execute,
    ) -> OSSNativeCommandRead:
        provider_key = _normalize_provider(provider)
        capability_key = _normalize_capability(capability)
        operation_key = OSSNativeCommandOperation(operation)
        payload = assert_secret_free_json(
            data.payload,
            field_name="command payload",
            max_bytes=256 * 1024,
        )
        payload_hash = _command_hash(data, operation_key)
        execution_policy = self._execution_policy(provider_key, capability_key)
        await self._bind(workspace_id)

        existing = await self._command(
            workspace_id,
            provider_key,
            capability_key,
            data.idempotency_key,
        )
        if existing is not None:
            if not _same_command_request(existing, data, payload_hash):
                raise OSSNativeCommandConflictError(
                    "Idempotency key already belongs to another command payload."
                )
            existing = await self._reconcile_abandoned_received_command(
                command=existing,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                provider=provider_key,
                capability=capability_key,
            )
            existing = await self._reconcile_expired_running_command(
                command=existing,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                provider=provider_key,
                capability=capability_key,
            )
            return self._command_read(existing, deduplicated=True)

        state = await self._state(workspace_id, provider_key, capability_key)
        current_version = state.config_version if state else 0
        if current_version != data.expected_version:
            raise OSSNativeVersionConflictError(current_version)
        execution_mode = state.execution_mode if state else OSSNativeExecutionMode.disabled
        command = OSSNativeCommand(
            workspace_id=workspace_id,
            state_id=state.id if state else None,
            provider=provider_key,
            capability=capability_key,
            command_type=data.command_type,
            idempotency_key=data.idempotency_key,
            payload_hash=payload_hash,
            command_payload=payload,
            status=OSSNativeCommandStatus.received,
            execution_mode=execution_mode,
            state_version=current_version,
            command_result={},
            requested_by_user_id=actor_user_id,
            dispatcher_invoked=False,
            external_network_allowed=False,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(command)
                await self.db.flush()
        except IntegrityError:
            existing = await self._command(
                workspace_id,
                provider_key,
                capability_key,
                data.idempotency_key,
            )
            if existing is None:
                raise
            if not _same_command_request(existing, data, payload_hash):
                raise OSSNativeCommandConflictError(
                    "Idempotency key already belongs to another command payload."
                ) from None
            existing = await self._reconcile_abandoned_received_command(
                command=existing,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                provider=provider_key,
                capability=capability_key,
            )
            existing = await self._reconcile_expired_running_command(
                command=existing,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                provider=provider_key,
                capability=capability_key,
            )
            return self._command_read(existing, deduplicated=True)

        await self._audit(
            workspace_id=workspace_id,
            provider=provider_key,
            capability=capability_key,
            action=OSSNativeAuditAction.command_received,
            actor_user_id=actor_user_id,
            state=state,
            command=command,
            payload_hash=payload_hash,
            state_version=current_version,
            metadata={
                "commandType": data.command_type,
                "executionMode": execution_mode.value,
                "operation": operation_key.value,
                "status": OSSNativeCommandStatus.received.value,
            },
        )
        local_control_operation = operation_key in {
            OSSNativeCommandOperation.dry_run,
            OSSNativeCommandOperation.configure,
        }
        server_gate_allows_dispatch = execution_policy.catalogued and (
            execution_policy.execute_allowed or local_control_operation
        )
        if not server_gate_allows_dispatch:
            error_code = (
                "capability_not_catalogued"
                if not execution_policy.catalogued
                else "server_execution_gate"
            )
            command.status = OSSNativeCommandStatus.blocked
            command.command_result = {
                "persistedLocally": True,
                "dispatcherInvoked": False,
                "externalNetworkExecuted": False,
                "serverGate": execution_policy.external_execution_state,
            }
            command.error_code = error_code
            command.error_message = "The immutable server capability catalog blocked execution."
            command.completed_at = datetime.now(UTC)
            command.row_version += 1
            await self._audit(
                workspace_id=workspace_id,
                provider=provider_key,
                capability=capability_key,
                action=OSSNativeAuditAction.command_blocked,
                actor_user_id=actor_user_id,
                state=state,
                command=command,
                payload_hash=payload_hash,
                state_version=current_version,
                metadata={
                    "errorCode": error_code,
                    "externalExecutionState": execution_policy.external_execution_state,
                    "nativeMode": execution_policy.native_mode,
                    "operation": operation_key.value,
                    "status": OSSNativeCommandStatus.blocked.value,
                },
            )
            await self.db.commit()
            await self.db.refresh(command)
            return self._command_read(command, deduplicated=False)

        if (
            operation_key == OSSNativeCommandOperation.execute
            and execution_mode != OSSNativeExecutionMode.local_native
        ):
            error_code = (
                "capability_disabled"
                if execution_mode == OSSNativeExecutionMode.disabled
                else "external_execution_blocked"
            )
            command.status = OSSNativeCommandStatus.blocked
            command.command_result = {
                "persistedLocally": True,
                "dispatcherInvoked": False,
                "externalNetworkExecuted": False,
            }
            command.error_code = error_code
            command.error_message = "Command persisted locally without external execution."
            command.completed_at = datetime.now(UTC)
            command.row_version += 1
            await self._audit(
                workspace_id=workspace_id,
                provider=provider_key,
                capability=capability_key,
                action=OSSNativeAuditAction.command_blocked,
                actor_user_id=actor_user_id,
                state=state,
                command=command,
                payload_hash=payload_hash,
                state_version=current_version,
                metadata={
                    "errorCode": error_code,
                    "executionMode": execution_mode.value,
                    "operation": operation_key.value,
                    "status": OSSNativeCommandStatus.blocked.value,
                },
            )
            await self.db.commit()
            await self.db.refresh(command)
            return self._command_read(command, deduplicated=False)

        try:
            dispatcher = self.dispatchers.resolve(provider_key)
        except Exception:
            dispatcher = None
            dispatcher_error_code = "dispatcher_load_failed"
        else:
            dispatcher_error_code = "dispatcher_unavailable"
        if dispatcher is None:
            command.status = OSSNativeCommandStatus.blocked
            command.command_result = {
                "persistedLocally": True,
                "dispatcherInvoked": False,
                "externalNetworkExecuted": False,
            }
            command.error_code = dispatcher_error_code
            command.error_message = "No local native dispatcher is available."
            command.completed_at = datetime.now(UTC)
            command.row_version += 1
            await self._audit(
                workspace_id=workspace_id,
                provider=provider_key,
                capability=capability_key,
                action=OSSNativeAuditAction.command_blocked,
                actor_user_id=actor_user_id,
                state=state,
                command=command,
                payload_hash=payload_hash,
                state_version=current_version,
                metadata={
                    "errorCode": dispatcher_error_code,
                    "executionMode": execution_mode.value,
                    "operation": operation_key.value,
                    "status": OSSNativeCommandStatus.blocked.value,
                },
            )
            await self.db.commit()
            await self.db.refresh(command)
            return self._command_read(command, deduplicated=False)

        command.status = OSSNativeCommandStatus.running
        command.dispatcher_invoked = True
        command.started_at = datetime.now(UTC)
        command.row_version += 1
        await self._audit(
            workspace_id=workspace_id,
            provider=provider_key,
            capability=capability_key,
            action=OSSNativeAuditAction.command_started,
            actor_user_id=actor_user_id,
            state=state,
            command=command,
            payload_hash=payload_hash,
            state_version=current_version,
            metadata={
                "commandType": data.command_type,
                "leaseSeconds": int(self.running_lease.total_seconds()),
                "operation": operation_key.value,
                "status": OSSNativeCommandStatus.running.value,
            },
        )
        await self.db.commit()

        context = OSSNativeDispatchContext(
            db=self.db,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            command_id=command.id,
            provider=provider_key,
            capability=capability_key,
            command_type=data.command_type,
            operation=operation_key,
            payload=dict(payload),
            configuration=dict(state.configuration if state else {}),
            config_version=current_version,
            external_network_allowed=False,
        )
        try:
            dispatched = dispatcher(context)
            raw_result = await dispatched if inspect.isawaitable(dispatched) else dispatched
            if not isinstance(raw_result, dict):
                raise TypeError("Native OSS dispatchers must return a JSON object.")
            _reject_positive_external_effect_markers(raw_result)
            safe_result = assert_secret_free_json(
                raw_result,
                field_name="command result",
                max_bytes=256 * 1024,
            )
        except Exception as error:
            await self.db.rollback()
            await self._bind(workspace_id)
            command = cast(
                OSSNativeCommand,
                await self.db.scalar(
                    select(OSSNativeCommand).where(
                        OSSNativeCommand.id == command.id,
                        OSSNativeCommand.workspace_id == workspace_id,
                    )
                ),
            )
            external_effect_reported = isinstance(
                error, OSSNativeExternalEffectReportedError
            )
            command.status = (
                OSSNativeCommandStatus.reconciliation_required
                if external_effect_reported
                else OSSNativeCommandStatus.failed
            )
            command.command_result = (
                {
                    "adapterEffectReport": {
                        "markerPath": error.marker_path,
                        "state": "positive",
                    }
                }
                if external_effect_reported
                else {}
            )
            command.error_code = (
                "external_effect_reported" if external_effect_reported else "effect_unknown"
            )
            command.error_message = (
                "The local adapter reported an external effect. Human reconciliation is required."
                if external_effect_reported
                else (
                    "Local native execution did not produce a confirmable result. "
                    "The effect may have occurred and requires reconciliation."
                )
            )
            command.completed_at = datetime.now(UTC)
            command.row_version += 1
            await self._audit(
                workspace_id=workspace_id,
                provider=provider_key,
                capability=capability_key,
                action=(
                    OSSNativeAuditAction.command_reconciliation_required
                    if external_effect_reported
                    else OSSNativeAuditAction.command_failed
                ),
                actor_user_id=actor_user_id,
                state=state,
                command=command,
                payload_hash=payload_hash,
                state_version=current_version,
                metadata={
                    "automaticRetryAllowed": False,
                    "effectState": (
                        "reported_external" if external_effect_reported else "unknown"
                    ),
                    "errorCode": command.error_code,
                    "reportedMarkerPath": (
                        error.marker_path if external_effect_reported else None
                    ),
                    "requiresReconciliation": True,
                    "status": command.status.value,
                },
            )
            await self.db.commit()
            await self.db.refresh(command)
            return self._command_read(command, deduplicated=False)

        command.status = OSSNativeCommandStatus.succeeded
        command.command_result = safe_result
        command.error_code = None
        command.error_message = None
        command.completed_at = datetime.now(UTC)
        command.row_version += 1
        await self._audit(
            workspace_id=workspace_id,
            provider=provider_key,
            capability=capability_key,
            action=OSSNativeAuditAction.command_succeeded,
            actor_user_id=actor_user_id,
            state=state,
            command=command,
            payload_hash=payload_hash,
            state_version=current_version,
            metadata={
                "resultFieldCount": len(safe_result),
                "status": OSSNativeCommandStatus.succeeded.value,
            },
        )
        await self.db.commit()
        await self.db.refresh(command)
        return self._command_read(command, deduplicated=False)
