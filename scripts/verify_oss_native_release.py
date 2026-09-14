#!/usr/bin/env python3
"""Run and bind the OSS-native release proofs to the exact reviewed source tree."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
LOCAL_EVIDENCE_DIR = ROOT / ".local" / "qa-evidence" / "oss-native"
PROOF_DIR = LOCAL_EVIDENCE_DIR / "proofs"
MANIFEST_PATH = LOCAL_EVIDENCE_DIR / "oss_native_verification.json"
POST_FINALIZE_EVIDENCE_PATH = LOCAL_EVIDENCE_DIR / "post-finalize.json"
POST_FINALIZE_OUTPUT_PATH = LOCAL_EVIDENCE_DIR / "post-finalize.log"
SOURCE_INVENTORY_PATH = PROOF_DIR / "source-inventory.json"
SOURCE_SNAPSHOT_PATH = PROOF_DIR / "source-snapshot.tar.gz"
SOURCE_BINDING_MODE = "content_snapshot"
SUITES = (
    "backend",
    "frontend-unit",
    "frontend-e2e",
    "local-runtime",
    "upstream-locks",
)
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
NATIVE_MATRIX_LINE = re.compile(
    r"\[(?:capabilities|oss)-native\]\s+p95=(\d+)ms\s+actions=(\d+)"
)
NATIVE_IDS_LINE = re.compile(
    r"\[(?:capabilities|oss)-native-ids\]\s+(\[[^\n]+\])"
)
PYTEST_PASSED_LINE = re.compile(r"(?<!\d)(\d+)\s+passed(?:\s+in|[,\n])")


def _extract_native_matrix(output: str) -> tuple[int, int, list[str]] | None:
    """Read the native route proof from current or retained legacy reporters."""

    clean = ANSI_ESCAPE.sub("", output)
    matrix_matches = NATIVE_MATRIX_LINE.findall(clean)
    id_matches = NATIVE_IDS_LINE.findall(clean)
    if not matrix_matches or not id_matches:
        return None
    p95_ms, actions = (int(value) for value in matrix_matches[-1])
    native_ids = json.loads(id_matches[-1])
    if not isinstance(native_ids, list) or not all(
        isinstance(value, str) for value in native_ids
    ):
        return None
    return p95_ms, actions, native_ids


def _parse_vitest_test_summary(output: str) -> tuple[int, int, int]:
    """Return passed, skipped and failed counts from the final Vitest summary."""
    clean = ANSI_ESCAPE.sub("", output).replace("\r", "\n")
    summaries = re.findall(r"^\s*Tests\s+(.+?)\s*$", clean, flags=re.MULTILINE)
    if not summaries:
        raise RuntimeError("Não foi possível extrair o resumo final do Vitest.")
    summary = summaries[-1]
    total_match = re.search(r"\((\d+)\)\s*$", summary)
    if total_match is None:
        raise RuntimeError("O resumo do Vitest não informou a contagem total.")
    total = int(total_match.group(1))
    counts = {
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "pending": 0,
        "todo": 0,
    }
    for raw_count, status_name in re.findall(
        r"(\d+)\s+(passed|failed|skipped|pending|todo)\b",
        summary,
    ):
        counts[status_name] += int(raw_count)
    residue = re.sub(
        r"(?:\d+)\s+(?:passed|failed|skipped|pending|todo)\b|\(\d+\)|[|\s]",
        "",
        summary,
    )
    accounted = sum(counts.values())
    if residue or accounted != total:
        raise RuntimeError(
            "O resumo do Vitest contém estados desconhecidos ou contagem inconsistente."
        )
    return (
        counts["passed"],
        counts["skipped"] + counts["pending"] + counts["todo"],
        counts["failed"],
    )


def _registry_contract(*, refresh: bool = False):
    from app.services.oss_capability_registry import (
        _EXPECTED_ACTION_OCCURRENCES,
        _EXPECTED_UNIQUE_COMMANDS,
        _canonical_capability_id,
        _verification_action_ids,
        _verification_contract,
        _verification_file_count,
        _verification_files_digest,
        _verification_ids,
        _verification_source_inventory,
    )

    native_ids = _verification_ids()[0]
    return {
        "contract": _verification_contract(),
        "sourceDigest": _verification_files_digest(refresh=refresh),
        "sourceFileCount": _verification_file_count(),
        "sourceInventory": _verification_source_inventory(),
        "nativeIds": native_ids,
        "publicNativeIds": [
            _canonical_capability_id(value) for value in native_ids
        ],
        "contractIds": _verification_ids()[1],
        "actionIds": _verification_action_ids(),
        "actionOccurrences": _EXPECTED_ACTION_OCCURRENCES,
        "uniqueCommands": _EXPECTED_UNIQUE_COMMANDS,
    }


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> tuple[int, str, int]:
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env={**os.environ, **(env or {})},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    output_parts: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        output_parts.append(line)
        print(line, end="", flush=True)
    exit_code = process.wait()
    duration_ms = round((time.monotonic() - started) * 1000)
    return exit_code, "".join(output_parts), duration_ms


def _proof_base(
    suite: str,
    command: list[str],
    output: str,
    duration_ms: int,
    source_digest: str,
    source_file_count: int,
) -> dict[str, Any]:
    git_head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    worktree_dirty = bool(
        subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True
        ).strip()
    )
    return {
        "proofVersion": "1.0",
        "suite": suite,
        "status": "passed",
        "exitCode": 0,
        "generatedAt": datetime.now(UTC).isoformat(),
        "sourceDigest": source_digest,
        "worktreeDigest": source_digest,
        "sourceFileCount": source_file_count,
        "gitHead": git_head,
        "worktreeDirty": worktree_dirty,
        "sourceBindingMode": SOURCE_BINDING_MODE,
        "command": command,
        "durationMs": duration_ms,
        "outputSha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _write_source_binding_artifacts(
    contract: dict[str, Any],
    *,
    git_head: str,
    worktree_dirty: bool,
) -> dict[str, Any]:
    inventory = contract.get("sourceInventory")
    if not isinstance(inventory, list) or len(inventory) != contract["sourceFileCount"]:
        raise RuntimeError("Inventário inicial do código-fonte está incompleto.")

    tar_buffer = io.BytesIO()
    verified_inventory: list[dict[str, Any]] = []
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.GNU_FORMAT) as archive:
        for entry in inventory:
            if not isinstance(entry, dict):
                raise RuntimeError("Entrada inválida no inventário do código-fonte.")
            relative_path = Path(str(entry.get("path", "")))
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise RuntimeError("Caminho inseguro no inventário do código-fonte.")
            payload = (ROOT / relative_path).read_bytes()
            verified = {
                "path": relative_path.as_posix(),
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            if verified != entry:
                raise RuntimeError(
                    f"Arquivo mudou após a captura do inventário: {relative_path.as_posix()}."
                )
            verified_inventory.append(verified)
            member = tarfile.TarInfo(relative_path.as_posix())
            member.size = len(payload)
            member.mode = 0o644
            member.mtime = 0
            member.uid = 0
            member.gid = 0
            member.uname = ""
            member.gname = ""
            archive.addfile(member, io.BytesIO(payload))

    raw_snapshot = tar_buffer.getvalue()
    compressed_snapshot = gzip.compress(raw_snapshot, compresslevel=9, mtime=0)
    _write_bytes(SOURCE_SNAPSHOT_PATH, compressed_snapshot)
    snapshot_reference = {
        "path": str(SOURCE_SNAPSHOT_PATH.relative_to(ROOT)),
        "sha256": hashlib.sha256(compressed_snapshot).hexdigest(),
        "uncompressedSha256": hashlib.sha256(raw_snapshot).hexdigest(),
    }
    inventory_payload = {
        "artifactVersion": "1.0",
        "mode": SOURCE_BINDING_MODE,
        "sourceDigest": contract["sourceDigest"],
        "sourceFileCount": contract["sourceFileCount"],
        "gitHead": git_head,
        "worktreeDirty": worktree_dirty,
        "snapshotSha256": snapshot_reference["sha256"],
        "files": verified_inventory,
    }
    _write_json(SOURCE_INVENTORY_PATH, inventory_payload)
    return {
        "mode": SOURCE_BINDING_MODE,
        "gitHead": git_head,
        "worktreeDirty": worktree_dirty,
        "inventory": {
            "path": str(SOURCE_INVENTORY_PATH.relative_to(ROOT)),
            "sha256": hashlib.sha256(SOURCE_INVENTORY_PATH.read_bytes()).hexdigest(),
        },
        "snapshot": snapshot_reference,
    }


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _write_raw_artifact(suite: str, name: str, payload: bytes) -> dict[str, Any]:
    path = PROOF_DIR / "raw" / f"{suite}-{name}.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    compressed = gzip.compress(payload, compresslevel=9, mtime=0)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(compressed)
    os.replace(temporary, path)
    return {
        "path": str(path.relative_to(ROOT)),
        "sha256": hashlib.sha256(compressed).hexdigest(),
        "uncompressedSha256": hashlib.sha256(payload).hexdigest(),
    }


def _require_unchanged_source(before: str) -> None:
    after = _registry_contract(refresh=True)["sourceDigest"]
    if after != before:
        raise RuntimeError("O código de verificação mudou durante a execução da prova.")


def _backend_proof(contract: dict[str, Any]) -> dict[str, Any]:
    junit_path = LOCAL_EVIDENCE_DIR / "backend-junit.xml"
    junit_path.parent.mkdir(parents=True, exist_ok=True)
    ruff_targets = {
        ROOT / "app/api/v1/endpoints/oss_adapters.py",
        ROOT / "app/api/v1/endpoints/oss_native.py",
        ROOT / "app/models/oss_bridge.py",
        ROOT / "app/models/oss_native.py",
        ROOT / "app/schemas/oss_bridge.py",
        ROOT / "app/schemas/oss_native.py",
        *ROOT.glob("app/services/oss_*.py"),
        *ROOT.glob("scripts/*oss*.py"),
        *ROOT.glob("tests/api/test_oss_*.py"),
        *ROOT.glob("tests/services/test_oss_*.py"),
        *ROOT.glob("tests/oss/**/*.py"),
    }
    quality_commands = [
        [
            str(ROOT / ".venv" / "bin" / "ruff"),
            "check",
            *(str(path.relative_to(ROOT)) for path in sorted(ruff_targets)),
        ],
        ["git", "diff", "--check"],
    ]
    combined_output: list[str] = []
    total_duration_ms = 0
    for quality_command in quality_commands:
        exit_code, quality_output, quality_duration = _run(quality_command, cwd=ROOT)
        combined_output.append(quality_output)
        total_duration_ms += quality_duration
        if exit_code != 0:
            raise RuntimeError(f"Gate backend falhou: {' '.join(quality_command)}")
    pytest_command = [
        "bash",
        "scripts/verify_postgresql_qa.sh",
        "--all",
        f"--junitxml={junit_path}",
    ]
    exit_code, pytest_output, pytest_duration = _run(pytest_command, cwd=ROOT)
    combined_output.append(pytest_output)
    total_duration_ms += pytest_duration
    output = "\n".join(combined_output)
    if exit_code != 0:
        raise RuntimeError(f"Backend QA falhou com exit code {exit_code}.")
    root = ET.parse(junit_path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("./testsuite"))
    tests = sum(int(suite.attrib.get("tests", 0)) for suite in suites)
    failed = sum(int(suite.attrib.get("failures", 0)) for suite in suites)
    errors = sum(int(suite.attrib.get("errors", 0)) for suite in suites)
    skipped = sum(int(suite.attrib.get("skipped", 0)) for suite in suites)
    passed = tests - failed - errors - skipped
    if failed or errors or skipped or passed < 1:
        raise RuntimeError(
            "O JUnit do backend não comprova uma execução integral sem skips."
        )
    proof_command = [
        item
        for command in (*quality_commands, pytest_command)
        for item in (*command, "&&")
    ][:-1]
    return {
        **_proof_base(
            "backend",
            proof_command,
            output,
            total_duration_ms,
            contract["sourceDigest"],
            contract["sourceFileCount"],
        ),
        "tests": tests,
        "passed": passed,
        "skipped": skipped,
        "failed": failed,
        "errors": errors,
        "ruffPassed": True,
        "diffCheckPassed": True,
        "junitSha256": hashlib.sha256(junit_path.read_bytes()).hexdigest(),
        "rawArtifacts": [
            _write_raw_artifact("backend", "junit.xml", junit_path.read_bytes()),
            _write_raw_artifact("backend", "output.log", output.encode("utf-8")),
        ],
    }


def _frontend_unit_proof(contract: dict[str, Any]) -> dict[str, Any]:
    commands = [
        ["npm", "run", "typecheck"],
        ["npm", "run", "lint"],
        ["npm", "run", "build"],
        ["npm", "run", "test", "--", "--run"],
    ]
    combined_output: list[str] = []
    duration_ms = 0
    for command in commands:
        exit_code, command_output, command_duration = _run(
            command, cwd=ROOT / "frontend"
        )
        combined_output.append(command_output)
        duration_ms += command_duration
        if exit_code != 0:
            raise RuntimeError(f"Gate frontend falhou: {' '.join(command)}")
    output = "\n".join(combined_output)
    passed, skipped, failed = _parse_vitest_test_summary(output)
    if failed:
        raise RuntimeError(f"Vitest reportou {failed} teste(s) com falha.")
    if skipped:
        raise RuntimeError(f"Vitest omitiu {skipped} teste(s).")
    if passed < 1:
        raise RuntimeError("Vitest não comprovou nenhum teste aprovado.")
    return {
        **_proof_base(
            "frontend-unit",
            [item for command in commands for item in (*command, "&&")][:-1],
            output,
            duration_ms,
            contract["sourceDigest"],
            contract["sourceFileCount"],
        ),
        "passed": passed,
        "skipped": skipped,
        "failed": failed,
        "errors": 0,
        "typecheckPassed": True,
        "lintPassed": True,
        "buildPassed": True,
        "rawArtifacts": [
            _write_raw_artifact("frontend-unit", "output.log", output.encode("utf-8"))
        ],
    }


def _frontend_e2e_proof(contract: dict[str, Any]) -> dict[str, Any]:
    command = ["npm", "run", "test:e2e"]
    api_url = os.environ.get("KLM_API_URL", "http://127.0.0.1:8010")
    exit_code, output, duration_ms = _run(
        command,
        cwd=ROOT / "frontend",
        env={
            "KLM_E2E_PRODUCTION": "1",
            "KLM_E2E_ROLE_JOURNEYS": "1",
            "KLM_E2E_OPERATIONS": "1",
            "KLM_E2E_CANCELLATION": "1",
            "KLM_E2E_PAGINATION": "1",
            "KLM_DEV_API_TARGET": api_url,
            "KLM_FRONTEND_URL": "http://127.0.0.1:4273",
            "KLM_BLACKBOX_URL": "http://127.0.0.1:4274",
        },
    )
    if exit_code != 0:
        raise RuntimeError(f"Playwright QA falhou com exit code {exit_code}.")
    clean = ANSI_ESCAPE.sub("", output)
    passed_matches = re.findall(r"(?:^|\n)\s*(\d+)\s+passed\s*\(", clean)
    skipped_matches = re.findall(r"(?:^|\n)\s*(\d+)\s+skipped", clean)
    matrix = _extract_native_matrix(clean)
    if not passed_matches or matrix is None:
        raise RuntimeError("Não foi possível extrair a prova Playwright da matriz nativa.")
    p95_ms, actions, native_ids = matrix
    if not isinstance(native_ids, list) or len(native_ids) != len(set(native_ids)):
        raise RuntimeError("A matriz Playwright não emitiu IDs nativos únicos.")
    skipped = int(skipped_matches[-1]) if skipped_matches else 0
    if skipped:
        raise RuntimeError(f"Playwright omitiu {skipped} teste(s).")
    return {
        **_proof_base(
            "frontend-e2e",
            command,
            output,
            duration_ms,
            contract["sourceDigest"],
            contract["sourceFileCount"],
        ),
        "passed": int(passed_matches[-1]),
        "skipped": skipped,
        "failed": 0,
        "errors": 0,
        "nativeCapabilityIds": sorted(str(value) for value in native_ids),
        "actionOccurrencesExposed": actions,
        "routeP95Ms": p95_ms,
        "rawArtifacts": [
            _write_raw_artifact("frontend-e2e", "output.log", output.encode("utf-8"))
        ],
    }


def _local_runtime_proof(contract: dict[str, Any]) -> dict[str, Any]:
    base_url = os.environ.get("KLM_API_URL", "http://127.0.0.1:8010")
    commands = [
        [
            str(ROOT / ".venv" / "bin" / "python"),
            "scripts/verify_klm_local.py",
            "--base-url",
            base_url,
        ],
        [
            str(ROOT / ".venv" / "bin" / "python"),
            "scripts/verify_oss_native_contracts.py",
            "--base-url",
            base_url,
        ],
    ]
    exit_code, local_output, local_duration = _run(commands[0], cwd=ROOT)
    if exit_code != 0:
        raise RuntimeError(f"Runtime local falhou com exit code {exit_code}.")
    start = local_output.find("{")
    payload = json.loads(local_output[start:]) if start >= 0 else {}
    if payload.get("status") != "PASS":
        raise RuntimeError("O verificador local não retornou PASS.")
    exit_code, contract_output, contract_duration = _run(commands[1], cwd=ROOT)
    if exit_code != 0:
        raise RuntimeError("A verificação HTTP dos contratos nativos KLM falhou.")
    evidence_matches = re.findall(
        r"KLM_NATIVE_CONTRACT_EVIDENCE=(\{[^\n]+\})", contract_output
    )
    if not evidence_matches:
        raise RuntimeError("O verificador KLM não emitiu evidência estruturada.")
    evidence = json.loads(evidence_matches[-1])
    if evidence.get("status") != "PASS":
        raise RuntimeError("O verificador KLM reportou contratos nativos incompletos.")
    if (
        evidence.get("serverRuntimeSourceDigest") != contract["sourceDigest"]
        or evidence.get("serverRuntimeSourceFileCount") != contract["sourceFileCount"]
        or evidence.get("serverRuntimeSourceMatchesCurrent") is not True
    ):
        raise RuntimeError(
            "A API local não executa o mesmo digest de código vinculado à prova."
        )
    if (
        evidence.get("externalNetworkAssertions") != contract["actionOccurrences"]
        or evidence.get("externalNetworkExecutions") != 0
        or evidence.get("externalEffectEvidenceScope")
        != "application_reported_dry_run"
        or evidence.get("externalEffectIndependentlyObserved") is not False
    ):
        raise RuntimeError(
            "A prova de efeitos externos está ausente, incompleta ou superdimensionada."
        )
    output = local_output + "\n" + contract_output
    return {
        **_proof_base(
            "local-runtime",
            [item for command in commands for item in (*command, "&&")][:-1],
            output,
            local_duration + contract_duration,
            contract["sourceDigest"],
            contract["sourceFileCount"],
        ),
        "checks": int(payload.get("checks", 0)),
        "concurrency": int(payload.get("concurrency", 0)),
        "externalActionsExecuted": int(payload.get("externalActionsExecuted", -1)),
        "nativeDocumentsValidated": int(evidence.get("nativeDocumentsValidated", 0)),
        "actionContractsValidated": int(evidence.get("actionContractsValidated", 0)),
        "contractCapabilitiesValidated": int(
            evidence.get("contractCapabilitiesValidated", 0)
        ),
        "nativeDocumentCapabilityIds": evidence.get("nativeDocumentCapabilityIds", []),
        "contractCapabilityIds": evidence.get("contractCapabilityIds", []),
        "actionContractIds": evidence.get("actionContractIds", []),
        "serverRuntimeSourceDigest": evidence.get("serverRuntimeSourceDigest"),
        "serverRuntimeSourceFileCount": evidence.get("serverRuntimeSourceFileCount"),
        "serverRuntimeSourceMatchesCurrent": evidence.get(
            "serverRuntimeSourceMatchesCurrent"
        ),
        "externalNetworkExecutions": int(evidence.get("externalNetworkExecutions", -1)),
        "externalNetworkAssertions": int(evidence.get("externalNetworkAssertions", -1)),
        "externalEffectEvidenceScope": evidence.get("externalEffectEvidenceScope"),
        "externalEffectIndependentlyObserved": evidence.get(
            "externalEffectIndependentlyObserved"
        ),
        "rawArtifacts": [
            _write_raw_artifact("local-runtime", "output.log", output.encode("utf-8"))
        ],
    }


def _upstream_locks_proof(contract: dict[str, Any]) -> dict[str, Any]:
    commands = [
        ["./scripts/verify_klm_upstreams.sh"],
        [
            str(ROOT / ".venv" / "bin" / "python"),
            "scripts/verify_oss_sidecars.py",
            "--preflight",
            "--profile",
            "all",
        ],
        [
            str(ROOT / ".venv" / "bin" / "pytest"),
            "-q",
            "tests/services/test_oss_native_local_primary_adapter.py",
        ],
    ]
    combined: list[str] = []
    duration_ms = 0
    for command in commands:
        exit_code, output, command_duration = _run(command, cwd=ROOT)
        if exit_code != 0:
            raise RuntimeError(f"Prova upstream falhou: {' '.join(command)}")
        combined.append(output)
        duration_ms += command_duration
    output = "\n".join(combined)
    passed_counts = [int(value) for value in PYTEST_PASSED_LINE.findall(output)]
    sidecar_off_tests_passed = passed_counts[-1] if passed_counts else 0
    if (
        "All 5 OSS upstreams match the lock." not in output
        or "OK preflight:" not in output
        or sidecar_off_tests_passed <= 0
    ):
        raise RuntimeError(
            "A saída não comprova pins, preflight preservado e jornada sidecar-off."
        )
    return {
        **_proof_base(
            "upstream-locks",
            [item for command in commands for item in (*command, "&&")][:-1],
            output,
            duration_ms,
            contract["sourceDigest"],
            contract["sourceFileCount"],
        ),
        "upstreamLocksVerified": 5,
        "sidecarProfilesConfigured": 4,
        "sidecarPrimaryRequired": False,
        "sidecarOffProofPassed": True,
        "sidecarOffTestsPassed": sidecar_off_tests_passed,
        "externalNetworkUsed": False,
        "rawArtifacts": [
            _write_raw_artifact("upstream-locks", "output.log", output.encode("utf-8"))
        ],
    }


RUNNERS = {
    "backend": _backend_proof,
    "frontend-unit": _frontend_unit_proof,
    "frontend-e2e": _frontend_e2e_proof,
    "local-runtime": _local_runtime_proof,
    "upstream-locks": _upstream_locks_proof,
}


def _finalize_manifest(contract: dict[str, Any]) -> None:
    proof_artifacts: dict[str, dict[str, str]] = {}
    proof_payloads: dict[str, dict[str, Any]] = {}
    generated_at: list[str] = []
    git_head: str | None = None
    worktree_dirty: bool | None = None
    for suite in SUITES:
        path = PROOF_DIR / f"{suite}.json"
        proof = json.loads(path.read_text(encoding="utf-8"))
        if (
            proof.get("suite") != suite
            or proof.get("status") != "passed"
            or proof.get("sourceDigest") != contract["sourceDigest"]
            or proof.get("worktreeDigest") != contract["sourceDigest"]
            or proof.get("sourceFileCount") != contract["sourceFileCount"]
            or proof.get("sourceBindingMode") != SOURCE_BINDING_MODE
            or not isinstance(proof.get("worktreeDirty"), bool)
            or re.fullmatch(r"[0-9a-f]{40}", str(proof.get("gitHead", ""))) is None
        ):
            raise RuntimeError(f"A prova {suite} não corresponde ao código atual.")
        if suite in {"backend", "frontend-unit", "frontend-e2e"} and int(
            proof.get("skipped", -1)
        ) != 0:
            raise RuntimeError(f"A prova {suite} contém testes omitidos.")
        if git_head is None:
            git_head = str(proof["gitHead"])
            worktree_dirty = bool(proof["worktreeDirty"])
        elif proof.get("gitHead") != git_head or proof.get("worktreeDirty") is not worktree_dirty:
            raise RuntimeError("As provas não compartilham a mesma proveniência do código.")
        raw = path.read_bytes()
        proof_artifacts[suite] = {
            "path": str(path.relative_to(ROOT)),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        proof_payloads[suite] = proof
        generated_at.append(str(proof["generatedAt"]))
    runtime_proof = proof_payloads["local-runtime"]
    external_effects = runtime_proof.get("externalNetworkExecutions")
    external_assertions = runtime_proof.get("externalNetworkAssertions")
    evidence_scope = runtime_proof.get("externalEffectEvidenceScope")
    independently_observed = runtime_proof.get("externalEffectIndependentlyObserved")
    if (
        external_effects != 0
        or external_assertions != contract["actionOccurrences"]
        or evidence_scope != "application_reported_dry_run"
        or independently_observed is not False
    ):
        raise RuntimeError("A prova local não sustenta a declaração de efeitos externos.")
    assert git_head is not None and worktree_dirty is not None
    _require_unchanged_source(contract["sourceDigest"])
    source_binding = _write_source_binding_artifacts(
        contract,
        git_head=git_head,
        worktree_dirty=worktree_dirty,
    )
    payload = {
        "schemaVersion": "1.2",
        "generatedAt": max(generated_at),
        **contract["contract"],
        "verificationFilesDigest": contract["sourceDigest"],
        "verificationFileCount": contract["sourceFileCount"],
        "actionOccurrencesContractValidated": contract["actionOccurrences"],
        "uniqueCommandsContractValidated": contract["uniqueCommands"],
        "externalEffectsExecuted": external_effects,
        "externalNetworkAssertionsValidated": external_assertions,
        "externalEffectEvidenceScope": evidence_scope,
        "externalEffectIndependentlyObserved": independently_observed,
        "sourceBinding": source_binding,
        "proofArtifacts": proof_artifacts,
    }
    _write_json(MANIFEST_PATH, payload)
    print(f"Manifesto finalizado: {MANIFEST_PATH.relative_to(ROOT)}", flush=True)


def _post_finalize_smoke(contract: dict[str, Any]) -> dict[str, Any]:
    api_url = os.environ.get("KLM_API_URL", "http://127.0.0.1:8010")
    command = [
        str(ROOT / "frontend" / "node_modules" / ".bin" / "playwright"),
        "test",
        "e2e/native-capabilities.spec.ts",
    ]
    exit_code, output, duration_ms = _run(
        command,
        cwd=ROOT / "frontend",
        env={
            "KLM_REQUIRE_VERIFIED_OSS": "1",
            "KLM_E2E_PRODUCTION": "1",
            "KLM_DEV_API_TARGET": api_url,
            "KLM_FRONTEND_URL": "http://127.0.0.1:4273",
            "KLM_BLACKBOX_URL": "http://127.0.0.1:4274",
        },
    )
    POST_FINALIZE_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = POST_FINALIZE_OUTPUT_PATH.with_suffix(".log.tmp")
    temporary_output.write_text(output, encoding="utf-8")
    os.replace(temporary_output, POST_FINALIZE_OUTPUT_PATH)
    if exit_code != 0:
        raise RuntimeError(
            f"Smoke pós-finalização falhou com exit code {exit_code}."
        )

    clean = ANSI_ESCAPE.sub("", output)
    passed_matches = re.findall(r"(?:^|\n)\s*(\d+)\s+passed\s*\(", clean)
    skipped_matches = re.findall(r"(?:^|\n)\s*(\d+)\s+skipped", clean)
    matrix = _extract_native_matrix(clean)
    if not passed_matches or matrix is None:
        raise RuntimeError(
            "O smoke pós-finalização não emitiu a matriz nativa completa."
        )
    p95_ms, actions, native_ids = matrix
    if (
        int(passed_matches[-1]) < 3
        or any(int(value) for value in skipped_matches)
        or not isinstance(native_ids, list)
        or sorted(native_ids) != sorted(contract["publicNativeIds"])
        or actions != contract["actionOccurrences"]
    ):
        raise RuntimeError(
            "O smoke pós-finalização divergiu das 135 capacidades ou 511 ações."
        )

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    evidence = {
        "proofVersion": "1.0",
        "status": "passed",
        "generatedAt": datetime.now(UTC).isoformat(),
        "sourceDigest": contract["sourceDigest"],
        "sourceFileCount": contract["sourceFileCount"],
        "manifestSha256": hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest(),
        "command": command,
        "requiredVerifiedRelease": True,
        "passed": int(passed_matches[-1]),
        "nativeExperienceVerified": len(native_ids),
        "contractCapabilitiesVerified": len(contract["contractIds"]),
        "actionOccurrencesExposed": actions,
        "externalEffectsExecuted": manifest.get("externalEffectsExecuted"),
        "externalNetworkAssertionsValidated": manifest.get(
            "externalNetworkAssertionsValidated"
        ),
        "externalEffectEvidenceScope": manifest.get("externalEffectEvidenceScope"),
        "externalEffectIndependentlyObserved": manifest.get(
            "externalEffectIndependentlyObserved"
        ),
        "externalEffectObservationSource": "release_manifest",
        "routeP95Ms": p95_ms,
        "durationMs": duration_ms,
        "outputPath": _display_path(POST_FINALIZE_OUTPUT_PATH),
        "outputSha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
    }
    _write_json(POST_FINALIZE_EVIDENCE_PATH, evidence)
    print(
        "Smoke pós-finalização aprovado: "
        f"{len(native_ids)} experiências, {len(contract['contractIds'])} contratos, "
        f"{actions} ações, p95={p95_ms}ms.",
        flush=True,
    )
    return evidence


def _restore_manifest(previous: bytes | None) -> None:
    if previous is None:
        MANIFEST_PATH.unlink(missing_ok=True)
        return
    temporary = MANIFEST_PATH.with_suffix(".json.restore")
    temporary.write_bytes(previous)
    os.replace(temporary, MANIFEST_PATH)


def _restore_artifact(path: Path, previous: bytes | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
        return
    _write_bytes(path, previous)


def _finalize_and_verify(contract: dict[str, Any]) -> None:
    previous_manifest = MANIFEST_PATH.read_bytes() if MANIFEST_PATH.exists() else None
    previous_inventory = (
        SOURCE_INVENTORY_PATH.read_bytes() if SOURCE_INVENTORY_PATH.exists() else None
    )
    previous_snapshot = (
        SOURCE_SNAPSHOT_PATH.read_bytes() if SOURCE_SNAPSHOT_PATH.exists() else None
    )
    _finalize_manifest(contract)
    try:
        _post_finalize_smoke(contract)
    except BaseException:
        _restore_manifest(previous_manifest)
        _restore_artifact(SOURCE_INVENTORY_PATH, previous_inventory)
        _restore_artifact(SOURCE_SNAPSHOT_PATH, previous_snapshot)
        POST_FINALIZE_EVIDENCE_PATH.unlink(missing_ok=True)
        print(
            "Manifesto restaurado porque o estado final verificado não passou no smoke.",
            file=sys.stderr,
            flush=True,
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=(*SUITES, "all"), default="all")
    parser.add_argument("--finalize-only", action="store_true")
    arguments = parser.parse_args()
    contract = _registry_contract(refresh=True)
    if not arguments.finalize_only:
        selected = SUITES if arguments.suite == "all" else (arguments.suite,)
        for suite in selected:
            print(f"\n== Prova {suite} ==", flush=True)
            proof = RUNNERS[suite](contract)
            _require_unchanged_source(contract["sourceDigest"])
            _write_json(PROOF_DIR / f"{suite}.json", proof)
    if arguments.suite == "all" or arguments.finalize_only:
        _finalize_and_verify(contract)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, ET.ParseError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error
