"""Guardas de migration, RLS e auditoria imutável da continuidade."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.db.append_only import ImmutableRecordError
from app.db.rls import (
    PLATFORM_STAFF_REASSIGNMENT_TABLES,
    RLS_POLICY_PREDICATES,
    STAFF_REASSIGNMENT_DIRECT_TENANT_TABLES,
    platform_staff_reassignment_policy_predicate,
    workspace_policy_predicate,
)
from app.models.staff_reassignment import (
    WorkspaceStaffReassignmentEvent,
    WorkspaceStaffReassignmentPlan,
)
from app.models.user import AgentType, User, UserRole
from app.workers.celery_app import celery_app

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_rls_separates_workspace_and_platform_fail_closed() -> None:
    workspace_predicate = workspace_policy_predicate()
    platform_predicate = platform_staff_reassignment_policy_predicate()

    for table_name in STAFF_REASSIGNMENT_DIRECT_TENANT_TABLES:
        assert RLS_POLICY_PREDICATES[table_name] == workspace_predicate
    for table_name in PLATFORM_STAFF_REASSIGNMENT_TABLES:
        assert RLS_POLICY_PREDICATES[table_name] == platform_predicate

    assert "app.current_platform_scope" in platform_predicate
    assert "staff_reassignment" in platform_predicate
    assert "app.current_platform_actor_id" in platform_predicate
    assert "staff_reassignment:view" in platform_predicate
    assert "staff_reassignment:approve" in platform_predicate
    assert "support_tickets" in RLS_POLICY_PREDICATES
    assert "staff_reassignment" in RLS_POLICY_PREDICATES["support_tickets"]
    assert "staff_reassignment" not in RLS_POLICY_PREDICATES[
        "support_ticket_messages"
    ]


def test_migration_declares_tables_rls_and_append_only_guards() -> None:
    source = (
        PROJECT_ROOT
        / "alembic"
        / "versions"
        / "2f4a6c8e0b31_add_staff_reassignment_continuity.py"
    ).read_text(encoding="utf-8")

    assert 'down_revision: str | Sequence[str] | None = "1e3f5a7c9b20"' in source
    for table_name in (
        *STAFF_REASSIGNMENT_DIRECT_TENANT_TABLES,
        *PLATFORM_STAFF_REASSIGNMENT_TABLES,
    ):
        assert table_name in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "klm_reject_append_only_mutation" in source
    assert "staff_reassignment:approve" in source
    assert "staff_reassignment:view" in source


def test_auto_restore_worker_is_registered_and_scheduled() -> None:
    assert "staff_reassignment_tasks.dispatch_due" in celery_app.tasks
    assert "staff_reassignment_tasks.restore_workspace" in celery_app.tasks
    assert "staff_reassignment_tasks.restore_platform" in celery_app.tasks
    schedule = celery_app.conf.beat_schedule[
        "restore-expired-staff-reassignments-every-five-minutes"
    ]
    assert schedule["task"] == "staff_reassignment_tasks.dispatch_due"


@pytest.mark.asyncio
async def test_workspace_audit_event_rejects_mutation(
    db_session,
    auth_context,
) -> None:
    target = User(
        name="Destino da auditoria",
        email=f"{uuid4().hex}@auditoria.local",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="DA",
        color="#0e3d5c",
        is_active=True,
        is_internal_staff=False,
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(target)
    await db_session.flush()
    now = datetime.now(UTC)
    plan = WorkspaceStaffReassignmentPlan(
        workspace_id=auth_context.workspace_id,
        source_user_id=auth_context.user_id,
        target_user_id=target.id,
        mode="temporary",
        status="applied",
        reason="Prova da auditoria imutável.",
        tag_label=f"continuidade:{uuid4()}",
        scopes=["booking_links"],
        assignment_summary={"booking_links": 0},
        conflict_count=0,
        starts_at=now,
        ends_at=now + timedelta(hours=1),
        applied_at=now,
        requested_by_user_id=auth_context.user_id,
        approved_by_user_id=auth_context.user_id,
        idempotency_key=f"audit-{uuid4().hex}",
        request_fingerprint="a" * 64,
        version=1,
    )
    db_session.add(plan)
    await db_session.flush()
    event = WorkspaceStaffReassignmentEvent(
        workspace_id=auth_context.workspace_id,
        plan_id=plan.id,
        actor_user_id=auth_context.user_id,
        action="applied",
        details={"proof": "original"},
        occurred_at=now,
    )
    db_session.add(event)
    await db_session.flush()

    event.details = {"proof": "alterado"}
    with pytest.raises(ImmutableRecordError):
        await db_session.flush()
