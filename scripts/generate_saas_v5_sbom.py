#!/usr/bin/env python3
"""Generate the deterministic, offline CycloneDX evidence for SaaS V5 P5."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import socket
import sys
import uuid
from collections import Counter
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Never
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REQUIREMENTS_LOCK = ROOT / "requirements.lock"
DEFAULT_PACKAGE_LOCK = ROOT / "frontend/package-lock.json"
DEFAULT_UPSTREAM_LOCK = ROOT / "docs/oss-upstreams.lock.json"
DEFAULT_OUTPUT = ROOT / ".local/qa-evidence/saas-v5-p5/sbom.cdx.json"

SCRIPT_VERSION = "klm-saas-v5-p5-sbom-v1"
CYCLONEDX_SPEC_VERSION = "1.5"

EXPECTED_OSS_PINS: dict[str, dict[str, str]] = {
    "bottlecrm": {
        "commit": "3ab79d75ece005a414d9d3ef729b7240545d1b41",
        "license": "MIT",
        "licenseStatus": "verified-file",
        "repository": "https://github.com/MicroPyramid/Django-CRM.git",
    },
    "chattermate": {
        "commit": "9770df5b4d4909084a3b3a00ff2404d25724de90",
        "license": "Apache-2.0",
        "licenseStatus": "verified-file",
        "repository": "https://github.com/chattermate/chattermate.chat.git",
    },
    "chatwoot": {
        "commit": "97bb8ecd326299c46eacea74863976427c96fe94",
        "license": "MIT",
        "licenseStatus": "verified-file",
        "repository": "https://github.com/chatwoot/chatwoot.git",
    },
    "rocket-embedded-chat": {
        "commit": "06a0299680394b4b03f932e300bc52e52df83f80",
        "license": "ISC",
        "licenseStatus": "package-metadata-only",
        "repository": "https://github.com/RocketChat/EmbeddedChat.git",
    },
    "rocketchat": {
        "commit": "bfd782302d20cbd672c785ac8c0d072d7cc6b0db",
        "license": "MIT",
        "licenseStatus": "verified-file",
        "repository": "https://github.com/RocketChat/Rocket.Chat.git",
    },
}

_HASH_ALGORITHMS: dict[str, tuple[str, int]] = {
    "sha1": ("SHA-1", 20),
    "sha256": ("SHA-256", 32),
    "sha384": ("SHA-384", 48),
    "sha512": ("SHA-512", 64),
}
_PYTHON_PIN = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[(?P<extras>[^\]]+)\])?==(?P<version>[^\s;\\]+)"
    r"(?:\s*;\s*(?P<marker>.+))?$"
)
_PYTHON_HASH = re.compile(r"--hash=([A-Za-z0-9_-]+):([A-Fa-f0-9]+)")
_SPDX_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+-]*$")
_SPDX_EXPRESSION = re.compile(r"^[A-Za-z0-9.+()\-\s]+$")
_SPDX_OPERATORS = frozenset({"AND", "OR", "WITH"})
_KNOWN_SPDX_IDENTIFIERS = frozenset(
    {
        "0BSD",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "BlueOak-1.0.0",
        "CC-BY-4.0",
        "ISC",
        "MIT",
        "OFL-1.1",
        "Python-2.0",
    }
)


class SbomGenerationError(RuntimeError):
    """Base class for a fail-closed SBOM result."""


class LockValidationError(SbomGenerationError):
    """A source lock is missing, malformed, or internally conflicting."""


class ForbiddenLicenseError(SbomGenerationError):
    """A declared license violates the configured deny policy."""


class OfflineNetworkAttemptError(SbomGenerationError):
    """A code path attempted network access while the offline guard was active."""


class OfflineNetworkGuard(AbstractContextManager["OfflineNetworkGuard"]):
    """Deny socket resolution and transport while recording every attempt."""

    def __init__(self) -> None:
        self.attempted_operations: list[str] = []
        self._originals: list[tuple[Any, str, Any]] = []

    @property
    def attempts(self) -> int:
        return len(self.attempted_operations)

    def _blocked(self, operation: str) -> Any:
        def deny(*args: Any, **kwargs: Any) -> Never:
            del args, kwargs
            self.attempted_operations.append(operation)
            raise OfflineNetworkAttemptError(
                f"Network operation denied by the P5 offline guard: {operation}"
            )

        return deny

    def __enter__(self) -> OfflineNetworkGuard:
        targets: tuple[tuple[Any, str], ...] = (
            (socket, "create_connection"),
            (socket, "getaddrinfo"),
            (socket.socket, "connect"),
            (socket.socket, "connect_ex"),
            (socket.socket, "sendto"),
        )
        for owner, attribute in targets:
            original = getattr(owner, attribute)
            self._originals.append((owner, attribute, original))
            setattr(owner, attribute, self._blocked(f"{owner.__name__}.{attribute}"))
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        del exc_type, exc_value, traceback
        for owner, attribute, original in reversed(self._originals):
            setattr(owner, attribute, original)
        self._originals.clear()


@dataclass(frozen=True, slots=True)
class PythonPin:
    name: str
    version: str
    marker: str
    extras: tuple[str, ...]
    hashes: tuple[tuple[str, str], ...]


def _canonical_python_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _read_lock(path: Path, label: str) -> bytes:
    try:
        if not path.is_file():
            raise LockValidationError(f"Required lock is missing: {label}")
        content = path.read_bytes()
    except OSError as exc:
        raise LockValidationError(f"Cannot read required lock {label}: {exc}") from exc
    if not content:
        raise LockValidationError(f"Required lock is empty: {label}")
    return content


def _load_json_lock(content: bytes, label: str) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise LockValidationError(f"Duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    try:
        decoded = content.decode("utf-8")
        payload = json.loads(decoded, object_pairs_hook=reject_duplicate_keys)
    except LockValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LockValidationError(f"Malformed JSON lock {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise LockValidationError(f"JSON lock root must be an object: {label}")
    return payload


def _validate_hex_hash(algorithm: str, value: str, context: str) -> tuple[str, str]:
    normalized = algorithm.lower().replace("-", "")
    definition = _HASH_ALGORITHMS.get(normalized)
    if definition is None:
        raise LockValidationError(f"Unsupported hash algorithm in {context}: {algorithm}")
    cyclone_name, byte_length = definition
    if len(value) != byte_length * 2 or not re.fullmatch(r"[A-Fa-f0-9]+", value):
        raise LockValidationError(f"Malformed {algorithm} hash in {context}")
    return cyclone_name, value.lower()


def _parse_python_lock(content: bytes) -> list[PythonPin]:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise LockValidationError(f"requirements.lock is not UTF-8: {exc}") from exc

    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line and not line[0].isspace() and not line.startswith("#"):
            if current:
                blocks.append(current)
            current = [line]
        elif current:
            current.append(line)
        elif line.strip() and not line.lstrip().startswith("#"):
            raise LockValidationError("Orphan content in requirements.lock")
    if current:
        blocks.append(current)
    if not blocks:
        raise LockValidationError("requirements.lock contains no pinned packages")

    pins: dict[tuple[str, str], PythonPin] = {}
    for block in blocks:
        first = block[0].strip()
        if first.endswith("\\"):
            first = first[:-1].rstrip()
        match = _PYTHON_PIN.fullmatch(first)
        if match is None:
            raise LockValidationError(f"Unsupported Python lock entry: {block[0].strip()}")

        name = _canonical_python_name(match.group("name"))
        version = match.group("version")
        marker = re.sub(r"\s+", " ", match.group("marker") or "").strip()
        extras = tuple(
            sorted(
                {
                    extra.strip().lower()
                    for extra in (match.group("extras") or "").split(",")
                    if extra.strip()
                }
            )
        )
        parsed_hashes: set[tuple[str, str]] = set()
        for line in block[1:]:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.endswith("\\"):
                stripped = stripped[:-1].rstrip()
            tokens = _PYTHON_HASH.findall(stripped)
            residue = _PYTHON_HASH.sub("", stripped).strip()
            if residue or not tokens:
                raise LockValidationError(
                    f"Unsupported continuation for Python pin {name}=={version}: {line.strip()}"
                )
            for algorithm, value in tokens:
                parsed_hashes.add(
                    _validate_hex_hash(algorithm, value, f"Python pin {name}=={version}")
                )

        pin = PythonPin(
            name=name,
            version=version,
            marker=marker,
            extras=extras,
            hashes=tuple(sorted(parsed_hashes)),
        )
        identity = (name, marker)
        previous = pins.get(identity)
        if previous is not None and previous != pin:
            raise LockValidationError(
                "Conflicting duplicate Python pin for "
                f"{name} with marker {marker or '<unconditional>'}"
            )
        pins[identity] = pin
    return sorted(pins.values(), key=lambda item: (item.name, item.version, item.marker))


def _purl(package_type: str, name: str, version: str) -> str:
    encoded_name = quote(name, safe="/._-")
    encoded_version = quote(version, safe="._-+")
    return f"pkg:{package_type}/{encoded_name}@{encoded_version}"


def _properties(values: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"name": name, "value": value if isinstance(value, str) else _compact_json(value)}
        for name, value in sorted(values.items())
    ]


def _license_choice(declaration: str) -> list[dict[str, Any]]:
    if _SPDX_IDENTIFIER.fullmatch(declaration) and declaration in _KNOWN_SPDX_IDENTIFIERS:
        return [{"license": {"id": declaration}}]
    expression_tokens = declaration.replace("(", " ").replace(")", " ").split()
    if (
        _SPDX_EXPRESSION.fullmatch(declaration)
        and any(operator in expression_tokens for operator in _SPDX_OPERATORS)
        and all(
            token in _KNOWN_SPDX_IDENTIFIERS or token in _SPDX_OPERATORS
            for token in expression_tokens
        )
    ):
        return [{"expression": declaration}]
    return [{"license": {"name": declaration}}]


def _build_python_components(pins: list[PythonPin]) -> list[dict[str, Any]]:
    components: dict[str, dict[str, Any]] = {}
    component_markers: dict[str, set[str]] = {}
    component_extras: dict[str, set[str]] = {}
    for pin in pins:
        purl = _purl("pypi", pin.name, pin.version)
        hashes = [{"alg": algorithm, "content": value} for algorithm, value in pin.hashes]
        existing = components.get(purl)
        if existing is not None and existing.get("hashes", []) != hashes:
            raise LockValidationError(f"Conflicting duplicate Python component: {purl}")
        if existing is None:
            components[purl] = {
                "bom-ref": purl,
                "hashes": hashes,
                "name": pin.name,
                "purl": purl,
                "scope": "required",
                "type": "library",
                "version": pin.version,
            }
            component_markers[purl] = set()
            component_extras[purl] = set()
        if pin.marker:
            component_markers[purl].add(pin.marker)
        component_extras[purl].update(pin.extras)

    for purl, component in components.items():
        values: dict[str, Any] = {
            "klm:ecosystem": "python",
            "klm:licenseAssurance": "unverified",
            "klm:licenseStatus": "not-declared-in-requirements-lock",
            "klm:sourceLock": "requirements.lock",
        }
        if component_markers[purl]:
            values["klm:environmentMarkers"] = sorted(component_markers[purl])
        if component_extras[purl]:
            values["klm:extras"] = sorted(component_extras[purl])
        component["properties"] = _properties(values)
        if not component["hashes"]:
            component.pop("hashes")
    return [components[key] for key in sorted(components)]


def _npm_name(path: str, package: dict[str, Any]) -> str:
    declared = package.get("name")
    if declared is not None:
        if not isinstance(declared, str) or not declared.strip():
            raise LockValidationError(f"Malformed npm package name at {path}")
        return declared.strip()
    if "node_modules/" not in path:
        raise LockValidationError(f"Cannot derive npm package name from path: {path}")
    tail = path.rsplit("node_modules/", 1)[1]
    parts = tail.split("/")
    if parts[0].startswith("@"):
        if len(parts) < 2 or not parts[1]:
            raise LockValidationError(f"Malformed scoped npm package path: {path}")
        return "/".join(parts[:2])
    if not parts[0]:
        raise LockValidationError(f"Malformed npm package path: {path}")
    return parts[0]


def _parse_npm_integrity(integrity: str, context: str) -> list[dict[str, str]]:
    hashes: set[tuple[str, str]] = set()
    for token in integrity.split():
        if "-" not in token:
            raise LockValidationError(f"Malformed npm integrity token in {context}")
        algorithm, encoded = token.split("-", 1)
        definition = _HASH_ALGORITHMS.get(algorithm.lower())
        if definition is None:
            raise LockValidationError(
                f"Unsupported npm integrity algorithm in {context}: {algorithm}"
            )
        cyclone_name, byte_length = definition
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise LockValidationError(f"Malformed npm integrity digest in {context}") from exc
        if len(decoded) != byte_length:
            raise LockValidationError(f"Wrong npm integrity length in {context}")
        hashes.add((cyclone_name, decoded.hex()))
    if not hashes:
        raise LockValidationError(f"Empty npm integrity in {context}")
    return [{"alg": algorithm, "content": value} for algorithm, value in sorted(hashes)]


def _build_npm_components(lock: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    if lock.get("lockfileVersion") not in {2, 3}:
        raise LockValidationError("frontend/package-lock.json must use lockfileVersion 2 or 3")
    packages = lock.get("packages")
    if not isinstance(packages, dict) or not packages:
        raise LockValidationError("frontend/package-lock.json packages must be a non-empty object")
    root_package = packages.get("")
    if not isinstance(root_package, dict):
        raise LockValidationError("frontend/package-lock.json is missing the root package")

    aggregated: dict[str, dict[str, Any]] = {}
    install_paths: dict[str, set[str]] = {}
    development_flags: dict[str, list[bool]] = {}
    optional_flags: dict[str, list[bool]] = {}
    records = 0
    for path, package in sorted(packages.items()):
        if path == "":
            continue
        records += 1
        if not isinstance(path, str) or not isinstance(package, dict):
            raise LockValidationError("Malformed npm package record")
        name = _npm_name(path, package)
        version = package.get("version")
        if not isinstance(version, str) or not version.strip():
            raise LockValidationError(f"Missing npm package version at {path}")
        integrity = package.get("integrity")
        if integrity is not None and (not isinstance(integrity, str) or not integrity.strip()):
            raise LockValidationError(f"Malformed npm integrity at {path}")
        hashes = _parse_npm_integrity(integrity, path) if integrity else []
        resolved = package.get("resolved")
        if resolved is not None and (not isinstance(resolved, str) or not resolved.strip()):
            raise LockValidationError(f"Malformed npm resolved source at {path}")
        declaration = package.get("license")
        if declaration is not None and (
            not isinstance(declaration, str) or not declaration.strip()
        ):
            raise LockValidationError(f"Malformed npm license declaration at {path}")
        declaration = declaration.strip() if isinstance(declaration, str) else None

        purl = _purl("npm", name, version)
        core = {
            "hashes": hashes,
            "licenses": _license_choice(declaration) if declaration else [],
            "name": name,
            "resolved": resolved,
            "version": version,
        }
        previous = aggregated.get(purl)
        if previous is not None and any(previous[field] != core[field] for field in core):
            raise LockValidationError(f"Conflicting duplicate npm component: {purl}")
        if previous is None:
            aggregated[purl] = core
            install_paths[purl] = set()
            development_flags[purl] = []
            optional_flags[purl] = []
        install_paths[purl].add(path)
        development_flags[purl].append(package.get("dev") is True)
        optional_flags[purl].append(package.get("optional") is True)

    components: list[dict[str, Any]] = []
    for purl in sorted(aggregated):
        core = aggregated[purl]
        licenses = core["licenses"]
        development_only = all(development_flags[purl])
        optional = any(optional_flags[purl])
        component: dict[str, Any] = {
            "bom-ref": purl,
            "name": core["name"],
            "purl": purl,
            "scope": "optional" if development_only or optional else "required",
            "type": "library",
            "version": core["version"],
            "properties": _properties(
                {
                    "klm:developmentOnly": str(development_only).lower(),
                    "klm:ecosystem": "npm",
                    "klm:installPaths": sorted(install_paths[purl]),
                    "klm:licenseAssurance": "unverified",
                    "klm:licenseStatus": (
                        "declared-package-metadata"
                        if licenses
                        else "not-declared-in-package-lock"
                    ),
                    "klm:optional": str(optional).lower(),
                    "klm:sourceLock": "frontend/package-lock.json",
                }
            ),
        }
        if core["hashes"]:
            component["hashes"] = core["hashes"]
        if licenses:
            component["licenses"] = licenses
        components.append(component)
    return components, records


def _required_string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LockValidationError(f"Missing or malformed string: {context}")
    return value.strip()


def _github_purl(repository: str, commit: str) -> str:
    match = re.fullmatch(r"https://github\.com/([^/]+)/([^/]+?)\.git", repository)
    if match is None:
        raise LockValidationError(f"Unsupported upstream repository URL: {repository}")
    name = f"{match.group(1)}/{match.group(2)}"
    return _purl("github", name, commit)


def _build_upstream_components(
    lock: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if lock.get("schemaVersion") != 2:
        raise LockValidationError("docs/oss-upstreams.lock.json schemaVersion must be 2")
    sources = lock.get("sources")
    if not isinstance(sources, list):
        raise LockValidationError("docs/oss-upstreams.lock.json sources must be an array")

    unique: dict[str, dict[str, Any]] = {}
    fingerprints: dict[str, str] = {}
    for source in sources:
        if not isinstance(source, dict):
            raise LockValidationError("Malformed OSS upstream record")
        name = _required_string(source.get("name"), "OSS upstream name")
        fingerprint = _compact_json(source)
        if name in unique:
            if fingerprints[name] != fingerprint:
                raise LockValidationError(f"Conflicting duplicate OSS upstream: {name}")
            continue
        unique[name] = source
        fingerprints[name] = fingerprint

    expected_names = set(EXPECTED_OSS_PINS)
    actual_names = set(unique)
    if actual_names != expected_names:
        missing = sorted(expected_names - actual_names)
        unexpected = sorted(actual_names - expected_names)
        raise LockValidationError(
            f"OSS pin set mismatch; missing={missing}, unexpected={unexpected}"
        )

    components: list[dict[str, Any]] = []
    pin_summary: list[dict[str, str]] = []
    for name in sorted(unique):
        source = unique[name]
        expected = EXPECTED_OSS_PINS[name]
        repository = _required_string(source.get("repository"), f"{name}.repository")
        commit = _required_string(source.get("commit"), f"{name}.commit")
        if repository != expected["repository"] or commit != expected["commit"]:
            raise LockValidationError(f"Unexpected immutable pin for OSS upstream: {name}")
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise LockValidationError(f"Malformed Git commit for OSS upstream: {name}")

        ref = _required_string(source.get("ref"), f"{name}.ref")
        license_data = source.get("license")
        if not isinstance(license_data, dict):
            raise LockValidationError(f"Missing license object for OSS upstream: {name}")
        license_status = _required_string(
            license_data.get("status"), f"{name}.license.status"
        )
        conclusion = _required_string(
            license_data.get("conclusion"), f"{name}.license.conclusion"
        )
        if license_status != expected["licenseStatus"]:
            raise LockValidationError(f"Unexpected license assurance status for {name}")
        evidence = license_data.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise LockValidationError(f"Missing pinned license evidence for {name}")
        normalized_evidence: list[dict[str, str]] = []
        for index, receipt in enumerate(evidence):
            if not isinstance(receipt, dict):
                raise LockValidationError(f"Malformed license evidence for {name}")
            git_blob = _required_string(
                receipt.get("gitBlob"), f"{name}.license.evidence[{index}].gitBlob"
            )
            if not re.fullmatch(r"[0-9a-f]{40}", git_blob):
                raise LockValidationError(f"Malformed license evidence Git blob for {name}")
            normalized_evidence.append(
                {
                    "gitBlob": git_blob,
                    "kind": _required_string(
                        receipt.get("kind"), f"{name}.license.evidence[{index}].kind"
                    ),
                    "path": _required_string(
                        receipt.get("path"), f"{name}.license.evidence[{index}].path"
                    ),
                }
            )

        checkout = source.get("checkout")
        if not isinstance(checkout, dict):
            raise LockValidationError(f"Missing checkout policy for {name}")
        exclusions = checkout.get("excludePaths", [])
        if not isinstance(exclusions, list) or any(
            not isinstance(path, str) or not path for path in exclusions
        ):
            raise LockValidationError(f"Malformed checkout exclusions for {name}")
        exclusions = sorted(set(exclusions))

        if license_status == "package-metadata-only":
            package_metadata = source.get("packageMetadata")
            if not isinstance(package_metadata, dict):
                raise LockValidationError(f"Missing package metadata license source for {name}")
            declaration = _required_string(
                package_metadata.get("license"), f"{name}.packageMetadata.license"
            )
            if (
                declaration != expected["license"]
                or license_data.get("rootLicenseAbsent") is not True
            ):
                raise LockValidationError(
                    f"Unreviewed package license assertion changed for {name}"
                )
            assurance = "unverified"
            reuse_status = "blocked-pending-explicit-license-review"
        else:
            declaration = expected["license"]
            assurance = "verified-from-pinned-git-evidence"
            reuse_status = (
                "allowed-only-with-recorded-exclusions"
                if exclusions
                else "allowed-under-verified-file-license"
            )

        purl = _github_purl(repository, commit)
        properties: dict[str, Any] = {
            "klm:commit": commit,
            "klm:ecosystem": "oss-upstream",
            "klm:licenseAssurance": assurance,
            "klm:licenseConclusion": conclusion,
            "klm:licenseEvidence": normalized_evidence,
            "klm:licenseStatus": license_status,
            "klm:ref": ref,
            "klm:repository": repository,
            "klm:reuseMode": _required_string(source.get("reuseMode"), f"{name}.reuseMode"),
            "klm:reuseStatus": reuse_status,
            "klm:sourceLock": "docs/oss-upstreams.lock.json",
        }
        if exclusions:
            properties["klm:excludedPaths"] = exclusions
        if license_status == "package-metadata-only":
            properties["klm:blockingReason"] = (
                "Repository-root license is absent; package metadata is not verified-file evidence."
            )

        components.append(
            {
                "bom-ref": purl,
                "externalReferences": [
                    {"type": "vcs", "url": f"{repository}#{commit}"}
                ],
                "hashes": [{"alg": "SHA-1", "content": commit}],
                "licenses": _license_choice(declaration),
                "name": name,
                "properties": _properties(properties),
                "purl": purl,
                "scope": "required",
                "type": "application",
                "version": commit,
            }
        )
        pin_summary.append(
            {
                "commit": commit,
                "license": declaration,
                "licenseAssurance": assurance,
                "licenseStatus": license_status,
                "name": name,
                "reuseStatus": reuse_status,
            }
        )
    return components, pin_summary


def _component_property(component: dict[str, Any], name: str) -> str | None:
    for item in component.get("properties", []):
        if item.get("name") == name:
            value = item.get("value")
            return value if isinstance(value, str) else None
    return None


def _declared_license_tokens(component: dict[str, Any]) -> set[str]:
    declarations: set[str] = set()
    for item in component.get("licenses", []):
        expression = item.get("expression")
        if isinstance(expression, str):
            declarations.add(expression)
            declarations.update(
                token
                for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9.+-]*", expression)
                if token.upper() not in _SPDX_OPERATORS
            )
        license_item = item.get("license")
        if isinstance(license_item, dict):
            for field in ("id", "name"):
                declaration = license_item.get(field)
                if isinstance(declaration, str):
                    declarations.add(declaration)
    return {item.casefold() for item in declarations}


def _enforce_forbidden_licenses(
    components: list[dict[str, Any]], forbidden_licenses: frozenset[str]
) -> None:
    normalized = {item.casefold() for item in forbidden_licenses}
    if not normalized:
        return
    for component in components:
        denied = sorted(_declared_license_tokens(component) & normalized)
        if denied:
            raise ForbiddenLicenseError(
                f"Forbidden declared license on {component['bom-ref']}: {', '.join(denied)}"
            )


def _license_summary(components: list[dict[str, Any]]) -> dict[str, Any]:
    assurance = Counter(
        _component_property(component, "klm:licenseAssurance") or "missing-assurance"
        for component in components
    )
    statuses = Counter(
        _component_property(component, "klm:licenseStatus") or "missing-status"
        for component in components
    )
    blocked = sorted(
        component["name"]
        for component in components
        if (_component_property(component, "klm:reuseStatus") or "").startswith("blocked-")
    )
    return {
        "assuranceCounts": dict(sorted(assurance.items())),
        "blockedPendingReview": blocked,
        "statusCounts": dict(sorted(statuses.items())),
        "verifiedComponents": assurance.get("verified-from-pinned-git-evidence", 0),
        "unverifiedComponents": assurance.get("unverified", 0),
    }


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    except OSError as exc:
        raise SbomGenerationError(f"Cannot write SBOM evidence {path}: {exc}") from exc


def generate_sbom(
    *,
    requirements_lock: Path = DEFAULT_REQUIREMENTS_LOCK,
    package_lock: Path = DEFAULT_PACKAGE_LOCK,
    upstream_lock: Path = DEFAULT_UPSTREAM_LOCK,
    output: Path = DEFAULT_OUTPUT,
    forbidden_licenses: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Build and persist one deterministic SBOM while all network paths are denied."""

    guard = OfflineNetworkGuard()
    with guard:
        input_bytes = {
            "docs/oss-upstreams.lock.json": _read_lock(
                upstream_lock, "docs/oss-upstreams.lock.json"
            ),
            "frontend/package-lock.json": _read_lock(
                package_lock, "frontend/package-lock.json"
            ),
            "requirements.lock": _read_lock(requirements_lock, "requirements.lock"),
        }
        input_digests = {
            name: hashlib.sha256(content).hexdigest()
            for name, content in sorted(input_bytes.items())
        }
        python_pins = _parse_python_lock(input_bytes["requirements.lock"])
        package_payload = _load_json_lock(
            input_bytes["frontend/package-lock.json"], "frontend/package-lock.json"
        )
        upstream_payload = _load_json_lock(
            input_bytes["docs/oss-upstreams.lock.json"],
            "docs/oss-upstreams.lock.json",
        )

        python_components = _build_python_components(python_pins)
        npm_components, npm_records = _build_npm_components(package_payload)
        upstream_components, pin_summary = _build_upstream_components(upstream_payload)
        components = sorted(
            [*python_components, *npm_components, *upstream_components],
            key=lambda item: item["bom-ref"],
        )
        if len({component["bom-ref"] for component in components}) != len(components):
            raise LockValidationError("Conflicting duplicate CycloneDX bom-ref")
        _enforce_forbidden_licenses(components, forbidden_licenses)

        input_seed = "\n".join(
            f"{name}:{digest}" for name, digest in sorted(input_digests.items())
        )
        input_seed += "\nforbidden-licenses:" + ",".join(sorted(forbidden_licenses))
        serial = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"urn:klm:saas-v5:p5:sbom:{input_seed}",
        )
        component_counts = {
            "npm": len(npm_components),
            "ossUpstream": len(upstream_components),
            "python": len(python_components),
            "total": len(components),
        }
        bom = {
            "bomFormat": "CycloneDX",
            "components": components,
            "metadata": {
                "properties": _properties(
                    {
                        "klm:componentCounts": component_counts,
                        "klm:forbiddenLicensePolicy": sorted(forbidden_licenses),
                        "klm:inputLockDigests": input_digests,
                        "klm:networkAccess": "denied-by-runtime-socket-guard",
                        "klm:networkAttempts": "0",
                        "klm:reproducible": "true",
                    }
                ),
                "tools": [
                    {
                        "name": "generate_saas_v5_sbom.py",
                        "vendor": "KLM Hub",
                        "version": SCRIPT_VERSION,
                    }
                ],
            },
            "serialNumber": f"urn:uuid:{serial}",
            "specVersion": CYCLONEDX_SPEC_VERSION,
            "version": 1,
        }
        rendered = _canonical_bytes(bom)
        _atomic_write(output, rendered)

    if guard.attempts != 0:
        raise OfflineNetworkAttemptError(
            f"SBOM generation attempted {guard.attempts} network operations"
        )

    digest = hashlib.sha256(rendered).hexdigest()
    return {
        "componentCounts": component_counts,
        "digest": {"algorithm": "sha256", "value": digest},
        "expectedOssPins": pin_summary,
        "forbiddenLicenses": sorted(forbidden_licenses),
        "inputLockDigests": input_digests,
        "licenseEvidence": _license_summary(components),
        "networkAccess": {
            "attempts": guard.attempts,
            "externalCallsExecuted": 0,
            "guard": "socket-deny",
            "passed": True,
        },
        "output": output.as_posix(),
        "recordCounts": {
            "npmLockEntries": npm_records,
            "pythonPins": len(python_pins),
            "upstreamPins": len(pin_summary),
        },
        "serialNumber": bom["serialNumber"],
        "status": "passed",
    }


