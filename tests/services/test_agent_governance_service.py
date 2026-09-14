from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from app.data.legal_area_catalog import SPECIALIZED_AGENT_BY_KEY
from app.schemas.playbook import InternalPlaybookContent
from app.services.agent_governance_service import (
    DEFAULT_AUTONOMY_MODE,
    MAX_DELEGATION_DEPTH,
    PARENT_BY_KEY,
    AgentGovernanceService,
    AgentGovernanceViolation,
    AutonomyLevel,
    autonomy_level_for_mode,
    build_handoff_summary,
    validate_agent_hierarchy,
)
from app.services.playbook_defaults import default_playbook_content


def _authorize(**overrides):
    values = {
        "target_agent_key": "saude_medicamentos",
        "initiator_type": "human",
        "requested_by_agent_key": None,
        "delegation_depth": 0,
        "matter_area_key": "saude_suplementar",
        "matter_product_key": "medicamento_alto_custo",
        "run_workspace_id": "workspace-a",
        "matter_workspace_id": "workspace-a",
        "context_client_id": "cliente-a",
        "matter_client_id": "cliente-a",
        "manual_context_revision": 3,
        "area_enabled": True,
        "request_human": False,
    }
    values.update(overrides)
    return AgentGovernanceService.authorize(**values)


def test_catalog_hierarchy_is_complete_acyclic_and_versioned() -> None:
    validate_agent_hierarchy()

    assert PARENT_BY_KEY["orquestrador"] is None
    assert PARENT_BY_KEY["hermes"] == "secretaria"
    assert PARENT_BY_KEY["saude_medicamentos"] == "juridico_saude"
    assert set(SPECIALIZED_AGENT_BY_KEY).issubset(PARENT_BY_KEY)
    assert AgentGovernanceService.hierarchy_path("saude_medicamentos") == (
        "orquestrador",
        "juridico_saude",
        "saude_medicamentos",
    )
    assert AgentGovernanceService.hierarchy_path("hermes") == (
        "orquestrador",
        "secretaria",
        "hermes",
    )
    snapshot = AgentGovernanceService.policy_snapshot()
    assert snapshot["manualOverrideWins"] is True
    assert snapshot["crossWorkspaceAccessDenied"] is True
    assert snapshot["crossClientAccessDenied"] is True
    assert snapshot["crossProductAccessDisabled"] is True
    assert snapshot["leastPrivilegeTools"] is True
    assert snapshot["externalActionsAllowed"] is False
    assert snapshot["maxDelegationDepth"] == MAX_DELEGATION_DEPTH
    assert snapshot["ownerBridgeAgentKey"] == "hermes"
    assert snapshot["ownerBridgeDelegatorKey"] == "secretaria"
    assert snapshot["version"] == "agent-governance-v1.5.0"
    assert snapshot["policyDigestAlgorithm"] == "sha256"
    assert re.fullmatch(r"[0-9a-f]{64}", snapshot["policyDigest"])
    assert snapshot["policyDigest"] == AgentGovernanceService.policy_snapshot()["policyDigest"]
    assert snapshot["hermesOnlyHumanBridge"] is False
    assert snapshot["humanQueueCoordinator"] == "orquestrador"
    assert snapshot["delegationRules"] == {
        "directParentOnly": True,
        "ownerBridgeOnlyFrom": "secretaria",
        "directOwnerBridgeInvocationDisabled": True,
        "independentControlSystemOnly": True,
        "depthValidated": True,
    }
    assert set(snapshot["frozenAreaSafeAgents"]) == {
        "auditor",
        "hermes",
        "orquestrador",
        "qualidade",
        "ti",
    }


