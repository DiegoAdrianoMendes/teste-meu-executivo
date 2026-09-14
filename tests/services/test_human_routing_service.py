from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.agent_platform import (
    HumanDecision,
    HumanDecisionStatus,
    HumanNotificationDelivery,
)
from app.models.omnichannel_v6 import WorkspaceTeam, WorkspaceTeamMember
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.schemas.staff_availability import ResponsibilityResolutionRead
from app.services.agent_platform_service import (
    HumanRoutingService,
    consume_human_decision,
    finite_routing_path,
)
from app.services.staff_availability_service import StaffAvailabilityService


def test_routing_is_cycle_free_and_ends_at_owner():
    assert finite_routing_path(["legal", "legal", "manager"], owner="owner", max_hops=4) == [
        "legal",
        "manager",
        "owner",
    ]


def test_routing_reserves_last_hop_for_owner():
    assert finite_routing_path(["a", "b", "c", "d"], owner="owner", max_hops=3) == [
        "a",
        "b",
        "owner",
    ]


def test_owner_is_not_duplicated_and_single_hop_still_calls_owner():
    assert finite_routing_path(["owner", "owner"], owner="owner", max_hops=1) == ["owner"]


def test_missing_owner_never_creates_an_unbounded_path():
    assert finite_routing_path(["a", "a", "b", "c"], owner=None, max_hops=2) == [
        "a",
        "b",
    ]


@pytest.mark.asyncio
async def test_decision_selects_role_and_skill_and_records_owner_phone_call(
    db_session, auth_context
):
    """A skill-qualified operator is selected; the workspace owner is always watched."""
    workspace = await db_session.get(Workspace, auth_context.workspace_id)
    workspace.owner_whatsapp_phone = "+5511999990000"
    operator = User(
        name="Operador Financeiro",
        email=f"operator-{uuid4().hex}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="OF",
        color="#123456",
        is_active=True,
        phone="+5511999991111",
    )
    db_session.add(operator)
    await db_session.flush()
    membership = WorkspaceMembership(
        workspace_id=auth_context.workspace_id,
        user_id=operator.id,
        role=WorkspaceRole.operator,
        status=MembershipStatus.active,
    )
    db_session.add(membership)
    await db_session.flush()
    team = WorkspaceTeam(
        workspace_id=auth_context.workspace_id,
        name="Financeiro",
        slug="financeiro",
        team_type="financeiro",
        is_active=True,
    )
    db_session.add(team)
    await db_session.flush()
    db_session.add(
        WorkspaceTeamMember(
            workspace_id=auth_context.workspace_id,
            team_id=team.id,
            user_id=operator.id,
            membership_id=membership.id,
        )
    )
    await db_session.flush()

    decision = await HumanRoutingService(db_session).create_decision(
        workspace_id=auth_context.workspace_id,
        decision_key=f"skill-{uuid4().hex}",
        options={"approve": True},
        required_roles=[WorkspaceRole.operator.value],
        required_skills=["financeiro"],
        sla_minutes=10,
    )
    assert decision.assigned_user_id == operator.id
    assert decision.owner_user_id == auth_context.user_id
    deliveries = list(
        (
            await db_session.scalars(
                select(HumanNotificationDelivery).where(
                    HumanNotificationDelivery.decision_id == decision.id
                )
            )
        ).all()
    )
    assert {
        (
            item.recipient_user_id,
            item.channel,
            item.status.value if hasattr(item.status, "value") else item.status,
        )
        for item in deliveries
    } >= {
        (operator.id, "in_app", "delivered"),
        (operator.id, "phone", "pending"),
        (auth_context.user_id, "in_app", "delivered"),
        (auth_context.user_id, "phone", "pending"),
    }


