from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.core.platform_rbac import has_platform_permission
from app.main import app
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.platform_organization import (
    PlatformOnboardingMilestone,
    PlatformOperationalAssignment,
    PlatformReviewRequest,
    PlatformStaffInvitation,
    PlatformTeam,
    PlatformWorkspaceAssignment,
)
from app.models.user import User
from app.services.platform_audit_event_service import PlatformAuditEventService


def test_organization_mutations_publish_canonical_snapshot_contracts() -> None:
    operations = app.openapi()["paths"]
    mutation_paths = {
        "/api/v1/platform/organization/teams": ["post"],
        "/api/v1/platform/organization/teams/{team_id}": ["patch"],
        "/api/v1/platform/organization/teams/{team_id}/members": ["post"],
        "/api/v1/platform/organization/workspaces/{workspace_id}/assignments": ["put"],
        "/api/v1/platform/organization/dashboard-preference": ["put"],
        "/api/v1/platform/organization/staff-invitations": ["post"],
        "/api/v1/platform/organization/staff-invitations/{invitation_id}/revoke": ["post"],
        "/api/v1/platform/organization/workspaces/{workspace_id}/milestones": ["put"],
        "/api/v1/platform/organization/operational-assignments": ["put"],
        "/api/v1/platform/organization/reviews": ["put"],
    }
    for path, methods in mutation_paths.items():
        for method in methods:
            descriptor = operations[path][method]["x-platform-mutation"]
            assert descriptor["snapshotAdapter"] != "none", (path, method)
            expected_reason = (
                "system_allowed" if path.endswith("/dashboard-preference") else "required"
            )
            assert descriptor["reasonPolicy"] == expected_reason, (path, method)


@pytest.mark.asyncio
async def test_team_commands_keep_request_correlation_and_rollback_with_audit(
    platform_client,
    db_session,
    monkeypatch,
) -> None:
    slug = f"support-{uuid4().hex[:8]}"
    create_correlation = f"organization-create-{uuid4()}"
    created = await platform_client.post(
        "/api/v1/platform/organization/teams",
        headers={"X-Correlation-ID": create_correlation},
        json={
            "name": "Equipe de suporte auditável",
            "slug": slug,
            "description": "Operação interna do SAC.",
            "reason": "Estrutura organizacional aprovada pelo titular.",
        },
    )
    assert created.status_code == 201, created.text
    team_id = created.json()["id"]

    update_correlation = f"organization-update-{uuid4()}"
    updated = await platform_client.patch(
        f"/api/v1/platform/organization/teams/{team_id}",
        headers={"X-Correlation-ID": update_correlation},
        json={
            "name": "Equipe de suporte e sucesso",
            "slug": slug,
            "description": "Operação interna do SAC e CS.",
            "expectedVersion": created.json()["version"],
            "reason": "Escopo da equipe revisado e aprovado.",
        },
    )
    assert updated.status_code == 200, updated.text

    events = list(
        (
            await db_session.scalars(
                select(PlatformAuditEvent)
                .where(
                    PlatformAuditEvent.resource_type == "platform_team",
                    PlatformAuditEvent.resource_id == team_id,
                )
                .order_by(PlatformAuditEvent.occurred_at, PlatformAuditEvent.id)
            )
        ).all()
    )
    assert len(events) == 2
    assert events[0].correlation_id == create_correlation
    assert events[1].correlation_id == update_correlation
    assert events[0].route == "/api/v1/platform/organization/teams"
    assert events[1].route == "/api/v1/platform/organization/teams/{team_id}"
    assert events[0].method == "POST"
    assert events[1].method == "PATCH"
    assert events[0].before is None
    assert {"name", "description", "version"} <= set(events[1].changed_paths)

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(PlatformAuditEventService, "record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        await platform_client.patch(
            f"/api/v1/platform/organization/teams/{team_id}",
            json={
                "name": "Nome que não pode persistir",
                "slug": slug,
                "description": "Mudança revertida.",
                "expectedVersion": updated.json()["version"],
                "reason": "Mudança deve falhar junto da auditoria.",
            },
        )

    db_session.expire_all()
    persisted = await db_session.get(PlatformTeam, UUID(team_id))
    assert persisted is not None
    assert persisted.name == "Equipe de suporte e sucesso"
    assert persisted.version == updated.json()["version"]


