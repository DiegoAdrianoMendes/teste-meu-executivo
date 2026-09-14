from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import verify_saas_v5_p1 as verifier


def baseline(digest: str, *, ready: bool = True) -> dict[str, object]:
    return {
        "sourceDigest": digest,
        "sourceFileCount": 42,
        "snapshotReady": ready,
        "secretScan": {"findingCount": 0},
        "upstreams": {"allMatchLock": True},
        "git": {"head": "a" * 40, "dirty": True},
    }


def test_verify_binds_all_suites_to_a_stable_source_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path
    frontend = root / "frontend"
    frontend.mkdir()
    suites = (
        verifier.Suite("backend", ("pytest",), root),
        verifier.Suite("frontend", ("npm", "test"), frontend),
    )
    captures = iter((baseline("d" * 64), baseline("d" * 64)))

    def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["cwd"] in {root, frontend}
        return SimpleNamespace(returncode=0, stdout=f"ok {' '.join(command)}\n")

    monkeypatch.setattr(verifier, "ROOT", root)
    monkeypatch.setattr(verifier, "DEFAULT_OUTPUT", root / "baseline.json")
    monkeypatch.setattr(verifier, "MANIFEST_PATH", root / "p1.json")
    monkeypatch.setattr(verifier, "EVIDENCE_DIR", root / ".local" / "evidence")
    monkeypatch.setattr(verifier, "SUITES", suites)
    monkeypatch.setattr(verifier, "capture", lambda _root: next(captures))
    monkeypatch.setattr(verifier.subprocess, "run", fake_run)

    manifest, exit_code = verifier.verify()

    assert exit_code == 0
    assert manifest["status"] == "passed"
    assert manifest["source"]["stableDuringVerification"] is True
    assert [item["name"] for item in manifest["suites"]] == ["backend", "frontend"]
    assert json.loads((root / "baseline.json").read_text(encoding="utf-8"))[
        "sourceDigest"
    ] == "d" * 64


def test_verify_fails_closed_when_source_changes_and_preserves_last_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text('{"state":"previous"}\n', encoding="utf-8")
    captures = iter((baseline("a" * 64), baseline("b" * 64)))
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    monkeypatch.setattr(verifier, "DEFAULT_OUTPUT", baseline_path)
    monkeypatch.setattr(verifier, "MANIFEST_PATH", tmp_path / "p1.json")
    monkeypatch.setattr(verifier, "EVIDENCE_DIR", tmp_path / ".local" / "evidence")
    monkeypatch.setattr(
        verifier,
        "SUITES",
        (verifier.Suite("unit", ("pytest",), tmp_path),),
    )
    monkeypatch.setattr(verifier, "capture", lambda _root: next(captures))
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="ok\n"),
    )

    manifest, exit_code = verifier.verify()

    assert exit_code == 1
    assert manifest["status"] == "failed"
    assert manifest["source"]["stableDuringVerification"] is False
    assert baseline_path.read_text(encoding="utf-8") == '{"state":"previous"}\n'


def test_verify_stops_after_the_first_failed_suite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    monkeypatch.setattr(verifier, "DEFAULT_OUTPUT", tmp_path / "baseline.json")
    monkeypatch.setattr(verifier, "MANIFEST_PATH", tmp_path / "p1.json")
    monkeypatch.setattr(verifier, "EVIDENCE_DIR", tmp_path / ".local" / "evidence")
    monkeypatch.setattr(
        verifier,
        "SUITES",
        (
            verifier.Suite("failure", ("false",), tmp_path),
            verifier.Suite("must-not-run", ("true",), tmp_path),
        ),
    )
    monkeypatch.setattr(verifier, "capture", lambda _root: baseline("c" * 64))

    def fake_run(command, **_kwargs):  # type: ignore[no-untyped-def]
        calls.append(tuple(command))
        return SimpleNamespace(returncode=1, stdout="failed\n")

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)

    manifest, exit_code = verifier.verify()

    assert exit_code == 1
    assert calls == [("false",)]
    assert [item["name"] for item in manifest["suites"]] == ["failure"]


def test_backend_static_gate_targets_functional_and_import_failures() -> None:
    suite = next(item for item in verifier.SUITES if item.name == "backend-static")

    assert suite.command[:5] == (
        ".venv/bin/ruff",
        "check",
        "--select",
        "F,I",
        "app/ai",
    )


def test_p1_gate_cannot_skip_storage_premium_or_database_role_contracts() -> None:
    assert {
        "tests/api/test_document_storage_convergence.py",
        "tests/api/test_klm_operations.py",
        "tests/api/test_matter_tenant_references.py",
        "tests/api/test_premium_feature_gates.py",
        "tests/services/test_storage_p1_whatsapp.py",
        "tests/services/test_whatsapp_inbound_pipeline.py",
        "tests/test_production_database_roles.py",
        "tests/test_saas_p1_tenant_model_constraints.py",
    }.issubset(verifier.BACKEND_TESTS)

    static_suite = next(
        item for item in verifier.SUITES if item.name == "backend-static"
    )
    assert {
        "app/services/finance_service.py",
        "app/models/client.py",
        "app/models/matter.py",
        "app/services/agenda_service.py",
        "app/services/whatsapp/inbox_service.py",
        "app/services/whatsapp/webhook_service.py",
        "app/workers/flow_tasks.py",
    }.issubset(static_suite.command)

    assert "src/test/platform-support-access-panel.test.tsx" in verifier.FRONTEND_TESTS
    assert "src/test/app-context-realtime.test.tsx" in verifier.FRONTEND_TESTS
    assert "src/test/support-workspace.test.tsx" in verifier.FRONTEND_TESTS
    assert {
        "app/models/identity.py",
        "app/services/auth_revocation_service.py",
        "app/services/identity_lifecycle_service.py",
        "app/services/identity_outbox_service.py",
    }.issubset(static_suite.command)
