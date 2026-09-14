from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import verify_saas_v5_p2 as verifier


def baseline(digest: str) -> dict[str, object]:
    return {
        "sourceDigest": digest,
        "sourceFileCount": 50,
        "git": {"head": "b" * 40, "dirty": True},
    }


def contract(*, ready: bool = True) -> dict[str, object]:
    return {
        "mandatoryBlocks": 13,
        "objections": 14,
        "agents": 18,
        "cadenceCategories": 8,
        "autonomyModesTested": 4,
        "minimumInitialFeeBrl": 4000.0,
        "maximumInitialFeeBrl": None,
        "successOnlyAllowed": True,
        "externalActionsExecuted": 0,
        "contextualSurfaces": {},
        "contextualSurfacesReady": ready,
    }


def test_verify_binds_p2_to_a_stable_source_and_complete_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captures = iter((baseline("c" * 64), baseline("c" * 64)))
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    monkeypatch.setattr(verifier, "MANIFEST_PATH", tmp_path / "p2.json")
    monkeypatch.setattr(verifier, "EVIDENCE_DIR", tmp_path / ".local" / "evidence")
    monkeypatch.setattr(verifier, "SUITES", (verifier.Suite("unit", ("true",), tmp_path),))
    monkeypatch.setattr(verifier, "capture", lambda _root: next(captures))
    monkeypatch.setattr(verifier, "_contract_snapshot", contract)
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="ok\n"),
    )

    manifest, exit_code = verifier.verify()

    assert exit_code == 0
    assert manifest["status"] == "passed"
    assert manifest["source"]["stableDuringVerification"] is True
    assert manifest["contract"]["contextualSurfacesReady"] is True


def test_verify_fails_closed_when_a_contextual_surface_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    monkeypatch.setattr(verifier, "MANIFEST_PATH", tmp_path / "p2.json")
    monkeypatch.setattr(verifier, "EVIDENCE_DIR", tmp_path / ".local" / "evidence")
    monkeypatch.setattr(verifier, "SUITES", (verifier.Suite("unit", ("true",), tmp_path),))
    monkeypatch.setattr(verifier, "capture", lambda _root: baseline("d" * 64))
    monkeypatch.setattr(verifier, "_contract_snapshot", lambda: contract(ready=False))
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="ok\n"),
    )

    manifest, exit_code = verifier.verify()

    assert exit_code == 1
    assert manifest["status"] == "failed"
    assert manifest["contract"]["contextualSurfacesReady"] is False


def test_p2_gate_cannot_skip_lifecycle_rls_context_or_production_bundle() -> None:
    assert {
        "tests/services/test_playbook_service.py",
        "tests/api/test_playbooks.py",
        "tests/migrations/test_playbook_studio_migration.py",
        "tests/db/test_tenant_rls_sqlite.py",
    }.issubset(verifier.BACKEND_TESTS)
    assert {
        "src/test/effective-playbook-link.test.tsx",
        "src/test/conversations-tools.test.tsx",
        "src/test/lead-proposal-integrity.test.tsx",
    }.issubset(verifier.FRONTEND_TESTS)
    assert set(verifier.CONTEXTUAL_SURFACES) == {
        "conversations",
        "agents",
        "flows",
        "proposals",
        "areas",
    }
    assert any(suite.name == "postgresql-playbooks" for suite in verifier.SUITES)
    assert any(suite.name == "frontend-production" for suite in verifier.SUITES)