@pytest.mark.asyncio
async def test_staff_invitation_page_has_server_total_status_and_stable_pages(
    platform_client,
    platform_auth_context,
    db_session,
) -> None:
    marker = uuid4().hex[:8]
    now = datetime.now(UTC)
    db_session.add_all(
        [
            PlatformStaffInvitation(
                email=f"invite-page-{marker}-{index:03d}@example.com",
                name=f"Convite Paginado {marker} {index:03d}",
                role_package="support",
                invited_by_user_id=platform_auth_context.user_id,
                status="pending" if index < 52 else "revoked",
                expires_at=now + timedelta(days=7),
                last_sent_at=now,
                send_count=1,
                version=1,
            )
            for index in range(55)
        ]
    )
    await db_session.commit()

    first = await platform_client.get(
        "/api/v1/platform/organization/staff-invitations/page",
        params={
            "search": f"Convite Paginado {marker}",
            "status": "pending",
            "limit": 20,
            "offset": 0,
        },
    )
    last = await platform_client.get(
        "/api/v1/platform/organization/staff-invitations/page",
        params={
            "search": f"Convite Paginado {marker}",
            "status": "pending",
            "limit": 20,
            "offset": 40,
        },
    )

    assert first.status_code == 200, first.text
    assert last.status_code == 200, last.text
    first_page = first.json()
    last_page = last.json()
    assert first_page["total"] == 52
    assert len(first_page["items"]) == 20
    assert first_page["hasMore"] is True
    assert last_page["total"] == 52
    assert len(last_page["items"]) == 12
    assert last_page["hasMore"] is False
    assert {item["id"] for item in first_page["items"]}.isdisjoint(
        {item["id"] for item in last_page["items"]}
    )


@pytest.mark.asyncio
async def test_team_page_has_server_total_search_and_stable_pages(
    platform_client,
    db_session,
) -> None:
    marker = uuid4().hex[:8]
    db_session.add_all(
        [
            PlatformTeam(
                name=f"Equipe Paginada {marker} {index:03d}",
                slug=f"equipe-paginada-{marker}-{index:03d}",
                description=None,
                manager_user_id=None,
                active=index < 52,
                version=1,
            )
            for index in range(55)
        ]
    )
    await db_session.commit()

    first = await platform_client.get(
        "/api/v1/platform/organization/teams/page",
        params={"search": marker, "active": True, "limit": 20, "offset": 0},
    )
    last = await platform_client.get(
        "/api/v1/platform/organization/teams/page",
        params={"search": marker, "active": True, "limit": 20, "offset": 40},
    )

    assert first.status_code == 200, first.text
    assert last.status_code == 200, last.text
    first_page = first.json()
    last_page = last.json()
    assert first_page["total"] == 52
    assert len(first_page["items"]) == 20
    assert first_page["hasMore"] is True
    assert last_page["total"] == 52
    assert len(last_page["items"]) == 12
    assert last_page["hasMore"] is False
    assert {item["id"] for item in first_page["items"]}.isdisjoint(
        {item["id"] for item in last_page["items"]}
    )
    exact = await platform_client.get(
        "/api/v1/platform/organization/teams/page",
        params={"search": first_page["items"][0]["id"], "limit": 1, "offset": 0},
    )
    assert exact.status_code == 200, exact.text
    assert exact.json()["total"] == 1
    assert exact.json()["items"][0]["id"] == first_page["items"][0]["id"]


@pytest.mark.asyncio
async def test_account_operations_pages_have_real_totals_and_stable_order(
    platform_client,
    platform_auth_context,
    db_session,
) -> None:
    marker = uuid4().hex[:8]
    created = await platform_client.post(
        "/api/v1/platform/workspaces",
        json={"name": f"Conta operacional paginada {marker}", "setupMode": "assisted"},
    )
    assert created.status_code == 201, created.text
    workspace_id = UUID(created.json()["id"])
    now = datetime.now(UTC)
    db_session.add_all(
        [
            PlatformWorkspaceAssignment(
                workspace_id=workspace_id,
                assignment_type=assignment_type,
                assigned_user_id=platform_auth_context.user_id,
                assigned_team_id=None,
                starts_at=now,
                ends_at=None,
                version=1,
            )
            for assignment_type in ("onboarding", "customer_success", "billing", "support")
        ]
        + [
            PlatformOnboardingMilestone(
                workspace_id=workspace_id,
                key=f"marco_{index:02d}",
                label=f"Marco operacional {index:02d}",
                status="pending",
                owner_user_id=platform_auth_context.user_id,
                due_at=now + timedelta(days=index),
                completed_at=None,
                version=1,
            )
            for index in range(7)
        ]
    )
    await db_session.commit()

    assignments = await platform_client.get(
        f"/api/v1/platform/organization/workspaces/{workspace_id}/assignments/page",
        params={"limit": 2, "offset": 2},
    )
    milestones = await platform_client.get(
        f"/api/v1/platform/organization/workspaces/{workspace_id}/milestones/page",
        params={"limit": 5, "offset": 5},
    )

    assert assignments.status_code == 200, assignments.text
    assert assignments.json()["total"] == 4
    assert assignments.json()["hasMore"] is False
    assert len(assignments.json()["items"]) == 2
    assert milestones.status_code == 200, milestones.text
    assert milestones.json()["total"] == 7
    assert milestones.json()["hasMore"] is False
    assert [item["key"] for item in milestones.json()["items"]] == [
        "marco_05",
        "marco_06",
    ]


