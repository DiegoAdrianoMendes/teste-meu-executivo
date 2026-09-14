from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.models  # noqa: F401
from app.db.platform_control import set_platform_control_context
from app.db.rls import install_rls_runtime_guard
from app.db.tenant import TenantSession, set_workspace_context
from app.models.client import Client
from app.models.matter import Matter
from app.models.platform_task import (
    PlatformTask,
    PlatformTaskEvent,
    PlatformTaskEventType,
    PlatformTaskPriority,
)
from app.models.support_ticket import (
    SupportTicket,
    SupportTicketPriority,
    SupportTicketQueue,
    SupportTicketSeverity,
    SupportTicketStatus,
    SupportTicketType,
)
from app.models.task import (
    Task,
    TaskEvent,
    TaskEventType,
    TaskParticipant,
    TaskParticipantRole,
    TaskParticipantState,
    TaskPriority,
    TaskReviewRound,
    TaskReviewState,
    TaskStatus,
)
from app.models.task_organization import (
    TaskBoard,
    TaskBoardColumn,
    TaskBoardPlacement,
    TaskList,
    TaskListMembership,
)
from app.models.task_reminder import (
    TaskReminderChannel,
    TaskReminderCommandReceipt,
    TaskReminderDeliveryStatus,
    TaskReminderEvent,
    TaskReminderEventType,
    TaskReminderOutbox,
    TaskReminderRecipient,
    TaskReminderRelativeTo,
    TaskReminderRule,
    TaskReminderRuleState,
)
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.schemas.platform_task import PlatformTaskCreate, PlatformTaskUpdate
from app.services.platform_task_service import (
    PlatformTaskError,
    PlatformTaskMutationContext,
    PlatformTaskMutationResult,
    PlatformTaskService,
)

MASTER_URL = os.getenv("KLM_TEST_POSTGRES_MASTER_URL")
ADMIN_URL = os.getenv("KLM_TEST_POSTGRES_ADMIN_URL")
APP_URL = os.getenv("KLM_TEST_POSTGRES_APP_URL")

if not MASTER_URL or not ADMIN_URL or not APP_URL:
    raise RuntimeError(
        "PostgreSQL QA requires KLM_TEST_POSTGRES_MASTER_URL, "
        "KLM_TEST_POSTGRES_ADMIN_URL, and KLM_TEST_POSTGRES_APP_URL; "
        "run scripts/verify_postgresql_qa.sh."
    )

if os.getenv("KLM_TEST_POSTGRES_QA_DISPOSABLE") != "1":
    raise RuntimeError(
        "Task collaboration RLS QA disables immutable triggers during cleanup "
        "and therefore requires the disposable PostgreSQL QA harness."
    )


@dataclass(frozen=True)
class PostgresEngines:
    master: AsyncEngine
    admin: AsyncEngine
    app: AsyncEngine
    master_sessions: async_sessionmaker[AsyncSession]
    admin_sessions: async_sessionmaker[AsyncSession]
    app_sessions: async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class TenantCollaborationRows:
    workspace_id: UUID
    user_id: UUID
    membership_id: UUID
    matter_id: UUID
    task_id: UUID
    participant_id: UUID
    review_round_id: UUID
    event_id: UUID
    reminder_rule_id: UUID
    reminder_outbox_id: UUID
    reminder_event_id: UUID
    reminder_receipt_id: UUID


@dataclass(frozen=True)
class CollaborationRows:
    tenant_a: TenantCollaborationRows
    tenant_b: TenantCollaborationRows
    platform_actor_id: UUID
    platform_task_id: UUID
    platform_event_id: UUID


TENANT_MODELS = {
    "tasks": Task,
    "task_participants": TaskParticipant,
    "task_review_rounds": TaskReviewRound,
    "task_events": TaskEvent,
    "task_reminder_rules": TaskReminderRule,
    "task_reminder_outbox": TaskReminderOutbox,
    "task_reminder_events": TaskReminderEvent,
    "task_reminder_command_receipts": TaskReminderCommandReceipt,
}

APPEND_ONLY_MODELS = {
    "task_events": TaskEvent,
    "task_reminder_events": TaskReminderEvent,
    "task_reminder_command_receipts": TaskReminderCommandReceipt,
}

MUTABLE_TENANT_MODELS = {
    table_name: model
    for table_name, model in TENANT_MODELS.items()
    if table_name not in APPEND_ONLY_MODELS
}

TASK_ORGANIZATION_TENANT_MODELS = {
    "task_lists": TaskList,
    "task_list_memberships": TaskListMembership,
    "task_boards": TaskBoard,
    "task_board_columns": TaskBoardColumn,
    "task_board_placements": TaskBoardPlacement,
}

IMMUTABLE_FIXTURE_TRIGGERS = (
    ("platform_task_events", "trg_platform_task_events_immutable"),
    ("task_events", "trg_task_events_immutable"),
    (
        "task_reminder_command_receipts",
        "trg_task_reminder_command_receipts_immutable",
    ),
    ("task_reminder_events", "trg_task_reminder_events_immutable"),
)


