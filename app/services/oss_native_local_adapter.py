"""Sidecar-independent primary adapter for the native KLM capability workspace.

The reviewed upstream adapters remain in the repository for import, comparison,
and rollback.  The KLM SPA, however, must keep working when every sidecar is
offline. This adapter exposes the same 135 capability and 511 action contracts,
stores canonical state in ``oss_native_records``, and leaves command rows as an
immutable audit/idempotency boundary rather than reconstructing business state from
the journal.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.advisory import acquire_transaction_advisory_lock
from app.db.tenant import set_workspace_context
from app.models.oss_bridge import REDACTED_SECRET, sanitize_oss_json
from app.models.oss_native import (
    OSSNativeCommand,
    OSSNativeCommandStatus,
    OSSNativeRecord,
    OSSNativeRecordState,
)
from app.services.native_aggregate_policy import (
    NativeAggregatePolicyError,
    NativeAggregateVersionConflictError,
    apply_native_aggregate_transition,
    canonical_native_data_digest,
    native_aggregate_contract,
)
from app.services.oss_native_state_service import OSSNativeStateService

_PROVIDERS = frozenset({"chatwoot", "rocketchat", "chattermate", "bottlecrm"})
_CONTROL_FIELDS = frozenset(
    {
        "actionMode",
        "externalNetworkAllowed",
        "expectedRecordVersion",
        "expectedVersion",
        "humanApprovalId",
        "idempotencyKey",
        "operationId",
        "recordId",
        "requestFingerprint",
        "requiredPermissions",
        "resourceId",
        "scopeKey",
    }
)
_READ_HEADS = frozenset(
    {
        "calculate",
        "check",
        "download",
        "dry",
        "export",
        "fetch",
        "find",
        "get",
        "health",
        "history",
        "inspect",
        "list",
        "preview",
        "probe",
        "query",
        "read",
        "refresh",
        "report",
        "search",
        "show",
        "statistics",
        "suggest",
        "test",
        "validate",
        "view",
    }
)
_DELETE_HEADS = frozenset(
    {
        "archive",
        "cancel",
        "clear",
        "close",
        "deactivate",
        "delete",
        "disable",
        "dissociate",
        "detach",
        "end",
        "kick",
        "remove",
        "revoke",
        "stop",
        "unlink",
        "unpublish",
        "unpin",
        "unstar",
    }
)
_UPSERT_HEADS = frozenset(
    {
        "activate",
        "add",
        "apply",
        "approve",
        "assign",
        "associate",
        "attach",
        "begin",
        "change",
        "clone",
        "commit",
        "complete",
        "configure",
        "connect",
        "create",
        "dispatch",
        "edit",
        "enable",
        "execute",
        "grant",
        "ingest",
        "invite",
        "link",
        "mark",
        "merge",
        "move",
        "open",
        "persist",
        "pin",
        "post",
        "publish",
        "react",
        "register",
        "reopen",
        "replace",
        "request",
        "restore",
        "save",
        "send",
        "set",
        "snooze",
        "start",
        "sync",
        "transfer",
        "update",
        "upload",
        "upsert",
        "verify",
    }
)


class OSSNativeLocalContractError(ValueError):
    """Raised when a provider, capability, or action is outside the pinned catalog."""


class OSSNativeLocalExternalGateError(PermissionError):
    """Raised when a direct call tries to cross an external-effect gate."""


class OSSNativeLocalValidationError(ValueError):
    """Raised when a native action payload violates its typed contract."""


class OSSNativeLocalRecordConflictError(RuntimeError):
    """Raised when optimistic record versioning detects a stale write."""


@dataclass(frozen=True)
class _CapabilityContract:
    label: str
    actions: tuple[dict[str, Any], ...]


def _command_head(command: str) -> str:
    return re.split(r"[._:-]+", command.casefold(), maxsplit=1)[0]


def _default_mutating(command: str) -> bool:
    return _command_head(command) not in _READ_HEADS


@lru_cache(maxsize=1)
def _contracts() -> dict[tuple[str, str], _CapabilityContract]:
    """Build the immutable local contract from the four pinned adapter catalogs."""

    from app.services.oss_native_bottlecrm_service import (
        _CAPABILITIES as BOTTLE_SPECS,
    )
    from app.services.oss_native_chattermate_service import (
        CAPABILITY_SPECS as CHATTER_SPECS,
    )
    from app.services.oss_native_chatwoot_service import (
        _CAPABILITIES as CHATWOOT_SPECS,
    )
    from app.services.oss_native_chatwoot_service import (
        ChatwootNativeService,
    )
    from app.services.oss_native_rocketchat_service import (
        _ACTION_SPECS as ROCKET_ACTION_SPECS,
    )
    from app.services.oss_native_rocketchat_service import (
        _CAPABILITY_ACTIONS as ROCKET_CAPABILITY_ACTIONS,
    )

    contracts: dict[tuple[str, str], _CapabilityContract] = {}
    for key, spec in CHATWOOT_SPECS.items():
        contracts[("chatwoot", key)] = _CapabilityContract(
            label=spec.label,
            actions=tuple(ChatwootNativeService._actions(spec)),
        )
    for key, commands in ROCKET_CAPABILITY_ACTIONS.items():
        actions = []
        for command in commands:
            action_spec = ROCKET_ACTION_SPECS[command]
            actions.append(
                {
                    "command": command,
                    "required": list(action_spec.required),
                    "mutates": action_spec.mutating,
                    "authority": (
                        "workspace_admin" if action_spec.admin else "workspace_operator"
                    ),
                    "externalDispatch": False,
                    "networkAllowed": False,
                }
            )
        contracts[("rocketchat", key)] = _CapabilityContract(
            label=key.replace("-", " ").title(),
            actions=tuple(actions),
        )
    for key, spec in CHATTER_SPECS.items():
        contracts[("chattermate", key)] = _CapabilityContract(
            label=spec.title,
            actions=tuple(
                {
                    "command": command,
                    "mutates": _default_mutating(command),
                    "externalDispatch": False,
                    "networkAllowed": False,
                }
                for command in spec.commands
            ),
        )
    for spec in BOTTLE_SPECS:
        contracts[("bottlecrm", spec.key)] = _CapabilityContract(
            label=spec.title,
            actions=tuple(
                {
                    "command": command,
                    "mutates": _default_mutating(command),
                    "destructive": _command_head(command) in _DELETE_HEADS,
                    "required": (
                        ["provider"]
                        if command == "test_integration"
                        else ["recordId", "intoId"]
                        if command.startswith("merge")
                        else ["recordId"]
                        if _command_head(command) in _DELETE_HEADS
                        else []
                    ),
                    "externalDispatch": False,
                    "networkAllowed": False,
                }
                for command in spec.actions
            ),
        )

    if len(contracts) != 135:
        raise RuntimeError(
            f"O contrato local OSS deveria conter 135 capacidades, não {len(contracts)}."
        )
    action_count = sum(len(contract.actions) for contract in contracts.values())
    if action_count != 511:
        raise RuntimeError(f"O contrato local OSS deveria conter 511 ações, não {action_count}.")
    return contracts


def local_contract_summary() -> dict[str, Any]:
    contracts = _contracts()
    by_provider = {
        provider: sum(1 for candidate, _key in contracts if candidate == provider)
        for provider in sorted(_PROVIDERS)
    }
    actions_by_provider = {
        provider: sum(
            len(contract.actions)
            for (candidate, _key), contract in contracts.items()
            if candidate == provider
        )
        for provider in sorted(_PROVIDERS)
    }
    return {
        "capabilities": len(contracts),
        "actions": sum(actions_by_provider.values()),
        "byProvider": by_provider,
        "actionsByProvider": actions_by_provider,
        "sidecarRequired": False,
    }


def local_capability_contracts() -> dict[str, dict[str, Any]]:
    """Return a reviewable, deterministic action contract for every capability."""

    return {
        f"{provider}.{key}": {
            "provider": provider,
            "capability": key,
            "label": contract.label,
            "actionIds": [str(action["command"]) for action in contract.actions],
            "actions": [
                {
                    **dict(action),
                    "effect": _effect(str(action["command"]), action),
                    "inputSchema": {
                        "type": "object",
                        "required": list(action.get("required") or []),
                        "additionalProperties": True,
                    },
                    "outputSchema": {
                        "type": "object",
                        "required": ["status", "resourceId", "recordVersion"],
                    },
                    "errors": [
                        "validation_error",
                        "permission_denied",
                        "version_conflict",
                        "record_not_found",
                        "external_effect_blocked",
                    ],
                }
                for action in contract.actions
            ],
            "actionCount": len(contract.actions),
            "sidecarRequired": False,
        }
        for (provider, key), contract in sorted(_contracts().items())
    }


def _without_redacted(value: Any) -> Any:
    sanitized = sanitize_oss_json(value)
    if isinstance(sanitized, dict):
        return {
            key: _without_redacted(child)
            for key, child in sanitized.items()
            if child != REDACTED_SECRET
        }
    if isinstance(sanitized, list):
        return [_without_redacted(child) for child in sanitized]
    return sanitized


def _public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return _without_redacted(
        {key: value for key, value in payload.items() if key not in _CONTROL_FIELDS}
    )


def _effect(command: str, action: dict[str, Any] | None = None) -> str:
    head = _command_head(command)
    if bool((action or {}).get("destructive")):
        return "archive"
    if head in _READ_HEADS:
        return "read"
    if head in _DELETE_HEADS:
        return "archive"
    if (action or {}).get("mutates") is False:
        return "read"
    if head in _UPSERT_HEADS:
        return "upsert"
    if (action or {}).get("mutates") is True:
        return "upsert"
    return "event"


def _resource_id(
    workspace_id: UUID,
    provider: str,
    capability_key: str,
    command: str,
    payload: dict[str, Any],
) -> str:
    candidates = (
        "recordId",
        "resourceId",
        "matterId",
        "threadId",
        "conversation_id",
        "contact_id",
        "flow_id",
        "agent_id",
        "messageId",
        "roomId",
        "userId",
    )
    explicit = next(
        (str(payload[name]).strip() for name in candidates if str(payload.get(name) or "").strip()),
        "",
    )
    if explicit:
        return explicit[:180]
    request_identity = str(
        payload.get("operationId") or payload.get("idempotencyKey") or ""
    ).strip()
    if request_identity:
        identity = f"request:{request_identity}"
    else:
        content_digest = hashlib.sha256(
            json.dumps(
                _public_payload(payload),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        identity = f"content:{command}:{content_digest}"
    return str(
        uuid5(
            NAMESPACE_URL,
            f"urn:klm:native:{workspace_id}:{provider}:{capability_key}:{identity}",
        )
    )


class OSSNativeLocalPrimaryAdapter:
    """Primary KLM implementation for all licensed upstream capability contracts."""

    def __init__(self, db: AsyncSession, provider: str):
        normalized = str(provider or "").strip().casefold()
        if normalized not in _PROVIDERS:
            raise OSSNativeLocalContractError(f"Provider nativo desconhecido: {provider!r}.")
        self.db = db
        self.provider = normalized

    def _contract(self, capability_key: str) -> _CapabilityContract:
        contract = _contracts().get((self.provider, capability_key))
        if contract is None:
            raise OSSNativeLocalContractError(
                f"Capacidade {self.provider}.{capability_key} não pertence ao censo pinado."
            )
        return contract

    async def _events(self, workspace_id: UUID, capability_key: str) -> list[OSSNativeCommand]:
        return list(
            (
                await self.db.scalars(
                    select(OSSNativeCommand)
                    .where(
                        OSSNativeCommand.workspace_id == workspace_id,
                        OSSNativeCommand.provider == self.provider,
                        OSSNativeCommand.capability == capability_key,
                        OSSNativeCommand.status == OSSNativeCommandStatus.succeeded,
                    )
                    .order_by(OSSNativeCommand.created_at.asc(), OSSNativeCommand.id.asc())
                    .limit(1000)
                )
            ).all()
        )

    async def _records(
        self,
        workspace_id: UUID,
        capability_key: str,
        scope_key: str | None = None,
    ) -> list[OSSNativeRecord]:
        conditions = [
            OSSNativeRecord.workspace_id == workspace_id,
            OSSNativeRecord.provider == self.provider,
            OSSNativeRecord.capability == capability_key,
        ]
        if scope_key:
            conditions.append(OSSNativeRecord.scope_key == scope_key)
        return list(
            (
                await self.db.scalars(
                    select(OSSNativeRecord)
                    .where(*conditions)
                    .order_by(OSSNativeRecord.updated_at.desc(), OSSNativeRecord.id.desc())
                    .limit(1000)
                )
            ).all()
        )

    @staticmethod
    def _replay(
        events: list[OSSNativeCommand],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        records: dict[str, dict[str, Any]] = {}
        history: list[dict[str, Any]] = []
        for event in events:
            payload = dict(event.command_payload or {})
            if payload.get("actionMode") != "execute":
                continue
            result = dict(event.command_result or {})
            command = event.command_type
            effect = str(result.get("effect") or _effect(command))
            resource_id = str(result.get("resourceId") or event.id)
            occurred_at = (event.completed_at or event.created_at).isoformat()
            public_payload = _public_payload(payload)
            history.append(
                {
                    "id": str(event.id),
                    "command": command,
                    "effect": effect,
                    "resourceId": resource_id,
                    "occurredAt": occurred_at,
                    "actorUserId": (
                        str(event.requested_by_user_id) if event.requested_by_user_id else None
                    ),
                }
            )
            if effect == "read":
                continue
            previous = records.get(resource_id, {})
            records[resource_id] = {
                **previous,
                "id": resource_id,
                "recordType": "nativeCapabilityRecord",
                "state": "archived" if effect == "archive" else "active",
                "lastCommand": command,
                "data": {**dict(previous.get("data") or {}), **public_payload},
                "updatedAt": occurred_at,
                "eventId": str(event.id),
            }
        return list(records.values()), history[-100:]

    async def snapshot(
        self,
        workspace_id: UUID | str,
        capability_key: str,
        query: dict[str, Any],
    ) -> dict[str, Any]:
        workspace_uuid = UUID(str(workspace_id))
        contract = self._contract(capability_key)
        safe_query = _without_redacted(dict(query or {}))
        events = await self._events(workspace_uuid, capability_key)
        _legacy_records, history = self._replay(events)
        scope_key = str(safe_query.get("scopeKey") or "").strip() or None
        stored_records = await self._records(workspace_uuid, capability_key, scope_key)
        records = [
            {
                "id": row.resource_id,
                "recordType": row.record_type,
                "lastCommand": row.last_operation,
                "state": row.state.value,
                "data": dict(row.data or {}),
                "dataSha256": canonical_native_data_digest(dict(row.data or {})),
                "version": row.row_version,
                "updatedAt": row.updated_at.isoformat(),
                "commandId": str(row.last_command_id) if row.last_command_id else None,
            }
            for row in stored_records
        ]
        search = str(safe_query.get("search") or "").strip().casefold()
        state_filter = str(safe_query.get("status") or "").strip().casefold()
        if search:
            records = [row for row in records if search in str(row).casefold()]
        if state_filter:
            records = [row for row in records if row["state"].casefold() == state_filter]
        state = await OSSNativeStateService(self.db).get_snapshot(
            workspace_uuid,
            self.provider,
            capability_key,
        )
        return {
            "provider": self.provider,
            "capabilityKey": capability_key,
            "status": "ready",
            "version": state.state.config_version,
            "records": records[:200],
            "sections": [
                {
                    "id": "local-records",
                    "title": contract.label,
                    "kind": "nativeCapabilityRecord",
                    "recordCount": len(records),
                }
            ],
            "view": {
                "summary": {
                    "nativeRecords": len(records),
                    "localEvents": len(history),
                    "availableActions": len(contract.actions),
                    "sidecarDependencies": 0,
                }
            },
            "nativeState": {
                "records": records[:200],
                "events": history,
                "recordStore": "oss_native_records",
                "auditLog": "oss_native_commands",
                "replaySafe": True,
                "tenantScoped": True,
            },
            "actions": [dict(action) for action in contract.actions],
            "health": {
                "status": "healthy",
                "sidecarRequired": False,
                "externalNetworkAllowed": False,
            },
            "upstream": {
                "role": "preserved_optional_fallback",
                "queried": False,
                "required": False,
                "networkExecuted": False,
            },
            "source": {
                "authority": "klm",
                "engine": "oss_native_local_primary",
                "recordStore": "oss_native_records",
                "auditLog": "oss_native_commands",
            },
            "query": safe_query,
        }

    async def validate(
        self,
        workspace_id: UUID | str,
        actor_user_id: UUID | str,
        capability_key: str,
        command: str,
        payload: dict[str, Any],
        *,
        mode: str,
    ) -> dict[str, Any]:
        workspace_uuid = UUID(str(workspace_id))
        actor_uuid = UUID(str(actor_user_id))
        contract = self._contract(capability_key)
        if mode not in {"execute", "dry-run", "configure"}:
            raise OSSNativeLocalContractError("Modo de execução local inválido.")
        action = next(
            (row for row in contract.actions if row.get("command") == command),
            None,
        )
        if action is None:
            raise OSSNativeLocalContractError(
                f"Ação {command!r} não pertence a {self.provider}.{capability_key}."
            )
        if mode == "execute" and bool(action.get("externalDispatch")):
            raise OSSNativeLocalExternalGateError(
                "A ação depende de efeito externo e só aceita configuração ou simulação local."
            )
        if not isinstance(payload, dict):
            raise TypeError("payload deve ser um objeto.")
        missing = []
        for required in action.get("required") or []:
            alternatives = [
                name.strip()
                for name in str(required).split("|")
                if name.strip()
            ]
            if not any(
                (value := payload.get(name)) is not None
                and (not isinstance(value, str) or bool(value.strip()))
                for name in alternatives
            ):
                missing.append(" ou ".join(alternatives))
        if missing:
            raise OSSNativeLocalValidationError(
                "Campos obrigatórios ausentes: " + ", ".join(sorted(map(str, missing)))
            )
        return {
            "provider": self.provider,
            "workspaceId": str(workspace_uuid),
            "actorUserId": str(actor_uuid),
            "capabilityKey": capability_key,
            "command": command,
            "mode": mode,
            "status": "validated",
            "engine": "oss_native_local_primary",
            "sidecarRequired": False,
            "externalNetworkExecuted": False,
        }

    async def execute(
        self,
        workspace_id: UUID | str,
        actor_user_id: UUID | str,
        capability_key: str,
        command: str,
        payload: dict[str, Any],
        *,
        command_id: UUID | None = None,
    ) -> dict[str, Any]:
        workspace_uuid = UUID(str(workspace_id))
        actor_uuid = UUID(str(actor_user_id))
        contract = self._contract(capability_key)
        action = next(
            (row for row in contract.actions if row.get("command") == command),
            None,
        )
        if action is None:
            raise OSSNativeLocalContractError(
                f"Ação {command!r} não pertence a {self.provider}.{capability_key}."
            )
        if bool(action.get("externalDispatch")):
            raise OSSNativeLocalExternalGateError(
                "A ação depende de efeito externo e não pode ser executada localmente."
            )
        await self.validate(
            workspace_uuid,
            actor_uuid,
            capability_key,
            command,
            payload,
            mode="execute",
        )
        return await self.execute_operation(
            workspace_uuid,
            actor_uuid,
            capability_key,
            operation_id=command,
            effect=_effect(command, action),
            payload=payload,
            record_type=str(action.get("recordType") or capability_key),
            command_id=command_id,
        )

    async def execute_operation(
        self,
        workspace_id: UUID | str,
        actor_user_id: UUID | str,
        capability_key: str,
        *,
        operation_id: str,
        effect: str,
        payload: dict[str, Any],
        resource_id: str | None = None,
        record_type: str | None = None,
        scope_key: str = "capability",
        command_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Apply a typed KLM operation without pretending it is a catalog action.

        FunctionUnit bindings use this path when the source boundary has no exact
        capability-action counterpart. This keeps the adaptation explicit while still
        sharing tenant isolation, optimistic concurrency, canonical state, and audit
        behavior with the 511 curated capability actions.
        """

        workspace_uuid = UUID(str(workspace_id))
        actor_uuid = UUID(str(actor_user_id))
        await set_workspace_context(self.db, workspace_uuid)
        self._contract(capability_key)
        if effect not in {"read", "upsert", "archive", "event"}:
            raise OSSNativeLocalContractError(f"Efeito nativo inválido: {effect!r}.")
        safe_payload = _public_payload(dict(payload or {}))
        normalized_scope = str(scope_key or "").strip()
        if not normalized_scope or len(normalized_scope) > 80:
            raise OSSNativeLocalValidationError("scopeKey nativo inválido.")
        resolved_resource_id = str(resource_id or "").strip()[:180] or _resource_id(
            workspace_uuid,
            self.provider,
            capability_key,
            operation_id,
            dict(payload or {}),
        )
        await acquire_transaction_advisory_lock(
            self.db,
            namespace="klm-native-record",
            value=(
                f"{workspace_uuid}:{self.provider}:{capability_key}:"
                f"{normalized_scope}:{resolved_resource_id}"
            ),
        )
        record = await self.db.scalar(
            select(OSSNativeRecord)
            .where(
                OSSNativeRecord.workspace_id == workspace_uuid,
                OSSNativeRecord.provider == self.provider,
                OSSNativeRecord.capability == capability_key,
                OSSNativeRecord.scope_key == normalized_scope,
                OSSNativeRecord.resource_id == resolved_resource_id,
            )
            .with_for_update()
        )
        expected_record_version = payload.get("expectedRecordVersion")
        parsed_version: int | None = None
        if expected_record_version is not None:
            try:
                parsed_version = int(expected_record_version)
            except (TypeError, ValueError) as error:
                raise OSSNativeLocalValidationError(
                    "expectedRecordVersion deve ser inteiro."
                ) from error
        aggregate_contract = native_aggregate_contract(
            self.provider,
            str(record_type or capability_key),
        )
        try:
            transition = apply_native_aggregate_transition(
                effect=effect,
                operation_id=operation_id,
                patch=safe_payload,
                current_state=record.state.value if record is not None else None,
                current_data=dict(record.data or {}) if record is not None else None,
                current_version=record.row_version if record is not None else 0,
                expected_version=parsed_version,
            )
        except NativeAggregateVersionConflictError as error:
            raise OSSNativeLocalRecordConflictError(str(error)) from error
        except NativeAggregatePolicyError as error:
            raise OSSNativeLocalValidationError(str(error)) from error

        if effect != "read":
            if record is None:
                record = OSSNativeRecord(
                    workspace_id=workspace_uuid,
                    provider=self.provider,
                    capability=capability_key,
                    scope_key=normalized_scope,
                    resource_id=resolved_resource_id,
                    record_type=aggregate_contract.aggregate_type,
                    last_operation=operation_id[:180],
                    state=OSSNativeRecordState(transition.state),
                    data=transition.data,
                    row_version=transition.version,
                    last_command_id=command_id,
                    created_by_user_id=actor_uuid,
                    updated_by_user_id=actor_uuid,
                )
                self.db.add(record)
            else:
                record.state = OSSNativeRecordState(transition.state)
                record.data = transition.data
                record.last_operation = operation_id[:180]
                record.row_version = transition.version
                record.last_command_id = command_id
                record.updated_by_user_id = actor_uuid
            await self.db.flush()

        record_version = record.row_version if record is not None else 0
        return {
            "status": "completed",
            "provider": self.provider,
            "capabilityKey": capability_key,
            "command": operation_id,
            "effect": effect,
            "resourceId": resolved_resource_id,
            "recordVersion": record_version,
            "record": {
                "state": record.state.value if record is not None else "unchanged",
                "data": dict(record.data) if record is not None else transition.data,
                "version": record_version,
                "dataSha256": transition.data_sha256,
                "updatedAt": datetime.now(UTC).isoformat(),
            },
            "aggregate": {
                "domain": aggregate_contract.domain,
                "type": aggregate_contract.aggregate_type,
                "schemaVersion": aggregate_contract.schema_version,
                "dataSha256": transition.data_sha256,
            },
            "actorUserId": str(actor_uuid),
            "engine": "oss_native_local_primary",
            "persistedVia": "oss_native_records" if effect != "read" else None,
            "auditedVia": (
                "oss_native_commands" if command_id is not None else "oss_function_unit_events"
            ),
            "replaySafe": True,
            "sidecarRequired": False,
            "externalNetworkExecuted": False,
        }


__all__ = [
    "OSSNativeLocalContractError",
    "OSSNativeLocalExternalGateError",
    "OSSNativeLocalRecordConflictError",
    "OSSNativeLocalValidationError",
    "OSSNativeLocalPrimaryAdapter",
    "local_capability_contracts",
    "local_contract_summary",
]
