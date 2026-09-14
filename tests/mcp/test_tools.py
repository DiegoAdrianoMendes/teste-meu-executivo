from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.mcp.errors import McpToolError
from app.mcp.revocations import GrantRevocationStore
from app.mcp.tools import MutationCoordinator
from app.models.approval import ApprovalQueueItem
from app.models.client import Client
from app.models.klm_operations import ExternalActionIntent, KlmAuditEvent
from app.models.matter import Matter, PracticeArea
from app.models.task import Task
from app.models.timeline_event import TimelineEvent, TimelineEventType
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from tests.mcp.conftest import runtime_for


async def seed_other_tenant(db_session):
    workspace = Workspace(
        name="Outro Escritório",
        slug=f"other-{uuid4()}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    user = User(
        name="Outro Usuário",
        email=f"other-{uuid4()}@example.com",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="OU",
        color="#000000",
        is_active=True,
    )
    db_session.add_all([workspace, user])
    await db_session.flush()
    membership = WorkspaceMembership(
        workspace_id=workspace.id,
        user_id=user.id,
        role=WorkspaceRole.workspace_owner,
        status=MembershipStatus.active,
    )
    client = Client(workspace_id=workspace.id, name="Cliente de Outro Tenant")
    db_session.add_all([membership, client])
    await db_session.flush()
    matter = Matter(
        workspace_id=workspace.id,
        client_id=client.id,
        practice_area=PracticeArea.saude_suplementar,
        demand_type="Caso secreto de outro tenant",
    )
    db_session.add(matter)
    await db_session.flush()
    return workspace, user, client, matter


@pytest.mark.asyncio
async def test_sensitive_grant_returns_identity(mcp_runtime, legal_matter):
    client, _matter = legal_matter
    result = await mcp_runtime.call_tool(
        "klm.crm.get_client",
        {"client_id": str(client.id)},
    )
    assert result["client"]["name"] == "Ana Saúde"
    assert result["client"]["cpf"] == "123.456.789-01"
    assert result["client"]["sensitiveDataHidden"] is False


@pytest.mark.asyncio
async def test_read_only_grant_masks_identity_and_health_details(
    auth_context,
    db_session,
    tmp_path: Path,
    legal_matter,
):
    client, matter = legal_matter
    runtime = runtime_for(
        auth_context,
        db_session,
        tmp_path,
        capabilities=("crm.read",),
    )
    client_result = await runtime.call_tool(
        "klm.crm.get_client",
        {"client_id": str(client.id)},
    )
    matter_result = await runtime.call_tool(
        "klm.crm.get_matter",
        {"matter_id": str(matter.id)},
    )
    assert client_result["client"]["label"].startswith("Lead ")
    assert "name" not in client_result["client"]
    assert "healthObjectName" not in matter_result["matter"]
    assert "qualification" not in matter_result["matter"]


@pytest.mark.asyncio
async def test_masked_grant_cannot_search_client_identity(
    auth_context,
    db_session,
    tmp_path: Path,
    legal_matter,
):
    runtime = runtime_for(
        auth_context,
        db_session,
        tmp_path,
        capabilities=("crm.read",),
    )
    with pytest.raises(McpToolError) as error:
        await runtime.call_tool("klm.crm.list_clients", {"query": "Ana"})
    assert error.value.code == "MCP_SENSITIVE_SEARCH_DENIED"


@pytest.mark.asyncio
async def test_other_tenant_client_and_matter_are_not_addressable(
    mcp_runtime,
    db_session,
):
    _workspace, _user, client, matter = await seed_other_tenant(db_session)
    client_id = client.id
    matter_id = matter.id
    await db_session.commit()
    with pytest.raises(McpToolError) as client_error:
        await mcp_runtime.call_tool("klm.crm.get_client", {"client_id": str(client_id)})
    with pytest.raises(McpToolError) as matter_error:
        await mcp_runtime.call_tool("klm.crm.get_matter", {"matter_id": str(matter_id)})
    assert client_error.value.code == "MCP_NOT_FOUND"
    assert matter_error.value.code == "MCP_NOT_FOUND"


