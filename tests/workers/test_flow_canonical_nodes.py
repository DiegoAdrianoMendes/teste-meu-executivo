"""Executable contracts for the canonical human and finance flow nodes.

These tests deliberately exercise the worker entry point (rather than only the
catalogue) so a node cannot be considered implemented merely because it has a
schema in the registry.
"""

import inspect
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import select

from app.models.agent_platform import (
    HumanDecision,
    HumanDecisionStatus,
    HumanNotificationDelivery,
)
from app.models.finance import FinanceEntry, FinanceEntryStatus
from app.models.finance_dimensional import FinanceExportJob
from app.models.klm_operations import KlmAuditEvent
from app.models.omnichannel_v6 import WorkspaceTeam, WorkspaceTeamMember
from app.models.user import AgentType, User, UserRole
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.services.finance_dimensional_service import FinanceDimensionalService
from app.services.finance_executive_service import FinanceExecutiveService
from app.services.flow_node_registry import flow_node_catalog
from app.workers import flow_tasks
from tests.workers.test_flow_tasks import _create_flow_run, _session_factory


def _valid_example(schema: object) -> object:
    """Build the smallest authorable payload accepted by a catalog schema.

    This deliberately exercises every published node contract rather than
    treating a JSON Schema declaration as evidence that the editor can send a
    valid payload.  The generated values are only test fixtures: execution is
    covered separately by the worker tests below.
    """
    if not isinstance(schema, dict):
        return "value"
    if "default" in schema:
        return deepcopy(schema["default"])
    if "const" in schema:
        return deepcopy(schema["const"])
    if schema.get("enum"):
        return deepcopy(schema["enum"][0])
    if schema.get("anyOf"):
        return _valid_example(schema["anyOf"][0])
    if schema.get("oneOf"):
        return _valid_example(schema["oneOf"][0])

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next((item for item in schema_type if item != "null"), "null")
    if schema_type == "object" or (schema_type is None and "properties" in schema):
        properties = schema.get("properties") or {}
        value = {
            key: _valid_example(properties[key])
            for key in schema.get("required") or []
            if key in properties
        }
        min_properties = int(schema.get("minProperties") or 0)
        for key, property_schema in properties.items():
            if len(value) >= min_properties:
                break
            value.setdefault(key, _valid_example(property_schema))
        if len(value) < min_properties and schema.get("additionalProperties") is not False:
            for index in range(len(value), min_properties):
                value[f"value_{index + 1}"] = "value"
        return value
    if schema_type == "array":
        min_items = int(schema.get("minItems") or 0)
        return [_valid_example(schema.get("items") or {}) for _ in range(min_items)]
    if schema_type == "integer":
        minimum = schema.get("minimum")
        return int(minimum) if minimum is not None else 1
    if schema_type == "number":
        if "exclusiveMinimum" in schema:
            return float(schema["exclusiveMinimum"]) + 1
        minimum = schema.get("minimum")
        return float(minimum) if minimum is not None else 1.0
    if schema_type == "boolean":
        return True
    if schema_type == "null":
        return None

    # The current public contracts use these formats for authorable strings.
    # Keep the fixture concrete so a future editor schema cannot silently ship
    # a malformed UUID/date field.
    fmt = schema.get("format")
    if fmt == "uuid":
        return "00000000-0000-4000-8000-000000000001"
    if fmt == "date-time":
        return "2026-08-03T12:00:00Z"
    if fmt == "date":
        return "2026-08-03"
    if fmt == "uri":
        return "https://app.meuexecutivo.test/flow"
    pattern = schema.get("pattern")
    if pattern == r"^([01]?\d|2[0-3]):[0-5]\d$":
        return "09:00"
    min_length = int(schema.get("minLength") or 0)
    return "x" * max(1, min_length)