def test_exact_area_and_product_scope_is_authorized() -> None:
    decision = _authorize(
        available_tool_keys=("conversation_rag", "document_rag"),
        requested_tool_keys=("document_rag",),
    )

    assert decision.area_key == "saude_suplementar"
    assert decision.product_key == "medicamento_alto_custo"
    assert decision.manual_context_revision == 3
    assert decision.manual_override_wins is True
    assert decision.external_actions_allowed is False
    assert decision.policy_digest_algorithm == "sha256"
    assert decision.policy_digest == AgentGovernanceService.policy_snapshot()["policyDigest"]
    assert decision.allowed_tool_keys == ("conversation_rag", "document_rag")
    assert decision.requested_tool_keys == ("document_rag",)
    assert decision.allowed_delegate_keys == ()


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"area_enabled": False}, "AREA_FROZEN"),
        ({"matter_area_key": "bancario"}, "AGENT_SCOPE_MISMATCH"),
        ({"matter_product_key": "cirurgia"}, "AGENT_PRODUCT_SCOPE_MISMATCH"),
        ({"matter_product_key": None}, "AGENT_PRODUCT_SCOPE_MISMATCH"),
        ({"matter_workspace_id": "workspace-b"}, "CROSS_WORKSPACE_ACCESS_DENIED"),
        ({"matter_client_id": "cliente-b"}, "CROSS_CLIENT_ACCESS_DENIED"),
        ({"matter_workspace_id": ""}, "WORKSPACE_SCOPE_REQUIRED"),
        ({"matter_client_id": ""}, "CLIENT_SCOPE_REQUIRED"),
        ({"initiator_type": "agent"}, "DELEGATOR_REQUIRED"),
        ({"initiator_type": "worker"}, "INITIATOR_TYPE_INVALID"),
        ({"delegation_depth": 1}, "DELEGATION_DEPTH_INVALID"),
        ({"requested_action": "send_external"}, "HUMAN_GATE_REQUIRED"),
        (
            {
                "available_tool_keys": ("conversation_rag",),
                "requested_tool_keys": ("document_rag",),
            },
            "TOOL_SCOPE_DENIED",
        ),
    ],
)
def test_governance_fails_closed(overrides, code) -> None:
    with pytest.raises(AgentGovernanceViolation) as captured:
        _authorize(**overrides)

    assert captured.value.code == code


def test_autonomy_default_is_hil_total_and_fails_closed_on_grant_discount() -> None:
    """Default autonomy (no workspace override) must never let an agent grant
    a discount on its own, even with a valid-looking band, because the mode
    itself has not been switched to 'autonomo' by a human."""
    with pytest.raises(AgentGovernanceViolation) as captured:
        _authorize(
            requested_action="grant_discount",
            negotiation_floor_pct=0.85,
            negotiation_band_pct=0.1,
            requested_discount_pct=0.05,
        )

    assert captured.value.code == "HUMAN_GATE_REQUIRED"


def test_autonomous_grant_discount_within_pre_approved_band_is_allowed_with_audit() -> None:
    decision = _authorize(
        requested_action="grant_discount",
        autonomy_mode="autonomo",
        negotiation_floor_pct=0.85,
        negotiation_band_pct=0.10,
        requested_discount_pct=0.08,
    )

    assert decision.autonomy_mode == "autonomo"
    assert decision.autonomous_grant_applied is True
    assert decision.requested_action == "grant_discount"
    # manual_override_wins is untouched by the autonomy exception path.
    assert decision.manual_override_wins is True


@pytest.mark.parametrize(
    "overrides",
    [
        # Below the floor: 1 - 0.20 = 0.80 < floor 0.85.
        {
            "negotiation_floor_pct": 0.85,
            "negotiation_band_pct": 0.30,
            "requested_discount_pct": 0.20,
        },
        # Above the pre-approved band, even though it would stay above the floor.
        {
            "negotiation_floor_pct": 0.5,
            "negotiation_band_pct": 0.05,
            "requested_discount_pct": 0.10,
        },
        # No band configured at all: must always escalate to a human.
        {
            "negotiation_floor_pct": None,
            "negotiation_band_pct": None,
            "requested_discount_pct": 0.05,
        },
    ],
)
def test_autonomous_grant_discount_outside_band_or_below_floor_requires_human(overrides) -> None:
    with pytest.raises(AgentGovernanceViolation) as captured:
        _authorize(
            requested_action="grant_discount",
            autonomy_mode="autonomo",
            **overrides,
        )

    assert captured.value.code == "HUMAN_GATE_REQUIRED"