@pytest.mark.asyncio
async def test_membership_revocation_takes_effect_on_next_call(
    mcp_runtime,
    db_session,
    auth_context,
):
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    membership.status = MembershipStatus.suspended
    await db_session.commit()
    with pytest.raises(McpToolError) as error:
        await mcp_runtime.call_tool("klm.crm.list_matters", {})
    assert error.value.code == "MCP_IDENTITY_REVOKED"


@pytest.mark.asyncio
async def test_expired_runtime_grant_fails_before_database_call(
    auth_context,
    db_session,
    tmp_path: Path,
):
    runtime = runtime_for(
        auth_context,
        db_session,
        tmp_path,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    with pytest.raises(McpToolError) as error:
        await runtime.call_tool("klm.crm.list_matters", {})
    assert error.value.code == "MCP_GRANT_EXPIRED"


@pytest.mark.asyncio
async def test_revoked_grant_stops_an_already_running_runtime_on_next_call(
    auth_context,
    db_session,
    tmp_path: Path,
):
    runtime = runtime_for(auth_context, db_session, tmp_path)
    await runtime.call_tool("klm.crm.list_matters", {})

    marker = GrantRevocationStore(tmp_path).revoke(runtime.claims.grant_id)

    assert marker.exists()
    with pytest.raises(McpToolError) as error:
        await runtime.call_tool("klm.crm.list_matters", {})
    assert error.value.code == "MCP_GRANT_REVOKED"


@pytest.mark.asyncio
async def test_unknown_kanban_column_is_rejected(mcp_runtime):
    with pytest.raises(McpToolError) as error:
        await mcp_runtime.call_tool(
            "klm.crm.list_matters",
            {"column_id": "invented-column"},
        )
    assert error.value.code == "MCP_INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_pagination_is_capped_by_schema(mcp_runtime):
    with pytest.raises(McpToolError) as error:
        await mcp_runtime.call_tool("klm.crm.list_clients", {"limit": 51})
    assert error.value.code == "MCP_INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_workspace_in_tool_arguments_is_rejected(mcp_runtime):
    with pytest.raises(McpToolError) as error:
        await mcp_runtime.call_tool(
            "klm.crm.list_matters",
            {"workspace_id": str(uuid4())},
        )
    assert error.value.code == "MCP_INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_false_confirmation_creates_nothing(mcp_runtime, legal_matter, db_session):
    _client, matter = legal_matter
    with pytest.raises(McpToolError) as error:
        await mcp_runtime.call_tool(
            "klm.crm.create_case_note",
            {
                "matter_id": str(matter.id),
                "note": "Não deve ser criada",
                "confirm": False,
                "idempotency_key": "note-false-0001",
            },
        )
    count = await db_session.scalar(
        select(func.count(TimelineEvent.id)).where(
            TimelineEvent.matter_id == matter.id,
            TimelineEvent.event_type == TimelineEventType.comment,
        )
    )
    assert error.value.code == "MCP_INVALID_ARGUMENT"
    assert count == 0


@pytest.mark.asyncio
async def test_qualification_is_audited_and_idempotent(
    mcp_runtime,
    legal_matter,
    db_session,
):
    _client, matter = legal_matter
    key = "qualify-case-0001"
    arguments = {
        "matter_id": str(matter.id),
        "lead_score": 91,
        "classification": "Premium",
        "priority": "alta",
        "urgency": "urgente",
        "tags": ["Liminar", "Alto custo", "liminar"],
        "qualification_summary": "Documentos médicos recebidos e negativa formal presente.",
        "next_action": "Revisão jurídica humana",
        "confirm": True,
        "idempotency_key": key,
    }
    first = await mcp_runtime.call_tool("klm.crm.qualify_and_tag_matter", arguments)
    second = await mcp_runtime.call_tool("klm.crm.qualify_and_tag_matter", arguments)
    await db_session.refresh(matter)
    timeline_count = await db_session.scalar(
        select(func.count(TimelineEvent.id)).where(
            TimelineEvent.matter_id == matter.id,
            TimelineEvent.title == "Qualificação CRM atualizada via MCP",
        )
    )
    audit_count = await db_session.scalar(
        select(func.count(KlmAuditEvent.id)).where(
            KlmAuditEvent.workspace_id == matter.workspace_id,
            KlmAuditEvent.correlation_id == MutationCoordinator.correlation_id(key),
        )
    )
    assert first["deduplicated"] is False
    assert second["deduplicated"] is True
    assert matter.lead_score == 91
    assert matter.sdr_qualification["tags"] == ["Liminar", "Alto custo"]
    assert timeline_count == 1
    assert audit_count == 1


@pytest.mark.asyncio
async def test_qualification_rechecks_live_lead_edit_permission(
    mcp_runtime,
    legal_matter,
    db_session,
    auth_context,
):
    _client, matter = legal_matter
    matter_id = matter.id
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    membership.role = WorkspaceRole.operator
    await db_session.commit()
    with pytest.raises(McpToolError) as error:
        await mcp_runtime.call_tool(
            "klm.crm.qualify_and_tag_matter",
            {
                "matter_id": str(matter_id),
                "lead_score": 80,
                "confirm": True,
                "idempotency_key": "qualification-denied-1",
            },
        )
    assert error.value.code == "MCP_PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_idempotency_key_cannot_be_reused_for_another_tool(
    mcp_runtime,
    legal_matter,
):
    _client, matter = legal_matter
    key = "global-idempotency-0001"
    await mcp_runtime.call_tool(
        "klm.crm.create_case_note",
        {
            "matter_id": str(matter.id),
            "note": "Nota original",
            "confirm": True,
            "idempotency_key": key,
        },
    )
    with pytest.raises(McpToolError) as error:
        await mcp_runtime.call_tool(
            "klm.crm.create_task",
            {
                "matter_id": str(matter.id),
                "title": "Tarefa conflitante",
                "confirm": True,
                "idempotency_key": key,
            },
        )
    assert error.value.code == "MCP_IDEMPOTENCY_CONFLICT"