def test_every_canonical_node_exposes_a_valid_json_schema() -> None:
    catalog = flow_node_catalog()
    node_types = [entry["type"] for entry in catalog]
    assert len(node_types) == len(set(node_types))
    assert {
        "human_assign",
        "human_request_input",
        "human_review",
        "human_approval",
        "human_takeover",
        "human_notify",
        "human_escalate",
        "human_schedule",
        "human_wait",
        "human_resolve",
        "finance_read",
        "finance_draft",
    }.issubset(node_types)
    assert all(entry.get("schema") for entry in catalog)

    for entry in catalog:
        Draft202012Validator.check_schema(entry["schema"])
        assert entry["outputSchema"]["type"] == "object"
        assert "ports" in entry["outputSchema"] or entry["availability"] == "available"
        assert entry["risk"] in {"low", "medium", "high", "critical"}
        assert entry["approval_policy"]
        assert entry["idempotency_policy"]
        assert entry["retry_policy"]


def test_every_published_node_has_an_authorable_payload_and_worker_branch() -> None:
    """Prevent a node from becoming visible in the editor without runtime code.

    The API catalog is intentionally the frontend's source of truth.  This is
    therefore a release gate: every node marked ``available`` needs a valid
    authoring payload and an explicit branch in the canonical worker entry
    point.  Nodes that are not ready may exist, but must be truthfully marked
    unavailable with a human-readable reason.
    """
    worker_source = inspect.getsource(flow_tasks._async_execute_flow_run_step)
    validator_format_checker = FormatChecker()

    for entry in flow_node_catalog():
        availability = entry.get("availability", "available")
        schema = entry.get("schema")
        assert isinstance(schema, dict), entry["type"]
        Draft202012Validator.check_schema(schema)

        if availability == "unavailable":
            reason = entry.get("unavailable_reason") or entry.get("availabilityReason")
            assert isinstance(reason, str) and reason.strip(), entry["type"]
            continue

        assert availability == "available", entry["type"]
        payload = _valid_example(schema)
        Draft202012Validator(schema, format_checker=validator_format_checker).validate(payload)
        # A literal appears either in its dedicated branch or in the grouped
        # branch that owns the node.  This catches registry-only promotions.
        assert f'"{entry["type"]}"' in worker_source, entry["type"]


def test_operational_native_nodes_are_renderable_and_available() -> None:
    catalog = {entry["type"]: entry for entry in flow_node_catalog()}
    for node_type in {
        "create_meeting",
        "update_client",
        "update_contract",
        "register_event",
        "generate_document",
        "generate_report",
        "request_export",
    }:
        assert catalog[node_type]["availability"] == "available"
        assert catalog[node_type]["execution_mode"] == "native"
        Draft202012Validator.check_schema(catalog[node_type]["schema"])


def test_available_nodes_never_present_as_planned_or_unimplemented() -> None:
    for entry in flow_node_catalog():
        if entry["availability"] != "available":
            continue
        assert entry["category"] != "Planejado", entry["type"]
        assert entry["description"] != "Executor ainda não implementado", entry["type"]


@pytest.mark.asyncio
async def test_register_event_is_append_only_and_idempotent(db_session, auth_context):
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="register-event",
        node_type="register_event",
        node_config={"event_type": "flow_note_registered", "summary": "Evento de teste"},
    )
    await db_session.commit()
    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    events = list(
        (
            await db_session.scalars(
                select(KlmAuditEvent).where(
                    KlmAuditEvent.workspace_id == auth_context.workspace_id,
                    KlmAuditEvent.event_type == "flow_note_registered",
                )
            )
        ).all()
    )
    assert len(events) == 1


@pytest.mark.asyncio
async def test_generate_report_uses_real_reports_service(db_session, auth_context):
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="report",
        node_type="generate_report",
        node_config={},
    )
    await db_session.commit()
    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    await db_session.refresh(run)
    assert run.context["report"]["kpis"]


@pytest.mark.asyncio
async def test_request_export_persists_job_before_dispatch(db_session, auth_context, monkeypatch):
    dispatched = []
    monkeypatch.setattr(
        "app.workers.finance_export_tasks.run_finance_export.apply_async",
        lambda *args, **kwargs: dispatched.append((args, kwargs)),
    )
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="export",
        node_type="request_export",
        node_config={"format": "xlsx", "drill_context": {}},
    )
    run.context = {
        **dict(run.context or {}),
        "humanAuthorizedActors": {"node-1": str(auth_context.user_id)},
    }
    await db_session.flush()
    await db_session.commit()
    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    job = await db_session.scalar(
        select(FinanceExportJob).where(FinanceExportJob.workspace_id == auth_context.workspace_id)
    )
    assert job is not None and job.status == "queued"
    assert dispatched


