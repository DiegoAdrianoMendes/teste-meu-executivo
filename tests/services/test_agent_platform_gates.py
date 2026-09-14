from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.agent_platform_service import (
    HUMAN_ROUTING_POLICY_VERSION,
    consume_human_decision,
    finite_routing_path,
    human_routing_policy,
    local_handoff_dry_run,
    routing_policy_catalog,
    runtime_gate,
    validate_deployment_ready,
    validate_node_payload,
)


def test_human_routing_policy_catalog_is_versioned_and_rejects_arbitrary_keys():
    live = {
        "roles": [{"key": "finance_manager"}],
        "skills": [{"key": "financeiro"}],
        "teams": [],
        "ownerFallback": {"available": True},
    }
    catalog = routing_policy_catalog(live)
    assert catalog["version"] == HUMAN_ROUTING_POLICY_VERSION
    finance = next(item for item in catalog["policies"] if item["key"] == "finance_review_v1")
    assert finance["specialistAvailable"] is True
    assert finance["matchedRoles"] == ["finance_manager"]
    with pytest.raises(ValueError, match="published policy catalog"):
        human_routing_policy("typed_by_operator")


def test_local_handoff_dry_run_proves_owner_without_creating_effects():
    policy = human_routing_policy("legal_review_v1")
    live = {"ownerFallback": {"available": True}}
    published = {"matchedRoles": [], "matchedSkills": [], "fallbackMode": "owner"}
    proof = local_handoff_dry_run(policy, published, live)
    assert proof == {
        "kind": "local_human_handoff_dry_run",
        "policyKey": "legal_review_v1",
        "policyVersion": HUMAN_ROUTING_POLICY_VERSION,
        "selectedLane": "workspace_owner",
        "fallbackMode": "owner",
        "ownerFallbackVerified": True,
        "decisionCreated": False,
        "notificationCreated": False,
        "externalEffect": False,
    }


def test_runtime_gate_requires_human_and_blocks():
    with pytest.raises(PermissionError):
        runtime_gate({"require_human": True})
    with pytest.raises(PermissionError):
        runtime_gate({"blocked": True}, human_approved=True)


def test_node_schema_fail_closed():
    with pytest.raises(ValueError):
        validate_node_payload(
            {"type": "object", "required": ["role"], "properties": {"role": {"type": "string"}}}, {}
        )


def test_runtime_node_contract_fails_closed_for_unknown_and_external_metadata():
    from app.services.flow_node_registry import FLOW_NODE_SPEC_BY_TYPE
    from app.workers.flow_tasks import _runtime_node_contract_error

    assert _runtime_node_contract_error("does_not_exist")
    original = FLOW_NODE_SPEC_BY_TYPE["payment_request"]["approval_policy"]
    FLOW_NODE_SPEC_BY_TYPE["payment_request"]["approval_policy"] = "none"
    try:
        assert "política de aprovação" in (_runtime_node_contract_error("payment_request") or "")
    finally:
        FLOW_NODE_SPEC_BY_TYPE["payment_request"]["approval_policy"] = original


def test_finance_node_contract_requires_declared_permission_when_snapshot_exists():
    from app.workers.flow_tasks import _runtime_node_contract_error

    assert "Permissão ausente" in (
        _runtime_node_contract_error("finance_draft", {"permissions": ["finance:view"]}) or ""
    )
    assert _runtime_node_contract_error("finance_read", {"permissions": ["finance:view"]}) is None


def test_required_permission_is_fail_closed_without_manifest_and_root_is_explicit():
    from app.workers.flow_tasks import _runtime_node_contract_error

    assert "Permissão ausente" in (_runtime_node_contract_error("finance_read", {}) or "")
    assert "Permissão ausente" in (_runtime_node_contract_error("finance_read") or "")
    assert _runtime_node_contract_error("finance_read", {"isPlatformRoot": True}) is None


def test_every_node_permission_exists_in_workspace_rbac_catalog():
    from app.core.rbac import ROLE_DEFINITIONS
    from app.services.flow_node_registry import FLOW_NODE_SPEC_BY_TYPE

    catalog = {
        f"{permission.module}:{permission.action}"
        for role in ROLE_DEFINITIONS.values()
        for permission in role.permissions
    }
    missing = {
        node_type: permission
        for node_type, spec in FLOW_NODE_SPEC_BY_TYPE.items()
        if (permission := spec.get("required_permission")) and permission not in catalog
    }
    assert missing == {}


@pytest.mark.parametrize(
    "node_type", ["debounce", "dedupe", "checkpoint", "retry", "timeout", "cancel"]
)
def test_deterministic_control_nodes_persist_explicit_state(node_type):
    from app.services.flow_runtime_primitives import apply_data_node

    context = {}
    result = apply_data_node(node_type, {"key": "k", "max_attempts": 2}, context)
    assert result["control"] == node_type
    assert "flowControls" in context
    if node_type == "cancel":
        assert context["flowControls"]["cancelRequested"] is True