@pytest.mark.asyncio
async def test_case_note_is_internal_and_idempotent(
    mcp_runtime,
    legal_matter,
    db_session,
):
    _client, matter = legal_matter
    arguments = {
        "matter_id": str(matter.id),
        "note": "Solicitar relatório médico atualizado.",
        "visibility": "internal",
        "confirm": True,
        "idempotency_key": "internal-note-0001",
    }
    first = await mcp_runtime.call_tool("klm.crm.create_case_note", arguments)
    second = await mcp_runtime.call_tool("klm.crm.create_case_note", arguments)
    count = await db_session.scalar(
        select(func.count(TimelineEvent.id)).where(
            TimelineEvent.matter_id == matter.id,
            TimelineEvent.event_type == TimelineEventType.comment,
        )
    )
    assert first["visibility"] == "internal"
    assert second["eventId"] == first["eventId"]
    assert second["deduplicated"] is True
    assert count == 1


@pytest.mark.asyncio
async def test_task_accepts_active_same_workspace_assignee(
    mcp_runtime,
    legal_matter,
    auth_context,
    db_session,
):
    _client, matter = legal_matter
    result = await mcp_runtime.call_tool(
        "klm.crm.create_task",
        {
            "matter_id": str(matter.id),
            "title": "Revisar negativa do plano",
            "assigned_to": str(auth_context.user_id),
            "due_date": "2026-07-20",
            "priority": "alta",
            "confirm": True,
            "idempotency_key": "task-same-tenant-1",
        },
    )
    task = await db_session.get(Task, result["taskId"])
    assert task.assigned_to == auth_context.user_id
    assert task.is_ai_generated is True


