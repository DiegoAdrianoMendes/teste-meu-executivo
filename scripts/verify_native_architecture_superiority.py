#!/usr/bin/env python3
"""Build or verify the fail-closed KLM native architecture scorecard."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RELEASE_EVIDENCE_RELATIVE = Path(".local/qa-evidence/oss-native")
OUTPUT = ROOT / RELEASE_EVIDENCE_RELATIVE / "native_architecture_superiority.json"
RELEASE_MANIFEST = ROOT / RELEASE_EVIDENCE_RELATIVE / "oss_native_verification.json"
RELEASE_PROOF_DIR = ROOT / RELEASE_EVIDENCE_RELATIVE / "proofs"
FUNCTION_LOCK = ROOT / "docs/oss-function-units.lock.json"
UPSTREAM_LOCK = ROOT / "docs/oss-upstreams.lock.json"
CONTRACT = ROOT / "docs/KLM_NATIVE_ARCHITECTURE_SUPERIORITY_CONTRACT.md"

FORBIDDEN_PUBLIC_VOCABULARY = re.compile(
    r"chatwoot|rocket(?:[. _-]?chat)|chatter(?:[. _-]?mate)|"
    r"bottle(?:[. _-]?crm)|sidecar|/api/v1/oss-|"
    r"(?:^|[^a-z0-9])oss(?:[^a-z0-9]|$)",
    re.IGNORECASE,
)
EXPECTED_UPSTREAMS = {
    "bottlecrm",
    "chattermate",
    "chatwoot",
    "rocket-embedded-chat",
    "rocketchat",
}
REQUIRED_PRIMARY_ERRORS = {
    "idempotency_conflict",
    "permission_denied",
    "version_conflict",
}
REQUIRED_CRITERION_IDS = (
    "provider_neutral_native_boundary",
    "strict_typed_contracts",
    "classified_tenant_isolation",
    "transactional_rls_role_hardening",
    "tenant_reference_integrity",
    "optimistic_and_locked_concurrency",
    "intent_idempotency",
    "exact_single_use_human_veto",
    "immutable_evidence",
    "durable_post_commit_effects",
    "explicit_external_dependency_gates",
    "source_bound_release_verification",
)
REQUIRED_RELEASE_SUITES = (
    "backend",
    "frontend-unit",
    "frontend-e2e",
    "local-runtime",
    "upstream-locks",
)


@dataclass(frozen=True)
class Criterion:
    id: str
    passed: bool
    summary: str
    evidence: tuple[str, ...]
    metrics: dict[str, Any]


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected an object in {path}")
    return payload


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest_files(root: Path, paths: set[Path]) -> tuple[str, list[dict[str, Any]]]:
    digest = hashlib.sha256()
    inventory: list[dict[str, Any]] = []
    for path in sorted(paths):
        payload = path.read_bytes()
        relative = path.relative_to(root).as_posix()
        file_digest = hashlib.sha256(payload).hexdigest()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
        inventory.append({"path": relative, "sha256": file_digest, "size": len(payload)})
    return digest.hexdigest(), inventory


def _source_inventory(root: Path) -> tuple[str, list[dict[str, Any]]]:
    from app.services.oss_capability_registry import (
        _verification_files_digest,
        _verification_source_inventory,
    )

    digest = _verification_files_digest(refresh=True)
    inventory = _verification_source_inventory()
    missing_required = sorted(
        path
        for path in (
            "app/main.py",
            "app/api/v1/router.py",
            "frontend/vite.config.ts",
            "scripts/verify_native_architecture_superiority.py",
            "scripts/verify_saas_v5_p5.py",
            "tests/scripts/test_native_architecture_superiority.py",
            "tests/scripts/test_verify_saas_v5_p5.py",
        )
        if path not in {str(item.get("path")) for item in inventory}
    )
    if missing_required:
        raise ValueError(f"Superiority source inventory is incomplete: {missing_required}")
    return digest, inventory


def _criterion_upstream_pins(root: Path, upstream_lock: dict[str, Any]) -> Criterion:
    sources = upstream_lock.get("sources", [])
    names = {source.get("name") for source in sources if isinstance(source, dict)}
    valid_commits = all(
        isinstance(source.get("commit"), str)
        and re.fullmatch(r"[0-9a-f]{40}", source["commit"])
        for source in sources
    )
    licensed = all(
        source.get("license", {}).get("status")
        in {"verified-file", "package-metadata-only"}
        for source in sources
    )
    materialized = all((root / ".vendor/upstreams" / str(name)).is_dir() for name in names)
    passed = names == EXPECTED_UPSTREAMS and valid_commits and licensed and materialized
    return Criterion(
        id="pinned_upstream_baseline",
        passed=passed,
        summary="Comparator sources are commit-pinned, license-scoped and materialized locally.",
        evidence=("docs/oss-upstreams.lock.json", ".vendor/upstreams"),
        metrics={"sources": len(sources), "materialized": materialized},
    )


def _criterion_function_units(
    root: Path,
    function_lock: dict[str, Any],
) -> tuple[Criterion, Criterion, dict[str, int]]:
    units = function_lock.get("units", [])
    coverage = function_lock.get("coverage", {})
    dispositions = Counter(
        str(unit.get("disposition", {}).get("state", "missing")) for unit in units
    )
    for state in (
        "discovered",
        "mapped",
        "native_shadow",
        "native_primary",
        "native_verified",
        "blocked_proprietary",
        "blocked_license",
        "missing",
    ):
        dispositions.setdefault(state, 0)
    census_passed = (
        coverage.get("denominator") == len(units)
        and coverage.get("candidates", {}).get("manual_review") == 0
        and dispositions["discovered"] == 0
        and dispositions["mapped"] == 0
        and dispositions["missing"] == 0
    )
    census = Criterion(
        id="complete_honest_function_census",
        passed=census_passed,
        summary="Every extracted function has one explicit maturity or license disposition.",
        evidence=("docs/oss-function-units.lock.json",),
        metrics={"denominator": len(units), **dict(sorted(dispositions.items()))},
    )

    primary_errors: list[str] = []
    shadow_errors: list[str] = []
    human_veto_count = 0
    for unit in units:
        unit_id = str(unit.get("id"))
        state = str(unit.get("disposition", {}).get("state"))
        if state == "blocked_license":
            continue
        mapping = unit.get("klmMapping", {})
        effects = mapping.get("effects", {})
        authorization = mapping.get("authorizationPolicy", {})
        if effects.get("externalNetworkAllowed") is not False:
            primary_errors.append(f"{unit_id}:external_network")
        if mapping.get("maturity") != state:
            primary_errors.append(f"{unit_id}:maturity_mismatch")
        if state in {"native_primary", "native_verified"}:
            if not mapping.get("capabilityActionId"):
                primary_errors.append(f"{unit_id}:unbound_action")
            if mapping.get("coverageKind") == "category_default":
                primary_errors.append(f"{unit_id}:category_default")
            if effects.get("audit") is not True:
                primary_errors.append(f"{unit_id}:audit_disabled")
            if authorization.get("workspaceScoped") is not True:
                primary_errors.append(f"{unit_id}:workspace_unscoped")
            errors = set(mapping.get("errorContract", []))
            if not REQUIRED_PRIMARY_ERRORS <= errors:
                primary_errors.append(f"{unit_id}:weak_error_contract")
            if not isinstance(mapping.get("inputSchema"), dict) or not isinstance(
                mapping.get("outputSchema"), dict
            ):
                primary_errors.append(f"{unit_id}:untyped_contract")
        if state == "native_shadow":
            if effects.get("externalNetworkAllowed") is not False:
                shadow_errors.append(f"{unit_id}:external_network")
            if mapping.get("proofProfile") != "typed_native_shadow_contract":
                shadow_errors.append(f"{unit_id}:proof_profile")
        if authorization.get("humanVeto") is True:
            human_veto_count += 1
            if not (
                authorization.get("approvalTypePrefix")
                and authorization.get("singleUse") is True
                and authorization.get("intentBound") is True
            ):
                primary_errors.append(f"{unit_id}:weak_human_veto")

    runtime_source = (root / "app/services/oss_function_unit_runtime_service.py").read_text(
        encoding="utf-8"
    )
    shadow_guard = (
        'contract.maturity in {"mapped", "native_shadow"}' in runtime_source
        and "OSSFunctionUnitMaturityError" in runtime_source
    )
    contracts_passed = not primary_errors and not shadow_errors and shadow_guard
    contracts = Criterion(
        id="typed_fail_closed_native_contracts",
        passed=contracts_passed,
        summary=(
            "Primary functions are precisely bound; shadows cannot mutate; "
            "risky intent is single-use."
        ),
        evidence=(
            "app/services/native_function_unit_binding_service.py",
            "app/services/oss_function_unit_runtime_service.py",
            "tests/services/test_native_function_unit_bindings.py",
            "tests/services/test_oss_function_unit_runtime_service.py",
        ),
        metrics={
            "primaryErrors": len(primary_errors),
            "shadowErrors": len(shadow_errors),
            "shadowRuntimeGuard": shadow_guard,
            "humanVetoFunctions": human_veto_count,
        },
    )
    return census, contracts, dict(sorted(dispositions.items()))


def _criterion_aggregate_policy() -> Criterion:
    from app.services.native_aggregate_policy import (
        NativeAggregateStateError,
        NativeAggregateVersionConflictError,
        apply_native_aggregate_transition,
    )

    created = apply_native_aggregate_transition(
        effect="upsert",
        operation_id="create",
        patch={"profile": {"name": "KLM", "tier": "premium"}},
        current_state=None,
        current_data=None,
        current_version=0,
        expected_version=0,
    )
    updated = apply_native_aggregate_transition(
        effect="upsert",
        operation_id="update",
        patch={"profile": {"tier": "enterprise"}},
        current_state=created.state,
        current_data=created.data,
        current_version=created.version,
        expected_version=created.version,
    )
    stale_rejected = False
    try:
        apply_native_aggregate_transition(
            effect="upsert",
            operation_id="update",
            patch={"profile": {"tier": "stale"}},
            current_state=updated.state,
            current_data=updated.data,
            current_version=updated.version,
            expected_version=created.version,
        )
    except NativeAggregateVersionConflictError:
        stale_rejected = True
    archived = apply_native_aggregate_transition(
        effect="archive",
        operation_id="archive",
        patch={},
        current_state=updated.state,
        current_data=updated.data,
        current_version=updated.version,
        expected_version=updated.version,
    )
    archived_write_rejected = False
    try:
        apply_native_aggregate_transition(
            effect="upsert",
            operation_id="update",
            patch={"profile": {"tier": "forbidden"}},
            current_state=archived.state,
            current_data=archived.data,
            current_version=archived.version,
            expected_version=archived.version,
        )
    except NativeAggregateStateError:
        archived_write_rejected = True
    passed = (
        updated.data == {"profile": {"name": "KLM", "tier": "enterprise"}}
        and stale_rejected
        and archived_write_rejected
        and len(updated.data_sha256) == 64
    )
    return Criterion(
        id="typed_aggregate_concurrency",
        passed=passed,
        summary=(
            "Native aggregates deep-merge, reject stale writes and enforce "
            "archive transitions."
        ),
        evidence=(
            "app/services/native_aggregate_policy.py",
            "tests/services/test_native_aggregate_policy.py",
        ),
        metrics={
            "staleRejected": stale_rejected,
            "archivedWriteRejected": archived_write_rejected,
            "schemaVersion": 1,
        },
    )


def _criterion_tenant_isolation() -> Criterion:
    import app.models  # noqa: F401
    from app.db.base import Base
    from app.db.rls import (
        COMMERCIAL_CONTROL_TABLES,
        PLATFORM_TASK_CONTROL_TABLES,
        RLS_DIRECT_TENANT_TABLES,
        RLS_EXEMPT_DIRECT_TENANT_TABLES,
        RLS_TENANT_TABLES,
    )

    workspace_tables = {
        table.name for table in Base.metadata.tables.values() if "workspace_id" in table.c
    }
    protected_catalog = (
        set(RLS_DIRECT_TENANT_TABLES)
        | set(COMMERCIAL_CONTROL_TABLES)
        | set(PLATFORM_TASK_CONTROL_TABLES)
    )
    protected = {
        table_name
        for table_name in protected_catalog
        if "workspace_id" in Base.metadata.tables[table_name].c
    }
    exempt = set(RLS_EXEMPT_DIRECT_TENANT_TABLES)
    complete = workspace_tables == protected | exempt and protected.isdisjoint(exempt)
    reviewed = all(
        exception.category
        and exception.reason
        and len(exception.alternative_controls) >= 2
        for exception in RLS_EXEMPT_DIRECT_TENANT_TABLES.values()
    )
    tenant_source = (ROOT / "app/db/tenant.py").read_text(encoding="utf-8")
    guard_source = (ROOT / "app/db/rls.py").read_text(encoding="utf-8")
    upstream_source = (
        ROOT / ".vendor/upstreams/bottlecrm/backend/common/middleware/rls_context.py"
    ).read_text(encoding="utf-8")
    transaction_local = (
        "set_config('app.current_workspace_id', :workspace_id, true)" in tenant_source
    )
    no_workspace_switch = "A database session cannot switch workspaces" in tenant_source
    role_guard = "rolbypassrls" in guard_source and "rolsuper" in guard_source
    strongest_baseline_recorded = "set_config('app.current_org', %s, false)" in upstream_source
    passed = (
        complete
        and reviewed
        and transaction_local
        and no_workspace_switch
        and role_guard
        and strongest_baseline_recorded
    )
    return Criterion(
        id="classified_transactional_tenant_isolation",
        passed=passed,
        summary=(
            "Every workspace table is protected or explicitly controlled; "
            "RLS context is transactional."
        ),
        evidence=(
            "app/db/tenant.py",
            "app/db/rls.py",
            "tests/db/test_tenant_rls_sqlite.py",
            ".vendor/upstreams/bottlecrm/backend/common/middleware/rls_context.py",
        ),
        metrics={
            "workspaceTables": len(workspace_tables),
            "directRlsTables": len(protected),
            "allRlsTables": len(RLS_TENANT_TABLES),
            "reviewedExceptions": len(exempt),
            "classificationComplete": complete,
            "exceptionsReviewed": reviewed,
            "transactionLocal": transaction_local,
            "workspaceSwitchRejected": no_workspace_switch,
            "runtimeRoleGuarded": role_guard,
            "upstreamSessionScopeRecorded": strongest_baseline_recorded,
        },
    )


def _criterion_evidence_and_approvals(root: Path) -> Criterion:
    migration_source = (
        root / "alembic/versions/d4f6a8c0e215_enforce_tenant_evidence_integrity.py"
    ).read_text(encoding="utf-8")
    approval_migration_source = (
        root / "alembic/versions/f6b8d0e2a417_linearize_approval_workflow.py"
    ).read_text(encoding="utf-8")
    model_source = (root / "app/models/approval.py").read_text(encoding="utf-8")
    schema_source = (root / "app/schemas/approval.py").read_text(encoding="utf-8")
    service_source = (root / "app/services/approval_service.py").read_text(encoding="utf-8")
    endpoint_source = (root / "app/api/v1/endpoints/approvals.py").read_text(encoding="utf-8")
    post_commit_source = (root / "app/db/post_commit.py").read_text(encoding="utf-8")
    upstream_approval = (
        root / ".vendor/upstreams/bottlecrm/backend/cases/approval_views.py"
    ).read_text(encoding="utf-8")
    immutable = all(
        marker in migration_source
        for marker in (
            "klm_reject_immutable_evidence_mutation",
            "BEFORE UPDATE OR DELETE",
            "BEFORE TRUNCATE",
            "evidence_sha256",
        )
    )
    baseline_lock = "select_for_update" in upstream_approval
    decision_typed = "ApprovalDecision" in model_source and "ApprovalDecision" in schema_source
    linearized = (
        "with_for_update" in service_source
        and "acquire_transaction_advisory_lock" in service_source
    )
    post_commit = (
        "queue_realtime_event" in service_source
        and "broadcast_board_update" not in endpoint_source
        and "BackgroundTasks" not in endpoint_source
    )
    durable_agent = (
        "queue_agent_run" in service_source
        and "queue_agent_run" in post_commit_source
        and "AgentRun" in service_source
    )
    single_pending_intent = all(
        marker in model_source
        for marker in (
            "uq_approval_pending_intent",
            '"matter_id"',
            '"approval_type"',
            '"intent_key"',
            "sqlite_where",
            "postgresql_where",
        )
    ) and all(
        marker in approval_migration_source
        for marker in (
            "uq_approval_pending_intent",
            "matter_id",
            "approval_type",
            "intent_key",
            "postgresql_where",
        )
    )
    intent_idempotency = all(
        marker in service_source
        for marker in (
            "ApprovalQueueItem.intent_key == intent_key",
            "approval_pending_payload_conflict",
            "existing.intent_key == data.intent_key",
        )
    )
    blank_intent_rejected = False
    try:
        from uuid import uuid4

        from app.schemas.approval import ApprovalCreate

        ApprovalCreate(
            matter_id=uuid4(),
            approval_type="petition_review",
            intent_key="   ",
            title="Review",
        )
    except ValueError:
        blank_intent_rejected = True
    workspace_bound = all(
        marker in service_source
        for marker in (
            "def __init__(self, db: AsyncSession, workspace_id: UUID)",
            "self.workspace_id = workspace_id",
            "Matter.workspace_id == self.workspace_id",
        )
    )
    passed = all(
        (
            immutable,
            baseline_lock,
            decision_typed,
            linearized,
            post_commit,
            durable_agent,
            single_pending_intent,
            intent_idempotency,
            blank_intent_rejected,
            workspace_bound,
        )
    )
    return Criterion(
        id="linearizable_human_veto_and_immutable_evidence",
        passed=passed,
        summary=(
            "KLM preserves row locking and adds typed, durable, single-use "
            "human control and evidence."
        ),
        evidence=(
            "app/models/approval.py",
            "app/services/approval_service.py",
            "app/db/post_commit.py",
            "alembic/versions/d4f6a8c0e215_enforce_tenant_evidence_integrity.py",
            "alembic/versions/f6b8d0e2a417_linearize_approval_workflow.py",
            ".vendor/upstreams/bottlecrm/backend/cases/approval_views.py",
        ),
        metrics={
            "immutableEvidence": immutable,
            "upstreamRowLockBaseline": baseline_lock,
            "typedDecision": decision_typed,
            "linearized": linearized,
            "postCommitRealtime": post_commit,
            "durableAgentRun": durable_agent,
            "singlePendingIntentConstraint": single_pending_intent,
            "intentIdempotency": intent_idempotency,
            "blankIntentRejected": blank_intent_rejected,
            "workspaceBound": workspace_bound,
        },
    )


def _criterion_public_boundary() -> Criterion:
    from app.main import app
    from app.services.native_public_boundary import sanitize_public_payload

    app.openapi_schema = None
    document = app.openapi()
    serialized = json.dumps(document, ensure_ascii=False, sort_keys=True)
    paths = set(document.get("paths", {}))
    neutral = FORBIDDEN_PUBLIC_VOCABULARY.search(serialized) is None
    no_legacy_path = not any(path.startswith("/api/v1/oss-") for path in paths)
    untyped_roots = []
    for name, schema in document.get("components", {}).get("schemas", {}).items():
        if not str(name).startswith("KLM") or not isinstance(schema, dict):
            continue
        if schema.get("type") == "object" and schema.get("additionalProperties") is True:
            if not schema.get("properties"):
                untyped_roots.append(name)
    adversarial_payload = {
        "connection": "redis:6379",
        "path": "/srv/klm/.vendor/private.py",
        "module": "app.services.internal.dispatcher",
        "nested": [{"url": "postgresql://user:secret@10.0.0.2:5432/klm"}],
    }
    sanitized_payload = sanitize_public_payload(adversarial_payload)
    serialized_adversarial = json.dumps(sanitized_payload, ensure_ascii=False)
    adversarial_blocked = not any(
        marker in serialized_adversarial
        for marker in (
            "redis:6379",
            "/srv/",
            ".vendor",
            "app.services",
            "postgresql://",
            "10.0.0.2",
            "secret",
        )
    )
    legitimate_preserved = sanitize_public_payload(
        {"meetingUrl": "https://meet.google.com/abc-defg-hij", "score": 82}
    ) == {"meetingUrl": "https://meet.google.com/abc-defg-hij", "score": 82}
    vite_source = (ROOT / "frontend/vite.config.ts").read_text(encoding="utf-8")
    bundle_unmodified = (
        "generateBundle" not in vite_source
        and "replace(/sidecar" not in vite_source.casefold()
    )
    passed = all(
        (
            neutral,
            no_legacy_path,
            not untyped_roots,
            adversarial_blocked,
            legitimate_preserved,
            bundle_unmodified,
        )
    )
    return Criterion(
        id="provider_neutral_native_boundary",
        passed=passed,
        summary="Mounted API and OpenAPI expose strict KLM DTOs without source topology.",
        evidence=(
            "app/schemas/native_public.py",
            "app/services/native_public_boundary.py",
            "tests/api/test_native_route_convergence.py",
            "frontend/scripts/verify-production-bundle.mjs",
        ),
        metrics={
            "paths": len(paths),
            "neutralVocabulary": neutral,
            "legacyPaths": not no_legacy_path,
            "untypedRootSchemas": sorted(untyped_roots),
            "adversarialPayloadBlocked": adversarial_blocked,
            "legitimatePayloadPreserved": legitimate_preserved,
            "buildArtifactUnmodified": bundle_unmodified,
        },
    )


def _criterion_tenant_reference_integrity() -> Criterion:
    import app.models  # noqa: F401
    from app.db.base import Base

    native_prefixes = ("klm_", "oss_native_")
    tenant_tables = {
        table.name for table in Base.metadata.tables.values() if "workspace_id" in table.c
    }
    checked: set[tuple[str, str, str]] = set()
    violations: list[str] = []
    for table in Base.metadata.tables.values():
        if "workspace_id" not in table.c or not table.name.startswith(native_prefixes):
            continue
        for constraint in table.foreign_key_constraints:
            referred = constraint.referred_table
            if referred.name not in tenant_tables:
                continue
            for element in constraint.elements:
                if element.parent.name == "workspace_id":
                    continue
                relation = (table.name, element.parent.name, referred.name)
                if relation in checked:
                    continue
                checked.add(relation)
                composite = any(
                    candidate.referred_table.name == referred.name
                    and {"workspace_id", element.parent.name}.issubset(
                        {candidate_element.parent.name for candidate_element in candidate.elements}
                    )
                    and "workspace_id"
                    in {candidate_element.column.name for candidate_element in candidate.elements}
                    for candidate in table.foreign_key_constraints
                )
                if not composite:
                    violations.append(".".join(relation))
    passed = bool(checked) and not violations
    return Criterion(
        id="tenant_reference_integrity",
        passed=passed,
        summary=(
            "Every tenant-owned reference used by the native-primary core is bound "
            "to the same workspace at database-schema level."
        ),
        evidence=(
            "app/models/klm_operations.py",
            "app/models/oss_native.py",
            "alembic/versions/d4f6a8c0e215_enforce_tenant_evidence_integrity.py",
            "tests/api/test_matter_tenant_references.py",
        ),
        metrics={
            "nativeTenantReferences": len(checked),
            "unboundReferences": sorted(violations),
        },
    )


def _criterion_external_dependency_gates(
    root: Path,
    function_lock: dict[str, Any],
) -> Criterion:
    from app.core.config import settings
    from app.core.klm_runtime import external_delivery_allowed

    units = function_lock.get("units", [])
    licensed_units = [
        unit
        for unit in units
        if unit.get("disposition", {}).get("state") != "blocked_license"
    ]
    network_allowances = [
        str(unit.get("id"))
        for unit in licensed_units
        if unit.get("klmMapping", {}).get("effects", {}).get("externalNetworkAllowed")
        is not False
    ]
    runtime_source = (root / "app/core/klm_runtime.py").read_text(encoding="utf-8")
    explicit_gate = all(
        marker in runtime_source
        for marker in (
            "KLM_EXTERNAL_ACTIONS_ENABLED",
            "uses_local_infrastructure",
            "external_delivery_allowed",
        )
    )
    local_default = (
        settings.is_local_only is True
        and settings.KLM_EXTERNAL_ACTIONS_ENABLED is False
        and external_delivery_allowed() is False
    )
    passed = bool(licensed_units) and not network_allowances and explicit_gate and local_default
    return Criterion(
        id="explicit_external_dependency_gates",
        passed=passed,
        summary=(
            "Every licensed native mapping is local by default and external delivery "
            "requires an explicit runtime gate."
        ),
        evidence=(
            "app/core/klm_runtime.py",
            "app/core/config.py",
            "tests/services/test_external_delivery_gates.py",
            "tests/services/test_remaining_external_sinks.py",
        ),
        metrics={
            "licensedUnits": len(licensed_units),
            "externalNetworkAllowances": len(network_allowances),
            "explicitRuntimeGate": explicit_gate,
            "localDefaultClosed": local_default,
        },
    )


def _criterion_source_bound_release(
    root: Path,
    *,
    source_digest: str,
    source_file_count: int,
    upstream: Criterion,
) -> Criterion:
    manifest: dict[str, Any] = {}
    try:
        manifest = _load_json(
            root / RELEASE_EVIDENCE_RELATIVE / "oss_native_verification.json"
        )
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        pass
    manifest_bound = (
        manifest.get("verificationFilesDigest") == source_digest
        and manifest.get("verificationFileCount") == source_file_count
        and manifest.get("sourceBinding", {}).get("mode") == "content_snapshot"
    )
    proof_artifacts = manifest.get("proofArtifacts", {})
    proofs_bound = manifest_bound and set(proof_artifacts) == set(REQUIRED_RELEASE_SUITES)
    skipped = 0
    for suite in REQUIRED_RELEASE_SUITES:
        path = root / RELEASE_EVIDENCE_RELATIVE / "proofs" / f"{suite}.json"
        try:
            proof = _load_json(path)
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            proofs_bound = False
            continue
        proof_bytes = path.read_bytes()
        artifact = proof_artifacts.get(suite, {})
        suite_skipped = int(proof.get("skipped", 0))
        skipped += suite_skipped
        proofs_bound = proofs_bound and all(
            (
                proof.get("status") == "passed",
                proof.get("sourceDigest") == source_digest,
                proof.get("sourceFileCount") == source_file_count,
                artifact.get("sha256") == hashlib.sha256(proof_bytes).hexdigest(),
                suite not in {"backend", "frontend-unit", "frontend-e2e"}
                or suite_skipped == 0,
            )
        )
    passed = upstream.passed and manifest_bound and proofs_bound
    return Criterion(
        id="source_bound_release_verification",
        passed=passed,
        summary=(
            "Pinned comparator sources and every release proof are bound to the exact "
            "current source snapshot."
        ),
        evidence=(
            "docs/oss-upstreams.lock.json",
            ".local/qa-evidence/oss-native/oss_native_verification.json",
            ".local/qa-evidence/oss-native/proofs",
            "scripts/verify_oss_native_release.py",
            "scripts/verify_saas_v5_p5.py",
        ),
        metrics={
            "pinnedBaseline": upstream.passed,
            "manifestBound": manifest_bound,
            "proofsBound": proofs_bound,
            "requiredSuites": len(REQUIRED_RELEASE_SUITES),
            "skippedTests": skipped,
        },
    )


def build_report(root: Path = ROOT) -> dict[str, Any]:
    function_lock = _load_json(root / "docs/oss-function-units.lock.json")
    upstream_lock = _load_json(root / "docs/oss-upstreams.lock.json")
    source_digest, source_inventory = _source_inventory(root)
    census, function_contracts, dispositions = _criterion_function_units(root, function_lock)
    upstream = _criterion_upstream_pins(root, upstream_lock)
    aggregate = _criterion_aggregate_policy()
    tenant = _criterion_tenant_isolation()
    approvals = _criterion_evidence_and_approvals(root)
    public_boundary = _criterion_public_boundary()

    strict_contracts = Criterion(
        id="strict_typed_contracts",
        passed=census.passed and function_contracts.passed,
        summary=(
            "The complete function census is classified and every executable native "
            "mapping has a strict, fail-closed contract."
        ),
        evidence=tuple(dict.fromkeys((*census.evidence, *function_contracts.evidence))),
        metrics={**census.metrics, **function_contracts.metrics},
    )
    classified_isolation = Criterion(
        id="classified_tenant_isolation",
        passed=bool(
            tenant.metrics.get("classificationComplete")
            and tenant.metrics.get("exceptionsReviewed")
        ),
        summary="Every workspace-owned table is protected or has reviewed controls.",
        evidence=("app/db/rls.py", "tests/db/test_tenant_rls_sqlite.py"),
        metrics={
            key: tenant.metrics[key]
            for key in (
                "workspaceTables",
                "directRlsTables",
                "allRlsTables",
                "reviewedExceptions",
                "classificationComplete",
                "exceptionsReviewed",
            )
        },
    )
    transactional_rls = Criterion(
        id="transactional_rls_role_hardening",
        passed=all(
            bool(tenant.metrics.get(key))
            for key in (
                "transactionLocal",
                "workspaceSwitchRejected",
                "runtimeRoleGuarded",
                "upstreamSessionScopeRecorded",
            )
        ),
        summary=(
            "Workspace context is transaction-local, cannot switch in-session and "
            "runtime roles cannot bypass RLS."
        ),
        evidence=(
            "app/db/tenant.py",
            "app/db/rls.py",
            ".vendor/upstreams/bottlecrm/backend/common/middleware/rls_context.py",
            "tests/db/test_legal_crm_rls_postgresql.py",
        ),
        metrics={
            key: tenant.metrics[key]
            for key in (
                "transactionLocal",
                "workspaceSwitchRejected",
                "runtimeRoleGuarded",
                "upstreamSessionScopeRecorded",
            )
        },
    )
    concurrency = Criterion(
        id="optimistic_and_locked_concurrency",
        passed=aggregate.passed and bool(approvals.metrics.get("linearized")),
        summary=(
            "Native aggregates reject stale versions and approvals use database locks."
        ),
        evidence=tuple(dict.fromkeys((*aggregate.evidence, "app/services/approval_service.py"))),
        metrics={**aggregate.metrics, "approvalLinearized": approvals.metrics["linearized"]},
    )
    intent_idempotency = Criterion(
        id="intent_idempotency",
        passed=all(
            bool(approvals.metrics.get(key))
            for key in (
                "singlePendingIntentConstraint",
                "intentIdempotency",
                "blankIntentRejected",
            )
        ),
        summary=(
            "Approval intent keys normalize, replay equivalent payloads and reject "
            "divergent or blank intent."
        ),
        evidence=(
            "app/models/approval.py",
            "app/schemas/approval.py",
            "app/services/approval_service.py",
            "alembic/versions/f6b8d0e2a417_linearize_approval_workflow.py",
        ),
        metrics={
            key: approvals.metrics[key]
            for key in (
                "singlePendingIntentConstraint",
                "intentIdempotency",
                "blankIntentRejected",
            )
        },
    )
    function_runtime_source = (
        root / "app/services/oss_function_unit_runtime_service.py"
    ).read_text(encoding="utf-8")
    exact_veto_runtime = all(
        marker in function_runtime_source
        for marker in (
            "intent_digest",
            "function_unit_approval_type",
            "native_capability_human_approval_consumed",
            "ApprovalQueueItem.approval_type == required_approval_type",
        )
    )
    human_veto = Criterion(
        id="exact_single_use_human_veto",
        passed=all(
            (
                approvals.metrics.get("typedDecision"),
                approvals.metrics.get("linearized"),
                approvals.metrics.get("singlePendingIntentConstraint"),
                approvals.metrics.get("workspaceBound"),
                exact_veto_runtime,
            )
        ),
        summary=(
            "Sensitive native actions bind a typed approval to the intent digest and "
            "consume it exactly once."
        ),
        evidence=(
            "app/services/oss_function_unit_runtime_service.py",
            "app/services/approval_service.py",
            "tests/services/test_oss_function_unit_runtime_service.py",
            "tests/integration/test_approval_concurrency_postgresql.py",
        ),
        metrics={
            "typedDecision": approvals.metrics["typedDecision"],
            "approvalLinearized": approvals.metrics["linearized"],
            "singlePendingIntent": approvals.metrics["singlePendingIntentConstraint"],
            "workspaceBound": approvals.metrics["workspaceBound"],
            "exactIntentBoundRuntime": exact_veto_runtime,
        },
    )
    immutable_evidence = Criterion(
        id="immutable_evidence",
        passed=bool(approvals.metrics.get("immutableEvidence")),
        summary="Legal versions and evidence ledgers reject update, delete and truncate.",
        evidence=(
            "app/db/append_only.py",
            "alembic/versions/d4f6a8c0e215_enforce_tenant_evidence_integrity.py",
            "tests/db/test_append_only_evidence.py",
        ),
        metrics={"databaseAndOrmImmutable": approvals.metrics["immutableEvidence"]},
    )
    post_commit_source = (root / "app/db/post_commit.py").read_text(encoding="utf-8")
    bounded_post_commit = all(
        marker in post_commit_source
        for marker in (
            "_ADVISORY_DISPATCH_TIMEOUT_SECONDS",
            "_BROKER_WAKEUP_TIMEOUT_SECONDS",
            "asyncio.wait_for",
            "Wake durable intent first",
        )
    )
    durable_effects = Criterion(
        id="durable_post_commit_effects",
        passed=all(
            (
                approvals.metrics.get("postCommitRealtime"),
                approvals.metrics.get("durableAgentRun"),
                bounded_post_commit,
            )
        ),
        summary=(
            "Authoritative state commits first; durable wakeups precede bounded advisory "
            "realtime dispatch."
        ),
        evidence=(
            "app/db/post_commit.py",
            "app/core/redis_client.py",
            "app/services/oss_native_outbox_service.py",
            "tests/services/test_post_commit_realtime.py",
        ),
        metrics={
            "postCommitRealtime": approvals.metrics["postCommitRealtime"],
            "durableAgentRun": approvals.metrics["durableAgentRun"],
            "boundedDispatch": bounded_post_commit,
        },
    )
    source_bound = _criterion_source_bound_release(
        root,
        source_digest=source_digest,
        source_file_count=len(source_inventory),
        upstream=upstream,
    )
    criteria = (
        public_boundary,
        strict_contracts,
        classified_isolation,
        transactional_rls,
        _criterion_tenant_reference_integrity(),
        concurrency,
        intent_idempotency,
        human_veto,
        immutable_evidence,
        durable_effects,
        _criterion_external_dependency_gates(root, function_lock),
        source_bound,
    )
    criterion_ids = tuple(criterion.id for criterion in criteria)
    if criterion_ids != REQUIRED_CRITERION_IDS:
        raise ValueError(f"Native architecture criteria changed: {criterion_ids!r}")
    architecture_passed = all(criterion.passed for criterion in criteria)
    structural_preflight_passed = all(
        criterion.passed
        for criterion in criteria
        if criterion.id != "source_bound_release_verification"
    )
    licensed = sum(
        count for state, count in dispositions.items() if state != "blocked_license"
    )
    behavioral_claim_allowed = (
        architecture_passed
        and licensed > 0
        and dispositions.get("native_shadow", 0) == 0
        and dispositions.get("native_primary", 0) == 0
        and dispositions.get("native_verified", 0) == licensed
    )
    report = {
        "schemaVersion": 2,
        "contract": "docs/KLM_NATIVE_ARCHITECTURE_SUPERIORITY_CONTRACT.md",
        "comparisonScope": "architecture_controls_on_pinned_community_sources",
        "sourceBinding": {
            "sha256": source_digest,
            "fileCount": len(source_inventory),
            "files": source_inventory,
        },
        "functionMaturity": {
            "licensed": licensed,
            **dispositions,
        },
        "criteria": [
            {**asdict(criterion), "evidence": list(criterion.evidence)}
            for criterion in criteria
        ],
        "architectureGatePassed": architecture_passed,
        "structuralPreflightPassed": structural_preflight_passed,
        "globalBehavioralSuperiorityClaimAllowed": behavioral_claim_allowed,
        "releaseProofBinding": {
            "sourceDigest": source_digest,
            "sourceFileCount": len(source_inventory),
            "current": source_bound.passed,
            "requiredSuites": list(REQUIRED_RELEASE_SUITES),
        },
        "claimBoundary": (
            "All architecture controls and release proofs passed; behavioral "
            "superiority remains function-scoped."
            if architecture_passed
            else (
                "Structural preflight passed; architecture superiority remains "
                "blocked until current release proofs pass."
                if structural_preflight_passed
                else "Architecture superiority claim is blocked by failing structural controls."
            )
        ),
    }
    report["reportSha256"] = hashlib.sha256(_canonical_json(report)).hexdigest()
    return report


def _write_atomic(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(report, handle, ensure_ascii=True, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    arguments = parser.parse_args(argv)
    report = build_report(ROOT)
    rendered = json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if arguments.check:
        try:
            current = arguments.output.read_text(encoding="utf-8")
        except FileNotFoundError:
            print(f"Native architecture scorecard is missing: {arguments.output}", file=sys.stderr)
            return 1
        if current != rendered:
            print("Native architecture scorecard is stale.", file=sys.stderr)
            return 1
    else:
        _write_atomic(arguments.output, report)
    failed = [criterion["id"] for criterion in report["criteria"] if not criterion["passed"]]
    if failed:
        print(f"Native architecture gate failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    print(
        "Native architecture gate passed: "
        f"{len(report['criteria'])} criteria, source {report['sourceBinding']['sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