@pytest.mark.parametrize("hard_action", ["sign_contract", "file_petition", "protocol_petition"])
def test_hard_human_gates_stay_hard_in_autonomo_mode(hard_action) -> None:
    """sign_contract/file_petition/protocol_petition never get an autonomy
    bypass, no matter the mode or how generous the negotiation band is."""
    with pytest.raises(AgentGovernanceViolation) as captured:
        _authorize(
            requested_action=hard_action,
            autonomy_mode="autonomo",
            negotiation_floor_pct=0.0,
            negotiation_band_pct=1.0,
            requested_discount_pct=0.0,
        )

    assert captured.value.code == "HUMAN_GATE_REQUIRED"


def test_manual_override_wins_is_untouched_by_autonomy() -> None:
    decision = _authorize(autonomy_mode="hil_marcos")
    assert decision.manual_override_wins is True
    assert decision.autonomy_mode == "hil_marcos"
    assert decision.autonomous_grant_applied is False


def test_unknown_autonomy_mode_fails_closed() -> None:
    with pytest.raises(AgentGovernanceViolation) as captured:
        _authorize(autonomy_mode="totalmente_livre")

    assert captured.value.code == "AUTONOMY_MODE_INVALID"


def test_authorize_defaults_to_hil_total_when_autonomy_not_passed() -> None:
    decision = _authorize()
    assert decision.autonomy_mode == DEFAULT_AUTONOMY_MODE == "hil_total"


# ── Plano Mestre §14 — mapeamento modo HIL -> nível A0-A4 canônico ──


def test_autonomy_mode_maps_to_canonical_a_level() -> None:
    assert autonomy_level_for_mode("hil_total") == AutonomyLevel.A4_HUMAN_REQUIRED
    assert autonomy_level_for_mode("hil_marcos") == AutonomyLevel.A2_PREPARE
    assert autonomy_level_for_mode("autonomo") == AutonomyLevel.A3_EXECUTE_INTERNAL_REVERSIBLE

    assert _authorize().autonomy_level == "A4"
    assert _authorize(autonomy_mode="hil_marcos").autonomy_level == "A2"
    assert _authorize(autonomy_mode="autonomo").autonomy_level == "A3"


def test_autonomy_level_for_mode_fails_closed_on_unknown_mode() -> None:
    with pytest.raises(AgentGovernanceViolation) as captured:
        autonomy_level_for_mode("totalmente_livre")
    assert captured.value.code == "AUTONOMY_MODE_INVALID"


def test_authorize_without_execution_request_never_triggers_level_enforcement() -> None:
    """Nenhum chamador anterior a este enforcement (autonomous_execution_requested
    default False) muda de comportamento — inclusive em A4/hil_total."""
    decision = _authorize()
    assert decision.autonomous_execution_requested is False
    assert decision.autonomous_execution_applied is False


def test_autonomy_level_a2_prepares_but_never_executes() -> None:
    # hil_marcos (A2) can be authorized normally (prepare)...
    decision = _authorize(autonomy_mode="hil_marcos")
    assert decision.autonomy_level == "A2"
    assert decision.autonomous_execution_applied is False

    # ...but asking it to actually execute autonomously is always refused.
    with pytest.raises(AgentGovernanceViolation) as captured:
        _authorize(autonomy_mode="hil_marcos", autonomous_execution_requested=True)
    assert captured.value.code == "AUTONOMY_LEVEL_A2_PREPARES_ONLY"


def test_autonomy_level_a3_executes_internal_reversible_but_external_stays_gated() -> None:
    decision = _authorize(
        autonomy_mode="autonomo",
        autonomous_execution_requested=True,
        action_is_external=False,
    )
    assert decision.autonomy_level == "A3"
    assert decision.autonomous_execution_applied is True
    assert decision.action_is_external is False

    with pytest.raises(AgentGovernanceViolation) as captured:
        _authorize(
            autonomy_mode="autonomo",
            autonomous_execution_requested=True,
            action_is_external=True,
        )
    assert captured.value.code == "AUTONOMY_LEVEL_A3_EXTERNAL_ACTION_GATED"