@pytest.mark.asyncio
async def test_skill_only_routes_to_skilled_human_before_owner(db_session, auth_context):
    specialist = User(
        name="Especialista Tributária",
        email=f"tax-{uuid4().hex}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="ET",
        color="#123456",
        is_active=True,
    )
    db_session.add(specialist)
    await db_session.flush()
    membership = WorkspaceMembership(
        workspace_id=auth_context.workspace_id,
        user_id=specialist.id,
        role=WorkspaceRole.operator,
        status=MembershipStatus.active,
    )
    db_session.add(membership)
    await db_session.flush()
    team = WorkspaceTeam(
        workspace_id=auth_context.workspace_id,
        name="Tributário",
        slug="tributario",
        team_type="tributario",
        is_active=True,
    )
    db_session.add(team)
    await db_session.flush()
    db_session.add(
        WorkspaceTeamMember(
            workspace_id=auth_context.workspace_id,
            team_id=team.id,
            user_id=specialist.id,
            membership_id=membership.id,
        )
    )
    await db_session.flush()

    decision = await HumanRoutingService(db_session).create_decision(
        workspace_id=auth_context.workspace_id,
        decision_key=f"skill-only-{uuid4().hex}",
        options={},
        required_roles=[],
        required_skills=["tributario"],
    )

    assert decision.assigned_user_id == specialist.id
    assert decision.routing_path[0] == str(specialist.id)
    assert decision.routing_path[-1] == str(auth_context.user_id)


@pytest.mark.asyncio
async def test_routing_catalog_exposes_only_live_workspace_routes_and_owner_fallback(
    db_session, auth_context
):
    """The UI catalog cannot offer a static/typed specialty with no human."""
    initial = await HumanRoutingService(db_session).routing_catalog(
        workspace_id=auth_context.workspace_id
    )
    assert initial["directOwnerOnly"] is True
    assert initial["roles"] == [
        {
            "key": WorkspaceRole.workspace_owner.value,
            "label": "Dono do escritório",
            "routableCount": 1,
        }
    ]
    assert initial["skills"] == []
    assert initial["teams"] == []
    assert initial["ownerFallback"]["available"] is True
    assert "diretamente para o dono" in initial["ownerFallback"]["reason"]
    assert all(
        "id" not in item for group in ("roles", "skills", "teams") for item in initial[group]
    )

    specialist = User(
        name="Especialista Tributária",
        email=f"catalog-tax-{uuid4().hex}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="ET",
        color="#123456",
        is_active=True,
    )
    db_session.add(specialist)
    await db_session.flush()
    membership = WorkspaceMembership(
        workspace_id=auth_context.workspace_id,
        user_id=specialist.id,
        role=WorkspaceRole.finance_manager,
        status=MembershipStatus.active,
    )
    db_session.add(membership)
    await db_session.flush()
    team = WorkspaceTeam(
        workspace_id=auth_context.workspace_id,
        name="Tributário",
        slug="tributario",
        team_type="tributario",
        is_active=True,
    )
    db_session.add(team)
    await db_session.flush()
    db_session.add(
        WorkspaceTeamMember(
            workspace_id=auth_context.workspace_id,
            team_id=team.id,
            user_id=specialist.id,
            membership_id=membership.id,
            is_available=True,
        )
    )
    await db_session.flush()

    catalog = await HumanRoutingService(db_session).routing_catalog(
        workspace_id=auth_context.workspace_id
    )
    assert catalog["directOwnerOnly"] is False
    assert {item["key"] for item in catalog["roles"]} >= {
        WorkspaceRole.workspace_owner.value,
        WorkspaceRole.finance_manager.value,
    }
    assert catalog["skills"] == [{"key": "tributario", "label": "Tributario", "routableCount": 1}]
    assert catalog["teams"] == [{"key": "tributario", "label": "Tributário", "routableCount": 1}]
    assert "competência_inventada" not in {item["key"] for item in catalog["skills"]}


