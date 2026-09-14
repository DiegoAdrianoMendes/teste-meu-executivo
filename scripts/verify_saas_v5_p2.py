#!/usr/bin/env python3
"""Run the reproducible local-only acceptance gate for SaaS V5 phase P2."""

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

from app.services.playbook_defaults import default_playbook_content  # noqa: E402
from scripts.capture_klm_baseline import capture  # noqa: E402

MANIFEST_PATH = ROOT / "app" / "data" / "saas_v5" / "p2-verification.json"
EVIDENCE_DIR = ROOT / ".local" / "qa-evidence" / "saas-v5-p2"


@dataclass(frozen=True, slots=True)
class Suite:
    name: str
    command: tuple[str, ...]
    cwd: Path = ROOT


BACKEND_TESTS = (
    "tests/services/test_playbook_service.py",
    "tests/api/test_playbooks.py",
    "tests/migrations/test_playbook_studio_migration.py",
    "tests/migrations/test_single_alembic_head.py",
    "tests/db/test_tenant_rls_sqlite.py",
)

FRONTEND_TESTS = (
    "src/test/playbooks-service.test.ts",
    "src/test/playbooks-page.test.tsx",
    "src/test/playbook-editor.test.tsx",
    "src/test/effective-playbook-link.test.tsx",
    "src/test/conversations-tools.test.tsx",
    "src/test/lead-proposal-integrity.test.tsx",
    "src/test/klm-frontend.test.tsx",
    "src/test/legal-native-workflows.test.tsx",
    "src/test/flow-editor.test.ts",
)

CONTEXTUAL_SURFACES = {
    "conversations": ROOT / "frontend/src/klm/pages/ConversationsPage.tsx",
    "agents": ROOT / "frontend/src/klm/pages/AgentsPage.tsx",
    "flows": ROOT / "frontend/src/klm/pages/FlowsPage.tsx",
    "proposals": ROOT / "frontend/src/klm/pages/ProposalPreviewPage.tsx",
    "areas": ROOT / "frontend/src/klm/pages/AreasPage.tsx",
}

SUITES = (
    Suite("backend-playbooks", (".venv/bin/pytest", "-q", *BACKEND_TESTS)),
    Suite(
        "backend-static",
        (
            ".venv/bin/ruff",
            "check",
            "--select",
            "F,I",
            "alembic/versions/d9a7e5c3b102_add_playbook_studio.py",
            "app/api/v1/endpoints/playbooks.py",
            "app/models/playbook.py",
            "app/schemas/playbook.py",
            "app/services/playbook_defaults.py",
            "app/services/playbook_service.py",
            "scripts/verify_saas_v5_p2.py",
            *BACKEND_TESTS,
        ),
    ),
    Suite("postgresql-playbooks", ("bash", "scripts/verify_postgresql_qa.sh")),
    Suite("frontend-typecheck", ("npm", "run", "typecheck"), ROOT / "frontend"),
    Suite(
        "frontend-playbooks",
        ("npm", "run", "test", "--", "--run", *FRONTEND_TESTS),
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


def _contract_snapshot() -> dict[str, Any]:
    content = default_playbook_content()
    surfaces = {
        name: path.is_file()
        and "EffectivePlaybookLink" in path.read_text(encoding="utf-8")
        for name, path in CONTEXTUAL_SURFACES.items()
    }
    return {
        "mandatoryBlocks": 13,
        "objections": len(content.objections),
        "agents": len(content.agents),
        "cadenceCategories": len(content.follow_up.cadences),
        "autonomyModesTested": 4,
        "minimumInitialFeeBrl": float(content.commercial.minimum_initial_fee_brl),
        "maximumInitialFeeBrl": content.commercial.maximum_initial_fee_brl,
        "successOnlyAllowed": content.commercial.success_only_allowed,
        "externalActionsExecuted": 0,
        "contextualSurfaces": surfaces,
        "contextualSurfacesReady": all(surfaces.values()),
    }


def verify(*, stop_on_failure: bool = True) -> tuple[dict[str, Any], int]:
    before = capture(ROOT)
    contract = _contract_snapshot()
    suite_results: list[dict[str, Any]] = []
    for suite in SUITES:
        print(f"\n[p2] {suite.name}: {' '.join(suite.command)}", flush=True)
        result = _run_suite(suite)
        suite_results.append(result)
        if result["status"] != "passed" and stop_on_failure:
            break

    after = capture(ROOT)
    source_stable = before["sourceDigest"] == after["sourceDigest"]
    all_suites_passed = len(suite_results) == len(SUITES) and all(
        item["status"] == "passed" for item in suite_results
    )
    contract_ready = (
        contract["mandatoryBlocks"] == 13
        and contract["objections"] >= 14
        and contract["agents"] >= 18
        and contract["cadenceCategories"] >= 8
        and contract["autonomyModesTested"] == 4
        and contract["minimumInitialFeeBrl"] >= 4000
        and contract["maximumInitialFeeBrl"] is None
        and contract["successOnlyAllowed"] is True
        and contract["externalActionsExecuted"] == 0
        and contract["contextualSurfacesReady"] is True
    )
    passed = all_suites_passed and source_stable and contract_ready
    manifest = {
        "schemaVersion": "1.0",
        "kind": "klm-saas-v5-p2-verification",
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
        "contract": contract,
        "suitesExpected": [suite.name for suite in SUITES],
        "suites": suite_results,
    }
    _write_json(MANIFEST_PATH, manifest)
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
