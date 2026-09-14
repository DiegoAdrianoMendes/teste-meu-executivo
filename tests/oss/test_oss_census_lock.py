from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from scripts.oss_census.model import CATEGORIES, REQUIRED_UPSTREAMS

ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = ROOT / "docs" / "oss-function-units.lock.json"


def _load_lock() -> dict[str, object]:
    document = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_lock_is_self_consistent_and_bound_to_generator_sources() -> None:
    document = _load_lock()
    receipt = document["reproducibility"]
    without_receipt = dict(document)
    del without_receipt["reproducibility"]
    payload = json.dumps(without_receipt, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(payload.encode("utf-8")).hexdigest() == receipt["payloadSha256"]

    source_records = document["generator"]["sourceFiles"]
    for record in source_records:
        assert _sha256(ROOT / record["path"]) == record["sha256"]
    digest_input = "\n".join(f"{item['path']}\0{item['sha256']}" for item in source_records)
    assert hashlib.sha256(digest_input.encode("utf-8")).hexdigest() == document["generator"][
        "sourceDigest"
    ]
    assert _sha256(ROOT / document["upstreamLock"]["path"]) == document["upstreamLock"][
        "sha256"
    ]
    assert _sha256(ROOT / document["dispositionPolicy"]["path"]) == document[
        "dispositionPolicy"
    ]["sha256"]


def test_lock_has_exact_toolchain_denominator_categories_and_candidates() -> None:
    document = _load_lock()
    coverage = document["coverage"]
    assert document["schemaVersion"] == 2
    assert document["generator"]["pythonToolchain"] == {
        "implementation": "CPython",
        "runtimeVersion": "3.12.13",
        "requiredRuntimeVersion": "3.12.13",
        "requiredMajorMinor": "3.12",
        "parser": "cpython-stdlib-ast-pep701",
        "grammarVersion": "3.12",
        "featureVersion": [3, 12],
        "pep701": True,
        "fallbackParser": None,
    }
    assert coverage["denominator"] == len(document["units"]) == 3845
    assert coverage["rawObservations"] == 3852
    assert coverage["reconciledDuplicateDeclarations"] == 7
    assert coverage["byUpstream"] == {
        "chatwoot": 1117,
        "chattermate": 279,
        "bottlecrm": 667,
        "rocketchat": 1705,
        "rocket-embedded-chat": 77,
    }
    assert tuple(coverage["byCategory"]) == CATEGORIES
    assert all(coverage["byCategory"][category] > 0 for category in CATEGORIES)
    assert coverage["candidates"] == {"accepted": 3354, "excluded": 46, "manual_review": 0}
    assert coverage["candidatesByReason"] == {
        "developer_schema_ui_not_product_capability": 3,
        "django_admin_mount_not_product_capability": 1,
        "django_auth_framework_route_not_product_capability": 1,
        "dynamic_registration_adapter": 1,
        "include_mount_container": 2,
        "proprietary_guard": 27,
        "routing_container_only": 11,
    }


def test_lock_proves_all_pin_dimensions_and_explicit_sparse_submodule_state() -> None:
    document = _load_lock()
    assert tuple(pin["name"] for pin in document["pins"]) == REQUIRED_UPSTREAMS
    assert document["coverage"]["allPinsVerified"] is True
    for pin in document["pins"]:
        proof = pin["sourceVerification"]
        assert proof["status"] == "verified"
        assert proof["canonical"] is True
        assert proof["sourceMode"] == "git_commit_tree"
        assert proof["origin"] == pin["repository"]
        assert proof["ref"] == pin["ref"]
        assert proof["commit"] == pin["commit"]
        assert proof["headDetached"] is True
        assert proof["partialClone"] == {"promisor": True, "filter": "blob:none"}
        assert proof["checkout"]["status"] == "canonical_commit_tree"
        assert proof["checkout"]["inputIsolation"] == (
            "pinned_git_objects_materialized_to_temporary_directory"
        )
        assert proof["checkout"]["worktreeInspected"] is False
        assert proof["checkout"]["trackedChanges"] is None
        assert proof["checkout"]["untrackedOrIgnored"] is None
        assert proof["submodules"]["policy"] == "do-not-initialize"
        assert proof["submodules"]["initialized"] == []
        assert proof["selectedBlobs"] > 0
        assert proof["selectedBytes"] > 0
        assert re.fullmatch(r"[0-9a-f]{64}", proof["selectedManifestSha256"])
        assert re.fullmatch(r"[0-9a-f]{64}", proof["selectedContentManifestSha256"])
        if pin["checkout"]["mode"] == "sparse-excluding-proprietary":
            assert proof["checkout"]["sparseEnabled"] is True
            assert proof["checkout"]["excludedPrefixes"]
        else:
            assert proof["checkout"]["sparseEnabled"] is False
            assert proof["checkout"]["excludedPrefixes"] == []
    chattermate = next(pin for pin in document["pins"] if pin["name"] == "chattermate")
    assert chattermate["sourceVerification"]["submodules"]["gitlinks"] == [
        "backend/app/enterprise",
        "frontend/src/modules/enterprise",
    ]


def test_135_family_registry_provider_runtime_and_test_evidence_are_validated() -> None:
    baseline = _load_lock()["familyBaseline"]
    assert baseline["status"] == "validated"
    assert baseline["familyCount"] == baseline["expectedFamilyCount"] == 135
    assert baseline["byProvider"] == {
        "chatwoot": 39,
        "rocketchat": 33,
        "chattermate": 27,
        "bottlecrm": 36,
    }
    assert baseline["providerValidation"] == "exact"
    assert baseline["upstreamEvidence"] == {
        "status": "paths_verified_against_materialized_pins",
        "pathCount": 154,
    }
    assert baseline["runtimeEvidence"]["status"] == "paths_and_bytes_verified"
    assert baseline["runtimeEvidence"]["pathCount"] >= 54
    assert baseline["runtimeEvidence"]["fileCount"] == sum(
        item["fileCount"] for item in baseline["runtimeEvidence"]["paths"]
    )
    assert baseline["runtimeEvidence"]["fileCount"] >= 120
    assert baseline["testEvidence"]["status"] == "paths_and_bytes_verified"
    assert baseline["testEvidence"]["pathCount"] >= 28
    assert baseline["testEvidence"]["fileCount"] >= 31
    manifest = baseline["verificationManifest"]
    assert manifest["contractBinding"] == "derived_from_registry"
    assert (manifest["nativeCount"], manifest["contractCount"], manifest["actionCount"]) == (
        135,
        121,
        511,
    )


def test_units_have_canonical_structural_identity_and_resolved_license_scope() -> None:
    document = _load_lock()
    identities: set[tuple[str, str]] = set()
    for unit in document["units"]:
        source = unit["source"]
        values = json.loads(unit["contractKey"])
        assert values == [
            source["transport"],
            source["operation"],
            source["route"],
            source["handler"],
        ]
        assert "#declaration" not in unit["contractKey"]
        identity = (unit["upstream"], unit["contractKey"])
        assert identity not in identities
        identities.add(identity)
        assert unit["license"]["status"] in {"verified-file", "package-metadata-only"}

    embedded = [unit for unit in document["units"] if unit["upstream"] == "rocket-embedded-chat"]
    assert len(embedded) == 77
    assert {unit["license"]["scopePrefix"] for unit in embedded} == {
        "packages/api",
        "packages/react",
    }
    assert {unit["license"]["package"]["name"] for unit in embedded} == {
        "@embeddedchat/api",
        "@embeddedchat/react",
    }
    assert {unit["disposition"]["state"] for unit in embedded} == {"blocked_license"}


def test_lock_contains_the_audited_framework_specific_surfaces() -> None:
    units = _load_lock()["units"]
    assert any(
        unit["upstream"] == "chatwoot" and unit["source"]["family"] == "rails-devise-route"
        for unit in units
    )
    assert any(
        unit["upstream"] == "chattermate"
        and unit["source"]["details"].get("application") == "public_app"
        for unit in units
    )
    assert any(
        unit["upstream"] == "bottlecrm" and unit["source"]["family"] == "sveltekit-form-action"
        for unit in units
    )
    rocket_methods = {
        unit["source"]["operation"]
        for unit in units
        if unit["upstream"] == "rocketchat"
        and unit["source"]["route"] == "/livechat/appearance"
        and unit["source"]["handler"].startswith("addRoute.")
    }
    assert rocket_methods >= {"GET", "POST"}
    assert any(
        unit["upstream"] == "rocketchat" and unit["source"]["family"] == "meteor-ddp-method"
        for unit in units
    )
