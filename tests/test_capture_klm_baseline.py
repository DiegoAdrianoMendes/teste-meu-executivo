from __future__ import annotations

from pathlib import Path

from scripts.capture_klm_baseline import (
    _display_path,
    _file_entry,
    _iter_source_files,
    _scan_secrets,
    _source_digest,
)


def test_display_path_accepts_outputs_inside_and_outside_the_repository(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    inside = root / "evidence" / "baseline.json"
    outside = tmp_path / "external" / "baseline.json"

    assert _display_path(inside, root) == "evidence/baseline.json"
    assert _display_path(outside, root) == str(outside.resolve())


def test_source_inventory_excludes_local_runtime_and_vendor_material(tmp_path: Path) -> None:
    source = tmp_path / "app" / "service.py"
    source.parent.mkdir()
    source.write_text("print('source')\n", encoding="utf-8")
    vendor = tmp_path / ".vendor" / "upstream.py"
    vendor.parent.mkdir()
    vendor.write_text("print('vendor')\n", encoding="utf-8")
    local = tmp_path / ".local" / "runtime.json"
    local.parent.mkdir()
    local.write_text("{}\n", encoding="utf-8")

    paths = [path.relative_to(tmp_path).as_posix() for path in _iter_source_files(tmp_path)]

    assert paths == ["app/service.py"]


def test_source_inventory_excludes_generated_proof_and_baseline_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "app" / "service.py"
    source.parent.mkdir()
    source.write_text("print('source')\n", encoding="utf-8")
    baseline = tmp_path / "app" / "data" / "saas_v5" / "baseline.json"
    baseline.parent.mkdir(parents=True)
    baseline.write_text('{"capturedAt":"first"}\n', encoding="utf-8")
    manifest = tmp_path / "app" / "data" / "oss_native_verification.json"
    manifest.write_text('{"generatedAt":"first"}\n', encoding="utf-8")
    proof = tmp_path / "app" / "data" / "oss_native_proofs" / "backend.json"
    proof.parent.mkdir(parents=True)
    proof.write_text('{"status":"passed"}\n', encoding="utf-8")
    p5_manifest = tmp_path / "app" / "data" / "saas_v5" / "p5-verification.json"
    p5_manifest.write_text('{"status":"first"}\n', encoding="utf-8")

    first = [_file_entry(path, tmp_path) for path in _iter_source_files(tmp_path)]
    baseline.write_text('{"capturedAt":"second"}\n', encoding="utf-8")
    manifest.write_text('{"generatedAt":"second"}\n', encoding="utf-8")
    proof.write_text('{"status":"replayed"}\n', encoding="utf-8")
    p5_manifest.write_text('{"status":"second"}\n', encoding="utf-8")
    second = [_file_entry(path, tmp_path) for path in _iter_source_files(tmp_path)]

    assert [item["path"] for item in first] == ["app/service.py"]
    assert _source_digest(first) == _source_digest(second)


def test_source_inventory_excludes_mutable_coverage_databases(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text("print('source')\n", encoding="utf-8")
    (tmp_path / ".coverage").write_bytes(b"sqlite coverage payload")
    (tmp_path / ".coverage.worker-1").write_bytes(b"parallel coverage payload")

    paths = [path.relative_to(tmp_path).as_posix() for path in _iter_source_files(tmp_path)]

    assert paths == ["app.py"]


def test_source_digest_changes_with_content_and_is_path_sensitive(tmp_path: Path) -> None:
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("same\n", encoding="utf-8")
    second.write_text("same\n", encoding="utf-8")
    before = _source_digest([_file_entry(first, tmp_path), _file_entry(second, tmp_path)])

    second.write_text("changed\n", encoding="utf-8")
    after = _source_digest([_file_entry(first, tmp_path), _file_entry(second, tmp_path)])

    assert before != after


def test_secret_scan_records_location_without_persisting_secret_value(tmp_path: Path) -> None:
    source = tmp_path / "unsafe.txt"
    secret = "AKIA" + "A" * 16
    source.write_text(f"line one\n{secret}\n", encoding="utf-8")

    findings = _scan_secrets(source, tmp_path)

    assert findings == [{"path": "unsafe.txt", "line": 2, "kind": "aws_access_key"}]
    assert secret not in str(findings)


def test_secret_scan_ignores_only_the_exact_redaction_fixture(tmp_path: Path) -> None:
    source = tmp_path / "redaction-fixture.txt"
    production_shaped_fixture = "sk-" + "A" * 24
    source.write_text(
        "sk-this-value-must-never-escape\n"
        f"{production_shaped_fixture}\n",
        encoding="utf-8",
    )

    findings = _scan_secrets(source, tmp_path)

    assert findings == [
        {"path": "redaction-fixture.txt", "line": 2, "kind": "openai_key"}
    ]
