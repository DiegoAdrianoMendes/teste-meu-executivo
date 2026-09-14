from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.models.client import Client
from app.models.matter import Matter, PracticeArea
from app.models.meeting import MeetingNature
from app.models.task import TaskParticipantRole
from app.models.user import AgentType, User, UserRole
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.schemas.task import TaskCreate, TaskParticipantWrite
from app.services.agenda_service import AgendaService
from app.services.task_service import TaskMutationContext, TaskService


async def _matter(db: AsyncSession, workspace_id) -> Matter:
    client = Client(workspace_id=workspace_id, name="Cliente Agenda")
    db.add(client)
    await db.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client.id,
        demand_type="Caso Agenda",
        practice_area=PracticeArea.civel,
        case_number="CASO-AGENDA-1",
    )
    db.add(matter)
    await db.flush()
    return matter


async def _reviewer(db: AsyncSession, workspace_id) -> User:
    user = User(
        name="Revisora Agenda",
        email=f"revisora-agenda-{uuid4().hex[:8]}@example.com",
        initials="RA",
        color="#334155",
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
async def test_unified_agenda_projects_three_task_clocks_without_fallback(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    matter = await _matter(db_session, auth_context.workspace_id)
    reviewer = await _reviewer(db_session, auth_context.workspace_id)
    start = datetime.now(UTC).replace(microsecond=0)
    owner_due = start + timedelta(days=1)
    reviewer_due = start + timedelta(days=2)
    review_due = start + timedelta(days=2, hours=12)
    task_due = start + timedelta(days=3)
    task = await TaskService(db_session).create(
        matter.id,
        TaskCreate(
            title="Preparar manifestação",
            due_at=task_due,
            review_required=True,
            review_due_at=review_due,
            participants=[
                TaskParticipantWrite(
                    user_id=auth_context.user_id,
                    role=TaskParticipantRole.owner,
                    due_at=owner_due,
                ),
                TaskParticipantWrite(
                    user_id=reviewer.id,
                    role=TaskParticipantRole.reviewer,
                    due_at=reviewer_due,
                ),
            ],
        ),
        auth_context.workspace_id,
        context=TaskMutationContext(
            actor_user_id=auth_context.user_id,
            correlation_id="agenda-task-clock:test",
        ),
    )

    events = await AgendaService(db_session).unified_agenda(
        auth_context.workspace_id,
        include_tasks=True,
        include_process_deadlines=False,
        can_view_sensitive=True,
        natureza=MeetingNature.juridico,
        start=start,
        end=start + timedelta(days=4),
    )

    task_events = [event for event in events if event.task_id == task.id]
    assert {event.source for event in task_events} == {
        "task_due",
        "task_participant_due",
        "task_review_due",
    }
    general = next(event for event in task_events if event.source == "task_due")
    review = next(event for event in task_events if event.source == "task_review_due")
    participant_dates = {
        event.starts_at for event in task_events if event.source == "task_participant_due"
    }
    assert general.starts_at == task_due
    assert review.starts_at == review_due
    assert participant_dates == {owner_due, reviewer_due}
    assert all(event.matter_label == "CASO-AGENDA-1" for event in task_events)
    assert all(event.client_label == "Cliente Agenda" for event in task_events)
    assert all(event.is_inviolable is False for event in task_events)

    commercial = await AgendaService(db_session).unified_agenda(
        auth_context.workspace_id,
        include_tasks=True,
        include_process_deadlines=False,
        can_view_sensitive=True,
        natureza=MeetingNature.comercial,
        start=start,
        end=start + timedelta(days=4),
    )
    assert all(event.task_id is None for event in commercial)