@pytest_asyncio.fixture
async def postgres_engines() -> AsyncIterator[PostgresEngines]:
    assert MASTER_URL is not None and ADMIN_URL is not None and APP_URL is not None
    master_engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    admin_engine = create_async_engine(ADMIN_URL, pool_pre_ping=True)
    app_engine = create_async_engine(APP_URL, pool_pre_ping=True)
    install_rls_runtime_guard(app_engine)
    master_sessions = async_sessionmaker(
        master_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    admin_sessions = async_sessionmaker(
        admin_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    app_sessions = async_sessionmaker(
        app_engine,
        class_=AsyncSession,
        sync_session_class=TenantSession,
        expire_on_commit=False,
    )

    try:
        yield PostgresEngines(
            master_engine,
            admin_engine,
            app_engine,
            master_sessions,
            admin_sessions,
            app_sessions,
        )
    finally:
        await app_engine.dispose()
        await admin_engine.dispose()
        await master_engine.dispose()


def _workspace(token: str, suffix: str) -> Workspace:
    return Workspace(
        name=f"Task collaboration RLS {suffix.upper()} {token}",
        slug=f"task-collab-rls-{suffix}-{token}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )


def _user(token: str, suffix: str, *, internal: bool = False) -> User:
    return User(
        name=f"Task collaboration {suffix.upper()} {token}",
        email=f"task-collab-{suffix}-{token}@example.test",
        agent_type=AgentType.human,
        role=UserRole.admin if internal else UserRole.lawyer,
        initials=suffix[:2].upper(),
        color="#164e63",
        is_active=True,
        email_verified_at=datetime.now(UTC),
        is_internal_staff=internal,
        is_platform_admin=internal,
        platform_role_package="platform_admin" if internal else None,
        platform_access_active=internal,
    )


async def _seed_tenant_collaboration(
    sessions: async_sessionmaker[AsyncSession],
    *,
    workspace_id: UUID,
    user_id: UUID,
    membership_id: UUID,
    matter_id: UUID,
    token: str,
    suffix: str,
) -> TenantCollaborationRows:
    now = datetime.now(UTC)
    due_at = now + timedelta(days=2)
    review_due_at = now + timedelta(days=1)
    async with sessions() as session:
        await set_workspace_context(session, workspace_id)
        task = Task(
            workspace_id=workspace_id,
            matter_id=matter_id,
            assigned_to=user_id,
            title=f"Tenant task {suffix.upper()} {token}",
            status=TaskStatus.pending,
            priority=TaskPriority.media,
            due_at=due_at,
            review_due_at=review_due_at,
            review_required=True,
            review_state=TaskReviewState.pending,
            review_round=1,
            version=1,
            created_by_user_id=user_id,
        )
        session.add(task)
        await session.flush()

        participant = TaskParticipant(
            workspace_id=workspace_id,
            matter_id=matter_id,
            task_id=task.id,
            membership_id=membership_id,
            user_id=user_id,
            role=TaskParticipantRole.assignee,
            due_at=due_at,
            state=TaskParticipantState.assigned,
            required_for_completion=True,
            label_snapshot=f"Tenant user {suffix.upper()}",
            active=True,
            created_by_user_id=user_id,
        )
        review_round = TaskReviewRound(
            workspace_id=workspace_id,
            matter_id=matter_id,
            task_id=task.id,
            round_number=1,
            reviewer_user_id=user_id,
            state=TaskReviewState.pending,
            due_at=review_due_at,
            submitted_by_user_id=user_id,
        )
        event = TaskEvent(
            workspace_id=workspace_id,
            matter_id=matter_id,
            task_id=task.id,
            actor_user_id=user_id,
            actor_label_snapshot=f"Tenant user {suffix.upper()}",
            actor_role_snapshot=WorkspaceRole.workspace_owner.value,
            actor_type="human",
            event_type=TaskEventType.created,
            before_data=None,
            after_data={"status": TaskStatus.pending.value},
            changed_paths=["status"],
            comment=None,
            reason="Seed PostgreSQL collaboration RLS proof.",
            reason_source="manual",
            correlation_id=f"task-collab-{suffix}-{token}",
            session_type="normal",
            resource_version_before=None,
            resource_version_after=1,
        )
        session.add_all((participant, review_round, event))
        await session.flush()

        reminder_rule = TaskReminderRule(
            workspace_id=workspace_id,
            matter_id=matter_id,
            task_id=task.id,
            relative_to=TaskReminderRelativeTo.general_due,
            offset_minutes_before=60,
            channel=TaskReminderChannel.in_app,
            recipient=TaskReminderRecipient.specific_user,
            recipient_user_id=user_id,
            recipient_key=f"user:{user_id}",
            state=TaskReminderRuleState.scheduled,
            active=True,
            version=1,
            created_by_user_id=user_id,
        )
        session.add(reminder_rule)
        await session.flush()

        reminder_outbox = TaskReminderOutbox(
            workspace_id=workspace_id,
            matter_id=matter_id,
            task_id=task.id,
            rule_id=reminder_rule.id,
            rule_version=1,
            task_version=1,
            recipient_user_id=user_id,
            deadline_at=due_at,
            scheduled_for=due_at - timedelta(minutes=60),
            channel=TaskReminderChannel.in_app,
            status=TaskReminderDeliveryStatus.pending,
            dedupe_key=f"task-collab:{suffix}:{token}",
            correlation_id=f"task-reminder-{suffix}-{token}",
            attempts=0,
            available_at=now,
        )
        session.add(reminder_outbox)
        await session.flush()

        reminder_event = TaskReminderEvent(
            workspace_id=workspace_id,
            matter_id=matter_id,
            task_id=task.id,
            rule_id=reminder_rule.id,
            outbox_id=reminder_outbox.id,
            actor_user_id=user_id,
            event_type=TaskReminderEventType.created,
            before_data=None,
            after_data={"state": TaskReminderRuleState.scheduled.value},
            reason="Seed PostgreSQL reminder RLS proof.",
            correlation_id=f"task-reminder-event-{suffix}-{token}",
            session_type="normal",
            resource_version_before=None,
            resource_version_after=1,
        )
        reminder_receipt = TaskReminderCommandReceipt(
            workspace_id=workspace_id,
            matter_id=matter_id,
            task_id=task.id,
            rule_id=reminder_rule.id,
            idempotency_key=f"task-reminder-receipt-{suffix}-{token}",
            operation="create",
            request_sha256=(suffix * 64)[:64],
            response_status=201,
            response_data={"rule_id": str(reminder_rule.id)},
        )
        session.add_all((reminder_event, reminder_receipt))
        await session.commit()

    return TenantCollaborationRows(
        workspace_id=workspace_id,
        user_id=user_id,
        membership_id=membership_id,
        matter_id=matter_id,
        task_id=task.id,
        participant_id=participant.id,
        review_round_id=review_round.id,
        event_id=event.id,
        reminder_rule_id=reminder_rule.id,
        reminder_outbox_id=reminder_outbox.id,
        reminder_event_id=reminder_event.id,
        reminder_receipt_id=reminder_receipt.id,
    )


@pytest_asyncio.fixture
async def collaboration_rows(
    postgres_engines: PostgresEngines,
) -> AsyncIterator[CollaborationRows]:
    token = uuid4().hex[:12]
    workspace_a = _workspace(token, "a")
    workspace_b = _workspace(token, "b")
    user_a = _user(token, "a")
    user_b = _user(token, "b")
    platform_actor = _user(token, "platform", internal=True)

    # The cluster credential is fixture-only: it may bypass forced RLS to seed
    # two mutually isolated tenants. Assertions below always use the separately
    # authenticated runtime and least-privilege migrator roles.
    async with postgres_engines.master_sessions() as session:
        session.add_all((workspace_a, workspace_b, user_a, user_b, platform_actor))
        await session.flush()
        membership_a = WorkspaceMembership(
            workspace_id=workspace_a.id,
            user_id=user_a.id,
            role=WorkspaceRole.workspace_owner,
            status=MembershipStatus.active,
            is_default=True,
        )
        membership_b = WorkspaceMembership(
            workspace_id=workspace_b.id,
            user_id=user_b.id,
            role=WorkspaceRole.workspace_owner,
            status=MembershipStatus.active,
            is_default=True,
        )
        client_a = Client(workspace_id=workspace_a.id, name=f"Client A {token}")
        client_b = Client(workspace_id=workspace_b.id, name=f"Client B {token}")
        session.add_all((membership_a, membership_b, client_a, client_b))
        await session.flush()
        matter_a = Matter(
            workspace_id=workspace_a.id,
            client_id=client_a.id,
            demand_type=f"Demand A {token}",
        )
        matter_b = Matter(
            workspace_id=workspace_b.id,
            client_id=client_b.id,
            demand_type=f"Demand B {token}",
        )
        session.add_all((matter_a, matter_b))
        await session.commit()

    tenant_a = await _seed_tenant_collaboration(
        postgres_engines.master_sessions,
        workspace_id=workspace_a.id,
        user_id=user_a.id,
        membership_id=membership_a.id,
        matter_id=matter_a.id,
        token=token,
        suffix="a",
    )
    tenant_b = await _seed_tenant_collaboration(
        postgres_engines.master_sessions,
        workspace_id=workspace_b.id,
        user_id=user_b.id,
        membership_id=membership_b.id,
        matter_id=matter_b.id,
        token=token,
        suffix="b",
    )

    async with postgres_engines.master_sessions() as session:
        await set_platform_control_context(
            session,
            actor_user_id=platform_actor.id,
            module="platform_root",
            action="manage",
            is_platform_root=True,
        )
        platform_task = PlatformTask(
            title=f"Platform task {token}",
            priority=PlatformTaskPriority.P2,
            owner_user_id=platform_actor.id,
            created_by_user_id=platform_actor.id,
            workspace_id=workspace_a.id,
        )
        session.add(platform_task)
        await session.flush()
        platform_event = PlatformTaskEvent(
            task_id=platform_task.id,
            actor_user_id=platform_actor.id,
            actor_label_snapshot=platform_actor.name,
            actor_role_snapshot="platform_admin",
            is_platform_root=True,
            event_type=PlatformTaskEventType.created,
            before_data=None,
            after_data={"priority": PlatformTaskPriority.P2.value},
            changed_paths=["priority"],
            comment=None,
            reason="Seed PostgreSQL platform-task RLS proof.",
            correlation_id=f"platform-task-{token}",
            session_type="normal",
            resource_version_before=None,
            resource_version_after=1,
        )
        session.add(platform_event)
        await session.commit()

    rows = CollaborationRows(
        tenant_a=tenant_a,
        tenant_b=tenant_b,
        platform_actor_id=platform_actor.id,
        platform_task_id=platform_task.id,
        platform_event_id=platform_event.id,
    )
    try:
        yield rows
    finally:
        async with postgres_engines.master_sessions() as session:
            try:
                await session.execute(text("SET LOCAL lock_timeout = '10s'"))
                for table_name, trigger_name in IMMUTABLE_FIXTURE_TRIGGERS:
                    await session.execute(
                        text(f'ALTER TABLE "{table_name}" DISABLE TRIGGER "{trigger_name}"')
                    )

                await session.execute(
                    delete(PlatformTaskEvent).where(PlatformTaskEvent.id == rows.platform_event_id)
                )
                await session.execute(
                    delete(PlatformTask).where(PlatformTask.id == rows.platform_task_id)
                )
                tenant_ids = (rows.tenant_a, rows.tenant_b)
                await session.execute(
                    delete(TaskReminderEvent).where(
                        TaskReminderEvent.id.in_(row.reminder_event_id for row in tenant_ids)
                    )
                )
                await session.execute(
                    delete(TaskReminderCommandReceipt).where(
                        TaskReminderCommandReceipt.id.in_(
                            row.reminder_receipt_id for row in tenant_ids
                        )
                    )
                )
                await session.execute(
                    delete(TaskReminderOutbox).where(
                        TaskReminderOutbox.id.in_(row.reminder_outbox_id for row in tenant_ids)
                    )
                )
                await session.execute(
                    delete(TaskReminderRule).where(
                        TaskReminderRule.id.in_(row.reminder_rule_id for row in tenant_ids)
                    )
                )
                await session.execute(
                    delete(TaskEvent).where(TaskEvent.id.in_(row.event_id for row in tenant_ids))
                )
                await session.execute(
                    delete(TaskReviewRound).where(
                        TaskReviewRound.id.in_(row.review_round_id for row in tenant_ids)
                    )
                )
                await session.execute(
                    delete(TaskParticipant).where(
                        TaskParticipant.id.in_(row.participant_id for row in tenant_ids)
                    )
                )
                await session.execute(
                    delete(Task).where(Task.id.in_(row.task_id for row in tenant_ids))
                )
                await session.execute(
                    delete(Workspace).where(
                        Workspace.id.in_(row.workspace_id for row in tenant_ids)
                    )
                )
                await session.execute(
                    delete(User).where(
                        User.id.in_(
                            (
                                rows.tenant_a.user_id,
                                rows.tenant_b.user_id,
                                rows.platform_actor_id,
                            )
                        )
                    )
                )

                for table_name, trigger_name in IMMUTABLE_FIXTURE_TRIGGERS:
                    await session.execute(
                        text(f'ALTER TABLE "{table_name}" ENABLE TRIGGER "{trigger_name}"')
                    )
                await session.commit()
            except Exception:
                await session.rollback()
                raise


def _tenant_expected_ids(rows: TenantCollaborationRows) -> dict[str, UUID]:
    return {
        "tasks": rows.task_id,
        "task_participants": rows.participant_id,
        "task_review_rounds": rows.review_round_id,
        "task_events": rows.event_id,
        "task_reminder_rules": rows.reminder_rule_id,
        "task_reminder_outbox": rows.reminder_outbox_id,
        "task_reminder_events": rows.reminder_event_id,
        "task_reminder_command_receipts": rows.reminder_receipt_id,
    }


def _tenant_insert_factories(
    rows: TenantCollaborationRows,
    *,
    label: str,
) -> dict[str, Callable[[], object]]:
    now = datetime.now(UTC)
    return {
        "tasks": lambda: Task(
            workspace_id=rows.workspace_id,
            matter_id=rows.matter_id,
            assigned_to=rows.user_id,
            title=f"{label} task",
            status=TaskStatus.pending,
            priority=TaskPriority.media,
            created_by_user_id=rows.user_id,
        ),
        "task_participants": lambda: TaskParticipant(
            workspace_id=rows.workspace_id,
            matter_id=rows.matter_id,
            task_id=rows.task_id,
            membership_id=rows.membership_id,
            user_id=rows.user_id,
            role=TaskParticipantRole.observer,
            state=TaskParticipantState.removed,
            required_for_completion=False,
            label_snapshot=f"{label} participant",
            active=False,
            deactivated_at=now,
            created_by_user_id=rows.user_id,
        ),
        "task_review_rounds": lambda: TaskReviewRound(
            workspace_id=rows.workspace_id,
            matter_id=rows.matter_id,
            task_id=rows.task_id,
            round_number=2,
            reviewer_user_id=rows.user_id,
            state=TaskReviewState.pending,
            submitted_by_user_id=rows.user_id,
        ),
        "task_events": lambda: TaskEvent(
            workspace_id=rows.workspace_id,
            matter_id=rows.matter_id,
            task_id=rows.task_id,
            actor_user_id=rows.user_id,
            actor_label_snapshot=f"{label} actor",
            actor_role_snapshot=WorkspaceRole.workspace_owner.value,
            actor_type="human",
            event_type=TaskEventType.comment_added,
            before_data=None,
            after_data={"comment": True},
            changed_paths=["comment"],
            comment=f"{label} PostgreSQL RLS proof.",
            reason=f"{label} PostgreSQL RLS proof.",
            reason_source="manual",
            correlation_id=f"{label}-{uuid4()}",
            session_type="normal",
        ),
        "task_reminder_rules": lambda: TaskReminderRule(
            workspace_id=rows.workspace_id,
            matter_id=rows.matter_id,
            task_id=rows.task_id,
            relative_to=TaskReminderRelativeTo.general_due,
            offset_minutes_before=120,
            channel=TaskReminderChannel.email,
            recipient=TaskReminderRecipient.specific_user,
            recipient_user_id=rows.user_id,
            recipient_key=f"user:{rows.user_id}",
            state=TaskReminderRuleState.scheduled,
            active=True,
            version=1,
            created_by_user_id=rows.user_id,
        ),
        "task_reminder_outbox": lambda: TaskReminderOutbox(
            workspace_id=rows.workspace_id,
            matter_id=rows.matter_id,
            task_id=rows.task_id,
            rule_id=rows.reminder_rule_id,
            rule_version=1,
            task_version=1,
            recipient_user_id=rows.user_id,
            deadline_at=now + timedelta(days=2),
            scheduled_for=now + timedelta(days=1),
            channel=TaskReminderChannel.email,
            status=TaskReminderDeliveryStatus.pending,
            dedupe_key=f"{label}:{uuid4()}",
            correlation_id=f"{label}:{uuid4()}",
            attempts=0,
            available_at=now,
        ),
        "task_reminder_events": lambda: TaskReminderEvent(
            workspace_id=rows.workspace_id,
            matter_id=rows.matter_id,
            task_id=rows.task_id,
            rule_id=rows.reminder_rule_id,
            outbox_id=rows.reminder_outbox_id,
            actor_user_id=rows.user_id,
            event_type=TaskReminderEventType.updated,
            before_data={"state": "scheduled"},
            after_data={"state": "scheduled"},
            reason=f"{label} PostgreSQL RLS proof.",
            correlation_id=f"{label}:{uuid4()}",
            session_type="normal",
            resource_version_before=1,
            resource_version_after=2,
        ),
        "task_reminder_command_receipts": lambda: TaskReminderCommandReceipt(
            workspace_id=rows.workspace_id,
            matter_id=rows.matter_id,
            task_id=rows.task_id,
            rule_id=rows.reminder_rule_id,
            idempotency_key=f"{label}:{uuid4()}",
            operation="update",
            request_sha256="f" * 64,
            response_status=200,
            response_data={"ok": True},
        ),
    }


def _sqlstate(error: DBAPIError) -> str | None:
    original = error.orig
    cause = getattr(original, "__cause__", None)
    return (
        getattr(original, "sqlstate", None)
        or getattr(original, "pgcode", None)
        or getattr(cause, "sqlstate", None)
        or getattr(cause, "pgcode", None)
    )


@pytest.mark.asyncio
async def test_task_organization_rls_blocks_cross_workspace_reads_and_writes(
    postgres_engines: PostgresEngines,
    collaboration_rows: CollaborationRows,
) -> None:
    """Lists and boards are tenant data even though they never alter task status."""

    owner = collaboration_rows.tenant_a
    token = uuid4().hex[:10]
    async with postgres_engines.app_sessions() as session:
        await set_workspace_context(session, owner.workspace_id)
        task_list = TaskList(
            workspace_id=owner.workspace_id,
            name=f"RLS list {token}",
            normalized_name=f"rls list {token}",
            created_by_user_id=owner.user_id,
        )
        board = TaskBoard(
            workspace_id=owner.workspace_id,
            name=f"RLS board {token}",
            normalized_name=f"rls board {token}",
            created_by_user_id=owner.user_id,
        )
        session.add_all((task_list, board))
        await session.flush()
        column = TaskBoardColumn(
            workspace_id=owner.workspace_id,
            board_id=board.id,
            name="A fazer",
            normalized_name="a fazer",
            position=0,
        )
        session.add(column)
        await session.flush()
        membership = TaskListMembership(
            workspace_id=owner.workspace_id,
            list_id=task_list.id,
            task_id=owner.task_id,
            created_by_user_id=owner.user_id,
        )
        placement = TaskBoardPlacement(
            workspace_id=owner.workspace_id,
            board_id=board.id,
            column_id=column.id,
            task_id=owner.task_id,
            moved_by_user_id=owner.user_id,
        )
        session.add_all((membership, placement))
        await session.commit()
        ids = {
            "task_lists": task_list.id,
            "task_list_memberships": membership.id,
            "task_boards": board.id,
            "task_board_columns": column.id,
            "task_board_placements": placement.id,
        }

    intruder = collaboration_rows.tenant_b
    async with postgres_engines.app_sessions() as session:
        await set_workspace_context(session, intruder.workspace_id)
        for table_name, model in TASK_ORGANIZATION_TENANT_MODELS.items():
            assert await session.scalar(
                select(func.count()).select_from(model).where(model.id == ids[table_name])
            ) == 0
            result = await session.execute(
                update(model)
                .where(model.id == ids[table_name])
                .values(updated_at=datetime.now(UTC))
            )
            assert result.rowcount == 0
        await session.commit()

    async with postgres_engines.app_sessions() as session:
        await set_workspace_context(session, intruder.workspace_id)
        session.add(
            TaskList(
                workspace_id=owner.workspace_id,
                name=f"Cross tenant {token}",
                normalized_name=f"cross tenant {token}",
                created_by_user_id=intruder.user_id,
            )
        )
        with pytest.raises(DBAPIError) as captured:
            await session.flush()
        assert _sqlstate(captured.value) == "42501"


@pytest.mark.asyncio
async def test_tenant_task_collaboration_and_reminders_are_cross_workspace_isolated(
    postgres_engines: PostgresEngines,
    collaboration_rows: CollaborationRows,
) -> None:
    async with postgres_engines.app_sessions() as session:
        for model in TENANT_MODELS.values():
            assert await session.scalar(select(func.count()).select_from(model)) == 0

    async with postgres_engines.app_sessions() as session:
        await set_workspace_context(session, collaboration_rows.tenant_a.workspace_id)
        expected = _tenant_expected_ids(collaboration_rows.tenant_a)
        for table_name, model in TENANT_MODELS.items():
            assert set((await session.scalars(select(model.id))).all()) == {expected[table_name]}

            can_select, can_insert, can_update, can_delete, can_truncate = (
                await session.execute(
                    text(
                        "SELECT has_table_privilege(current_user, :table_name, 'SELECT'), "
                        "has_table_privilege(current_user, :table_name, 'INSERT'), "
                        "has_table_privilege(current_user, :table_name, 'UPDATE'), "
                        "has_table_privilege(current_user, :table_name, 'DELETE'), "
                        "has_table_privilege(current_user, :table_name, 'TRUNCATE')"
                    ),
                    {"table_name": table_name},
                )
            ).one()
            mutable = table_name in MUTABLE_TENANT_MODELS
            assert (can_select, can_insert, can_update, can_delete, can_truncate) == (
                True,
                True,
                mutable,
                mutable,
                False,
            )

        own_factories = _tenant_insert_factories(
            collaboration_rows.tenant_a,
            label="same-workspace",
        )
        for factory in own_factories.values():
            candidate = factory()
            session.add(candidate)
            await session.flush()
            assert getattr(candidate, "id") is not None
            await session.rollback()

        own_mutations = (
            (Task, collaboration_rows.tenant_a.task_id, {"title": "Authorized update"}),
            (
                TaskParticipant,
                collaboration_rows.tenant_a.participant_id,
                {"label_snapshot": "Authorized update"},
            ),
            (
                TaskReviewRound,
                collaboration_rows.tenant_a.review_round_id,
                {"decision_comment": "Authorized update"},
            ),
            (
                TaskReminderRule,
                collaboration_rows.tenant_a.reminder_rule_id,
                {"version": 2},
            ),
            (
                TaskReminderOutbox,
                collaboration_rows.tenant_a.reminder_outbox_id,
                {"attempts": 1},
            ),
        )
        for model, own_id, values in own_mutations:
            own_update = await session.execute(
                update(model).where(model.id == own_id).values(**values)
            )
            assert own_update.rowcount == 1
            await session.rollback()

        for table_name, model in MUTABLE_TENANT_MODELS.items():
            candidate = own_factories[table_name]()
            session.add(candidate)
            await session.flush()
            own_delete = await session.execute(
                delete(model).where(model.id == getattr(candidate, "id"))
            )
            assert own_delete.rowcount == 1
            await session.rollback()

        foreign_factories = _tenant_insert_factories(
            collaboration_rows.tenant_b,
            label="cross-workspace",
        )
        for factory in foreign_factories.values():
            session.add(factory())
            with pytest.raises(DBAPIError) as exc_info:
                await session.flush()
            assert _sqlstate(exc_info.value) == "42501"
            await session.rollback()

        foreign_mutations = (
            (Task, collaboration_rows.tenant_b.task_id, {"title": "Hidden"}),
            (TaskParticipant, collaboration_rows.tenant_b.participant_id, {"active": False}),
            (
                TaskReviewRound,
                collaboration_rows.tenant_b.review_round_id,
                {"decision_comment": "Hidden"},
            ),
            (TaskReminderRule, collaboration_rows.tenant_b.reminder_rule_id, {"version": 2}),
            (TaskReminderOutbox, collaboration_rows.tenant_b.reminder_outbox_id, {"attempts": 1}),
        )
        for model, foreign_id, values in foreign_mutations:
            hidden_update = await session.execute(
                update(model).where(model.id == foreign_id).values(**values)
            )
            assert hidden_update.rowcount == 0
            await session.rollback()

            hidden_delete = await session.execute(delete(model).where(model.id == foreign_id))
            assert hidden_delete.rowcount == 0
            await session.rollback()

    async with postgres_engines.app_sessions() as session:
        await set_workspace_context(session, collaboration_rows.tenant_b.workspace_id)
        expected = _tenant_expected_ids(collaboration_rows.tenant_b)
        for table_name, model in TENANT_MODELS.items():
            assert set((await session.scalars(select(model.id))).all()) == {expected[table_name]}


@pytest.mark.asyncio
async def test_tenant_collaboration_ledgers_are_append_only_for_runtime_and_migrator(
    postgres_engines: PostgresEngines,
    collaboration_rows: CollaborationRows,
) -> None:
    own_ids = {
        "task_events": collaboration_rows.tenant_a.event_id,
        "task_reminder_events": collaboration_rows.tenant_a.reminder_event_id,
        "task_reminder_command_receipts": collaboration_rows.tenant_a.reminder_receipt_id,
    }
    async with postgres_engines.app_sessions() as session:
        await set_workspace_context(session, collaboration_rows.tenant_a.workspace_id)
        for table_name, model in APPEND_ONLY_MODELS.items():
            can_update, can_delete, can_truncate = (
                await session.execute(
                    text(
                        "SELECT has_table_privilege(current_user, :table_name, 'UPDATE'), "
                        "has_table_privilege(current_user, :table_name, 'DELETE'), "
                        "has_table_privilege(current_user, :table_name, 'TRUNCATE')"
                    ),
                    {"table_name": table_name},
                )
            ).one()
            assert (can_update, can_delete, can_truncate) == (False, False, False)

            with pytest.raises(DBAPIError) as exc_info:
                await session.execute(
                    update(model)
                    .where(model.id == own_ids[table_name])
                    .values(id=own_ids[table_name])
                )
            assert _sqlstate(exc_info.value) == "42501"
            await session.rollback()

            with pytest.raises(DBAPIError) as exc_info:
                await session.execute(delete(model).where(model.id == own_ids[table_name]))
            assert _sqlstate(exc_info.value) == "42501"
            await session.rollback()

            with pytest.raises(DBAPIError) as exc_info:
                # CASCADE bypasses the foreign-key precheck so this assertion
                # exercises the append-only TRUNCATE trigger itself.  A plain
                # TRUNCATE can now fail earlier because task attachments retain
                # an audited reference to task_events.
                await session.execute(text(f'TRUNCATE TABLE "{table_name}" CASCADE'))
            assert _sqlstate(exc_info.value) == "42501"
            await session.rollback()

    async with postgres_engines.admin_sessions() as session:
        for table_name, model in APPEND_ONLY_MODELS.items():
            await set_workspace_context(session, collaboration_rows.tenant_a.workspace_id)
            with pytest.raises(DBAPIError, match=rf"{table_name} is immutable"):
                await session.execute(
                    update(model)
                    .where(model.id == own_ids[table_name])
                    .values(id=own_ids[table_name])
                )
            await session.rollback()

            await set_workspace_context(session, collaboration_rows.tenant_a.workspace_id)
            with pytest.raises(DBAPIError, match=rf"{table_name} is immutable"):
                await session.execute(delete(model).where(model.id == own_ids[table_name]))
            await session.rollback()

            await set_workspace_context(session, collaboration_rows.tenant_a.workspace_id)
            with pytest.raises(DBAPIError, match=rf"{table_name} is immutable"):
                await session.execute(text(f'TRUNCATE TABLE "{table_name}" CASCADE'))
            await session.rollback()


@pytest.mark.asyncio
async def test_platform_tasks_are_blackbox_isolated_and_events_are_append_only(
    postgres_engines: PostgresEngines,
    collaboration_rows: CollaborationRows,
) -> None:
    async with postgres_engines.app_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(PlatformTask)) == 0
        assert await session.scalar(select(func.count()).select_from(PlatformTaskEvent)) == 0

    async with postgres_engines.app_sessions() as session:
        await set_workspace_context(session, collaboration_rows.tenant_a.workspace_id)
        assert await session.scalar(select(func.count()).select_from(PlatformTask)) == 0
        assert await session.scalar(select(func.count()).select_from(PlatformTaskEvent)) == 0

    async with postgres_engines.app_sessions() as session:
        await set_platform_control_context(
            session,
            actor_user_id=collaboration_rows.platform_actor_id,
            module="sales",
            action="view",
        )
        assert await session.scalar(select(func.count()).select_from(PlatformTask)) == 0
        assert await session.scalar(select(func.count()).select_from(PlatformTaskEvent)) == 0
        session.add(
            PlatformTask(
                title="Unrelated capability must not create a platform task",
                priority=PlatformTaskPriority.P2,
                owner_user_id=collaboration_rows.platform_actor_id,
                created_by_user_id=collaboration_rows.platform_actor_id,
                workspace_id=collaboration_rows.tenant_a.workspace_id,
            )
        )
        with pytest.raises(DBAPIError) as exc_info:
            await session.flush()
        assert _sqlstate(exc_info.value) == "42501"
        await session.rollback()

    async with postgres_engines.app_sessions() as session:
        await set_platform_control_context(
            session,
            actor_user_id=collaboration_rows.platform_actor_id,
            module="platform_tasks",
            action="create",
        )
        authorized_task = PlatformTask(
            title="Authorized runtime platform task",
            priority=PlatformTaskPriority.P2,
            owner_user_id=collaboration_rows.platform_actor_id,
            created_by_user_id=collaboration_rows.platform_actor_id,
            workspace_id=collaboration_rows.tenant_a.workspace_id,
        )
        session.add(authorized_task)
        await session.flush()
        authorized_event = PlatformTaskEvent(
            task_id=authorized_task.id,
            actor_user_id=collaboration_rows.platform_actor_id,
            actor_label_snapshot="Authorized platform actor",
            actor_role_snapshot="platform_admin",
            is_platform_root=False,
            event_type=PlatformTaskEventType.created,
            before_data=None,
            after_data={"priority": PlatformTaskPriority.P2.value},
            changed_paths=["priority"],
            comment=None,
            reason="Authorized runtime platform-task create proof.",
            correlation_id=f"platform-task-authorized-{uuid4()}",
            session_type="normal",
            resource_version_before=None,
            resource_version_after=1,
            occurred_at=datetime.now(UTC),
        )
        session.add(authorized_event)
        await session.flush()
        assert authorized_task.id is not None
        assert authorized_event.id is not None
        await session.rollback()

    async with postgres_engines.app_sessions() as session:
        await set_platform_control_context(
            session,
            actor_user_id=collaboration_rows.platform_actor_id,
            module="platform_tasks",
            action="edit",
        )
        authorized_update = await session.execute(
            update(PlatformTask)
            .where(PlatformTask.id == collaboration_rows.platform_task_id)
            .values(title="Authorized runtime platform task update")
        )
        assert authorized_update.rowcount == 1
        await session.rollback()

    async with postgres_engines.app_sessions() as session:
        await set_platform_control_context(
            session,
            actor_user_id=collaboration_rows.platform_actor_id,
            module="platform_tasks",
            action="view",
        )
        assert set((await session.scalars(select(PlatformTask.id))).all()) == {
            collaboration_rows.platform_task_id
        }
        assert set((await session.scalars(select(PlatformTaskEvent.id))).all()) == {
            collaboration_rows.platform_event_id
        }
        task_select, task_insert, task_update, task_delete, task_truncate = (
            await session.execute(
                text(
                    "SELECT has_table_privilege(current_user, 'platform_tasks', 'SELECT'), "
                    "has_table_privilege(current_user, 'platform_tasks', 'INSERT'), "
                    "has_table_privilege(current_user, 'platform_tasks', 'UPDATE'), "
                    "has_table_privilege(current_user, 'platform_tasks', 'DELETE'), "
                    "has_table_privilege(current_user, 'platform_tasks', 'TRUNCATE')"
                )
            )
        ).one()
        assert (task_select, task_insert, task_update, task_delete, task_truncate) == (
            True,
            True,
            True,
            False,
            False,
        )
        event_update, event_delete, event_truncate = (
            await session.execute(
                text(
                    "SELECT has_table_privilege(current_user, 'platform_task_events', 'UPDATE'), "
                    "has_table_privilege(current_user, 'platform_task_events', 'DELETE'), "
                    "has_table_privilege(current_user, 'platform_task_events', 'TRUNCATE')"
                )
            )
        ).one()
        assert (event_update, event_delete, event_truncate) == (False, False, False)

        view_only_update = await session.execute(
            update(PlatformTask)
            .where(PlatformTask.id == collaboration_rows.platform_task_id)
            .values(title="View capability must not edit")
        )
        assert view_only_update.rowcount == 0

        session.add(
            PlatformTaskEvent(
                task_id=collaboration_rows.platform_task_id,
                actor_user_id=collaboration_rows.platform_actor_id,
                actor_label_snapshot="View-only platform actor",
                actor_role_snapshot="platform_admin",
                is_platform_root=False,
                event_type=PlatformTaskEventType.comment_added,
                before_data=None,
                after_data={"comment": True},
                changed_paths=["comment"],
                comment="View capability must not append an event.",
                reason="Blackbox view-only RLS denial proof.",
                correlation_id=f"platform-task-view-denied-{uuid4()}",
                session_type="normal",
                resource_version_before=1,
                resource_version_after=2,
                occurred_at=datetime.now(UTC),
            )
        )
        with pytest.raises(DBAPIError) as exc_info:
            await session.flush()
        assert _sqlstate(exc_info.value) == "42501"
        await session.rollback()

    async with postgres_engines.app_sessions() as session:
        await set_platform_control_context(
            session,
            actor_user_id=collaboration_rows.platform_actor_id,
            module="platform_tasks",
            action="view",
        )
        with pytest.raises(DBAPIError) as exc_info:
            await session.execute(
                update(PlatformTaskEvent)
                .where(PlatformTaskEvent.id == collaboration_rows.platform_event_id)
                .values(id=collaboration_rows.platform_event_id)
            )
        assert _sqlstate(exc_info.value) == "42501"
        await session.rollback()

    async with postgres_engines.app_sessions() as session:
        await set_platform_control_context(
            session,
            actor_user_id=collaboration_rows.platform_actor_id,
            module="platform_tasks",
            action="view",
        )
        with pytest.raises(DBAPIError) as exc_info:
            await session.execute(
                delete(PlatformTaskEvent).where(
                    PlatformTaskEvent.id == collaboration_rows.platform_event_id
                )
            )
        assert _sqlstate(exc_info.value) == "42501"
        await session.rollback()

    async with postgres_engines.app_sessions() as session:
        await set_platform_control_context(
            session,
            actor_user_id=collaboration_rows.platform_actor_id,
            module="platform_tasks",
            action="view",
        )
        with pytest.raises(DBAPIError) as exc_info:
            await session.execute(text('TRUNCATE TABLE "platform_task_events"'))
        assert _sqlstate(exc_info.value) == "42501"
        await session.rollback()

    async with postgres_engines.admin_sessions() as session:
        await set_platform_control_context(
            session,
            actor_user_id=collaboration_rows.platform_actor_id,
            module="platform_root",
            action="manage",
            is_platform_root=True,
        )
        hidden_update = await session.execute(
            update(PlatformTaskEvent)
            .where(PlatformTaskEvent.id == collaboration_rows.platform_event_id)
            .values(id=collaboration_rows.platform_event_id)
        )
        assert hidden_update.rowcount == 0
        await session.rollback()

        await set_platform_control_context(
            session,
            actor_user_id=collaboration_rows.platform_actor_id,
            module="platform_root",
            action="manage",
            is_platform_root=True,
        )
        hidden_delete = await session.execute(
            delete(PlatformTaskEvent).where(
                PlatformTaskEvent.id == collaboration_rows.platform_event_id
            )
        )
        assert hidden_delete.rowcount == 0
        await session.rollback()

        await set_platform_control_context(
            session,
            actor_user_id=collaboration_rows.platform_actor_id,
            module="platform_root",
            action="manage",
            is_platform_root=True,
        )
        with pytest.raises(DBAPIError, match="platform_task_events is immutable"):
            await session.execute(text('TRUNCATE TABLE "platform_task_events"'))
        await session.rollback()

    # Platform event UPDATE/DELETE policies intentionally do not exist, so the
    # forced-RLS migrator sees zero rows.  The fixture-only cluster role bypasses
    # RLS and proves that the physical trigger remains a second append-only
    # boundary for every mutation operation.
    async with postgres_engines.master_sessions() as session:
        with pytest.raises(DBAPIError, match="platform_task_events is immutable"):
            await session.execute(
                update(PlatformTaskEvent)
                .where(PlatformTaskEvent.id == collaboration_rows.platform_event_id)
                .values(id=collaboration_rows.platform_event_id)
            )
        await session.rollback()

        with pytest.raises(DBAPIError, match="platform_task_events is immutable"):
            await session.execute(
                delete(PlatformTaskEvent).where(
                    PlatformTaskEvent.id == collaboration_rows.platform_event_id
                )
            )
        await session.rollback()

        with pytest.raises(DBAPIError, match="platform_task_events is immutable"):
            await session.execute(text('TRUNCATE TABLE "platform_task_events"'))
        await session.rollback()


@pytest.mark.asyncio
async def test_platform_task_service_links_force_rls_ticket_and_restores_task_context(
    postgres_engines: PostgresEngines,
    collaboration_rows: CollaborationRows,
) -> None:
    now = datetime.now(UTC)
    token = uuid4().hex[:12]
    ticket = SupportTicket(
        public_number=f"SUP-{token.upper()}",
        workspace_id=collaboration_rows.tenant_a.workspace_id,
        reporter_user_id=collaboration_rows.tenant_a.user_id,
        ticket_type=SupportTicketType.problem,
        priority=SupportTicketPriority.normal,
        severity=SupportTicketSeverity.medium,
        queue=SupportTicketQueue.support,
        status=SupportTicketStatus.new,
        subject="Ticket protegido por FORCE RLS",
        description="Prova de vínculo por contexto support:view temporário.",
        technical_context={},
        idempotency_key=f"platform-task-ticket-{token}",
        first_response_due_at=now + timedelta(hours=4),
        resolution_due_at=now + timedelta(days=1),
    )
    async with postgres_engines.master_sessions() as session:
        session.add(ticket)
        await session.commit()

    created_task_id: UUID | None = None
    try:
        async with postgres_engines.app_sessions() as session:
            await set_platform_control_context(
                session,
                actor_user_id=collaboration_rows.platform_actor_id,
                module="platform_tasks",
                action="create",
            )
            actor = await session.get(User, collaboration_rows.platform_actor_id)
            assert actor is not None
            mutation = await PlatformTaskService(session).create(
                data=PlatformTaskCreate(
                    title="Investigar ticket em contexto restrito",
                    owner_user_id=actor.id,
                    linked_ticket_id=ticket.id,
                    reason="Comprovar vínculo governado sob FORCE RLS.",
                ),
                context=PlatformTaskMutationContext.for_actor(
                    actor,
                    correlation_id=f"platform-task-ticket-link:{token}",
                ),
            )
            created_task_id = mutation.task.id
            scope, actor_setting, action = (
                await session.execute(
                    text(
                        "SELECT "
                        "current_setting('app.current_platform_scope', true), "
                        "current_setting('app.current_platform_actor_id', true), "
                        "current_setting('app.current_platform_action', true)"
                    )
                )
            ).one()
            assert scope == "platform_tasks"
            assert actor_setting == str(actor.id)
            assert action == "platform_tasks:create"
            assert mutation.workspace_id == collaboration_rows.tenant_a.workspace_id
            assert mutation.task.linked_ticket_id == ticket.id
            await session.commit()

        async with postgres_engines.master_sessions() as session:
            persisted = await session.get(PlatformTask, created_task_id)
            assert persisted is not None
            assert persisted.workspace_id == collaboration_rows.tenant_a.workspace_id
            assert persisted.linked_ticket_id == ticket.id
    finally:
        async with postgres_engines.master_sessions() as session:
            await session.execute(
                text(
                    'ALTER TABLE "platform_task_events" '
                    'DISABLE TRIGGER "trg_platform_task_events_immutable"'
                )
            )
            if created_task_id is not None:
                await session.execute(
                    delete(PlatformTaskEvent).where(PlatformTaskEvent.task_id == created_task_id)
                )
                await session.execute(
                    delete(PlatformTask).where(PlatformTask.id == created_task_id)
                )
            await session.execute(delete(SupportTicket).where(SupportTicket.id == ticket.id))
            await session.execute(
                text(
                    'ALTER TABLE "platform_task_events" '
                    'ENABLE TRIGGER "trg_platform_task_events_immutable"'
                )
            )
            await session.commit()


@pytest.mark.asyncio
async def test_platform_task_service_concurrent_updates_return_locked_audit_snapshots(
    postgres_engines: PostgresEngines,
    collaboration_rows: CollaborationRows,
) -> None:
    async def update_once(label: str) -> PlatformTaskMutationResult:
        async with postgres_engines.app_sessions() as session:
            await set_platform_control_context(
                session,
                actor_user_id=collaboration_rows.platform_actor_id,
                module="platform_tasks",
                action="edit",
            )
            actor = await session.get(User, collaboration_rows.platform_actor_id)
            assert actor is not None
            try:
                mutation = await PlatformTaskService(session).update(
                    task_id=collaboration_rows.platform_task_id,
                    data=PlatformTaskUpdate(
                        expected_version=1,
                        title=f"Atualização concorrente {label}",
                        reason=f"Comprovar snapshot transacional da escrita {label}.",
                    ),
                    context=PlatformTaskMutationContext.for_actor(
                        actor,
                        correlation_id=f"platform-task-concurrency:{label}:{uuid4()}",
                    ),
                )
                await session.commit()
                return mutation
            except Exception:
                await session.rollback()
                raise

    outcomes = await asyncio.gather(
        update_once("A"),
        update_once("B"),
        return_exceptions=True,
    )
    successes = [outcome for outcome in outcomes if isinstance(outcome, PlatformTaskMutationResult)]
    failures = [outcome for outcome in outcomes if isinstance(outcome, PlatformTaskError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].code == "PLATFORM-TASK-VERSION-CONFLICT"

    mutation = successes[0]
    assert mutation.before is not None
    assert mutation.after is not None
    assert mutation.before["version"] == 1
    assert mutation.after["version"] == 2
    assert mutation.before["title"] != mutation.after["title"]

    async with postgres_engines.master_sessions() as session:
        persisted = await session.get(PlatformTask, collaboration_rows.platform_task_id)
        assert persisted is not None
        assert persisted.version == 2
        assert persisted.title == mutation.after["title"]
        event = await session.scalar(
            select(PlatformTaskEvent).where(
                PlatformTaskEvent.task_id == collaboration_rows.platform_task_id,
                PlatformTaskEvent.correlation_id.like("platform-task-concurrency:%"),
            )
        )
        assert event is not None
        assert event.before_data == mutation.before
        assert event.after_data == mutation.after
