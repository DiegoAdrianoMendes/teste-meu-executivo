"""Provas do contrato de continuidade nas superfícies isoladas."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.main import app as main_app
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.staff_reassignment import PlatformStaffReassignmentPlan
from app.models.user import AgentType, User, UserRole
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.services.platform_audit_event_service import PlatformAuditEventService
from app.services.staff_reassignment_service import (
    StaffReassignmentAuthorizationError,
    StaffReassignmentService,
)


def _human(name: str, *, platform_package: str | None = None) -> User:
    suffix = uuid4().hex[:10]
    return User(
        name=name,
        email=f"{suffix}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="TR",
        is_active=True,
        is_internal_staff=platform_package is not None,
        platform_role_package=platform_package,
        platform_access_active=platform_package is not None,
        email_verified_at=datetime.now(UTC),
    )


def test_blackbox_reassignment_openapi_distinguishes_preview_and_commands() -> None:
    operations = main_app.openapi()["paths"]
    preview = operations["/api/v1/platform/staff-reassignments/preview"]["post"][
        "x-platform-mutation"
    ]
    assert preview["mutationKind"] == "query"
    assert preview["snapshotAdapter"] == "not_applicable_query"
    for path in (
        "/api/v1/platform/staff-reassignments",
        "/api/v1/platform/staff-reassignments/{plan_id}/restore",
    ):
        descriptor = operations[path]["post"]["x-platform-mutation"]
        assert descriptor["mutationKind"] == "command", path
        assert descriptor["snapshotAdapter"] == "platform_staff_reassignment", path


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_workspace_reassignment_uses_admin_contract_and_is_idempotent(
    db_session,
    auth_context,
    client,
) -> None:
    source = _human("Funcionário afastado")
    target = _human("Funcionário substituto")
    db_session.add_all([source, target])
    await db_session.flush()
    db_session.add_all(
        [
            WorkspaceMembership(
                workspace_id=auth_context.workspace_id,
                user_id=source.id,
                role=WorkspaceRole.operator,
                status=MembershipStatus.active,
            ),
            WorkspaceMembership(
                workspace_id=auth_context.workspace_id,
                user_id=target.id,
                role=WorkspaceRole.operator,
                status=MembershipStatus.active,
            ),
        ]
    )
    await db_session.flush()

    preview = await client.post(
        "/api/v1/admin/staff-reassignments/preview",
        json={
            "sourceUserId": str(source.id),
            "targetUserId": str(target.id),
            "scopes": ["matters", "tasks"],
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["total"] == 0

    payload = {
        "sourceUserId": str(source.id),
        "targetUserId": str(target.id),
        "scopes": ["matters", "tasks"],
        "mode": "temporary",
        "reason": "Afastamento médico informado pela gestão.",
        "startsAt": datetime.now(UTC).isoformat(),
        "endsAt": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
    }
    headers = {"Idempotency-Key": f"workspace-continuity-{uuid4()}"}
    created = await client.post(
        "/api/v1/admin/staff-reassignments",
        json=payload,
        headers=headers,
    )
    assert created.status_code == 201, created.text
    plan = created.json()
    assert plan["surface"] == "workspace"
    assert plan["status"] == "applied"
    assert plan["tagLabel"].startswith("continuidade:")

    repeated = await client.post(
        "/api/v1/admin/staff-reassignments",
        json=payload,
        headers=headers,
    )
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["id"] == plan["id"]

    read = await client.get(f"/api/v1/admin/staff-reassignments/{plan['id']}")
    assert read.status_code == 200, read.text
    assert read.json()["id"] == plan["id"]
    page = await client.get(
        "/api/v1/admin/staff-reassignments/page",
        params={"limit": 1, "offset": 0},
    )
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 1
    assert page.json()["items"][0]["id"] == plan["id"]
    assert page.json()["hasMore"] is False


@pytest.mark.asyncio
async def test_blackbox_reassignment_is_separate_from_workspace_route(
    db_session,
    platform_client,
) -> None:
    source = _human("Suporte afastado", platform_package="technical_support")
    target = _human("Suporte substituto", platform_package="technical_support")
    db_session.add_all([source, target])
    await db_session.flush()

    created = await platform_client.post(
        "/api/v1/platform/staff-reassignments",
        headers={"Idempotency-Key": f"platform-continuity-{uuid4()}"},
        json={
            "sourceUserId": str(source.id),
            "targetUserId": str(target.id),
            "scopes": ["support_tickets"],
            "mode": "permanent",
            "reason": "Redistribuição definitiva da fila de suporte.",
            "startsAt": datetime.now(UTC).isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    plan = created.json()
    assert plan["surface"] == "platform"
    assert plan["workspaceId"] is None

    read = await platform_client.get(
        f"/api/v1/platform/staff-reassignments/{plan['id']}"
    )
    assert read.status_code == 200, read.text
    assert read.json()["id"] == plan["id"]
    page = await platform_client.get(
        "/api/v1/platform/staff-reassignments/page",
        params={"limit": 1, "offset": 0},
    )
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 1
    assert page.json()["items"][0]["id"] == plan["id"]
    assert page.json()["hasMore"] is False
    canonical = list(
        await db_session.scalars(
            select(PlatformAuditEvent).where(
                PlatformAuditEvent.resource_id == plan["id"],
                PlatformAuditEvent.action == "staff_reassignment.apply",
            )
        )
    )
    assert len(canonical) == 1
    assert canonical[0].before is None
    assert canonical[0].after["status"] == "applied"
    assert canonical[0].after["reasonPresent"] is True


@pytest.mark.asyncio
async def test_blackbox_reassignment_page_total_respects_manager_scope(
    db_session,
    platform_client,
    platform_auth_context,
) -> None:
    actor = await db_session.get(User, platform_auth_context.user_id)
    assert actor is not None
    actor.is_platform_admin = False
    actor.is_internal_staff = True
    actor.platform_access_active = True
    actor.platform_role_package = "technical_support"
    source = _human("Origem da paginação", platform_package="technical_support")
    target = _human("Destino da paginação", platform_package="technical_support")
    db_session.add_all([source, target])
    await db_session.flush()
    now = datetime.now(UTC)
    db_session.add_all(
        [
            PlatformStaffReassignmentPlan(
                source_user_id=source.id,
                target_user_id=target.id,
                mode="permanent",
                status="applied",
                reason="Transferência técnica visível ao suporte.",
                tag_label="continuidade:suporte",
                scopes=["support_tickets"],
                assignment_summary={"support_tickets": 1},
                conflict_count=0,
                starts_at=now,
                ends_at=None,
                applied_at=now,
                requested_by_user_id=actor.id,
                approved_by_user_id=actor.id,
                idempotency_key=f"support-page-{uuid4()}",
                request_fingerprint="a" * 64,
                version=1,
            ),
            PlatformStaffReassignmentPlan(
                source_user_id=source.id,
                target_user_id=target.id,
                mode="permanent",
                status="applied",
                reason="Transferência comercial invisível ao suporte.",
                tag_label="continuidade:comercial",
                scopes=["growth_prospects"],
                assignment_summary={"growth_prospects": 1},
                conflict_count=0,
                starts_at=now,
                ends_at=None,
                applied_at=now,
                requested_by_user_id=actor.id,
                approved_by_user_id=actor.id,
                idempotency_key=f"growth-page-{uuid4()}",
                request_fingerprint="b" * 64,
                version=1,
            ),
        ]
    )
    await db_session.flush()

    page = await platform_client.get(
        "/api/v1/platform/staff-reassignments/page",
        params={"limit": 10, "offset": 0},
    )
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 1
    assert len(page.json()["items"]) == 1
    assert page.json()["items"][0]["scopes"] == ["support_tickets"]


@pytest.mark.asyncio
async def test_blackbox_reassignment_restore_is_versioned_and_audited(
    db_session,
    platform_client,
) -> None:
    source = _human("Origem restaurável", platform_package="technical_support")
    target = _human("Destino temporário", platform_package="technical_support")
    db_session.add_all([source, target])
    await db_session.flush()
    starts_at = datetime.now(UTC)
    created = await platform_client.post(
        "/api/v1/platform/staff-reassignments",
        headers={"Idempotency-Key": f"platform-restore-{uuid4()}"},
        json={
            "sourceUserId": str(source.id),
            "targetUserId": str(target.id),
            "scopes": ["support_tickets"],
            "mode": "temporary",
            "reason": "Cobertura temporária auditável da fila de suporte.",
            "startsAt": starts_at.isoformat(),
            "endsAt": (starts_at + timedelta(days=2)).isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    plan = created.json()
    restored = await platform_client.post(
        f"/api/v1/platform/staff-reassignments/{plan['id']}/restore",
        json={
            "expectedVersion": plan["version"],
            "reason": "Retorno confirmado e restauração autorizada.",
        },
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["status"] == "restored"
    event = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_id == plan["id"],
            PlatformAuditEvent.action == "staff_reassignment.restore",
        )
    )
    assert event is not None
    assert event.before["status"] == "applied"
    assert event.after["status"] == "restored"
    assert event.resource_version_after == event.resource_version_before + 1


@pytest.mark.asyncio
async def test_blackbox_reassignment_rolls_back_when_canonical_audit_fails(
    db_session,
    platform_client,
    monkeypatch,
) -> None:
    source = _human("Origem do rollback", platform_package="technical_support")
    target = _human("Destino do rollback", platform_package="technical_support")
    db_session.add_all([source, target])
    await db_session.flush()

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    before_count = int(
        (await db_session.scalar(select(func.count(PlatformStaffReassignmentPlan.id))))
        or 0
    )
    monkeypatch.setattr(PlatformAuditEventService, "record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        await platform_client.post(
            "/api/v1/platform/staff-reassignments",
            headers={"Idempotency-Key": f"platform-rollback-{uuid4()}"},
            json={
                "sourceUserId": str(source.id),
                "targetUserId": str(target.id),
                "scopes": ["support_tickets"],
                "mode": "permanent",
                "reason": "Redistribuição que não pode existir sem auditoria.",
                "startsAt": datetime.now(UTC).isoformat(),
            },
        )
    db_session.expire_all()
    after_count = int(
        (await db_session.scalar(select(func.count(PlatformStaffReassignmentPlan.id))))
        or 0
    )
    assert after_count == before_count


def test_blackbox_manager_cannot_reassign_another_area() -> None:
    commercial = _human("Gestão comercial", platform_package="commercial_manager")
    StaffReassignmentService._assert_platform_scope_manager(
        commercial,
        ["growth_prospects"],
    )
    with pytest.raises(StaffReassignmentAuthorizationError):
        StaffReassignmentService._assert_platform_scope_manager(
            commercial,
            ["support_tickets"],
        )