def test_autonomy_level_a4_always_requires_a_human() -> None:
    with pytest.raises(AgentGovernanceViolation) as captured:
        _authorize(autonomy_mode="hil_total", autonomous_execution_requested=True)
    assert captured.value.code == "AUTONOMY_LEVEL_A4_HUMAN_REQUIRED"

    # Even asking for an internal-only action does not open a bypass at A4.
    with pytest.raises(AgentGovernanceViolation) as captured_internal:
        _authorize(
            autonomy_mode="hil_total",
            autonomous_execution_requested=True,
            action_is_external=False,
        )
    assert captured_internal.value.code == "AUTONOMY_LEVEL_A4_HUMAN_REQUIRED"


def test_policy_snapshot_exposes_autonomy_level_by_mode() -> None:
    snapshot = AgentGovernanceService.policy_snapshot()
    assert snapshot["autonomyLevelByMode"] == {
        "hil_total": "A4",
        "hil_marcos": "A2",
        "autonomo": "A3",
    }
    assert set(snapshot["autonomyLevels"]) == {"A0", "A1", "A2", "A3", "A4"}


def test_build_handoff_summary_folds_thesis_dossier_objections_and_next_step() -> None:
    summary = build_handoff_summary(
        {
            "thesis": "Negativa de cobertura de cirurgia bariátrica",
            "dossier": {
                "legalAreaKey": "saude_suplementar",
                "legalProductKey": "cirurgia",
                "clientName": "Maria",
                "evidenceRefs": ["doc-1", "doc-2"],
            },
            "objectionsHandled": ["Cliente questionou prazo", "Cliente pediu desconto"],
            "nextStep": "Encaminhar para o closer com proposta.",
        }
    )

    assert summary.thesis == "Negativa de cobertura de cirurgia bariátrica"
    assert "saude_suplementar" in summary.dossier_summary
    assert "cirurgia" in summary.dossier_summary
    assert "Maria" in summary.dossier_summary
    assert "2 evidência" in summary.dossier_summary
    assert summary.objections_handled == (
        "Cliente questionou prazo",
        "Cliente pediu desconto",
    )
    assert summary.next_step == "Encaminhar para o closer com proposta."

    payload = summary.as_payload()
    assert payload["schemaVersion"] == "agent-handoff-summary-v1"
    assert payload["objectionsHandled"] == [
        "Cliente questionou prazo",
        "Cliente pediu desconto",
    ]


def test_build_handoff_summary_is_pure_and_never_fabricates_missing_data() -> None:
    summary = build_handoff_summary({})

    assert summary.thesis == "Tese ainda não identificada nesta etapa."
    assert summary.dossier_summary == "Nenhum dossiê levantado até aqui."
    assert summary.objections_handled == ()
    assert "aguardar orientação" in summary.next_step.lower()

    # Pure function: same input always yields the same output, no I/O.
    assert build_handoff_summary({}) == summary


def test_agent_delegation_must_follow_declared_parent_chain() -> None:
    decision = _authorize(
        initiator_type="agent",
        requested_by_agent_key="juridico_saude",
        delegation_depth=2,
    )
    assert decision.requested_by_agent_key == "juridico_saude"

    with pytest.raises(AgentGovernanceViolation) as invalid_parent:
        _authorize(
            initiator_type="agent",
            requested_by_agent_key="sdr_saude",
            delegation_depth=1,
        )
    assert invalid_parent.value.code == "INVALID_DELEGATION_PATH"

    with pytest.raises(AgentGovernanceViolation) as invalid_depth:
        _authorize(
            initiator_type="agent",
            requested_by_agent_key="juridico_saude",
            delegation_depth=1,
        )
    assert invalid_depth.value.code == "DELEGATION_DEPTH_INCONSISTENT"

    with pytest.raises(AgentGovernanceViolation) as self_delegation:
        _authorize(
            target_agent_key="juridico_saude",
            initiator_type="agent",
            requested_by_agent_key="juridico_saude",
            delegation_depth=1,
            matter_product_key="tratamento",
        )
    assert self_delegation.value.code == "SELF_DELEGATION_BLOCKED"