def _configured_forbidden_licenses(
    cli_values: list[str], environment: dict[str, str] | None = None
) -> frozenset[str]:
    environment = environment if environment is not None else os.environ
    values = [*cli_values, environment.get("KLM_SBOM_FORBIDDEN_LICENSES", "")]
    return frozenset(
        item.strip()
        for value in values
        for item in value.split(",")
        if item.strip()
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirements-lock", type=Path, default=DEFAULT_REQUIREMENTS_LOCK)
    parser.add_argument("--package-lock", type=Path, default=DEFAULT_PACKAGE_LOCK)
    parser.add_argument("--upstream-lock", type=Path, default=DEFAULT_UPSTREAM_LOCK)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--forbid-license",
        action="append",
        default=[],
        help=(
            "Fail when a declared license or SPDX token matches this value. "
            "Repeat or comma-separate values; KLM_SBOM_FORBIDDEN_LICENSES is also read."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = generate_sbom(
            requirements_lock=args.requirements_lock,
            package_lock=args.package_lock,
            upstream_lock=args.upstream_lock,
            output=args.output,
            forbidden_licenses=_configured_forbidden_licenses(args.forbid_license),
        )
    except SbomGenerationError as exc:
        print(
            json.dumps(
                {
                    "error": str(exc),
                    "errorType": type(exc).__name__,
                    "status": "failed",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
