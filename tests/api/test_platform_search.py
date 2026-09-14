from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints.platform_search import router as platform_search_router
from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.models.growth_crm import GrowthAcquisitionChannel, GrowthFunnelStage, GrowthProspect
from app.models.platform_inbox import (
    PlatformInboxChannel,
    PlatformInboxConversation,
    PlatformInboxQueue,
    PlatformInboxStatus,
)
from app.models.platform_incident import PlatformIncident
from app.models.support_ticket import (
    SupportTicket,
    SupportTicketPriority,
    SupportTicketQueue,
    SupportTicketSeverity,
    SupportTicketStatus,
    SupportTicketType,
)
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.services.platform_search_service import PlatformSearchService

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def platform_search_environment(db_session):
    root = User(
        name="Safe Root",
        email="safe-root@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="SR",
        color="#123456",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=True,
        platform_access_active=True,
        platform_role_package="platform_owner",
    )
    db_session.add(root)
    await db_session.flush()
    context = AuthContext(
        user_id=root.id,
        workspace_id=None,
        membership_id=None,
        role=None,
        is_internal_staff=True,
        surface="platform",
    )

    test_app = FastAPI()
    test_app.include_router(platform_search_router, prefix="/api/v1")

    async def override_get_db():
        yield db_session

    async def override_context():
        return context

    test_app.dependency_overrides[get_db] = override_get_db
    test_app.dependency_overrides[get_current_auth_context] = override_context
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as client:
        yield client, context, root


async def _seed_searchable_records(db_session):
    workspace = Workspace(
        name="Safe Conta",
        slug=f"safe-conta-{uuid4().hex[:8]}",
        legal_name="Safe Conta Legal",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        onboarding_completed=False,
        settings={},
    )
    customer = User(
        name="Safe Cliente",
        email=f"safe-cliente-{uuid4().hex[:8]}@private.example",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="SC",
        color="#111111",
        is_active=True,
        is_internal_staff=False,
    )
    staff = User(
        name="Safe Funcionário",
        email=f"safe-staff-{uuid4().hex[:8]}@internal.example",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="SF",
        color="#222222",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=False,
        platform_access_active=True,
        platform_role_package="support",
    )
    db_session.add_all([workspace, customer, staff])
    await db_session.flush()
    membership = WorkspaceMembership(
        workspace_id=workspace.id,
        user_id=customer.id,
        role=WorkspaceRole.workspace_owner,
        status=MembershipStatus.active,
        is_default=True,
    )
    lead = GrowthProspect(
        nome_escritorio="Safe Lead",
        contato_nome="Contato Seguro",
        contato_email="safe-lead@private.example",
        origem=GrowthAcquisitionChannel.inbound_site,
        estagio=GrowthFunnelStage.prospect,
        notas="PRIVATE-LEAD-NOTES",
    )
    now = datetime.now(UTC)
    ticket = SupportTicket(
        public_number=f"SAC-SAFE-{uuid4().hex[:8]}",
        workspace_id=workspace.id,
        reporter_user_id=customer.id,
        ticket_type=SupportTicketType.problem,
        priority=SupportTicketPriority.high,
        severity=SupportTicketSeverity.high,
        queue=SupportTicketQueue.support,
        status=SupportTicketStatus.new,
        subject="Safe Ticket",
        description="PRIVATE-TICKET-DESCRIPTION",
        technical_context={},
        idempotency_key=f"safe-{uuid4()}",
        version=1,
        first_response_due_at=now + timedelta(hours=1),
        resolution_due_at=now + timedelta(days=1),
    )
    sales_conversation = PlatformInboxConversation(
        channel=PlatformInboxChannel.webchat,
        contact_name="Safe Conversa Vendas",
        contact_address="safe-sales@private.example",
        queue=PlatformInboxQueue.sales,
        status=PlatformInboxStatus.open,
        workspace_id=workspace.id,
        subject="Safe Interesse comercial",
        last_message_preview="PRIVATE-SALES-MESSAGE",
        metadata_json={},
    )
    support_conversation = PlatformInboxConversation(
        channel=PlatformInboxChannel.email_support,
        contact_name="Safe Conversa Suporte",
        contact_address="safe-support@private.example",
        queue=PlatformInboxQueue.support,
        status=PlatformInboxStatus.open,
        workspace_id=workspace.id,
        subject="Safe Ajuda",
        last_message_preview="PRIVATE-SUPPORT-MESSAGE",
        metadata_json={},
    )
    incident = PlatformIncident(
        title="Safe Incident",
        summary="PRIVATE-INCIDENT-SUMMARY",
        severity="sev2",
        status="investigating",
        components=["api"],
        created_by_user_id=staff.id,
        updates=[],
    )
    db_session.add_all(
        [
            membership,
            lead,
            ticket,
            sales_conversation,
            support_conversation,
            incident,
        ]
    )
    await db_session.flush()
    return {
        "workspace": workspace,
        "customer": customer,
        "staff": staff,
        "lead": lead,
        "ticket": ticket,
        "sales_conversation": sales_conversation,
        "support_conversation": support_conversation,
        "incident": incident,
    }


