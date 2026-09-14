from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client import Client
from app.models.flow import (
    Flow,
    FlowRevision,
    FlowRun,
    ScheduledMessage,
    build_flow_revision_snapshot,
    flow_revision_snapshot_hash,
)
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import Matter
from app.models.task import Task
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.schemas.flow import FLOW_CONDITION_OPERATORS
from app.services.flow_node_registry import CONDITION_OPERATORS
from app.services.playbook_service import PlaybookNotFoundError

pytestmark = pytest.mark.usefixtures("active_subscription")


async def _attach_initial_revision(
    db: AsyncSession,
    flow: Flow,
    *,
    source: str = "test_fixture",
) -> FlowRevision:
    await db.flush()
    revision_id = uuid4()
    snapshot = build_flow_revision_snapshot(
        flow,
        revision_id=revision_id,
        revision_number=1,
    )
    revision = FlowRevision(
        id=revision_id,
        flow_id=flow.id,
        revision_number=1,
        snapshot=snapshot,
        snapshot_hash=flow_revision_snapshot_hash(snapshot),
        source=source,
    )
    db.add(revision)
    await db.flush()
    flow.current_revision = 1
    flow.current_revision_id = revision.id
    await db.flush()
    return revision


async def _create_flow(
    db: AsyncSession,
    workspace_id,
    suffix: str,
    *,
    is_active: bool = True,
) -> Flow:
    flow = Flow(
        workspace_id=workspace_id,
        name=f"Fluxo {suffix}",
        description=f"Fluxo {suffix}",
        trigger="manual",
        trigger_config={},
        nodes=[
            {
                "id": "trigger-1",
                "type": "trigger",
                "position": {"x": 0, "y": 0},
                "label": "Novo lead",
                "config": {"event": "new_lead", "trigger_type": "new_lead"},
            }
        ],
        edges=[],
        is_active=is_active,
    )
    db.add(flow)
    await _attach_initial_revision(db, flow)
    return flow


async def _create_matter(db: AsyncSession, workspace_id, suffix: str = "flow") -> Matter:
    client = Client(
        workspace_id=workspace_id,
        name=f"Cliente {suffix}",
        cpf=uuid4().hex[:11],
        phone="+5511999999999",
    )
    db.add(client)
    await db.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client.id,
        demand_type=f"Demanda {suffix}",
    )
    db.add(matter)
    await db.flush()
    return matter


async def _unrelated_workspace_owner(
    db: AsyncSession, workspace_id: UUID
) -> WorkspaceMembership:
    user = User(
        name="Gestor sem vínculo na tarefa",
        email=f"task-flow-outsider-{uuid4().hex[:8]}@example.com",
        initials="SV",
        color="#475569",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    membership = WorkspaceMembership(
        workspace_id=workspace_id,
        user_id=user.id,
        role=WorkspaceRole.workspace_owner,
        status=MembershipStatus.active,
    )
    db.add(membership)
    await db.flush()
    return membership


@pytest.mark.asyncio
async def test_task_workflow_http_authorization_idempotency_and_tenant_scope(
    client,
    db_session: AsyncSession,
    auth_context,
):
    matter = await _create_matter(db_session, auth_context.workspace_id, "task-flow-http")
    flow = await _create_flow(db_session, auth_context.workspace_id, "tarefa autorizada")
    await db_session.commit()
    created_task = await client.post(
        f"/api/v1/matters/{matter.id}/tasks",
        json={
            "title": "Executar fluxo jurídico",
            "participants": [{"userId": str(auth_context.user_id), "role": "owner"}],
        },
    )
    assert created_task.status_code == 201, created_task.text
    task_id = created_task.json()["id"]
    path = f"/api/v1/tasks/{task_id}/workflow-runs"
    headers = {"Idempotency-Key": "task-flow-http-authorized-001"}

    first = await client.post(path, headers=headers, json={"flowId": str(flow.id)})
    replay = await client.post(path, headers=headers, json={"flowId": str(flow.id)})
    listed = await client.get(path)

    assert first.status_code == 201, first.text
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == first.json()["id"]
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()] == [first.json()["id"]]
    assert await db_session.scalar(
        select(func.count(FlowRun.id)).where(
            FlowRun.workspace_id == auth_context.workspace_id,
            FlowRun.task_id == UUID(task_id),
        )
    ) == 1

    original_user_id = auth_context.user_id
    original_membership_id = auth_context.membership_id
    outsider = await _unrelated_workspace_owner(db_session, auth_context.workspace_id)
    await db_session.commit()
    auth_context.user_id = outsider.user_id
    auth_context.membership_id = outsider.id
    try:
        denied_post = await client.post(
            path,
            headers={"Idempotency-Key": "task-flow-http-outsider-001"},
            json={"flowId": str(flow.id)},
        )
        denied_list = await client.get(path)
        assert denied_post.status_code == 404
        assert denied_list.status_code == 404
    finally:
        auth_context.user_id = original_user_id
        auth_context.membership_id = original_membership_id

    foreign_workspace = Workspace(
        name="Workspace externo da tarefa com workflow",
        slug=f"workspace-task-flow-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        settings={},
    )
    db_session.add(foreign_workspace)
    await db_session.flush()
    foreign_matter = await _create_matter(db_session, foreign_workspace.id, "task-flow-foreign")
    foreign_task = Task(
        workspace_id=foreign_workspace.id,
        matter_id=foreign_matter.id,
        title="Tarefa de outro workspace",
    )
    db_session.add(foreign_task)
    await db_session.commit()

    foreign_response = await client.post(
        f"/api/v1/tasks/{foreign_task.id}/workflow-runs",
        headers={"Idempotency-Key": "task-flow-http-foreign-001"},
        json={"flowId": str(flow.id)},
    )
    assert foreign_response.status_code == 404
    assert (
        await client.get(f"/api/v1/tasks/{foreign_task.id}/workflow-runs")
    ).status_code == 404


@pytest.mark.asyncio
async def test_case_360_flow_journey_reports_progress_timeline_and_kpis(
    client,
    db_session: AsyncSession,
    auth_context,
):
    matter = await _create_matter(db_session, auth_context.workspace_id, "journey")
    flow = Flow(
        workspace_id=auth_context.workspace_id,
        name="Triagem com revisão humana",
        description="Jornada exibida no Caso 360",
        trigger="manual",
        trigger_config={},
        nodes=[
            {"id": "start", "type": "trigger", "label": "Lead recebido", "config": {}},
            {
                "id": "review",
                "type": "human_pause",
                "label": "Revisar documentos",
                "config": {"reason": "Validação jurídica"},
            },
            {
                "id": "follow-up",
                "type": "send_message",
                "label": "Enviar retorno",
                "config": {"message": "Retorno"},
            },
        ],
        edges=[
            {"id": "start-review", "source": "start", "target": "review"},
            {"id": "review-follow-up", "source": "review", "target": "follow-up"},
        ],
        is_active=True,
    )
    db_session.add(flow)
    revision = await _attach_initial_revision(db_session, flow)
    occurred_at = datetime.now(UTC) - timedelta(minutes=4)
    run = FlowRun(
        flow_id=flow.id,
        revision_id=revision.id,
        matter_id=matter.id,
        workspace_id=auth_context.workspace_id,
        status="waiting_human",
        current_node_id="review",
        started_at=occurred_at,
        paused_at=datetime.now(UTC),
        pause_reason="Validação jurídica",
        context={
            "trace": [
                {
                    "step": 1,
                    "nodeId": "start",
                    "type": "trigger",
                    "label": "Lead recebido",
                    "status": "completed",
                    "occurredAt": occurred_at.isoformat(),
                    "decision": {
                        "field": "lead_score",
                        "operator": "gte",
                        "branch": "true",
                        "actual": 92,
                        "expected": 80,
                    },
                }
            ],
            "flowResumeNodeId": "follow-up",
        },
    )
    db_session.add(run)
    await db_session.commit()

    response = await client.get(f"/api/v1/flows/journeys/matters/{matter.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["kpis"] == {
        "total_runs": 1,
        "active_runs": 0,
        "waiting_human_runs": 1,
        "completed_runs": 0,
        "failed_runs": 0,
        "completion_rate": 0.0,
        "overall_progress_percent": 33.3,
        "average_duration_seconds": None,
        "completed_steps": 1,
        "total_steps": 3,
    }
    journey = payload["journeys"][0]
    assert journey["progress_percent"] == 33.3
    assert journey["current_step_label"] == "Revisar documentos"
    assert [step["state"] for step in journey["timeline"]] == [
        "completed",
        "waiting_human",
        "upcoming",
    ]
    assert journey["timeline"][0]["decision"] == {
        "field": "lead_score",
        "operator": "gte",
        "branch": "true",
    }