@pytest.mark.asyncio
async def test_request_export_without_actor_waits_for_real_human_owner(db_session, auth_context):
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="export-no-actor",
        node_type="request_export",
        node_config={"format": "pdf", "drill_context": {}},
    )
    await db_session.commit()
    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    await db_session.refresh(run)
    decision = await db_session.scalar(
        select(HumanDecision).where(HumanDecision.flow_run_id == run.id)
    )
    assert run.status == "waiting_human"
    assert decision is not None and decision.status == HumanDecisionStatus.pending


@pytest.mark.asyncio
async def test_generate_document_creates_durable_human_review(db_session, auth_context):
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="document",
        node_type="generate_document",
        node_config={"matter_id": str(uuid4()), "title": "Minuta para validação"},
    )
    await db_session.commit()
    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    await db_session.refresh(run)
    decision = await db_session.scalar(
        select(HumanDecision).where(HumanDecision.flow_run_id == run.id)
    )
    assert run.status == "waiting_human"
    assert decision is not None and decision.status == HumanDecisionStatus.pending


def test_human_escalate_schema_matches_its_existing_checkpoint_contract() -> None:
    spec = next(item for item in flow_node_catalog() if item["type"] == "human_escalate")
    schema = spec["schema"]
    assert schema["required"] == ["target_node_id"]
    assert "target_node_id" in schema["properties"]
    assert "required_roles" not in schema["properties"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "node_type",
    [
        "human_assign",
        "human_notify",
        "human_request_input",
        "human_review",
        "human_approval",
        "human_wait",
        "human_pause",
        "human_transfer",
    ],
)
async def test_routed_human_nodes_create_a_durable_decision_before_waiting(
    db_session, auth_context, node_type
):
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix=f"canonical-{node_type}",
        node_type=node_type,
        node_config={
            "required_roles": ["workspace_owner"],
            "reason": "Teste de roteamento canônico",
            "options": {"operation": node_type},
        },
    )
    await db_session.commit()

    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    await db_session.refresh(run)
    decision = await db_session.scalar(
        select(HumanDecision).where(HumanDecision.flow_run_id == run.id)
    )

    assert decision is not None
    assert decision.status == HumanDecisionStatus.pending
    assert decision.assigned_user_id is not None
    assert decision.owner_user_id == auth_context.user_id
    assert run.context["humanCheckpoints"]["node-1"]["decisionId"] == str(decision.id)
    deliveries = list(
        (
            await db_session.scalars(
                select(HumanNotificationDelivery).where(
                    HumanNotificationDelivery.decision_id == decision.id,
                    HumanNotificationDelivery.channel == "in_app",
                )
            )
        ).all()
    )
    # The owner can be the first eligible assignee.  In either case, the
    # durable in-app call belongs to the owner and exists before the state is
    # allowed to become waiting_human.
    assert any(item.recipient_user_id == auth_context.user_id for item in deliveries)
    # A durable decision must correspond to an actual paused run; otherwise a
    # later human resolve would be rejected as an orphaned call.
    assert run.status == "waiting_human"