def test_hermes_is_owner_bridge_and_never_orchestrator() -> None:
    with pytest.raises(AgentGovernanceViolation) as direct_bridge:
        _authorize(
            target_agent_key="hermes",
            request_human=True,
        )
    assert direct_bridge.value.code == "OWNER_BRIDGE_DELEGATOR_REQUIRED"

    handoff = _authorize(
        target_agent_key="hermes",
        initiator_type="agent",
        requested_by_agent_key="secretaria",
        delegation_depth=2,
        request_human=True,
    )
    assert handoff.human_request_agent_key == "orquestrador"
    assert handoff.human_handoff_required is True
    assert handoff.hierarchy_path == ("orquestrador", "secretaria", "hermes")

    with pytest.raises(AgentGovernanceViolation) as skipped_secretary:
        _authorize(
            target_agent_key="hermes",
            initiator_type="agent",
            requested_by_agent_key="saude_medicamentos",
            delegation_depth=2,
            request_human=True,
        )
    assert skipped_secretary.value.code == "OWNER_BRIDGE_DELEGATOR_REQUIRED"

    with pytest.raises(AgentGovernanceViolation) as no_human_request:
        _authorize(
            target_agent_key="hermes",
            initiator_type="agent",
            requested_by_agent_key="secretaria",
            delegation_depth=2,
            request_human=False,
        )
    assert no_human_request.value.code == "OWNER_BRIDGE_REQUEST_REQUIRED"

    with pytest.raises(AgentGovernanceViolation) as invalid_bridge_depth:
        _authorize(
            target_agent_key="hermes",
            initiator_type="agent",
            requested_by_agent_key="secretaria",
            delegation_depth=1,
            request_human=True,
        )
    assert invalid_bridge_depth.value.code == "DELEGATION_DEPTH_INCONSISTENT"

    secretary = _authorize(
        target_agent_key="secretaria",
        matter_product_key="medicamento_alto_custo",
    )
    assert "hermes" in secretary.allowed_delegate_keys

    with pytest.raises(AgentGovernanceViolation) as captured:
        _authorize(
            target_agent_key="juridico_saude",
            initiator_type="agent",
            requested_by_agent_key="hermes",
            delegation_depth=1,
            matter_product_key="tratamento",
        )
    assert captured.value.code == "OWNER_BRIDGE_CANNOT_ORCHESTRATE"


def test_independent_control_agents_only_start_from_human_or_system() -> None:
    direct = _authorize(
        target_agent_key="auditor",
        initiator_type="system",
        matter_area_key="bancario",
        matter_product_key="fraude_pix",
    )
    assert direct.target_agent_key == "auditor"

    with pytest.raises(AgentGovernanceViolation) as captured:
        _authorize(
            target_agent_key="auditor",
            initiator_type="agent",
            requested_by_agent_key="orquestrador",
            delegation_depth=1,
            matter_area_key="bancario",
            matter_product_key="fraude_pix",
        )
    assert captured.value.code == "INDEPENDENT_CONTROL_REQUIRED"


