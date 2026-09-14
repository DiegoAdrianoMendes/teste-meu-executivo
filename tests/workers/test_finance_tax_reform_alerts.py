"""Tax-reform D-window scheduler: real human calls, no fake phone proof."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.agent_platform import HumanDecision, HumanDeliveryStatus, HumanNotificationDelivery
from app.models.finance_dimensional import FinanceTaxProfile, FinanceTaxReformAlert
from app.models.omnichannel_v6 import WorkspaceTeam, WorkspaceTeamMember
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.workers.finance_tasks import _async_tax_reform_alert_tick


def _worker_session_factory(db_session):
    return async_sessionmaker(
        db_session.bind, class_=AsyncSession, expire_on_commit=False
    )


async def _add_tax_accountant(db_session, auth_context):
    accountant = User(
        name="Contador Tributário",
        email=f"accountant-{uuid4().hex}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="CT",
        color="#123456",
        is_active=True,
        phone="+5511999991111",
    )
    db_session.add(accountant)
    await db_session.flush()
    membership = WorkspaceMembership(
        workspace_id=auth_context.workspace_id,
        user_id=accountant.id,
        role=WorkspaceRole.operator,
        status=MembershipStatus.active,
        access_package="accounting",
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
            user_id=accountant.id,
            membership_id=membership.id,
        )
    )
    return accountant


async def _add_tax_profile(db_session, auth_context, *, version: int = 1):
    profile = FinanceTaxProfile(
        workspace_id=auth_context.workspace_id,
        regime="lucro_presumido",
        tax_rate=Decimal("12.5"),
        effective_from=date(2026, 1, 1),
        sources=[{"type": "contador", "reference": "responsável configurado"}],
        reform_calendar={"confirmationRequired": True},
        settings={"humanValidationRequired": True},
        version=version,
    )
    db_session.add(profile)
    return profile


@pytest.mark.asyncio
async def test_tax_reform_scheduler_calls_qualified_accountant_and_owner_once(
    db_session, auth_context
):
    workspace = await db_session.get(Workspace, auth_context.workspace_id)
    assert workspace is not None
    workspace.owner_whatsapp_phone = "+5511999990000"
    accountant = await _add_tax_accountant(db_session, auth_context)
    profile = await _add_tax_profile(db_session, auth_context)
    await db_session.commit()

    first = await _async_tax_reform_alert_tick(
        reference_date="2026-07-05", session_factory=_worker_session_factory(db_session)
    )
    assert first == {"called": 1, "existing": 0, "blocked": 0, "failed": 0}

    alerts = list(
        (
            await db_session.scalars(
                select(FinanceTaxReformAlert).where(
                    FinanceTaxReformAlert.workspace_id == auth_context.workspace_id
                )
            )
        ).all()
    )
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.status == "called"
    assert alert.profile_version == profile.version
    assert alert.offset_days == 180
    decision = await db_session.get(HumanDecision, alert.human_decision_id)
    assert decision is not None
    assert decision.assigned_user_id == accountant.id
    assert decision.owner_user_id == auth_context.user_id
    assert decision.options["purpose"] == "finance_tax_reform_scheduled_review"

    deliveries = list(
        (
            await db_session.scalars(
                select(HumanNotificationDelivery).where(
                    HumanNotificationDelivery.decision_id == decision.id
                )
            )
        ).all()
    )
    owner_phone = next(
        item
        for item in deliveries
        if item.recipient_user_id == auth_context.user_id and item.channel == "phone"
    )
    assert owner_phone.status == HumanDeliveryStatus.pending
    assert owner_phone.provider_receipt_id is None
    assert any(
        item.recipient_user_id == auth_context.user_id and item.channel == "in_app"
        for item in deliveries
    )

    second = await _async_tax_reform_alert_tick(
        reference_date="2026-07-05", session_factory=_worker_session_factory(db_session)
    )
    assert second == {"called": 0, "existing": 1, "blocked": 0, "failed": 0}
    assert len(
        list(
            (
                await db_session.scalars(
                    select(FinanceTaxReformAlert).where(
                        FinanceTaxReformAlert.workspace_id == auth_context.workspace_id
                    )
                )
            ).all()
        )
    ) == 1


@pytest.mark.asyncio
async def test_tax_reform_scheduler_new_profile_version_creates_new_review_window(
    db_session, auth_context
):
    await _add_tax_accountant(db_session, auth_context)
    profile = await _add_tax_profile(db_session, auth_context)
    await db_session.commit()

    first = await _async_tax_reform_alert_tick(
        reference_date="2026-07-05", session_factory=_worker_session_factory(db_session)
    )
    assert first["called"] == 1
    profile.version = 2
    await db_session.commit()

    second = await _async_tax_reform_alert_tick(
        reference_date="2026-07-05", session_factory=_worker_session_factory(db_session)
    )
    assert second == {"called": 1, "existing": 0, "blocked": 0, "failed": 0}
    alerts = list(
        (
            await db_session.scalars(
                select(FinanceTaxReformAlert.profile_version)
                .where(FinanceTaxReformAlert.workspace_id == auth_context.workspace_id)
                .order_by(FinanceTaxReformAlert.profile_version)
            )
        ).all()
    )
    assert alerts == [1, 2]


@pytest.mark.asyncio
async def test_tax_reform_scheduler_records_blocked_routing_once_without_waiting(
    db_session, auth_context
):
    await _add_tax_profile(db_session, auth_context)
    owner_membership = await db_session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == auth_context.workspace_id,
            WorkspaceMembership.user_id == auth_context.user_id,
        )
    )
    assert owner_membership is not None
    owner_membership.status = MembershipStatus.suspended
    await db_session.commit()

    first = await _async_tax_reform_alert_tick(
        reference_date="2026-07-05", session_factory=_worker_session_factory(db_session)
    )
    assert first == {"called": 0, "existing": 0, "blocked": 1, "failed": 0}
    assert (
        await db_session.scalar(
            select(HumanDecision).where(
                HumanDecision.workspace_id == auth_context.workspace_id
            )
        )
    ) is None
    blocked = await db_session.scalar(
        select(FinanceTaxReformAlert).where(
            FinanceTaxReformAlert.workspace_id == auth_context.workspace_id
        )
    )
    assert blocked is not None
    assert blocked.status == "blocked"

    second = await _async_tax_reform_alert_tick(
        reference_date="2026-07-05", session_factory=_worker_session_factory(db_session)
    )
    assert second == {"called": 0, "existing": 1, "blocked": 0, "failed": 0}


# ── S30: contexto por workspace e isolamento de falha ─────────────────────


@pytest.mark.asyncio
async def test_tick_abre_um_contexto_por_workspace_e_isola_falhas(
    db_session, auth_context, monkeypatch
):
    import app.workers.finance_tasks as finance_tasks
    from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
    from app.services.finance_tax_reform_alert_service import FinanceTaxReformAlertService

    outro = Workspace(
        name="Outro escritório",
        slug=f"outro-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    inativo = Workspace(
        name="Inativo",
        slug=f"inativo-{uuid4().hex[:8]}",
        status=WorkspaceStatus.suspended,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=False,
        settings={},
    )
    db_session.add_all((outro, inativo))
    await db_session.commit()

    contextos: list = []

    async def espiao(session, workspace_id):
        contextos.append(workspace_id)

    monkeypatch.setattr(finance_tasks, "set_workspace_context", espiao)
    original = FinanceTaxReformAlertService.schedule_workspace

    async def quebra_no_outro(self, workspace_id, *, as_of=None):
        if workspace_id == outro.id:
            raise RuntimeError("perfil corrompido")
        return await original(self, workspace_id, as_of=as_of)

    monkeypatch.setattr(FinanceTaxReformAlertService, "schedule_workspace", quebra_no_outro)

    totals = await _async_tax_reform_alert_tick(
        reference_date="2026-07-05", session_factory=_worker_session_factory(db_session)
    )

    assert set(contextos) >= {auth_context.workspace_id, outro.id}
    assert inativo.id not in contextos, "workspace inativo não entra no ciclo"
    assert totals["failed"] == 1, "a falha de um workspace é contada, não derruba o tick"