@pytest.mark.asyncio
async def test_case_360_flow_journey_does_not_cross_workspace(
    client,
    db_session: AsyncSession,
):
    foreign_workspace = Workspace(
        name="Workspace externo da jornada",
        slug=f"workspace-journey-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        settings={},
    )
    db_session.add(foreign_workspace)
    await db_session.flush()
    foreign_matter = await _create_matter(db_session, foreign_workspace.id, "foreign-journey")
    await db_session.commit()

    response = await client.get(f"/api/v1/flows/journeys/matters/{foreign_matter.id}")

    assert response.status_code == 404


async def _create_uncertain_external_run(
    db: AsyncSession,
    workspace_id,
    *,
    suffix: str = "uncertain",
) -> tuple[FlowRun, str]:
    matter = await _create_matter(db, workspace_id, suffix)
    flow = Flow(
        workspace_id=workspace_id,
        name=f"Flow incerto {suffix}",
        description="Reconciliação de efeito externo",
        trigger="manual",
        trigger_config={},
        nodes=[
            {
                "id": "send-1",
                "type": "send_message",
                "position": {"x": 0, "y": 0},
                "label": "Enviar mensagem",
                "config": {"message": "Olá"},
            }
        ],
        edges=[],
        is_active=True,
    )
    db.add(flow)
    await _attach_initial_revision(db, flow)
    intent_key = f"flow-effect:{uuid4()}"
    uncertain_at = datetime.now(UTC).isoformat()
    intent = {
        "intentKey": intent_key,
        "idempotencyKey": intent_key,
        "revisionId": str(flow.current_revision_id),
        "nodeId": "send-1",
        "nodeType": "send_message",
        "flowStep": 1,
        "sequence": 1,
        "state": "uncertain",
        "preparedAt": uncertain_at,
        "uncertainAt": uncertain_at,
        "attemptCount": 1,
        "providerSupportsIdempotency": False,
        "reconciliationRequired": True,
    }
    run = FlowRun(
        flow_id=flow.id,
        revision_id=flow.current_revision_id,
        matter_id=matter.id,
        workspace_id=workspace_id,
        status="waiting_human",
        current_node_id="send-1",
        paused_at=datetime.now(UTC),
        pause_reason="Efeito externo incerto",
        context={
            "phone": "+5511999999999",
            "flowStepCount": 1,
            "flowExternalIntents": {intent_key: intent},
            "flowActiveExternalIntent": intent_key,
            "flowExternalIntentSequence": 1,
            "uncertainExternalAction": {
                "intentKey": intent_key,
                "nodeId": "send-1",
                "nodeType": "send_message",
                "state": "uncertain",
                "reconciliationRequired": True,
                "detectedAt": uncertain_at,
            },
        },
    )
    db.add(run)
    await db.commit()
    return run, intent_key


def _graph_payload(*, operator: str = "gte") -> dict:
    return {
        "name": f"Flow grafo {uuid4().hex[:8]}",
        "trigger": "manual",
        "nodes": [
            {"id": "start", "type": "trigger", "config": {}},
            {
                "id": "check",
                "type": "condition",
                "config": {"field": "score", "operator": operator, "value": "7"},
            },
        ],
        "edges": [{"id": "start-check", "source": "start", "target": "check"}],
    }


