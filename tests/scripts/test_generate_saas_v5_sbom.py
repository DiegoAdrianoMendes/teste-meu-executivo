from __future__ import annotations

import base64
import hashlib
import json
import socket
from collections import Counter
from copy import deepcopy
from pathlib import Path

import pytest

from scripts import generate_saas_v5_sbom as sbom


def _property(component: dict, name: str) -> str | None:
    return next(
        (item["value"] for item in component.get("properties", []) if item["name"] == name),
        None,
    )


def _generate(tmp_path: Path, name: str = "sbom.cdx.json", **kwargs: object) -> dict:
    return sbom.generate_sbom(output=tmp_path / name, **kwargs)


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict, dict, bytes]:
    directory = tmp_path_factory.mktemp("p5-sbom")
    first_path = directory / "first.cdx.json"
    second_path = directory / "second.cdx.json"
    first = sbom.generate_sbom(output=first_path)
    second = sbom.generate_sbom(output=second_path)
    assert first_path.read_bytes() == second_path.read_bytes()
    assert first["digest"] == second["digest"]
    return first, json.loads(first_path.read_text(encoding="utf-8")), first_path.read_bytes()


def test_actual_locks_generate_reproducible_cyclonedx_without_network(
    generated: tuple[dict, dict, bytes],
) -> None:
    summary, document, rendered = generated

    assert document["bomFormat"] == "CycloneDX"
    assert document["specVersion"] == "1.5"
    assert document["serialNumber"].startswith("urn:uuid:")
    assert summary["status"] == "passed"
    assert summary["networkAccess"] == {
        "attempts": 0,
        "externalCallsExecuted": 0,
        "guard": "socket-deny",
        "passed": True,
    }
    package_lock = json.loads(sbom.DEFAULT_PACKAGE_LOCK.read_text(encoding="utf-8"))
    python_pins = sbom._parse_python_lock(sbom.DEFAULT_REQUIREMENTS_LOCK.read_bytes())
    ecosystem_counts = Counter(
        _property(component, "klm:ecosystem") for component in document["components"]
    )
    assert summary["recordCounts"] == {
        "npmLockEntries": len(package_lock["packages"]) - 1,
        "pythonPins": len(python_pins),
        "upstreamPins": 5,
    }
    assert summary["componentCounts"] == {
        "npm": ecosystem_counts["npm"],
        "ossUpstream": ecosystem_counts["oss-upstream"],
        "python": ecosystem_counts["python"],
        "total": len(document["components"]),
    }
    assert summary["digest"] == {
        "algorithm": "sha256",
        "value": hashlib.sha256(rendered).hexdigest(),
    }
    assert len({component["bom-ref"] for component in document["components"]}) == len(
        document["components"]
    )
    assert all(component.get("purl", "").startswith("pkg:") for component in document["components"])


def test_all_five_expected_oss_pins_and_license_boundaries_are_explicit(
    generated: tuple[dict, dict, bytes],
) -> None:
    summary, document, _ = generated
    pins = {item["name"]: item for item in summary["expectedOssPins"]}

    assert set(pins) == set(sbom.EXPECTED_OSS_PINS)
    assert all(
        pins[name]["commit"] == expected["commit"]
        for name, expected in sbom.EXPECTED_OSS_PINS.items()
    )
    assert summary["licenseEvidence"]["verifiedComponents"] == 4
    assert summary["licenseEvidence"]["blockedPendingReview"] == [
        "rocket-embedded-chat"
    ]

    rocket = next(
        component
        for component in document["components"]
        if component["name"] == "rocket-embedded-chat"
    )
    assert rocket["licenses"] == [{"license": {"id": "ISC"}}]
    assert _property(rocket, "klm:licenseStatus") == "package-metadata-only"
    assert _property(rocket, "klm:licenseAssurance") == "unverified"
    assert _property(rocket, "klm:reuseStatus") == (
        "blocked-pending-explicit-license-review"
    )
    assert "not verified-file evidence" in (_property(rocket, "klm:blockingReason") or "")


def test_missing_license_is_never_reported_as_verified(
    generated: tuple[dict, dict, bytes],
) -> None:
    _, document, _ = generated
    python_component = next(
        component
        for component in document["components"]
        if _property(component, "klm:ecosystem") == "python"
    )
    npm_without_license = [
        component
        for component in document["components"]
        if _property(component, "klm:licenseStatus") == "not-declared-in-package-lock"
    ]

    assert "licenses" not in python_component
    assert _property(python_component, "klm:licenseAssurance") == "unverified"
    assert _property(python_component, "klm:licenseStatus") == (
        "not-declared-in-requirements-lock"
    )
    # O lockfile atual declara licença para todos os pacotes npm; quando algum
    # deixar de declarar, o invariante continua garantido componente a componente.
    for component in npm_without_license:
        assert "licenses" not in component
        assert _property(component, "klm:licenseAssurance") == "unverified"


def test_offline_guard_blocks_and_counts_network_attempts() -> None:
    guard = sbom.OfflineNetworkGuard()

    with guard, pytest.raises(sbom.OfflineNetworkAttemptError, match="socket.create_connection"):
        socket.create_connection(("example.invalid", 443))

    assert guard.attempts == 1
    assert guard.attempted_operations == ["socket.create_connection"]