def test_frozen_area_blocks_business_agents_but_keeps_control_path_available() -> None:
    with pytest.raises(AgentGovernanceViolation) as blocked:
        _authorize(
            target_agent_key="sdr",
            matter_product_key="medicamento_alto_custo",
            area_enabled=False,
        )
    assert blocked.value.code == "AREA_FROZEN"

    for safe_agent in ("orquestrador", "auditor", "ti", "qualidade"):
        decision = _authorize(
            target_agent_key=safe_agent,
            matter_product_key="medicamento_alto_custo",
            area_enabled=False,
        )
        assert decision.target_agent_key == safe_agent
        assert decision.external_actions_allowed is False
        assert decision.allowed_delegate_keys == ()

    owner_bridge = _authorize(
        target_agent_key="hermes",
        initiator_type="agent",
        requested_by_agent_key="secretaria",
        delegation_depth=2,
        matter_product_key="medicamento_alto_custo",
        area_enabled=False,
        request_human=True,
    )
    assert owner_bridge.target_agent_key == "hermes"
    assert owner_bridge.hierarchy_path == ("orquestrador", "secretaria", "hermes")
    assert owner_bridge.external_actions_allowed is False

    handoff = _authorize(
        target_agent_key="orquestrador",
        area_enabled=False,
        request_human=True,
    )
    assert handoff.human_handoff_required is True

    with pytest.raises(AgentGovernanceViolation) as secretary_business_action:
        _authorize(
            target_agent_key="secretaria",
            area_enabled=False,
            request_human=False,
        )
    assert secretary_business_action.value.code == "AREA_FROZEN"


def test_default_playbook_uses_only_authorized_direct_handoffs() -> None:
    content = default_playbook_content()
    by_key = {agent.key: agent for agent in content.agents}

    assert by_key["orquestrador"].parent_agent_key is None
    assert by_key["secretaria"].handoffs == ["hermes"]
    assert by_key["hermes"].parent_agent_key == "secretaria"
    assert by_key["hermes"].handoffs == []
    assert "fila_humana" in by_key["orquestrador"].handoffs
    assert by_key["hermes"].independent_control is False
    assert by_key["auditor"].independent_control is True
    assert all(
        "hermes" not in agent.handoffs
        for agent in content.agents
        if agent.key != "secretaria"
    )


def test_playbook_rejects_direct_shortcut_to_hermes() -> None:
    payload = default_playbook_content().model_dump(mode="json", by_alias=True)
    sdr = next(agent for agent in payload["agents"] if agent["key"] == "sdr")
    sdr["handoffs"] = ["hermes"]

    with pytest.raises(ValidationError, match="violates the agent hierarchy"):
        InternalPlaybookContent.model_validate(payload)


def test_playbook_rejects_unknown_handoff_targets() -> None:
    payload = default_playbook_content().model_dump(mode="json", by_alias=True)
    sdr = next(agent for agent in payload["agents"] if agent["key"] == "sdr")
    sdr["handoffs"] = ["agente_inexistente"]

    with pytest.raises(ValidationError, match="unknown handoff target"):
        InternalPlaybookContent.model_validate(payload)


def test_custom_subagents_follow_their_declared_parent() -> None:
    payload = default_playbook_content().model_dump(mode="json", by_alias=True)
    custom_parent = {
        **next(agent for agent in payload["agents"] if agent["key"] == "sdr"),
        "key": "especialista_customizado",
        "label": "Especialista customizado",
        "parentAgentKey": "orquestrador",
        "handoffs": ["subagente_customizado"],
    }
    custom_child = {
        **custom_parent,
        "key": "subagente_customizado",
        "label": "Subagente customizado",
        "parentAgentKey": "especialista_customizado",
        "handoffs": [],
    }
    payload["agents"].extend([custom_parent, custom_child])

    validated = InternalPlaybookContent.model_validate(payload)
    assert any(agent.key == "subagente_customizado" for agent in validated.agents)

    broken = validated.model_dump(mode="json", by_alias=True)
    parent = next(agent for agent in broken["agents"] if agent["key"] == "especialista_customizado")
    parent["handoffs"] = ["sdr"]
    with pytest.raises(ValidationError, match="violates the agent hierarchy"):
        InternalPlaybookContent.model_validate(broken)


def test_custom_agents_cannot_create_an_uncontrolled_root() -> None:
    payload = default_playbook_content().model_dump(mode="json", by_alias=True)
    custom_root = {
        **next(agent for agent in payload["agents"] if agent["key"] == "sdr"),
        "key": "raiz_customizada",
        "label": "Raiz customizada",
        "parentAgentKey": None,
        "handoffs": [],
    }
    payload["agents"].append(custom_root)

    with pytest.raises(ValidationError, match="must report to a registered parent"):
        InternalPlaybookContent.model_validate(payload)