@pytest.mark.asyncio
async def test_flow_routes_are_scoped_to_active_workspace(client, db_session: AsyncSession):
    other_workspace = Workspace(
        name="Workspace Externo Flow",
        slug=f"workspace-flow-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()

    foreign_flow = await _create_flow(db_session, other_workspace.id, suffix="externo")
    await db_session.commit()

    get_response = await client.get(f"/api/v1/flows/{foreign_flow.id}")
    update_response = await client.put(
        f"/api/v1/flows/{foreign_flow.id}",
        json={"name": "Fluxo Inválido"},
    )
    delete_response = await client.delete(f"/api/v1/flows/{foreign_flow.id}")

    assert get_response.status_code == 404
    assert update_response.status_code == 404
    assert delete_response.status_code == 404


@pytest.mark.asyncio
async def test_template_library_and_apply_do_not_cross_workspace(
    client,
    db_session: AsyncSession,
    auth_context,
):
    other_workspace = Workspace(
        name="Workspace Externo Template",
        slug=f"workspace-template-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()

    own_template = Flow(
        workspace_id=auth_context.workspace_id,
        name="Template privado próprio",
        trigger="manual",
        trigger_config={},
        nodes=[{"id": "start", "type": "trigger", "config": {}}],
        edges=[],
        is_active=True,
        is_template=True,
    )
    foreign_template = Flow(
        workspace_id=other_workspace.id,
        name="Template privado externo",
        trigger="manual",
        trigger_config={},
        nodes=[{"id": "start", "type": "trigger", "config": {}}],
        edges=[],
        is_active=True,
        is_template=True,
    )
    db_session.add_all([own_template, foreign_template])
    await db_session.commit()

    library = await client.get("/api/v1/flows/templates")
    own_apply = await client.post(f"/api/v1/flows/templates/{own_template.id}/apply")
    foreign_apply = await client.post(f"/api/v1/flows/templates/{foreign_template.id}/apply")

    assert library.status_code == 200, library.text
    assert str(own_template.id) not in {item["id"] for item in library.json()}
    assert str(foreign_template.id) not in {item["id"] for item in library.json()}
    assert own_apply.status_code == 201, own_apply.text
    assert own_apply.json()["workspace_id"] == str(auth_context.workspace_id)
    assert foreign_apply.status_code == 404


@pytest.mark.asyncio
async def test_flow_graph_rejects_duplicate_or_orphan_ids_and_unknown_contracts(client):
    invalid_payloads = []

    duplicate_node = _graph_payload()
    duplicate_node["nodes"][1]["id"] = "start"
    invalid_payloads.append((duplicate_node, "ID de nó duplicado"))

    duplicate_edge = _graph_payload()
    duplicate_edge["edges"].append({"id": "start-check", "source": "check", "target": "start"})
    invalid_payloads.append((duplicate_edge, "ID de edge duplicado"))

    orphan_edge = _graph_payload()
    orphan_edge["edges"][0]["target"] = "missing"
    invalid_payloads.append((orphan_edge, "target órfão"))

    unknown_type = _graph_payload()
    unknown_type["nodes"][1]["type"] = "shell_command"
    invalid_payloads.append((unknown_type, "Tipo de nó inválido"))

    non_string_type = _graph_payload()
    non_string_type["nodes"][1]["type"] = ["condition"]
    invalid_payloads.append((non_string_type, "Tipo de nó inválido"))

    unknown_operator = _graph_payload(operator="exec")
    invalid_payloads.append((unknown_operator, "Operador de condição inválido"))

    non_string_operator = _graph_payload()
    non_string_operator["nodes"][1]["config"]["operator"] = {"name": "gte"}
    invalid_payloads.append((non_string_operator, "Operador de condição inválido"))

    for payload, expected_error in invalid_payloads:
        response = await client.post("/api/v1/flows", json=payload)
        assert response.status_code == 422, response.text
        assert expected_error in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("operator", ["gte", "lte", "exists", "not_empty"])
async def test_flow_graph_accepts_supported_condition_operators(client, operator: str):
    response = await client.post("/api/v1/flows", json=_graph_payload(operator=operator))

    assert response.status_code == 201, response.text
    assert response.json()["nodes"][1]["config"]["operator"] == operator


def test_condition_operator_enum_matches_runtime_operators():
    # O enum do schema (registro v2) não pode divergir do conjunto validado
    # em app/schemas/flow.py e executado pelo runtime.
    assert frozenset(CONDITION_OPERATORS) == FLOW_CONDITION_OPERATORS


@pytest.mark.asyncio
async def test_flow_catalog_exposes_schema_driven_condition_contract(client):
    response = await client.get("/api/v1/flows/catalog")
    assert response.status_code == 200, response.text
    catalog = response.json()

    # (b) Marcador de versão da leitura do catálogo subiu para 2.
    assert catalog["schema_version"] == 2

    entries = {item["type"]: item for item in catalog["nodes"]}
    condition = entries["condition"]

    # (a) O nó condition passa a expor {schema, uischema, outputSchema}.
    assert {"schema", "uischema", "outputSchema"} <= set(condition)
    assert condition["config_version"] == 2

    schema = condition["schema"]
    assert schema["type"] == "object"
    props = schema["properties"]
    assert {"field", "operator", "value"} <= set(props)
    assert props["field"]["type"] == "string"
    # O enum do operador reflete os operadores realmente suportados.
    assert set(props["operator"]["enum"]) == set(FLOW_CONDITION_OPERATORS)
    assert schema["required"] == ["field", "operator"]

    # outputSchema descreve as portas true/false.
    assert set(condition["outputSchema"]["properties"]) == {"true", "false"}

    # O catálogo V3 é integralmente schema-driven: nenhum nó operacional pode
    # depender de configuração implícita ou de formulário frontend ad hoc.
    for node_type in ("trigger", "send_message"):
        assert {"schema", "uischema", "outputSchema"} <= set(entries[node_type])

    # The public response model must not silently strip the V3 governance
    # contract consumed by the editor.
    payment = entries["payment_request"]
    assert payment["risk"] == "high"
    assert payment["approval_policy"] == "required"
    assert payment["idempotency_policy"] == "required"
    assert payment["human_fallback"] == "human_review"


@pytest.mark.asyncio
async def test_flow_validate_and_simulate_compile_without_side_effects(client):
    graph = {
        "nodes": [
            {"id": "start", "type": "trigger", "config": {}},
            {
                "id": "approval",
                "type": "human_approval",
                "config": {"required_roles": ["owner"]},
            },
            {
                "id": "payment",
                "type": "payment_request",
                "config": {
                    "amount_expression": "invoice.total",
                    "payment_provider": "internal",
                    "payment_method": "pix",
                },
            },
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "approval"},
            {"id": "e2", "source": "approval", "target": "payment"},
        ],
    }

    validated = await client.post("/api/v1/flows/validate", json=graph)
    assert validated.status_code == 200, validated.text
    assert validated.json()["valid"] is True
    assert validated.json()["external_action_count"] == 1
    assert validated.json()["human_gate_count"] == 1

    simulated = await client.post("/api/v1/flows/simulate", json=graph)
    assert simulated.status_code == 200, simulated.text
    assert simulated.json() == validated.json()


@pytest.mark.asyncio
async def test_flow_graph_accepts_valid_and_legacy_condition(client):
    # Grafo condition válido (contrato v2 completo).
    valid = await client.post("/api/v1/flows", json=_graph_payload(operator="eq"))
    assert valid.status_code == 201, valid.text

    # Grafo legado: condition só com operador, sem field/value. O backend não
    # pode exigir os campos "required" do schema (compat de leitura).
    legacy = {
        "name": f"Flow legado {uuid4().hex[:8]}",
        "trigger": "manual",
        "nodes": [
            {"id": "start", "type": "trigger", "config": {}},
            {"id": "check", "type": "condition", "config": {"operator": "exists"}},
        ],
        "edges": [{"id": "start-check", "source": "start", "target": "check"}],
        "is_active": False,
    }
    legacy_response = await client.post("/api/v1/flows", json=legacy)
    assert legacy_response.status_code == 201, legacy_response.text
    assert legacy_response.json()["is_active"] is False


@pytest.mark.asyncio
async def test_direct_api_cannot_activate_node_with_invalid_catalog_config(client):
    payload = {
        "name": f"Flow inválido {uuid4().hex[:8]}",
        "trigger": "manual",
        "nodes": [
            {"id": "start", "type": "trigger", "config": {}},
            {"id": "finance", "type": "finance_read", "config": {"operation": "x"}},
        ],
        "edges": [{"id": "start-finance", "source": "start", "target": "finance"}],
        "is_active": True,
    }

    response = await client.post("/api/v1/flows", json=payload)

    assert response.status_code == 422, response.text
    assert "invalid_node_config" in response.text


@pytest.mark.asyncio
async def test_flow_graph_rejects_invalid_condition_schema_fail_closed(client):
    invalid_payloads = []

    non_scalar_field = _graph_payload()
    non_scalar_field["nodes"][1]["config"]["field"] = {"path": "score"}
    invalid_payloads.append((non_scalar_field, "Campo da condição"))

    non_scalar_value = _graph_payload()
    non_scalar_value["nodes"][1]["config"]["value"] = ["7"]
    invalid_payloads.append((non_scalar_value, "Valor da condição"))

    unknown_operator = _graph_payload(operator="regex")
    invalid_payloads.append((unknown_operator, "Operador de condição inválido"))

    for payload, expected_error in invalid_payloads:
        response = await client.post("/api/v1/flows", json=payload)
        assert response.status_code == 422, response.text
        assert expected_error in response.text


@pytest.mark.asyncio
async def test_flow_revision_round_trip_is_auditable_and_immutable(
    client,
    db_session: AsyncSession,
    auth_context,
):
    payload = _graph_payload()
    payload["name"] = "Versão inicial"
    created = await client.post("/api/v1/flows", json=payload)

    assert created.status_code == 201, created.text
    initial = created.json()
    assert initial["current_revision"] == 1
    assert initial["version"] == 1
    assert initial["current_revision_id"]

    updated = await client.put(
        f"/api/v1/flows/{initial['id']}",
        json={
            "expected_revision": initial["current_revision"],
            "name": "Versão revisada",
            "description": "Snapshot dois",
        },
    )

    assert updated.status_code == 200, updated.text
    assert updated.json()["current_revision"] == 2
    assert updated.json()["version"] == 2
    assert updated.json()["current_revision_id"] != initial["current_revision_id"]

    history = await client.get(f"/api/v1/flows/{initial['id']}/revisions")
    assert history.status_code == 200, history.text
    revisions = history.json()
    assert [item["revision_number"] for item in revisions] == [2, 1]
    assert revisions[0]["snapshot"]["name"] == "Versão revisada"
    assert revisions[1]["snapshot"]["name"] == "Versão inicial"
    assert revisions[1]["id"] == initial["current_revision_id"]
    assert revisions[0]["created_by_user_id"] == str(auth_context.user_id)

    revision_one = await client.get(f"/api/v1/flows/{initial['id']}/revisions/1")
    assert revision_one.status_code == 200, revision_one.text
    assert revision_one.json()["snapshot"]["nodes"] == payload["nodes"]

    persisted_revision = await db_session.scalar(
        select(FlowRevision).where(FlowRevision.id == initial["current_revision_id"])
    )
    assert persisted_revision is not None
    persisted_revision.source = "tampered"
    with pytest.raises(RuntimeError, match="imutável"):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_two_editors_get_conflict_instead_of_lost_update(client):
    created = await client.post("/api/v1/flows", json=_graph_payload())
    assert created.status_code == 201, created.text
    flow_id = created.json()["id"]

    editor_a = await client.get(f"/api/v1/flows/{flow_id}")
    editor_b = await client.get(f"/api/v1/flows/{flow_id}")
    assert editor_a.json()["current_revision"] == editor_b.json()["current_revision"] == 1

    first_write = await client.put(
        f"/api/v1/flows/{flow_id}",
        json={"expected_revision": 1, "name": "Edição A"},
    )
    stale_write = await client.put(
        f"/api/v1/flows/{flow_id}",
        json={"expected_revision": 1, "name": "Edição B"},
    )

    assert first_write.status_code == 200, first_write.text
    assert stale_write.status_code == 409, stale_write.text
    assert stale_write.json()["detail"] == {
        "code": "stale_flow_revision",
        "message": "Flow foi alterado por outro editor (esperada: 1, atual: 2)",
        "expected_revision": 1,
        "current_revision": 2,
    }
    current = await client.get(f"/api/v1/flows/{flow_id}")
    assert current.json()["name"] == "Edição A"
    assert current.json()["current_revision"] == 2


@pytest.mark.asyncio
async def test_legacy_update_is_only_inferred_for_initial_revision(client):
    created = await client.post("/api/v1/flows", json=_graph_payload())
    assert created.status_code == 201, created.text
    flow_id = created.json()["id"]

    baseline_update = await client.put(
        f"/api/v1/flows/{flow_id}",
        json={"name": "Compatibilidade segura"},
    )
    missing_precondition = await client.put(
        f"/api/v1/flows/{flow_id}",
        json={"name": "Sem token novamente"},
    )

    assert baseline_update.status_code == 200, baseline_update.text
    assert baseline_update.json()["current_revision"] == 2
    assert missing_precondition.status_code == 428, missing_precondition.text
    assert missing_precondition.json()["detail"]["code"] == "expected_revision_required"


@pytest.mark.asyncio
async def test_flow_run_remains_pinned_to_old_revision_after_update(
    client,
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    matter = await _create_matter(db_session, auth_context.workspace_id, "pinned")
    await db_session.commit()
    created = await client.post("/api/v1/flows", json=_graph_payload())
    assert created.status_code == 201, created.text
    initial = created.json()

    queued: list[list[str]] = []
    monkeypatch.setattr(
        "app.services.flow_service.automation_worker_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.workers.flow_tasks.execute_flow_run_step.apply_async",
        lambda *args, **kwargs: queued.append(kwargs.get("args")),
    )
    started = await client.post(
        f"/api/v1/flows/{initial['id']}/run",
        json={"matter_id": str(matter.id), "context": {"score": 8}},
    )
    assert started.status_code == 201, started.text
    assert started.json()["revision_id"] == initial["current_revision_id"]

    revised_nodes = [{"id": "new-start", "type": "trigger", "config": {}}]
    updated = await client.put(
        f"/api/v1/flows/{initial['id']}",
        json={
            "expected_revision": 1,
            "nodes": revised_nodes,
            "edges": [],
        },
    )
    assert updated.status_code == 200, updated.text

    run = await db_session.get(FlowRun, started.json()["id"])
    old_revision = await db_session.get(FlowRevision, run.revision_id)
    assert run.revision_id == UUID(initial["current_revision_id"])
    assert old_revision.snapshot["nodes"][0]["id"] == "start"
    assert updated.json()["nodes"] == revised_nodes
    assert updated.json()["current_revision"] == 2
    assert len(queued) == 1
    assert queued[0][:2] == [str(run.id), str(auth_context.workspace_id)]
    dispatch_token = queued[0][2]
    assert dispatch_token.startswith("flow-step-dispatch:")
    assert run.context["flowStepDispatches"][dispatch_token]["nodeId"] == "start"

    revision_count = await db_session.scalar(
        select(func.count())
        .select_from(FlowRevision)
        .where(FlowRevision.flow_id == UUID(initial["id"]))
    )
    assert revision_count == 2


@pytest.mark.asyncio
async def test_partial_graph_update_cannot_orphan_persisted_edge(client):
    created = await client.post("/api/v1/flows", json=_graph_payload())
    assert created.status_code == 201, created.text

    response = await client.put(
        f"/api/v1/flows/{created.json()['id']}",
        json={"nodes": [{"id": "start", "type": "trigger", "config": {}}]},
    )

    assert response.status_code == 422, response.text
    assert "target órfão" in response.text


@pytest.mark.asyncio
async def test_inactive_flow_cannot_start(
    client,
    db_session: AsyncSession,
    auth_context,
):
    matter = await _create_matter(db_session, auth_context.workspace_id, "inativo")
    flow = await _create_flow(
        db_session,
        auth_context.workspace_id,
        "inativo",
        is_active=False,
    )
    await db_session.commit()

    response = await client.post(
        f"/api/v1/flows/{flow.id}/run",
        json={"matter_id": str(matter.id), "context": {"phone": "+5511999999999"}},
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Flow inativo não pode ser executado"
    runs = list((await db_session.scalars(select(FlowRun))).all())
    assert runs == []
    await db_session.refresh(flow)
    assert flow.run_count == 0


@pytest.mark.asyncio
async def test_dry_run_traverses_condition_without_persisting_or_external_work(
    client,
    db_session: AsyncSession,
    auth_context,
):
    matter = await _create_matter(db_session, auth_context.workspace_id, "dry-condition")
    matter.lead_score = 82
    flow = Flow(
        workspace_id=auth_context.workspace_id,
        name="Dry-run condicional",
        trigger="manual",
        trigger_config={},
        nodes=[
            {"id": "start", "type": "trigger", "config": {}},
            {
                "id": "score",
                "type": "condition",
                "config": {"field": "lead_score", "operator": "gte", "value": "60"},
            },
            {"id": "human", "type": "human_pause", "config": {"reason": "Revisar"}},
            {"id": "message", "type": "send_message", "config": {"message": "Não enviar"}},
        ],
        edges=[
            {"id": "e1", "source": "start", "target": "score"},
            {"id": "e2", "source": "score", "target": "human", "condition_value": "true"},
            {"id": "e3", "source": "score", "target": "message", "condition_value": "false"},
        ],
        is_active=False,
    )
    db_session.add(flow)
    await db_session.commit()

    response = await client.post(
        "/api/v1/flows/dry-runs",
        json={"flow_id": str(flow.id), "matter_id": str(matter.id), "max_steps": 20},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "waiting_human"
    assert payload["revision"] == payload["version"] == 1
    assert payload["revision_id"] == str(flow.current_revision_id)
    assert [step["nodeId"] for step in payload["trace"]] == ["start", "score", "human"]
    assert payload["trace"][1]["decision"]["branch"] == "true"
    assert payload["external_actions_executed"] == 0
    assert list((await db_session.scalars(select(FlowRun))).all()) == []
    assert list((await db_session.scalars(select(ScheduledMessage))).all()) == []
    await db_session.refresh(flow)
    assert flow.run_count == 0


@pytest.mark.asyncio
async def test_flow_catalog_and_router_dry_run_are_authoritative(
    client,
    db_session: AsyncSession,
    auth_context,
):
    catalog_response = await client.get("/api/v1/flows/catalog")
    assert catalog_response.status_code == 200, catalog_response.text
    catalog = catalog_response.json()
    catalog_types = {item["type"] for item in catalog["nodes"]}
    assert catalog["node_count"] == len(catalog["nodes"])
    assert {
        "router",
        "loop",
        "error_handler",
        "link_action",
        "payment_request",
        "social_action",
        "connector_action",
    }.issubset(catalog_types)

    matter = await _create_matter(db_session, auth_context.workspace_id, "dry-router")
    matter.lead_score = 82
    flow = Flow(
        workspace_id=auth_context.workspace_id,
        name="Dry-run com roteador e link",
        trigger="manual",
        trigger_config={"localOnly": True},
        nodes=[
            {"id": "start", "type": "trigger", "config": {}},
            {
                "id": "router",
                "type": "router",
                "config": {
                    "routes": [
                        {
                            "key": "qualificado",
                            "field": "lead_score",
                            "operator": "gte",
                            "value": 70,
                        },
                        {"key": "fallback"},
                    ],
                    "fallback_route_key": "fallback",
                },
            },
            {
                "id": "qualified-link",
                "type": "link_action",
                "config": {
                    "link_kind": "booking",
                    "url": "/app/agenda",
                    "link_title": "Agendar reunião",
                    "target_variable": "flow.bookingLink",
                },
            },
            {
                "id": "fallback-note",
                "type": "internal_note",
                "config": {"note": "Revisar qualificação"},
            },
        ],
        edges=[
            {"id": "e1", "source": "start", "target": "router"},
            {
                "id": "e2",
                "source": "router",
                "target": "qualified-link",
                "route_key": "qualificado",
            },
            {
                "id": "e3",
                "source": "router",
                "target": "fallback-note",
                "route_key": "fallback",
                "is_fallback": True,
            },
        ],
        is_active=False,
    )
    db_session.add(flow)
    await db_session.commit()

    response = await client.post(
        "/api/v1/flows/dry-runs",
        json={"flow_id": str(flow.id), "matter_id": str(matter.id), "max_steps": 20},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "completed"
    assert [step["nodeId"] for step in payload["trace"]] == [
        "start",
        "router",
        "qualified-link",
    ]
    assert payload["trace"][1]["decision"]["routeKey"] == "qualificado"
    assert payload["context"]["flow"]["bookingLink"]["url"] == "/app/agenda"
    assert payload["external_actions_executed"] == 0


@pytest.mark.asyncio
async def test_dry_run_stops_a_cycle_at_explicit_step_limit(
    client,
    db_session: AsyncSession,
    auth_context,
):
    matter = await _create_matter(db_session, auth_context.workspace_id, "dry-cycle")
    flow = Flow(
        workspace_id=auth_context.workspace_id,
        name="Dry-run com ciclo",
        trigger="manual",
        trigger_config={"localOnly": True},
        nodes=[
            {"id": "start", "type": "trigger", "config": {}},
            {"id": "wait", "type": "wait", "config": {"delay_hours": 1}},
        ],
        edges=[
            {"id": "e1", "source": "start", "target": "wait"},
            {"id": "e2", "source": "wait", "target": "start"},
        ],
        is_active=False,
    )
    db_session.add(flow)
    await db_session.commit()

    response = await client.post(
        "/api/v1/flows/dry-runs",
        json={"flow_id": str(flow.id), "matter_id": str(matter.id), "max_steps": 5},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "max_steps_reached"
    assert len(payload["trace"]) == 5
    assert [step["nodeId"] for step in payload["trace"]] == [
        "start",
        "wait",
        "start",
        "wait",
        "start",
    ]
    assert list((await db_session.scalars(select(FlowRun))).all()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("human_node_type", ["human_pause", "human_transfer"])
async def test_local_run_follows_branch_pauses_and_resumes_on_pinned_revision(
    client,
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
    human_node_type: str,
):
    matter = await _create_matter(db_session, auth_context.workspace_id, "local-branch")
    matter.lead_score = 82
    flow = Flow(
        workspace_id=auth_context.workspace_id,
        name="Run local condicional",
        trigger="manual",
        trigger_config={"maxSteps": 10},
        nodes=[
            {"id": "start", "type": "trigger", "config": {}},
            {
                "id": "score",
                "type": "condition",
                "config": {"field": "lead_score", "operator": "gte", "value": "60"},
            },
            {
                "id": "review",
                "type": human_node_type,
                "config": {"reason": "Validar estratégia"},
            },
            {"id": "done", "type": "send_message", "config": {"message": "Aprovado"}},
            {
                "id": "false-branch",
                "type": "send_message",
                "config": {"message": "Não alcançada"},
            },
            {"id": "orphan", "type": "agent", "config": {"agent": "klm_sdr"}},
        ],
        edges=[
            {"id": "start-score", "source": "start", "target": "score"},
            {
                "id": "score-review",
                "source": "score",
                "target": "review",
                "condition_value": "true",
            },
            {
                "id": "score-false",
                "source": "score",
                "target": "false-branch",
                "condition_value": "false",
            },
            {"id": "review-done", "source": "review", "target": "done"},
        ],
        is_active=True,
    )
    db_session.add(flow)
    revision = await _attach_initial_revision(db_session, flow)
    await db_session.commit()

    queued: list[str] = []
    monkeypatch.setattr(
        "app.services.flow_service.automation_worker_allowed",
        lambda: False,
    )
    monkeypatch.setattr(
        "app.workers.flow_tasks.execute_flow_run_step.apply_async",
        lambda *args, **kwargs: queued.append(str(kwargs.get("args"))),
    )

    started = await client.post(
        f"/api/v1/flows/{flow.id}/run",
        json={"matter_id": str(matter.id)},
    )

    assert started.status_code == 201, started.text
    waiting = started.json()
    assert waiting["revision_id"] == str(revision.id)
    assert waiting["status"] == "waiting_human"
    assert waiting["current_node_id"] == "review"
    assert waiting["paused_at"] is not None
    assert waiting["pause_reason"] == "Validar estratégia"
    assert waiting["completed_at"] is None
    assert [entry["nodeId"] for entry in waiting["context"]["trace"]] == [
        "start",
        "score",
        "review",
    ]
    assert waiting["context"]["trace"][1]["decision"]["branch"] == "true"
    assert waiting["context"]["flowStepCount"] == 3
    assert waiting["context"]["externalActionsExecuted"] == 0
    checkpoint = waiting["context"]["humanCheckpoints"]["review"]
    assert checkpoint["decisionId"]
    assert checkpoint["decisionKey"].startswith(f"{waiting['id']}:review:")
    assert checkpoint["assignedUserId"] == str(auth_context.user_id)
    assert checkpoint["ownerUserId"] == str(auth_context.user_id)
    assert checkpoint["dueAt"] is not None
    assert queued == []

    already_waiting = await client.post(f"/api/v1/flows/runs/{waiting['id']}/pause")
    assert already_waiting.status_code == 409, already_waiting.text
    assert "waiting_human" in already_waiting.json()["detail"]

    revised = await client.put(
        f"/api/v1/flows/{flow.id}",
        json={
            "expected_revision": 1,
            "nodes": [{"id": "replacement", "type": "trigger", "config": {}}],
            "edges": [],
        },
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["current_revision"] == 2

    resumed = await client.post(f"/api/v1/flows/runs/{waiting['id']}/resume")

    assert resumed.status_code == 200, resumed.text
    completed = resumed.json()
    assert completed["revision_id"] == str(revision.id)
    assert completed["status"] == "completed"
    assert completed["current_node_id"] == "done"
    assert completed["paused_at"] is None
    assert completed["pause_reason"] is None
    assert completed["completed_at"] is not None
    assert [entry["nodeId"] for entry in completed["context"]["trace"]] == [
        "start",
        "score",
        "review",
        "done",
    ]
    assert completed["context"]["flowStepCount"] == 4
    assert queued == []

    resumed_again = await client.post(f"/api/v1/flows/runs/{waiting['id']}/resume")
    assert resumed_again.status_code == 409, resumed_again.text
    assert "completed" in resumed_again.json()["detail"]


@pytest.mark.asyncio
async def test_local_run_stops_cycle_at_snapshot_step_limit(
    client,
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    matter = await _create_matter(db_session, auth_context.workspace_id, "local-cycle")
    flow = Flow(
        workspace_id=auth_context.workspace_id,
        name="Run local com ciclo",
        trigger="manual",
        trigger_config={"localOnly": True, "maxSteps": 3},
        nodes=[
            {"id": "start", "type": "trigger", "config": {}},
            {"id": "wait", "type": "wait", "config": {"delay_hours": 1}},
        ],
        edges=[
            {"id": "start-wait", "source": "start", "target": "wait"},
            {"id": "wait-start", "source": "wait", "target": "start"},
        ],
        is_active=True,
    )
    db_session.add(flow)
    await _attach_initial_revision(db_session, flow)
    await db_session.commit()

    queued: list[str] = []
    monkeypatch.setattr(
        "app.services.flow_service.automation_worker_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.workers.flow_tasks.execute_flow_run_step.apply_async",
        lambda *args, **kwargs: queued.append(str(kwargs.get("args"))),
    )

    response = await client.post(
        f"/api/v1/flows/{flow.id}/run",
        json={"matter_id": str(matter.id)},
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["current_node_id"] == "wait"
    assert payload["completed_at"] is None
    assert [entry["nodeId"] for entry in payload["context"]["trace"]] == [
        "start",
        "wait",
        "start",
    ]
    assert payload["context"]["flowStepCount"] == 3
    assert payload["context"]["flowMaxSteps"] == 3
    assert payload["context"]["flowLoopGuardTriggered"] is True
    assert queued == []

    persisted = await db_session.get(FlowRun, UUID(payload["id"]))
    assert persisted.error_message == "Limite seguro de 3 etapas atingido"

    paused = await client.post(f"/api/v1/flows/runs/{payload['id']}/pause")
    resumed = await client.post(f"/api/v1/flows/runs/{payload['id']}/resume")
    assert paused.status_code == 409, paused.text
    assert resumed.status_code == 409, resumed.text
    assert "failed" in paused.json()["detail"]
    assert "failed" in resumed.json()["detail"]


@pytest.mark.asyncio
async def test_external_schedule_is_rejected_without_creating_pending_work(
    client,
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    matter = await _create_matter(db_session, auth_context.workspace_id, "gate")
    await db_session.commit()
    monkeypatch.setattr(
        "app.services.flow_service.external_delivery_allowed",
        lambda: False,
    )

    response = await client.post(
        "/api/v1/flows/scheduled-messages",
        json={
            "matter_id": str(matter.id),
            "contact_phone": "+5511999999999",
            "message": "Não enviar",
            "scheduled_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )

    assert response.status_code == 409, response.text
    assert "entregas externas estão desabilitadas" in response.json()["detail"]
    messages = list((await db_session.scalars(select(ScheduledMessage))).all())
    assert messages == []


@pytest.mark.asyncio
async def test_inactive_flow_run_cannot_create_external_schedule(
    client,
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    matter = await _create_matter(db_session, auth_context.workspace_id, "schedule-inativo")
    flow = await _create_flow(
        db_session,
        auth_context.workspace_id,
        "schedule-inativo",
        is_active=False,
    )
    run = FlowRun(
        flow_id=flow.id,
        revision_id=flow.current_revision_id,
        matter_id=matter.id,
        workspace_id=auth_context.workspace_id,
        status="active",
        current_node_id="trigger-1",
        context={},
    )
    db_session.add(run)
    await db_session.commit()
    monkeypatch.setattr(
        "app.services.flow_service.external_delivery_allowed",
        lambda: True,
    )

    response = await client.post(
        "/api/v1/flows/scheduled-messages",
        json={
            "matter_id": str(matter.id),
            "flow_run_id": str(run.id),
            "contact_phone": "+5511999999999",
            "message": "Não agendar",
            "scheduled_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Flow inativo não aceita agendamentos externos"
    messages = list((await db_session.scalars(select(ScheduledMessage))).all())
    assert messages == []


@pytest.mark.asyncio
async def test_uncertain_flow_effect_cannot_use_generic_resume_and_reconciles_as_delivered(
    client,
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    run, intent_key = await _create_uncertain_external_run(
        db_session,
        auth_context.workspace_id,
    )
    queued: list[dict] = []
    monkeypatch.setattr(
        "app.services.flow_service.automation_worker_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.workers.flow_tasks.execute_flow_run_step.apply_async",
        lambda *args, **kwargs: queued.append({"args": args, "kwargs": kwargs}),
    )

    generic_resume = await client.post(f"/api/v1/flows/runs/{run.id}/resume")
    reconciled = await client.post(
        f"/api/v1/flows/runs/{run.id}/external-action/reconcile",
        json={
            "outcome": "delivered",
            "provider_reference": "wamid.confirmed-by-human",
            "note": "Confirmado no painel do provedor",
        },
    )
    duplicate = await client.post(
        f"/api/v1/flows/runs/{run.id}/external-action/reconcile",
        json={"outcome": "delivered"},
    )

    assert generic_resume.status_code == 409, generic_resume.text
    assert "reconciliação explícita" in generic_resume.json()["detail"]
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["status"] == "active"
    assert reconciled.json()["current_node_id"] == "send-1"
    assert duplicate.status_code == 409, duplicate.text
    assert len(queued) == 1

    await db_session.refresh(run)
    intent = run.context["flowExternalIntents"][intent_key]
    assert intent["state"] == "confirmed"
    assert intent["providerReference"] == "wamid.confirmed-by-human"
    assert intent["reconciliationOutcome"] == "delivered"
    assert "uncertainExternalAction" not in run.context
    audit = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.workspace_id == auth_context.workspace_id,
            KlmAuditEvent.entity_id == run.id,
            KlmAuditEvent.event_type == "flow_external_action_reconciled",
        )
    )
    assert audit is not None
    assert audit.actor_user_id == auth_context.user_id


@pytest.mark.asyncio
async def test_uncertain_flow_retry_requires_external_delivery_gate(
    client,
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    run, intent_key = await _create_uncertain_external_run(
        db_session,
        auth_context.workspace_id,
        suffix="retry-gate",
    )
    monkeypatch.setattr(
        "app.services.flow_service.automation_worker_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.flow_service.external_delivery_allowed",
        lambda: False,
    )

    response = await client.post(
        f"/api/v1/flows/runs/{run.id}/external-action/reconcile",
        json={"outcome": "not_delivered"},
    )

    assert response.status_code == 409, response.text
    assert "entregas externas permanecem desabilitadas" in response.json()["detail"]
    await db_session.refresh(run)
    assert run.status == "waiting_human"
    assert run.context["flowExternalIntents"][intent_key]["state"] == "uncertain"


@pytest.mark.asyncio
async def test_scheduled_uncertain_delivery_reconciles_as_delivered_once(
    client,
    db_session: AsyncSession,
    auth_context,
):
    matter = await _create_matter(
        db_session,
        auth_context.workspace_id,
        "scheduled-delivered",
    )
    message = ScheduledMessage(
        matter_id=matter.id,
        workspace_id=auth_context.workspace_id,
        contact_phone="+5511999999999",
        message="Mensagem ambígua",
        scheduled_at=datetime.now(UTC) - timedelta(minutes=5),
        status="delivery_uncertain",
        delivery_attempted_at=datetime.now(UTC) - timedelta(minutes=4),
        delivery_attempt_count=1,
        delivery_uncertain_at=datetime.now(UTC),
        error_message="Reconciliação humana obrigatória",
    )
    db_session.add(message)
    await db_session.commit()

    response = await client.post(
        f"/api/v1/flows/scheduled-messages/{message.id}/reconcile",
        json={
            "outcome": "delivered",
            "provider_reference": "wamid.manual-confirmation",
        },
    )
    duplicate = await client.post(
        f"/api/v1/flows/scheduled-messages/{message.id}/reconcile",
        json={"outcome": "delivered"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "sent"
    assert response.json()["provider_message_id"] == "wamid.manual-confirmation"
    assert response.json()["delivery_uncertain_at"] is None
    assert duplicate.status_code == 409, duplicate.text
    await db_session.refresh(message)
    assert message.sent_at is not None
    assert message.error_message is None
    audit = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.workspace_id == auth_context.workspace_id,
            KlmAuditEvent.entity_id == message.id,
            KlmAuditEvent.event_type == "scheduled_message_reconciled",
        )
    )
    assert audit is not None


@pytest.mark.asyncio
async def test_scheduled_uncertain_delivery_can_authorize_one_explicit_retry(
    client,
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    matter = await _create_matter(db_session, auth_context.workspace_id, "scheduled-retry")
    message = ScheduledMessage(
        matter_id=matter.id,
        workspace_id=auth_context.workspace_id,
        contact_phone="+5511999999999",
        message="Tentar novamente",
        scheduled_at=datetime.now(UTC) - timedelta(minutes=5),
        status="delivery_uncertain",
        idempotency_key=f"scheduled-message:{uuid4()}",
        delivery_attempted_at=datetime.now(UTC) - timedelta(minutes=4),
        delivery_attempt_count=1,
        provider_supports_idempotency=False,
        delivery_uncertain_at=datetime.now(UTC),
    )
    db_session.add(message)
    await db_session.commit()
    queued: list[bool] = []
    monkeypatch.setattr(
        "app.services.flow_service.automation_worker_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.flow_service.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.workers.flow_tasks.process_scheduled_messages.delay",
        lambda: queued.append(True),
    )

    response = await client.post(
        f"/api/v1/flows/scheduled-messages/{message.id}/reconcile",
        json={
            "outcome": "not_delivered",
            "retry_at": datetime.now(UTC).isoformat(),
            "note": "Provedor confirmou que não recebeu",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending"
    assert response.json()["delivery_attempt_count"] == 1
    assert queued == [True]
    await db_session.refresh(message)
    assert message.delivery_attempted_at is None
    assert message.delivery_uncertain_at is None
    assert message.lease_token is None


@pytest.mark.asyncio
async def test_reconciliation_endpoints_do_not_cross_workspace(
    client,
    db_session: AsyncSession,
):
    other_workspace = Workspace(
        name="Workspace Externo Reconciliation",
        slug=f"workspace-reconciliation-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()
    foreign_run, _ = await _create_uncertain_external_run(
        db_session,
        other_workspace.id,
        suffix="foreign",
    )
    foreign_matter = await _create_matter(db_session, other_workspace.id, "foreign-message")
    foreign_message = ScheduledMessage(
        matter_id=foreign_matter.id,
        workspace_id=other_workspace.id,
        contact_phone="+5511888888888",
        message="Não pertence ao workspace",
        scheduled_at=datetime.now(UTC),
        status="delivery_uncertain",
        delivery_uncertain_at=datetime.now(UTC),
    )
    db_session.add(foreign_message)
    await db_session.commit()

    flow_response = await client.post(
        f"/api/v1/flows/runs/{foreign_run.id}/external-action/reconcile",
        json={"outcome": "delivered"},
    )
    message_response = await client.post(
        f"/api/v1/flows/scheduled-messages/{foreign_message.id}/reconcile",
        json={"outcome": "delivered"},
    )

    assert flow_response.status_code == 404, flow_response.text
    assert message_response.status_code == 404, message_response.text


@pytest.mark.asyncio
async def test_terminal_and_local_simulation_runs_reject_pause_and_resume(
    client,
    db_session: AsyncSession,
    auth_context,
):
    matter = await _create_matter(db_session, auth_context.workspace_id, "terminais")
    flow = await _create_flow(db_session, auth_context.workspace_id, "terminais")
    terminal_runs = [
        FlowRun(
            flow_id=flow.id,
            revision_id=flow.current_revision_id,
            matter_id=matter.id,
            workspace_id=auth_context.workspace_id,
            status=run_status,
            current_node_id="trigger-1",
            context={},
        )
        for run_status in ("completed", "failed", "cancelled")
    ]
    local_active = FlowRun(
        flow_id=flow.id,
        revision_id=flow.current_revision_id,
        matter_id=matter.id,
        workspace_id=auth_context.workspace_id,
        status="active",
        current_node_id="trigger-1",
        context={"klmRuntime": "local_simulation"},
    )
    local_paused = FlowRun(
        flow_id=flow.id,
        revision_id=flow.current_revision_id,
        matter_id=matter.id,
        workspace_id=auth_context.workspace_id,
        status="paused",
        current_node_id="trigger-1",
        context={"klmRuntime": "local_simulation"},
    )
    db_session.add_all([*terminal_runs, local_active, local_paused])
    await db_session.commit()

    for run in terminal_runs:
        paused = await client.post(f"/api/v1/flows/runs/{run.id}/pause")
        resumed = await client.post(f"/api/v1/flows/runs/{run.id}/resume")
        assert paused.status_code == 409, paused.text
        assert resumed.status_code == 409, resumed.text

    local_pause = await client.post(f"/api/v1/flows/runs/{local_active.id}/pause")
    local_resume = await client.post(f"/api/v1/flows/runs/{local_paused.id}/resume")
    assert local_pause.status_code == 409, local_pause.text
    assert local_resume.status_code == 409, local_resume.text

    for run in [*terminal_runs, local_active, local_paused]:
        await db_session.refresh(run)
    assert [run.status for run in terminal_runs] == ["completed", "failed", "cancelled"]
    assert local_active.status == "active"
    assert local_paused.status == "paused"


@pytest.mark.asyncio
async def test_valid_pause_resume_and_cancel_cascades_to_pending_messages(
    client,
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    matter = await _create_matter(db_session, auth_context.workspace_id, "lifecycle")
    flow = await _create_flow(db_session, auth_context.workspace_id, "lifecycle")
    run = FlowRun(
        flow_id=flow.id,
        revision_id=flow.current_revision_id,
        matter_id=matter.id,
        workspace_id=auth_context.workspace_id,
        status="active",
        current_node_id="trigger-1",
        context={},
    )
    db_session.add(run)
    await db_session.commit()

    queued: list[dict] = []
    monkeypatch.setattr(
        "app.services.flow_service.automation_worker_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.workers.flow_tasks.execute_flow_run_step.apply_async",
        lambda *args, **kwargs: queued.append({"args": args, "kwargs": kwargs}),
    )

    paused = await client.post(
        f"/api/v1/flows/runs/{run.id}/pause",
        params={"reason": "Revisão manual"},
    )
    resumed = await client.post(f"/api/v1/flows/runs/{run.id}/resume")

    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "paused"
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "active"
    assert len(queued) == 1

    message = ScheduledMessage(
        flow_run_id=run.id,
        matter_id=matter.id,
        workspace_id=auth_context.workspace_id,
        contact_phone="+5511999999999",
        message="Cancelar junto",
        scheduled_at=datetime.now(UTC) + timedelta(hours=1),
        status="pending",
    )
    claimed_message = ScheduledMessage(
        flow_run_id=run.id,
        matter_id=matter.id,
        workspace_id=auth_context.workspace_id,
        contact_phone="+5511888888888",
        message="Claim em andamento",
        scheduled_at=datetime.now(UTC) + timedelta(hours=1),
        status="processing",
    )
    attempted_message = ScheduledMessage(
        flow_run_id=run.id,
        matter_id=matter.id,
        workspace_id=auth_context.workspace_id,
        contact_phone="+5511666666666",
        message="Entrega já iniciada",
        scheduled_at=datetime.now(UTC),
        status="processing",
        delivery_attempted_at=datetime.now(UTC),
        delivery_attempt_count=1,
    )
    uncertain_message = ScheduledMessage(
        flow_run_id=run.id,
        matter_id=matter.id,
        workspace_id=auth_context.workspace_id,
        contact_phone="+5511777777777",
        message="Entrega incerta",
        scheduled_at=datetime.now(UTC),
        status="delivery_uncertain",
        delivery_uncertain_at=datetime.now(UTC),
    )
    db_session.add_all([message, claimed_message, attempted_message, uncertain_message])
    await db_session.commit()

    cancelled = await client.post(f"/api/v1/flows/runs/{run.id}/cancel")
    resumed_after_cancel = await client.post(f"/api/v1/flows/runs/{run.id}/resume")

    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert resumed_after_cancel.status_code == 409
    await db_session.refresh(message)
    await db_session.refresh(claimed_message)
    await db_session.refresh(attempted_message)
    await db_session.refresh(uncertain_message)
    assert message.status == "cancelled"
    assert claimed_message.status == "cancelled"
    assert attempted_message.status == "processing"
    assert uncertain_message.status == "delivery_uncertain"
    assert message.sent_at is None
    assert claimed_message.sent_at is None


@pytest.mark.asyncio
async def test_direct_message_cancel_requires_reconciliation_after_attempt(
    client,
    db_session: AsyncSession,
    auth_context,
):
    matter = await _create_matter(db_session, auth_context.workspace_id, "cancel-fence")
    cancellable = ScheduledMessage(
        matter_id=matter.id,
        workspace_id=auth_context.workspace_id,
        contact_phone="+5511999999999",
        message="Ainda não enviada",
        scheduled_at=datetime.now(UTC),
        status="processing",
        lease_token="lease-before-attempt",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    attempted = ScheduledMessage(
        matter_id=matter.id,
        workspace_id=auth_context.workspace_id,
        contact_phone="+5511888888888",
        message="Tentativa iniciada",
        scheduled_at=datetime.now(UTC),
        status="processing",
        delivery_attempted_at=datetime.now(UTC),
        delivery_attempt_count=1,
        lease_token="lease-after-attempt",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    uncertain = ScheduledMessage(
        matter_id=matter.id,
        workspace_id=auth_context.workspace_id,
        contact_phone="+5511777777777",
        message="Resultado incerto",
        scheduled_at=datetime.now(UTC),
        status="delivery_uncertain",
        delivery_attempted_at=datetime.now(UTC),
        delivery_attempt_count=1,
        delivery_uncertain_at=datetime.now(UTC),
    )
    db_session.add_all([cancellable, attempted, uncertain])
    await db_session.commit()

    cancelled = await client.delete(f"/api/v1/flows/scheduled-messages/{cancellable.id}")
    attempted_response = await client.delete(f"/api/v1/flows/scheduled-messages/{attempted.id}")
    uncertain_response = await client.delete(f"/api/v1/flows/scheduled-messages/{uncertain.id}")

    assert cancelled.status_code == 204, cancelled.text
    assert attempted_response.status_code == 409, attempted_response.text
    assert uncertain_response.status_code == 409, uncertain_response.text
    await db_session.refresh(cancellable)
    await db_session.refresh(attempted)
    await db_session.refresh(uncertain)
    assert cancellable.status == "cancelled"
    assert cancellable.lease_token is None
    assert attempted.status == "processing"
    assert uncertain.status == "delivery_uncertain"


@pytest.mark.asyncio
async def test_flow_with_uncertain_external_effect_cannot_be_cancelled(
    client,
    db_session: AsyncSession,
    auth_context,
):
    run, intent_key = await _create_uncertain_external_run(
        db_session,
        auth_context.workspace_id,
        suffix="cancel-reconcile-first",
    )

    response = await client.post(f"/api/v1/flows/runs/{run.id}/cancel")

    assert response.status_code == 409, response.text
    assert "reconciliação" in response.json()["detail"]
    await db_session.refresh(run)
    assert run.status == "waiting_human"
    assert run.context["flowExternalIntents"][intent_key]["state"] == "uncertain"


@pytest.mark.asyncio
async def test_resume_waiting_human_advances_before_enqueuing(
    client,
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    matter = await _create_matter(db_session, auth_context.workspace_id, "humano")
    flow = Flow(
        workspace_id=auth_context.workspace_id,
        name="Flow humano",
        trigger="manual",
        trigger_config={},
        nodes=[
            {"id": "review", "type": "human_pause", "config": {"reason": "Revisar"}},
            {"id": "message", "type": "send_message", "config": {"message": "OK"}},
        ],
        edges=[{"id": "review-message", "source": "review", "target": "message"}],
        is_active=True,
    )
    db_session.add(flow)
    await _attach_initial_revision(db_session, flow)
    run = FlowRun(
        flow_id=flow.id,
        revision_id=flow.current_revision_id,
        matter_id=matter.id,
        workspace_id=auth_context.workspace_id,
        status="waiting_human",
        current_node_id="review",
        context={},
    )
    db_session.add(run)
    await db_session.commit()

    queued: list[str] = []
    monkeypatch.setattr(
        "app.services.flow_service.automation_worker_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.workers.flow_tasks.execute_flow_run_step.apply_async",
        lambda *args, **kwargs: queued.append(str(kwargs.get("args"))),
    )

    response = await client.post(f"/api/v1/flows/runs/{run.id}/resume")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"
    assert response.json()["current_node_id"] == "message"
    assert len(queued) == 1


@pytest.mark.asyncio
async def test_resume_external_checkpoint_retries_same_node_only_after_channel_gate(
    client,
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    matter = await _create_matter(db_session, auth_context.workspace_id, "canal-gate")
    flow = Flow(
        workspace_id=auth_context.workspace_id,
        name="Flow com canal bloqueado",
        trigger="manual",
        trigger_config={},
        nodes=[
            {
                "id": "message",
                "type": "send_message",
                "config": {"message": "Enviar somente após aprovação"},
            },
            {"id": "done", "type": "wait", "config": {"delay_hours": 1}},
        ],
        edges=[{"id": "message-done", "source": "message", "target": "done"}],
        is_active=True,
    )
    db_session.add(flow)
    await _attach_initial_revision(db_session, flow)
    run = FlowRun(
        flow_id=flow.id,
        revision_id=flow.current_revision_id,
        matter_id=matter.id,
        workspace_id=auth_context.workspace_id,
        status="waiting_human",
        current_node_id="message",
        context={
            "blockedExternalAction": {
                "nodeId": "message",
                "nodeType": "send_message",
                "blockedAt": datetime.now(UTC).isoformat(),
            },
            "externalActionsExecuted": 0,
        },
    )
    db_session.add(run)
    await db_session.commit()

    queued: list[list[str]] = []
    monkeypatch.setattr(
        "app.services.flow_service.automation_worker_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.flow_service.external_delivery_allowed",
        lambda: False,
    )
    monkeypatch.setattr(
        "app.workers.flow_tasks.execute_flow_run_step.apply_async",
        lambda *args, **kwargs: queued.append(kwargs.get("args")),
    )

    blocked = await client.post(f"/api/v1/flows/runs/{run.id}/resume")
    assert blocked.status_code == 409, blocked.text
    await db_session.refresh(run)
    assert run.status == "waiting_human"
    assert run.current_node_id == "message"
    assert queued == []

    monkeypatch.setattr(
        "app.services.flow_service.external_delivery_allowed",
        lambda: True,
    )
    resumed = await client.post(f"/api/v1/flows/runs/{run.id}/resume")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "active"
    assert resumed.json()["current_node_id"] == "message"
    assert len(queued) == 1
    assert queued[0][:2] == [str(run.id), str(auth_context.workspace_id)]
    assert queued[0][2].startswith("flow-step-dispatch:")


@pytest.mark.asyncio
async def test_resume_rejects_corrupt_external_checkpoint_without_enqueuing(
    client,
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    matter = await _create_matter(db_session, auth_context.workspace_id, "checkpoint-corrupto")
    flow = await _create_flow(db_session, auth_context.workspace_id, "checkpoint-corrupto")
    run = FlowRun(
        flow_id=flow.id,
        revision_id=flow.current_revision_id,
        matter_id=matter.id,
        workspace_id=auth_context.workspace_id,
        status="waiting_human",
        current_node_id="trigger-1",
        context={
            "blockedExternalAction": {
                "nodeId": "outro-no",
                "nodeType": "send_message",
                "blockedAt": datetime.now(UTC).isoformat(),
            }
        },
    )
    db_session.add(run)
    await db_session.commit()

    queued: list[list[str]] = []
    monkeypatch.setattr(
        "app.services.flow_service.automation_worker_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.flow_service.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.workers.flow_tasks.execute_flow_run_step.apply_async",
        lambda *args, **kwargs: queued.append(kwargs.get("args")),
    )

    response = await client.post(f"/api/v1/flows/runs/{run.id}/resume")

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == (
        "Checkpoint externo não corresponde ao nó atual do flow run"
    )
    await db_session.refresh(run)
    assert run.status == "waiting_human"
    assert queued == []


@pytest.mark.asyncio
async def test_flow_copilot_proposes_valid_draft_without_persisting_or_executing(
    client,
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    flow = await _create_flow(db_session, auth_context.workspace_id, "copiloto")
    await db_session.commit()
    original_nodes = list(flow.nodes)

    async def no_effective_playbook(*args, **kwargs):
        raise PlaybookNotFoundError("sem playbook publicado")

    monkeypatch.setattr(
        "app.services.flow_copilot_service.PlaybookService.effective",
        no_effective_playbook,
    )

    response = await client.post(
        "/api/v1/flows/copilot/plan",
        json={
            "instruction": (
                "Quando entrar novo lead, faça triagem, peça documentos, aguarde, "
                "prepare o contrato."
            ),
            "flow_id": str(flow.id),
            "practice_area": "saude",
            "current_nodes": [],
            "current_edges": [],
            "attachments": [
                {
                    "name": "regras.txt",
                    "media_type": "text/plain",
                    "size_bytes": 48,
                    "text_excerpt": "Sempre revisar antes da assinatura.",
                }
            ],
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "awaiting_approval"
    assert payload["approval_required"] is True
    assert payload["external_actions_executed"] == 0
    assert payload["attachment_count"] == 1
    assert payload["added_node_ids"]
    assert payload["nodes"][0]["id"] == original_nodes[0]["id"]
    assert sum(node["type"] == "trigger" for node in payload["nodes"]) == 1
    assert {node["type"] for node in payload["nodes"]}.issuperset(
        {"trigger", "agent", "send_message", "wait", "condition", "zapsign_request", "human_pause"}
    )
    assert any("Trechos textuais interpretados" in item for item in payload["rationale"])
    assert all(edge["target"] != original_nodes[0]["id"] for edge in payload["edges"])

    await db_session.refresh(flow)
    assert flow.nodes == original_nodes
    assert list((await db_session.scalars(select(FlowRun))).all()) == []


@pytest.mark.asyncio
async def test_flow_copilot_rejects_audio_attachment(client):
    response = await client.post(
        "/api/v1/flows/copilot/plan",
        json={
            "instruction": "Crie um fluxo de triagem.",
            "attachments": [
                {
                    "name": "pedido.ogg",
                    "media_type": "audio/ogg",
                    "size_bytes": 100,
                }
            ],
        },
    )

    assert response.status_code == 422
    assert "não aceita arquivos de áudio" in response.text


@pytest.mark.asyncio
async def test_flow_copilot_requires_branch_selection_before_inserting(client):
    response = await client.post(
        "/api/v1/flows/copilot/plan",
        json={
            "instruction": "Crie uma tarefa para revisar o caso.",
            "current_nodes": [
                {
                    "id": "trigger-1",
                    "type": "trigger",
                    "label": "Entrada",
                    "config": {"event": "manual"},
                },
                {
                    "id": "message-a",
                    "type": "send_message",
                    "label": "Caminho A",
                    "config": {"message": "A"},
                },
                {
                    "id": "message-b",
                    "type": "send_message",
                    "label": "Caminho B",
                    "config": {"message": "B"},
                },
            ],
            "current_edges": [
                {
                    "id": "edge-a",
                    "source": "trigger-1",
                    "target": "message-a",
                },
                {
                    "id": "edge-b",
                    "source": "trigger-1",
                    "target": "message-b",
                },
            ],
        },
    )

    assert response.status_code == 422
    assert "múltiplas saídas" in response.text