def test_missing_and_malformed_locks_fail_closed(tmp_path: Path) -> None:
    missing = tmp_path / "missing.lock"
    with pytest.raises(sbom.LockValidationError, match="Required lock is missing"):
        _generate(tmp_path, requirements_lock=missing)

    malformed = tmp_path / "package-lock.json"
    malformed.write_text("{not-json", encoding="utf-8")
    with pytest.raises(sbom.LockValidationError, match="Malformed JSON lock"):
        _generate(tmp_path, package_lock=malformed)

    duplicate_key = tmp_path / "duplicate-key.json"
    duplicate_key.write_text(
        '{"lockfileVersion":3,"packages":{},"packages":{}}',
        encoding="utf-8",
    )
    with pytest.raises(sbom.LockValidationError, match="Duplicate JSON key"):
        _generate(tmp_path, package_lock=duplicate_key)


def test_conflicting_duplicate_python_pin_fails_closed(tmp_path: Path) -> None:
    content = sbom.DEFAULT_REQUIREMENTS_LOCK.read_text(encoding="utf-8")
    conflict = tmp_path / "requirements.lock"
    conflict.write_text(
        content
        + "\naiofiles==99.0.0 \\\n"
        + "    --hash=sha256:"
        + "0" * 64
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(sbom.LockValidationError, match="Conflicting duplicate Python pin"):
        _generate(tmp_path, requirements_lock=conflict)


def test_conflicting_duplicate_npm_component_fails_closed(tmp_path: Path) -> None:
    payload = json.loads(sbom.DEFAULT_PACKAGE_LOCK.read_text(encoding="utf-8"))
    payload["packages"]["node_modules/adversarial/node_modules/react"] = {
        "integrity": "sha512-" + base64.b64encode(b"x" * 64).decode("ascii"),
        "license": "MIT",
        "version": "18.3.1",
    }
    conflict = tmp_path / "package-lock.json"
    conflict.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(sbom.LockValidationError, match="Conflicting duplicate npm component"):
        _generate(tmp_path, package_lock=conflict)


def test_conflicting_duplicate_upstream_fails_closed(tmp_path: Path) -> None:
    payload = json.loads(sbom.DEFAULT_UPSTREAM_LOCK.read_text(encoding="utf-8"))
    duplicate = deepcopy(payload["sources"][0])
    duplicate["commit"] = "0" * 40
    payload["sources"].append(duplicate)
    conflict = tmp_path / "oss-upstreams.lock.json"
    conflict.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(sbom.LockValidationError, match="Conflicting duplicate OSS upstream"):
        _generate(tmp_path, upstream_lock=conflict)


def test_missing_or_changed_expected_upstream_pin_fails_closed(tmp_path: Path) -> None:
    payload = json.loads(sbom.DEFAULT_UPSTREAM_LOCK.read_text(encoding="utf-8"))
    payload["sources"] = [
        source for source in payload["sources"] if source["name"] != "chatwoot"
    ]
    missing = tmp_path / "missing-upstream.json"
    missing.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(sbom.LockValidationError, match="OSS pin set mismatch"):
        _generate(tmp_path, upstream_lock=missing)

    payload = json.loads(sbom.DEFAULT_UPSTREAM_LOCK.read_text(encoding="utf-8"))
    payload["sources"][0]["commit"] = "0" * 40
    changed = tmp_path / "changed-upstream.json"
    changed.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(sbom.LockValidationError, match="Unexpected immutable pin"):
        _generate(tmp_path, upstream_lock=changed)


def test_configurable_forbidden_license_policy_fails_on_tokens(tmp_path: Path) -> None:
    with pytest.raises(sbom.ForbiddenLicenseError, match="Forbidden declared license"):
        _generate(tmp_path, forbidden_licenses=frozenset({"MIT"}))

    configured = sbom._configured_forbidden_licenses(
        ["GPL-3.0-only,AGPL-3.0-only"],
        {"KLM_SBOM_FORBIDDEN_LICENSES": "SSPL-1.0, BUSL-1.1"},
    )
    assert configured == frozenset(
        {"GPL-3.0-only", "AGPL-3.0-only", "SSPL-1.0", "BUSL-1.1"}
    )


def test_undefined_license_does_not_satisfy_or_violate_a_declared_policy(
    tmp_path: Path,
) -> None:
    summary = _generate(
        tmp_path,
        forbidden_licenses=frozenset({"NOASSERTION", "LicenseRef-Unknown"}),
    )
    assert summary["status"] == "passed"
    assert summary["licenseEvidence"]["unverifiedComponents"] > 0


def test_cli_emits_machine_readable_summary_and_digest(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KLM_SBOM_FORBIDDEN_LICENSES", raising=False)
    output = tmp_path / "cli.cdx.json"
    exit_code = sbom.main(["--output", str(output)])

    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert summary["status"] == "passed"
    assert summary["digest"]["value"] == hashlib.sha256(output.read_bytes()).hexdigest()
