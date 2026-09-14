from __future__ import annotations

from app.services.oss_capability_registry import _CAPABILITIES
from app.services.oss_function_unit_runtime_service import (
    build_function_unit_contract,
)
from app.services.oss_function_unit_service import (
    OSSFunctionUnitNotFoundError,
    OSSFunctionUnitQuery,
    function_unit_catalog,
    get_function_unit,
    list_function_units,
)


def test_every_licensed_unit_has_one_explicit_consistent_native_mapping() -> None:
    catalog = function_unit_catalog()
    capabilities = {item["id"] for item in _CAPABILITIES}
    licensed = [
        unit
        for unit in catalog["units"]
        if unit["license"]["status"] == "verified-file"
    ]

    assert len(licensed) == 3768
    licensed_states = {unit["disposition"]["state"] for unit in licensed}
    assert licensed_states == {"native_shadow", "native_primary"}
    for unit in licensed:
        mapping = unit["klmMapping"]
        runtime_contract = build_function_unit_contract(unit)
        assert mapping["assessment"] == unit["disposition"]["state"]
        assert mapping["canonicalEntity"]
        assert mapping["spaRoute"].startswith("/app/capacidades/")
        assert mapping["applicationService"] == (
            "app.services.oss_function_unit_runtime_service."
            "OSSFunctionUnitRuntimeService"
        )
        assert mapping["capabilityId"] in capabilities
        assert mapping["localMode"] == (
            "klm_typed_native_primary_v3"
            if runtime_contract.maturity == "native_primary"
            else "klm_exact_contract_registry_v2"
        )
        assert mapping["apiRoute"] == (
            f"/api/v1/capabilities/functions/{unit['id']}/runtime"
        )
        assert mapping["runtimeContractId"] == runtime_contract.contract_id
        assert mapping["runtimeHandler"] == runtime_contract.handler
        assert mapping["semanticProfile"] == runtime_contract.semantic_profile
        assert mapping["proofProfile"] == runtime_contract.proof_profile
        assert mapping["coverageKind"] in {
            "semantic_rule",
            "lexical_rule",
            "category_default",
        }
        assert mapping["maturity"] == runtime_contract.maturity
        assert mapping["proofProfile"] == (
            "typed_native_primary_contract"
            if runtime_contract.maturity == "native_primary"
            else "typed_native_shadow_contract"
        )
        assert mapping["verificationManifest"] is None
        assert mapping["klmTests"]
        assert mapping["visualProof"]
        assert mapping["sidecarOffProof"]


def test_embedded_chat_units_remain_in_denominator_and_blocked_by_license() -> None:
    catalog = function_unit_catalog()
    embedded = [
        unit
        for unit in catalog["units"]
        if unit["upstream"] == "rocket-embedded-chat"
    ]

    assert len(embedded) == 77
    assert {unit["disposition"]["state"] for unit in embedded} == {
        "blocked_license"
    }
    assert {unit["klmMapping"]["assessment"] for unit in embedded} == {"blocked"}
    assert all(unit["klmMapping"]["spaRoute"] is None for unit in embedded)


def test_function_unit_listing_is_paginated_filterable_and_digest_bound() -> None:
    first = list_function_units(
        OSSFunctionUnitQuery(
            upstream="bottlecrm",
            category="mcp",
            page=1,
            page_size=3,
            search="crm_",
        )
    )

    assert first["scope"] == "pinned-community-function-units"
    assert len(first["digest"]) == 64
    expected_disposition = dict(function_unit_catalog()["coverage"]["byDisposition"])
    verified = int(expected_disposition["native_verified"])
    assert first["summary"] == {
        "denominator": 3845,
        "licensed": 3768,
        "blocked": 77,
        "verified": verified,
        "remaining": 3768 - verified,
        "byUpstream": {
            "chatwoot": 1117,
            "chattermate": 279,
            "bottlecrm": 667,
            "rocketchat": 1705,
            "rocket-embedded-chat": 77,
        },
        "byCategory": {
            "api": 1949,
            "ui": 398,
            "job": 132,
            "webhook": 27,
            "mcp": 8,
            "realtime": 150,
            "permission": 175,
            "config": 994,
            "import": 9,
            "export": 3,
        },
        "byDisposition": expected_disposition,
        "allPinsVerified": True,
    }
    assert first["total"] == 8
    assert first["pages"] == 3
    assert len(first["items"]) == 3
    assert all(unit["source"]["family"] == "fastmcp-tool" for unit in first["items"])

    exact = get_function_unit(first["items"][0]["id"])
    assert exact["id"] == first["items"][0]["id"]


def test_unknown_function_unit_fails_closed() -> None:
    try:
        get_function_unit("chatwoot:api:missing:0000000000000000")
    except OSSFunctionUnitNotFoundError:
        pass
    else:
        raise AssertionError("Unknown FunctionUnit must not resolve to a generic capability")
