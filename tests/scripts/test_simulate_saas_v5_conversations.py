from __future__ import annotations

import socket
from copy import deepcopy
from pathlib import Path

import pytest

from app.ai.context_compiler import ContextCompiler
from scripts import simulate_saas_v5_conversations as simulator


@pytest.fixture(scope="module")
def contracts() -> simulator.SimulationContracts:
    return simulator.build_contracts()


@pytest.fixture(scope="module")
def evidence() -> dict:
    return simulator.simulate("p5-test-seed")


def test_full_simulation_is_deterministic_and_exhaustive(
    evidence: dict,
    tmp_path: Path,
) -> None:
    replay = simulator.simulate("p5-test-seed")

    assert replay == evidence
    assert evidence["conversationCount"] >= 1_000
    assert evidence["conversationCount"] == evidence["matrix"]["expectedCombinations"]
    assert evidence["matrix"]["observedCombinations"] == evidence["conversationCount"]
    assert evidence["coverage"]["passed"] is True
    assert all(evidence["coverage"]["checks"].values())

    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    simulator.write_evidence(first, evidence)
    simulator.write_evidence(second, replay)
    assert first.read_bytes() == second.read_bytes()

    digest_payload = dict(evidence)
    digest = digest_payload.pop("digest")
    assert simulator._digest(digest_payload) == digest
    assert simulator._digest(evidence["journeys"]) == evidence["journeysDigest"]


def test_simulation_executes_real_context_policy_and_provenance_contracts(
    evidence: dict,
) -> None:
    journeys = evidence["journeys"]

    assert evidence["contracts"]["contextCompilerVersion"]
    assert evidence["isolation"] == {
        "workspaceCrossReadBlocked": True,
        "matterCrossReadBlocked": True,
        "probeCount": 2,
        "passed": True,
    }
    assert all(item["workspaceId"].startswith("sim-workspace-") for item in journeys)
    assert all(item["matterId"].startswith("sim-matter-") for item in journeys)
    assert all(item["runtime"]["contextCompilerExecuted"] for item in journeys)
    assert all(item["runtime"]["requestPolicyValidated"] for item in journeys)
    assert all(item["runtime"]["scopeValidated"] for item in journeys)
    assert all(item["runtime"]["provenanceValidated"] for item in journeys)
    assert all(
        item["runtime"]["estimatedContextTokens"] <= item["runtime"]["tokenBudget"]
        for item in journeys
    )
    assert all(item["runtime"]["recentItemsOmitted"] >= 2 for item in journeys)
    assert all(item["runtime"]["sourceCount"] >= 3 for item in journeys)
    assert len({item["workspaceId"] for item in journeys}) == simulator.WORKSPACE_COUNT
    assert len({item["matterId"] for item in journeys}) == len(journeys)
    assert all(item["media"]["provenanceValidated"] for item in journeys)
    assert all(
        item["media"]["partCount"] == len(item["media"]["contentDigests"])
        for item in journeys
    )


def test_high_risk_terminal_contact_cadence_and_commercial_gates(evidence: dict) -> None:
    journeys = evidence["journeys"]
    high_risk = [item for item in journeys if item["gates"]["highRisk"]]
    terminal = [
        item
        for item in journeys
        if item["contactState"] in simulator.TERMINAL_CONTACT_STATES
    ]
    active = [item for item in journeys if item["contactState"] == "active"]
    upfront = [item for item in journeys if item["commercial"]["billingMode"] == "upfront"]
    success_only = [
        item for item in journeys if item["commercial"]["billingMode"] == "success_only"
    ]

    assert high_risk
    assert all(item["gates"]["humanRequired"] for item in high_risk)
    assert all(item["gates"]["handoffRequired"] for item in high_risk)
    assert all(item["gates"]["handoffDispatcher"] == "orquestrador" for item in high_risk)
    assert terminal
    assert all(item["cadence"]["plannedMessages"] == 0 for item in terminal)
    assert all(item["cadence"]["status"].startswith("suppressed_") for item in terminal)
    assert active
    assert all(item["cadence"]["plannedMessages"] > 0 for item in active)
    assert all(item["cadence"]["boundsValidated"] for item in active)
    assert upfront
    assert min(item["commercial"]["initialFeeBrl"] for item in upfront) == 4_000
    assert success_only
    assert all(item["commercial"]["successOption"] for item in success_only)
    assert all(item["gates"]["humanRequired"] for item in success_only)
    assert {item["collectionRequested"] for item in journeys} == {False, True}
    assert all(
        item["collectionStatus"] == item["commercial"]["status"] for item in journeys
    )
    assert all(all(value == 0 for value in item["effects"].values()) for item in journeys)


def test_validation_fails_when_a_mandatory_combination_is_missing(
    evidence: dict,
    contracts: simulator.SimulationContracts,
) -> None:
    incomplete = evidence["journeys"][:-1]

    with pytest.raises(simulator.SimulationValidationError, match="mandatoryCartesianMatrix"):
        simulator.validate_journeys(incomplete, contracts)


def test_validation_fails_when_a_high_risk_human_gate_is_removed(
    evidence: dict,
    contracts: simulator.SimulationContracts,
) -> None:
    broken = deepcopy(evidence["journeys"])
    target = next(item for item in broken if item["gates"]["highRisk"])
    target["gates"]["humanRequired"] = False

    with pytest.raises(simulator.SimulationValidationError, match="highRiskHumanGates"):
        simulator.validate_journeys(broken, contracts)


def test_validation_fails_when_opt_out_or_blocking_keeps_contacting(
    evidence: dict,
    contracts: simulator.SimulationContracts,
) -> None:
    broken = deepcopy(evidence["journeys"])
    target = next(
        item
        for item in broken
        if item["contactState"] in simulator.TERMINAL_CONTACT_STATES
    )
    target["cadence"]["plannedMessages"] = 1

    with pytest.raises(
        simulator.SimulationValidationError,
        match="terminalContactStopsCadence",
    ):
        simulator.validate_journeys(broken, contracts)


def test_scope_probes_and_network_guard_fail_closed() -> None:
    probes = simulator.run_scope_isolation_probes(
        ContextCompiler(recent_window_size=simulator.RECENT_WINDOW_SIZE)
    )
    assert probes["workspaceCrossReadBlocked"] is True
    assert probes["matterCrossReadBlocked"] is True

    guard = simulator.OfflineNetworkGuard()
    with guard, pytest.raises(simulator.OfflineNetworkAttemptError):
        socket.create_connection(("example.invalid", 443))
    assert guard.attempts == 1
