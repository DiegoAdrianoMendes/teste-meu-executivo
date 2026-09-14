"""Static validation of the pre-existing 135-family KLM OSS baseline."""

from __future__ import annotations

import ast
import json
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.oss_census.common import ExtractionError, sha256_bytes, sha256_text
from scripts.oss_census.python_ast import parse_python

EXPECTED_FAMILY_COUNT = 135
EXPECTED_PROVIDER_COUNTS = {
    "chatwoot": 39,
    "rocketchat": 33,
    "chattermate": 27,
    "bottlecrm": 36,
}
_NATIVE_DOMAIN_SLUGS = {
    "chatwoot": "communications",
    "rocketchat": "collaboration",
    "chattermate": "automation",
    "bottlecrm": "crm",
}
_CAPABILITY_POSITIONAL_FIELDS = (
    "provider",
    "key",
    "label",
    "category",
    "description",
    "disposition",
    "currentExposure",
)
_COMMON_RUNTIME_EVIDENCE = (
    "app/services/oss_native_state_service.py",
    "frontend/src/klm/pages/NativeCapabilityPage.tsx",
    "frontend/src/klm/components/NativeCapabilityWorkspace.tsx",
)
_COMMON_TEST_EVIDENCE = (
    "tests/api/test_oss_native.py",
    "tests/services/test_oss_native_document_matrix.py",
    "frontend/src/test/native-capability-workspace.test.tsx",
    "frontend/e2e/native-capabilities.spec.ts",
)
_IGNORED_TREE_PARTS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "coverage",
    "dist",
    "node_modules",
    "playwright-report",
    "test-results",
}


def _literal(node: ast.AST, context: str) -> Any:
    try:
        return ast.literal_eval(node)
    except (TypeError, ValueError) as exc:
        raise ExtractionError(f"Non-literal 135-family registry value at {context}") from exc


def _assigned_literal(tree: ast.Module, name: str) -> Any:
    for node in tree.body:
        target: ast.AST | None = None
        value: ast.AST | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            value = node.value
        if isinstance(target, ast.Name) and target.id == name and value is not None:
            return _literal(value, name)
    raise ExtractionError(f"Missing {name} in 135-family registry")


def _capabilities(tree: ast.Module) -> list[dict[str, Any]]:
    assignment: ast.AnnAssign | ast.Assign | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            targets = []
        if any(isinstance(target, ast.Name) and target.id == "_CAPABILITIES" for target in targets):
            assignment = node
            break
    if assignment is None or not isinstance(assignment.value, ast.Tuple | ast.List):
        raise ExtractionError("_CAPABILITIES must be a literal sequence of _capability calls")

    capabilities: list[dict[str, Any]] = []
    for index, item in enumerate(assignment.value.elts):
        if (
            not isinstance(item, ast.Call)
            or not isinstance(item.func, ast.Name)
            or item.func.id != "_capability"
        ):
            raise ExtractionError(f"Invalid capability declaration at index {index}")
        if len(item.args) != len(_CAPABILITY_POSITIONAL_FIELDS):
            raise ExtractionError(f"Capability {index} has an unexpected positional signature")
        values = {
            field: _literal(value, f"_CAPABILITIES[{index}].{field}")
            for field, value in zip(_CAPABILITY_POSITIONAL_FIELDS, item.args, strict=True)
        }
        for keyword in item.keywords:
            if keyword.arg is None:
                raise ExtractionError(f"Capability {index} may not use **kwargs")
            values[keyword.arg] = _literal(
                keyword.value, f"_CAPABILITIES[{index}].{keyword.arg}"
            )
        values.setdefault("klm_evidence", ())
        values.setdefault("test_evidence", ())
        values["id"] = f"{values['provider']}.{values['key']}"
        capabilities.append(values)
    return capabilities


def _legacy_native_mode(item: dict[str, Any]) -> str:
    disposition = item["disposition"]
    exposure = item["currentExposure"]
    if disposition in {"blocked_external_gate", "blocked_dependency"}:
        return "gated_configuration"
    if disposition == "blocked_license":
        return "klm_equivalent"
    if disposition == "not_applicable":
        return "responsive_substitution"
    if exposure in {"bridge", "microfrontend"}:
        return "sidecar_round_trip"
    if exposure == "full_app":
        return "sidecar_command"
    if exposure == "native":
        return "klm_canonical"
    return "klm_equivalent"


def _native_mode(item: dict[str, Any]) -> str:
    legacy_mode = _legacy_native_mode(item)
    if legacy_mode in {
        "gated_configuration",
        "klm_equivalent",
        "responsive_substitution",
    }:
        return legacy_mode
    return "klm_canonical"


def _stable_digest(value: Any) -> str:
    return sha256_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "utf-8"
        )
    )


def _path_digest(repo_root: Path, relative: str) -> dict[str, Any]:
    path = repo_root / relative
    if not path.exists():
        raise ExtractionError(f"Missing 135-family evidence path: {relative}")
    if path.is_symlink():
        raise ExtractionError(f"135-family evidence may not be a symlink: {relative}")
    if path.is_file():
        return {
            "path": relative,
            "kind": "file",
            "fileCount": 1,
            "sha256": sha256_bytes(path.read_bytes()),
        }
    files = sorted(
        candidate
        for candidate in path.rglob("*")
        if candidate.is_file()
        and not candidate.is_symlink()
        and not any(part in _IGNORED_TREE_PARTS for part in candidate.relative_to(path).parts)
    )
    manifest = "\n".join(
        f"{candidate.relative_to(repo_root).as_posix()}\0{sha256_bytes(candidate.read_bytes())}"
        for candidate in files
    )
    return {
        "path": relative,
        "kind": "directory",
        "fileCount": len(files),
        "sha256": sha256_text(manifest),
    }