async def test_root_searches_all_seven_groups_without_private_content(
    platform_search_environment,
    db_session,
):
    client, _context, _root = platform_search_environment
    await _seed_searchable_records(db_session)

    response = await client.post(
        "/api/v1/platform/search/query",
        json={"query": "Safe", "limitPerType": 20},
    )

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert {group["entityType"] for group in payload["groups"]} == {
        "accounts",
        "users",
        "staff",
        "tickets",
        "leads",
        "conversations",
        "incidents",
    }
    serialized = response.text
    assert "PRIVATE-TICKET-DESCRIPTION" not in serialized
    assert "PRIVATE-LEAD-NOTES" not in serialized
    assert "PRIVATE-SALES-MESSAGE" not in serialized
    assert "PRIVATE-SUPPORT-MESSAGE" not in serialized
    assert "PRIVATE-INCIDENT-SUMMARY" not in serialized
    assert "safe-cliente-" not in serialized
    users = next(group for group in payload["groups"] if group["entityType"] == "users")
    assert users["items"][0]["maskedEmail"] == "***@private.example"
    tickets = next(group for group in payload["groups"] if group["entityType"] == "tickets")
    assert "ticket.assume" in tickets["items"][0]["allowedCommands"]
    assert tickets["items"][0]["metadata"]["version"] == 1


async def test_command_catalog_is_backend_authorized_and_root_only_stays_exclusive(
    platform_search_environment,
    db_session,
):
    client, context, _root = platform_search_environment

    root_response = await client.get("/api/v1/platform/commands/catalog")
    assert root_response.status_code == 200, root_response.text
    assert root_response.headers["cache-control"] == "no-store"
    root_ids = {item["id"] for item in root_response.json()["commands"]}
    assert "account.root_direct" in root_ids
    assert "ticket.assume" in root_ids
    assert "incident.assume" in root_ids

    platform_admin = User(
        name="Safe Platform Admin",
        email=f"safe-admin-{uuid4().hex[:8]}@internal.example",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="PA",
        color="#555555",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=True,
        platform_access_active=True,
        platform_role_package="platform_admin",
    )
    db_session.add(platform_admin)
    await db_session.flush()
    context.user_id = platform_admin.id

    admin_response = await client.get("/api/v1/platform/commands/catalog")
    assert admin_response.status_code == 200, admin_response.text
    admin_ids = {item["id"] for item in admin_response.json()["commands"]}
    assert "account.root_direct" not in admin_ids
    assert "account.create" in admin_ids


