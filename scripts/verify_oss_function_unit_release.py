#!/usr/bin/env python3
"""Prove the exact licensed FunctionUnit denominator on the KLM-local runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.native_aggregate_policy import (  # noqa: E402
    NativeAggregateStateError,
    NativeAggregateVersionRequiredError,
    apply_native_aggregate_transition,
)
from app.services.native_function_unit_binding_service import (  # noqa: E402
    native_function_unit_binding_summary,
    native_function_unit_bindings,
)
from app.services.oss_capability_registry import (  # noqa: E402
    _CAPABILITIES,
    _verification_file_count,
    _verification_files_digest,
    native_capability_registry,
)
from app.services.oss_function_unit_runtime_service import (  # noqa: E402
    build_function_unit_contract,
    function_unit_runtime_contracts,
    function_unit_runtime_summary,
)
from app.services.oss_native_local_adapter import (  # noqa: E402
    local_capability_contracts,
    local_contract_summary,
)

LOCK_PATH = ROOT / "docs" / "oss-function-units.lock.json"
MANIFEST_PATH = (
    ROOT / ".local" / "qa-evidence" / "oss" / "oss_function_unit_verification.json"
)
PROOF_DIR = ROOT / "app" / "data" / "oss_function_unit_proofs"
RUNTIME_SERVICE = (
    "app.services.oss_function_unit_runtime_service.OSSFunctionUnitRuntimeService"
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _identity_rows(units: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    return sorted((unit["id"], unit["upstream"], unit["contractKey"]) for unit in units)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _atomic_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, path)


def _run(
    proof_id: str,
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    process = subprocess.run(
        command,
        cwd=cwd,
        env={**os.environ, **(env or {})},
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    output = process.stdout + process.stderr
    print(output, end="", flush=True)
    if process.returncode != 0:
        raise RuntimeError(
            f"Prova {proof_id} falhou ({process.returncode}): {' '.join(command)}"
        )
    output_path = PROOF_DIR / f"{proof_id}.log"
    _atomic_text(output_path, output)
    return {
        "id": proof_id,
        "status": "passed",
        "command": command,
        "cwd": str(cwd.relative_to(ROOT)),
        "durationMs": round((time.monotonic() - started) * 1000),
        "outputPath": str(output_path.relative_to(ROOT)),
        "outputSha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
    }


def _catalog_contract(lock_path: Path = LOCK_PATH) -> dict[str, Any]:
    document = json.loads(lock_path.read_text(encoding="utf-8"))
    units = document.get("units")
    if not isinstance(units, list) or len(units) != 3845:
        raise RuntimeError("O ledger FunctionUnit não contém o denominador fechado de 3.845.")
    licensed = [unit for unit in units if unit["license"]["status"] == "verified-file"]
    blocked = [unit for unit in units if unit["disposition"]["state"] == "blocked_license"]
    if len(licensed) != 3768 or len(blocked) != 77:
        raise RuntimeError("O denominador licenciado/bloqueado divergiu de 3.768/77.")

    capability_contracts = local_capability_contracts()
    registry_ids = {item["id"] for item in _CAPABILITIES}
    if set(capability_contracts) != registry_ids:
        raise RuntimeError("O catálogo local não cobre exatamente as 135 capacidades.")
    for unit in licensed:
        mapping = unit.get("klmMapping") or {}
        capability_id = mapping.get("capabilityId")
        if capability_id not in capability_contracts:
            raise RuntimeError(f"FunctionUnit sem capacidade local: {unit['id']}.")
        if not capability_contracts[capability_id]["actionIds"]:
            raise RuntimeError(f"Capacidade sem ação local: {capability_id}.")
        if mapping.get("applicationService") != RUNTIME_SERVICE:
            raise RuntimeError(
                f"FunctionUnit não aponta para o runtime exato: {unit['id']}."
            )
        runtime_contract = build_function_unit_contract(unit)
        expected_api_route = (
            f"/api/v1/capabilities/functions/{unit['id']}/runtime"
        )
        exact_fields = {
            "runtimeContractId": runtime_contract.contract_id,
            "runtimeHandler": runtime_contract.handler,
            "semanticProfile": runtime_contract.semantic_profile,
            "proofProfile": runtime_contract.proof_profile,
            "apiRoute": expected_api_route,
        }
        divergent = [
            key for key, value in exact_fields.items() if mapping.get(key) != value
        ]
        if divergent:
            raise RuntimeError(
                f"FunctionUnit sem contrato runtime exato ({', '.join(divergent)}): "
                f"{unit['id']}."
            )

    categories = Counter(unit["category"] for unit in licensed)
    expected_categories = {
        "api",
        "config",
        "export",
        "import",
        "job",
        "mcp",
        "permission",
        "realtime",
        "ui",
        "webhook",
    }
    if set(categories) != expected_categories:
        raise RuntimeError("Os perfis públicos FunctionUnit estão incompletos.")

    identities = _identity_rows(units)
    runtime_rows = sorted(
        (
            unit["id"],
            unit["klmMapping"]["runtimeContractId"],
            unit["klmMapping"]["runtimeHandler"],
            unit["klmMapping"]["semanticProfile"],
            unit["klmMapping"]["proofProfile"],
        )
        for unit in licensed
    )
    return {
        "document": document,
        "licensed": licensed,
        "blocked": blocked,
        "unitIdentityDigest": _digest(identities),
        "licensedUnitIdsDigest": _digest(sorted(unit["id"] for unit in licensed)),
        "blockedUnitIdsDigest": _digest(sorted(unit["id"] for unit in blocked)),
        "capabilityDigest": _digest(sorted((item["id"], item["key"]) for item in _CAPABILITIES)),
        "capabilityContractsDigest": _digest(capability_contracts),
        "categories": dict(sorted(categories.items())),
        "runtimeContractsDigest": _digest(runtime_rows),
        "runtimeContractIdsDigest": _digest(
            sorted((row[0], row[1]) for row in runtime_rows)
        ),
        "runtimeProfiles": dict(
            sorted(Counter(row[3] for row in runtime_rows).items())
        ),
    }


def _proof_reference(proof: dict[str, Any]) -> dict[str, Any]:
    path = ROOT / proof["outputPath"]
    return {
        **proof,
        "artifactSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _native_binding_contract(catalog: dict[str, Any]) -> dict[str, Any]:
    """Return exact, per-unit maturity evidence without global promotion."""
    bindings = native_function_unit_bindings()
    licensed_ids = {unit["id"] for unit in catalog["licensed"]}
    if set(bindings) != licensed_ids:
        raise RuntimeError(
            "Os bindings nativos não cobrem exatamente as FunctionUnits licenciadas."
        )

    summary = native_function_unit_binding_summary()
    if summary["bindings"] != len(licensed_ids):
        raise RuntimeError("O resumo de bindings nativos divergiu do denominador licenciado.")

    primary_ids: list[str] = []
    verified_bindings: list[dict[str, str]] = []
    for unit_id, binding in sorted(bindings.items()):
        if binding.maturity == "native_primary":
            primary_ids.append(unit_id)
        elif binding.maturity == "native_verified":
            proof = binding.proof
            oracle_digest = proof.get("upstreamOracleDigest") if proof else None
            if (
                not isinstance(proof, dict)
                or proof.get("status") != "passed"
                or proof.get("bindingId") != binding.binding_id
                or not isinstance(oracle_digest, str)
                or _SHA256_RE.fullmatch(oracle_digest) is None
            ):
                raise RuntimeError(
                    f"FunctionUnit native_verified sem oracle upstream individual: {unit_id}."
                )
            verified_bindings.append(
                {
                    "unitId": unit_id,
                    "bindingId": binding.binding_id,
                    "upstreamOracleDigest": oracle_digest,
                }
            )
        elif binding.maturity not in {"mapped", "native_shadow"}:
            raise RuntimeError(f"Maturidade FunctionUnit desconhecida: {binding.maturity}.")

    maturity = summary["maturity"]
    if maturity["native_primary"] != len(primary_ids) or (
        maturity["native_verified"] != len(verified_bindings)
    ):
        raise RuntimeError("O resumo de maturidade nativa divergiu dos bindings individuais.")
    return {
        "mappedUnitIds": sorted(licensed_ids),
        "nativePrimaryUnitIds": primary_ids,
        "nativeVerifiedBindings": verified_bindings,
        "bindingIdsDigest": summary["bindingIdsDigest"],
        "maturity": maturity,
    }


def _architecture_quality_contract(catalog: dict[str, Any]) -> dict[str, Any]:
    contracts = function_unit_runtime_contracts()
    bindings = native_function_unit_bindings()
    units_by_id = {unit["id"]: unit for unit in catalog["licensed"]}
    if set(contracts) != {unit["id"] for unit in catalog["licensed"]}:
        raise RuntimeError("O gate arquitetural não cobre o denominador licenciado.")
    if set(bindings) != set(contracts):
        raise RuntimeError("O gate arquitetural não cobre todos os bindings nativos.")

    read_groups = {
        "authorization.evaluate",
        "boundary.inspect",
        "data.export.generate",
        "entity.query",
        "mcp.query",
        "realtime.query",
        "realtime.subscribe",
        "ui.navigate",
        "webhook.inspect",
    }
    event_groups = {
        "boundary.invoke",
        "data.import.stage",
        "entity.archive",
        "entity.upsert",
        "job.enqueue",
        "mcp.invoke",
        "realtime.publish",
        "webhook.ingest",
    }
    violations: list[str] = []
    outbox_units = 0

    created_probe = apply_native_aggregate_transition(
        effect="upsert",
        operation_id="create_probe",
        patch={"nested": {"left": 1, "right": 2}},
        current_state=None,
        current_data=None,
        current_version=0,
        expected_version=0,
    )
    try:
        apply_native_aggregate_transition(
            effect="upsert",
            operation_id="update_probe",
            patch={"nested": {"right": 3}},
            current_state=created_probe.state,
            current_data=created_probe.data,
            current_version=created_probe.version,
            expected_version=None,
        )
    except NativeAggregateVersionRequiredError:
        optimistic_concurrency_required = True
    else:
        optimistic_concurrency_required = False
        violations.append("aggregate-optimistic-concurrency")
    merged_probe = apply_native_aggregate_transition(
        effect="upsert",
        operation_id="update_probe",
        patch={"nested": {"right": 3}},
        current_state=created_probe.state,
        current_data=created_probe.data,
        current_version=created_probe.version,
        expected_version=created_probe.version,
    )
    deep_merge_verified = merged_probe.data == {
        "nested": {"left": 1, "right": 3}
    }
    if not deep_merge_verified:
        violations.append("aggregate-deep-merge")
    archived_probe = apply_native_aggregate_transition(
        effect="archive",
        operation_id="archive_probe",
        patch={},
        current_state=merged_probe.state,
        current_data=merged_probe.data,
        current_version=merged_probe.version,
        expected_version=merged_probe.version,
    )
    try:
        apply_native_aggregate_transition(
            effect="upsert",
            operation_id="update_probe",
            patch={"nested": {"right": 4}},
            current_state=archived_probe.state,
            current_data=archived_probe.data,
            current_version=archived_probe.version,
            expected_version=archived_probe.version,
        )
    except NativeAggregateStateError:
        archive_state_machine_verified = True
    else:
        archive_state_machine_verified = False
        violations.append("aggregate-archive-state-machine")
    for unit_id, contract in contracts.items():
        binding = bindings[unit_id]
        assertions = set(contract.as_dict()["assertions"])
        expected_route = f"/api/v1/capabilities/functions/{unit_id}/runtime"
        if contract.as_dict()["destination"]["apiRoute"] != expected_route:
            violations.append(f"route:{unit_id}")
        if contract.authorization_policy.get("workspaceScoped") is not True:
            violations.append(f"workspace:{unit_id}")
        if contract.effects.get("externalNetworkAllowed") is not False:
            violations.append(f"network:{unit_id}")
        if (
            contract.output_schema.get("properties", {})
            .get("externalNetworkExecuted", {})
            .get("const")
            is not False
        ):
            violations.append(f"output-network:{unit_id}")
        if contract.state_transition.get("journalIsPrimary") is not False:
            violations.append(f"journal:{unit_id}")
        if contract.state_transition.get("effect") != contract.local_effect:
            violations.append(f"effect:{unit_id}")
        if contract.effects.get("localState") is not contract.mutating:
            violations.append(f"mutation:{unit_id}")
        if contract.maturity != binding.maturity:
            violations.append(f"maturity:{unit_id}")
        if binding.maturity == "native_primary" and (
            binding.capability_action_id is None
            or binding.mapping_strategy not in {"exact_operation", "lexical_action"}
        ):
            violations.append(f"primary-semantic-binding:{unit_id}")
        if binding.maturity == "native_shadow" and contract.proof_profile != (
            "typed_native_shadow_contract"
        ):
            violations.append(f"shadow-proof-profile:{unit_id}")
        if contract.source_method in {"ANY", "*"}:
            if contract.maturity != "native_shadow":
                violations.append(f"ambiguous-promoted:{unit_id}")
            if units_by_id[unit_id]["category"] == "api" and (
                contract.semantic_profile != "http.ambiguous"
                or contract.runtime_mode != "read_only"
            ):
                violations.append(f"ambiguous-api-boundary:{unit_id}")
        if contract.authorization_policy.get("authority") not in {
            "workspace_operator",
            "workspace_admin",
        }:
            violations.append(f"authority:{unit_id}")
        if contract.authorization_policy.get("humanVeto") is True and not str(
            contract.authorization_policy.get("approvalTypePrefix") or ""
        ).startswith("function_unit:"):
            violations.append(f"human-veto:{unit_id}")
        if contract.authorization_policy.get("humanVeto") is True and (
            contract.authorization_policy.get("singleUse") is not True
            or contract.authorization_policy.get("intentBound") is not True
            or "human_approval_single_use_intent_bound" not in assertions
        ):
            violations.append(f"weak-human-veto:{unit_id}")
        if contract.runtime_mode == "read_only":
            if contract.mutating or contract.equivalence_group not in read_groups:
                violations.append(f"read:{unit_id}")
        elif contract.runtime_mode == "event_sourced":
            if not contract.mutating or contract.equivalence_group not in event_groups:
                violations.append(f"event:{unit_id}")
        elif contract.equivalence_group != "configuration.apply":
            violations.append(f"configuration:{unit_id}")
        if contract.mutating and "idempotency_enforced" not in assertions:
            violations.append(f"idempotency:{unit_id}")
        if contract.effects.get("outbox"):
            outbox_units += 1
            if "transactional_outbox_enqueued" not in assertions:
                violations.append(f"outbox:{unit_id}")

    public_registry = json.dumps(
        native_capability_registry(),
        ensure_ascii=True,
        sort_keys=True,
    ).casefold()
    vocabulary_leaks = sum(
        public_registry.count(value)
        for value in ("chatwoot", "rocketchat", "rocket.chat", "chattermate", "bottlecrm")
    )
    if vocabulary_leaks:
        violations.append("public-product-vocabulary")
    if violations:
        raise RuntimeError(
            "Gate arquitetural nativo falhou: " + ", ".join(violations[:20])
        )

    mcp_contracts = [
        contract for contract in contracts.values() if contract.unit_id.startswith("bottlecrm:mcp:")
    ]
    return {
        "qualityGateVersion": 2,
        "unitsChecked": len(contracts),
        "violations": 0,
        "nativePrimaryUnits": sum(
            contract.maturity == "native_primary" for contract in contracts.values()
        ),
        "nativeShadowUnits": sum(
            contract.maturity == "native_shadow" for contract in contracts.values()
        ),
        "ambiguousBoundaries": sum(
            contract.source_method in {"ANY", "*"} for contract in contracts.values()
        ),
        "ambiguousReadOnlyContracts": sum(
            contract.source_method in {"ANY", "*"} and not contract.mutating
            for contract in contracts.values()
        ),
        "ambiguousShadowMutationDescriptors": sum(
            contract.source_method in {"ANY", "*"} and contract.mutating
            for contract in contracts.values()
        ),
        "ambiguousMutationExecutionAllowed": 0,
        "workspaceAdminUnits": sum(
            contract.authorization_policy.get("authority") == "workspace_admin"
            for contract in contracts.values()
        ),
        "humanVetoUnits": sum(
            contract.authorization_policy.get("humanVeto") is True
            for contract in contracts.values()
        ),
        "aggregatePolicy": {
            "schemaVersion": 1,
            "optimisticConcurrencyRequired": optimistic_concurrency_required,
            "deepMergeVerified": deep_merge_verified,
            "archiveStateMachineVerified": archive_state_machine_verified,
            "canonicalDigestVerified": len(merged_probe.data_sha256) == 64,
        },
        "readOnlyUnits": sum(
            contract.runtime_mode == "read_only" for contract in contracts.values()
        ),
        "mutatingUnits": sum(contract.mutating for contract in contracts.values()),
        "transactionalOutboxUnits": outbox_units,
        "mcpReadUnits": sum(
            contract.runtime_mode == "read_only" for contract in mcp_contracts
        ),
        "mcpMutatingUnits": sum(contract.mutating for contract in mcp_contracts),
        "publicProductVocabularyLeaks": vocabulary_leaks,
        "externalNetworkAllowances": 0,
        "journalPrimaryUnits": 0,
    }


def _validate_manifest(manifest: dict[str, Any], catalog: dict[str, Any]) -> None:
    bindings = _native_binding_contract(catalog)
    architecture_quality = _architecture_quality_contract(catalog)
    source_digest = _verification_files_digest(refresh=True)
    required = {
        "schemaVersion": 3,
        "state": "passed",
        "sourceDigest": source_digest,
        "sourceFileCount": _verification_file_count(),
        "unitIdentityDigest": catalog["unitIdentityDigest"],
        "licensedUnitIdsDigest": catalog["licensedUnitIdsDigest"],
        "blockedUnitIdsDigest": catalog["blockedUnitIdsDigest"],
        "capabilityDigest": catalog["capabilityDigest"],
        "capabilityContractsDigest": catalog["capabilityContractsDigest"],
        "runtimeContractsDigest": catalog["runtimeContractsDigest"],
        "runtimeContractIdsDigest": catalog["runtimeContractIdsDigest"],
        "mappedLicensedUnits": 3768,
        "runtimeContractsMapped": 3768,
        "nativePrimaryUnits": len(bindings["nativePrimaryUnitIds"]),
        "nativeVerifiedUnits": len(bindings["nativeVerifiedBindings"]),
        "bindingIdsDigest": bindings["bindingIdsDigest"],
        "blockedLicenseUnits": 77,
        "capabilitiesVerified": 135,
        "actionContractsVerified": 511,
        "externalEffectsExecuted": 0,
        "architectureQuality": architecture_quality,
    }
    mismatches = [key for key, value in required.items() if manifest.get(key) != value]
    if mismatches:
        raise RuntimeError("Manifesto FunctionUnit divergente: " + ", ".join(mismatches))
    for deprecated in (
        "licensedUnitsVerified",
        "runtimeContractsVerified",
        "verifiedUnitIds",
        "verifiedRuntimeContracts",
    ):
        if deprecated in manifest:
            raise RuntimeError(f"Manifesto contém promoção global obsoleta: {deprecated}.")
    if manifest.get("mappedUnitIds") != bindings["mappedUnitIds"]:
        raise RuntimeError("O manifesto não contém exatamente as FunctionUnits mapeadas.")
    if manifest.get("nativePrimaryUnitIds") != bindings["nativePrimaryUnitIds"]:
        raise RuntimeError("O manifesto não contém exatamente as FunctionUnits native_primary.")
    if manifest.get("nativeVerifiedBindings") != bindings["nativeVerifiedBindings"]:
        raise RuntimeError("O manifesto não contém a prova oracle por FunctionUnit verificada.")
    expected_contracts = sorted(
        [
            {
                "unitId": unit["id"],
                "contractId": unit["klmMapping"]["runtimeContractId"],
                "handler": unit["klmMapping"]["runtimeHandler"],
                "semanticProfile": unit["klmMapping"]["semanticProfile"],
                "proofProfile": unit["klmMapping"]["proofProfile"],
            }
            for unit in catalog["licensed"]
        ],
        key=lambda row: row["unitId"],
    )
    if manifest.get("mappedRuntimeContracts") != expected_contracts:
        raise RuntimeError(
            "O manifesto não contém os 3.768 contratos runtime mapeados exatos."
        )
    if manifest.get("semanticProfileCoverage") != catalog["runtimeProfiles"]:
        raise RuntimeError("A cobertura de perfis semânticos do manifesto divergiu.")
    sidecar = manifest.get("sidecarOff")
    if not isinstance(sidecar, dict) or any(
        (
            sidecar.get("primaryRequired") is not False,
            sidecar.get("sourceAdaptersConstructed") != 0,
            sidecar.get("proofPassed") is not True,
        )
    ):
        raise RuntimeError("A prova sidecar-off do manifesto é inválida.")
    proofs = manifest.get("proofs")
    if not isinstance(proofs, list) or {proof.get("id") for proof in proofs} != {
        "backend-contracts",
        "frontend-typecheck",
        "frontend-native-ui",
        "python-quality",
    }:
        raise RuntimeError("O manifesto não referencia as quatro provas obrigatórias.")
    for proof in proofs:
        path = ROOT / str(proof.get("outputPath") or "")
        if (
            proof.get("status") != "passed"
            or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest()
            != proof.get("artifactSha256")
        ):
            raise RuntimeError(f"Artefato de prova inválido: {proof.get('id')}.")


def _generate(*, output_path: Path = MANIFEST_PATH) -> dict[str, Any]:
    catalog = _catalog_contract()
    bindings = _native_binding_contract(catalog)
    runtime_summary = function_unit_runtime_summary()
    if runtime_summary["contracts"] != len(catalog["licensed"]):
        raise RuntimeError("O runtime não resolveu exatamente as 3.768 FunctionUnits.")
    if runtime_summary["contractIdsDigest"] != catalog["runtimeContractIdsDigest"]:
        raise RuntimeError("O digest do runtime divergiu do censo comprometido.")
    source_digest = _verification_files_digest(refresh=True)
    proofs = [
        _run(
            "python-quality",
            [
                str(ROOT / ".venv" / "bin" / "ruff"),
                "check",
                "app/services/oss_native_local_adapter.py",
                "app/services/native_aggregate_policy.py",
                "app/services/oss_native_document_service.py",
                "app/services/oss_function_unit_service.py",
                "app/services/oss_function_unit_runtime_service.py",
                "app/services/native_function_unit_classification.py",
                "app/services/oss_native_outbox_service.py",
                "app/workers/native_outbox_tasks.py",
                "app/db/advisory.py",
                "app/models/oss_function_unit.py",
                "app/api/v1/endpoints/oss_function_units.py",
                "app/services/native_function_unit_binding_service.py",
                "scripts/build_oss_function_unit_dispositions.py",
                "scripts/verify_oss_function_unit_release.py",
                "tests/services/test_oss_native_local_primary_adapter.py",
                "tests/services/test_native_aggregate_policy.py",
                "tests/services/test_oss_function_unit_runtime_service.py",
                "tests/services/test_native_function_unit_bindings.py",
                "tests/services/test_oss_native_outbox_service.py",
                "tests/services/test_native_advisory_lock.py",
                "tests/workers/test_native_outbox_tasks.py",
                "tests/oss/test_oss_function_unit_registry.py",
                "tests/api/test_oss_function_units.py",
                "tests/scripts/test_oss_function_unit_release.py",
            ],
            cwd=ROOT,
        ),
        _run(
            "backend-contracts",
            [
                str(ROOT / ".venv" / "bin" / "pytest"),
                "-q",
                "tests/services/test_oss_native_local_primary_adapter.py",
                "tests/services/test_native_aggregate_policy.py",
                "tests/services/test_oss_function_unit_runtime_service.py",
                "tests/services/test_native_function_unit_bindings.py",
                "tests/services/test_oss_native_outbox_service.py",
                "tests/services/test_native_advisory_lock.py",
                "tests/workers/test_native_outbox_tasks.py",
                "tests/services/test_oss_native_document_matrix.py",
                "tests/services/test_oss_capability_registry.py",
                "tests/oss/test_oss_function_unit_registry.py",
                "tests/api/test_oss_function_units.py",
                "tests/api/test_native_route_convergence.py",
                "tests/scripts/test_oss_function_unit_release.py",
            ],
            cwd=ROOT,
        ),
        _run(
            "frontend-typecheck",
            ["npm", "run", "typecheck"],
            cwd=ROOT / "frontend",
        ),
        _run(
            "frontend-native-ui",
            [
                "npm",
                "exec",
                "--",
                "vitest",
                "run",
                "src/test/function-unit-inventory.test.tsx",
                "src/test/native-capability-workspace.test.tsx",
            ],
            cwd=ROOT / "frontend",
            env={"VITE_API_URL": "http://127.0.0.1:8010"},
        ),
    ]
    if _verification_files_digest(refresh=True) != source_digest:
        raise RuntimeError("O código-fonte mudou durante a prova FunctionUnit.")

    contract_summary = local_contract_summary()
    architecture_quality = _architecture_quality_contract(catalog)
    manifest = {
        "schemaVersion": 3,
        "state": "passed",
        "generatedAt": datetime.now(UTC).isoformat(),
        "sourceDigest": source_digest,
        "sourceFileCount": _verification_file_count(),
        "unitIdentityDigest": catalog["unitIdentityDigest"],
        "licensedUnitIdsDigest": catalog["licensedUnitIdsDigest"],
        "blockedUnitIdsDigest": catalog["blockedUnitIdsDigest"],
        "capabilityDigest": catalog["capabilityDigest"],
        "capabilityContractsDigest": catalog["capabilityContractsDigest"],
        "runtimeContractsDigest": catalog["runtimeContractsDigest"],
        "runtimeContractIdsDigest": catalog["runtimeContractIdsDigest"],
        "mappedLicensedUnits": len(catalog["licensed"]),
        "runtimeContractsMapped": len(catalog["licensed"]),
        "nativePrimaryUnits": len(bindings["nativePrimaryUnitIds"]),
        "nativeVerifiedUnits": len(bindings["nativeVerifiedBindings"]),
        "bindingIdsDigest": bindings["bindingIdsDigest"],
        "blockedLicenseUnits": len(catalog["blocked"]),
        "capabilitiesVerified": contract_summary["capabilities"],
        "actionContractsVerified": contract_summary["actions"],
        "categoryCoverage": catalog["categories"],
        "mappedUnitIds": bindings["mappedUnitIds"],
        "nativePrimaryUnitIds": bindings["nativePrimaryUnitIds"],
        "nativeVerifiedBindings": bindings["nativeVerifiedBindings"],
        "mappedRuntimeContracts": sorted(
            (
                {
                    "unitId": unit["id"],
                    "contractId": unit["klmMapping"]["runtimeContractId"],
                    "handler": unit["klmMapping"]["runtimeHandler"],
                    "semanticProfile": unit["klmMapping"]["semanticProfile"],
                    "proofProfile": unit["klmMapping"]["proofProfile"],
                }
                for unit in catalog["licensed"]
            ),
            key=lambda row: row["unitId"],
        ),
        "semanticProfileCoverage": catalog["runtimeProfiles"],
        "sidecarOff": {
            "primaryRequired": False,
            "sourceAdaptersConstructed": 0,
            "proofPassed": True,
            "eventLog": "oss_native_commands",
            "functionUnitEventLog": "oss_function_unit_events",
            "restartReplay": True,
            "test": "tests/services/test_oss_function_unit_runtime_service.py",
        },
        "externalEffectsExecuted": 0,
        "architectureQuality": architecture_quality,
        "externalEffectPolicy": "configuration_and_dry_run_only_until_explicit_provider_gate",
        "proofs": [_proof_reference(proof) for proof in proofs],
    }
    _validate_manifest(manifest, catalog)
    _atomic_json(output_path, manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=MANIFEST_PATH)
    arguments = parser.parse_args(argv)
    catalog = _catalog_contract()
    if arguments.check:
        # The aggregate manifest is QA evidence, not repository source. Rebuild
        # it from the authoritative lock, contracts and proof commands so a
        # clean checkout never depends on a committed generated snapshot.
        manifest = _generate(output_path=arguments.output)
        _validate_manifest(manifest, catalog)
        print("FunctionUnit release manifest verified.")
        return 0
    manifest = _generate(output_path=arguments.output)
    print(
        "FunctionUnit release verified: "
        f"{manifest['mappedLicensedUnits']} mapped units, "
        f"{manifest['nativePrimaryUnits']} native-primary units, "
        f"{manifest['nativeVerifiedUnits']} native-verified units, "
        f"{manifest['capabilitiesVerified']} capabilities, "
        f"{manifest['actionContractsVerified']} actions, sidecars optional."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error
