from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from scripts import verify_oss_native_release as release


@pytest.mark.parametrize("marker", ["capabilities", "oss"])
def test_native_matrix_parser_accepts_current_and_retained_markers(marker: str) -> None:
    output = (
        f"[{marker}-native] p95=321ms actions=511\n"
        f'[{marker}-native-ids] ["communications.contacts","crm.accounts"]\n'
    )
    assert release._extract_native_matrix(output) == (
        321,
        511,
        ["communications.contacts", "crm.accounts"],
    )


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        ("Tests  391 passed (391)", (391, 0, 0)),
        ("Tests  2 failed | 4 skipped | 385 passed (391)", (385, 4, 2)),
        ("Tests  1 pending | 1 todo | 389 passed (391)", (389, 2, 0)),
    ],
)
def test_vitest_summary_parser_counts_every_reported_state(
    summary: str,
    expected: tuple[int, int, int],
) -> None:
    assert release._parse_vitest_test_summary(f"header\n{summary}\n") == expected


@pytest.mark.parametrize(
    "summary",
    [
        "Tests  390 passed (391)",
        "Tests  390 passed | 1 quarantined (391)",
        "Tests  391 passed",
    ],
)
def test_vitest_summary_parser_rejects_incomplete_or_unknown_evidence(summary: str) -> None:
    with pytest.raises(RuntimeError):
        release._parse_vitest_test_summary(summary)