@pytest.mark.asyncio
async def test_routing_catalog_hides_specialist_redirected_to_owner(
    db_session, auth_context, monkeypatch
):
    """A redirect to the owner is a fallback, not a selectable specialty."""
    specialist = User(
        name="Especialista Ausente",
        email=f"catalog-away-{uuid4().hex}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="EA",
        color="#123456",
        is_active=True,
    )
    db_session.add(specialist)
    await db_session.flush()
    membership = WorkspaceMembership(
        workspace_id=auth_context.workspace_id,
        user_id=specialist.id,
        role=WorkspaceRole.finance_manager,
        status=MembershipStatus.active,
    )
    db_session.add(membership)
    await db_session.flush()
    team = WorkspaceTeam(
        workspace_id=auth_context.workspace_id,
        name="Tributário",
        slug="tributario",
        team_type="tributario",
        is_active=True,
    )
    db_session.add(team)
    await db_session.flush()
    db_session.add(
        WorkspaceTeamMember(
            workspace_id=auth_context.workspace_id,
            team_id=team.id,
            user_id=specialist.id,
            membership_id=membership.id,
            is_available=True,
        )
    )
    await db_session.flush()

    original = HumanRoutingService._resolve_routable_user

    async def owner_redirect(self, *, candidate_user_id, owner_user_id, **kwargs):
        if candidate_user_id == specialist.id:
            return owner_user_id
        return await original(
            self,
            candidate_user_id=candidate_user_id,
            owner_user_id=owner_user_id,
            **kwargs,
        )

    monkeypatch.setattr(HumanRoutingService, "_resolve_routable_user", owner_redirect)
    catalog = await HumanRoutingService(db_session).routing_catalog(
        workspace_id=auth_context.workspace_id
    )

    assert catalog["directOwnerOnly"] is True
    assert catalog["roles"] == [
        {
            "key": WorkspaceRole.workspace_owner.value,
            "label": "Dono do escritório",
            "routableCount": 1,
        }
    ]
    assert catalog["skills"] == []
    assert catalog["teams"] == []


@pytest.mark.asyncio
async def test_escalation_is_finite_owner_is_last_and_terminal_call_is_expired(
    monkeypatch,
):
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    calls = []
    monkeypatch.setattr(
        HumanRoutingService,
        "_record_call",
        AsyncMock(side_effect=lambda *args, **kwargs: calls.append((args[1], kwargs["event_key"]))),
    )
    owner = uuid4()
    first = uuid4()
    decision = SimpleNamespace(
        status=HumanDecisionStatus.pending,
        routing_path=[str(first), str(owner)],
        escalation_level=0,
        max_hops=2,
        owner_user_id=owner,
        assigned_user_id=first,
        due_at=datetime.now(UTC),
        workspace_id=uuid4(),
        decision_key="finite-escalation",
        id=uuid4(),
    )
    router = HumanRoutingService(db)
    assert await router.escalate(decision) is True
    assert decision.assigned_user_id == owner
    assert [recipient for recipient, _ in calls] == [owner]
    assert await router.escalate(decision) is False
    assert decision.status == HumanDecisionStatus.expired
    assert calls == [
        (owner, "escalated_1"),
        (owner, "terminal_owner_2"),
    ], "terminal expiry must create the owner's durable final alert"


@pytest.mark.asyncio
async def test_decision_consume_success_is_single_use_and_expiry_is_not_waiting():
    decision = HumanDecision(
        workspace_id=uuid4(),
        decision_key="single-use",
        status=HumanDecisionStatus.pending,
        options={},
        # SQLite may return timezone columns without tzinfo.  Runtime expiry
        # checks must normalize that representation instead of raising 500.
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5),
    )
    db = MagicMock()
    db.scalar = AsyncMock(return_value=decision)
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    consumed = await consume_human_decision(
        db,
        decision.id,
        workspace_id=decision.workspace_id,
        user_id=uuid4(),
        choice={"ok": True},
    )
    assert consumed is decision
    assert decision.status == HumanDecisionStatus.decided
    assert (
        await consume_human_decision(
            db,
            decision.id,
            workspace_id=decision.workspace_id,
            user_id=uuid4(),
            choice={"ok": False},
        )
        is None
    )

    expired = HumanDecision(
        workspace_id=decision.workspace_id,
        decision_key="expired",
        status=HumanDecisionStatus.pending,
        options={},
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    db.scalar = AsyncMock(return_value=expired)
    assert (
        await consume_human_decision(
            db,
            expired.id,
            workspace_id=expired.workspace_id,
            user_id=uuid4(),
            choice={"ok": True},
        )
        is None
    )
    assert expired.status == HumanDecisionStatus.expired


@pytest.mark.asyncio
async def test_explicit_human_must_be_workspace_member_and_skill_qualified(
    db_session, auth_context
):
    operator = User(
        name="Operador sem skill",
        email=f"noskill-{uuid4().hex}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="NS",
        color="#123456",
        is_active=True,
    )
    db_session.add(operator)
    await db_session.flush()
    membership = WorkspaceMembership(
        workspace_id=auth_context.workspace_id,
        user_id=operator.id,
        role=WorkspaceRole.operator,
        status=MembershipStatus.active,
    )
    db_session.add(membership)
    await db_session.flush()
    with pytest.raises(Exception, match="required skills"):
        await HumanRoutingService(db_session).create_decision(
            workspace_id=auth_context.workspace_id,
            decision_key=f"bad-skill-{uuid4().hex}",
            options={},
            required_roles=[WorkspaceRole.operator.value],
            required_skills=["juridico"],
            requested_user_id=operator.id,
        )

    with pytest.raises(Exception, match="required role"):
        await HumanRoutingService(db_session).create_decision(
            workspace_id=auth_context.workspace_id,
            decision_key=f"bad-role-{uuid4().hex}",
            options={},
            required_roles=[WorkspaceRole.finance_manager.value],
            required_skills=[],
            requested_user_id=operator.id,
        )

    foreign_workspace = Workspace(
        name="Outro",
        slug=f"outro-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
    )
    db_session.add(foreign_workspace)
    await db_session.flush()
    foreign = User(
        name="Fora do workspace",
        email=f"foreign-{uuid4().hex}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="FW",
        color="#123456",
        is_active=True,
    )
    db_session.add(foreign)
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=foreign_workspace.id,
            user_id=foreign.id,
            role=WorkspaceRole.operator,
            status=MembershipStatus.active,
        )
    )
    await db_session.flush()
    with pytest.raises(Exception, match="not active in this workspace"):
        await HumanRoutingService(db_session).create_decision(
            workspace_id=auth_context.workspace_id,
            decision_key=f"foreign-{uuid4().hex}",
            options={},
            required_roles=[],
            required_skills=[],
            requested_user_id=foreign.id,
        )