@pytest.mark.asyncio
async def test_operational_assignments_page_has_real_total_filters_and_stable_pages(
    platform_client,
    db_session,
) -> None:
    now = datetime.now(UTC)
    db_session.add_all(
        [
            PlatformOperationalAssignment(
                source_type="lead",
                source_id=uuid4(),
                owner_user_id=None,
                team_id=None,
                next_action=f"Ação paginada {index:03d}",
                next_action_at=None,
                due_at=now + timedelta(minutes=index) if index < 52 else None,
                state="open" if index < 52 else "done",
                version=1,
            )
            for index in range(55)
        ]
    )
    await db_session.commit()

    first = await platform_client.get(
        "/api/v1/platform/organization/operational-assignments/page",
        params={"sourceType": "lead", "state": "open", "limit": 20, "offset": 0},
    )
    last = await platform_client.get(
        "/api/v1/platform/organization/operational-assignments/page",
        params={"sourceType": "lead", "state": "open", "limit": 20, "offset": 40},
    )

    assert first.status_code == 200, first.text
    assert last.status_code == 200, last.text
    first_page = first.json()
    last_page = last.json()
    assert first_page["total"] == 52
    assert len(first_page["items"]) == 20
    assert first_page["hasMore"] is True
    assert last_page["total"] == 52
    assert len(last_page["items"]) == 12
    assert last_page["hasMore"] is False
    assert {item["id"] for item in first_page["items"]}.isdisjoint(
        {item["id"] for item in last_page["items"]}
    )


@pytest.mark.asyncio
async def test_reviews_page_has_real_total_filters_and_stable_pages(
    platform_client,
    db_session,
) -> None:
    now = datetime.now(UTC)
    db_session.add_all(
        [
            PlatformReviewRequest(
                source_type="publication",
                source_id=uuid4(),
                state="in_review" if index < 52 else "published",
                author_user_id=None,
                reviewer_user_id=None,
                review_due_at=now + timedelta(minutes=index) if index < 52 else None,
                reviewed_at=None,
                reason=None,
                version=1,
            )
            for index in range(55)
        ]
    )
    await db_session.commit()

    first = await platform_client.get(
        "/api/v1/platform/organization/reviews/page",
        params={
            "sourceType": "publication",
            "state": "in_review",
            "limit": 20,
            "offset": 0,
        },
    )
    last = await platform_client.get(
        "/api/v1/platform/organization/reviews/page",
        params={
            "sourceType": "publication",
            "state": "in_review",
            "limit": 20,
            "offset": 40,
        },
    )

    assert first.status_code == 200, first.text
    assert last.status_code == 200, last.text
    first_page = first.json()
    last_page = last.json()
    assert first_page["total"] == 52
    assert len(first_page["items"]) == 20
    assert first_page["hasMore"] is True
    assert last_page["total"] == 52
    assert len(last_page["items"]) == 12
    assert last_page["hasMore"] is False
    assert {item["id"] for item in first_page["items"]}.isdisjoint(
        {item["id"] for item in last_page["items"]}
    )


@pytest.mark.asyncio
async def test_global_configuration_assignment_and_review_require_settings_permission(
    platform_client,
    platform_auth_context,
    db_session,
) -> None:
    actor = await db_session.get(User, platform_auth_context.user_id)
    assert actor is not None
    actor.is_platform_admin = False
    actor.platform_role_package = "support"
    actor.platform_permission_grants = []
    actor.platform_permission_denials = []
    await db_session.commit()

    source_id = str(uuid4())
    assignment_payload = {
        "sourceType": "configuration",
        "sourceId": source_id,
        "state": "open",
        "reason": "Validar domínio global de configurações.",
    }
    review_payload = {
        "sourceType": "configuration",
        "sourceId": source_id,
        "state": "in_review",
        "reason": "Validar domínio global de configurações.",
    }

    # Support owns tenant configuration, but it must not authorize global
    # PlatformConfigDocument workflows.
    denied_assignment = await platform_client.put(
        "/api/v1/platform/organization/operational-assignments",
        json=assignment_payload,
    )
    denied_review = await platform_client.put(
        "/api/v1/platform/organization/reviews",
        json=review_payload,
    )
    assert denied_assignment.status_code == 403
    assert denied_review.status_code == 403

    actor = await db_session.get(User, platform_auth_context.user_id)
    assert actor is not None
    actor.platform_permission_grants = [
        {"module": "settings", "action": "view"},
        {"module": "settings", "action": "manage"},
    ]
    flag_modified(actor, "platform_permission_grants")
    await db_session.commit()
    assert has_platform_permission(actor, "settings", "manage")

    authorized_assignment = await platform_client.put(
        "/api/v1/platform/organization/operational-assignments",
        json=assignment_payload,
    )
    authorized_review = await platform_client.put(
        "/api/v1/platform/organization/reviews",
        json=review_payload,
    )
    assert authorized_assignment.status_code == 404, authorized_assignment.text
    assert authorized_assignment.json()["detail"]["code"] == (
        "PLATFORM_OPERATIONAL_SOURCE_NOT_FOUND"
    )
    assert authorized_review.status_code == 404, authorized_review.text
    assert authorized_review.json()["detail"]["code"] == "PLATFORM_REVIEW_SOURCE_NOT_FOUND"
