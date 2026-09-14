#!/usr/bin/env python3
"""Run the reproducible local-only acceptance gate for SaaS V5 phase P1."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.capture_klm_baseline import DEFAULT_OUTPUT, capture  # noqa: E402

MANIFEST_PATH = ROOT / "app" / "data" / "saas_v5" / "p1-verification.json"
EVIDENCE_DIR = ROOT / ".local" / "qa-evidence" / "saas-v5-p1"


@dataclass(frozen=True, slots=True)
class Suite:
    name: str
    command: tuple[str, ...]
    cwd: Path = ROOT


BACKEND_TESTS = (
    "tests/ai",
    "tests/core",
    "tests/migrations",
    "tests/api/test_admin_domain_security.py",
    "tests/api/test_auth_identity_hardening.py",
    "tests/api/test_auth_mfa.py",
    "tests/api/test_auth_session.py",
    "tests/api/test_local_billing.py",
    "tests/api/test_local_provisioning.py",
    "tests/api/test_local_storage_objects.py",
    "tests/api/test_klm_operations.py",
    "tests/api/test_document_storage_convergence.py",
    "tests/api/test_matter_tenant_references.py",
    "tests/api/test_premium_feature_gates.py",
    "tests/api/test_public_api.py",
    "tests/api/test_support_access.py",
    "tests/api/test_team_permissions.py",
    "tests/api/test_typed_entitlement_quotas.py",
    "tests/services/test_configuration_service.py",
    "tests/services/test_auth_registration_billing.py",
    "tests/services/test_entitlement_service.py",
    "tests/services/test_identity_remediation.py",
    "tests/services/test_local_billing_service.py",
    "tests/services/test_local_object_storage_service.py",
    "tests/services/test_local_provisioning_service.py",
    "tests/services/test_local_schema.py",
    "tests/services/test_storage_p1_whatsapp.py",
    "tests/services/test_whatsapp_inbound_pipeline.py",
    "tests/services/test_subscription_access_policy.py",
    "tests/services/test_typed_entitlement_gates.py",
    "tests/integration/test_subscription_access_gate.py",
    "tests/workers",
    "tests/test_production_database_roles.py",
    "tests/test_saas_p1_tenant_model_constraints.py",
)

FRONTEND_TESTS = (
    "src/test/api-origin.test.ts",
    "src/test/auth-security-panel.test.tsx",
    "src/test/auth-security-service.test.ts",
    "src/test/auth-token-storage.test.ts",
    "src/test/app-context-realtime.test.tsx",
    "src/test/configuration-governance.test.tsx",
    "src/test/configuration-service.test.ts",
    "src/test/frontend-contracts.test.tsx",
    "src/test/http-session-recovery.test.ts",
    "src/test/klm-frontend.test.tsx",
    "src/test/local-provisioning-panel.test.tsx",
    "src/test/local-storage-panel.test.tsx",
    "src/test/local-storage-service.test.ts",
    "src/test/login-mfa.test.tsx",
    "src/test/platform-support-access-panel.test.tsx",
    "src/test/saas-billing-panel.test.tsx",
    "src/test/support-access-panel.test.tsx",
    "src/test/support-access-service.test.ts",
    "src/test/support-workspace.test.tsx",
    "src/test/team-permissions.test.tsx",
)

SUITES = (
    Suite("upstream-locks", ("bash", "scripts/verify_klm_upstreams.sh")),
    Suite(
        "function-unit-census",
        (".venv/bin/python", "scripts/census_oss_function_units.py", "--check"),
    ),
    Suite("function-unit-census-tests", (".venv/bin/pytest", "-q", "tests/oss")),
    Suite("postgresql-p1", ("bash", "scripts/verify_postgresql_qa.sh")),
    Suite("backend-p1", (".venv/bin/pytest", "-q", *BACKEND_TESTS)),
    Suite(
        "backend-static",
        (
            ".venv/bin/ruff",
            "check",
            "--select",
            "F,I",
            "app/ai",
            "app/core",
            "app/models/auth_security.py",
            "app/models/client.py",
            "app/models/configuration.py",
            "app/models/entitlement.py",
            "app/models/identity.py",
            "app/models/matter.py",
            "app/models/storage_object.py",
            "app/models/subscription_access.py",
            "app/models/support_access.py",
            "app/models/user.py",
            "app/services/auth_rate_limit_service.py",
            "app/services/configuration_service.py",
            "app/services/entitlement_service.py",
            "app/services/auth_revocation_service.py",
            "app/services/identity_lifecycle_service.py",
            "app/services/identity_outbox_service.py",
            "app/services/local_billing_service.py",
            "app/services/local_object_storage_service.py",
            "app/services/local_provisioning_service.py",
            "app/services/support_access_service.py",
            "app/services/finance_service.py",
            "app/services/agenda_service.py",
            "app/services/document_service.py",
            "app/services/s3_service.py",
            "app/services/sdr_intake_service.py",
            "app/services/whatsapp/inbox_service.py",
            "app/services/whatsapp/webhook_service.py",
            "app/api/v1/entitlement_errors.py",
            "app/api/v1/endpoints/auth.py",
            "app/api/v1/endpoints/agenda.py",
            "app/api/v1/endpoints/documents.py",
            "app/api/v1/endpoints/finance.py",
            "app/api/v1/endpoints/integrations.py",
            "app/api/v1/endpoints/klm_operations.py",
            "app/api/v1/endpoints/meetings.py",
            "app/api/v1/endpoints/whatsapp.py",
            "app/api/v1/endpoints/local_storage.py",
            "app/api/v1/endpoints/platform_support_access.py",
            "app/api/v1/endpoints/workspace_support_access.py",
            "app/workers/flow_tasks.py",
            "app/workers/auth_revocation_tasks.py",
            "app/workers/storage_tasks.py",
            "app/workers/whatsapp_inbound_tasks.py",
            "scripts/capture_klm_baseline.py",
            "scripts/verify_saas_v5_p1.py",
            "tests/test_saas_p1_tenant_model_constraints.py",
            "tests/ai",
            "tests/core",
            "tests/migrations",
        ),
    ),
    Suite("frontend-typecheck", ("npm", "run", "typecheck"), ROOT / "frontend"),
    Suite(
        "frontend-p1",
        ("npm", "run", "test", "--", *FRONTEND_TESTS),
        ROOT / "frontend",
    ),
    Suite("frontend-production", ("npm", "run", "verify:production"), ROOT / "frontend"),
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _run_suite(suite: Suite) -> dict[str, Any]:
    started = time.monotonic()
    completed = subprocess.run(
        suite.command,
        cwd=suite.cwd,
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
        "status": "passed" if completed.returncode == 0 else "failed",
        "exitCode": completed.returncode,
        "durationMs": duration_ms,
        "cwd": suite.cwd.relative_to(ROOT).as_posix() or ".",
        "command": list(suite.command),
        "output": {
            "path": log_path.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        },
    }


def verify(*, stop_on_failure: bool = True) -> tuple[dict[str, Any], int]:
    before = capture(ROOT)
    suite_results: list[dict[str, Any]] = []
    for suite in SUITES:
        print(f"\n[p1] {suite.name}: {' '.join(suite.command)}", flush=True)
        result = _run_suite(suite)
        suite_results.append(result)
        if result["status"] != "passed" and stop_on_failure:
            break

    after = capture(ROOT)
    source_stable = before["sourceDigest"] == after["sourceDigest"]
    all_suites_passed = len(suite_results) == len(SUITES) and all(
        item["status"] == "passed" for item in suite_results
    )
    passed = (
        all_suites_passed
        and source_stable
        and before["snapshotReady"]
        and after["snapshotReady"]
    )
    manifest = {
        "schemaVersion": "1.0",
        "kind": "klm-saas-v5-p1-verification",
        "status": "passed" if passed else "failed",
        "generatedAt": datetime.now(UTC).isoformat(),
        "localOnly": True,
        "externalActionsExecuted": 0,
        "source": {
            "digestBefore": before["sourceDigest"],
            "digestAfter": after["sourceDigest"],
            "fileCountBefore": before["sourceFileCount"],
            "fileCountAfter": after["sourceFileCount"],
            "stableDuringVerification": source_stable,
            "gitHead": after["git"]["head"],
            "worktreeDirty": after["git"]["dirty"],
        },
        "baseline": {
            "path": DEFAULT_OUTPUT.relative_to(ROOT).as_posix(),
            "snapshotReadyBefore": before["snapshotReady"],
            "snapshotReadyAfter": after["snapshotReady"],
            "secretFindings": after["secretScan"]["findingCount"],
            "upstreamsMatch": after["upstreams"]["allMatchLock"],
        },
        "suitesExpected": [suite.name for suite in SUITES],
        "suites": suite_results,
    }
    _write_json(MANIFEST_PATH, manifest)
    if passed:
        _write_json(DEFAULT_OUTPUT, after)
    return manifest, 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Run all suites even after a failure.",
    )
    arguments = parser.parse_args()
    manifest, exit_code = verify(stop_on_failure=not arguments.keep_going)
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "sourceDigest": manifest["source"]["digestAfter"],
                "sourceStable": manifest["source"]["stableDuringVerification"],
                "suitesPassed": sum(
                    item["status"] == "passed" for item in manifest["suites"]
                ),
                "suitesExpected": len(manifest["suitesExpected"]),
                "manifest": MANIFEST_PATH.relative_to(ROOT).as_posix(),
            },
            sort_keys=True,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
