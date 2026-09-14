#!/usr/bin/env python3
"""Verifica os gates locais P4 com provas reproduzíveis e sem efeitos externos."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ai.telemetry import ModelTelemetryEvent  # noqa: E402
from scripts.capture_klm_baseline import capture  # noqa: E402

SCRIPT_VERSION = "klm-saas-v5-p4-verifier-v1"
MANIFEST_PATH = ROOT / "app/data/saas_v5/p4-verification.json"
EVIDENCE_DIR = ROOT / ".local/qa-evidence/saas-v5-p4"
BENCHMARK_EVIDENCE = EVIDENCE_DIR / "benchmark.json"


@dataclass(frozen=True, slots=True)
class Suite:
    name: str
    category: str
    command: tuple[str, ...]
    cwd: Path = ROOT
    required: bool = True


SUITES = (
    Suite(
        "ai-context-benchmark",
        "benchmark",
        (
            ".venv/bin/python",
            "scripts/benchmark_ai_context.py",
            "--output",
            ".local/qa-evidence/saas-v5-p4/benchmark.json",
            "--assert-gates",
        ),
    ),
    Suite(
        "context-gateway-budget",
        "budget-race-and-attribution-runtime",
        (
            ".venv/bin/pytest",
            "-q",
            "tests/ai/test_context_compiler.py",
            "tests/ai/test_gateway.py",
            "tests/ai/test_limits.py",
            "tests/services/test_ai_service_api_compat.py",
        ),
    ),
    Suite(
        "rag-tenant-isolation",
        "rag-isolation",
        (
            ".venv/bin/pytest",
            "-q",
            "tests/services/test_knowledge_service.py",
        ),
    ),
    Suite(
        "restart-idempotency",
        "restart-and-idempotency",
        (
            ".venv/bin/pytest",
            "-q",
            (
                "tests/services/test_whatsapp_message_hot_path.py::"
                "test_restart_after_request_checkpoint_never_redispatches"
            ),
            (
                "tests/services/test_whatsapp_message_hot_path.py::"
                "test_document_replay_and_payload_conflict_are_fenced"
            ),
            (
                "tests/workers/test_flow_tasks.py::"
                "test_flow_idempotent_adapter_reuses_key_after_crash"
            ),
            (
                "tests/workers/test_event_bus.py::"
                "test_recovery_republishes_run_after_crash_between_commit_and_publish"
            ),
        ),
    ),
    Suite(
        "local-stack-static-contract",
        "stack-contract",
        (".venv/bin/python", "scripts/verify_local_stack.py"),
    ),
    Suite(
        "p4-script-tests",
        "verifier-tests",
        (
            ".venv/bin/pytest",
            "-q",
            "tests/scripts/test_benchmark_ai_context.py",
            "tests/scripts/test_verify_saas_v5_p4.py",
        ),
    ),
    Suite(
        "p4-static-quality",
        "static-quality",
        (
            ".venv/bin/ruff",
            "check",
            "scripts/benchmark_ai_context.py",
            "scripts/verify_saas_v5_p4.py",
            "tests/scripts/test_benchmark_ai_context.py",
            "tests/scripts/test_verify_saas_v5_p4.py",
        ),
    ),
)

REQUIRED_CATEGORIES = frozenset(
    {
        "benchmark",
        "budget-race-and-attribution-runtime",
        "rag-isolation",
        "restart-and-idempotency",
        "stack-contract",
        "verifier-tests",
        "static-quality",
    }
)
REQUIRED_TELEMETRY_FIELDS = frozenset(
    {
        "workspace_ref",
        "agent_name",
        "task_class",
        "operation",
        "scope_type",
        "matter_ref",
        "context_digest",
        "playbook_ref",
        "source_refs_digest",
        "source_count",
        "attribution_complete",
    }
)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _offline_env() -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "AI_PROVIDER": "fake",
            "ANTHROPIC_API_KEY": "",
            "AWS_ACCESS_KEY_ID": "",
            "AWS_SECRET_ACCESS_KEY": "",
            "GEMINI_API_KEY": "",
            "KLM_AWS_ENABLED": "false",
            "KLM_EXTERNAL_ACTIONS_ENABLED": "false",
            "KLM_MODEL_APIS_ENABLED": "false",
            "KLM_ONLINE_APPROVED": "false",
            "KLM_RUNTIME_MODE": "local_simulation",
            "OPENAI_API_KEY": "",
            "RESEND_API_KEY": "",
        }
    )
    return environment


def _safe_relative(path: Path, root: Path = ROOT) -> str:
    try:
        return path.relative_to(root).as_posix() or "."
    except ValueError:
        return str(path)


def _run_suite(suite: Suite) -> dict[str, Any]:
    started = time.monotonic()
    completed = subprocess.run(
        suite.command,
        cwd=suite.cwd,
        env=_offline_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    duration_ms = round((time.monotonic() - started) * 1000)
    output = completed.stdout
    log_path = EVIDENCE_DIR / f"{suite.name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(output, encoding="utf-8")
    print(output, end="")
    return {
        "name": suite.name,
        "category": suite.category,
        "required": suite.required,
        "status": "passed" if completed.returncode == 0 else "failed",
        "exitCode": completed.returncode,
        "durationMs": duration_ms,
        "cwd": _safe_relative(suite.cwd),
        "command": list(suite.command),
        "output": {
            "path": _safe_relative(log_path),
            "sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        },
    }


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def audit_model_calls(root: Path = ROOT) -> dict[str, Any]:
    """Require explicit attribution at every application model-call boundary."""
    callsites: list[dict[str, Any]] = []
    parse_errors: list[dict[str, Any]] = []
    app_root = root / "app"
    for path in sorted(app_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeError) as exc:
            parse_errors.append(
                {
                    "path": _safe_relative(path, root),
                    "errorType": type(exc).__name__,
                }
            )
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node.func)
            if name not in {"ModelRequest", "complete"}:
                continue
            relative_parts = path.relative_to(app_root).parts
            if name == "complete" and relative_parts[:2] == ("ai", "providers"):
                # Provider adapters receive an already-attributed ModelRequest from
                # ModelGateway. Their internal SDK compatibility call is not a new
                # application request boundary and is covered by gateway telemetry.
                continue
            keyword_names = {item.arg for item in node.keywords if item.arg is not None}
            if name == "ModelRequest":
                required = {"workspace_id", "agent_name", "attribution"}
                kind = "typed-model-request"
            else:
                required = {"workspace_id", "agent_name", "attribution"}
                kind = "provider-complete"
            missing = sorted(required - keyword_names)
            callsites.append(
                {
                    "path": _safe_relative(path, root),
                    "line": node.lineno,
                    "kind": kind,
                    "requiredKeywords": sorted(required),
                    "missingKeywords": missing,
                    "compliant": not missing,
                }
            )
    compliant = sum(item["compliant"] for item in callsites)
    total = len(callsites)
    coverage = compliant / total if total else 0.0
    return {
        "policy": "explicit workspace_id + agent_name + attribution at every model call",
        "totalCallsites": total,
        "compliantCallsites": compliant,
        "coverage": round(coverage, 8),
        "parseErrors": parse_errors,
        "missing": [item for item in callsites if not item["compliant"]],
        "callsites": callsites,
        "passed": total > 0 and compliant == total and not parse_errors,
    }


def telemetry_contract(root: Path = ROOT) -> dict[str, Any]:
    available = {field.name for field in fields(ModelTelemetryEvent)}
    gateway_path = root / "app/ai/gateway.py"
    gateway_source = gateway_path.read_text(encoding="utf-8") if gateway_path.is_file() else ""
    missing_fields = sorted(REQUIRED_TELEMETRY_FIELDS - available)
    required_mappings = {
        "operation": "operation=attribution.operation",
        "matter_ref": "matter_ref=matter_ref",
        "context_digest": "context_digest=attribution.context_digest",
        "playbook_ref": "playbook_ref=(",
        "source_refs_digest": "source_refs_digest=source_refs_digest(",
        "attribution_complete": "attribution_complete=bool(",
    }
    missing_mappings = sorted(
        name for name, fragment in required_mappings.items() if fragment not in gateway_source
    )
    return {
        "requiredFields": sorted(REQUIRED_TELEMETRY_FIELDS),
        "missingFields": missing_fields,
        "requiredGatewayMappings": sorted(required_mappings),
        "missingGatewayMappings": missing_mappings,
        "passed": not missing_fields and not missing_mappings,
    }


def attribution_snapshot(root: Path = ROOT) -> dict[str, Any]:
    calls = audit_model_calls(root)
    telemetry = telemetry_contract(root)
    return {
        "requiredCoverage": 1.0,
        "observedCoverage": calls["coverage"],
        "calls": calls,
        "telemetry": telemetry,
        "passed": calls["passed"] and telemetry["passed"],
    }


def _read_benchmark(path: Path = BENCHMARK_EVIDENCE) -> dict[str, Any]:
    if not path.is_file():
        return {
            "status": "missing",
            "path": _safe_relative(path),
            "sha256": None,
            "result": None,
        }
    raw = path.read_bytes()
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "status": "invalid",
            "path": _safe_relative(path),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "result": None,
        }
    return {
        "status": result.get("status", "invalid"),
        "path": _safe_relative(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "result": result,
    }


def stack_snapshot(suite_results: list[dict[str, Any]]) -> dict[str, Any]:
    suite = next(
        (item for item in suite_results if item["category"] == "stack-contract"),
        None,
    )
    output = ""
    if suite is not None:
        log_path = ROOT / suite["output"]["path"]
        if log_path.is_file():
            output = log_path.read_text(encoding="utf-8")
    cache_match = re.search(r"IMAGE_CACHE_PRESENT: (\d+)/(\d+)", output)
    static_passed = bool(suite and suite["status"] == "passed" and "LOCAL_STACK_OK" in output)
    if "IMAGE_BUILD_SKIPPED" in output:
        live_reason = "pinned_images_missing_and_no_pull_attempted"
    elif "IMAGE_CACHE_READY" in output:
        live_reason = "containers_not_built_or_started_by_p4_verifier"
    else:
        live_reason = "local_stack_live_execution_not_performed"
    return {
        "staticContract": {
            "status": "passed" if static_passed else "failed",
            "offlineComposeRendered": "LOCAL_STACK_OK" in output,
            "imageCachePresent": int(cache_match.group(1)) if cache_match else None,
            "imageCacheExpected": int(cache_match.group(2)) if cache_match else None,
        },
        "liveRuntime": {
            "status": "blocked",
            "requiredForP4OfflineGate": False,
            "reason": live_reason,
            "containersStartedByVerifier": False,
            "networkPullAttempted": False,
        },
    }


def _suite_contract() -> dict[str, Any]:
    actual = {suite.category for suite in SUITES if suite.required}
    missing = sorted(REQUIRED_CATEGORIES - actual)
    return {
        "requiredCategories": sorted(REQUIRED_CATEGORIES),
        "actualCategories": sorted(actual),
        "missingCategories": missing,
        "passed": not missing,
    }


def verify(
    *,
    stop_on_failure: bool = False,
    manifest_path: Path = MANIFEST_PATH,
) -> tuple[dict[str, Any], int]:
    before = capture(ROOT)
    attribution = attribution_snapshot(ROOT)
    suite_contract = _suite_contract()
    suite_results: list[dict[str, Any]] = []
    for suite in SUITES:
        print(f"\n[p4] {suite.name}: {' '.join(suite.command)}", flush=True)
        result = _run_suite(suite)
        suite_results.append(result)
        if result["status"] != "passed" and stop_on_failure:
            break

    benchmark = _read_benchmark(BENCHMARK_EVIDENCE)
    stack = stack_snapshot(suite_results)
    after = capture(ROOT)
    source_stable = before["sourceDigest"] == after["sourceDigest"]
    expected_suites = [suite.name for suite in SUITES]
    all_required_suites_passed = (
        len(suite_results) == len(SUITES)
        and all(item["status"] == "passed" for item in suite_results if item["required"])
    )
    gates = {
        "sourceStable": source_stable,
        "suiteContractComplete": suite_contract["passed"],
        "allRequiredSuitesPassed": all_required_suites_passed,
        "benchmarkPassed": benchmark["status"] == "passed",
        "modelAttributionCoverage100Percent": attribution["passed"],
        "budgetRacePassed": any(
            item["category"] == "budget-race-and-attribution-runtime"
            and item["status"] == "passed"
            for item in suite_results
        ),
        "ragIsolationPassed": any(
            item["category"] == "rag-isolation" and item["status"] == "passed"
            for item in suite_results
        ),
        "restartIdempotencyPassed": any(
            item["category"] == "restart-and-idempotency"
            and item["status"] == "passed"
            for item in suite_results
        ),
        "stackStaticContractPassed": stack["staticContract"]["status"] == "passed",
    }
    passed = all(gates.values())
    manifest = {
        "schemaVersion": "1.0",
        "kind": "klm-saas-v5-p4-verification",
        "verifierVersion": SCRIPT_VERSION,
        "status": "passed" if passed else "failed",
        "generatedAt": datetime.now(UTC).isoformat(),
        "localOnly": True,
        "externalActionsExecuted": 0,
        "networkCallsExecuted": 0,
        "awsActionsExecuted": 0,
        "source": {
            "digestBefore": before["sourceDigest"],
            "digestAfter": after["sourceDigest"],
            "fileCountBefore": before["sourceFileCount"],
            "fileCountAfter": after["sourceFileCount"],
            "stableDuringVerification": source_stable,
            "gitHead": after["git"]["head"],
            "worktreeDirty": after["git"]["dirty"],
        },
        "claims": {
            "offlineP4Verified": passed,
            "dockerStaticContractVerified": (
                stack["staticContract"]["status"] == "passed"
            ),
            "dockerLiveRuntimeVerified": False,
            "externalProviderVerified": False,
            "productionVerified": False,
        },
        "gates": gates,
        "suiteContract": suite_contract,
        "suitesExpected": expected_suites,
        "suites": suite_results,
        "attribution": attribution,
        "benchmark": benchmark,
        "stack": stack,
        "blocked": [
            {
                "scope": "docker-live-runtime",
                "status": "blocked",
                "reason": stack["liveRuntime"]["reason"],
                "requiredForP4OfflineGate": False,
            },
            {
                "scope": "external-apis-and-aws",
                "status": "blocked_by_scope",
                "reason": "explicitly_outside_local_p4",
                "requiredForP4OfflineGate": False,
            },
        ],
    }
    _atomic_write_json(manifest_path, manifest)
    return manifest, 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--output", type=Path, default=MANIFEST_PATH)
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    manifest, exit_code = verify(
        stop_on_failure=args.fail_fast,
        manifest_path=output,
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "sourceStable": manifest["source"]["stableDuringVerification"],
                "attributionCoverage": manifest["attribution"]["observedCoverage"],
                "suitesPassed": sum(
                    item["status"] == "passed" for item in manifest["suites"]
                ),
                "suitesExpected": len(manifest["suitesExpected"]),
                "dockerLive": manifest["stack"]["liveRuntime"]["status"],
                "manifest": _safe_relative(output),
            },
            sort_keys=True,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