@pytest.mark.asyncio
async def test_task_explicit_assignee_revalidates_tasks_assign_permission(
    mcp_runtime,
    legal_matter,
    auth_context,
    db_session,
):
    _client, matter = legal_matter
    matter_id = matter.id
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    membership.permission_denials = [
        {"module": "tasks", "action": "assign", "scope": "workspace"}
    ]
    await db_session.commit()

    with pytest.raises(McpToolError) as denied:
        await mcp_runtime.call_tool(
            "klm.crm.create_task",
            {
                "matter_id": str(matter_id),
                "title": "Atribuição MCP proibida",
                "assigned_to": str(auth_context.user_id),
                "confirm": True,
                "idempotency_key": "task-assign-denied-1",
            },
        )
    assert denied.value.code == "MCP_PERMISSION_DENIED"
    assert denied.value.details == {"missingPermissions": ["tasks.assign"]}

    implicit_owner = await mcp_runtime.call_tool(
        "klm.crm.create_task",
        {
            "matter_id": str(matter_id),
            "title": "Owner MCP implícito permitido",
            "confirm": True,
            "idempotency_key": "task-implicit-owner-1",
        },
    )
    assert implicit_owner["assignedTo"] == str(auth_context.user_id)


@pytest.mark.asyncio
async def test_task_rejects_assignee_from_other_tenant(
    mcp_runtime,
    legal_matter,
    db_session,
):
    _workspace, other_user, _client, _matter = await seed_other_tenant(db_session)
    _client, matter = legal_matter
    with pytest.raises(McpToolError) as error:
        await mcp_runtime.call_tool(
            "klm.crm.create_task",
            {
                "matter_id": str(matter.id),
                "title": "Não pode vazar",
                "assigned_to": str(other_user.id),
                "confirm": True,
                "idempotency_key": "task-cross-tenant-1",
            },
        )
    assert error.value.code == "MCP_INVALID_ASSIGNEE"


@pytest.mark.asyncio
async def test_handoff_creates_only_local_human_approval(
    mcp_runtime,
    legal_matter,
    db_session,
):
    _client, matter = legal_matter
    result = await mcp_runtime.call_tool(
        "klm.crm.request_human_handoff",
        {
            "matter_id": str(matter.id),
            "reason": "O lead pediu para falar com o advogado responsável.",
            "confirm": True,
            "idempotency_key": "handoff-human-0001",
        },
    )
    approval = await db_session.get(ApprovalQueueItem, result["approvalId"])
    external_count = await db_session.scalar(select(func.count(ExternalActionIntent.id)))
    await db_session.refresh(matter)
    assert approval.approval_type == "agent_handoff"
    assert matter.needs_approval is True
    assert result["externalActionExecuted"] is False
    assert external_count == 0


@pytest.mark.asyncio
async def test_approval_request_never_auto_approves(
    mcp_runtime,
    legal_matter,
    db_session,
):
    _client, matter = legal_matter
    result = await mcp_runtime.call_tool(
        "klm.crm.request_approval",
        {
            "matter_id": str(matter.id),
            "approval_type": "petition_review",
            "title": "Revisar minuta de inicial",
            "document_version_url": "https://docs.google.com/document/d/example",
            "confirm": True,
            "idempotency_key": "approval-petition-1",
        },
    )
    approval = await db_session.get(ApprovalQueueItem, result["approvalId"])
    assert approval.status.value == "pending"
    assert result["autoApproved"] is False


@pytest.mark.asyncio
async def test_approval_rejects_unsafe_document_url(mcp_runtime, legal_matter):
    _client, matter = legal_matter
    with pytest.raises(McpToolError) as error:
        await mcp_runtime.call_tool(
            "klm.crm.request_approval",
            {
                "matter_id": str(matter.id),
                "approval_type": "petition_review",
                "title": "URL insegura",
                "document_version_url": "javascript:alert(1)",
                "confirm": True,
                "idempotency_key": "approval-unsafe-url-1",
            },
        )
    assert error.value.code == "MCP_INVALID_ARGUMENT"
