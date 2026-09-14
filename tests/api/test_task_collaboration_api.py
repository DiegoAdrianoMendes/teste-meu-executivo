from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, can_view_sensitive_lead_data
from app.main import app as fastapi_app
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.task import Task, TaskDependency
from app.models.task_organization import TaskBoardPlacement
from app.models.user import AgentType, User, UserRole
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole

pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.mark.asyncio
async def test_legal_task_lists_boards_columns_and_archive_are_independent_from_status(
    client, db_session: AsyncSession, auth_context: AuthContext
) -> None:
    matter = await _matter(client)
    created = await client.post(
        f"/api/v1/matters/{matter['id']}/tasks",
        json={
            "title": "Organizar estratégia processual",
            "participants": [{"userId": str(auth_context.user_id), "role": "owner"}],
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["status"] == "pending"

    task_list = await client.post("/api/v1/tasks/lists", json={"name": "Contencioso"})
    assert task_list.status_code == 201, task_list.text
    board = await client.post("/api/v1/tasks/boards", json={"name": "Kanban jurídico"})
    assert board.status_code == 201, board.text
    first_column = await client.post(
        f"/api/v1/tasks/boards/{board.json()['id']}/columns",
        json={"name": "A preparar", "expectedBoardVersion": 1},
    )
    assert first_column.status_code == 201, first_column.text
    second_column = await client.post(
        f"/api/v1/tasks/boards/{board.json()['id']}/columns",
        json={"name": "Em revisão", "expectedBoardVersion": 2},
    )
    assert second_column.status_code == 201, second_column.text

    membership = await client.put(
        "/api/v1/tasks/list-memberships",
        json={
            "taskId": task["id"], "listId": task_list.json()["id"],
            "expectedTaskVersion": 1,
        },
    )
    assert membership.status_code == 200, membership.text
    first_placement = await client.put(
        "/api/v1/tasks/board-placements",
        json={
            "taskId": task["id"], "boardId": board.json()["id"],
            "columnId": first_column.json()["id"], "expectedTaskVersion": 2,
        },
    )
    assert first_placement.status_code == 200, first_placement.text
    assert (await client.get(f"/api/v1/tasks/{task['id']}")).json()["status"] == "pending"

    moved = await client.put(
        "/api/v1/tasks/board-placements",
        json={
            "taskId": task["id"], "boardId": board.json()["id"],
            "columnId": second_column.json()["id"], "expectedTaskVersion": 3,
            "expectedPlacementVersion": first_placement.json()["version"],
        },
    )
    assert moved.status_code == 200, moved.text
    detail_after_move = await client.get(f"/api/v1/tasks/{task['id']}")
    assert detail_after_move.json()["status"] == "pending"
    assert detail_after_move.json()["version"] == 4
    placement = await db_session.scalar(
        select(TaskBoardPlacement).where(TaskBoardPlacement.task_id == task["id"])
    )
    assert placement is not None and str(placement.column_id) == second_column.json()["id"]

    organization = await client.get("/api/v1/tasks/organization")
    assert organization.status_code == 200, organization.text
    assert organization.json()["boards"][0]["columns"][1]["name"] == "Em revisão"
    assert organization.json()["listMemberships"][0]["taskId"] == task["id"]

    archived = await client.post(
        f"/api/v1/tasks/{task['id']}/archive",
        json={"expectedVersion": 4, "reason": "Trabalho jurídico temporariamente encerrado."},
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["archivedAt"] is not None
    active_page = await client.get("/api/v1/tasks", params={"scope": "mine"})
    assert task["id"] not in {item["id"] for item in active_page.json()["items"]}

    restored = await client.post(
        f"/api/v1/tasks/{task['id']}/unarchive",
        json={"expectedVersion": 5, "reason": "Demanda retomada pelo responsável jurídico."},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["archivedAt"] is None
    assert restored.json()["status"] == "pending"


async def _matter(client) -> dict:
    lead = await client.post(
        "/api/v1/clients",
        json={"name": "Cliente API Task", "email": f"task-{uuid4().hex[:8]}@example.com"},
    )
    assert lead.status_code == 201, lead.text
    matter = await client.post(
        "/api/v1/matters",
        json={
            "clientId": lead.json()["id"],
            "practiceArea": "civel",
            "demandType": "Demanda colaborativa",
            "columnId": "novo_lead",
            "microStatus": "lead_novo",
        },
    )
    assert matter.status_code == 201, matter.text
    payload = matter.json()
    # O read model resumido do caso não expõe clientId; o teste conserva a
    # identidade criada pela própria fixture para exercitar o filtro composto.
    payload["clientId"] = lead.json()["id"]
    return payload


async def _member(db: AsyncSession, workspace_id, label: str) -> User:
    user = User(
        name=label,
        email=f"{label.lower()}-{uuid4().hex[:8]}@example.com",
        initials=label[:2].upper(),
        color="#475569",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    db.add(
        WorkspaceMembership(
            workspace_id=workspace_id,
            user_id=user.id,
            role=WorkspaceRole.operator,
            status=MembershipStatus.active,
        )
    )
    await db.flush()
    return user


@pytest.mark.asyncio
async def test_flexible_task_contexts_are_explicit_and_fail_closed(
    client, auth_context: AuthContext
) -> None:
    workspace_task = await client.post(
        "/api/v1/tasks",
        json={
            "title": "Atualizar procedimento interno",
            "context": {"type": "workspace"},
            "origin": "administrative",
            "participants": [
                {"userId": str(auth_context.user_id), "role": "owner"}
            ],
        },
    )
    assert workspace_task.status_code == 201, workspace_task.text
    body = workspace_task.json()
    assert body["workspaceId"] == str(auth_context.workspace_id)
    assert body["contextType"] == "workspace"
    assert body["matterId"] is None
    assert body["clientId"] is None

    checklist = await client.post(
        f"/api/v1/tasks/{body['id']}/checklists",
        json={"expectedTaskVersion": 1, "title": "Procedimento"},
    )
    assert checklist.status_code == 201, checklist.text
    subtask = await client.post(
        f"/api/v1/tasks/{body['id']}/subtasks",
        json={
            "expectedParentVersion": 2,
            "title": "Documentar procedimento",
            "participants": [
                {"userId": str(auth_context.user_id), "role": "owner"}
            ],
        },
    )
    assert subtask.status_code == 201, subtask.text
    assert subtask.json()["contextType"] == "workspace"
    assert subtask.json()["matterId"] is None
    assert subtask.json()["parentTaskId"] == body["id"]

    client_record = await client.post(
        "/api/v1/clients",
        json={"name": "Cliente contexto", "email": f"context-{uuid4().hex}@example.com"},
    )
    assert client_record.status_code == 201, client_record.text
    client_task = await client.post(
        "/api/v1/tasks",
        json={
            "title": "Validar dados comerciais",
            "context": {"type": "client", "clientId": client_record.json()["id"]},
            "origin": "commercial",
            "participants": [
                {"userId": str(auth_context.user_id), "role": "owner"}
            ],
        },
    )
    assert client_task.status_code == 201, client_task.text
    assert client_task.json()["contextType"] == "client"
    assert client_task.json()["clientId"] == client_record.json()["id"]
    assert client_task.json()["matterId"] is None

    legal_without_matter = await client.post(
        "/api/v1/tasks",
        json={
            "title": "Tratar publicação",
            "context": {"type": "workspace"},
            "origin": "publication",
            "originResourceId": str(uuid4()),
        },
    )
    assert legal_without_matter.status_code == 422

    created_contract = await client.post(
        "/api/v1/legal/client-contracts",
        json={
            "clientId": client_record.json()["id"],
            "title": "Assessoria jurídica mensal",
            "status": "active",
        },
    )
    assert created_contract.status_code == 201, created_contract.text
    contract_list = await client.get(
        "/api/v1/legal/client-contracts",
        params={"clientId": client_record.json()["id"]},
    )
    assert contract_list.status_code == 200, contract_list.text
    assert [item["id"] for item in contract_list.json()] == [created_contract.json()["id"]]

    contract_task = await client.post(
        "/api/v1/tasks",
        json={
            "title": "Revisar contrato",
            "context": {
                "type": "contract",
                "clientId": client_record.json()["id"],
                "contractId": created_contract.json()["id"],
            },
            "participants": [
                {"userId": str(auth_context.user_id), "role": "owner"}
            ],
        },
    )
    assert contract_task.status_code == 201, contract_task.text
    assert contract_task.json()["contextType"] == "contract"
    assert contract_task.json()["contractId"] == created_contract.json()["id"]
    assert contract_task.json()["clientId"] == client_record.json()["id"]
    assert contract_task.json()["matterId"] is None


@pytest.mark.asyncio
async def test_collaborative_api_commands_comments_history_and_conflicts(
    client, db_session: AsyncSession, auth_context: AuthContext
) -> None:
    matter = await _matter(client)
    reviewer = await _member(db_session, auth_context.workspace_id, "Reviewer API")
    created = await client.post(
        f"/api/v1/matters/{matter['id']}/tasks",
        headers={"X-Correlation-ID": "api-task-create"},
        json={
            "title": "Elaborar manifestação",
            "reviewRequired": True,
            "participants": [
                {"userId": str(auth_context.user_id), "role": "owner"},
                {"userId": str(reviewer.id), "role": "reviewer"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["version"] == 1
    assert body["contextType"] == "matter"
    assert body["origin"] == "legacy"
    assert body["matterId"] == matter["id"]
    assert {item["role"] for item in body["participants"] if item["active"]} == {
        "owner",
        "reviewer",
    }

    workspace_page = await client.get("/api/v1/tasks", params={"scope": "mine"})
    assert workspace_page.status_code == 200, workspace_page.text
    assert workspace_page.json()["hasMore"] is False
    assert [item["id"] for item in workspace_page.json()["items"]] == [body["id"]]
    assert workspace_page.json()["items"][0]["clientLabel"] == "Cliente API Task"

    member_options = await client.get("/api/v1/tasks/member-options", params={"search": "Reviewer"})
    assert member_options.status_code == 200, member_options.text
    assert len(member_options.json()) == 1
    assert {
        key: member_options.json()[0][key]
        for key in ("id", "label", "role")
    } == {"id": str(reviewer.id), "label": "Reviewer API", "role": "operator"}
    assert member_options.json()[0]["handle"]
    assert str(reviewer.id) not in member_options.json()[0]["handle"]

    matter_options = await client.get(
        "/api/v1/tasks/matter-options", params={"search": "colaborativa"}
    )
    assert matter_options.status_code == 200, matter_options.text
    assert len(matter_options.json()) == 1
    assert {
        key: matter_options.json()[0][key]
        for key in ("id", "label", "clientLabel")
    } == {
        "id": matter["id"],
        "label": "Demanda colaborativa",
        "clientLabel": "Cliente API Task",
    }
    assert matter_options.json()[0]["handle"]
    assert matter["id"] not in matter_options.json()[0]["handle"]

    missing_version = await client.patch(
        f"/api/v1/tasks/{body['id']}", json={"title": "Sem versão"}
    )
    assert missing_version.status_code == 422

    started = await client.post(
        f"/api/v1/tasks/{body['id']}/commands/start",
        headers={"X-Correlation-ID": "api-task-start"},
        json={"expectedVersion": 1},
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "in_progress"
    assert started.json()["version"] == 2

    stale = await client.post(
        f"/api/v1/tasks/{body['id']}/commands/block",
        json={"expectedVersion": 1, "reason": "Dependência externa pendente."},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "TASK_VERSION_CONFLICT"

    commented = await client.post(
        f"/api/v1/tasks/{body['id']}/comments",
        headers={"X-Correlation-ID": "api-task-comment"},
        json={"expectedVersion": 2, "comment": "Documento principal anexado."},
    )
    assert commented.status_code == 200, commented.text
    assert commented.json()["version"] == 3

    history = await client.get(f"/api/v1/tasks/{body['id']}/events")
    assert history.status_code == 200, history.text
    assert history.json()["total"] == 3
    assert {item["correlationId"] for item in history.json()["items"]} == {
        "api-task-create",
        "api-task-start",
        "api-task-comment",
    }


@pytest.mark.asyncio
async def test_workspace_wide_task_collection_requires_administrative_session(client) -> None:
    response = await client.get("/api/v1/tasks", params={"scope": "workspace"})

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "TASK-WORKSPACE-SCOPE-SUPPORT-ONLY"


@pytest.mark.asyncio
async def test_legal_task_checklist_is_versioned_audited_and_archived_explicitly(
    client,
    auth_context: AuthContext,
) -> None:
    matter = await _matter(client)
    created = await client.post(
        f"/api/v1/matters/{matter['id']}/tasks",
        json={
            "title": "Preparar protocolo com checklist",
            "participants": [{"userId": str(auth_context.user_id), "role": "owner"}],
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()

    checklist = await client.post(
        f"/api/v1/tasks/{task['id']}/checklists",
        headers={"Idempotency-Key": f"checklist-{uuid4()}"},
        json={"expectedTaskVersion": 1, "title": "Conferência para protocolo"},
    )
    assert checklist.status_code == 201, checklist.text
    checklist_body = checklist.json()
    assert checklist_body["title"] == "Conferência para protocolo"
    assert checklist_body["items"] == []

    added = await client.post(
        f"/api/v1/tasks/{task['id']}/checklists/{checklist_body['id']}/items",
        headers={"Idempotency-Key": f"checklist-item-{uuid4()}"},
        json={"expectedTaskVersion": 2, "label": "Validar procuração"},
    )
    assert added.status_code == 201, added.text
    item = added.json()["items"][0]
    assert item["completedAt"] is None

    stale = await client.put(
        f"/api/v1/tasks/{task['id']}/checklists/{checklist_body['id']}/items/{item['id']}",
        json={"expectedTaskVersion": 2, "expectedItemVersion": 1, "completed": True},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "TASK_VERSION_CONFLICT"

    completed = await client.put(
        f"/api/v1/tasks/{task['id']}/checklists/{checklist_body['id']}/items/{item['id']}",
        json={"expectedTaskVersion": 3, "expectedItemVersion": 1, "completed": True},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["items"][0]["completedAt"] is not None

    archived = await client.request(
        "DELETE",
        f"/api/v1/tasks/{task['id']}/checklists/{checklist_body['id']}",
        json={
            "expectedTaskVersion": 4,
            "expectedChecklistVersion": 3,
            "reason": "Checklist substituído por uma versão revisada.",
        },
    )
    assert archived.status_code == 204, archived.text
    listed = await client.get(f"/api/v1/tasks/{task['id']}/checklists")
    assert listed.status_code == 200, listed.text
    assert listed.json() == []
    history = await client.get(f"/api/v1/tasks/{task['id']}/events")
    assert history.status_code == 200, history.text
    checklist_events = [
        item for item in history.json()["items"] if item["eventType"] == "checklist_changed"
    ]
    assert len(checklist_events) == 4
    assert any(
        item["reason"] == "Checklist substituído por uma versão revisada."
        for item in checklist_events
    )


@pytest.mark.asyncio
async def test_legal_task_resources_labels_subtasks_dependencies_attachments_and_mentions(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    matter = await _matter(client)
    mentioned = await _member(db_session, auth_context.workspace_id, "Pessoa Mencionada")
    document = Document(
        matter_id=matter["id"],
        created_by=auth_context.user_id,
        name="procuração assinada.pdf",
        doc_type=DocumentType.procuracao,
        status=DocumentStatus.completo,
        mime_type="application/pdf",
    )
    db_session.add(document)
    await db_session.flush()

    parent_response = await client.post(
        f"/api/v1/matters/{matter['id']}/tasks",
        json={
            "title": "Preparar protocolo",
            "participants": [{"userId": str(auth_context.user_id), "role": "owner"}],
        },
    )
    assert parent_response.status_code == 201, parent_response.text
    parent = parent_response.json()

    labels = await client.put(
        f"/api/v1/tasks/{parent['id']}/labels",
        json={
            "expectedVersion": 1,
            "reason": "Classificação jurídica inicial.",
            "labels": [
                {"name": "Urgente", "color": "#DC2626"},
                {"name": "Protocolo", "color": "#2563EB"},
            ],
        },
    )
    assert labels.status_code == 200, labels.text
    assert {item["name"] for item in labels.json()} == {"Urgente", "Protocolo"}
    relabeled = await client.put(
        f"/api/v1/tasks/{parent['id']}/labels",
        json={
            "expectedVersion": 2,
            "reason": "Mantém somente a etiqueta operacional.",
            "labels": [{"name": "Protocolo", "color": "#2563EB"}],
        },
    )
    assert relabeled.status_code == 200, relabeled.text
    assert [item["name"] for item in relabeled.json()] == ["Protocolo"]

    catalog = await client.get("/api/v1/tasks/label-catalog")
    assert catalog.status_code == 200, catalog.text
    catalog_items = catalog.json()
    protocolo = next(item for item in catalog_items if item["name"] == "Protocolo")
    urgente = next(item for item in catalog_items if item["name"] == "Urgente")
    assert protocolo["color"] == "#2563EB"
    assert protocolo["handle"] != protocolo["id"]
    assert urgente["color"] == "#DC2626"

    filtered = await client.get(
        "/api/v1/tasks",
        params={"scope": "mine", "labelRef": protocolo["handle"]},
    )
    assert filtered.status_code == 200, filtered.text
    assert [item["id"] for item in filtered.json()["items"]] == [parent["id"]]

    child_response = await client.post(
        f"/api/v1/tasks/{parent['id']}/subtasks",
        json={
            "expectedParentVersion": 3,
            "title": "Conferir assinatura",
            "participants": [{"userId": str(auth_context.user_id), "role": "owner"}],
        },
    )
    assert child_response.status_code == 201, child_response.text
    child = child_response.json()
    assert child["parentTaskId"] == parent["id"]

    dependency = await client.post(
        f"/api/v1/tasks/{parent['id']}/dependencies",
        json={
            "expectedVersion": 4,
            "predecessorTaskId": child["id"],
            "reason": "A conferência precede o protocolo.",
        },
    )
    assert dependency.status_code == 201, dependency.text

    cycle = await client.post(
        f"/api/v1/tasks/{child['id']}/dependencies",
        json={
            "expectedVersion": 1,
            "predecessorTaskId": parent["id"],
            "reason": "Tentativa deliberada de ciclo.",
        },
    )
    assert cycle.status_code == 409, cycle.text
    assert cycle.json()["detail"]["code"] == "TASK_DEPENDENCY_CYCLE"

    comment = await client.post(
        f"/api/v1/tasks/{parent['id']}/comments",
        json={
            "expectedVersion": 5,
            "comment": "Documento conferido com a responsável.",
            "documentIds": [str(document.id)],
            "mentionedUserIds": [str(mentioned.id)],
        },
    )
    assert comment.status_code == 200, comment.text
    assert comment.json()["version"] == 6

    resources = await client.get(f"/api/v1/tasks/{parent['id']}/legal-resources")
    assert resources.status_code == 200, resources.text
    payload = resources.json()
    assert [item["name"] for item in payload["labels"]] == ["Protocolo"]
    assert [item["id"] for item in payload["subtasks"]] == [child["id"]]
    assert payload["dependencies"][0]["predecessorTaskId"] == child["id"]
    assert payload["attachments"][0]["documentId"] == str(document.id)
    assert payload["attachments"][0]["nameSnapshot"] == "procuração assinada.pdf"
    assert payload["mentions"][0]["mentionedUserId"] == str(mentioned.id)
    assert payload["mentions"][0]["labelSnapshot"] == "Pessoa Mencionada"


@pytest.mark.asyncio
async def test_legal_resources_and_dependencies_do_not_expose_tasks_hidden_from_actor(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    matter = await _matter(client)
    visible_actor = await _member(db_session, auth_context.workspace_id, "Executor Visivel")
    hidden_actor = await _member(db_session, auth_context.workspace_id, "Executor Oculto")

    parent_response = await client.post(
        f"/api/v1/matters/{matter['id']}/tasks",
        json={
            "title": "Tarefa visível",
            "participants": [{"userId": str(visible_actor.id), "role": "owner"}],
        },
    )
    hidden_response = await client.post(
        f"/api/v1/matters/{matter['id']}/tasks",
        json={
            "title": "Tarefa oculta",
            "participants": [{"userId": str(hidden_actor.id), "role": "owner"}],
        },
    )
    visible_candidate_response = await client.post(
        f"/api/v1/matters/{matter['id']}/tasks",
        json={
            "title": "Tarefa candidata autorizada",
            "participants": [{"userId": str(visible_actor.id), "role": "owner"}],
        },
    )
    assert (
        parent_response.status_code
        == hidden_response.status_code
        == visible_candidate_response.status_code
        == 201
    )
    parent = parent_response.json()
    hidden = hidden_response.json()

    # Vincula a tarefa oculta como subtarefa diretamente para simular dados
    # legados e provar que o read model ainda aplica o escopo do ator.
    hidden_task = await db_session.get(Task, hidden["id"])
    assert hidden_task is not None
    hidden_task.parent_task_id = parent["id"]
    db_session.add(
        TaskDependency(
            workspace_id=auth_context.workspace_id,
            matter_id=matter["id"],
            predecessor_task_id=hidden["id"],
            successor_task_id=parent["id"],
            created_by_user_id=auth_context.user_id,
        )
    )
    await db_session.commit()

    visible_membership = await db_session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == auth_context.workspace_id,
            WorkspaceMembership.user_id == visible_actor.id,
        )
    )
    assert visible_membership is not None
    auth_context.user_id = visible_actor.id
    auth_context.membership_id = visible_membership.id
    auth_context.role = WorkspaceRole.operator
    resources = await client.get(f"/api/v1/tasks/{parent['id']}/legal-resources")
    assert resources.status_code == 200, resources.text
    assert resources.json()["subtasks"] == []
    assert resources.json()["dependencies"] == []

    candidates = await client.get(
        f"/api/v1/tasks/{parent['id']}/dependency-candidates"
    )
    assert candidates.status_code == 200, candidates.text
    candidate_items = candidates.json()
    assert [item["label"] for item in candidate_items] == [
        "Tarefa candidata autorizada"
    ]
    assert all("id" not in item for item in candidate_items)
    candidate_handle = candidate_items[0]["handle"]

    dependency = await client.post(
        f"/api/v1/tasks/{parent['id']}/dependencies",
        json={
            "expectedVersion": parent["version"],
            "predecessorTaskId": hidden["id"],
            "reason": "Tentativa de vincular uma tarefa invisível.",
        },
    )
    assert dependency.status_code == 422
    assert dependency.json()["detail"]["code"] == "TASK_DEPENDENCY_SCOPE_INVALID"

    dependency_by_reference = await client.post(
        f"/api/v1/tasks/{parent['id']}/dependencies",
        json={
            "expectedVersion": parent["version"],
            "predecessorRef": candidate_handle,
            "reason": "Vínculo seguro pela referência opaca.",
        },
    )
    assert dependency_by_reference.status_code == 201, dependency_by_reference.text
    assert (
        dependency_by_reference.json()["predecessorTaskId"]
        == visible_candidate_response.json()["id"]
    )

@pytest.mark.asyncio
async def test_operational_filters_compose_and_keep_cursor_inside_the_authorized_collection(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    first_matter = await _matter(client)
    executor = await _member(db_session, auth_context.workspace_id, "Executor Filtro")
    reviewer = await _member(db_session, auth_context.workspace_id, "Revisor Filtro")

    second_matter_response = await client.post(
        "/api/v1/matters",
        json={
            "clientId": first_matter["clientId"],
            "practiceArea": "civel",
            "demandType": "Segundo caso do mesmo cliente",
            "columnId": "novo_lead",
            "microStatus": "lead_novo",
        },
    )
    assert second_matter_response.status_code == 201, second_matter_response.text
    second_matter = second_matter_response.json()
    other_matter = await _matter(client)

    first = await client.post(
        f"/api/v1/matters/{first_matter['id']}/tasks",
        json={
            "title": "Tarefa agosto filtrável",
            "priority": "alta",
            "dueAt": "2026-08-15T15:00:00Z",
            "participants": [
                {"userId": str(auth_context.user_id), "role": "owner"},
                {"userId": str(executor.id), "role": "assignee"},
                {"userId": str(reviewer.id), "role": "reviewer"},
            ],
        },
    )
    assert first.status_code == 201, first.text
    second = await client.post(
        f"/api/v1/matters/{second_matter['id']}/tasks",
        json={
            "title": "Tarefa setembro do mesmo cliente",
            "priority": "baixa",
            "dueAt": "2026-09-15T15:00:00Z",
            "participants": [{"userId": str(auth_context.user_id), "role": "owner"}],
        },
    )
    assert second.status_code == 201, second.text
    other = await client.post(
        f"/api/v1/matters/{other_matter['id']}/tasks",
        json={
            "title": "Tarefa de outro cliente",
            "priority": "media",
            "dueAt": "2026-10-15T15:00:00Z",
            "participants": [{"userId": str(auth_context.user_id), "role": "owner"}],
        },
    )
    assert other.status_code == 201, other.text

    cases = {
        "matter": ({"matter": first_matter["id"]}, {first.json()["id"]}),
        "client": (
            {"client": first_matter["clientId"]},
            {first.json()["id"], second.json()["id"]},
        ),
        "owner": (
            {"owner": str(auth_context.user_id)},
            {first.json()["id"], second.json()["id"], other.json()["id"]},
        ),
        "assignee": ({"assignee": str(executor.id)}, {first.json()["id"]}),
        "reviewer": ({"reviewer": str(reviewer.id)}, {first.json()["id"]}),
        "priority": ({"priority": "alta"}, {first.json()["id"]}),
        "period": (
            {
                "dueFrom": "2026-08-01T00:00:00Z",
                "dueTo": "2026-09-01T00:00:00Z",
            },
            {first.json()["id"]},
        ),
    }
    for label, (params, expected_ids) in cases.items():
        response = await client.get("/api/v1/tasks", params={"scope": "mine", **params})
        assert response.status_code == 200, f"{label}: {response.text}"
        assert {item["id"] for item in response.json()["items"]} == expected_ids

    impossible_intersection = await client.get(
        "/api/v1/tasks",
        params={
            "scope": "mine",
            "matter": first_matter["id"],
            "client": other_matter["clientId"],
        },
    )
    assert impossible_intersection.status_code == 200, impossible_intersection.text
    assert impossible_intersection.json()["items"] == []

    first_page = await client.get(
        "/api/v1/tasks",
        params={"scope": "mine", "client": first_matter["clientId"], "limit": 1},
    )
    assert first_page.status_code == 200, first_page.text
    assert first_page.json()["hasMore"] is True
    second_page = await client.get(
        "/api/v1/tasks",
        params={
            "scope": "mine",
            "client": first_matter["clientId"],
            "limit": 1,
            "cursor": first_page.json()["nextCursor"],
        },
    )
    assert second_page.status_code == 200, second_page.text
    paged_ids = {
        first_page.json()["items"][0]["id"],
        second_page.json()["items"][0]["id"],
    }
    assert paged_ids == {first.json()["id"], second.json()["id"]}

    matter_options = await client.get(
        "/api/v1/tasks/filter-options/matters",
        params={
            "scope": "mine",
            "search": "Segundo caso",
            "selectedId": first_matter["id"],
        },
    )
    assert matter_options.status_code == 200, matter_options.text
    assert {item["id"] for item in matter_options.json()} == {
        first_matter["id"],
        second_matter["id"],
    }
    first_matter_option = next(
        item for item in matter_options.json() if item["id"] == first_matter["id"]
    )
    assert first_matter["id"] not in first_matter_option["handle"]
    by_opaque_matter = await client.get(
        "/api/v1/tasks",
        params={"scope": "mine", "matterRef": first_matter_option["handle"]},
    )
    assert by_opaque_matter.status_code == 200, by_opaque_matter.text
    assert {item["id"] for item in by_opaque_matter.json()["items"]} == {
        first.json()["id"]
    }
    tampered_handle = (
        first_matter_option["handle"][:-1]
        + ("A" if first_matter_option["handle"][-1] != "A" else "B")
    )
    tampered = await client.get(
        "/api/v1/tasks",
        params={"scope": "mine", "matterRef": tampered_handle},
    )
    assert tampered.status_code == 422, tampered.text
    assert tampered.json()["detail"]["code"] == "TASK_FILTER_REFERENCE_INVALID"

    client_options = await client.get(
        "/api/v1/tasks/filter-options/clients",
        params={"scope": "mine", "search": "Cliente API Task"},
    )
    assert client_options.status_code == 200, client_options.text
    assert {
        (item["id"], item["label"], item["role"])
        for item in client_options.json()
    } == {
        (first_matter["clientId"], "Cliente API Task", "client"),
        (other_matter["clientId"], "Cliente API Task", "client"),
    }
    client_handle = client_options.json()[0]["handle"]
    wrong_kind = await client.get(
        "/api/v1/tasks",
        params={"scope": "mine", "matterRef": client_handle},
    )
    assert wrong_kind.status_code == 422, wrong_kind.text
    assert wrong_kind.json()["detail"]["code"] == "TASK_FILTER_REFERENCE_INVALID"


@pytest.mark.asyncio
async def test_task_id_routes_fail_closed_for_workspace_member_outside_authorized_collection(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    matter = await _matter(client)
    created = await client.post(
        f"/api/v1/matters/{matter['id']}/tasks",
        json={
            "title": "Tarefa privada da equipe original",
            "participants": [
                {"userId": str(auth_context.user_id), "role": "owner"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()
    original_user_id = auth_context.user_id

    outsider = await _member(db_session, auth_context.workspace_id, "Gestor sem equipe")
    outsider_membership = await db_session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == auth_context.workspace_id,
            WorkspaceMembership.user_id == outsider.id,
        )
    )
    assert outsider_membership is not None
    outsider_membership.role = WorkspaceRole.workspace_owner
    auth_context.user_id = outsider.id
    auth_context.membership_id = outsider_membership.id
    auth_context.role = WorkspaceRole.workspace_owner
    await db_session.flush()

    reads = [
        await client.get(f"/api/v1/tasks/{task['id']}"),
        await client.get(f"/api/v1/tasks/{task['id']}/events"),
    ]
    for response in reads:
        assert response.status_code == 404, response.text

    matter_tasks = await client.get(f"/api/v1/matters/{matter['id']}/tasks")
    assert matter_tasks.status_code == 200, matter_tasks.text
    assert matter_tasks.json() == []

    mutations = [
        await client.patch(
            f"/api/v1/tasks/{task['id']}",
            json={"expectedVersion": task["version"], "title": "Acesso indevido"},
        ),
        await client.patch(
            f"/api/v1/tasks/{task['id']}/toggle",
            params={"expectedVersion": task["version"]},
        ),
        await client.put(
            f"/api/v1/tasks/{task['id']}/participants",
            json={
                "expectedVersion": task["version"],
                "participants": [
                    {"userId": str(original_user_id), "role": "owner"},
                ],
                "reason": "Tentativa fora da coleção autorizada.",
            },
        ),
        await client.put(
            f"/api/v1/tasks/{task['id']}/participants/{original_user_id}/state",
            json={
                "expectedVersion": task["version"],
                "state": "in_progress",
                "reason": "Tentativa fora da coleção autorizada.",
            },
        ),
        await client.post(
            f"/api/v1/tasks/{task['id']}/commands/start",
            json={"expectedVersion": task["version"]},
        ),
        await client.post(
            f"/api/v1/tasks/{task['id']}/comments",
            json={
                "expectedVersion": task["version"],
                "comment": "Comentário que não deve ser persistido.",
            },
        ),
    ]
    for response in mutations:
        assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_api_rejects_reviewer_who_is_also_owner(client, auth_context: AuthContext) -> None:
    matter = await _matter(client)
    response = await client.post(
        f"/api/v1/matters/{matter['id']}/tasks",
        json={
            "title": "Autorrevisão proibida",
            "participants": [
                {"userId": str(auth_context.user_id), "role": "owner"},
                {"userId": str(auth_context.user_id), "role": "reviewer"},
            ],
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "TASK_REVIEWER_CONFLICT"


@pytest.mark.asyncio
async def test_hidden_lead_identity_is_not_a_task_search_oracle(
    client,
    auth_context: AuthContext,
) -> None:
    matter = await _matter(client)
    created = await client.post(
        f"/api/v1/matters/{matter['id']}/tasks",
        json={
            # A identidade duplicada num campo operacional não pode reabrir o
            # oráculo depois de ``Client.name`` sair do predicado de busca.
            "title": "Preparar peça para Cliente API Task",
            "participants": [
                {"userId": str(auth_context.user_id), "role": "owner"},
            ],
        },
    )
    assert created.status_code == 201, created.text

    fastapi_app.dependency_overrides[can_view_sensitive_lead_data] = lambda: False

    by_secret_name = await client.get(
        "/api/v1/tasks",
        params={"scope": "mine", "search": "Cliente API Task"},
    )
    assert by_secret_name.status_code == 200, by_secret_name.text
    assert by_secret_name.json()["items"] == []

    option_by_secret_name = await client.get(
        "/api/v1/tasks/matter-options",
        params={"search": "Cliente API Task"},
    )
    assert option_by_secret_name.status_code == 200, option_by_secret_name.text
    assert option_by_secret_name.json() == []

    by_allowed_demand = await client.get(
        "/api/v1/tasks",
        params={"scope": "mine", "search": "colaborativa"},
    )
    assert by_allowed_demand.status_code == 200, by_allowed_demand.text
    assert len(by_allowed_demand.json()["items"]) == 1
    assert by_allowed_demand.json()["items"][0]["clientLabel"].startswith("Lead ")
    assert "Cliente API Task" not in by_allowed_demand.text

    allowed_option = await client.get(
        "/api/v1/tasks/matter-options",
        params={"search": "colaborativa"},
    )
    assert allowed_option.status_code == 200, allowed_option.text
    assert len(allowed_option.json()) == 1
    assert allowed_option.json()[0]["clientLabel"].startswith("Lead ")
    assert "Cliente API Task" not in allowed_option.text


@pytest.mark.asyncio
async def test_member_options_are_paginated_beyond_legacy_fifty_member_cap(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    seeded = [
        await _member(db_session, auth_context.workspace_id, f"Pagina {index:02d}")
        for index in range(55)
    ]
    first = await client.get(
        "/api/v1/tasks/member-options/page",
        params={"search": "Pagina", "limit": 50, "offset": 0},
    )
    assert first.status_code == 200, first.text
    assert first.json()["total"] == 55
    assert len(first.json()["items"]) == 50
    assert first.json()["hasMore"] is True

    second = await client.get(
        "/api/v1/tasks/member-options/page",
        params={"search": "Pagina", "limit": 50, "offset": 50},
    )
    assert second.status_code == 200, second.text
    assert second.json()["total"] == 55
    assert len(second.json()["items"]) == 5
    assert second.json()["hasMore"] is False
    returned_ids = {item["id"] for item in first.json()["items"] + second.json()["items"]}
    assert returned_ids == {str(item.id) for item in seeded}


@pytest.mark.asyncio
async def test_assignee_can_update_self_but_other_user_requires_tasks_assign(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    matter = await _matter(client)
    owner = await _member(db_session, auth_context.workspace_id, "Owner Estado")
    other_assignee = await _member(db_session, auth_context.workspace_id, "Executor Estado Outro")
    created = await client.post(
        f"/api/v1/matters/{matter['id']}/tasks",
        json={
            "title": "Estado individual via API",
            "participants": [
                {"userId": str(owner.id), "role": "owner"},
                {"userId": str(auth_context.user_id), "role": "assignee"},
                {"userId": str(other_assignee.id), "role": "assignee"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    self_participant = next(
        item for item in body["participants"] if item["userId"] == str(auth_context.user_id)
    )
    assert self_participant["labelSnapshot"]
    assert self_participant["requiredForCompletion"] is True
    assert self_participant["state"] == "assigned"

    self_update = await client.put(
        f"/api/v1/tasks/{body['id']}/participants/{auth_context.user_id}/state",
        json={
            "expectedVersion": 1,
            "state": "completed",
            "reason": "Minha parte foi concluída.",
        },
    )
    assert self_update.status_code == 200, self_update.text
    assert self_update.json()["version"] == 2

    membership = await db_session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.id == auth_context.membership_id,
            WorkspaceMembership.workspace_id == auth_context.workspace_id,
        )
    )
    assert membership is not None
    membership.permission_denials = [{"module": "tasks", "action": "assign", "scope": "workspace"}]
    await db_session.commit()

    denied = await client.put(
        f"/api/v1/tasks/{body['id']}/participants/{other_assignee.id}/state",
        json={
            "expectedVersion": 2,
            "state": "completed",
            "reason": "Tentativa de concluir por terceiro.",
        },
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["detail"]["code"] == "AUTH-012"
