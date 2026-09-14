#!/usr/bin/env python3
"""Capture a reviewable V5 baseline without vendoring local caches or secrets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "app" / "data" / "saas_v5" / "baseline.json"
UPSTREAM_LOCK = ROOT / "docs" / "oss-upstreams.lock.json"

EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".local",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        ".vendor",
        "__pycache__",
        "build",
        "dist",
        "htmlcov",
        "node_modules",
        "playwright-report",
        "test-results",
    }
)
EXCLUDED_SUFFIXES = frozenset({".db", ".key", ".log", ".pem", ".pyc", ".sqlite", ".sqlite3"})
EXCLUDED_NAMES = frozenset({".coverage", ".env", ".DS_Store"})
GENERATED_ARTIFACT_PATHS = frozenset(
    {
        "app/data/oss_native_verification.json",
        "app/data/saas_v5/baseline.json",
        "app/data/saas_v5/p1-verification.json",
        "app/data/saas_v5/p5-verification.json",
    }
)
GENERATED_ARTIFACT_PREFIXES = (
    "app/data/oss_native_proofs/",
)

SECRET_PATTERNS = (
    ("private_key", re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("aws_access_key", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    ("openai_key", re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b")),
    ("github_token", re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("slack_token", re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
)

# Exact values deliberately used by local tests to prove redaction behavior.
# Keep this allowlist value-specific so production-shaped tokens still fail closed.
SAFE_SYNTHETIC_SECRETS = frozenset({b"sk-this-value-must-never-escape"})


def _run_git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=cwd,
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()


def _is_included(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    relative_path = relative.as_posix()
    if any(part in EXCLUDED_PARTS for part in relative.parts):
        return False
    if relative_path in GENERATED_ARTIFACT_PATHS:
        return False
    if any(relative_path.startswith(prefix) for prefix in GENERATED_ARTIFACT_PREFIXES):
        return False
    if (
        path.name in EXCLUDED_NAMES
        or path.name.startswith(".coverage.")
        or path.suffix.lower() in EXCLUDED_SUFFIXES
    ):
        return False
    return path.is_file() and not path.is_symlink()


def _iter_source_files(root: Path) -> Iterable[Path]:
    for directory, names, files in os.walk(root):
        base = Path(directory)
        names[:] = sorted(name for name in names if name not in EXCLUDED_PARTS)
        for name in sorted(files):
            path = base / name
            if _is_included(path, root):
                yield path


def _file_entry(path: Path, root: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _source_digest(files: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for item in files:
        digest.update(str(item["path"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(item["size"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(item["sha256"]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _scan_secrets(path: Path, root: Path) -> list[dict[str, Any]]:
    if path.stat().st_size > 2 * 1024 * 1024:
        return []
    payload = path.read_bytes()
    if b"\0" in payload:
        return []
    findings: list[dict[str, Any]] = []
    for kind, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(payload):
            if match.group(0) in SAFE_SYNTHETIC_SECRETS:
                continue
            findings.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "line": payload.count(b"\n", 0, match.start()) + 1,
                    "kind": kind,
                }
            )
    return findings


def _git_state(root: Path) -> dict[str, Any]:
    status = _run_git("status", "--porcelain=v1", "--untracked-files=all", cwd=root)
    entries = [line for line in status.splitlines() if line]
    return {
        "head": _run_git("rev-parse", "HEAD", cwd=root),
        "branch": _run_git("branch", "--show-current", cwd=root),
        "dirty": bool(entries),
        "entryCount": len(entries),
        "entries": entries,
    }


def _upstream_state(root: Path) -> dict[str, Any]:
    lock_path = root / UPSTREAM_LOCK.relative_to(ROOT)
    lock_payload = lock_path.read_bytes()
    lock = json.loads(lock_payload)
    sources: list[dict[str, Any]] = []
    for source in lock.get("sources", []):
        name = str(source["name"])
        checkout = root / ".vendor" / "upstreams" / name
        actual_commit: str | None = None
        clean: bool | None = None
        if checkout.exists():
            actual_commit = _run_git("rev-parse", "HEAD", cwd=checkout)
            clean = not bool(
                _run_git("status", "--porcelain=v1", "--untracked-files=all", cwd=checkout)
            )
        sources.append(
            {
                "name": name,
                "ref": source["ref"],
                "expectedCommit": source["commit"],
                "actualCommit": actual_commit,
                "materialized": checkout.exists(),
                "clean": clean,
                "matchesLock": actual_commit == source["commit"] and clean is True,
                "licenseStatus": source.get("license", {}).get("status"),
            }
        )
    return {
        "lockPath": lock_path.relative_to(root).as_posix(),
        "lockSha256": hashlib.sha256(lock_payload).hexdigest(),
        "sources": sources,
        "allMatchLock": all(item["matchesLock"] for item in sources),
    }


def capture(root: Path = ROOT) -> dict[str, Any]:
    files = [_file_entry(path, root) for path in _iter_source_files(root)]
    findings = [
        finding
        for path in _iter_source_files(root)
        for finding in _scan_secrets(path, root)
    ]
    upstreams = _upstream_state(root)
    return {
        "schemaVersion": "1.0",
        "kind": "klm-saas-v5-baseline",
        "capturedAt": datetime.now(UTC).isoformat(),
        "sourceDigest": _source_digest(files),
        "sourceFileCount": len(files),
        "excludedRoots": sorted(EXCLUDED_PARTS),
        "git": _git_state(root),
        "upstreams": upstreams,
        "secretScan": {
            "scope": "high-confidence-token-patterns; values are never persisted",
            "findingCount": len(findings),
            "findings": findings,
        },
        "snapshotReady": not findings and upstreams["allMatchLock"],
        "files": files,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _display_path(path: Path, root: Path = ROOT) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--assert-ready", action="store_true")
    args = parser.parse_args()

    baseline = capture(ROOT)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    _write_json(output, baseline)
    print(
        json.dumps(
            {
                "output": _display_path(output),
                "sourceDigest": baseline["sourceDigest"],
                "sourceFileCount": baseline["sourceFileCount"],
                "gitEntries": baseline["git"]["entryCount"],
                "secretFindings": baseline["secretScan"]["findingCount"],
                "upstreamsMatch": baseline["upstreams"]["allMatchLock"],
                "snapshotReady": baseline["snapshotReady"],
            },
            sort_keys=True,
        )
    )
    if args.assert_ready and not baseline["snapshotReady"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
