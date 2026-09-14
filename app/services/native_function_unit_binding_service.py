"""Typed, per-unit bindings from reviewed source boundaries to KLM operations.

Mappings may share an implementation through an equivalence group, but every source
FunctionUnit receives its own immutable binding ID and fixture.  Local conformance can
promote a binding to ``native_primary``; only an independent source oracle may promote
it to ``native_verified``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from app.services.native_function_unit_classification import (
    classify_native_function_unit,
)
from app.services.oss_native_local_adapter import local_capability_contracts

ROOT = Path(__file__).resolve().parents[2]
CONFORMANCE_LEDGER = ROOT / "app" / "data" / "oss_function_unit_conformance.json"

BindingMaturity = Literal[
    "mapped",
    "native_shadow",
    "native_primary",
    "native_verified",
]

_SYNONYMS = {
    "add": "create",
    "begin": "start",
    "destroy": "delete",
    "edit": "update",
    "fetch": "read",
    "find": "search",
    "get": "read",
    "ingest": "create",
    "insert": "create",
    "list": "read",
    "patch": "update",
    "post": "create",
    "put": "update",
    "query": "search",
    "remove": "delete",
    "save": "update",
    "set": "update",
    "show": "read",
    "upsert": "update",
    "view": "read",
}


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _tokens(value: Any) -> set[str]:
    return {
        _SYNONYMS.get(token, token)
        for token in re.findall(r"[a-z0-9]+", str(value or "").casefold())
        if len(token) > 1
    }


def _source_tokens(unit: dict[str, Any]) -> set[str]:
    source = unit["source"]
    return _tokens(
        " ".join(
            str(value or "")
            for value in (
                unit.get("name"),
                source.get("operation"),
                source.get("route"),
                source.get("handler"),
                source.get("path"),
            )
        )
    )


def _action_effect(action: dict[str, Any]) -> str:
    return str(action.get("effect") or "event")


def _select_action(
    unit: dict[str, Any],
    capability_contract: dict[str, Any],
) -> tuple[dict[str, Any] | None, str, int]:
    source_tokens = _source_tokens(unit)
    desired = classify_native_function_unit(unit).local_effect
    actions = list(capability_contract.get("actions") or [])
    if not actions:
        raise ValueError(f"Capacidade {capability_contract['capability']} não possui ações.")

    def score(action: dict[str, Any]) -> tuple[int, int, str]:
        action_tokens = _tokens(action.get("command"))
        lexical = sum(len(token) for token in source_tokens & action_tokens)
        effect = _action_effect(action)
        effect_score = (
            40
            if effect == desired
            else 20
            if desired == "upsert" and effect == "event"
            else 0
        )
        exact = (
            100
            if str(unit["source"].get("operation") or "").casefold()
            == str(action.get("command") or "").casefold()
            else 0
        )
        return exact + effect_score + lexical, lexical, str(action.get("command") or "")

    ranked = sorted(
        ((score(action), action) for action in actions),
        key=lambda row: row[0],
        reverse=True,
    )
    (total, lexical, _command), action = ranked[0]
    strategy = (
        "exact_operation"
        if total >= 100
        else "lexical_action"
        if lexical
        else "effect_default"
    )
    if strategy == "effect_default":
        return None, "capability_unbound", total
    return action, strategy, total


def _input_schema(group: str, *, human_veto: bool) -> dict[str, Any]:
    required: list[str] = []
    properties: dict[str, Any] = {
        "resourceId": {"type": "string", "minLength": 1, "maxLength": 180},
        "expectedRecordVersion": {"type": "integer", "minimum": 0},
    }
    if group == "data.import.stage":
        properties["records"] = {"type": "array", "items": {"type": "object"}}
    if group == "authorization.evaluate":
        properties["permissionKeys"] = {"type": "array", "items": {"type": "string"}}
    if human_veto:
        properties["humanApprovalId"] = {"type": "string", "format": "uuid"}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": required,
        "properties": properties,
        "additionalProperties": True,
        "secretMaterialAllowed": False,
        "maxBytes": 262144,
    }


def _output_schema(group: str) -> dict[str, Any]:
    operation_required: str | None = None
    if group in {"entity.query", "mcp.query", "webhook.inspect", "realtime.query"}:
        operation_required = "records"
    elif group in {"entity.upsert", "entity.archive", "boundary.invoke"}:
        operation_required = "record"
    elif group == "ui.navigate":
        operation_required = "destination"
    elif group == "authorization.evaluate":
        operation_required = "decision"
    elif group == "job.enqueue":
        operation_required = "job"
    elif group == "realtime.subscribe":
        operation_required = "subscription"
    elif group.startswith("data."):
        operation_required = "artifact"
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["status", "operationId", "externalNetworkExecuted"],
        "properties": {
            "status": {"type": "string"},
            "operationId": {"type": "string"},
            "externalNetworkExecuted": {"const": False},
        },
        "additionalProperties": True,
    }
    if operation_required:
        schema["allOf"] = [
            {
                "if": {
                    "properties": {
                        "status": {"enum": ["succeeded", "completed"]},
                    },
                    "required": ["status"],
                },
                "then": {"required": [operation_required]},
            }
        ]
    return schema


@dataclass(frozen=True)
class NativeFunctionUnitBinding:
    unit_id: str
    binding_id: str
    capability_id: str
    action_id: str
    capability_action_id: str | None
    native_operation_id: str
    equivalence_group: str
    handler: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    error_contract: tuple[str, ...]
    authorization_policy: dict[str, Any]
    state_transition: dict[str, Any]
    effects: dict[str, Any]
    fixture_ref: str
    mapping_strategy: str
    mapping_score: int
    maturity: BindingMaturity
    proof: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "unitId": self.unit_id,
            "bindingId": self.binding_id,
            "capabilityId": self.capability_id,
            "actionId": self.action_id,
            "capabilityActionId": self.capability_action_id,
            "nativeOperationId": self.native_operation_id,
            "equivalenceGroup": self.equivalence_group,
            "handler": self.handler,
            "inputSchema": self.input_schema,
            "outputSchema": self.output_schema,
            "errorContract": list(self.error_contract),
            "authorizationPolicy": self.authorization_policy,
            "stateTransition": self.state_transition,
            "effects": self.effects,
            "fixtureRef": self.fixture_ref,
            "mappingStrategy": self.mapping_strategy,
            "mappingScore": self.mapping_score,
            "maturity": self.maturity,
            "proof": self.proof,
        }


@lru_cache(maxsize=1)
def _proofs() -> dict[str, dict[str, Any]]:
    if not CONFORMANCE_LEDGER.is_file():
        return {}
    document = json.loads(CONFORMANCE_LEDGER.read_text(encoding="utf-8"))
    if document.get("schemaVersion") != 1 or not isinstance(document.get("proofs"), list):
        raise ValueError("Ledger de conformidade FunctionUnit inválido.")
    return {
        str(row["unitId"]): row
        for row in document["proofs"]
        if isinstance(row, dict) and row.get("unitId")
    }


def build_native_function_unit_binding(
    unit: dict[str, Any],
    *,
    capability_id: str | None = None,
) -> NativeFunctionUnitBinding:
    mapping = unit.get("klmMapping") or {}
    resolved_capability = str(capability_id or mapping.get("capabilityId") or "").strip()
    capability_contract = local_capability_contracts().get(resolved_capability)
    if capability_contract is None:
        raise ValueError(
            f"Capacidade nativa desconhecida para {unit['id']}: {resolved_capability}."
        )
    capability_action, strategy, score = _select_action(unit, capability_contract)
    classification = classify_native_function_unit(unit)
    group = classification.equivalence_group
    human_veto = (
        bool((capability_action or {}).get("destructive"))
        or bool((capability_action or {}).get("requiresConfirmation"))
        or classification.local_effect == "archive"
    )
    authority = str(
        (capability_action or {}).get("authority") or "workspace_operator"
    )
    if authority not in {"workspace_operator", "workspace_admin"}:
        raise ValueError(
            f"Autoridade nativa desconhecida para {unit['id']}: {authority}."
        )
    action_id = group
    capability_action_id = (
        str(capability_action["command"]) if capability_action is not None else None
    )
    payload = {
        "schemaVersion": 1,
        "unitId": unit["id"],
        "sourceCommit": unit["commit"],
        "capabilityId": resolved_capability,
        "actionId": action_id,
        "capabilityActionId": capability_action_id,
        "equivalenceGroup": group,
        "inputSchema": _input_schema(group, human_veto=human_veto),
        "outputSchema": _output_schema(group),
        "authorization": {
            "workspaceScoped": True,
            "authority": authority,
            "humanVeto": human_veto,
        },
    }
    binding_id = _digest(payload)
    proof = _proofs().get(unit["id"])
    source_method = str(unit["source"].get("method") or "").upper()
    coverage_kind = str(mapping.get("coverageKind") or "")
    maturity: BindingMaturity = (
        "native_primary"
        if capability_action_id is not None
        and strategy in {"exact_operation", "lexical_action"}
        and coverage_kind != "category_default"
        and source_method not in {"ANY", "*"}
        else "native_shadow"
    )
    if proof is not None:
        if proof.get("bindingId") != binding_id:
            raise ValueError(f"Prova unitária stale para {unit['id']}.")
        if proof.get("status") == "passed" and proof.get("upstreamOracleDigest"):
            maturity = "native_verified"
        elif proof.get("status") == "shadow_passed":
            maturity = "native_shadow"
    return NativeFunctionUnitBinding(
        unit_id=unit["id"],
        binding_id=binding_id,
        capability_id=resolved_capability,
        action_id=action_id,
        capability_action_id=capability_action_id,
        native_operation_id=(
            f"{resolved_capability}:{group}:{str(unit['id']).rsplit(':', 1)[-1]}"
        ),
        equivalence_group=group,
        handler=f"native_function_unit.{group}.v1",
        input_schema=payload["inputSchema"],
        output_schema=payload["outputSchema"],
        error_contract=(
            "validation_error",
            "permission_denied",
            "version_conflict",
            "record_not_found",
            "idempotency_conflict",
            "external_effect_blocked",
        ),
        authorization_policy={
            **payload["authorization"],
            "approvalTypePrefix": f"function_unit:{binding_id}",
            "singleUse": human_veto,
            "intentBound": human_veto,
        },
        state_transition={
            "aggregate": resolved_capability,
            "effect": classification.local_effect,
            "journalIsPrimary": False,
            "recordStore": "oss_native_records",
        },
        effects={
            "localState": classification.mutating,
            "audit": True,
            "outbox": group in {"job.enqueue", "webhook.ingest", "realtime.publish", "mcp.invoke"},
            "externalNetworkAllowed": False,
        },
        fixture_ref=f"generated://oss-function-unit-fixtures#{unit['id']}",
        mapping_strategy=strategy,
        mapping_score=score,
        maturity=maturity,
        proof=proof,
    )


@lru_cache(maxsize=2)
def _registry(catalog_digest: str) -> dict[str, NativeFunctionUnitBinding]:
    del catalog_digest
    from app.services.oss_function_unit_service import function_unit_catalog

    catalog = function_unit_catalog()
    return {
        unit["id"]: build_native_function_unit_binding(unit)
        for unit in catalog["units"]
        if unit["license"]["status"] == "verified-file"
    }


def native_function_unit_bindings() -> dict[str, NativeFunctionUnitBinding]:
    from app.services.oss_function_unit_service import function_unit_catalog

    catalog = function_unit_catalog()
    return _registry(catalog["reproducibility"]["payloadSha256"])


def native_function_unit_binding_summary() -> dict[str, Any]:
    bindings = native_function_unit_bindings()
    maturity = {
        state: 0
        for state in ("mapped", "native_shadow", "native_primary", "native_verified")
    }
    strategies: dict[str, int] = {}
    groups: dict[str, int] = {}
    for binding in bindings.values():
        maturity[binding.maturity] += 1
        strategies[binding.mapping_strategy] = strategies.get(binding.mapping_strategy, 0) + 1
        groups[binding.equivalence_group] = groups.get(binding.equivalence_group, 0) + 1
    return {
        "bindings": len(bindings),
        "maturity": maturity,
        "mappingStrategies": dict(sorted(strategies.items())),
        "equivalenceGroups": dict(sorted(groups.items())),
        "bindingIdsDigest": _digest(
            sorted((unit_id, binding.binding_id) for unit_id, binding in bindings.items())
        ),
    }


__all__ = [
    "NativeFunctionUnitBinding",
    "build_native_function_unit_binding",
    "native_function_unit_binding_summary",
    "native_function_unit_bindings",
]
