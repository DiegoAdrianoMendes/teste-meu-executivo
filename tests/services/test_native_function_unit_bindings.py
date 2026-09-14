"""Conformance invariants for exact KLM native FunctionUnit bindings."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import app.services.native_function_unit_binding_service as binding_module
from app.services.native_function_unit_binding_service import (
    build_native_function_unit_binding,
    native_function_unit_binding_summary,
    native_function_unit_bindings,
)
from app.services.oss_function_unit_service import function_unit_catalog
from scripts.build_native_function_unit_fixtures import build_document

ROOT = Path(__file__).resolve().parents[2]
CONFORMANCE = ROOT / "app" / "data" / "oss_function_unit_conformance.json"


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_every_licensed_unit_has_one_typed_non_deceptive_binding() -> None:
    catalog = function_unit_catalog()
    licensed_ids = {
        unit["id"]
        for unit in catalog["units"]
        if unit["license"]["status"] == "verified-file"
    }
    bindings = native_function_unit_bindings()
    summary = native_function_unit_binding_summary()

    assert len(bindings) == len(licensed_ids) == 3768
    assert set(bindings) == licensed_ids
    assert len({binding.binding_id for binding in bindings.values()}) == len(bindings)
    assert summary["maturity"] == {
        "mapped": 0,
        "native_shadow": 2401,
        "native_primary": 1367,
        "native_verified": 0,
    }
    assert sum(summary["equivalenceGroups"].values()) == len(bindings)

    for unit_id, binding in bindings.items():
        assert binding.action_id == binding.equivalence_group
        assert binding.native_operation_id.endswith(unit_id.rsplit(":", 1)[-1])
        assert binding.state_transition["recordStore"] == "oss_native_records"
        assert binding.state_transition["journalIsPrimary"] is False
        assert binding.effects["externalNetworkAllowed"] is False
        assert binding.authorization_policy["workspaceScoped"] is True
        Draft202012Validator.check_schema(binding.input_schema)
        Draft202012Validator.check_schema(binding.output_schema)
        if binding.mapping_strategy == "capability_unbound":
            assert binding.capability_action_id is None
            assert binding.maturity == "native_shadow"
        if binding.state_transition["effect"] == "archive":
            assert binding.authorization_policy["humanVeto"] is True
            assert binding.authorization_policy["singleUse"] is True
            assert binding.authorization_policy["intentBound"] is True
            assert "humanApprovalId" in binding.input_schema["properties"]


def test_fixture_ledger_is_exact_and_schema_valid_for_both_runtime_modes() -> None:
    document = build_document()
    bindings = native_function_unit_bindings()
    fixtures = {row["unitId"]: row for row in document["fixtures"]}

    assert document["schemaVersion"] == 1
    assert document["fixtureCount"] == len(bindings) == len(fixtures)
    assert document["fixturesDigest"] == _digest(document["fixtures"])
    assert set(fixtures) == set(bindings)
    for unit_id, binding in bindings.items():
        fixture = fixtures[unit_id]
        assert fixture["bindingId"] == binding.binding_id
        assert fixture["nativeOperationId"] == binding.native_operation_id
        Draft202012Validator(binding.input_schema).validate(fixture["input"])
        Draft202012Validator(binding.output_schema).validate(fixture["simulatedOutput"])
        Draft202012Validator(binding.output_schema).validate(fixture["successOutput"])


def test_native_verified_requires_an_individual_oracle_proof() -> None:
    ledger = json.loads(CONFORMANCE.read_text(encoding="utf-8"))

    assert ledger["schemaVersion"] == 1
    assert ledger["proofs"] == []
    assert native_function_unit_binding_summary()["maturity"]["native_verified"] == 0


def _binding_unit() -> dict:
    unit = deepcopy(
        next(
            row
            for row in function_unit_catalog()["units"]
            if row["license"]["status"] == "verified-file"
        )
    )
    unit["name"] = "publish_record"
    unit["source"]["operation"] = "publish_record"
    unit["source"]["method"] = "POST"
    unit["klmMapping"]["coverageKind"] = "semantic_rule"
    return unit


def test_binding_rejects_unknown_authority_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = _binding_unit()
    monkeypatch.setattr(
        binding_module,
        "local_capability_contracts",
        lambda: {
            "klm.test": {
                "capability": "test",
                "actions": [
                    {
                        "command": "publish_record",
                        "effect": "upsert",
                        "authority": "unreviewed_superuser",
                    }
                ],
            }
        },
    )

    with pytest.raises(ValueError, match="Autoridade nativa desconhecida"):
        build_native_function_unit_binding(unit, capability_id="klm.test")


def test_binding_requires_human_veto_when_action_requests_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = _binding_unit()
    monkeypatch.setattr(
        binding_module,
        "local_capability_contracts",
        lambda: {
            "klm.test": {
                "capability": "test",
                "actions": [
                    {
                        "command": "publish_record",
                        "effect": "upsert",
                        "authority": "workspace_operator",
                        "requiresConfirmation": True,
                    }
                ],
            }
        },
    )

    binding = build_native_function_unit_binding(unit, capability_id="klm.test")

    assert binding.authorization_policy["humanVeto"] is True
    assert binding.authorization_policy["singleUse"] is True
    assert binding.authorization_policy["intentBound"] is True