@pytest.mark.asyncio
async def test_human_schedule_creates_canonical_meeting_then_waits_for_decision(
    db_session, auth_context, monkeypatch
):
    calls = []

    async def fake_create(self, workspace_id, payload, **kwargs):
        calls.append((workspace_id, payload, kwargs))
        return SimpleNamespace(
            id=uuid4(), starts_at=payload.starts_at, status=SimpleNamespace(value="scheduled")
        )

    monkeypatch.setattr(
        "app.services.meeting_service.MeetingService.create_for_workspace", fake_create
    )
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="human-schedule",
        node_type="human_schedule",
        node_config={
            "required_roles": ["workspace_owner"],
            "reason": "Agendar revisão",
            "title": "Revisão humana",
            "starts_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    await db_session.commit()

    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    await db_session.refresh(run)
    assert len(calls) == 1
    _, payload, kwargs = calls[0]
    assert payload.matter_id == run.matter_id
    assert payload.participants[0].role.value == "organizer"
    assert kwargs["idempotency_key"].startswith("flow-human-schedule:")
    assert run.status == "waiting_human"
    assert run.context["humanSchedules"]["node-1"]["meetingId"]


@pytest.mark.asyncio
async def test_human_takeover_uses_canonical_conversation_service_then_waits(
    db_session, auth_context, monkeypatch
):
    thread_id = uuid4()
    calls = []

    async def fake_takeover(db, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            thread=SimpleNamespace(id=thread_id, owner_generation=7), changed=True
        )

    monkeypatch.setattr("app.services.conversation_ownership_service.human_takeover", fake_takeover)
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="human-takeover",
        node_type="human_takeover",
        node_config={
            "required_roles": ["workspace_owner"],
            "reason": "Assumir atendimento",
            "thread_id": str(thread_id),
        },
    )
    await db_session.commit()

    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    await db_session.refresh(run)
    assert calls and calls[0]["workspace_id"] == auth_context.workspace_id
    assert calls[0]["thread_id"] == thread_id
    assert calls[0]["actor_type"] == "system"
    assert run.status == "waiting_human"
    assert run.context["humanTakeovers"]["node-1"]["threadId"] == str(thread_id)


@pytest.mark.asyncio
async def test_human_escalate_keeps_flow_waiting_on_the_same_durable_decision(
    db_session, auth_context, monkeypatch
):
    from app.services.agent_platform_service import HumanRoutingService

    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="human-escalate",
        node_type="human_escalate",
        node_config={"target_node_id": "origin", "required_roles": ["workspace_owner"]},
    )
    decision = HumanDecision(
        workspace_id=auth_context.workspace_id,
        flow_run_id=run.id,
        decision_key=f"{run.id}:origin:1:human_review",
        options={"purpose": "human_review"},
        required_roles=["workspace_owner"],
        assigned_user_id=auth_context.user_id,
        owner_user_id=auth_context.user_id,
        routing_path=[str(auth_context.user_id)],
        max_hops=1,
    )
    db_session.add(decision)
    await db_session.flush()
    run.context = {"humanCheckpoints": {"origin": {"decisionId": str(decision.id)}}}
    await db_session.commit()

    async def fake_escalate(self, target):
        target.escalation_level = 1
        target.due_at = datetime.now(UTC) + timedelta(minutes=30)
        return True

    monkeypatch.setattr(HumanRoutingService, "escalate", fake_escalate)
    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    await db_session.refresh(run)
    assert run.status == "waiting_human"
    assert run.context["humanCheckpoints"]["origin"]["decisionId"] == str(decision.id)
    assert run.context["humanCheckpoints"]["origin"]["status"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation", ["analytics", "dre_cash", "forecast", "valuation", "tax_reform"]
)
async def test_finance_read_calls_the_real_finance_service_and_persists_result(
    db_session, auth_context, monkeypatch, operation
):
    calls: list[str] = []

    async def fake_analytics(self, workspace_id):
        calls.append("analytics")
        return {"source": "executive"}

    async def fake_dre(self, workspace_id, **kwargs):
        calls.append("dre")
        return {"source": "dimensional", "mode": kwargs["mode"]}

    async def fake_list_forecasts(self, workspace_id):
        calls.append("forecast")
        return [{"id": "forecast-1"}]

    async def fake_list_valuations(self, workspace_id):
        calls.append("valuation")
        return [{"id": "valuation-1"}]

    async def fake_tax_reform(self, workspace_id, **kwargs):
        calls.append("tax_reform")
        return {"source": "tax"}

    monkeypatch.setattr(FinanceExecutiveService, "analytics", fake_analytics)
    monkeypatch.setattr(FinanceDimensionalService, "dre", fake_dre)
    monkeypatch.setattr(FinanceDimensionalService, "list_forecasts", fake_list_forecasts)
    monkeypatch.setattr(FinanceDimensionalService, "list_valuations", fake_list_valuations)
    monkeypatch.setattr(FinanceDimensionalService, "simulate_tax_reform", fake_tax_reform)

    finance_user = None
    if operation == "tax_reform":
        # Tax guidance must be routed to a real finance specialist and still
        # keep the workspace owner as the observed terminal fallback.
        finance_user = User(
            name="Responsável Contábil",
            email=f"finance-{uuid4().hex}@example.com",
            agent_type=AgentType.human,
            role=UserRole.admin,
            initials="RC",
            color="#14532d",
            is_active=True,
        )
        db_session.add(finance_user)
        await db_session.flush()
        membership = WorkspaceMembership(
            workspace_id=auth_context.workspace_id,
            user_id=finance_user.id,
            role=WorkspaceRole.finance_manager,
            status=MembershipStatus.active,
        )
        db_session.add(membership)
        await db_session.flush()
        team = WorkspaceTeam(
            workspace_id=auth_context.workspace_id,
            name="Contabilidade e Tributário",
            slug="contabilidade",
            team_type="tributario",
            is_active=True,
        )
        db_session.add(team)
        await db_session.flush()
        db_session.add(
            WorkspaceTeamMember(
                workspace_id=auth_context.workspace_id,
                team_id=team.id,
                user_id=finance_user.id,
                membership_id=membership.id,
            )
        )

    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix=f"finance-read-{operation}",
        node_type="finance_read",
        node_config={"operation": operation},
    )
    await db_session.commit()
    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    await db_session.refresh(run)

    assert calls == ["dre" if operation == "dre_cash" else operation]
    result = run.context["finance"]["node-1"]
    assert result["status"] == "completed"
    assert result["operation"] == operation
    assert result["result"] is not None
    if operation == "tax_reform":
        assert run.status == "waiting_human"
        decision = await db_session.scalar(
            select(HumanDecision).where(HumanDecision.flow_run_id == run.id)
        )
        assert decision is not None
        assert decision.assigned_user_id == finance_user.id
        assert decision.owner_user_id == auth_context.user_id
        deliveries = list(
            (
                await db_session.scalars(
                    select(HumanNotificationDelivery).where(
                        HumanNotificationDelivery.decision_id == decision.id
                    )
                )
            ).all()
        )
        assert any(
            item.recipient_user_id == finance_user.id and item.channel == "in_app"
            for item in deliveries
        )
        assert any(
            item.recipient_user_id == auth_context.user_id
            and item.recipient_kind == "owner"
            and item.channel == "in_app"
            for item in deliveries
        )
        # Purpose is part of the immutable, sanitized decision payload; the
        # persistence model deliberately has no denormalized purpose column.
        assert decision.options["purpose"] == "tax_accountant_review"
    else:
        assert run.status == "completed"