async def test_role_catalog_and_result_commands_do_not_leak_other_domains(
    platform_search_environment,
    db_session,
):
    client, context, _root = platform_search_environment
    seeded = await _seed_searchable_records(db_session)
    sales = User(
        name="Safe SDR Commands",
        email=f"safe-sdr-commands-{uuid4().hex[:8]}@internal.example",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="SC",
        color="#666666",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=False,
        platform_access_active=True,
        platform_role_package="sales_sdr",
    )
    db_session.add(sales)
    await db_session.flush()
    context.user_id = sales.id

    catalog = await client.get("/api/v1/platform/commands/catalog")
    command_ids = {item["id"] for item in catalog.json()["commands"]}
    assert {"pipeline.open", "lead.create", "lead.assume"} <= command_ids
    assert "ticket.assume" not in command_ids
    assert "staff.invite" not in command_ids
    assert "account.root_direct" not in command_ids

    search = await client.post(
        "/api/v1/platform/search/query",
        json={"query": "Safe"},
    )
    groups = {group["entityType"]: group for group in search.json()["groups"]}
    lead = next(item for item in groups["leads"]["items"] if item["id"] == str(seeded["lead"].id))
    assert lead["allowedCommands"] == ["lead.record.open", "lead.assume"]
    conversation = next(
        item
        for item in groups["conversations"]["items"]
        if item["id"] == str(seeded["sales_conversation"].id)
    )
    assert "conversation.assume" in conversation["allowedCommands"]


async def test_sales_permissions_omit_forbidden_groups_and_filter_conversation_queue(
    platform_search_environment,
    db_session,
):
    client, context, _root = platform_search_environment
    seeded = await _seed_searchable_records(db_session)
    sales = User(
        name="Safe SDR",
        email=f"safe-sdr-{uuid4().hex[:8]}@internal.example",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="SD",
        color="#333333",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=False,
        platform_access_active=True,
        platform_role_package="sales_sdr",
    )
    db_session.add(sales)
    await db_session.flush()
    context.user_id = sales.id

    response = await client.post(
        "/api/v1/platform/search/query",
        json={"query": "Safe"},
    )
    assert response.status_code == 200, response.text
    groups = {group["entityType"]: group for group in response.json()["groups"]}
    assert set(groups) == {"accounts", "users", "leads", "conversations"}
    conversation_ids = {item["id"] for item in groups["conversations"]["items"]}
    assert str(seeded["sales_conversation"].id) in conversation_ids
    assert str(seeded["support_conversation"].id) not in conversation_ids

    # A finalidade é apenas contexto: não transforma um SDR em gestor de equipe.
    forbidden_lookup = await client.post(
        "/api/v1/platform/lookups/query",
        json={"entityType": "staff", "purpose": "root_override", "query": "Safe"},
    )
    assert forbidden_lookup.status_code == 403
    assert forbidden_lookup.json()["detail"]["code"] == "PLATFORM-PERMISSION-DENIED"


