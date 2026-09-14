from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.v1.endpoints.platform_work_items import router
from app.core.deps import get_current_auth_context, get_db
from app.main import app as main_app
from app.models.configuration import PlatformConfigDocument
from app.models.identity import InvitationStatus, WorkspaceInvitation
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.platform_incident import PlatformIncident
from app.models.platform_organization import PlatformStaffInvitation
from app.models.platform_work_item import PlatformWorkItem
from app.models.subscription_commercial import (
    WorkspacePlanCode,
    WorkspaceSubscription,
    WorkspaceSubscriptionStatus,
)
from app.models.super_admin_notice import (
    SuperAdminNotice,
    SuperAdminNoticeAudienceKind,
    SuperAdminNoticeChannel,
    SuperAdminNoticeStatus,
)
from app.models.support_access import SupportAccessGrant, SupportAccessScope, SupportAccessState
from app.models.support_ticket import (
    SupportTicket,
    SupportTicketPriority,
    SupportTicketQueue,
    SupportTicketSeverity,
    SupportTicketStatus,
    SupportTicketType,
)
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.services.platform_audit_event_service import PlatformAuditEventService
from app.services.platform_work_item_service import (
    PlatformWorkItemForbiddenError,
    PlatformWorkItemService,
    blackbox_source_href,
)


def test_work_item_mutations_publish_canonical_snapshot_contracts() -> None:
    operations = main_app.openapi()["paths"]
    expected = {
        "/api/v1/platform/work-items/{item_id}/assign": "platform_work_item",
        "/api/v1/platform/work-items/{item_id}/snooze": "platform_work_item",
        "/api/v1/platform/work-items/{item_id}/commands/{command}": "work_item_source",
        "/api/v1/platform/work-items/reconcile": "platform_work_item_projection",
    }
    for path, adapter in expected.items():
        assert operations[path]["post"]["x-platform-mutation"]["snapshotAdapter"] == adapter


