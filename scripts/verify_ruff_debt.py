#!/usr/bin/env python3
"""Enforce a reviewable Ruff debt ratchet without hiding new violations."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "app/data/saas_v5/ruff-debt-baseline.json"
DEFAULT_OUTPUT = ROOT / ".local/qa-evidence/saas-v5-p5/ruff-ratchet.json"
PROJECT_RUFF = ROOT / ".venv/bin/ruff"
SCRIPT_VERSION = "klm-ruff-debt-ratchet-v1"


class RuffDebtError(RuntimeError):
    """Raised when Ruff or the debt ledger cannot be verified safely."""


def resolve_ruff() -> Path:
    """Resolve Ruff in local virtualenvs and portable CI environments."""

    configured = os.environ.get("KLM_RUFF_BIN")
    candidates = [Path(configured).expanduser()] if configured else []
    candidates.append(PROJECT_RUFF)
    discovered = shutil.which("ruff")
    if discovered:
        candidates.append(Path(discovered))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise RuffDebtError(
        "Ruff executable was not found. Set KLM_RUFF_BIN, install it in .venv, "
        "or expose it on PATH."
    )


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ruff_config_sha256(path: Path) -> str:
    """Fingerprint only Ruff policy, not unrelated project metadata/dependencies."""

    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise RuffDebtError(f"Cannot read Ruff configuration: {path}") from error
    ruff = payload.get("tool", {}).get("ruff")
    if not isinstance(ruff, dict) or not ruff:
        raise RuffDebtError("Ruff configuration is missing from pyproject.toml.")
    return _digest(ruff)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _relative_filename(value: str, root: Path) -> str:
    path = Path(value)
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise RuffDebtError(f"Ruff reported a file outside the repository: {value}") from error


def _source_excerpt(path: Path, diagnostic: dict[str, Any]) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as error:
        raise RuffDebtError(f"Cannot read Ruff source file: {path}") from error
    location = diagnostic.get("location") or {}
    end_location = diagnostic.get("end_location") or location
    start = max(0, int(location.get("row", 1)) - 1)
    end = max(start + 1, int(end_location.get("row", start + 1)))
    return "\n".join(line.rstrip() for line in lines[start:end]).strip()


def normalize_diagnostics(
    diagnostics: list[dict[str, Any]],
    *,
    root: Path = ROOT,
) -> tuple[Counter[str], list[dict[str, Any]]]:
    """Return line-number-independent fingerprints plus safe review metadata."""

    fingerprints: Counter[str] = Counter()
    normalized: list[dict[str, Any]] = []
    for diagnostic in diagnostics:
        code = str(diagnostic.get("code") or "")
        message = str(diagnostic.get("message") or "")
        filename = _relative_filename(str(diagnostic.get("filename") or ""), root)
        location = diagnostic.get("location") or {}
        excerpt = _source_excerpt(root / filename, diagnostic)
        identity = {
            "code": code,
            "message": message,
            "path": filename,
            "source": excerpt,
        }
        fingerprint = _digest(identity)
        fingerprints[fingerprint] += 1
        normalized.append(
            {
                "code": code,
                "fingerprint": fingerprint,
                "line": int(location.get("row", 0)),
                "message": message,
                "path": filename,
            }
        )
    return fingerprints, normalized


def _ruff_version(ruff: Path) -> str:
    completed = subprocess.run(
        (str(ruff), "--version"),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise RuffDebtError("Ruff version could not be determined.")
    return completed.stdout.strip()


def run_ruff(*, root: Path = ROOT, ruff: Path) -> list[dict[str, Any]]:
    completed = subprocess.run(
        (
            str(ruff),
            "check",
            ".",
            "--output-format=json",
            "--exit-zero",
            "--no-cache",
        ),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    if completed.returncode != 0:
        raise RuffDebtError(f"Ruff execution failed with exit code {completed.returncode}.")
    try:
        payload = json.loads(completed.stdout)
    except ValueError as error:
        raise RuffDebtError("Ruff did not return valid JSON.") from error
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise RuffDebtError("Ruff JSON output has an unexpected shape.")
    return payload


def build_baseline(
    diagnostics: list[dict[str, Any]],
    *,
    root: Path = ROOT,
    ruff_version: str,
) -> dict[str, Any]:
    fingerprints, normalized = normalize_diagnostics(diagnostics, root=root)
    by_code = Counter(item["code"] for item in normalized)
    by_path = Counter(item["path"] for item in normalized)
    payload: dict[str, Any] = {
        "schemaVersion": "1.0",
        "kind": "klm-ruff-debt-baseline",
        "generatorVersion": SCRIPT_VERSION,
        "capturedAt": datetime.now(UTC).isoformat(),
        "ruffVersion": ruff_version,
        "configPath": "pyproject.toml",
        "configSha256": _ruff_config_sha256(root / "pyproject.toml"),
        "configFingerprintKind": "tool.ruff",
        "scope": ".",
        "policy": {
            "newViolationsAllowed": 0,
            "resolvedViolationsMayReturn": False,
            "fingerprintFields": ["path", "code", "message", "source"],
            "lineNumbersExcluded": True,
            "multiplicityEnforced": True,
        },
        "findingCount": sum(fingerprints.values()),
        "fingerprints": dict(sorted(fingerprints.items())),
        "byCode": dict(sorted(by_code.items())),
        "byPath": dict(sorted(by_path.items())),
    }
    payload["baselineDigest"] = _digest(payload)
    return payload


def _load_baseline(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuffDebtError("Ruff debt baseline is missing or invalid.") from error
    if not isinstance(payload, dict) or payload.get("kind") != "klm-ruff-debt-baseline":
        raise RuffDebtError("Ruff debt baseline has an unexpected kind.")
    expected_digest = payload.get("baselineDigest")
    digest_input = dict(payload)
    digest_input.pop("baselineDigest", None)
    if expected_digest != _digest(digest_input):
        raise RuffDebtError("Ruff debt baseline digest does not match its contents.")
    fingerprints = payload.get("fingerprints")
    if not isinstance(fingerprints, dict) or not fingerprints:
        raise RuffDebtError("Ruff debt baseline fingerprint inventory is empty.")
    if sum(int(value) for value in fingerprints.values()) != payload.get("findingCount"):
        raise RuffDebtError("Ruff debt baseline counts do not reconcile.")
    return payload


def evaluate(
    baseline: dict[str, Any],
    diagnostics: list[dict[str, Any]],
    *,
    root: Path = ROOT,
    ruff_version: str,
) -> dict[str, Any]:
    if baseline.get("ruffVersion") != ruff_version:
        raise RuffDebtError("Ruff version differs from the reviewed debt baseline.")
    if baseline.get("configFingerprintKind") != "tool.ruff":
        raise RuffDebtError("Ruff configuration fingerprint kind is not supported.")
    if baseline.get("configSha256") != _ruff_config_sha256(root / "pyproject.toml"):
        raise RuffDebtError("Ruff configuration differs from the reviewed debt baseline.")

    current, normalized = normalize_diagnostics(diagnostics, root=root)
    allowed = Counter(
        {str(key): int(value) for key, value in baseline["fingerprints"].items()}
    )
    new = current - allowed
    resolved = allowed - current
    new_ids = set(new)
    new_examples = [item for item in normalized if item["fingerprint"] in new_ids][:25]
    passed = not new
    report: dict[str, Any] = {
        "schemaVersion": "1.0",
        "kind": "klm-ruff-debt-ratchet",
        "verifierVersion": SCRIPT_VERSION,
        "generatedAt": datetime.now(UTC).isoformat(),
        "status": "passed" if passed else "failed",
        "scope": ".",
        "ruffVersion": ruff_version,
        "configSha256": baseline["configSha256"],
        "baselineDigest": baseline["baselineDigest"],
        "baselineFindingCount": sum(allowed.values()),
        "currentFindingCount": sum(current.values()),
        "newViolationCount": sum(new.values()),
        "resolvedViolationCount": sum(resolved.values()),
        "newExamples": new_examples,
        "policy": baseline["policy"],
    }
    report["reportDigest"] = _digest(report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    baseline_path = (
        arguments.baseline if arguments.baseline.is_absolute() else ROOT / arguments.baseline
    )
    output_path = arguments.output if arguments.output.is_absolute() else ROOT / arguments.output
    try:
        ruff = resolve_ruff()
        version = _ruff_version(ruff)
        diagnostics = run_ruff(ruff=ruff)
        if arguments.write_baseline:
            if baseline_path.exists() and not arguments.force:
                raise RuffDebtError("Ruff debt baseline already exists; use --force explicitly.")
            baseline = build_baseline(diagnostics, ruff_version=version)
            _atomic_json(baseline_path, baseline)
            print(
                json.dumps(
                    {
                        "baseline": str(baseline_path.relative_to(ROOT)),
                        "baselineDigest": baseline["baselineDigest"],
                        "findingCount": baseline["findingCount"],
                    },
                    sort_keys=True,
                )
            )
            return 0

        baseline = _load_baseline(baseline_path)
        report = evaluate(baseline, diagnostics, ruff_version=version)
        _atomic_json(output_path, report)
        print(
            json.dumps(
                {
                    "baselineFindings": report["baselineFindingCount"],
                    "currentFindings": report["currentFindingCount"],
                    "newViolations": report["newViolationCount"],
                    "output": str(output_path),
                    "status": report["status"],
                },
                sort_keys=True,
            )
        )
        return 0 if report["status"] == "passed" else 1
    except (OSError, RuffDebtError, subprocess.SubprocessError) as error:
        print(f"RUFF_DEBT_RATCHET_REFUSED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