def _evidence_receipt(repo_root: Path, paths: set[str]) -> dict[str, Any]:
    records = [_path_digest(repo_root, path) for path in sorted(paths)]
    manifest = "\n".join(
        f"{record['path']}\0{record['kind']}\0{record['fileCount']}\0{record['sha256']}"
        for record in records
    )
    return {
        "status": "paths_and_bytes_verified",
        "pathCount": len(records),
        "fileCount": sum(record["fileCount"] for record in records),
        "manifestSha256": sha256_text(manifest),
        "paths": records,
    }


def validate_family_baseline(repo_root: Path) -> dict[str, Any]:
    registry_path = repo_root / "app" / "services" / "oss_capability_registry.py"
    registry_raw = registry_path.read_bytes()
    try:
        registry_text = registry_raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ExtractionError(f"135-family registry is not UTF-8: {exc}") from exc
    tree = parse_python(registry_text, "app/services/oss_capability_registry.py")
    capabilities = _capabilities(tree)
    if len(capabilities) != EXPECTED_FAMILY_COUNT:
        raise ExtractionError(
            f"Expected {EXPECTED_FAMILY_COUNT} OSS families, found {len(capabilities)}"
        )
    ids = [item["id"] for item in capabilities]
    if len(ids) != len(set(ids)):
        duplicates = sorted(item for item, count in Counter(ids).items() if count > 1)
        raise ExtractionError(f"Duplicate 135-family IDs: {duplicates[:5]}")
    provider_counts = Counter(item["provider"] for item in capabilities)
    if dict(provider_counts) != EXPECTED_PROVIDER_COUNTS:
        raise ExtractionError(
            f"135-family provider counts changed: expected {EXPECTED_PROVIDER_COUNTS}, "
            f"found {dict(provider_counts)}"
        )

    native_services = _assigned_literal(tree, "_NATIVE_SERVICE_EVIDENCE")
    native_tests = _assigned_literal(tree, "_NATIVE_TEST_EVIDENCE")
    if set(native_services) != set(EXPECTED_PROVIDER_COUNTS) or set(native_tests) != set(
        EXPECTED_PROVIDER_COUNTS
    ):
        raise ExtractionError("135-family provider runtime/test maps are incomplete")

    runtime_paths = set(_COMMON_RUNTIME_EVIDENCE)
    test_paths = set(_COMMON_TEST_EVIDENCE)
    upstream_paths: set[str] = set()
    for item in capabilities:
        upstream_evidence = item.get("upstream_evidence")
        if not isinstance(upstream_evidence, tuple) or not upstream_evidence:
            raise ExtractionError(f"Family lacks upstream evidence: {item['id']}")
        upstream_paths.update(upstream_evidence)
        runtime_paths.update(item["klm_evidence"])
        runtime_paths.add(native_services[item["provider"]])
        test_paths.update(item["test_evidence"])
        test_paths.add(native_tests[item["provider"]])
    missing_upstream = sorted(path for path in upstream_paths if not (repo_root / path).exists())
    if missing_upstream:
        raise ExtractionError(f"Missing 135-family upstream evidence: {missing_upstream[:5]}")

    contract_rows = [
        {
            "id": item["id"],
            "provider": item["provider"],
            "key": item["key"],
            "nativeMode": _native_mode(item),
            "nativeRoute": (
                f"/app/capacidades/{_NATIVE_DOMAIN_SLUGS[item['provider']]}/{item['key']}"
            ),
        }
        for item in capabilities
    ]
    native_ids = sorted(ids)
    contract_ids = sorted(
        row["id"]
        for row in contract_rows
        if row["nativeMode"] not in {"gated_configuration", "responsive_substitution"}
    )

    verification_generator_path = repo_root / "scripts" / "verify_oss_native_release.py"
    return {
        "status": "validated",
        "scope": "pre_existing_klm_family_baseline",
        "familyCount": len(capabilities),
        "expectedFamilyCount": EXPECTED_FAMILY_COUNT,
        "idsSha256": _stable_digest(native_ids),
        "byProvider": {
            provider: provider_counts[provider] for provider in EXPECTED_PROVIDER_COUNTS
        },
        "providerValidation": "exact",
        "registry": {
            "path": registry_path.relative_to(repo_root).as_posix(),
            "sha256": sha256_bytes(registry_raw),
        },
        "upstreamEvidence": {
            "status": "paths_verified_against_materialized_pins",
            "pathCount": len(upstream_paths),
        },
        "runtimeEvidence": _evidence_receipt(repo_root, runtime_paths),
        "testEvidence": _evidence_receipt(repo_root, test_paths),
        "verificationManifest": {
            "path": ".local/qa-evidence/oss-native/oss_native_verification.json",
            "generator": verification_generator_path.relative_to(repo_root).as_posix(),
            "generatorSha256": sha256_bytes(verification_generator_path.read_bytes()),
            "schemaVersion": "1.2",
            "contractBinding": "derived_from_registry",
            "nativeCount": len(native_ids),
            "nativeIdsDigest": _stable_digest(native_ids),
            "contractCount": len(contract_ids),
            "contractIdsDigest": _stable_digest(contract_ids),
            "contractDigest": _stable_digest(contract_rows),
            "actionCount": 511,
            "sourceBindingMode": "generated_local_evidence",
            "recordedWorktreeDirty": None,
            "claimBoundary": (
                "Registry and source evidence are validated by the census. Runtime proof "
                "is generated locally and is not committed as repository source."
            ),
        },
    }
