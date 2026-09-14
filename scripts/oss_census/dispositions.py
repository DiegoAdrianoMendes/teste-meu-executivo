"""Rule-based, explicit disposition policy for every discovered function unit."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.oss_census.model import DISPOSITION_STATES, Observation


class DispositionError(ValueError):
    """Raised when a unit has no unambiguous disposition."""


@dataclass(frozen=True)
class DispositionRule:
    rule_id: str
    state: str
    reason: str
    upstreams: frozenset[str]
    categories: frozenset[str]
    license_statuses: frozenset[str]
    priority: int = 0
    families: frozenset[str] = frozenset()
    path_prefixes: tuple[str, ...] = ()
    route_contains: tuple[str, ...] = ()
    handler_contains: tuple[str, ...] = ()
    name_contains: tuple[str, ...] = ()
    unit_ids: frozenset[str] = frozenset()
    mapping: dict[str, Any] = field(default_factory=dict)

    def matches(
        self,
        observation: Observation,
        license_status: str,
        unit_id: str | None = None,
    ) -> bool:
        route = observation.route.lower()
        handler = observation.handler.lower()
        name = observation.name.lower()
        return (
            (not self.upstreams or observation.upstream in self.upstreams)
            and (not self.categories or observation.category in self.categories)
            and (not self.license_statuses or license_status in self.license_statuses)
            and (not self.families or observation.family in self.families)
            and (
                not self.path_prefixes
                or any(observation.path.startswith(prefix) for prefix in self.path_prefixes)
            )
            and (
                not self.route_contains
                or any(fragment in route for fragment in self.route_contains)
            )
            and (
                not self.handler_contains
                or any(fragment in handler for fragment in self.handler_contains)
            )
            and (
                not self.name_contains
                or any(fragment in name for fragment in self.name_contains)
            )
            and (not self.unit_ids or unit_id in self.unit_ids)
        )


@dataclass(frozen=True)
class DispositionPolicy:
    schema_version: int
    policy: str
    rules: tuple[DispositionRule, ...]

    def resolve(
        self,
        observation: Observation,
        license_status: str,
        unit_id: str | None = None,
    ) -> dict[str, Any]:
        matches = [
            rule
            for rule in self.rules
            if rule.matches(observation, license_status, unit_id)
        ]
        if not matches:
            raise DispositionError(
                f"No disposition rule for {observation.upstream}:{observation.category}:"
                f"{observation.key} (license={license_status})"
            )
        highest_priority = max(rule.priority for rule in matches)
        winners = [rule for rule in matches if rule.priority == highest_priority]
        if len(winners) > 1:
            rule_ids = ", ".join(rule.rule_id for rule in winners)
            raise DispositionError(
                "Ambiguous disposition at priority "
                f"{highest_priority} for {observation.upstream}:{observation.key}: {rule_ids}"
            )
        rule = winners[0]
        return {
            "state": rule.state,
            "rule": rule.rule_id,
            "reason": rule.reason,
            "priority": rule.priority,
            "mapping": dict(rule.mapping),
        }


def _string_set(value: Any, field: str, rule_id: str) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise DispositionError(f"Rule {rule_id!r} field {field!r} must be a string array")
    return frozenset(value)


def _string_tuple(value: Any, field_name: str, rule_id: str) -> tuple[str, ...]:
    values = _string_set(value, field_name, rule_id)
    normalized = tuple(sorted(item.strip().lower() for item in values if item.strip()))
    if value is not None and not normalized:
        raise DispositionError(
            f"Rule {rule_id!r} field {field_name!r} cannot contain only empty strings"
        )
    return normalized


_MAPPING_FIELDS = frozenset(
    {
        "canonicalEntity",
        "spaRoute",
        "applicationService",
        "agentTool",
        "auditEvent",
        "localMode",
        "fixture",
        "klmTests",
        "visualProof",
        "sidecarOffProof",
        "capabilityId",
        "coverageKind",
        "apiRoute",
        "runtimeContractId",
        "runtimeHandler",
        "semanticProfile",
        "proofProfile",
        "verificationManifest",
        "bindingId",
        "nativeOperationId",
        "actionId",
        "capabilityActionId",
        "equivalenceGroup",
        "inputSchema",
        "outputSchema",
        "errorContract",
        "authorizationPolicy",
        "stateTransition",
        "effects",
        "maturity",
        "conformanceLedger",
    }
)


def _mapping(value: Any, rule_id: str, state: str) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise DispositionError(f"Rule {rule_id!r} mapping must be an object")
    unknown = sorted(set(value) - _MAPPING_FIELDS)
    if unknown:
        raise DispositionError(
            f"Rule {rule_id!r} mapping has unknown fields: {', '.join(unknown)}"
        )
    for key, item in value.items():
        if key in {"klmTests", "visualProof", "sidecarOffProof", "errorContract"}:
            if not isinstance(item, list) or not all(
                isinstance(entry, str) and entry.strip() for entry in item
            ):
                raise DispositionError(
                    f"Rule {rule_id!r} mapping field {key!r} must be a string array"
                )
        elif key in {
            "inputSchema",
            "outputSchema",
            "authorizationPolicy",
            "stateTransition",
            "effects",
        }:
            if not isinstance(item, dict):
                raise DispositionError(
                    f"Rule {rule_id!r} mapping field {key!r} must be an object"
                )
        elif item is not None and not isinstance(item, str):
            raise DispositionError(
                f"Rule {rule_id!r} mapping field {key!r} must be a string or null"
            )
    if state in {"mapped", "native_shadow", "native_primary", "native_verified"}:
        required = {"canonicalEntity", "spaRoute", "applicationService", "localMode"}
        missing = sorted(key for key in required if not value.get(key))
        if missing:
            raise DispositionError(
                f"Rule {rule_id!r} state {state!r} needs mapping fields: "
                + ", ".join(missing)
            )
    if state == "native_verified":
        exact_required = {
            "apiRoute",
            "runtimeContractId",
            "runtimeHandler",
            "semanticProfile",
            "proofProfile",
            "verificationManifest",
        }
        missing = sorted(key for key in exact_required if not value.get(key))
        if missing:
            raise DispositionError(
                f"Rule {rule_id!r} state native_verified needs exact runtime fields: "
                + ", ".join(missing)
                )
    if state in {"native_primary", "native_verified"}:
        binding_required = {
            "bindingId",
            "nativeOperationId",
            "actionId",
            "equivalenceGroup",
            "inputSchema",
            "outputSchema",
            "errorContract",
            "authorizationPolicy",
            "stateTransition",
            "effects",
            "conformanceLedger",
        }
        missing = sorted(key for key in binding_required if not value.get(key))
        if missing:
            raise DispositionError(
                f"Rule {rule_id!r} state {state!r} needs binding fields: "
                + ", ".join(missing)
            )
    return dict(value)


def load_disposition_policy(path: Path) -> DispositionPolicy:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DispositionError(f"Disposition policy does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DispositionError(f"Invalid disposition policy JSON at {path}: {exc}") from exc

    if not isinstance(document, dict) or document.get("schemaVersion") not in {1, 2}:
        raise DispositionError("Disposition policy schemaVersion must be 1 or 2")
    schema_version = int(document["schemaVersion"])
    policy_text = document.get("policy")
    raw_rules = document.get("rules")
    if not isinstance(policy_text, str) or not policy_text.strip():
        raise DispositionError("Disposition policy must describe its policy")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise DispositionError("Disposition policy must contain at least one rule")

    rules: list[DispositionRule] = []
    seen_ids: set[str] = set()
    for raw_rule in raw_rules:
        if not isinstance(raw_rule, dict):
            raise DispositionError("Each disposition rule must be an object")
        rule_id = raw_rule.get("id")
        state = raw_rule.get("state")
        reason = raw_rule.get("reason")
        match = raw_rule.get("match", {})
        priority = raw_rule.get("priority", 0)
        if not isinstance(rule_id, str) or not rule_id:
            raise DispositionError("Each disposition rule needs a non-empty id")
        if rule_id in seen_ids:
            raise DispositionError(f"Duplicate disposition rule id: {rule_id}")
        seen_ids.add(rule_id)
        if state not in DISPOSITION_STATES:
            raise DispositionError(f"Rule {rule_id!r} has invalid state {state!r}")
        if not isinstance(reason, str) or not reason.strip():
            raise DispositionError(f"Rule {rule_id!r} needs a reason")
        if not isinstance(match, dict):
            raise DispositionError(f"Rule {rule_id!r} match must be an object")
        if not isinstance(priority, int) or isinstance(priority, bool):
            raise DispositionError(f"Rule {rule_id!r} priority must be an integer")
        rules.append(
            DispositionRule(
                rule_id=rule_id,
                state=state,
                reason=reason,
                upstreams=_string_set(match.get("upstreams"), "upstreams", rule_id),
                categories=_string_set(match.get("categories"), "categories", rule_id),
                license_statuses=_string_set(
                    match.get("licenseStatuses"), "licenseStatuses", rule_id
                ),
                priority=priority,
                families=_string_set(match.get("families"), "families", rule_id),
                path_prefixes=_string_tuple(
                    match.get("pathPrefixes"), "pathPrefixes", rule_id
                ),
                route_contains=_string_tuple(
                    match.get("routeContains"), "routeContains", rule_id
                ),
                handler_contains=_string_tuple(
                    match.get("handlerContains"), "handlerContains", rule_id
                ),
                name_contains=_string_tuple(
                    match.get("nameContains"), "nameContains", rule_id
                ),
                unit_ids=_string_set(match.get("unitIds"), "unitIds", rule_id),
                mapping=_mapping(raw_rule.get("mapping"), rule_id, state),
            )
        )
    return DispositionPolicy(
        schema_version=schema_version,
        policy=policy_text,
        rules=tuple(rules),
    )