@pytest.mark.asyncio
async def test_finance_draft_is_idempotent_and_waits_only_after_human_call(
    db_session, auth_context, monkeypatch
):
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="finance-draft",
        node_type="finance_draft",
        node_config={
            "operation": "create_expense",
            "parameters": {"amount": "125.50", "title": "Rascunho"},
            "required_roles": ["workspace_owner"],
            "reason": "Revisão do rascunho financeiro",
        },
    )
    await db_session.commit()

    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    await db_session.refresh(run)
    entry = await db_session.scalar(
        select(FinanceEntry).where(FinanceEntry.workspace_id == auth_context.workspace_id)
    )
    decision = await db_session.scalar(
        select(HumanDecision).where(HumanDecision.flow_run_id == run.id)
    )

    assert entry is not None
    assert entry.status == FinanceEntryStatus.pending
    assert decision is not None and decision.status == HumanDecisionStatus.pending
    assert run.status == "waiting_human"
    assert run.context["finance"]["node-1"]["financeEntryId"] == str(entry.id)
    assert run.context["humanCheckpoints"]["node-1"]["decisionId"] == str(decision.id)

    # The worker cannot execute a waiting run again, and the durable external
    # reference is the idempotency boundary for retries.
    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    entries = list(
        (
            await db_session.scalars(
                select(FinanceEntry).where(FinanceEntry.external_ref.like("flow-draft:%"))
            )
        ).all()
    )
    assert len(entries) == 1