@pytest.mark.asyncio
async def test_hierarchy_fallback_selects_active_manager_when_no_specialist(
    db_session, auth_context
):
    manager = User(
        name="Gestor Financeiro",
        email=f"manager-{uuid4().hex}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="GF",
        color="#123456",
        is_active=True,
    )
    db_session.add(manager)
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=auth_context.workspace_id,
            user_id=manager.id,
            role=WorkspaceRole.finance_manager,
            status=MembershipStatus.active,
        )
    )
    await db_session.flush()
    decision = await HumanRoutingService(db_session).create_decision(
        workspace_id=auth_context.workspace_id,
        decision_key=f"hierarchy-{uuid4().hex}",
        options={},
        required_roles=["accounting"],
        required_skills=[],
        sla_minutes=5,
    )
    assert decision.assigned_user_id == manager.id
    assert decision.routing_path[-1] == str(auth_context.user_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_reason",
    [
        "unavailable_without_active_redirect",
        "redirect_cycle",
        "redirect_hop_limit",
        "invalid_redirect_target",
    ],
)
async def test_unavailable_specialist_is_not_assigned_and_owner_is_explicit_fallback(
    db_session, auth_context, monkeypatch, terminal_reason
):
    """Availability terminal states cannot create an uncalled pending wait."""
    workspace = await db_session.get(Workspace, auth_context.workspace_id)
    workspace.owner_whatsapp_phone = "+5511999990000"
    specialist = User(
        name="Especialista Indisponível",
        email=f"unavailable-{uuid4().hex}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="EI",
        color="#123456",
        is_active=True,
    )
    db_session.add(specialist)
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=auth_context.workspace_id,
            user_id=specialist.id,
            role=WorkspaceRole.operator,
            status=MembershipStatus.active,
        )
    )
    await db_session.flush()

    async def unavailable_resolution(self, *, workspace_id, responsible_user_id, **_kwargs):
        return ResponsibilityResolutionRead(
            workspace_id=workspace_id,
            requested_user_id=responsible_user_id,
            effective_user_id=None,
            effective_team_id=None,
            evaluated_at=datetime.now(UTC),
            changed=True,
            reason=terminal_reason,
            path=[responsible_user_id],
        )

    monkeypatch.setattr(StaffAvailabilityService, "resolve_responsibility", unavailable_resolution)
    decision = await HumanRoutingService(db_session).create_decision(
        workspace_id=auth_context.workspace_id,
        decision_key=f"unavailable-fallback-{uuid4().hex}",
        options={"reason": "Dados conflitantes exigem dono"},
        required_roles=[WorkspaceRole.operator.value],
        required_skills=[],
        sla_minutes=5,
    )

    assert decision.assigned_user_id == auth_context.user_id
    assert decision.routing_path == [str(auth_context.user_id)]
    deliveries = list(
        (
            await db_session.scalars(
                select(HumanNotificationDelivery).where(
                    HumanNotificationDelivery.decision_id == decision.id
                )
            )
        ).all()
    )
    assert {
        (
            item.channel,
            item.status.value if hasattr(item.status, "value") else item.status,
        )
        for item in deliveries
    } == {
        ("in_app", "delivered"),
        ("phone", "pending"),
    }