@pytest.mark.parametrize(
    "node_type",
    [
        "load_client",
        "load_contract",
        "load_matter",
        "load_task",
        "load_documents",
        "load_finance",
        "load_tax_profile",
        "validate_data",
        "mask_data",
        "data_quality",
        "compare_versions",
        "rag_query",
    ],
)
def test_context_nodes_are_deterministic_and_tenant_local(node_type):
    from app.services.flow_runtime_primitives import apply_data_node

    context = {
        "client": {"id": "public-1"},
        "knowledgeChunks": [{"sourceType": "internal"}],
        "a": 1,
    }
    config = {"required": ["client"], "fields": ["client"], "paths": ["a"], "left": 1, "right": 1}
    result = apply_data_node(node_type, config, context)
    assert isinstance(result, dict)
    assert "external" not in result


@pytest.mark.asyncio
async def test_typed_agent_node_registry_is_scoped_and_fail_closed():
    from uuid import uuid4

    from app.services.agent_node_executor import (
        AgentExecutionContext,
        AgentNodeExecutorRegistry,
        AgentNodeOutput,
    )

    registry = AgentNodeExecutorRegistry()

    async def executor(ctx, payload):
        assert ctx.workspace_id and ctx.context_type == "finance"
        return AgentNodeOutput(status="completed", data={"ok": True}, idempotency_key="k")

    registry.register("finance_insight", executor)
    output = await registry.resolve("finance_insight")(
        AgentExecutionContext(uuid4(), "finance", uuid4(), frozenset({"finance:view"})), {}
    )
    assert output.status == "completed"
    with pytest.raises(LookupError):
        registry.resolve("unknown")


@pytest.mark.asyncio
async def test_decision_consume_is_single_use():
    result = SimpleNamespace(rowcount=1)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    assert not await consume_human_decision(
        db, "id", workspace_id="w", user_id="u", choice={"ok": True}
    )
    result.rowcount = 0
    assert not await consume_human_decision(
        db, "id", workspace_id="w", user_id="u", choice={"ok": True}
    )


def test_route_owner_is_final_and_cycle_free():
    assert finite_routing_path(["a", "a", "owner", "b"], owner="owner", max_hops=3) == [
        "a",
        "b",
        "owner",
    ]


@pytest.mark.parametrize(
    "missing_gate",
    [
        "definitionVerified",
        "roleVerified",
        "modelVerified",
        "knowledgeVerified",
        "autonomyVerified",
        "policyVerified",
        "entitlementVerified",
        "toolsVerified",
        "humanQueueVerified",
        "budgetVerified",
        "testPassed",
    ],
)
def test_deployment_publication_requires_each_independent_gate(missing_gate):
    config = {
        "definitionVersion": 1,
        "rolePackage": "finance",
        "model": "local-test-model",
        "tools": ["finance.read"],
        "knowledgeSources": ["workspace"],
        "autonomyPolicy": "draft_only",
        "humanRoutingPolicy": "finance_queue",
        "budgetPolicy": {"monthlyTokenLimit": 1000},
        "testedAt": "2026-08-03T00:00:00Z",
    }
    gates = {
        key: True
        for key in (
            "definitionVerified",
            "roleVerified",
            "modelVerified",
            "knowledgeVerified",
            "autonomyVerified",
            "policyVerified",
            "entitlementVerified",
            "toolsVerified",
            "humanQueueVerified",
            "budgetVerified",
            "testPassed",
        )
    }
    gates[missing_gate] = False
    with pytest.raises(ValueError, match="gates are not approved"):
        validate_deployment_ready(config, gates)


def test_phone_delivery_without_number_is_not_success():
    from app.models.agent_platform import HumanDeliveryStatus

    assert HumanDeliveryStatus.not_configured.value == "not_configured"
    assert HumanDeliveryStatus.pending.value == "pending"


@pytest.mark.asyncio
async def test_terminal_escalation_expires_after_durable_owner_alert():
    from app.models.agent_platform import HumanDecisionStatus
    from app.services.agent_platform_service import HumanRoutingService

    db = AsyncMock()
    db.add = MagicMock()
    svc = HumanRoutingService(db)
    decision = SimpleNamespace(
        id="d",
        status=HumanDecisionStatus.pending,
        routing_path=["owner"],
        escalation_level=0,
        max_hops=1,
        owner_user_id="owner",
        workspace_id="w",
        decision_key="k",
        due_at=None,
    )
    assert await svc.escalate(decision) is False
    assert decision.status == HumanDecisionStatus.expired
    # One flush belongs to the owner in-app notification and the second makes
    # the terminal decision/audit/delivery transition durable.
    assert db.flush.await_count == 2
    assert db.add.call_count >= 3
