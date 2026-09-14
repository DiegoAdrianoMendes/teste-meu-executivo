"""Census orchestration, validation, and deterministic lock rendering."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from scripts.oss_census.baseline import validate_family_baseline
from scripts.oss_census.common import sha256_bytes, sha256_text
from scripts.oss_census.dispositions import DispositionPolicy, load_disposition_policy
from scripts.oss_census.extractors import EXTRACTORS, SOURCE_PATTERNS, extract_upstream
from scripts.oss_census.git_source import CanonicalGitSource
from scripts.oss_census.licenses import build_license_scopes, resolve_license_scope
from scripts.oss_census.model import (
    CATEGORIES,
    DISPOSITION_STATES,
    REQUIRED_UPSTREAMS,
    ExtractionResult,
    Observation,
    ScopeResult,
    canonical_contract_key,
)
from scripts.oss_census.python_ast import parser_receipt

SCHEMA_VERSION = 2
GENERATOR_VERSION = 2
MINIMUM_FAMILY_BASELINE = 135


class CensusError(RuntimeError):
    """Raised when the census cannot prove its declared denominator."""


def _skipped_pin_receipt(
    source: dict[str, Any], patterns: tuple[str, ...]
) -> dict[str, Any]:
    """Describe an explicit non-canonical source check without scanning it."""
    return {
        "status": "skipped",
        "reason": "explicit_skip_pin_verification",
        "sourceMode": "filesystem_unverified",
        "canonical": False,
        "origin": None,
        "ref": source["ref"],
        "commit": source["commit"],
        "checkout": {"status": "not_verified"},
        "submodules": {"status": "not_verified"},
        "selectedBlobs": None,
        "selectedBytes": None,
        "selectedManifestSha256": None,
        "selectedContentManifestSha256": None,
        "patterns": list(patterns),
    }


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CensusError(f"Required JSON file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CensusError(f"Invalid JSON at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CensusError(f"Expected a JSON object at {path}")
    return value


def _validate_upstream_lock(document: dict[str, Any]) -> list[dict[str, Any]]:
    if document.get("schemaVersion") != 2:
        raise CensusError("docs/oss-upstreams.lock.json schemaVersion must be 2")
    sources = document.get("sources")
    if not isinstance(sources, list):
        raise CensusError("docs/oss-upstreams.lock.json sources must be an array")
    names = [source.get("name") for source in sources if isinstance(source, dict)]
    if tuple(names) != REQUIRED_UPSTREAMS:
        raise CensusError(
            "Upstream lock must contain the five providers in canonical order: "
            + ", ".join(REQUIRED_UPSTREAMS)
        )
    if set(EXTRACTORS) != set(REQUIRED_UPSTREAMS):
        raise CensusError("Extractor registry does not exactly match the five pinned providers")
    for source in sources:
        for field in (
            "name",
            "repository",
            "ref",
            "refObject",
            "refObjectType",
            "commit",
            "checkout",
            "submodules",
            "license",
            "sentinelFiles",
        ):
            if field not in source:
                raise CensusError(f"Upstream {source.get('name')!r} is missing {field!r}")
        commit = source["commit"]
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise CensusError(f"Upstream {source['name']!r} has an invalid commit")
        if not isinstance(source["repository"], str) or not source["repository"].startswith(
            "https://"
        ):
            raise CensusError(f"Upstream {source['name']!r} must use a locked HTTPS origin")
        if source["refObjectType"] not in {"tag", "commit"}:
            raise CensusError(f"Upstream {source['name']!r} has an invalid ref object type")
        for digest_field in ("refObject", "commit"):
            digest = source[digest_field]
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{40}", digest):
                raise CensusError(
                    f"Upstream {source['name']!r} has an invalid {digest_field}"
                )
        if not isinstance(source["checkout"], dict) or not isinstance(
            source["submodules"], dict
        ):
            raise CensusError(f"Upstream {source['name']!r} checkout/submodules must be objects")
    return sources


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or "unit")[:56].rstrip("-")


def _unit_id(observation: Observation) -> str:
    identity = "\0".join((observation.upstream, observation.key))
    digest = sha256_text(identity)[:16]
    return f"{observation.upstream}:{observation.category}:{_slug(observation.name)}:{digest}"


def _validate_observation_identity(item: Observation) -> None:
    expected = canonical_contract_key(
        transport=item.transport,
        operation=item.operation,
        route=item.route,
        handler=item.handler,
    )
    if item.key != expected:
        raise CensusError(
            f"Non-canonical observation key for {item.upstream}/{item.path}:{item.line}"
        )
    if "#declaration" in item.key:
        raise CensusError(f"Declaration suffix entered canonical identity: {item.key}")


def _reconcile_observations(items: list[Observation]) -> list[Observation]:
    """Collapse repeated declarations without mutating their canonical boundary identity."""

    grouped: dict[tuple[str, str], list[Observation]] = defaultdict(list)
    for item in items:
        _validate_observation_identity(item)
        grouped[(item.upstream, item.key)].append(item)

    reconciled: list[Observation] = []
    identity_fields = (
        "upstream",
        "category",
        "boundary_type",
        "transport",
        "operation",
        "route",
        "handler",
    )
    for (_upstream, _key), declarations in sorted(grouped.items()):
        declarations.sort(
            key=lambda item: (item.path, item.line, item.extractor, item.candidate_id)
        )
        primary = declarations[0]
        for declaration in declarations[1:]:
            conflicts = [
                field
                for field in identity_fields
                if getattr(primary, field) != getattr(declaration, field)
            ]
            if conflicts:
                raise CensusError(
                    f"Canonical boundary metadata conflict for {primary.key}: {conflicts}"
                )
        details = dict(primary.details)
        if len(declarations) > 1:
            details["declarationCount"] = len(declarations)
            details["declarations"] = [
                {
                    "path": declaration.path,
                    "line": declaration.line,
                    "evidence": declaration.evidence,
                    "extractor": declaration.extractor,
                    "candidateId": declaration.candidate_id,
                    "family": declaration.family,
                    "details": dict(sorted(declaration.details.items())),
                }
                for declaration in declarations
            ]
        reconciled.append(
            replace(
                primary,
                aliases=tuple(
                    sorted(
                        {
                            alias
                            for declaration in declarations
                            for alias in declaration.aliases
                        }
                    )
                ),
                facets=tuple(
                    sorted(
                        {
                            facet
                            for declaration in declarations
                            for facet in declaration.facets
                        }
                    )
                ),
                details=details,
            )
        )
    return reconciled


_CONTRACT_DEFAULTS = {
    "api": {
        "actor": "api_client",
        "inputs": ["http_request"],
        "outputs": ["http_response"],
        "event": "request",
        "externalEffect": "potential_network_or_persistence_effect",
        "dependencies": ["upstream_http_runtime"],
    },
    "ui": {
        "actor": "user_or_administrator",
        "inputs": ["user_interaction"],
        "outputs": ["rendered_interface"],
        "event": "navigation_or_interaction",
        "externalEffect": "none_inferred_at_route_boundary",
        "dependencies": ["upstream_browser_runtime"],
    },
    "job": {
        "actor": "scheduler_or_worker",
        "inputs": ["job_payload_or_schedule"],
        "outputs": ["job_result_or_state_transition"],
        "event": "job_execution",
        "externalEffect": "potential_provider_or_persistence_effect",
        "dependencies": ["upstream_worker_runtime"],
    },
    "webhook": {
        "actor": "external_provider",
        "inputs": ["webhook_request"],
        "outputs": ["acknowledgement_and_state_transition"],
        "event": "webhook_delivery",
        "externalEffect": "inbound_network_effect",
        "dependencies": ["upstream_http_runtime", "external_provider_contract"],
    },
    "mcp": {
        "actor": "mcp_client_or_agent",
        "inputs": ["mcp_tool_arguments"],
        "outputs": ["mcp_tool_result"],
        "event": "tool_invocation",
        "externalEffect": "potential_process_network_or_persistence_effect",
        "dependencies": ["mcp_transport", "upstream_application_runtime"],
    },
    "realtime": {
        "actor": "realtime_client_or_server",
        "inputs": ["realtime_event_or_subscription"],
        "outputs": ["realtime_event_or_stream"],
        "event": "realtime_exchange",
        "externalEffect": "bidirectional_network_effect",
        "dependencies": ["upstream_realtime_transport"],
    },
    "permission": {
        "actor": "administrator_or_authorization_runtime",
        "inputs": ["principal_and_action"],
        "outputs": ["authorization_decision"],
        "event": "permission_evaluation_or_configuration",
        "externalEffect": "none",
        "dependencies": ["upstream_authorization_store"],
    },
    "config": {
        "actor": "administrator_or_integrator",
        "inputs": ["configuration_value"],
        "outputs": ["effective_configuration"],
        "event": "configuration_read_or_change",
        "externalEffect": "none_until_consumed_by_runtime",
        "dependencies": ["upstream_configuration_store"],
    },
    "import": {
        "actor": "user_administrator_or_worker",
        "inputs": ["import_payload_or_file"],
        "outputs": ["validation_and_import_result"],
        "event": "import",
        "externalEffect": "file_io_and_persistence_effect",
        "dependencies": ["upstream_import_pipeline"],
    },
    "export": {
        "actor": "user_administrator_or_worker",
        "inputs": ["export_scope_and_filters"],
        "outputs": ["export_artifact_or_stream"],
        "event": "export",
        "externalEffect": "file_io_effect",
        "dependencies": ["upstream_export_pipeline"],
    },
}


def _state_effect(observation: Observation) -> str:
    method = (observation.method or "").upper()
    if method in {"GET", "HEAD", "OPTIONS"}:
        return "read"
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        return "write_or_transition"
    if observation.category in {"permission", "config", "ui"}:
        return "source_specific"
    if observation.category in {"job", "webhook", "mcp", "realtime", "import"}:
        return "potential_transition"
    if observation.category == "export":
        return "read_and_artifact_creation"
    return "not_inferred"


def _interface_contract(observation: Observation) -> dict[str, Any]:
    defaults = _CONTRACT_DEFAULTS[observation.category]
    permissions = observation.details.get("permissions")
    if permissions is None and observation.category == "permission":
        permissions = observation.details.get("defaultRoles")
    permission_status = "declared" if permissions else "not_inferred_by_static_extractor"
    event = observation.details.get("event", defaults["event"])
    return {
        "inferenceLevel": "static_interface",
        "actor": defaults["actor"],
        "permission": {"status": permission_status, "value": permissions or None},
        "inputs": defaults["inputs"],
        "outputs": defaults["outputs"],
        "state": {"effect": _state_effect(observation)},
        "event": event,
        "errors": {"status": "not_statically_enumerated", "boundary": observation.category},
        "externalEffect": defaults["externalEffect"],
    }


def _klm_mapping(disposition: dict[str, Any]) -> dict[str, Any]:
    blocked = disposition["state"] in {"blocked_proprietary", "blocked_license"}
    mapping = dict(disposition.get("mapping") or {})
    result = {
        "assessment": (
            "blocked"
            if blocked
            else disposition["state"]
            if disposition["state"] != "discovered"
            else "not_assessed"
        ),
        "canonicalEntity": None,
        "spaRoute": None,
        "applicationService": None,
        "agentTool": None,
        "auditEvent": None,
        "localMode": None,
        "fixture": None,
        "klmTests": [],
        "visualProof": [],
        "sidecarOffProof": [],
        "apiRoute": None,
        "runtimeContractId": None,
        "runtimeHandler": None,
        "semanticProfile": None,
        "proofProfile": None,
        "verificationManifest": None,
    }
    result.update(mapping)
    return result


def _unit_document(
    observation: Observation,
    pin: dict[str, Any],
    license_scopes: tuple[dict[str, Any], ...],
    disposition_policy: DispositionPolicy,
    upstream_test_scopes: tuple[str, ...],
) -> dict[str, Any]:
    license_data = resolve_license_scope(observation.path, license_scopes)
    unit_id = _unit_id(observation)
    disposition = disposition_policy.resolve(
        observation,
        license_data["status"],
        unit_id,
    )
    public_disposition = {
        key: value
        for key, value in disposition.items()
        if key != "mapping"
    }
    details = dict(sorted(observation.details.items()))
    return {
        "id": unit_id,
        "upstream": observation.upstream,
        "ref": pin["ref"],
        "commit": pin["commit"],
        "category": observation.category,
        "facets": list(observation.facets),
        "name": observation.name,
        "contractKey": observation.key,
        "source": {
            "path": observation.path,
            "line": observation.line,
            "evidence": observation.evidence,
            "extractor": observation.extractor,
            "boundaryType": observation.boundary_type,
            "family": observation.family,
            "transport": observation.transport,
            "operation": observation.operation,
            "method": observation.method,
            "route": observation.route,
            "handler": observation.handler,
            "aliases": list(observation.aliases),
            "candidateId": observation.candidate_id,
            "candidateKind": observation.candidate_kind,
            "details": details,
        },
        "license": {
            "status": license_data["status"],
            "conclusion": license_data["conclusion"],
            "scopePrefix": license_data["prefix"],
            "package": license_data["package"],
            "evidence": license_data.get("evidence", []),
        },
        "dependencies": _CONTRACT_DEFAULTS[observation.category]["dependencies"],
        "interfaceContract": _interface_contract(observation),
        "upstreamVerification": {
            "status": "provider_test_inventory_recorded_not_unit_reconciled",
            "testScopes": list(upstream_test_scopes),
            "tests": [],
        },
        "klmMapping": _klm_mapping(disposition),
        "disposition": public_disposition,
    }


def _scope_manifest(scope: ScopeResult) -> str:
    lines = []
    for item in scope.files:
        lines.append(
            json.dumps(
                {
                    "path": item.path,
                    "sha256": item.sha256,
                    "classification": item.classification,
                    "observationCount": item.observation_count,
                    "candidateCount": item.candidate_count,
                    "categories": item.categories,
                    "candidateStatuses": item.candidate_statuses,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return sha256_text("\n".join(lines) + ("\n" if lines else ""))


def _scope_document(scope: ScopeResult) -> dict[str, Any]:
    classifications = Counter(item.classification for item in scope.files)
    categories = Counter(observation.category for observation in scope.observations)
    candidate_ids = [candidate.id for candidate in scope.candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise CensusError(f"Duplicate candidates in scope {scope.upstream}/{scope.scope_id}")
    candidate_map = {candidate.id: candidate for candidate in scope.candidates}
    observation_keys_by_candidate: dict[str, set[str]] = defaultdict(set)
    for item in scope.observations:
        candidate = candidate_map.get(item.candidate_id)
        if candidate is None or candidate.status != "accepted":
            raise CensusError(
                f"Observation lacks an accepted candidate in {scope.upstream}/{scope.scope_id}: "
                f"{item.candidate_id}"
            )
        observation_keys_by_candidate[item.candidate_id].add(item.key)
    for candidate in scope.candidates:
        if candidate.status != "accepted":
            continue
        emitted = set(candidate.emitted_contract_keys)
        if emitted != observation_keys_by_candidate[candidate.id]:
            raise CensusError(
                f"Candidate emission mismatch in {scope.upstream}/{scope.scope_id}: "
                f"{candidate.id}"
            )
    statuses = Counter(candidate.status for candidate in scope.candidates)
    reasons = Counter(
        candidate.reason for candidate in scope.candidates if candidate.reason is not None
    )
    return {
        "upstream": scope.upstream,
        "id": scope.scope_id,
        "extractor": scope.extractor,
        "includes": list(scope.includes),
        "excludes": list(scope.excludes),
        "filesExamined": len(scope.files),
        "filesClassified": len(scope.files),
        "fileClassifications": dict(sorted(classifications.items())),
        "observationsClassified": len(scope.observations),
        "candidatesClassified": len(scope.candidates),
        "candidatesByStatus": {
            status: statuses[status] for status in ("accepted", "excluded", "manual_review")
        },
        "categories": {category: categories.get(category, 0) for category in CATEGORIES},
        "candidatesByReason": dict(sorted(reasons.items())),
        "examinedManifestSha256": _scope_manifest(scope),
    }


def _source_files(
    extractions: list[ExtractionResult], units: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    file_metadata: dict[tuple[str, str], dict[str, Any]] = {}
    for extraction in extractions:
        for scope in extraction.scopes:
            for item in scope.files:
                key = (extraction.upstream, item.path)
                metadata = file_metadata.setdefault(
                    key,
                    {"sha256": item.sha256, "scopeIds": set(), "classifications": set()},
                )
                if metadata["sha256"] != item.sha256:
                    raise CensusError(
                        f"Source hash changed during census: {extraction.upstream}/{item.path}"
                    )
                metadata["scopeIds"].add(scope.scope_id)
                metadata["classifications"].add(item.classification)

    unit_counts: Counter[tuple[str, str]] = Counter()
    category_counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for unit in units:
        key = (unit["upstream"], unit["source"]["path"])
        unit_counts[key] += 1
        category_counts[key][unit["category"]] += 1

    documents = []
    for key in sorted(unit_counts):
        if key not in file_metadata:
            raise CensusError(
                f"Unit source was not present in an examined scope: {key[0]}/{key[1]}"
            )
        metadata = file_metadata[key]
        documents.append(
            {
                "upstream": key[0],
                "path": key[1],
                "sha256": metadata["sha256"],
                "scopeIds": sorted(metadata["scopeIds"]),
                "classifications": sorted(metadata["classifications"]),
                "unitCount": unit_counts[key],
                "categories": dict(sorted(category_counts[key].items())),
            }
        )
    return documents


def _generator_manifest(repo_root: Path) -> dict[str, Any]:
    files = sorted((repo_root / "scripts" / "oss_census").rglob("*.py"))
    files.extend(
        (
            repo_root / "scripts" / "census_oss_function_units.py",
            repo_root / "scripts" / "build_oss_function_unit_dispositions.py",
        )
    )
    records = []
    for path in sorted(set(files)):
        if path.is_file():
            records.append(
                {
                    "path": path.relative_to(repo_root).as_posix(),
                    "sha256": sha256_bytes(path.read_bytes()),
                }
            )
    digest_input = "\n".join(f"{item['path']}\0{item['sha256']}" for item in records)
    return {"sourceFiles": records, "sourceDigest": sha256_text(digest_input)}


def _validate_units(units: list[dict[str, Any]], pins: dict[str, dict[str, Any]]) -> None:
    if len(units) < MINIMUM_FAMILY_BASELINE:
        raise CensusError(
            f"Function-unit denominator {len(units)} is below the 135-family V5 baseline"
        )
    ids = [unit["id"] for unit in units]
    if len(ids) != len(set(ids)):
        duplicates = [item for item, count in Counter(ids).items() if count > 1]
        raise CensusError(f"Duplicate function-unit IDs: {duplicates[:5]}")
    contract_identities = [(unit["upstream"], unit["contractKey"]) for unit in units]
    if len(contract_identities) != len(set(contract_identities)):
        raise CensusError("Canonical contract identities were not fully reconciled")
    for unit in units:
        if unit["upstream"] not in pins:
            raise CensusError(f"Unknown upstream in unit {unit['id']}")
        if unit["category"] not in CATEGORIES:
            raise CensusError(f"Invalid category in unit {unit['id']}: {unit['category']}")
        if unit["disposition"]["state"] not in DISPOSITION_STATES:
            raise CensusError(f"Missing or invalid disposition in unit {unit['id']}")
        source_path = PurePathGuard(unit["source"]["path"])
        if source_path.is_proprietary:
            raise CensusError(f"Proprietary source entered denominator: {unit['source']['path']}")
        if "#declaration" in unit["contractKey"]:
            raise CensusError(f"Declaration suffix entered unit identity: {unit['id']}")
        if unit["license"]["status"] not in {
            "verified-file",
            "package-metadata-only",
        }:
            raise CensusError(f"Unverified license scope entered denominator: {unit['id']}")
        required_source_fields = {
            "boundaryType",
            "family",
            "transport",
            "operation",
            "route",
            "handler",
            "candidateId",
        }
        if not required_source_fields <= set(unit["source"]):
            raise CensusError(f"Incomplete structured source contract: {unit['id']}")
    missing_categories = [
        category
        for category in CATEGORIES
        if not any(unit["category"] == category for unit in units)
    ]
    if missing_categories:
        raise CensusError(
            f"No observed units for required categories: {', '.join(missing_categories)}"
        )
    missing_providers = [
        provider
        for provider in REQUIRED_UPSTREAMS
        if not any(unit["upstream"] == provider for unit in units)
    ]
    if missing_providers:
        raise CensusError(f"No observed units for providers: {', '.join(missing_providers)}")


class PurePathGuard:
    """Portable path validation without accepting absolute or parent paths."""

    def __init__(self, value: str):
        if not isinstance(value, str) or not value:
            raise CensusError("Unit source path must be a non-empty string")
        normalized = value.replace("\\", "/")
        parts = normalized.split("/")
        if normalized.startswith("/") or ".." in parts:
            raise CensusError(f"Unit source path must be relative: {value}")
        self.parts = parts

    @property
    def is_proprietary(self) -> bool:
        return "enterprise" in self.parts or "ee" in self.parts


def build_census(
    *,
    repo_root: Path,
    upstreams_root: Path | None = None,
    upstream_lock_path: Path | None = None,
    disposition_path: Path | None = None,
    verify_pins: bool = True,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    upstreams_root = (upstreams_root or repo_root / ".vendor" / "upstreams").resolve()
    upstream_lock_path = upstream_lock_path or repo_root / "docs" / "oss-upstreams.lock.json"
    disposition_path = (
        disposition_path or repo_root / "app" / "data" / "oss_function_unit_dispositions.json"
    )

    upstream_lock = _load_json(upstream_lock_path)
    sources = _validate_upstream_lock(upstream_lock)
    disposition_policy = load_disposition_policy(disposition_path)
    pins = {source["name"]: source for source in sources}

    family_baseline = validate_family_baseline(repo_root)
    extractions: list[ExtractionResult] = []
    source_receipts: dict[str, dict[str, Any]] = {}
    license_scopes_by_upstream: dict[str, tuple[dict[str, Any], ...]] = {}
    with TemporaryDirectory(prefix="klm-oss-census-") as temporary_root_name:
        temporary_root = Path(temporary_root_name)
        for source in sources:
            name = source["name"]
            source_root = upstreams_root / name
            if not source_root.is_dir():
                raise CensusError(f"Source tree does not exist: {source_root}")
            entries = {}
            extraction_root = source_root
            if verify_pins:
                canonical_source = CanonicalGitSource(source_root, source)
                receipt = canonical_source.verify()
                extraction_root = temporary_root / name
                entries = canonical_source.materialize(extraction_root, SOURCE_PATTERNS[name])
                if not entries:
                    raise CensusError(f"Canonical source selection is empty for {name}")
                source_receipts[name] = {
                    "status": "verified",
                    "reason": None,
                    "patterns": list(SOURCE_PATTERNS[name]),
                    **receipt,
                }
            else:
                source_receipts[name] = _skipped_pin_receipt(
                    source, SOURCE_PATTERNS[name]
                )
            license_scopes_by_upstream[name] = build_license_scopes(
                extraction_root, source, entries
            )
            extractions.append(extract_upstream(name, extraction_root))

    raw_observations = sorted(
        (item for extraction in extractions for item in extraction.observations),
        key=lambda item: (item.upstream, item.key, item.path, item.line),
    )
    observations = _reconcile_observations(raw_observations)
    test_scopes_by_upstream = {
        extraction.upstream: tuple(
            sorted(
                scope.scope_id
                for scope in extraction.scopes
                if any(item.classification == "supporting_test" for item in scope.files)
            )
        )
        for extraction in extractions
    }
    units = [
        _unit_document(
            observation,
            pins[observation.upstream],
            license_scopes_by_upstream[observation.upstream],
            disposition_policy,
            test_scopes_by_upstream[observation.upstream],
        )
        for observation in observations
    ]
    units.sort(key=lambda item: item["id"])
    _validate_units(units, pins)

    scope_documents = sorted(
        (_scope_document(scope) for extraction in extractions for scope in extraction.scopes),
        key=lambda item: (item["upstream"], item["id"]),
    )
    source_files = _source_files(extractions, units)
    category_counts = Counter(unit["category"] for unit in units)
    disposition_counts = Counter(unit["disposition"]["state"] for unit in units)
    upstream_counts = Counter(unit["upstream"] for unit in units)
    unique_examined_files = {
        (extraction.upstream, item.path)
        for extraction in extractions
        for scope in extraction.scopes
        for item in scope.files
    }
    supporting_files = {
        (extraction.upstream, item.path)
        for extraction in extractions
        for scope in extraction.scopes
        for item in scope.files
        if item.classification == "supporting_test"
    }
    candidate_statuses: Counter[str] = Counter()
    candidate_reasons: Counter[str] = Counter()
    for scope in scope_documents:
        candidate_statuses.update(scope["candidatesByStatus"])
        candidate_reasons.update(scope["candidatesByReason"])
    if candidate_statuses["manual_review"]:
        raise CensusError(
            f"Census has {candidate_statuses['manual_review']} unresolved manual-review candidates"
        )

    pin_documents = []
    for source in sources:
        pin_documents.append(
            {
                "name": source["name"],
                "repository": source["repository"],
                "ref": source["ref"],
                "commit": source["commit"],
                "checkout": source["checkout"],
                "licenseScopes": list(license_scopes_by_upstream[source["name"]]),
                "sourceRoot": f".vendor/upstreams/{source['name']}",
                "sourceVerification": source_receipts[source["name"]],
                "unitCount": upstream_counts[source["name"]],
            }
        )

    policy_raw = disposition_path.read_bytes()
    lock_raw = upstream_lock_path.read_bytes()
    document = {
        "schemaVersion": SCHEMA_VERSION,
        "generator": {
            "name": "scripts/census_oss_function_units.py",
            "version": GENERATOR_VERSION,
            "command": "python3.12 scripts/census_oss_function_units.py",
            "determinism": (
                "Exact CPython toolchain, no wall-clock fields, Git-object source bytes, "
                "canonical ordering, and content-derived IDs/hashes."
            ),
            "categories": list(CATEGORIES),
            "pythonToolchain": parser_receipt(),
            **_generator_manifest(repo_root),
        },
        "upstreamLock": {
            "path": upstream_lock_path.relative_to(repo_root).as_posix(),
            "sha256": sha256_bytes(lock_raw),
            "schemaVersion": upstream_lock["schemaVersion"],
        },
        "dispositionPolicy": {
            "path": disposition_path.relative_to(repo_root).as_posix(),
            "sha256": sha256_bytes(policy_raw),
            "schemaVersion": disposition_policy.schema_version,
            "policy": disposition_policy.policy,
        },
        "definition": (
            "A FunctionUnit is a public behavior observable through API, UI, job, webhook, "
            "MCP, realtime, permission, configuration, import, or export boundaries. Internal "
            "helpers and excluded proprietary trees are not denominator units."
        ),
        "familyBaseline": family_baseline,
        "pins": pin_documents,
        "coverage": {
            "denominator": len(units),
            "rawObservations": len(raw_observations),
            "reconciledDuplicateDeclarations": len(raw_observations) - len(units),
            "familyBaselineFloor": MINIMUM_FAMILY_BASELINE,
            "validatedFamilyBaseline": family_baseline["familyCount"],
            "allPinsVerified": all(
                receipt["status"] == "verified" for receipt in source_receipts.values()
            ),
            "allUnitsHaveDisposition": len(units)
            == sum(disposition_counts[state] for state in DISPOSITION_STATES),
            "byUpstream": {name: upstream_counts[name] for name in REQUIRED_UPSTREAMS},
            "byCategory": {category: category_counts[category] for category in CATEGORIES},
            "byDisposition": {
                state: disposition_counts[state] for state in DISPOSITION_STATES
            },
            "sourceScopes": len(scope_documents),
            "sourceFilesExamined": len(unique_examined_files),
            "sourceFilesWithUnits": len(source_files),
            "supportingTestFilesExamined": len(supporting_files),
            "candidates": {
                status: candidate_statuses[status]
                for status in ("accepted", "excluded", "manual_review")
            },
            "candidatesByReason": dict(sorted(candidate_reasons.items())),
        },
        "sourceScopes": scope_documents,
        "classifiedSourceFiles": source_files,
        "units": units,
        "limitations": [
            (
                "Static extraction records interface-level contracts; payload, error, and state "
                "semantics remain explicitly marked as not inferred where source declarations "
                "do not prove them."
            ),
            (
                "Upstream tests are inventoried by scope but are not yet reconciled one-to-one "
                "with every FunctionUnit."
            ),
            (
                "KLM native mappings, fixtures, visual proofs, and sidecar-off proofs remain "
                "unassessed for discovered units."
            ),
            (
                "Rocket EmbeddedChat units are retained in the denominator but blocked_license "
                "under package-prefix metadata and the current root-license review gate."
            ),
        ],
    }
    canonical_payload = json.dumps(
        document, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    document["reproducibility"] = {
        "payloadSha256": sha256_text(canonical_payload),
        "scope": "all_lock_fields_except_this_reproducibility_receipt",
    }
    return document


def render_census(document: dict[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=True, indent=2) + "\n"
