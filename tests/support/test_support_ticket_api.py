from __future__ import annotations

import hashlib
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.models.error_log import ErrorLog
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.platform_incident import PlatformIncident
from app.models.storage_object import StorageObject, StorageObjectState
from app.models.support_access import SupportAccessGrant
from app.models.support_ticket import (
    SupportMessageVisibility,
    SupportTicket,
    SupportTicketAttachment,
    SupportTicketEvent,
    SupportTicketMessage,
)
from app.models.workspace_membership import WorkspaceRole
from app.schemas.storage_object import StorageUploadRequest
from app.schemas.support_ticket import SupportMessageCreate
from app.services.support_ticket_service import SupportTicketService


async def _create_ticket(api_client, payload, *, key: str = "ticket-create-1"):
    response = await api_client.post(
        "/api/v1/support/tickets",
        headers={"Idempotency-Key": key},
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_ticket_and_message_creation_are_idempotent(
    client,
    db_session,
    support_ticket_payload,
):
    first = await _create_ticket(client, support_ticket_payload(), key="same-ticket")
    replay = await client.post(
        "/api/v1/support/tickets",
        headers={"Idempotency-Key": "same-ticket"},
        json=support_ticket_payload(subject="Payload replay must not replace the original"),
    )

    assert replay.status_code == 200
    assert replay.json()["id"] == first["id"]
    assert replay.json()["subject"] == first["subject"]
    assert await db_session.scalar(select(func.count(SupportTicket.id))) == 1

    message_url = f"/api/v1/support/tickets/{first['id']}/messages"
    created = await client.post(
        message_url,
        headers={"Idempotency-Key": "same-message"},
        json={"body": "Primeira mensagem publica.", "visibility": "public"},
    )
    message_replay = await client.post(
        message_url,
        headers={"Idempotency-Key": "same-message"},
        json={"body": "Este replay nao pode sobrescrever a mensagem.", "visibility": "public"},
    )

    assert created.status_code == 201
    assert message_replay.status_code == 200
    assert message_replay.json()["id"] == created.json()["id"]
    assert message_replay.json()["body"] == "Primeira mensagem publica."
    assert await db_session.scalar(select(func.count(SupportTicketMessage.id))) == 1


@pytest.mark.asyncio
async def test_ticket_and_message_reject_empty_idempotency_keys(
    client,
    support_ticket_payload,
):
    invalid_ticket = await client.post(
        "/api/v1/support/tickets",
        headers={"Idempotency-Key": ""},
        json=support_ticket_payload(),
    )
    assert invalid_ticket.status_code == 422

    ticket = await _create_ticket(
        client,
        support_ticket_payload(),
        key="valid-ticket-key",
    )
    invalid_message = await client.post(
        f"/api/v1/support/tickets/{ticket['id']}/messages",
        headers={"Idempotency-Key": ""},
        json={"body": "Mensagem sem chave idempotente valida."},
    )
    assert invalid_message.status_code == 422


@pytest.mark.asyncio
async def test_technical_context_is_strict_allowlisted_and_sanitized(
    client,
    db_session,
    support_ticket_payload,
):
    rejected = support_ticket_payload()
    rejected["technicalContext"]["authorization"] = "Bearer secret"
    strict_response = await client.post(
        "/api/v1/support/tickets",
        headers={"Idempotency-Key": "strict-context"},
        json=rejected,
    )
    assert strict_response.status_code == 422

    accepted = support_ticket_payload()
    accepted["technicalContext"]["failedRequestIds"] = [
        " req:valid-1 ",
        "contains/slash",
        "token=secret",
        "valid.identifier-2",
    ]
    created = await _create_ticket(client, accepted, key="sanitized-context")
    stored = await db_session.get(SupportTicket, UUID(created["id"]))

    assert stored is not None
    assert set(stored.technical_context) == {
        "page",
        "action",
        "frontendVersion",
        "browser",
        "device",
        "viewport",
        "realtimeStatus",
        "failedRequestIds",
    }
    assert stored.technical_context["failedRequestIds"] == [
        "req:valid-1",
        "valid.identifier-2",
    ]
    assert SupportTicketService.sanitize_context(
        {
            "page": "/app",
            "cookie": "session=secret",
            "authorization": "Bearer secret",
            "stack": "sensitive internals",
            "failedRequestIds": ["ok:1", "../../etc/passwd"],
        }
    ) == {"page": "/app", "failedRequestIds": ["ok:1"]}


@pytest.mark.asyncio
async def test_valid_occurred_at_is_persisted_as_sanitized_context(
    client,
    support_ticket_payload,
):
    payload = support_ticket_payload()
    payload["technicalContext"]["occurredAt"] = "2026-07-24T12:00:00Z"

    response = await client.post(
        "/api/v1/support/tickets",
        headers={"Idempotency-Key": "context-with-datetime"},
        json=payload,
    )

    assert response.status_code == 201, response.text
    assert response.json()["technicalContext"]["occurredAt"] == "2026-07-24T12:00:00Z"


@pytest.mark.asyncio
async def test_workspace_idor_and_same_workspace_access_rules(
    support_factory,
    support_ticket_payload,
):
    workspace_a = await support_factory.workspace(name="Conta A")
    workspace_b = await support_factory.workspace(name="Conta B")
    owner_a, owner_a_membership = await support_factory.user(
        workspace=workspace_a,
        name="Owner A",
        role=WorkspaceRole.workspace_owner,
    )
    owner_b, owner_b_membership = await support_factory.user(
        workspace=workspace_b,
        name="Owner B",
        role=WorkspaceRole.workspace_owner,
    )
    stranger, stranger_membership = await support_factory.user(
        workspace=workspace_a,
        name="Operador sem convite",
        role=WorkspaceRole.operator,
    )
    admin, admin_membership = await support_factory.user(
        workspace=workspace_a,
        name="Admin A",
        role=WorkspaceRole.workspace_admin,
    )

    context_a = support_factory.context(
        owner_a,
        workspace=workspace_a,
        membership=owner_a_membership,
    )
    async with support_factory.client(context_a) as api:
        ticket = await _create_ticket(api, support_ticket_payload(), key="idor-ticket")

    context_b = support_factory.context(
        owner_b,
        workspace=workspace_b,
        membership=owner_b_membership,
    )
    async with support_factory.client(context_b) as api:
        hidden = await api.get(f"/api/v1/support/tickets/{ticket['id']}")
        hidden_message = await api.post(
            f"/api/v1/support/tickets/{ticket['id']}/messages",
            headers={"Idempotency-Key": "idor-message"},
            json={"body": "Tentativa entre contas."},
        )
        listed = await api.get("/api/v1/support/tickets")
    assert hidden.status_code == 404
    assert hidden_message.status_code == 404
    assert listed.json()["items"] == []

    stranger_context = support_factory.context(
        stranger,
        workspace=workspace_a,
        membership=stranger_membership,
    )
    async with support_factory.client(stranger_context) as api:
        denied = await api.get(f"/api/v1/support/tickets/{ticket['id']}")
    assert denied.status_code == 403

    admin_context = support_factory.context(
        admin,
        workspace=workspace_a,
        membership=admin_membership,
    )
    async with support_factory.client(admin_context) as api:
        allowed = await api.get(f"/api/v1/support/tickets/{ticket['id']}")
    assert allowed.status_code == 200

    async with support_factory.client(context_a) as api:
        cross_workspace_collaborator = await api.post(
            f"/api/v1/support/tickets/{ticket['id']}/collaborators",
            json={"userId": str(owner_b.id)},
        )
        collaborator = await api.post(
            f"/api/v1/support/tickets/{ticket['id']}/collaborators",
            json={"userId": str(stranger.id)},
        )
    assert cross_workspace_collaborator.status_code == 422
    assert collaborator.status_code == 200

    async with support_factory.client(stranger_context) as api:
        invited = await api.get(f"/api/v1/support/tickets/{ticket['id']}")
    assert invited.status_code == 200


@pytest.mark.asyncio
async def test_internal_notes_are_visible_only_to_platform_staff(
    support_factory,
    support_ticket_payload,
):
    workspace = await support_factory.workspace()
    reporter, membership = await support_factory.user(
        workspace=workspace,
        role=WorkspaceRole.workspace_owner,
    )
    platform_user, _ = await support_factory.user(is_platform_admin=True, name="Platform Root")
    reporter_context = support_factory.context(
        reporter,
        workspace=workspace,
        membership=membership,
    )
    platform_context = support_factory.context(platform_user)

    async with support_factory.client(reporter_context) as api:
        ticket = await _create_ticket(api, support_ticket_payload(), key="note-visibility")

    async with support_factory.client(platform_context) as api:
        internal = await api.post(
            f"/api/v1/platform/support/tickets/{ticket['id']}/messages",
            headers={"Idempotency-Key": "internal-note"},
            json={"body": "Diagnostico interno sensivel.", "visibility": "internal"},
        )
        public = await api.post(
            f"/api/v1/platform/support/tickets/{ticket['id']}/messages",
            headers={"Idempotency-Key": "public-answer"},
            json={"body": "Resposta segura ao cliente.", "visibility": "public"},
        )
        platform_detail = await api.get(
            f"/api/v1/platform/support/tickets/{ticket['id']}"
        )

    assert internal.status_code == 201
    assert public.status_code == 201
    assert platform_detail.status_code == 200
    assert {item["body"] for item in platform_detail.json()["messages"]} == {
        "Diagnostico interno sensivel.",
        "Resposta segura ao cliente.",
    }
    assert "internal_note_added" in {
        item["eventType"] for item in platform_detail.json()["events"]
    }

    async with support_factory.client(reporter_context) as api:
        forbidden_note = await api.post(
            f"/api/v1/support/tickets/{ticket['id']}/messages",
            headers={"Idempotency-Key": "customer-internal-note"},
            json={"body": "Tentativa de nota interna.", "visibility": "internal"},
        )
        customer_detail = await api.get(f"/api/v1/support/tickets/{ticket['id']}")

    assert forbidden_note.status_code == 403
    assert [item["body"] for item in customer_detail.json()["messages"]] == [
        "Resposta segura ao cliente."
    ]
    assert "internal_note_added" not in {
        item["eventType"] for item in customer_detail.json()["events"]
    }


@pytest.mark.asyncio
async def test_platform_staff_can_open_an_audited_ticket_for_an_active_account_user(
    support_factory,
    support_ticket_payload,
):
    workspace = await support_factory.workspace(name="Conta assistida")
    reporter, _ = await support_factory.user(
        workspace=workspace,
        role=WorkspaceRole.workspace_owner,
        name="Cliente solicitante",
    )
    foreign_workspace = await support_factory.workspace(name="Outra conta")
    foreign_user, _ = await support_factory.user(
        workspace=foreign_workspace,
        role=WorkspaceRole.workspace_owner,
    )
    support_agent, _ = await support_factory.user(
        name="Agente de suporte",
        platform_role_package="support",
    )
    payload = support_ticket_payload(subject="Chamado iniciado pelo atendimento")
    payload.update({
        "workspaceId": str(workspace.id),
        "reporterUserId": str(reporter.id),
        "queue": "technical",
    })

    async with support_factory.client(support_factory.context(support_agent)) as api:
        created = await api.post(
            "/api/v1/platform/support/tickets",
            headers={
                "Idempotency-Key": "platform-created-ticket",
                "X-Operation-Reason": "Atendimento solicitado pelo cliente.",
            },
            json=payload,
        )
        replay = await api.post(
            "/api/v1/platform/support/tickets",
            headers={
                "Idempotency-Key": "platform-created-ticket",
                "X-Operation-Reason": "Atendimento solicitado pelo cliente.",
            },
            json=payload,
        )
        invalid = await api.post(
            "/api/v1/platform/support/tickets",
            headers={
                "Idempotency-Key": "platform-invalid-reporter",
                "X-Operation-Reason": "Validar solicitante de outra conta.",
            },
            json={**payload, "reporterUserId": str(foreign_user.id)},
        )
        detail = await api.get(
            f"/api/v1/platform/support/tickets/{created.json()['id']}"
        )

    assert created.status_code == 201, created.text
    assert replay.status_code == 200
    assert replay.json()["id"] == created.json()["id"]
    assert created.json()["workspaceId"] == str(workspace.id)
    assert created.json()["workspaceName"] == workspace.name
    assert created.json()["reporterUserId"] == str(reporter.id)
    assert created.json()["queue"] == "technical"
    assert invalid.status_code == 422
    creation_event = next(
        event for event in detail.json()["events"] if event["eventType"] == "created"
    )
    assert creation_event["actorUserId"] == str(support_agent.id)
    audit_events = list(
        (
            await support_factory.db.scalars(
                select(PlatformAuditEvent)
                .where(
                    PlatformAuditEvent.action == "create_platform_support_ticket",
                    PlatformAuditEvent.resource_id == created.json()["id"],
                )
                .order_by(PlatformAuditEvent.occurred_at, PlatformAuditEvent.id)
            )
        ).all()
    )
    assert len(audit_events) == 2
    assert any(event.before is None and event.after for event in audit_events)
    assert any(event.before == event.after and event.changed_paths == [] for event in audit_events)
    assert all(event.reason_source == "user" for event in audit_events)
    assert creation_event["afterData"]["openedByPlatform"] is True


@pytest.mark.asyncio
async def test_queue_routing_critical_auto_assignment_and_filters(
    support_factory,
    support_ticket_payload,
):
    workspace = await support_factory.workspace()
    reporter, membership = await support_factory.user(
        workspace=workspace,
        role=WorkspaceRole.workspace_owner,
    )
    technical, _ = await support_factory.user(
        name="Technical Support",
        platform_role_package="technical_support",
    )
    regular_user, _ = await support_factory.user(
        workspace=workspace,
        name="Regular User",
    )
    platform_root, _ = await support_factory.user(
        is_platform_admin=True,
        name="Platform Root",
    )
    reporter_context = support_factory.context(
        reporter,
        workspace=workspace,
        membership=membership,
    )

    async with support_factory.client(reporter_context) as api:
        critical = await _create_ticket(
            api,
            support_ticket_payload(
                ticketType="instability",
                severity="critical",
                priority="urgent",
            ),
            key="critical-ticket",
        )
        suggestion = await _create_ticket(
            api,
            support_ticket_payload(
                ticketType="suggestion",
                subject="Sugestao para filtros da fila",
            ),
            key="suggestion-ticket",
        )

    assert critical["queue"] == "technical"
    assert critical["assignedToUserId"] == str(technical.id)
    assert suggestion["queue"] == "support"
    assert suggestion["assignedToUserId"] is None

    platform_context = support_factory.context(platform_root)
    async with support_factory.client(platform_context) as api:
        invalid_assignee = await api.patch(
            f"/api/v1/platform/support/tickets/{critical['id']}",
            json={
                "expectedVersion": critical["version"],
                "assignedToUserId": str(regular_user.id),
            },
        )
        filtered = await api.get(
            "/api/v1/platform/support/tickets",
            params={"queues": "technical", "assignedToUserId": str(technical.id)},
        )

    assert invalid_assignee.status_code == 422
    assert [item["id"] for item in filtered.json()["items"]] == [critical["id"]]


@pytest.mark.asyncio
async def test_platform_status_update_returns_refreshed_resource(
    support_factory,
    support_ticket_payload,
):
    workspace = await support_factory.workspace()
    reporter, membership = await support_factory.user(
        workspace=workspace,
        role=WorkspaceRole.workspace_owner,
    )
    platform_root, _ = await support_factory.user(
        is_platform_admin=True,
        name="Platform Root",
    )
    reporter_context = support_factory.context(
        reporter,
        workspace=workspace,
        membership=membership,
    )
    async with support_factory.client(reporter_context) as api:
        ticket = await _create_ticket(
            api,
            support_ticket_payload(),
            key="optimistic-ticket",
        )

    async with support_factory.client(support_factory.context(platform_root)) as api:
        triaged = await api.patch(
            f"/api/v1/platform/support/tickets/{ticket['id']}",
            json={
                "expectedVersion": ticket["version"],
                "status": "triage",
            },
        )

    assert triaged.status_code == 200, triaged.text
    assert triaged.json()["version"] == ticket["version"] + 1


@pytest.mark.asyncio
async def test_assignment_to_active_platform_agent_is_persisted_and_audited(
    support_factory,
    support_ticket_payload,
):
    workspace = await support_factory.workspace()
    reporter, membership = await support_factory.user(
        workspace=workspace,
        role=WorkspaceRole.workspace_owner,
    )
    support_agent, _ = await support_factory.user(
        name="Support Agent",
        platform_role_package="support",
    )
    platform_root, _ = await support_factory.user(
        is_platform_admin=True,
        name="Platform Root",
    )
    reporter_context = support_factory.context(
        reporter,
        workspace=workspace,
        membership=membership,
    )
    async with support_factory.client(reporter_context) as api:
        ticket = await _create_ticket(
            api,
            support_ticket_payload(),
            key="manual-assignment-ticket",
        )

    async with support_factory.client(support_factory.context(platform_root)) as api:
        assigned = await api.patch(
            f"/api/v1/platform/support/tickets/{ticket['id']}",
            json={
                "expectedVersion": ticket["version"],
                "assignedToUserId": str(support_agent.id),
            },
        )
        detail = await api.get(
            f"/api/v1/platform/support/tickets/{ticket['id']}"
        )

    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["assignedToUserId"] == str(support_agent.id)
    assignment_events = [
        item
        for item in detail.json()["events"]
        if item["eventType"] == "assignment_changed"
    ]
    assert assignment_events[-1]["afterData"]["assigned_to_user_id"] == str(
        support_agent.id
    )


@pytest.mark.asyncio
async def test_assignment_requires_assign_permission_and_support_access(
    support_factory,
    support_ticket_payload,
):
    workspace = await support_factory.workspace()
    reporter, membership = await support_factory.user(
        workspace=workspace,
        role=WorkspaceRole.workspace_owner,
    )
    triager, _ = await support_factory.user(
        name="Triager without assignment",
        platform_role_package="support",
    )
    triager.platform_permission_denials = [
        {"module": "support", "action": "assign"}
    ]
    valid_assignee, _ = await support_factory.user(
        name="Support Assignee",
        platform_role_package="support",
    )
    invalid_assignee, _ = await support_factory.user(
        name="Finance Assignee",
        platform_role_package="finance_billing",
    )
    reporter_context = support_factory.context(
        reporter,
        workspace=workspace,
        membership=membership,
    )
    async with support_factory.client(reporter_context) as api:
        ticket = await _create_ticket(
            api,
            support_ticket_payload(),
            key="assignment-hardening-ticket",
        )

    async with support_factory.client(support_factory.context(triager)) as api:
        denied = await api.patch(
            f"/api/v1/platform/support/tickets/{ticket['id']}",
            json={
                "expectedVersion": ticket["version"],
                "assignedToUserId": str(valid_assignee.id),
            },
        )

    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "SUPPORT-ACCESS-DENIED"

    platform_root, _ = await support_factory.user(
        is_platform_admin=True,
        name="Platform Root",
    )
    async with support_factory.client(support_factory.context(platform_root)) as api:
        invalid_target = await api.patch(
            f"/api/v1/platform/support/tickets/{ticket['id']}",
            json={
                "expectedVersion": ticket["version"],
                "assignedToUserId": str(invalid_assignee.id),
            },
        )

    assert invalid_target.status_code == 422
    assert invalid_target.json()["detail"]["code"] == "SUPPORT-INVALID"


@pytest.mark.asyncio
async def test_evidence_links_validate_persisted_sources_and_can_be_cleared(
    support_factory,
    support_ticket_payload,
):
    workspace = await support_factory.workspace()
    other_workspace = await support_factory.workspace(name="Outra conta SAC")
    reporter, membership = await support_factory.user(
        workspace=workspace,
        role=WorkspaceRole.workspace_owner,
    )
    platform_root, _ = await support_factory.user(
        is_platform_admin=True,
        name="Platform Root",
    )
    valid_incident = PlatformIncident(
        title="Indisponibilidade parcial do SAC",
        summary="Incidente persistido e verificável para correlação com tickets.",
        severity="major",
        created_by_user_id=platform_root.id,
    )
    valid_error = ErrorLog(
        error_code="ERR-SAC-VALID",
        error_hash="a" * 64,
        exception_type="SupportEvidenceError",
        message="Falha vinculável ao chamado.",
        workspace_id=workspace.id,
    )
    foreign_error = ErrorLog(
        error_code="ERR-SAC-FOREIGN",
        error_hash="b" * 64,
        exception_type="ForeignSupportEvidenceError",
        message="Falha de outro workspace.",
        workspace_id=other_workspace.id,
    )
    valid_access = SupportAccessGrant(
        workspace_id=workspace.id,
        requester_user_id=platform_root.id,
        reason="Diagnóstico autorizado para o chamado do workspace.",
        ticket_reference="SAC-EVIDENCE-VALID",
        requested_scopes=["workspace:read"],
        requested_duration_minutes=15,
    )
    foreign_access = SupportAccessGrant(
        workspace_id=other_workspace.id,
        requester_user_id=platform_root.id,
        reason="Diagnóstico autorizado para outro workspace.",
        ticket_reference="SAC-EVIDENCE-FOREIGN",
        requested_scopes=["workspace:read"],
        requested_duration_minutes=15,
    )
    support_factory.db.add_all(
        (valid_incident, valid_error, foreign_error, valid_access, foreign_access)
    )
    await support_factory.db.flush()
    reporter_context = support_factory.context(
        reporter,
        workspace=workspace,
        membership=membership,
    )
    async with support_factory.client(reporter_context) as api:
        ticket = await _create_ticket(
            api,
            support_ticket_payload(),
            key="evidence-hardening-ticket",
        )
    stored_ticket = await support_factory.db.get(SupportTicket, UUID(ticket["id"]))
    legacy_release_id = uuid4()
    stored_ticket.linked_incident_id = uuid4()
    stored_ticket.linked_release_id = legacy_release_id
    await support_factory.db.flush()

    async with support_factory.client(support_factory.context(platform_root)) as api:
        linked = await api.patch(
            f"/api/v1/platform/support/tickets/{ticket['id']}",
            json={
                "expectedVersion": ticket["version"],
                "linkedErrorLogId": str(valid_error.id),
                "supportAccessRequestId": str(valid_access.id),
            },
        )
        foreign = await api.patch(
            f"/api/v1/platform/support/tickets/{ticket['id']}",
            json={
                "expectedVersion": linked.json()["version"],
                "linkedErrorLogId": str(foreign_error.id),
            },
        )
        foreign_access_link = await api.patch(
            f"/api/v1/platform/support/tickets/{ticket['id']}",
            json={
                "expectedVersion": linked.json()["version"],
                "supportAccessRequestId": str(foreign_access.id),
            },
        )
        unverified_incident = await api.patch(
            f"/api/v1/platform/support/tickets/{ticket['id']}",
            json={
                "expectedVersion": linked.json()["version"],
                "linkedIncidentId": str(uuid4()),
            },
        )
        verified_incident = await api.patch(
            f"/api/v1/platform/support/tickets/{ticket['id']}",
            json={
                "expectedVersion": linked.json()["version"],
                "linkedIncidentId": str(valid_incident.id),
            },
        )
        unverified_release = await api.patch(
            f"/api/v1/platform/support/tickets/{ticket['id']}",
            json={
                "expectedVersion": verified_incident.json()["version"],
                "linkedReleaseId": str(uuid4()),
            },
        )
        release_clear_denied = await api.patch(
            f"/api/v1/platform/support/tickets/{ticket['id']}",
            json={
                "expectedVersion": verified_incident.json()["version"],
                "linkedReleaseId": None,
            },
        )
        cleared = await api.patch(
            f"/api/v1/platform/support/tickets/{ticket['id']}",
            json={
                "expectedVersion": verified_incident.json()["version"],
                "linkedErrorLogId": None,
                "linkedIncidentId": None,
                "supportAccessRequestId": None,
            },
        )

    assert linked.status_code == 200, linked.text
    assert linked.json()["linkedErrorLogId"] == str(valid_error.id)
    assert linked.json()["supportAccessRequestId"] == str(valid_access.id)
    assert foreign.status_code == 422
    assert foreign_access_link.status_code == 422
    assert unverified_incident.status_code == 422
    assert verified_incident.status_code == 200, verified_incident.text
    assert verified_incident.json()["linkedIncidentId"] == str(valid_incident.id)
    assert unverified_release.status_code == 422
    assert release_clear_denied.status_code == 422
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["linkedErrorLogId"] is None
    assert cleared.json()["linkedIncidentId"] is None
    assert cleared.json()["linkedReleaseId"] == str(legacy_release_id)
    assert cleared.json()["supportAccessRequestId"] is None


@pytest.mark.asyncio
async def test_platform_permission_packages_enforce_view_respond_and_manage(
    support_factory,
    support_ticket_payload,
):
    workspace = await support_factory.workspace()
    reporter, membership = await support_factory.user(
        workspace=workspace,
        role=WorkspaceRole.workspace_owner,
    )
    view_only, _ = await support_factory.user(
        name="Product Operations",
        platform_role_package="product_operations",
    )
    support_agent, _ = await support_factory.user(
        name="Support Agent",
        platform_role_package="support",
    )
    reporter_context = support_factory.context(
        reporter,
        workspace=workspace,
        membership=membership,
    )
    async with support_factory.client(reporter_context) as api:
        ticket = await _create_ticket(api, support_ticket_payload(), key="permission-ticket")

    async with support_factory.client(support_factory.context(view_only)) as api:
        visible = await api.get("/api/v1/platform/support/tickets")
        export_denied = await api.get(
            "/api/v1/platform/support/tickets-export.csv"
        )
        respond_denied = await api.post(
            f"/api/v1/platform/support/tickets/{ticket['id']}/messages",
            headers={"Idempotency-Key": "view-only-response"},
            json={"body": "Resposta sem permissao."},
        )
        triage_denied = await api.patch(
            f"/api/v1/platform/support/tickets/{ticket['id']}",
            json={"expectedVersion": ticket["version"], "status": "triage"},
        )
    assert visible.status_code == 200
    assert export_denied.status_code == 403
    assert respond_denied.status_code == 403
    assert triage_denied.status_code == 403

    async with support_factory.client(support_factory.context(support_agent)) as api:
        export_allowed = await api.get(
            "/api/v1/platform/support/tickets-export.csv"
        )
        response_allowed = await api.post(
            f"/api/v1/platform/support/tickets/{ticket['id']}/messages",
            headers={"Idempotency-Key": "support-response"},
            json={"body": "Resposta autorizada."},
        )
        migration_denied = await api.post(
            "/api/v1/platform/support/migrate-technical-requests",
            headers={"X-Operation-Reason": quote("Incorporar solicitações legadas.")},
        )
    assert export_allowed.status_code == 200
    assert export_allowed.headers["content-type"].startswith("text/csv")
    assert "sac-tickets.csv" in export_allowed.headers["content-disposition"]
    assert response_allowed.status_code == 201
    assert migration_denied.status_code == 403


@pytest.mark.asyncio
async def test_support_availability_respects_the_app_and_blackbox_surfaces(
    support_factory,
):
    workspace = await support_factory.workspace()
    workspace_user, membership = await support_factory.user(
        workspace=workspace,
        role=WorkspaceRole.workspace_owner,
    )
    support_agent, _ = await support_factory.user(
        name="Support Agent",
        platform_role_package="support",
    )

    workspace_context = support_factory.context(
        workspace_user,
        workspace=workspace,
        membership=membership,
    )
    async with support_factory.client(workspace_context) as api:
        workspace_status = await api.get("/api/v1/support/availability")
        platform_namespace_denied = await api.get(
            "/api/v1/platform/support/availability"
        )

    async with support_factory.client(support_factory.context(support_agent)) as api:
        platform_status = await api.get("/api/v1/platform/support/availability")

    assert workspace_status.status_code == 200
    assert platform_namespace_denied.status_code == 403
    assert platform_status.status_code == 200


@pytest.mark.asyncio
async def test_attachment_authorization_is_ticket_and_workspace_bound(
    client,
    db_session,
    support_ticket_payload,
    auth_context,
):
    ticket = await _create_ticket(client, support_ticket_payload(), key="attachment-ticket")
    other = await _create_ticket(client, support_ticket_payload(), key="other-ticket")
    other_message = await client.post(
        f"/api/v1/support/tickets/{other['id']}/messages",
        headers={"Idempotency-Key": "other-message"},
        json={"body": "Mensagem do outro chamado."},
    )
    upload = {
        "filename": "evidencia.pdf",
        "contentType": "application/pdf",
        "byteSize": 128,
        "checksumSha256": "a" * 64,
        "expiresIn": 300,
    }

    missing_idempotency_key = await client.post(
        f"/api/v1/support/tickets/{ticket['id']}/attachments",
        json={"upload": upload},
    )
    wrong_message = await client.post(
        f"/api/v1/support/tickets/{ticket['id']}/attachments",
        headers={"Idempotency-Key": "support-attachment-wrong-message"},
        json={"upload": upload, "messageId": other_message.json()["id"]},
    )
    authorized = await client.post(
        f"/api/v1/support/tickets/{ticket['id']}/attachments",
        headers={"Idempotency-Key": "support-attachment-authorized"},
        json={"upload": upload},
    )

    assert missing_idempotency_key.status_code == 422
    assert wrong_message.status_code == 422
    assert authorized.status_code == 201, authorized.text
    body = authorized.json()
    assert "/api/v1/local-storage/object-upload?ticket=" in body["uploadUrl"]

    storage = await db_session.get(
        StorageObject,
        UUID(body["storageObjectVersionId"]),
    )
    attachment = await db_session.get(
        SupportTicketAttachment,
        UUID(body["attachmentId"]),
    )
    assert storage is not None
    assert attachment is not None
    assert storage.workspace_id == auth_context.workspace_id
    assert attachment.workspace_id == auth_context.workspace_id
    assert attachment.ticket_id == UUID(ticket["id"])
    assert storage.state is StorageObjectState.pending


@pytest.mark.asyncio
async def test_attachment_requires_completed_upload_before_confirm_and_download(
    client,
    db_session,
    support_ticket_payload,
):
    ticket = await _create_ticket(
        client,
        support_ticket_payload(),
        key="attachment-lifecycle-ticket",
    )
    payload = b"evidencia de suporte"
    authorized = await client.post(
        f"/api/v1/support/tickets/{ticket['id']}/attachments",
        headers={"Idempotency-Key": "support-attachment-lifecycle"},
        json={
            "upload": {
                "filename": "evidencia.txt",
                "contentType": "text/plain",
                "byteSize": len(payload),
                "checksumSha256": hashlib.sha256(payload).hexdigest(),
                "expiresIn": 300,
            }
        },
    )
    assert authorized.status_code == 201, authorized.text
    attachment_id = authorized.json()["attachmentId"]
    confirmation_url = (
        f"/api/v1/support/tickets/{ticket['id']}/attachments/"
        f"{attachment_id}/confirm"
    )

    premature = await client.post(confirmation_url)
    assert premature.status_code == 409
    blocked_download = await client.get(
        f"/api/v1/support/tickets/{ticket['id']}/attachments/"
        f"{attachment_id}/download-ticket"
    )
    assert blocked_download.status_code == 409

    parsed = urlsplit(authorized.json()["uploadUrl"])
    upload_target = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    uploaded = await client.put(
        upload_target,
        content=payload,
        headers={"Content-Type": "text/plain"},
    )
    assert uploaded.status_code == 200, uploaded.text

    confirmed = await client.post(confirmation_url)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["state"] == "available"

    replay = await client.post(
        f"/api/v1/support/tickets/{ticket['id']}/attachments",
        headers={"Idempotency-Key": "support-attachment-lifecycle"},
        json={
            "upload": {
                "filename": "evidencia.txt",
                "contentType": "text/plain",
                "byteSize": len(payload),
                "checksumSha256": hashlib.sha256(payload).hexdigest(),
                "expiresIn": 300,
            }
        },
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["attachmentId"] == attachment_id
    assert replay.json()["uploadRequired"] is False
    assert replay.json()["uploadUrl"] is None
    assert await db_session.scalar(select(func.count(SupportTicketAttachment.id))) == 1

    authorized_download = await client.get(
        f"/api/v1/support/tickets/{ticket['id']}/attachments/"
        f"{attachment_id}/download-ticket"
    )
    assert authorized_download.status_code == 200, authorized_download.text
    download_target = urlsplit(authorized_download.json()["downloadUrl"])
    downloaded = await client.get(
        download_target.path
        + (f"?{download_target.query}" if download_target.query else "")
    )
    assert downloaded.status_code == 200
    assert downloaded.content == payload


@pytest.mark.asyncio
async def test_attachment_linked_to_internal_note_is_not_exposed_to_customer(
    client,
    db_session,
    support_ticket_payload,
    auth_context,
    support_factory,
):
    created = await _create_ticket(
        client,
        support_ticket_payload(),
        key="internal-attachment-ticket",
    )
    ticket = await db_session.get(SupportTicket, UUID(created["id"]))
    platform_actor, _ = await support_factory.user(
        name="Technical Support",
        platform_role_package="technical_support",
    )
    service = SupportTicketService(db_session)
    internal_message, _ = await service.add_message(
        ticket,
        actor_user_id=platform_actor.id,
        actor_label=platform_actor.name,
        payload=SupportMessageCreate(
            body="Nota com anexo de diagnostico.",
            visibility=SupportMessageVisibility.internal,
        ),
        idempotency_key="internal-message-with-attachment",
        platform=True,
    )
    attachment, _ = await service.authorize_attachment(
        ticket,
        actor_user_id=platform_actor.id,
        payload=StorageUploadRequest(
            filename="diagnostico-interno.txt",
            content_type="text/plain",
            byte_size=10,
            checksum_sha256="b" * 64,
            expires_in=300,
        ),
        message_id=internal_message.id,
    )

    detail = await client.get(f"/api/v1/support/tickets/{ticket.id}")

    assert detail.status_code == 200
    assert detail.json()["messages"] == []
    assert attachment.id not in {
        UUID(item["id"]) for item in detail.json()["attachments"]
    }
    assert await db_session.scalar(
        select(func.count(SupportTicketEvent.id)).where(
            SupportTicketEvent.ticket_id == ticket.id
        )
    ) >= 3