@pytest.mark.asyncio
async def test_unavailable_specialist_advances_to_available_manager_then_owner(
    db_session, auth_context, monkeypatch
):
    """Function/skill matching is preferred, then the finite authority chain."""
    specialist = User(
        name="Especialista Indisponível",
        email=f"specialist-{uuid4().hex}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="EI",
        color="#123456",
        is_active=True,
    )
    manager = User(
        name="Gestora Disponível",
        email=f"manager-available-{uuid4().hex}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="GD",
        color="#123456",
        is_active=True,
    )
    db_session.add_all([specialist, manager])
    await db_session.flush()
    db_session.add_all(
        [
            WorkspaceMembership(
                workspace_id=auth_context.workspace_id,
                user_id=specialist.id,
                role=WorkspaceRole.operator,
                status=MembershipStatus.active,
            ),
            WorkspaceMembership(
                workspace_id=auth_context.workspace_id,
                user_id=manager.id,
                role=WorkspaceRole.finance_manager,
                status=MembershipStatus.active,
            ),
        ]
    )
    await db_session.flush()

    async def availability_by_candidate(self, *, workspace_id, responsible_user_id, **_kwargs):
        available = responsible_user_id == manager.id
        return ResponsibilityResolutionRead(
            workspace_id=workspace_id,
            requested_user_id=responsible_user_id,
            effective_user_id=responsible_user_id if available else None,
            effective_team_id=None,
            evaluated_at=datetime.now(UTC),
            changed=False,
            reason="responsible_available" if available else "redirect_hop_limit",
            path=[responsible_user_id],
        )

    monkeypatch.setattr(
        StaffAvailabilityService, "resolve_responsibility", availability_by_candidate
    )
    decision = await HumanRoutingService(db_session).create_decision(
        workspace_id=auth_context.workspace_id,
        decision_key=f"manager-fallback-{uuid4().hex}",
        options={},
        required_roles=[WorkspaceRole.operator.value],
        required_skills=[],
        sla_minutes=5,
    )
    assert decision.assigned_user_id == manager.id
    assert decision.routing_path == [str(manager.id), str(auth_context.user_id)]


@pytest.mark.asyncio
async def test_escalation_skips_newly_unavailable_human_before_owner(monkeypatch):
    """A stale escalation route is rechecked rather than assigning unavailable staff."""
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    owner = uuid4()
    unavailable = uuid4()
    calls = []
    monkeypatch.setattr(
        HumanRoutingService,
        "_record_call",
        AsyncMock(side_effect=lambda *args, **kwargs: calls.append((args[1], kwargs["event_key"]))),
    )

    async def unavailable_resolution(self, *, workspace_id, responsible_user_id, **_kwargs):
        return ResponsibilityResolutionRead(
            workspace_id=workspace_id,
            requested_user_id=responsible_user_id,
            effective_user_id=None,
            effective_team_id=None,
            evaluated_at=datetime.now(UTC),
            changed=True,
            reason="redirect_cycle",
            path=[responsible_user_id],
        )

    monkeypatch.setattr(StaffAvailabilityService, "resolve_responsibility", unavailable_resolution)
    decision = SimpleNamespace(
        status=HumanDecisionStatus.pending,
        routing_path=[str(uuid4()), str(unavailable), str(owner)],
        escalation_level=0,
        max_hops=3,
        owner_user_id=owner,
        assigned_user_id=uuid4(),
        due_at=datetime.now(UTC),
        workspace_id=uuid4(),
        decision_key="skip-unavailable",
        id=uuid4(),
    )
    assert await HumanRoutingService(db).escalate(decision) is True
    assert decision.assigned_user_id == owner
    assert decision.escalation_level == 2
    assert [recipient for recipient, _ in calls] == [owner]
