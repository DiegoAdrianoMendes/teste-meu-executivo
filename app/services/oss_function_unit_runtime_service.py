"""Exact, tenant-scoped local runtime for every licensed OSS FunctionUnit.

The upstream census describes observable boundaries, not Python callables that can be
invoked safely inside KLM. This module turns every exact unit ID into a reviewable KLM
contract and executes the adapted local effect without consulting a sidecar or network.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from jsonschema import Draft202012Validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.advisory import acquire_transaction_advisory_lock
from app.db.post_commit import queue_native_outbox_wakeup
from app.db.tenant import set_workspace_context
from app.models.agent_run import AgentRun
from app.models.client import Client
from app.models.finance import FinanceEntry
from app.models.flow import Flow, FlowRun
from app.models.klm_operations import (
    KlmAuditEvent,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
    OmnichannelThread,
)
from app.models.matter import Matter
from app.models.oss_function_unit import OSSFunctionUnitEvent, OSSFunctionUnitEventStatus
from app.models.oss_native import (
    OSSNativeExecutionMode,
    OSSNativeOutboxEvent,
    OSSNativeOutboxStatus,
    assert_secret_free_json,
)
from app.models.task import Task
from app.schemas.oss_native import OSSNativeConfigPatch
from app.services.audit_actor_service import resolve_human_actor_label
from app.services.native_function_unit_binding_service import (
    NativeFunctionUnitBinding,
    build_native_function_unit_binding,
)
from app.services.native_function_unit_classification import (
    RuntimeMode,
    classify_native_function_unit,
)
from app.services.oss_function_unit_service import (
    OSSFunctionUnitNotFoundError,
    function_unit_catalog,
    get_function_unit,
)
from app.services.oss_native_local_adapter import OSSNativeLocalPrimaryAdapter
from app.services.oss_native_state_service import OSSNativeStateService

_RUNTIME_SERVICE = (
    "app.services.oss_function_unit_runtime_service.OSSFunctionUnitRuntimeService"
)


class OSSFunctionUnitRuntimeError(RuntimeError):
    """Base error for exact FunctionUnit runtime failures."""


class OSSFunctionUnitContractError(OSSFunctionUnitRuntimeError):
    """Raised when the committed mapping and computed contract diverge."""


class OSSFunctionUnitIdempotencyConflictError(OSSFunctionUnitRuntimeError):
    """Raised when an idempotency key is reused for a different request."""


class OSSFunctionUnitExternalGateError(OSSFunctionUnitRuntimeError):
    """Raised when local-only execution would require an external provider."""


class OSSFunctionUnitAuthorizationError(PermissionError):
    """Raised when the actor lacks the exact domain or authority permission."""


class OSSFunctionUnitMaturityError(OSSFunctionUnitRuntimeError):
    """Raised when a shadow mapping is asked to mutate canonical state."""


class OSSFunctionUnitHumanApprovalError(PermissionError):
    """Raised when a destructive unit lacks an approved human decision."""


@dataclass(frozen=True)
class OSSFunctionUnitContract:
    unit_id: str
    contract_id: str
    provider: str
    capability_id: str
    semantic_profile: str
    runtime_mode: RuntimeMode
    local_effect: str
    handler: str
    proof_profile: str
    mutating: bool
    external_gate: bool
    source_method: str
    source_operation: str
    source_route: str
    source_handler: str
    canonical_entity: str
    spa_route: str
    binding_id: str
    action_id: str
    capability_action_id: str | None
    native_operation_id: str
    equivalence_group: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    error_contract: tuple[str, ...]
    authorization_policy: dict[str, Any]
    state_transition: dict[str, Any]
    effects: dict[str, Any]
    fixture_ref: str
    maturity: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "unitId": self.unit_id,
            "contractId": self.contract_id,
            "provider": self.provider,
            "capabilityId": self.capability_id,
            "semanticProfile": self.semantic_profile,
            "runtimeMode": self.runtime_mode,
            "localEffect": self.local_effect,
            "handler": self.handler,
            "proofProfile": self.proof_profile,
            "maturity": self.maturity,
            "mutating": self.mutating,
            "externalGate": self.external_gate,
            "source": {
                "method": self.source_method,
                "operation": self.source_operation,
                "route": self.source_route,
                "handler": self.source_handler,
            },
            "destination": {
                "canonicalEntity": self.canonical_entity,
                "spaRoute": self.spa_route,
                "apiRoute": f"/api/v1/capabilities/functions/{self.unit_id}/runtime",
                "actionRoute": f"/api/v1/capabilities/functions/{self.unit_id}/actions",
                "applicationService": _RUNTIME_SERVICE,
            },
            "binding": {
                "bindingId": self.binding_id,
                "actionId": self.action_id,
                "capabilityActionId": self.capability_action_id,
                "nativeOperationId": self.native_operation_id,
                "equivalenceGroup": self.equivalence_group,
                "inputSchema": self.input_schema,
                "outputSchema": self.output_schema,
                "errorContract": list(self.error_contract),
                "authorizationPolicy": self.authorization_policy,
                "stateTransition": self.state_transition,
                "effects": self.effects,
                "fixtureRef": self.fixture_ref,
            },
            "assertions": _assertions_for(self),
        }


def semantic_profile_for(unit: dict[str, Any]) -> tuple[str, RuntimeMode, str, bool]:
    """Reduce one static upstream boundary to an explicit adapted KLM effect."""
    classification = classify_native_function_unit(unit)
    return (
        classification.semantic_profile,
        classification.runtime_mode,
        classification.local_effect,
        classification.mutating,
    )


def _contract_payload(
    unit: dict[str, Any],
    capability_id: str,
    profile: str,
    mode: RuntimeMode,
    effect: str,
    binding: NativeFunctionUnitBinding,
) -> dict[str, Any]:
    source = unit["source"]
    return {
        "schemaVersion": 1,
        "unitId": unit["id"],
        "provider": unit["upstream"],
        "commit": unit["commit"],
        "category": unit["category"],
        "contractKey": unit["contractKey"],
        "capabilityId": capability_id,
        "semanticProfile": profile,
        "runtimeMode": mode,
        "localEffect": effect,
        "bindingId": binding.binding_id,
        "nativeOperationId": binding.native_operation_id,
        "equivalenceGroup": binding.equivalence_group,
        "source": {
            "path": source["path"],
            "line": source["line"],
            "method": source.get("method"),
            "operation": source["operation"],
            "route": source["route"],
            "handler": source["handler"],
        },
    }


def build_function_unit_contract(
    unit: dict[str, Any],
    *,
    capability_id: str | None = None,
    canonical_entity: str | None = None,
    spa_route: str | None = None,
) -> OSSFunctionUnitContract:
    if unit["license"]["status"] != "verified-file":
        raise OSSFunctionUnitContractError(
            f"FunctionUnit {unit['id']} não possui licença verificada para execução."
        )
    mapping = unit.get("klmMapping") or {}
    resolved_capability = str(capability_id or mapping.get("capabilityId") or "").strip()
    if not resolved_capability or "." not in resolved_capability:
        raise OSSFunctionUnitContractError(
            f"FunctionUnit {unit['id']} não possui capacidade KLM exata."
        )
    profile, mode, effect, mutating = semantic_profile_for(unit)
    binding = build_native_function_unit_binding(
        unit,
        capability_id=resolved_capability,
    )
    payload = _contract_payload(
        unit,
        resolved_capability,
        profile,
        mode,
        effect,
        binding,
    )
    contract_id = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    proof_profile = (
        "independent_source_oracle"
        if binding.maturity == "native_verified"
        else "typed_native_primary_contract"
        if binding.maturity == "native_primary"
        else "typed_native_shadow_contract"
    )
    source = unit["source"]
    return OSSFunctionUnitContract(
        unit_id=unit["id"],
        contract_id=contract_id,
        provider=unit["upstream"],
        capability_id=resolved_capability,
        semantic_profile=profile,
        runtime_mode=mode,
        local_effect=effect,
        handler=binding.handler,
        proof_profile=proof_profile,
        mutating=mutating,
        external_gate=mode == "configuration_gate",
        source_method=str(source.get("method") or ""),
        source_operation=str(source["operation"]),
        source_route=str(source["route"]),
        source_handler=str(source["handler"]),
        canonical_entity=str(canonical_entity or mapping.get("canonicalEntity") or ""),
        spa_route=str(spa_route or mapping.get("spaRoute") or ""),
        binding_id=binding.binding_id,
        action_id=binding.action_id,
        capability_action_id=binding.capability_action_id,
        native_operation_id=binding.native_operation_id,
        equivalence_group=binding.equivalence_group,
        input_schema=binding.input_schema,
        output_schema=binding.output_schema,
        error_contract=binding.error_contract,
        authorization_policy=binding.authorization_policy,
        state_transition=binding.state_transition,
        effects=binding.effects,
        fixture_ref=binding.fixture_ref,
        maturity=binding.maturity,
    )


def _assertions_for(contract: OSSFunctionUnitContract) -> list[str]:
    assertions = [
        "exact_unit_id_resolved",
        "workspace_scope_enforced",
        "sidecar_not_constructed",
        "external_network_executed_false",
        "secret_free_payload",
        "typed_binding_resolved",
        "journal_is_not_primary_state",
    ]
    if contract.runtime_mode == "event_sourced":
        assertions.extend(("idempotency_enforced", "canonical_state_round_trip"))
        if contract.effects.get("outbox"):
            assertions.append("transactional_outbox_enqueued")
    elif contract.runtime_mode == "configuration_gate":
        assertions.extend(
            (
                "idempotency_enforced",
                "configuration_persisted_locally",
                "external_effect_blocked",
            )
        )
    else:
        assertions.append("read_does_not_mutate")
    if contract.authorization_policy.get("humanVeto") is True:
        assertions.append("human_approval_single_use_intent_bound")
    return assertions


@lru_cache(maxsize=2)
def _contract_registry(catalog_digest: str) -> dict[str, OSSFunctionUnitContract]:
    del catalog_digest
    catalog = function_unit_catalog()
    return {
        unit["id"]: build_function_unit_contract(unit)
        for unit in catalog["units"]
        if unit["license"]["status"] == "verified-file"
    }


def function_unit_runtime_contracts() -> dict[str, OSSFunctionUnitContract]:
    catalog = function_unit_catalog()
    return _contract_registry(catalog["reproducibility"]["payloadSha256"])


def function_unit_runtime_summary() -> dict[str, Any]:
    contracts = function_unit_runtime_contracts()
    return {
        "contracts": len(contracts),
        "profiles": dict(
            sorted(Counter(row.semantic_profile for row in contracts.values()).items())
        ),
        "runtimeModes": dict(
            sorted(Counter(row.runtime_mode for row in contracts.values()).items())
        ),
        "maturity": dict(
            sorted(Counter(row.maturity for row in contracts.values()).items())
        ),
        "equivalenceGroups": dict(
            sorted(Counter(row.equivalence_group for row in contracts.values()).items())
        ),
        "contractIdsDigest": hashlib.sha256(
            json.dumps(
                sorted((unit_id, row.contract_id) for unit_id, row in contracts.items()),
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }


def _payload_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def function_unit_approval_type(
    contract: OSSFunctionUnitContract,
    *,
    resource_id: str,
    payload: dict[str, Any],
) -> str:
    """Bind one human decision to one exact destructive intent."""
    approval_payload = {
        key: value for key, value in payload.items() if key != "humanApprovalId"
    }
    intent_digest = _payload_digest(
        {
            "bindingId": contract.binding_id,
            "resourceId": resource_id,
            "payload": approval_payload,
        }
    )
    return (
        f"{contract.authorization_policy['approvalTypePrefix']}:"
        f"{intent_digest[:16]}"
    )


def _resource_id(
    workspace_id: UUID,
    contract: OSSFunctionUnitContract,
    payload: dict[str, Any],
    explicit: str | None,
) -> str:
    candidate = str(explicit or payload.get("id") or payload.get("resourceId") or "").strip()
    if candidate:
        return candidate[:180]
    return str(
        uuid5(
            NAMESPACE_URL,
            f"urn:klm:function-unit:{workspace_id}:{contract.unit_id}:{_payload_digest(payload)}",
        )
    )


def _record_scope(contract: OSSFunctionUnitContract) -> str:
    return f"function:{contract.binding_id[:32]}"


class OSSFunctionUnitRuntimeService:
    """Execute exact adapted contracts against KLM-owned storage only."""

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def contract(unit_id: str) -> OSSFunctionUnitContract:
        contract = function_unit_runtime_contracts().get(unit_id)
        if contract is None:
            try:
                unit = get_function_unit(unit_id)
            except OSSFunctionUnitNotFoundError:
                raise
            raise OSSFunctionUnitContractError(
                f"FunctionUnit {unit['id']} permanece bloqueada por licença."
            )
        mapping = get_function_unit(unit_id).get("klmMapping") or {}
        committed_contract_id = mapping.get("runtimeContractId")
        if committed_contract_id and committed_contract_id != contract.contract_id:
            raise OSSFunctionUnitContractError(
                f"Contrato runtime divergente para {unit_id}; regenere o censo."
            )
        committed_handler = mapping.get("runtimeHandler")
        if committed_handler and committed_handler != contract.handler:
            raise OSSFunctionUnitContractError(
                f"Handler runtime divergente para {unit_id}; regenere o censo."
            )
        return contract

    async def _domain_summary(self, workspace_id: UUID, provider: str) -> dict[str, int]:
        if provider == "chatwoot":
            threads = await self.db.scalar(
                select(func.count(OmnichannelThread.id)).where(
                    OmnichannelThread.workspace_id == workspace_id
                )
            )
            messages = await self.db.scalar(
                select(func.count(OmnichannelMessage.id)).where(
                    OmnichannelMessage.workspace_id == workspace_id
                )
            )
            return {"threads": int(threads or 0), "messages": int(messages or 0)}
        if provider == "rocketchat":
            rooms = await self.db.scalar(
                select(func.count(OmnichannelThread.id)).where(
                    OmnichannelThread.workspace_id == workspace_id,
                    OmnichannelThread.primary_channel == OmnichannelChannel.internal,
                )
            )
            messages = await self.db.scalar(
                select(func.count(OmnichannelMessage.id)).where(
                    OmnichannelMessage.workspace_id == workspace_id,
                    OmnichannelMessage.direction == MessageDirection.internal,
                )
            )
            return {"rooms": int(rooms or 0), "internalMessages": int(messages or 0)}
        if provider == "chattermate":
            flows = await self.db.scalar(
                select(func.count(Flow.id)).where(
                    Flow.workspace_id == workspace_id,
                    Flow.deleted_at.is_(None),
                )
            )
            flow_runs = await self.db.scalar(
                select(func.count(FlowRun.id))
                .join(Flow, Flow.id == FlowRun.flow_id)
                .where(Flow.workspace_id == workspace_id)
            )
            agent_runs = await self.db.scalar(
                select(func.count(AgentRun.id)).where(AgentRun.workspace_id == workspace_id)
            )
            return {
                "flows": int(flows or 0),
                "flowRuns": int(flow_runs or 0),
                "agentRuns": int(agent_runs or 0),
            }

        matters = await self.db.scalar(
            select(func.count(Matter.id)).where(
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
            )
        )
        clients = await self.db.scalar(
            select(func.count(Client.id)).where(
                Client.workspace_id == workspace_id,
                Client.deleted_at.is_(None),
            )
        )
        tasks = await self.db.scalar(
            select(func.count(Task.id))
            .join(Matter, Matter.id == Task.matter_id)
            .where(Matter.workspace_id == workspace_id, Matter.deleted_at.is_(None))
        )
        finance = await self.db.scalar(
            select(func.count(FinanceEntry.id)).where(
                FinanceEntry.workspace_id == workspace_id,
                FinanceEntry.deleted_at.is_(None),
            )
        )
        return {
            "matters": int(matters or 0),
            "clients": int(clients or 0),
            "tasks": int(tasks or 0),
            "financeEntries": int(finance or 0),
        }

    async def _events(
        self, workspace_id: UUID, unit_id: str
    ) -> list[OSSFunctionUnitEvent]:
        return list(
            await self.db.scalars(
                select(OSSFunctionUnitEvent)
                .where(
                    OSSFunctionUnitEvent.workspace_id == workspace_id,
                    OSSFunctionUnitEvent.unit_id == unit_id,
                )
                .order_by(
                    OSSFunctionUnitEvent.occurred_at.asc(),
                    OSSFunctionUnitEvent.id.asc(),
                )
                .limit(500)
            )
        )

    @staticmethod
    def _replay(events: list[OSSFunctionUnitEvent]) -> list[dict[str, Any]]:
        records: dict[str, dict[str, Any]] = {}
        for event in events:
            if event.status == OSSFunctionUnitEventStatus.simulated:
                continue
            previous = records.get(event.resource_id, {})
            state = "archived" if event.operation == "archive" else "configured" if (
                event.status == OSSFunctionUnitEventStatus.configured
            ) else "active"
            records[event.resource_id] = {
                **previous,
                "id": event.resource_id,
                "state": state,
                "data": {**dict(previous.get("data") or {}), **dict(event.payload or {})},
                "lastOperation": event.operation,
                "updatedAt": event.occurred_at.isoformat(),
                "eventId": str(event.id),
            }
        return list(records.values())

    async def snapshot(self, workspace_id: UUID, unit_id: str) -> dict[str, Any]:
        await set_workspace_context(self.db, workspace_id)
        contract = self.contract(unit_id)
        unit = get_function_unit(unit_id)
        events = await self._events(workspace_id, unit_id)
        capability_key = contract.capability_id.split(".", 1)[1]
        native_snapshot = await OSSNativeLocalPrimaryAdapter(
            self.db,
            contract.provider,
        ).snapshot(
            workspace_id,
            capability_key,
            {"scopeKey": _record_scope(contract)},
        )
        records = list(native_snapshot.get("records") or [])
        return {
            "schemaVersion": "1.0",
            "unit": {
                "id": unit_id,
                "name": unit["name"],
                "category": unit["category"],
                "upstream": unit["upstream"],
            },
            "contract": contract.as_dict(),
            "state": {
                "records": records[-100:],
                "auditEventCount": len(events),
                "lastEventAt": events[-1].occurred_at.isoformat() if events else None,
            },
            "nativeSource": {
                "systemOfRecord": "klm",
                "workspaceScoped": True,
                "counts": await self._domain_summary(workspace_id, contract.provider),
                "recordStore": "oss_native_records",
                "recordScope": _record_scope(contract),
                "auditLog": "oss_function_unit_events",
            },
            "runtime": {
                "sidecarRequired": False,
                "sidecarConstructed": False,
                "externalNetworkAllowed": False,
                "externalNetworkExecuted": False,
                "availableModes": ["dry-run"]
                if contract.maturity in {"mapped", "native_shadow"}
                else ["dry-run", "configure"]
                if contract.external_gate
                else ["dry-run", "execute"],
                "maturity": contract.maturity,
            },
        }

    async def _execute_native_operation(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        contract: OSSFunctionUnitContract,
        mode: Literal["dry-run", "execute", "configure"],
        payload: dict[str, Any],
        resource_id: str,
        permission_keys: frozenset[tuple[str, str]],
    ) -> dict[str, Any]:
        capability_key = contract.capability_id.split(".", 1)[1]
        adapter = OSSNativeLocalPrimaryAdapter(self.db, contract.provider)
        safe_payload = assert_secret_free_json(
            payload,
            field_name="FunctionUnit payload",
        )
        validation_errors = sorted(
            Draft202012Validator(contract.input_schema).iter_errors(safe_payload),
            key=lambda error: list(error.absolute_path),
        )
        if validation_errors:
            first = validation_errors[0]
            path = ".".join(str(part) for part in first.absolute_path) or "$"
            raise OSSFunctionUnitContractError(
                f"Payload inválido em {path}: {first.message}"
            )
        validation = {
            "status": "validated",
            "bindingId": contract.binding_id,
            "schema": "draft-2020-12",
            "externalNetworkExecuted": False,
        }
        if mode == "dry-run":
            response = {
                "status": "simulated",
                "operationId": contract.native_operation_id,
                "validation": validation,
                "externalNetworkExecuted": False,
            }
            if contract.authorization_policy.get("humanVeto") is True:
                response["approvalIntent"] = {
                    "required": True,
                    "singleUse": True,
                    "resourceId": resource_id,
                    "approvalType": function_unit_approval_type(
                        contract,
                        resource_id=resource_id,
                        payload=safe_payload,
                    ),
                }
            return response

        if mode == "configure":
            state_service = OSSNativeStateService(self.db)
            current = await state_service.get_snapshot(
                workspace_id,
                contract.provider,
                capability_key,
            )
            configured = await state_service.update_config(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                provider=contract.provider,
                capability=capability_key,
                data=OSSNativeConfigPatch(
                    expected_version=current.state.config_version,
                    configuration={
                        **dict(current.state.configuration),
                        f"functionUnit:{contract.unit_id}": safe_payload,
                    },
                    execution_mode=OSSNativeExecutionMode.local_native,
                ),
            )
            return {
                "status": "configured",
                "operationId": contract.native_operation_id,
                "configurationVersion": configured.state.config_version,
                "externalNetworkExecuted": False,
            }

        if contract.equivalence_group == "ui.navigate":
            return {
                "status": "succeeded",
                "operationId": contract.native_operation_id,
                "destination": contract.spa_route,
                "externalNetworkExecuted": False,
            }
        if contract.equivalence_group == "authorization.evaluate":
            from app.services.oss_native_document_service import (
                required_capability_read_permissions,
            )

            required = required_capability_read_permissions(
                contract.provider,
                capability_key,
            )
            allowed = required.issubset(permission_keys)
            return {
                "status": "succeeded",
                "operationId": contract.native_operation_id,
                "decision": {
                    "allowed": allowed,
                    "basis": "klm_capability_rbac_v1",
                    "required": [
                        {"module": module, "action": action}
                        for module, action in sorted(required)
                    ],
                    "sourcePermissionParityVerified": False,
                    "policy": contract.authorization_policy,
                },
                "externalNetworkExecuted": False,
            }
        if contract.equivalence_group == "realtime.subscribe":
            return {
                "status": "succeeded",
                "operationId": contract.native_operation_id,
                "subscription": {
                    "scope": _record_scope(contract),
                    "transport": "klm_realtime",
                    "externalNetworkRequired": False,
                },
                "externalNetworkExecuted": False,
            }
        if contract.equivalence_group in {
            "boundary.inspect",
            "entity.query",
            "mcp.query",
            "webhook.inspect",
            "realtime.query",
        }:
            snapshot = await adapter.snapshot(
                workspace_id,
                capability_key,
                {**safe_payload, "scopeKey": _record_scope(contract)},
            )
            return {
                "status": "succeeded",
                "operationId": contract.native_operation_id,
                "records": list(snapshot.get("records") or []),
                "externalNetworkExecuted": False,
            }
        if contract.equivalence_group == "data.export.generate":
            snapshot = await adapter.snapshot(
                workspace_id,
                capability_key,
                {**safe_payload, "scopeKey": _record_scope(contract)},
            )
            records = list(snapshot.get("records") or [])
            content = json.dumps(records, ensure_ascii=True, sort_keys=True)
            return {
                "status": "succeeded",
                "operationId": contract.native_operation_id,
                "artifact": {
                    "mediaType": "application/json",
                    "recordCount": len(records),
                    "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "content": content,
                },
                "externalNetworkExecuted": False,
            }

        operation_payload = dict(safe_payload)
        if contract.equivalence_group == "job.enqueue":
            operation_payload.setdefault("status", "queued")
            operation_payload.setdefault("jobType", contract.source_operation)
        elif contract.equivalence_group == "webhook.ingest":
            operation_payload.setdefault("status", "received")
            operation_payload.setdefault("receivedAt", datetime.now(UTC).isoformat())
        elif contract.equivalence_group == "realtime.publish":
            operation_payload.setdefault("status", "queued_for_local_publish")
        elif contract.equivalence_group == "mcp.invoke":
            operation_payload.setdefault("status", "queued_for_local_dispatch")
            operation_payload.setdefault("tool", contract.source_operation)
        elif contract.equivalence_group == "data.import.stage":
            operation_payload.setdefault("status", "staged")

        effect = (
            "archive"
            if contract.equivalence_group == "entity.archive"
            else "event"
            if contract.equivalence_group
            in {
                "boundary.invoke",
                "job.enqueue",
                "webhook.ingest",
                "realtime.publish",
                "realtime.subscribe",
                "mcp.invoke",
            }
            else "upsert"
        )
        result = await adapter.execute_operation(
            workspace_id,
            actor_user_id,
            capability_key,
            operation_id=contract.native_operation_id,
            effect=effect,
            payload=operation_payload,
            resource_id=resource_id,
            record_type=contract.equivalence_group,
            scope_key=_record_scope(contract),
        )
        response = {
            **result,
            "operationId": contract.native_operation_id,
            "externalNetworkExecuted": False,
        }
        if contract.equivalence_group == "job.enqueue":
            response["job"] = {
                "id": result["resourceId"],
                "status": "queued",
                "type": contract.source_operation,
            }
        elif contract.equivalence_group == "data.import.stage":
            records = operation_payload.get("records")
            response["artifact"] = {
                "id": result["resourceId"],
                "status": "staged",
                "recordCount": len(records) if isinstance(records, list) else 0,
            }
        return response

    async def execute(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        unit_id: str,
        mode: Literal["dry-run", "execute", "configure"],
        idempotency_key: str,
        payload: dict[str, Any],
        resource_id: str | None = None,
        permission_keys: frozenset[tuple[str, str]] = frozenset(),
    ) -> dict[str, Any]:
        await set_workspace_context(self.db, workspace_id)
        contract = self.contract(unit_id)
        payload = assert_secret_free_json(
            payload,
            field_name="FunctionUnit payload",
        )
        resolved_resource_id = _resource_id(
            workspace_id, contract, payload, resource_id
        )
        request = {
            "mode": mode,
            "payload": payload,
            "resourceId": resolved_resource_id,
            "contractId": contract.contract_id,
        }
        request_digest = _payload_digest(request)

        from app.services.oss_native_document_service import (
            required_capability_manage_permissions,
            required_capability_read_permissions,
        )

        capability_key = contract.capability_id.split(".", 1)[1]
        mutating_request = mode != "dry-run" and contract.runtime_mode != "read_only"
        required_permissions = (
            required_capability_manage_permissions(contract.provider, capability_key)
            if mutating_request
            else required_capability_read_permissions(contract.provider, capability_key)
        )
        authority = contract.authorization_policy.get("authority")
        if authority not in {"workspace_operator", "workspace_admin"}:
            raise OSSFunctionUnitAuthorizationError(
                "A função nativa declara uma autoridade desconhecida e foi bloqueada."
            )
        if authority == "workspace_admin":
            required_permissions = frozenset({*required_permissions, ("users", "manage")})
        missing_permissions = required_permissions - permission_keys
        if missing_permissions:
            missing = ", ".join(
                f"{module}.{action}" for module, action in sorted(missing_permissions)
            )
            raise OSSFunctionUnitAuthorizationError(
                f"Permissões insuficientes para a função nativa: {missing}."
            )
        if mode != "dry-run" and contract.maturity in {"mapped", "native_shadow"}:
            raise OSSFunctionUnitMaturityError(
                "A função permanece em shadow por ambiguidade semântica e aceita apenas simulação."
            )
        if mode != "dry-run" and contract.runtime_mode != "read_only":
            await acquire_transaction_advisory_lock(
                self.db,
                namespace="klm-function-unit-idempotency",
                value=f"{workspace_id}:{unit_id}:{idempotency_key}",
            )
        existing = await self.db.scalar(
            select(OSSFunctionUnitEvent).where(
                OSSFunctionUnitEvent.workspace_id == workspace_id,
                OSSFunctionUnitEvent.unit_id == unit_id,
                OSSFunctionUnitEvent.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.result.get("requestDigest") != request_digest:
                raise OSSFunctionUnitIdempotencyConflictError(
                    "A chave de idempotência já pertence a outra execução."
                )
            return {
                "status": existing.status.value,
                "idempotentReplay": True,
                "eventId": str(existing.id),
                "resourceId": existing.resource_id,
                "result": dict(existing.result),
                "externalNetworkExecuted": False,
            }

        if mode == "execute" and contract.external_gate:
            raise OSSFunctionUnitExternalGateError(
                "Esta função exige provedor externo. Configure ou simule localmente "
                "até a etapa de APIs."
            )
        if mode == "configure" and contract.runtime_mode != "configuration_gate":
            raise OSSFunctionUnitContractError(
                "Esta função não possui configuração externa; use execução ou simulação."
            )

        if mutating_request and contract.authorization_policy.get("humanVeto") is True:
            if ("aprovacoes", "approve") not in permission_keys:
                raise OSSFunctionUnitHumanApprovalError(
                    "A função destrutiva exige permissão de aprovação humana."
                )
            from app.models.approval import ApprovalQueueItem, ApprovalStatus
            from app.models.matter import Matter

            try:
                approval_id = UUID(str(payload.get("humanApprovalId") or ""))
            except ValueError as error:
                raise OSSFunctionUnitHumanApprovalError(
                    "Informe uma aprovação humana válida para executar esta função."
                ) from error
            await acquire_transaction_advisory_lock(
                self.db,
                namespace="klm-function-unit-human-approval",
                value=f"{workspace_id}:{approval_id}",
            )
            required_approval_type = function_unit_approval_type(
                contract,
                resource_id=resolved_resource_id,
                payload=payload,
            )
            approved = await self.db.scalar(
                select(ApprovalQueueItem)
                .join(Matter, Matter.id == ApprovalQueueItem.matter_id)
                .where(
                    ApprovalQueueItem.id == approval_id,
                    Matter.workspace_id == workspace_id,
                    ApprovalQueueItem.status == ApprovalStatus.approved,
                    ApprovalQueueItem.approval_type == required_approval_type,
                )
                .with_for_update()
            )
            if approved is None:
                raise OSSFunctionUnitHumanApprovalError(
                    "A aprovação humana não corresponde a este workspace e intento destrutivo."
                )
            consumed = await self.db.scalar(
                select(KlmAuditEvent.id).where(
                    KlmAuditEvent.workspace_id == workspace_id,
                    KlmAuditEvent.event_type
                    == "native_capability_human_approval_consumed",
                    KlmAuditEvent.entity_type == "approval_queue_item",
                    KlmAuditEvent.entity_id == approval_id,
                )
            )
            if consumed is not None:
                raise OSSFunctionUnitHumanApprovalError(
                    "A aprovação humana já foi consumida por outra execução."
                )
            actor_label = await resolve_human_actor_label(self.db, actor_user_id)
            self.db.add(
                KlmAuditEvent(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    actor_type="human",
                    actor_label=actor_label,
                    event_type="native_capability_human_approval_consumed",
                    entity_type="approval_queue_item",
                    entity_id=approval_id,
                    summary="Aprovação humana consumida por uma ação nativa destrutiva.",
                    after_data={
                        "approvalType": required_approval_type,
                        "bindingId": contract.binding_id,
                        "resourceId": resolved_resource_id,
                        "requestDigest": request_digest,
                    },
                    occurred_at=datetime.now(UTC),
                )
            )

        result = {
            "requestDigest": request_digest,
            "contractId": contract.contract_id,
            "handler": contract.handler,
            "semanticProfile": contract.semantic_profile,
            "localEffect": contract.local_effect,
            "assertions": _assertions_for(contract),
            "externalNetworkExecuted": False,
        }
        native_result = await self._execute_native_operation(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            contract=contract,
            mode=mode,
            payload=payload,
            resource_id=resolved_resource_id,
            permission_keys=permission_keys,
        )
        output_errors = sorted(
            Draft202012Validator(contract.output_schema).iter_errors(native_result),
            key=lambda error: list(error.absolute_path),
        )
        if output_errors:
            first = output_errors[0]
            path = ".".join(str(part) for part in first.absolute_path) or "$"
            raise OSSFunctionUnitContractError(
                f"Saída nativa inválida em {path}: {first.message}"
            )
        result["nativeOperation"] = native_result
        if mode == "execute" and bool(contract.effects.get("outbox")):
            outbox = OSSNativeOutboxEvent(
                id=uuid4(),
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                provider=contract.provider,
                capability=contract.capability_id,
                unit_id=unit_id,
                binding_id=contract.binding_id,
                topic=contract.equivalence_group,
                dedupe_key=idempotency_key,
                payload={
                    "operationId": contract.native_operation_id,
                    "resourceId": resolved_resource_id,
                    "data": payload,
                    "externalNetworkAllowed": False,
                },
                status=OSSNativeOutboxStatus.pending,
            )
            self.db.add(outbox)
            queue_native_outbox_wakeup(
                self.db,
                workspace_id=workspace_id,
            )
            result["outbox"] = {
                "id": str(outbox.id),
                "status": outbox.status.value,
                "topic": outbox.topic,
                "externalNetworkAllowed": False,
            }
        if mode == "dry-run":
            return {
                "status": "simulated",
                "idempotentReplay": False,
                "eventId": None,
                "resourceId": resolved_resource_id,
                "result": result,
                "externalNetworkExecuted": False,
            }
        if contract.runtime_mode == "read_only":
            actor_label = await resolve_human_actor_label(self.db, actor_user_id)
            self.db.add(
                KlmAuditEvent(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    actor_type="human",
                    actor_label=actor_label,
                    event_type="native_capability_function_read",
                    entity_type="native_capability_function",
                    entity_id=None,
                    summary=f"Função nativa {unit_id} consultada.",
                    after_data={
                        "unitId": unit_id,
                        "contractId": contract.contract_id,
                        "resourceId": resolved_resource_id,
                        "externalNetworkExecuted": False,
                    },
                    occurred_at=datetime.now(UTC),
                )
            )
            await self.db.flush()
            return {
                "status": "succeeded",
                "idempotentReplay": False,
                "eventId": None,
                "resourceId": resolved_resource_id,
                "result": result,
                "externalNetworkExecuted": False,
            }

        status = (
            OSSFunctionUnitEventStatus.configured
            if mode == "configure"
            else OSSFunctionUnitEventStatus.succeeded
        )
        event = OSSFunctionUnitEvent(
            workspace_id=workspace_id,
            unit_id=unit_id,
            contract_id=contract.contract_id,
            provider=contract.provider,
            capability=contract.capability_id,
            semantic_profile=contract.semantic_profile,
            operation="configure" if mode == "configure" else contract.local_effect,
            idempotency_key=idempotency_key,
            resource_id=resolved_resource_id,
            payload=payload,
            result=result,
            status=status,
            actor_user_id=actor_user_id,
            external_network_executed=False,
            occurred_at=datetime.now(UTC),
        )
        self.db.add(event)
        actor_label = await resolve_human_actor_label(self.db, actor_user_id)
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human",
                actor_label=actor_label,
                event_type="native_capability_function_executed",
                entity_type="native_capability_function",
                entity_id=None,
                summary=f"FunctionUnit {unit_id} executada localmente ({mode}).",
                after_data={
                    "unitId": unit_id,
                    "contractId": contract.contract_id,
                    "semanticProfile": contract.semantic_profile,
                    "mode": mode,
                    "resourceId": resolved_resource_id,
                    "externalNetworkExecuted": False,
                },
                occurred_at=datetime.now(UTC),
            )
        )
        await self.db.flush()
        return {
            "status": status.value,
            "idempotentReplay": False,
            "eventId": str(event.id),
            "resourceId": resolved_resource_id,
            "result": result,
            "externalNetworkExecuted": False,
        }


__all__ = [
    "OSSFunctionUnitAuthorizationError",
    "OSSFunctionUnitContract",
    "OSSFunctionUnitContractError",
    "OSSFunctionUnitExternalGateError",
    "OSSFunctionUnitHumanApprovalError",
    "OSSFunctionUnitIdempotencyConflictError",
    "OSSFunctionUnitMaturityError",
    "OSSFunctionUnitRuntimeService",
    "build_function_unit_contract",
    "function_unit_approval_type",
    "function_unit_runtime_contracts",
    "function_unit_runtime_summary",
    "semantic_profile_for",
]