async def test_lookup_query_and_resolve_hydrate_safe_labels(
    platform_search_environment,
    db_session,
):
    client, _context, _root = platform_search_environment
    seeded = await _seed_searchable_records(db_session)

    query = await client.post(
        "/api/v1/platform/lookups/query",
        json={
            "entityType": "tickets",
            "purpose": "benefit_ticket",
            "query": "SAC-SAFE",
            "workspaceId": str(seeded["workspace"].id),
        },
    )
    assert query.status_code == 200, query.text
    assert query.json()["items"][0]["publicIdentifier"].startswith("SAC-SAFE-")
    assert "PRIVATE-TICKET-DESCRIPTION" not in query.text

    resolved = await client.post(
        "/api/v1/platform/lookups/resolve",
        json={
            "entityType": "tickets",
            "purpose": "hydrate_saved_value",
            "ids": [str(seeded["ticket"].id)],
            "workspaceId": str(seeded["workspace"].id),
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["items"][0]["id"] == str(seeded["ticket"].id)


async def test_team_staff_lookup_excludes_root_and_inactive_identities(
    platform_search_environment,
    db_session,
):
    client, _context, root = platform_search_environment
    seeded = await _seed_searchable_records(db_session)
    inactive = User(
        name="Safe Funcionário Inativo",
        email=f"safe-inactive-{uuid4().hex[:8]}@internal.example",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="SI",
        color="#777777",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=False,
        platform_access_active=False,
        platform_role_package="support",
    )
    db_session.add(inactive)
    await db_session.flush()

    response = await client.post(
        "/api/v1/platform/lookups/query",
        json={
            "entityType": "staff",
            "purpose": "team_member",
            "query": "Safe",
            "limit": 20,
        },
    )

    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(seeded["staff"].id) in ids
    assert str(root.id) not in ids
    assert str(inactive.id) not in ids

    source_response = await client.post(
        "/api/v1/platform/lookups/query",
        json={
            "entityType": "staff",
            "purpose": "staff_reassignment_source",
            "query": "Safe",
            "limit": 20,
        },
    )
    target_response = await client.post(
        "/api/v1/platform/lookups/query",
        json={
            "entityType": "staff",
            "purpose": "staff_reassignment_target",
            "query": "Safe",
            "limit": 20,
        },
    )
    assert source_response.status_code == 200, source_response.text
    assert target_response.status_code == 200, target_response.text
    source_ids = {item["id"] for item in source_response.json()["items"]}
    target_ids = {item["id"] for item in target_response.json()["items"]}
    assert str(root.id) not in source_ids
    assert str(root.id) not in target_ids
    assert str(inactive.id) in source_ids
    assert str(inactive.id) not in target_ids


async def test_support_can_search_active_assignees_without_staff_directory_access(
    platform_search_environment,
    db_session,
):
    client, context, _root = platform_search_environment
    seeded = await _seed_searchable_records(db_session)
    support = User(
        name="Safe Suporte Operador",
        email=f"safe-support-{uuid4().hex[:8]}@internal.example",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="SS",
        color="#555555",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=False,
        platform_access_active=True,
        platform_role_package="support",
    )
    db_session.add(support)
    await db_session.flush()
    context.user_id = support.id

    response = await client.post(
        "/api/v1/platform/lookups/query",
        json={
            "entityType": "staff",
            "purpose": "support_assignee",
            "query": "Safe Funcionário",
            "limit": 20,
        },
    )

    assert response.status_code == 200, response.text
    assert {item["id"] for item in response.json()["items"]} == {
        str(seeded["staff"].id)
    }

    filter_response = await client.post(
        "/api/v1/platform/lookups/query",
        json={
            "entityType": "staff",
            "purpose": "support_ticket_filter",
            "query": "Safe Funcionário",
            "limit": 20,
        },
    )

    assert filter_response.status_code == 200, filter_response.text
    assert {item["id"] for item in filter_response.json()["items"]} == {
        str(seeded["staff"].id)
    }


async def test_inbox_and_growth_assignee_lookups_filter_by_operational_capability(
    platform_search_environment,
    db_session,
):
    client, context, _root = platform_search_environment
    seeded = await _seed_searchable_records(db_session)
    sales = User(
        name="Safe Comercial",
        email=f"safe-sales-{uuid4().hex[:8]}@internal.example",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="SV",
        color="#666666",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=False,
        platform_access_active=True,
        platform_role_package="sales_sdr",
    )
    db_session.add(sales)
    await db_session.flush()

    growth_response = await client.post(
        "/api/v1/platform/lookups/query",
        json={
            "entityType": "staff",
            "purpose": "growth_assignee",
            "query": "Safe",
            "limit": 20,
        },
    )
    inbox_response = await client.post(
        "/api/v1/platform/lookups/query",
        json={
            "entityType": "staff",
            "purpose": "inbox_assignee",
            "query": "Safe",
            "limit": 20,
        },
    )

    assert growth_response.status_code == 200, growth_response.text
    assert inbox_response.status_code == 200, inbox_response.text
    growth_ids = {item["id"] for item in growth_response.json()["items"]}
    inbox_ids = {item["id"] for item in inbox_response.json()["items"]}
    assert str(sales.id) in growth_ids
    assert str(seeded["staff"].id) not in growth_ids
    assert str(sales.id) in inbox_ids
    assert str(seeded["staff"].id) in inbox_ids

    context.user_id = sales.id
    allowed_for_sales = await client.post(
        "/api/v1/platform/lookups/query",
        json={
            "entityType": "staff",
            "purpose": "growth_assignee",
            "query": "Safe Comercial",
            "limit": 20,
        },
    )
    assert allowed_for_sales.status_code == 200, allowed_for_sales.text

    context.user_id = seeded["staff"].id
    forbidden_for_support = await client.post(
        "/api/v1/platform/lookups/query",
        json={
            "entityType": "staff",
            "purpose": "growth_assignee",
            "query": "Safe Comercial",
            "limit": 20,
        },
    )
    allowed_inbox_for_support = await client.post(
        "/api/v1/platform/lookups/query",
        json={
            "entityType": "staff",
            "purpose": "inbox_assignee",
            "query": "Safe",
            "limit": 20,
        },
    )
    assert forbidden_for_support.status_code == 403
    assert allowed_inbox_for_support.status_code == 200, allowed_inbox_for_support.text


async def test_one_unavailable_group_does_not_drop_healthy_groups(
    platform_search_environment,
    db_session,
    monkeypatch,
):
    client, _context, _root = platform_search_environment
    await _seed_searchable_records(db_session)

    async def unavailable_tickets(self, **kwargs):
        del self, kwargs
        raise RuntimeError("provider details must not be returned")

    monkeypatch.setattr(PlatformSearchService, "_search_tickets", unavailable_tickets)
    response = await client.post(
        "/api/v1/platform/search/query",
        json={"query": "Safe"},
    )
    assert response.status_code == 200, response.text
    groups = {group["entityType"]: group for group in response.json()["groups"]}
    assert groups["tickets"] == {
        "entityType": "tickets",
        "state": "unavailable",
        "items": [],
        "errorCode": "PLATFORM-SEARCH-GROUP-UNAVAILABLE",
    }
    assert groups["accounts"]["state"] == "ready"
    assert groups["accounts"]["items"]
    assert "provider details" not in response.text


async def test_invalid_lookup_cursor_is_rejected_without_query_execution(
    platform_search_environment,
):
    client, _context, _root = platform_search_environment
    response = await client.post(
        "/api/v1/platform/lookups/query",
        json={
            "entityType": "accounts",
            "purpose": "account_selector",
            "cursor": "not-a-platform-cursor",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "PLATFORM-LOOKUP-CURSOR-INVALID"


async def test_audit_role_can_use_account_lookup_for_audit_filter(
    platform_search_environment,
    db_session,
):
    client, context, _root = platform_search_environment
    seeded = await _seed_searchable_records(db_session)
    auditor = User(
        name="Safe Auditor",
        email=f"safe-auditor-{uuid4().hex[:8]}@internal.example",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="SA",
        color="#444444",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=False,
        platform_access_active=True,
        platform_role_package="compliance_security",
    )
    db_session.add(auditor)
    await db_session.flush()
    context.user_id = auditor.id

    allowed = await client.post(
        "/api/v1/platform/lookups/resolve",
        json={
            "entityType": "accounts",
            "purpose": "audit_filter",
            "ids": [str(seeded["workspace"].id)],
        },
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["items"][0]["label"] == "Safe Conta"


async def test_tenant_surface_cannot_use_platform_search(platform_search_environment):
    client, context, _root = platform_search_environment
    context.surface = "app"

    response = await client.post(
        "/api/v1/platform/search/query",
        json={"query": "Safe"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "BLACKBOX-IDENTITY-REQUIRED"
