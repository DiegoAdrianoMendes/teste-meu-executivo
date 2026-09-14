"""License-scope resolution for source-root and package-prefixed census units."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from scripts.oss_census.common import ExtractionError, sha256_bytes
from scripts.oss_census.git_source import GitTreeEntry


def _package_scope(
    root: Path,
    manifest_path: Path,
    entries: Mapping[str, GitTreeEntry],
) -> dict[str, Any]:
    relative = manifest_path.relative_to(root).as_posix()
    try:
        package = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExtractionError(f"Invalid package license metadata at {relative}: {exc}") from exc
    if not isinstance(package, dict):
        raise ExtractionError(f"Package metadata must be an object: {relative}")
    name = package.get("name")
    version = package.get("version")
    license_id = package.get("license")
    if not all(isinstance(value, str) and value for value in (name, version)):
        raise ExtractionError(
            f"Package metadata is incomplete at {relative}: name/version required"
        )
    raw = manifest_path.read_bytes()
    entry = entries.get(relative)
    evidence = {
        "kind": "package-metadata",
        "path": relative,
        "sha256": sha256_bytes(raw),
        "verifiedFrom": "git_commit_tree" if entry is not None else "unverified_filesystem",
    }
    if entry is not None:
        evidence["gitBlob"] = entry.oid
    verified = isinstance(license_id, str) and bool(license_id)
    return {
        "prefix": manifest_path.parent.relative_to(root).as_posix(),
        "status": "package-metadata-only" if verified else "package-license-unverified",
        "conclusion": (
            f"{name}@{version} declares {license_id} in package metadata; repository-root "
            "license scope remains unverified."
            if verified
            else f"{name}@{version} does not declare a package license."
        ),
        "package": {"name": name, "version": version, "license": license_id},
        "evidence": [evidence],
    }


def build_license_scopes(
    root: Path,
    pin: dict[str, Any],
    entries: Mapping[str, GitTreeEntry] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Build deterministic longest-prefix license scopes for one pinned source."""

    entries = entries or {}
    license_data = pin["license"]
    if license_data["status"] == "verified-file":
        return (
            {
                "prefix": "",
                "status": "verified-file",
                "conclusion": license_data["conclusion"],
                "package": None,
                "evidence": license_data.get("evidence", []),
            },
        )
    if license_data["status"] != "package-metadata-only":
        raise ExtractionError(
            f"Unsupported license status for {pin['name']}: {license_data['status']}"
        )
    manifests = sorted(root.glob("packages/*/package.json"))
    if not manifests:
        raise ExtractionError(f"No package license manifests found for {pin['name']}")
    package_scopes = tuple(_package_scope(root, manifest, entries) for manifest in manifests)
    scopes = (
        {
            "prefix": "",
            "status": "root-license-absent",
            "conclusion": license_data["conclusion"],
            "package": None,
            "evidence": [],
        },
        *package_scopes,
    )
    prefixes = [scope["prefix"] for scope in scopes]
    if len(prefixes) != len(set(prefixes)):
        raise ExtractionError(f"Duplicate package license prefixes for {pin['name']}")
    return scopes


def resolve_license_scope(path: str, scopes: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    matches = [
        scope
        for scope in scopes
        if not scope["prefix"]
        or path == scope["prefix"]
        or path.startswith(scope["prefix"] + "/")
    ]
    if not matches:
        raise ExtractionError(f"No license scope covers census source: {path}")
    matches.sort(key=lambda scope: len(scope["prefix"]), reverse=True)
    if len(matches) > 1 and len(matches[0]["prefix"]) == len(matches[1]["prefix"]):
        raise ExtractionError(f"Ambiguous license scopes cover census source: {path}")
    return matches[0]