async def _work_item_client(db_session, platform_auth_context):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def override_db():
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    async def override_context():
        return platform_auth_context

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_auth_context] = override_context
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_workspace(db_session, *, suffix: str = "task") -> Workspace:
    workspace = Workspace(
        name=f"Conta {suffix}",
        slug=f"conta-{suffix}-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        onboarding_completed=False,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    return workspace


async def _seed_ticket(db_session, *, workspace: Workspace, actor: User) -> SupportTicket:
    now = datetime.now(UTC)
    ticket = SupportTicket(
        public_number=f"SUP-{uuid4().hex[:8].upper()}",
        workspace_id=workspace.id,
        reporter_user_id=actor.id,
        ticket_type=SupportTicketType.problem,
        priority=SupportTicketPriority.high,
        severity=SupportTicketSeverity.high,
        queue=SupportTicketQueue.technical,
        status=SupportTicketStatus.new,
        subject="Assunto privado do cliente",
        description="DOCUMENTO PRIVADO que jamais deve chegar à projeção.",
        technical_context={"secret": "também não projetar"},
        idempotency_key=uuid4().hex,
        version=1,
        first_response_due_at=now + timedelta(hours=1),
        resolution_due_at=now + timedelta(hours=8),
    )
    db_session.add(ticket)
    await db_session.flush()
    return ticket


@pytest.mark.asyncio
async def test_reconciliation_is_idempotent_safe_and_reports_available_sources(
    db_session,
    platform_auth_context,
):
    actor = await db_session.get(User, platform_auth_context.user_id)
    workspace = await _seed_workspace(db_session)
    ticket = await _seed_ticket(db_session, workspace=workspace, actor=actor)
    now = datetime.now(UTC)
    membership = WorkspaceMembership(
        workspace_id=workspace.id,
        user_id=actor.id,
        role=WorkspaceRole.operator,
        status=MembershipStatus.invited,
        is_default=False,
        permission_grants=[],
        permission_denials=[],
    )
    db_session.add(membership)
    await db_session.flush()
    db_session.add_all(
        [
            PlatformIncident(
                title="Falha operacional controlada",
                summary="Detalhe técnico que não deve ser projetado.",
                severity="SEV2",
                status="investigating",
                components=["api"],
                owner_user_id=None,
                created_by_user_id=actor.id,
                updates=[],
            ),
            WorkspaceSubscription(
                workspace_id=workspace.id,
                plano=WorkspacePlanCode.performance,
                status=WorkspaceSubscriptionStatus.inadimplente,
                valor_mensal_cents=250000,
                ciclo="mensal",
                metadata_json={"provider_secret": "never-project"},
            ),
            PlatformConfigDocument(
                namespace=f"blackbox.test.{uuid4().hex[:8]}",
                schema_version="1.0",
                current_version=1,
                etag=uuid4().hex,
                payload={"apiKey": "never-project"},
                updated_by_user_id=actor.id,
            ),
            SupportAccessGrant(
                workspace_id=workspace.id,
                requester_user_id=actor.id,
                reason="Diagnóstico assistido solicitado para teste seguro.",
                ticket_reference=ticket.public_number,
                requested_scopes=[SupportAccessScope.workspace_read.value],
                requested_duration_minutes=15,
                state=SupportAccessState.requested,
                requested_at=now,
                version=1,
            ),
            WorkspaceInvitation(
                workspace_id=workspace.id,
                membership_id=membership.id,
                user_id=actor.id,
                invited_by_user_id=actor.id,
                invited_email=actor.email,
                invited_name=actor.name,
                token_digest="a" * 64,
                code_digest="b" * 64,
                status=InvitationStatus.pending,
                expires_at=now + timedelta(days=2),
                last_sent_at=now,
                send_count=1,
            ),
            PlatformStaffInvitation(
                email=f"staff-task-{uuid4().hex[:8]}@example.com",
                name="Funcionário convidado",
                role_package="support",
                invited_by_user_id=actor.id,
                status="pending",
                expires_at=now + timedelta(days=2),
                last_sent_at=now,
                send_count=1,
                version=1,
            ),
            SuperAdminNotice(
                title="Comunicado operacional",
                body="Conteúdo editorial privado que não deve ir para a projeção.",
                status=SuperAdminNoticeStatus.draft,
                audience_kind=SuperAdminNoticeAudienceKind.global_,
                audience_definition={},
                channels=[SuperAdminNoticeChannel.in_app.value],
                requires_acknowledgement=False,
                created_by_user_id=actor.id,
                updated_by_user_id=actor.id,
                version=1,
            ),
        ]
    )
    await db_session.flush()

    service = PlatformWorkItemService(db_session)
    first = await service.reconcile(
        source_types=[
            "support_ticket",
            "incident",
            "support_access",
            "workspace_onboarding",
            "billing",
            "workspace_invitation",
            "staff_invitation",
            "publication",
            "configuration",
        ]
    )
    second = await service.reconcile(
        source_types=[
            "support_ticket",
            "incident",
            "support_access",
            "workspace_onboarding",
            "billing",
            "workspace_invitation",
            "staff_invitation",
            "publication",
            "configuration",
        ]
    )

    first_by_type = {row.source_type: row for row in first}
    second_by_type = {row.source_type: row for row in second}
    assert set(first_by_type) == {
        "support_ticket",
        "incident",
        "support_access",
        "workspace_onboarding",
        "billing",
        "workspace_invitation",
        "staff_invitation",
        "publication",
        "configuration",
    }
    assert all(row.status == "available" for row in first_by_type.values())
    assert all(row.projected == 0 for row in second_by_type.values()), second_by_type

    items = list((await db_session.scalars(select(PlatformWorkItem))).all())
    assert {item.source_type for item in items} == set(first_by_type)
    assert len({item.dedupe_key for item in items}) == len(items)
    ticket_item = next(item for item in items if item.source_id == ticket.id)
    serialized = repr(ticket_item.payload_safe) + ticket_item.safe_title + ticket_item.safe_summary
    assert "DOCUMENTO PRIVADO" not in serialized
    assert "Assunto privado" not in serialized
    assert "também não projetar" not in serialized
    config_item = next(item for item in items if item.source_type == "configuration")
    assert config_item.state == "open"
    assert config_item.payload_safe["projectionAvailability"] == "available"
    assert "apiKey" not in config_item.payload_safe

    expected_links = {
        "support_ticket": ("/blackbox/support-tickets", "tickets", "context"),
        "incident": ("/blackbox/governance/security", "incidents", "context"),
        "support_access": ("/blackbox/support-access", "requests", "context"),
        "workspace_onboarding": ("/blackbox/workspaces", "onboarding", "workspace"),
        "billing": ("/blackbox/billing", "subscriptions", "context"),
        "workspace_invitation": ("/blackbox/workspaces", "invitations", "context"),
        "staff_invitation": ("/blackbox/team", "invitations", "context"),
        "publication": ("/blackbox/announcements", "publications", "context"),
        "configuration": ("/blackbox/settings", "configuration", "context"),
    }
    for item in items:
        route, tab, focus = expected_links[item.source_type]
        parsed = urlsplit(item.href)
        query = parse_qs(parsed.query)
        assert parsed.path == route
        assert query["sourceType"] == [item.source_type]
        assert query["sourceId"] == [str(item.source_id)]
        assert query["tab"] == [tab]
        assert query["focus"] == [focus]
        if item.workspace_id is not None:
            assert query["workspaceId"] == [str(item.workspace_id)]
        if item.source_type == "configuration":
            assert query["namespace"] == [item.payload_safe["namespace"]]

    # A resposta não perpetua um href legado mesmo antes de uma reconciliação
    # corretiva da linha persistida.
    ticket_item.href = f"/blackbox/support/tickets/{ticket_item.source_id}"
    presented_ticket = service.present(actor, ticket_item)
    assert urlsplit(presented_ticket["href"]).path == "/blackbox/support-tickets"


@pytest.mark.asyncio
async def test_cursor_list_counts_and_typed_command_refuse_stale_or_unknown(
    db_session,
    platform_auth_context,
):
    actor = await db_session.get(User, platform_auth_context.user_id)
    workspace = await _seed_workspace(db_session, suffix="cursor")
    first_ticket = await _seed_ticket(db_session, workspace=workspace, actor=actor)
    second_ticket = await _seed_ticket(db_session, workspace=workspace, actor=actor)
    service = PlatformWorkItemService(db_session)
    await service.reconcile(source_types=["support_ticket"])

    async with await _work_item_client(db_session, platform_auth_context) as client:
        first_page = await client.get("/api/v1/platform/work-items?limit=1")
        assert first_page.status_code == 200, first_page.text
        assert len(first_page.json()["items"]) == 1
        assert first_page.json()["nextCursor"]
        second_page = await client.get(
            "/api/v1/platform/work-items",
            params={"limit": 1, "cursor": first_page.json()["nextCursor"]},
        )
        assert second_page.status_code == 200, second_page.text
        assert second_page.json()["items"][0]["id"] != first_page.json()["items"][0]["id"]

        counts = await client.get("/api/v1/platform/work-items/counts")
        assert counts.status_code == 200
        assert counts.json()["bySource"]["support_ticket"] == 2

        searched = await client.get(
            "/api/v1/platform/work-items",
            params={"search": first_ticket.public_number},
        )
        assert searched.status_code == 200
        assert [row["sourceId"] for row in searched.json()["items"]] == [str(first_ticket.id)]
        private_search = await client.get(
            "/api/v1/platform/work-items",
            params={"search": "Assunto privado do cliente"},
        )
        assert private_search.status_code == 200
        assert private_search.json()["items"] == []
        unassigned = await client.get(
            "/api/v1/platform/work-items",
            params={"unassigned": "true"},
        )
        assert unassigned.status_code == 200
        assert len(unassigned.json()["items"]) == 2

        item = next(
            row
            for row in first_page.json()["items"] + second_page.json()["items"]
            if row["sourceId"] in {str(first_ticket.id), str(second_ticket.id)}
        )
        unknown = await client.post(
            f"/api/v1/platform/work-items/{item['id']}/commands/delete_everything",
            json={
                "expectedSourceVersion": item["sourceVersion"],
                "reason": "Comando de teste inválido",
            },
        )
        assert unknown.status_code == 422
        assert unknown.json()["detail"]["code"] == "WORK-ITEM-INVALID"

        stale = await client.post(
            f"/api/v1/platform/work-items/{item['id']}/commands/assign_to_me",
            json={"expectedSourceVersion": "stale-version", "reason": "Teste de concorrência"},
        )
        assert stale.status_code == 409

        assigned = await client.post(
            f"/api/v1/platform/work-items/{item['id']}/commands/assign_to_me",
            json={
                "expectedSourceVersion": item["sourceVersion"],
                "reason": "Assumir atendimento técnico",
            },
        )
        assert assigned.status_code == 200, assigned.text
        assert assigned.json()["sourceVersion"] == "2"
        assert assigned.json()["workItem"]["assignedUserId"] == str(actor.id)
        assigned_filter = await client.get(
            "/api/v1/platform/work-items",
            params={"assignedToUserId": str(actor.id)},
        )
        assert assigned_filter.status_code == 200
        assert [row["id"] for row in assigned_filter.json()["items"]] == [item["id"]]


@pytest.mark.asyncio
async def test_assignment_rejects_staff_without_source_permission_and_snooze_guardrails(
    db_session,
    platform_auth_context,
):
    actor = await db_session.get(User, platform_auth_context.user_id)
    workspace = await _seed_workspace(db_session, suffix="guards")
    await _seed_ticket(db_session, workspace=workspace, actor=actor)
    await PlatformWorkItemService(db_session).reconcile(source_types=["support_ticket"])
    item = await db_session.scalar(select(PlatformWorkItem))
    item_id = item.id
    projection_version = item.projection_version

    unauthorized = User(
        name="Marketing sem suporte",
        email=f"marketing-{uuid4().hex[:8]}@example.com",
        role=actor.role,
        agent_type=actor.agent_type,
        initials="MS",
        color="#111827",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=False,
        platform_access_active=True,
        platform_role_package="marketing_content",
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(unauthorized)
    await db_session.commit()

    async with await _work_item_client(db_session, platform_auth_context) as client:
        denied_assignment = await client.post(
            f"/api/v1/platform/work-items/{item_id}/assign",
            json={
                "assignedUserId": str(unauthorized.id),
                "expectedProjectionVersion": projection_version,
            },
        )
        assert denied_assignment.status_code == 422

        denied_snooze = await client.post(
            f"/api/v1/platform/work-items/{item_id}/snooze",
            json={
                "snoozedUntil": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                "reason": "Aguardar janela operacional",
                "expectedProjectionVersion": projection_version,
            },
        )
        assert denied_snooze.status_code == 422


@pytest.mark.asyncio
async def test_work_item_routes_require_task_permissions_in_addition_to_source_visibility(
    db_session,
    platform_auth_context,
):
    actor = await db_session.get(User, platform_auth_context.user_id)
    workspace = await _seed_workspace(db_session, suffix="task-rbac")
    ticket = await _seed_ticket(db_session, workspace=workspace, actor=actor)
    ticket.priority = SupportTicketPriority.low
    ticket.severity = SupportTicketSeverity.low
    service = PlatformWorkItemService(db_session)
    await service.reconcile(source_types=["support_ticket"])
    item = await db_session.scalar(
        select(PlatformWorkItem).where(PlatformWorkItem.source_id == ticket.id)
    )
    assert item is not None

    actor.is_platform_admin = False
    actor.platform_role_package = "support"
    actor.platform_permission_grants = []
    actor.platform_permission_denials = [
        {"module": "platform_tasks", "action": "view"},
    ]
    await db_session.commit()

    async with await _work_item_client(db_session, platform_auth_context) as client:
        for path in (
            "/api/v1/platform/work-items",
            "/api/v1/platform/work-items/counts",
            f"/api/v1/platform/work-items/{item.id}",
        ):
            denied_view = await client.get(path)
            assert denied_view.status_code == 403
            assert denied_view.json()["detail"]["module"] == "platform_tasks"
            assert denied_view.json()["detail"]["action"] == "view"

    with pytest.raises(PlatformWorkItemForbiddenError):
        await service.get(actor=actor, item_id=item.id)

    actor.platform_permission_denials = [
        {"module": "platform_tasks", "action": "edit"},
    ]
    await db_session.commit()

    async with await _work_item_client(db_session, platform_auth_context) as client:
        visible = await client.get("/api/v1/platform/work-items")
        assert visible.status_code == 200, visible.text
        assert [row["id"] for row in visible.json()["items"]] == [str(item.id)]

        denied_assignment = await client.post(
            f"/api/v1/platform/work-items/{item.id}/assign",
            json={
                "assignedUserId": str(actor.id),
                "expectedProjectionVersion": item.projection_version,
            },
        )
        assert denied_assignment.status_code == 403
        assert denied_assignment.json()["detail"] == {
            "code": "PLATFORM-PERMISSION-DENIED",
            "message": "Seu pacote de acesso não autoriza esta operação.",
            "module": "platform_tasks",
            "action": "assign",
        }

        denied_snooze = await client.post(
            f"/api/v1/platform/work-items/{item.id}/snooze",
            json={
                "snoozedUntil": (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
                "reason": "Aguardar a janela operacional autorizada.",
                "expectedProjectionVersion": item.projection_version,
            },
        )
        assert denied_snooze.status_code == 403
        assert denied_snooze.json()["detail"] == {
            "code": "PLATFORM-PERMISSION-DENIED",
            "message": "Seu pacote de acesso não autoriza esta operação.",
            "module": "platform_tasks",
            "action": "edit",
        }

    with pytest.raises(PlatformWorkItemForbiddenError):
        await service.assign(
            actor=actor,
            item_id=item.id,
            assigned_user_id=actor.id,
            expected_projection_version=item.projection_version,
        )
    with pytest.raises(PlatformWorkItemForbiddenError):
        await service.snooze(
            actor=actor,
            item_id=item.id,
            snoozed_until=datetime.now(UTC) + timedelta(hours=4),
            expected_projection_version=item.projection_version,
        )


@pytest.mark.asyncio
async def test_source_link_falls_back_when_actor_cannot_open_destination_route(
    db_session,
    platform_auth_context,
) -> None:
    actor = await db_session.get(User, platform_auth_context.user_id)
    source_id = uuid4()

    root_link = urlsplit(
        blackbox_source_href(
            "workspace_onboarding",
            source_id,
            workspace_id=source_id,
            actor=actor,
        )
    )
    assert root_link.path == "/blackbox/workspaces"

    actor.is_platform_admin = False
    actor.platform_role_package = "onboarding"
    actor.platform_permission_denials = [
        {"module": "customers", "action": "view"},
    ]
    onboarding_fallback = urlsplit(
        blackbox_source_href(
            "workspace_onboarding",
            source_id,
            workspace_id=source_id,
            actor=actor,
        )
    )
    assert onboarding_fallback.path == "/blackbox/tasks"
    assert parse_qs(onboarding_fallback.query)["sourceType"] == ["workspace_onboarding"]

    actor.platform_role_package = "finance_billing"
    actor.platform_permission_denials = [
        {"module": "finance", "action": "view"},
    ]
    billing_fallback = urlsplit(
        blackbox_source_href(
            "billing",
            source_id,
            workspace_id=source_id,
            actor=actor,
        )
    )
    assert billing_fallback.path == "/blackbox/tasks"
    assert parse_qs(billing_fallback.query)["sourceType"] == ["billing"]

    actor.is_platform_admin = True
    commercial_link = urlsplit(
        blackbox_source_href(
            "commercial_request",
            source_id,
            workspace_id=source_id,
            actor=actor,
        )
    )
    assert commercial_link.path == "/blackbox/workspaces"
    assert parse_qs(commercial_link.query)["tab"] == ["commercial"]
    assert parse_qs(commercial_link.query)["focus"] == ["workspace"]


@pytest.mark.asyncio
async def test_assignment_snooze_and_command_preserve_canonical_audit(
    db_session,
    platform_auth_context,
    monkeypatch,
):
    actor = await db_session.get(User, platform_auth_context.user_id)
    workspace = await _seed_workspace(db_session, suffix="audit")
    ticket = await _seed_ticket(db_session, workspace=workspace, actor=actor)
    ticket.priority = SupportTicketPriority.low
    ticket.severity = SupportTicketSeverity.low
    await PlatformWorkItemService(db_session).reconcile(source_types=["support_ticket"])
    item = await db_session.scalar(
        select(PlatformWorkItem).where(PlatformWorkItem.source_id == ticket.id)
    )
    assert item is not None
    item_id = item.id
    ticket_id = ticket.id
    actor_id = actor.id

    async with await _work_item_client(db_session, platform_auth_context) as client:
        assigned = await client.post(
            f"/api/v1/platform/work-items/{item_id}/assign",
            headers={"X-Correlation-ID": "work-item-assign-audit"},
            json={
                "assignedUserId": str(actor_id),
                "expectedProjectionVersion": item.projection_version,
            },
        )
        assert assigned.status_code == 200, assigned.text
        snoozed = await client.post(
            f"/api/v1/platform/work-items/{item_id}/snooze",
            json={
                "snoozedUntil": (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
                "reason": "Aguardar janela de manutenção aprovada.",
                "expectedProjectionVersion": assigned.json()["projectionVersion"],
            },
        )
        assert snoozed.status_code == 200, snoozed.text
        commanded = await client.post(
            f"/api/v1/platform/work-items/{item_id}/commands/assign_to_me",
            headers={"X-Correlation-ID": "work-item-command-audit"},
            json={
                "expectedSourceVersion": snoozed.json()["sourceVersion"],
                "reason": "Assumir atendimento após revisão da fila.",
            },
        )
        assert commanded.status_code == 200, commanded.text

    events = list(
        await db_session.scalars(
            select(PlatformAuditEvent)
            .where(PlatformAuditEvent.resource_id.in_([str(item_id), str(ticket_id)]))
            .order_by(PlatformAuditEvent.occurred_at, PlatformAuditEvent.id)
        )
    )
    by_action = {event.action: event for event in events}
    assert by_action["work_item.assign"].before["assignedUserId"] is None
    assert by_action["work_item.assign"].after["assignedUserId"] == str(actor_id)
    assert by_action["work_item.snooze"].after["snoozedUntil"] is not None
    assert by_action["work_item.assign"].correlation_id == "work-item-assign-audit"
    assert by_action["work_item.assign_to_me"].correlation_id == "work-item-command-audit"
    assert by_action["work_item.assign_to_me"].route == (
        "/api/v1/platform/work-items/{item_id}/commands/{command}"
    )

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(PlatformAuditEventService, "record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        async with await _work_item_client(db_session, platform_auth_context) as client:
            await client.post(
                f"/api/v1/platform/work-items/{item_id}/assign",
                json={
                    "assignedUserId": None,
                    "expectedProjectionVersion": commanded.json()["workItem"]["projectionVersion"],
                },
            )
    db_session.expire_all()
    persisted = await db_session.get(PlatformWorkItem, item_id)
    assert persisted is not None
    assert persisted.assigned_user_id == actor_id
