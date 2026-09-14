from __future__ import annotations

import json
from pathlib import Path

from scripts.build_oss_function_unit_dispositions import (
    DEFAULT_OUTPUT,
    build_policy,
)


def test_materialized_policy_matches_the_deterministic_builder() -> None:
    expected = build_policy()
    committed = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))

    assert committed == expected
    assert committed["generator"]["version"] == 8
    assert committed["generator"]["mappedLicensedUnits"] == 3768
    assert committed["generator"]["byConfidence"] == {
        "category_default": 751,
        "lexical_rule": 144,
        "semantic_rule": 2873,
    }


def test_every_licensed_mapping_is_exact_id_bound_and_non_overlapping() -> None:
    policy = build_policy()
    licensed_rules = [
        rule
        for rule in policy["rules"]
        if rule["state"] in {"native_shadow", "native_primary", "native_verified"}
    ]
    unit_ids = [
        unit_id
        for rule in licensed_rules
        for unit_id in rule["match"].get("unitIds", [])
    ]

    assert len(unit_ids) == len(set(unit_ids)) == 3768
    assert {rule["state"] for rule in licensed_rules} == {
        "native_shadow",
        "native_primary",
    }
    assert policy["generator"]["primaryLicensedUnits"] == 1367
    assert policy["generator"]["shadowLicensedUnits"] == 2401
    assert policy["generator"]["verifiedLicensedUnits"] == 0
    assert all(rule["priority"] == 300 for rule in licensed_rules)
    assert all(rule["match"]["unitIds"] for rule in licensed_rules)
    assert all(
        rule["mapping"]["coverageKind"]
        in {"semantic_rule", "lexical_rule", "category_default"}
        for rule in licensed_rules
    )
    assert not any("categories" in rule["match"] for rule in licensed_rules)


def test_builder_keeps_per_unit_maturity_without_aggregate_manifest(tmp_path: Path) -> None:
    policy = build_policy(verification_manifest_path=tmp_path / "missing.json")
    licensed_rules = [
        rule
        for rule in policy["rules"]
        if rule["state"] in {"native_shadow", "native_primary", "native_verified"}
    ]

    assert {rule["state"] for rule in licensed_rules} == {
        "native_shadow",
        "native_primary",
    }
    assert policy["generator"]["primaryLicensedUnits"] == 1367
    assert policy["generator"]["shadowLicensedUnits"] == 2401
    assert policy["generator"]["verifiedLicensedUnits"] == 0
    assert policy["generator"]["verification"] == {
        "state": "per_unit_oracle_only",
        "reason": "aggregate_release_manifest_is_observer_only",
        "authority": "app/data/oss_function_unit_conformance.json",
        "aggregateManifestIsInput": False,
        "manifest": str(tmp_path / "missing.json"),
        "sourceDigest": None,
    }


def test_builder_does_not_depend_on_aggregate_release_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "aggregate-release.json"
    manifest.write_text('{"state":"stale"}\n', encoding="utf-8")
    first = build_policy(verification_manifest_path=manifest)
    manifest.write_text('{"state":"passed","sourceDigest":"untrusted"}\n', encoding="utf-8")
    second = build_policy(verification_manifest_path=manifest)

    assert first == second
    assert first["generator"]["verification"]["aggregateManifestIsInput"] is False


def test_builder_source_is_part_of_the_census_generator_manifest() -> None:
    lock = json.loads(
        (Path(__file__).resolve().parents[2] / "docs" / "oss-function-units.lock.json").read_text(
            encoding="utf-8"
        )
    )
    source_paths = {item["path"] for item in lock["generator"]["sourceFiles"]}
    assert "scripts/build_oss_function_unit_dispositions.py" in source_paths