def test_frontend_unit_proof_fails_closed_when_vitest_skips(monkeypatch) -> None:
    outputs = iter(
        [
            "typecheck ok",
            "lint ok",
            "build ok",
            "Tests  1 skipped | 390 passed (391)",
        ]
    )

    def fake_run(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return 0, next(outputs), 1

    monkeypatch.setattr(release, "_run", fake_run)
    with pytest.raises(RuntimeError, match="Vitest omitiu 1"):
        release._frontend_unit_proof(
            {"sourceDigest": "a" * 64, "sourceFileCount": 1}
        )

def test_source_binding_writes_a_deterministic_reconstructible_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.txt"
    source.write_bytes(b"exact reviewed source\n")
    proof_dir = tmp_path / "proofs"
    inventory_path = proof_dir / "source-inventory.json"
    snapshot_path = proof_dir / "source-snapshot.tar.gz"
    entry = {
        "path": "source.txt",
        "size": source.stat().st_size,
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    contract = {
        "sourceDigest": "d" * 64,
        "sourceFileCount": 1,
        "sourceInventory": [entry],
    }
    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setattr(release, "SOURCE_INVENTORY_PATH", inventory_path)
    monkeypatch.setattr(release, "SOURCE_SNAPSHOT_PATH", snapshot_path)

    first = release._write_source_binding_artifacts(
        contract,
        git_head="0" * 40,
        worktree_dirty=True,
    )
    first_snapshot = snapshot_path.read_bytes()
    second = release._write_source_binding_artifacts(
        contract,
        git_head="0" * 40,
        worktree_dirty=True,
    )

    assert first == second
    assert snapshot_path.read_bytes() == first_snapshot
    raw_tar = gzip.decompress(first_snapshot)
    with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:") as archive:
        assert archive.getnames() == ["source.txt"]
        stream = archive.extractfile("source.txt")
        assert stream is not None
        assert stream.read() == b"exact reviewed source\n"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    assert inventory["files"] == [entry]
    assert inventory["snapshotSha256"] == first["snapshot"]["sha256"]


def test_post_finalize_smoke_requires_verified_matrix_and_writes_local_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    evidence_path = tmp_path / "post-finalize.json"
    output_path = tmp_path / "post-finalize.log"
    manifest_path.write_text(
        json.dumps(
            {
                "schemaVersion": "1.2",
                "externalEffectsExecuted": 0,
                "externalNetworkAssertionsValidated": 511,
                "externalEffectEvidenceScope": "application_reported_dry_run",
                "externalEffectIndependentlyObserved": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output = (
        '[capabilities-native] p95=321ms actions=511\n'
        '[capabilities-native-ids] ["communications.contacts","crm.accounts"]\n'
        '  3 passed (2.1s)\n'
    )
    captured: dict[str, object] = {}

    def fake_run(command, *, cwd, env=None):  # type: ignore[no-untyped-def]
        captured.update(command=command, cwd=cwd, env=env)
        return 0, output, 2_100

    monkeypatch.setattr(release, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(release, "POST_FINALIZE_EVIDENCE_PATH", evidence_path)
    monkeypatch.setattr(release, "POST_FINALIZE_OUTPUT_PATH", output_path)
    monkeypatch.setattr(release, "_run", fake_run)
    contract = {
        "sourceDigest": "a" * 64,
        "sourceFileCount": 900,
        "nativeIds": ["chatwoot.contacts", "bottlecrm.accounts"],
        "publicNativeIds": ["communications.contacts", "crm.accounts"],
        "contractIds": ["chatwoot.contacts"],
        "actionOccurrences": 511,
    }

    evidence = release._post_finalize_smoke(contract)

    assert captured["env"] == {
        "KLM_REQUIRE_VERIFIED_OSS": "1",
        "KLM_E2E_PRODUCTION": "1",
        "KLM_DEV_API_TARGET": "http://127.0.0.1:8010",
        "KLM_FRONTEND_URL": "http://127.0.0.1:4273",
        "KLM_BLACKBOX_URL": "http://127.0.0.1:4274",
    }
    assert evidence["status"] == "passed"
    assert evidence["nativeExperienceVerified"] == 2
    assert evidence["contractCapabilitiesVerified"] == 1
    assert evidence["actionOccurrencesExposed"] == 511
    assert evidence["externalEffectsExecuted"] == 0
    assert evidence["externalNetworkAssertionsValidated"] == 511
    assert evidence["externalEffectEvidenceScope"] == "application_reported_dry_run"
    assert evidence["externalEffectIndependentlyObserved"] is False
    assert evidence["externalEffectObservationSource"] == "release_manifest"
    assert output_path.read_text(encoding="utf-8") == output
    assert json.loads(evidence_path.read_text(encoding="utf-8")) == evidence


def test_finalize_restores_previous_manifest_when_final_state_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    evidence_path = tmp_path / "post-finalize.json"
    previous = b'{"state":"previous"}\n'
    manifest_path.write_bytes(previous)
    evidence_path.write_text('{"status":"stale"}\n', encoding="utf-8")

    def fake_finalize(_contract):  # type: ignore[no-untyped-def]
        manifest_path.write_text('{"state":"candidate"}\n', encoding="utf-8")

    def fail_smoke(_contract):  # type: ignore[no-untyped-def]
        raise RuntimeError("final state unavailable")

    monkeypatch.setattr(release, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(release, "POST_FINALIZE_EVIDENCE_PATH", evidence_path)
    monkeypatch.setattr(release, "_finalize_manifest", fake_finalize)
    monkeypatch.setattr(release, "_post_finalize_smoke", fail_smoke)

    with pytest.raises(RuntimeError, match="final state unavailable"):
        release._finalize_and_verify({})

    assert manifest_path.read_bytes() == previous
    assert not evidence_path.exists()


def test_upstream_proof_accepts_the_actual_positive_pytest_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = iter(
        [
            "All 5 OSS upstreams match the lock.",
            "OK preflight: all",
            "....... 7 passed in 0.21s",
        ]
    )

    def fake_run(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return 0, next(outputs), 1

    monkeypatch.setattr(release, "_run", fake_run)
    monkeypatch.setattr(
        release,
        "_write_raw_artifact",
        lambda *_args, **_kwargs: {"path": "output.log", "sha256": "a" * 64},
    )

    proof = release._upstream_locks_proof(
        {"sourceDigest": "b" * 64, "sourceFileCount": 900}
    )

    assert proof["status"] == "passed"
    assert proof["sidecarOffProofPassed"] is True
    assert proof["sidecarOffTestsPassed"] == 7


def test_manifest_derives_qualified_external_effect_evidence_from_runtime_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proof_dir = tmp_path / "proofs"
    proof_dir.mkdir()
    manifest_path = tmp_path / "manifest.json"
    source_digest = "a" * 64
    for suite in release.SUITES:
        proof = {
            "proofVersion": "1.0",
            "suite": suite,
            "status": "passed",
            "exitCode": 0,
            "sourceDigest": source_digest,
            "worktreeDigest": source_digest,
            "sourceFileCount": 903,
            "sourceBindingMode": "content_snapshot",
            "gitHead": "0" * 40,
            "worktreeDirty": True,
            "generatedAt": "2026-07-18T12:00:00+00:00",
        }
        if suite == "local-runtime":
            proof.update(
                externalNetworkExecutions=0,
                externalNetworkAssertions=511,
                externalEffectEvidenceScope="application_reported_dry_run",
                externalEffectIndependentlyObserved=False,
            )
        if suite in {"backend", "frontend-unit", "frontend-e2e"}:
            proof["skipped"] = 0
        (proof_dir / f"{suite}.json").write_text(
            json.dumps(proof),
            encoding="utf-8",
        )

    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setattr(release, "PROOF_DIR", proof_dir)
    monkeypatch.setattr(release, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(release, "_require_unchanged_source", lambda _digest: None)
    monkeypatch.setattr(
        release,
        "_write_source_binding_artifacts",
        lambda *_args, **_kwargs: {
            "mode": "content_snapshot",
            "gitHead": "0" * 40,
            "worktreeDirty": True,
            "inventory": {"path": "inventory.json", "sha256": "a" * 64},
            "snapshot": {"path": "snapshot.tar.gz", "sha256": "b" * 64},
        },
    )
    contract = {
        "sourceDigest": source_digest,
        "sourceFileCount": 903,
        "actionOccurrences": 511,
        "uniqueCommands": 360,
        "contract": {"contractDigest": "verified"},
    }

    release._finalize_manifest(contract)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schemaVersion"] == "1.2"
    assert manifest["sourceBinding"]["mode"] == "content_snapshot"
    assert manifest["externalEffectsExecuted"] == 0
    assert manifest["externalNetworkAssertionsValidated"] == 511
    assert manifest["externalEffectEvidenceScope"] == "application_reported_dry_run"
    assert manifest["externalEffectIndependentlyObserved"] is False

    runtime_path = proof_dir / "local-runtime.json"
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    runtime["externalNetworkExecutions"] = 1
    runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
    with pytest.raises(RuntimeError, match="não sustenta"):
        release._finalize_manifest(contract)

    runtime["externalNetworkExecutions"] = 0
    runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
    backend_path = proof_dir / "backend.json"
    backend = json.loads(backend_path.read_text(encoding="utf-8"))
    backend["skipped"] = 1
    backend_path.write_text(json.dumps(backend), encoding="utf-8")
    with pytest.raises(RuntimeError, match="testes omitidos"):
        release._finalize_manifest(contract)
