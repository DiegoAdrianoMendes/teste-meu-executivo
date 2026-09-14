from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.models.client import Client
from app.models.matter import Matter, PracticeArea
from app.models.omnichannel_v6 import WorkspaceTeam, WorkspaceTeamMember
from app.models.task import TaskStatus
from app.models.user import AgentType, User, UserRole
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.schemas.task import TaskCreate, TaskListScope
from app.services.task_service import TaskMutationContext, TaskService


async def _member(db: AsyncSession, workspace_id, label: str) -> User:
    user = User(
        name=label,
        email=f"team-scope-{uuid4().hex[:12]}@example.com",
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


async def _matter(db: AsyncSession, workspace_id, label: str) -> Matter:
    client = Client(workspace_id=workspace_id, name=f"Cliente {label}")
    db.add(client)
    await db.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client.id,
        demand_type=label,
        practice_area=PracticeArea.civel,
    )
    db.add(matter)
    await db.flush()
    return matter


def _context(actor_id, suffix: str) -> TaskMutationContext:
    return TaskMutationContext(
        actor_user_id=actor_id,
        correlation_id=f"test:team-scope:{suffix}:{uuid4()}",
    )


@pytest.mark.asyncio
async def test_team_scope_uses_canonical_active_team_and_no_team_fails_closed(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    teammate = await _member(db_session, auth_context.workspace_id, "Colega de equipe")
    outsider = await _member(db_session, auth_context.workspace_id, "Sem equipe")
    teammate_membership = await db_session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == auth_context.workspace_id,
            WorkspaceMembership.user_id == teammate.id,
        )
    )
    assert teammate_membership is not None
    team = WorkspaceTeam(
        workspace_id=auth_context.workspace_id,
        name="Equipe Contencioso",
        slug=f"contencioso-{uuid4().hex[:8]}",
        is_active=True,
    )
    db_session.add(team)
    await db_session.flush()
    db_session.add_all(
        [
            WorkspaceTeamMember(
                workspace_id=auth_context.workspace_id,
                team_id=team.id,
                membership_id=auth_context.membership_id,
                user_id=auth_context.user_id,
            ),
            WorkspaceTeamMember(
                workspace_id=auth_context.workspace_id,
                team_id=team.id,
                membership_id=teammate_membership.id,
                user_id=teammate.id,
            ),
        ]
    )
    await db_session.flush()

    service = TaskService(db_session)
    tasks = {}
    for title, owner_id in (
        ("Minha tarefa", auth_context.user_id),
        ("Tarefa do colega", teammate.id),
        ("Tarefa externa", outsider.id),
    ):
        matter = await _matter(db_session, auth_context.workspace_id, title)
        tasks[title] = await service.create(
            matter.id,
            TaskCreate(
                title=title,
                owner_id=owner_id,
                due_at=datetime.now(UTC) - timedelta(days=1),
                ),
                auth_context.workspace_id,
                context=_context(owner_id, title),
            )

    items, cursor, has_more = await service.list_workspace_tasks(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        scope=TaskListScope.team,
        limit=20,
        include_sensitive_identity=True,
    )
    assert {item.task.title for item in items} == {"Minha tarefa", "Tarefa do colega"}
    assert cursor is None
    assert has_more is False

    isolated_items, isolated_cursor, isolated_has_more = await service.list_workspace_tasks(
        workspace_id=auth_context.workspace_id,
        actor_user_id=outsider.id,
        scope=TaskListScope.team,
        limit=20,
        include_sensitive_identity=True,
    )
    assert isolated_items == []
    assert isolated_cursor is None
    assert isolated_has_more is False

    assert (
        await service.get_for_actor(
            tasks["Tarefa do colega"].id,
            auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
        )
    ) is not None
    assert (
        await service.get_for_actor(
            tasks["Tarefa externa"].id,
            auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
        )
    ) is None

    member_options = await service.member_options(
        auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        scope=TaskListScope.team,
        limit=20,
    )
    assert {user_id for user_id, _, _ in member_options} == {
        auth_context.user_id,
        teammate.id,
    }
    isolated_member_options = await service.member_options(
        auth_context.workspace_id,
        actor_user_id=outsider.id,
        scope=TaskListScope.team,
        limit=20,
    )
    assert isolated_member_options == []

    tasks["Tarefa do colega"].status = TaskStatus.blocked
    tasks["Tarefa externa"].status = TaskStatus.blocked
    await db_session.flush()
    for scope in (TaskListScope.overdue, TaskListScope.blocked):
        scoped_items, _, _ = await service.list_workspace_tasks(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            scope=scope,
            limit=20,
            include_sensitive_identity=True,
        )
        assert "Tarefa externa" not in {item.task.title for item in scoped_items}

    tasks["Tarefa do colega"].status = TaskStatus.done
    tasks["Tarefa do colega"].completed_at = datetime.now(UTC)
    tasks["Tarefa externa"].status = TaskStatus.done
    tasks["Tarefa externa"].completed_at = datetime.now(UTC)
    await db_session.flush()
    completed_items, _, _ = await service.list_workspace_tasks(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        scope=TaskListScope.completed,
        limit=20,
        include_sensitive_identity=True,
    )
    assert {item.task.title for item in completed_items} == {"Tarefa do colega"}

    team.is_active = False
    await db_session.flush()
    inactive_team_items, _, _ = await service.list_workspace_tasks(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        scope=TaskListScope.team,
        limit=20,
        include_sensitive_identity=True,
    )
    assert inactive_team_items == []
    assert (
        await service.member_options(
            auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            scope=TaskListScope.team,
            limit=20,
        )
        == []
    )
