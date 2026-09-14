from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import verify_oss_function_unit_release as release


def _catalog() -> dict[str, object]:
    return {"licensed": [{"id": "unit-a"}, {"id": "unit-b"}]}


def test_native_binding_contract_reports_primary_without_global_verified_promotion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bindings = {
        "unit-a": SimpleNamespace(maturity="native_primary", binding_id="a" * 64),
        "unit-b": SimpleNamespace(maturity="native_primary", binding_id="b" * 64),
    }
    monkeypatch.setattr(release, "native_function_unit_bindings", lambda: bindings)
    monkeypatch.setattr(
        release,
        "native_function_unit_binding_summary",
        lambda: {
            "bindings": 2,
            "bindingIdsDigest": "c" * 64,
            "maturity": {
                "mapped": 0,
                "native_shadow": 0,
                "native_primary": 2,
                "native_verified": 0,
            },
        },
    )

    contract = release._native_binding_contract(_catalog())

    assert contract["mappedUnitIds"] == ["unit-a", "unit-b"]
    assert contract["nativePrimaryUnitIds"] == ["unit-a", "unit-b"]
    assert contract["nativeVerifiedBindings"] == []


def test_native_binding_contract_rejects_verified_unit_without_individual_oracle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bindings = {
        "unit-a": SimpleNamespace(
            maturity="native_verified",
            binding_id="a" * 64,
            proof={"status": "passed", "bindingId": "a" * 64},
        ),
        "unit-b": SimpleNamespace(maturity="native_primary", binding_id="b" * 64),
    }
    monkeypatch.setattr(release, "native_function_unit_bindings", lambda: bindings)
    monkeypatch.setattr(
        release,
        "native_function_unit_binding_summary",
        lambda: {
            "bindings": 2,
            "bindingIdsDigest": "c" * 64,
            "maturity": {
                "mapped": 0,
                "native_shadow": 0,
                "native_primary": 1,
                "native_verified": 1,
            },
        },
    )

    with pytest.raises(RuntimeError, match="oracle upstream individual"):
        release._native_binding_contract(_catalog())


def test_architecture_quality_gate_covers_every_licensed_unit_without_violations() -> None:
    catalog = release._catalog_contract()

    quality = release._architecture_quality_contract(catalog)

    assert quality["unitsChecked"] == 3768
    assert quality["violations"] == 0
    assert quality["qualityGateVersion"] == 2
    assert quality["nativePrimaryUnits"] == 1367
    assert quality["nativeShadowUnits"] == 2401
    assert quality["ambiguousBoundaries"] > 0
    assert (
        quality["ambiguousReadOnlyContracts"]
        + quality["ambiguousShadowMutationDescriptors"]
        == quality["ambiguousBoundaries"]
    )
    assert quality["ambiguousMutationExecutionAllowed"] == 0
    assert quality["workspaceAdminUnits"] > 0
    assert quality["humanVetoUnits"] > 0
    assert quality["aggregatePolicy"] == {
        "schemaVersion": 1,
        "optimisticConcurrencyRequired": True,
        "deepMergeVerified": True,
        "archiveStateMachineVerified": True,
        "canonicalDigestVerified": True,
    }
    assert quality["mcpReadUnits"] == 4
    assert quality["mcpMutatingUnits"] == 4
    assert quality["transactionalOutboxUnits"] > 0
    assert quality["publicProductVocabularyLeaks"] == 0
    assert quality["externalNetworkAllowances"] == 0
    assert quality["journalPrimaryUnits"] == 0
