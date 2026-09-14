"""Focused API coverage for native conversation response metrics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from app.models.klm_operations import (
    MessageAuthorType,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
    OmnichannelThread,
    ThreadStatus,
)
from app.models.omnichannel_v6 import WorkspaceTeam
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole


async def _attendant(db_session, workspace_id: UUID, *, name: str) -> User:
    user = User(
        name=name,
        email=f"{uuid4().hex}@example.com",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="AT",
        color="#111827",
        is_active=True,
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=workspace_id,
            user_id=user.id,
            role=WorkspaceRole.operator,
            status=MembershipStatus.active,
            is_default=False,
        )
    )
    await db_session.flush()
    return user


async def _team(db_session, workspace_id: UUID, *, name: str) -> WorkspaceTeam:
    team = WorkspaceTeam(
        workspace_id=workspace_id,
        name=name,
        slug=f"team-{uuid4().hex[:12]}",
        team_type="service",
        color="#1a9948",
        capacity=10,
        routing_strategy="manual",
        schedule={},
        is_active=True,
    )
    db_session.add(team)
    await db_session.flush()
    return team


async def _thread(
    db_session,
    workspace_id: UUID,
    *,
    created_at: datetime,
    assigned_human_id: UUID | None = None,
    assigned_team_id: UUID | None = None,
    status: ThreadStatus = ThreadStatus.open,
    closed_at: datetime | None = None,
    first_response_due_at: datetime | None = None,
    sla_due_at: datetime | None = None,
) -> OmnichannelThread:
    thread = OmnichannelThread(
        workspace_id=workspace_id,
        assigned_human_id=assigned_human_id,
        assigned_team_id=assigned_team_id,
        primary_channel=OmnichannelChannel.whatsapp,
        status=status,
        queue="service",
        priority=0,
        is_favorite=False,
        version=1,
        score=0,
        unread_count=0,
        first_response_due_at=first_response_due_at,
        sla_due_at=sla_due_at,
        closed_at=closed_at,
        policy_state={},
        cost_state={},
        thread_metadata={},
        created_at=created_at,
        updated_at=created_at,
    )
    db_session.add(thread)
    await db_session.flush()
    return thread


async def _message(
    db_session,
    thread: OmnichannelThread,
    *,
    occurred_at: datetime,
    direction: MessageDirection,
    author_type: MessageAuthorType,
    audience: str = "client_private",
) -> OmnichannelMessage:
    message = OmnichannelMessage(
        workspace_id=thread.workspace_id,
        thread_id=thread.id,
        author_user_id=thread.assigned_human_id if direction == MessageDirection.outbound else None,
        channel=OmnichannelChannel.whatsapp,
        direction=direction,
        author_type=author_type,
        author_label="Cliente" if author_type == MessageAuthorType.client else "Escritório",
        body="Mensagem de teste",
        audience=audience,
        content_type="text",
        transport="local",
        idempotency_key=f"response-metric:{uuid4()}",
        delivery_status=(
            MessageDeliveryStatus.received
            if direction == MessageDirection.inbound
            else MessageDeliveryStatus.sent
        ),
        occurred_at=occurred_at,
        message_metadata={},
    )
    db_session.add(message)
    await db_session.flush()
    return message


@pytest.mark.asyncio
async def test_response_metrics_states_sla_and_aggregates(
    client,
    db_session,
    auth_context,
    active_subscription,
):
    now = datetime(2026, 7, 22, 15, 0, tzinfo=UTC)
    attendant = await _attendant(db_session, auth_context.workspace_id, name="Ana Atendente")
    team = await _team(db_session, auth_context.workspace_id, name="Equipe Saúde")

    pending = await _thread(
        db_session,
        auth_context.workspace_id,
        created_at=now - timedelta(hours=1),
        assigned_human_id=attendant.id,
        assigned_team_id=team.id,
        first_response_due_at=now - timedelta(minutes=5),
    )
    await _message(
        db_session,
        pending,
        occurred_at=now - timedelta(minutes=10),
        direction=MessageDirection.inbound,
        author_type=MessageAuthorType.client,
    )
    await _message(
        db_session,
        pending,
        occurred_at=now - timedelta(minutes=2),
        direction=MessageDirection.internal,
        author_type=MessageAuthorType.human,
        audience="internal_only",
    )
    await _message(
        db_session,
        pending,
        occurred_at=now - timedelta(minutes=1),
        direction=MessageDirection.outbound,
        author_type=MessageAuthorType.human,
        audience="internal_only",
    )

    waiting_client = await _thread(
        db_session,
        auth_context.workspace_id,
        created_at=now - timedelta(hours=2),
        assigned_human_id=attendant.id,
        assigned_team_id=team.id,
        first_response_due_at=now - timedelta(minutes=10),
    )
    await _message(
        db_session,
        waiting_client,
        occurred_at=now - timedelta(minutes=20),
        direction=MessageDirection.inbound,
        author_type=MessageAuthorType.client,
    )
    await _message(
        db_session,
        waiting_client,
        occurred_at=now - timedelta(minutes=15),
        direction=MessageDirection.outbound,
        author_type=MessageAuthorType.human,
    )

    responded = await _thread(
        db_session,
        auth_context.workspace_id,
        created_at=now - timedelta(hours=2),
        assigned_human_id=attendant.id,
        assigned_team_id=team.id,
        status=ThreadStatus.closed,
        closed_at=now - timedelta(minutes=5),
        first_response_due_at=now - timedelta(minutes=5),
    )
    await _message(
        db_session,
        responded,
        occurred_at=now - timedelta(minutes=12),
        direction=MessageDirection.inbound,
        author_type=MessageAuthorType.client,
    )
    await _message(
        db_session,
        responded,
        occurred_at=now - timedelta(minutes=10),
        direction=MessageDirection.outbound,
        author_type=MessageAuthorType.human,
    )

    without_messages = await _thread(
        db_session,
        auth_context.workspace_id,
        created_at=now - timedelta(minutes=30),
    )
    await _message(
        db_session,
        without_messages,
        occurred_at=now - timedelta(minutes=3),
        direction=MessageDirection.internal,
        author_type=MessageAuthorType.human,
        audience="internal_only",
    )
    await db_session.commit()

    with patch(
        "app.services.conversation_response_metrics_service._utc_now",
        return_value=now,
    ):
        response = await client.get("/api/v1/klm/omnichannel/response-metrics")

    assert response.status_code == 200, response.text
    payload = response.json()
    rows = {row["threadId"]: row for row in payload["items"]}
    assert rows[str(pending.id)]["state"] == "cliente_aguardando_escritorio"
    assert rows[str(pending.id)]["durationSeconds"] == 600
    assert rows[str(pending.id)]["slaStatus"] == "overdue"
    assert rows[str(pending.id)]["overdueSeconds"] == 300
    assert rows[str(waiting_client.id)]["state"] == "escritorio_aguardando_cliente"
    assert rows[str(waiting_client.id)]["firstResponseSeconds"] == 300
    assert rows[str(responded.id)]["state"] == "respondido"
    assert rows[str(responded.id)]["firstResponseSeconds"] == 120
    assert rows[str(without_messages.id)]["state"] == "sem_mensagem"
    assert rows[str(without_messages.id)]["durationSeconds"] is None

    account = payload["account"]["metrics"]
    assert account["conversations"] == 4
    assert account["pending"] == 1
    assert account["averageWaitSeconds"] == 600
    assert account["medianWaitSeconds"] == 600
    assert account["p90WaitSeconds"] == 600
    assert account["longestWaitSeconds"] == 600
    assert account["averageFirstResponseSeconds"] == 210
    assert account["slaEvaluated"] == 3
    assert account["withinSla"] == 2
    assert account["withinSlaRate"] == 66.67
    assert payload["byTeam"][0]["groupName"] == "Equipe Saúde"
    assert payload["byAttendant"][0]["groupName"] == "Ana Atendente"


@pytest.mark.asyncio
async def test_response_metrics_custom_filters_sort_and_limit(
    client,
    db_session,
    auth_context,
    active_subscription,
):
    now = datetime(2026, 7, 22, 15, 0, tzinfo=UTC)
    attendant = await _attendant(db_session, auth_context.workspace_id, name="Bia Atendente")
    team = await _team(db_session, auth_context.workspace_id, name="Equipe Contencioso")
    waits = (timedelta(hours=2), timedelta(minutes=30))
    expected_ids: list[str] = []
    for wait in waits:
        thread = await _thread(
            db_session,
            auth_context.workspace_id,
            created_at=now - timedelta(days=1),
            assigned_human_id=attendant.id,
            assigned_team_id=team.id,
            first_response_due_at=now - timedelta(minutes=5),
        )
        expected_ids.append(str(thread.id))
        await _message(
            db_session,
            thread,
            occurred_at=now - wait,
            direction=MessageDirection.inbound,
            author_type=MessageAuthorType.client,
        )
    old = await _thread(
        db_session,
        auth_context.workspace_id,
        created_at=now - timedelta(days=40),
        assigned_human_id=attendant.id,
        assigned_team_id=team.id,
    )
    await _message(
        db_session,
        old,
        occurred_at=now - timedelta(days=40),
        direction=MessageDirection.inbound,
        author_type=MessageAuthorType.client,
    )
    await db_session.commit()

    params = [
        ("period", "custom"),
        ("date_from", (now - timedelta(days=2)).isoformat()),
        ("date_to", now.isoformat()),
        ("attendant_id", str(attendant.id)),
        ("team_id", str(team.id)),
        ("status", "cliente_aguardando_escritorio"),
        ("sla", "overdue"),
        ("sort_by", "duration"),
        ("sort_direction", "desc"),
        ("limit", "1"),
    ]
    with patch(
        "app.services.conversation_response_metrics_service._utc_now",
        return_value=now,
    ):
        response = await client.get(
            "/api/v1/klm/omnichannel/response-metrics",
            params=params,
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 2
    assert payload["hasMore"] is True
    assert len(payload["items"]) == 1
    assert payload["items"][0]["threadId"] == expected_ids[0]
    assert payload["account"]["metrics"]["conversations"] == 2
    assert payload["filters"]["attendantIds"] == [str(attendant.id)]
    assert payload["filters"]["teamIds"] == [str(team.id)]


@pytest.mark.asyncio
async def test_response_metrics_never_reads_another_workspace(
    client,
    db_session,
    auth_context,
    active_subscription,
):
    now = datetime(2026, 7, 22, 15, 0, tzinfo=UTC)
    own = await _thread(
        db_session,
        auth_context.workspace_id,
        created_at=now - timedelta(minutes=10),
    )
    await _message(
        db_session,
        own,
        occurred_at=now - timedelta(minutes=5),
        direction=MessageDirection.inbound,
        author_type=MessageAuthorType.client,
    )

    foreign_workspace = Workspace(
        name="Workspace Estrangeiro",
        slug=f"foreign-{uuid4().hex[:12]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(foreign_workspace)
    await db_session.flush()
    foreign = await _thread(
        db_session,
        foreign_workspace.id,
        created_at=now - timedelta(days=2),
    )
    await _message(
        db_session,
        foreign,
        occurred_at=now - timedelta(days=1),
        direction=MessageDirection.inbound,
        author_type=MessageAuthorType.client,
    )
    await db_session.commit()

    with patch(
        "app.services.conversation_response_metrics_service._utc_now",
        return_value=now,
    ):
        response = await client.get("/api/v1/klm/omnichannel/response-metrics")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["threadId"] == str(own.id)
    assert payload["account"]["groupId"] == str(auth_context.workspace_id)


@pytest.mark.asyncio
async def test_response_metrics_rejects_incomplete_custom_period(
    client,
    active_subscription,
):
    response = await client.get(
        "/api/v1/klm/omnichannel/response-metrics",
        params={"period": "custom", "date_from": datetime.now(UTC).isoformat()},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_RESPONSE_METRICS_FILTER"


@pytest.mark.asyncio
async def test_response_metrics_is_available_to_conversation_operators(
    client,
    db_session,
    auth_context,
    active_subscription,
):
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    assert membership is not None
    membership.role = WorkspaceRole.operator
    auth_context.role = WorkspaceRole.operator
    await db_session.commit()

    response = await client.get("/api/v1/klm/omnichannel/response-metrics")

    assert response.status_code == 200, response.text
    assert response.json()["account"]["metrics"]["conversations"] == 0
