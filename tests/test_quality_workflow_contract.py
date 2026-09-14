from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/deploy.yml"
GITLEAKS = ROOT / ".gitleaks.toml"
PLAYWRIGHT = ROOT / "frontend/playwright.config.ts"


def _quality_job() -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    return workflow.split("\n  qualify-candidate:", maxsplit=1)[0]


def test_quality_job_runs_backend_and_postgresql_proof_exactly_once() -> None:
    quality = _quality_job()

    assert quality.count("scripts/verify_postgresql_qa.sh") == 1
    assert "--all" in quality
    assert "--junitxml=.local/qa-evidence/postgresql-qa/junit.xml" in quality
    assert 'PYTEST_ADDOPTS: "--timeout=60 -x"' in quality
    # The complete PostgreSQL-backed suite takes well over two hours on the
    # hosted runner. Keep both the job ceiling and the narrower backend-step
    # ceiling explicit so a healthy run is not terminated prematurely while a
    # genuinely hung run still fails closed.
    assert "timeout-minutes: 300" in quality
    assert "timeout-minutes: 240" in quality
    assert "scripts.run_postgresql_migration_cycle" not in quality
    assert "pytest tests/" not in quality
    assert "image: postgres:" not in quality
    assert "postgresql-client" not in quality
    assert 'KLM_KEEP_POSTGRES_QA: "1"' in quality
    assert "KLM_POSTGRES_QA_ENV_FILE" in quality


def test_history_secret_scan_is_fail_closed() -> None:
    quality = _quality_job()

    assert "git --no-banner --redact --exit-code 1" in quality
    assert "--config=/repo/.gitleaks.toml /repo" in quality


def test_quality_job_keeps_two_vitest_orders_without_duplicate_frontend_gates() -> None:
    quality = _quality_job()

    assert quality.count("npm test -- --no-file-parallelism") == 1
    assert quality.count("npm test\n") == 1
    assert "npm run lint" in quality
    assert "npm run verify:blackbox-ux" not in quality
    assert "npm run build:app" not in quality
    assert "npm run build:blackbox" not in quality


def test_browser_gate_builds_release_bundles_once_and_enables_internal_journeys() -> None:
    quality = _quality_job()
    playwright = PLAYWRIGHT.read_text(encoding="utf-8")

    for flag in (
        "KLM_E2E_PRODUCTION",
        "KLM_E2E_ROLE_JOURNEYS",
        "KLM_E2E_OPERATIONS",
        "KLM_E2E_CANCELLATION",
        "KLM_E2E_PAGINATION",
        "KLM_E2E_COMMERCIAL",
    ):
        assert f'{flag}: "1"' in quality

    assert "npm run test:e2e -- --reporter=line,junit" in quality
    assert "PLAYWRIGHT_JUNIT_OUTPUT_FILE" in quality
    assert "scripts/verify_junit_clean.py" in quality
    assert ".local/qa-evidence/browser-e2e/junit.xml" in quality
    assert "python -m uvicorn app.main:app" in quality
    assert 'export DATABASE_URL="$KLM_POSTGRES_QA_APP_ASYNC_URL"' in quality
    assert 'export DATABASE_URL_SYNC="$KLM_POSTGRES_QA_APP_SYNC_URL"' in quality
    assert "sqlite+aiosqlite" not in quality
    assert "scripts/run_local.py" not in quality
    assert 'current_user != "klm_app"' in quality
    assert 'version.startswith("17")' in quality
    assert playwright.count("npm run build:app") == 1
    assert playwright.count("npm run build:blackbox") == 1


def test_generic_secret_allowlists_never_exempt_an_entire_file() -> None:
    config = tomllib.loads(GITLEAKS.read_text(encoding="utf-8"))
    generic_rule = next(rule for rule in config["rules"] if rule["id"] == "generic-api-key")
    allowlists = generic_rule["allowlists"]

    assert allowlists
    for allowlist in allowlists:
        if "paths" not in allowlist:
            continue
        assert allowlist.get("condition") == "AND"
        assert allowlist.get("regexes"), allowlist["description"]
        assert allowlist.get("regexTarget") in {"match", "secret"}

    serialized = GITLEAKS.read_text(encoding="utf-8")
    assert "test_saas_benefits_service 2" in serialized
    assert "schedule-key-123" in serialized
    assert "tiktok-ignore-secret-at-least-32-characters" in serialized
    assert "catalogKey" in serialized

    env_allowlist = next(
        allowlist
        for allowlist in allowlists
        if "retired verification-token placeholder" in allowlist["description"]
    )
    assert env_allowlist["regexes"] == [
        r"^klm-waba-verify-2025$"
    ]
    assert env_allowlist["regexTarget"] == "secret"
    realistic_secret = "prod-" + "test-token-" + "R4nd0mValueWithEntropy987654"
    assert re.fullmatch(env_allowlist["regexes"][0], realistic_secret) is None


def test_postgresql_diagnostics_precede_failure_artifact_and_credentials_are_removed() -> None:
    quality = _quality_job()

    capture = quality.index("- name: Capture retained PostgreSQL diagnostics")
    artifact = quality.index("- name: Preserve failure diagnostics")
    cleanup = quality.index("- name: Stop isolated processes and retained PostgreSQL")
    assert capture < artifact < cleanup
    assert ".local/qa-evidence/postgresql-qa/container.log" in quality
    assert 'rm -f "$env_file"' in quality
