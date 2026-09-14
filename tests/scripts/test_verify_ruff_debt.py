from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts import verify_ruff_debt as ratchet


def _diagnostic(path: Path, *, code: str = "E501", message: str = "Line too long"):
    return {
        "code": code,
        "filename": str(path),
        "location": {"row": 1, "column": 1},
        "end_location": {"row": 1, "column": 5},
        "message": message,
    }


def _root(tmp_path: Path) -> tuple[Path, Path]:
    config = tmp_path / "pyproject.toml"
    config.write_text("[tool.ruff]\nline-length = 100\n", encoding="utf-8")
    source = tmp_path / "legacy.py"
    source.write_text("legacy = True\n", encoding="utf-8")
    return config, source


def test_ratchet_accepts_identical_debt_and_monotonic_reduction(tmp_path: Path) -> None:
    _config, source = _root(tmp_path)
    diagnostics = [_diagnostic(source), _diagnostic(source)]
    baseline = ratchet.build_baseline(
        diagnostics,
        root=tmp_path,
        ruff_version="ruff 0.test",
    )

    identical = ratchet.evaluate(
        baseline,
        diagnostics,
        root=tmp_path,
        ruff_version="ruff 0.test",
    )
    reduced = ratchet.evaluate(
        baseline,
        diagnostics[:1],
        root=tmp_path,
        ruff_version="ruff 0.test",
    )

    assert identical["status"] == "passed"
    assert identical["newViolationCount"] == 0
    assert reduced["status"] == "passed"
    assert reduced["resolvedViolationCount"] == 1


def test_ratchet_rejects_new_fingerprint_even_when_total_debt_falls(tmp_path: Path) -> None:
    _config, source = _root(tmp_path)
    baseline = ratchet.build_baseline(
        [_diagnostic(source), _diagnostic(source)],
        root=tmp_path,
        ruff_version="ruff 0.test",
    )
    replacement = _diagnostic(source, code="F821", message="Undefined name")

    report = ratchet.evaluate(
        baseline,
        [replacement],
        root=tmp_path,
        ruff_version="ruff 0.test",
    )

    assert report["status"] == "failed"
    assert report["currentFindingCount"] < report["baselineFindingCount"]
    assert report["newViolationCount"] == 1
    assert report["newExamples"][0]["code"] == "F821"


def test_baseline_digest_and_tool_configuration_fail_closed(tmp_path: Path) -> None:
    config, source = _root(tmp_path)
    baseline = ratchet.build_baseline(
        [_diagnostic(source)],
        root=tmp_path,
        ruff_version="ruff 0.test",
    )
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(baseline), encoding="utf-8")
    baseline["findingCount"] = 2
    path.write_text(json.dumps(baseline), encoding="utf-8")

    with pytest.raises(ratchet.RuffDebtError, match="digest"):
        ratchet._load_baseline(path)

    valid = ratchet.build_baseline(
        [_diagnostic(source)],
        root=tmp_path,
        ruff_version="ruff 0.test",
    )
    config.write_text("[tool.ruff]\nline-length = 88\n", encoding="utf-8")
    with pytest.raises(ratchet.RuffDebtError, match="configuration"):
        ratchet.evaluate(
            valid,
            [_diagnostic(source)],
            root=tmp_path,
            ruff_version="ruff 0.test",
        )


def test_ratchet_ignores_unrelated_pyproject_changes(tmp_path: Path) -> None:
    config, source = _root(tmp_path)
    baseline = ratchet.build_baseline(
        [_diagnostic(source)],
        root=tmp_path,
        ruff_version="ruff 0.test",
    )
    config.write_text(
        "[tool.poetry.dependencies]\npython = '^3.12'\nnew-package = '^1.0'\n\n"
        "[tool.ruff]\nline-length = 100\n",
        encoding="utf-8",
    )

    report = ratchet.evaluate(
        baseline,
        [_diagnostic(source)],
        root=tmp_path,
        ruff_version="ruff 0.test",
    )

    assert report["status"] == "passed"


def test_resolve_ruff_uses_ci_path_when_project_virtualenv_is_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "ruff"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.delenv("KLM_RUFF_BIN", raising=False)
    monkeypatch.setattr(ratchet, "PROJECT_RUFF", tmp_path / "missing-venv" / "ruff")
    monkeypatch.setattr(ratchet.shutil, "which", lambda name: str(executable))

    assert ratchet.resolve_ruff() == executable.resolve()


def test_resolve_ruff_prefers_explicit_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "explicit-ruff"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("KLM_RUFF_BIN", os.fspath(executable))

    assert ratchet.resolve_ruff() == executable.resolve()
