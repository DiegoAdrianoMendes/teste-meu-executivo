from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event

from app.models.client import Client
from app.models.legal_demand import (
    LegalDemand,
    LegalDemandNature,
    LegalDemandPhase,
    LegalDemandRisk,
)
from app.models.matter import Matter, PracticeArea
from app.models.staff_availability import (
    AvailabilityExceptionKind,
    AvailabilityExceptionScope,
    AvailabilityMode,
    StaffAvailabilityException,
    StaffAvailabilityProfile,
    StaffRecurringAvailability,
)
from app.models.task import Task, TaskStatus
from app.models.user import AgentType, User, UserRole
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.services.legal_capacity_service import LegalCapacityService
from tests.conftest import test_engine

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)  # segunda-feira


async def _make_client(db_session, workspace_id) -> Client:
    client = Client(workspace_id=workspace_id, name="Cliente Capacidade")
    db_session.add(client)
    await db_session.flush()
    return client


async def _make_matter(db_session, workspace_id, client_id) -> Matter:
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client_id,
        practice_area=PracticeArea.civel,
        demand_type="Caso capacidade",
    )
    db_session.add(matter)
    await db_session.flush()
    return matter


async def _make_member(db_session, workspace_id, *, name: str) -> User:
    user = User(
        name=name,
        email=f"capacidade-{uuid4().hex[:10]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials=name[:2].upper(),
        color="#445566",
        is_active=True,
        is_internal_staff=False,
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


async def _make_demand(
    db_session, workspace_id, client_id, *, responsible_user_id, phase=LegalDemandPhase.em_andamento
) -> LegalDemand:
    demand = LegalDemand(
        workspace_id=workspace_id,
        nature=LegalDemandNature.judicial,
        area="civel",
        client_id=client_id,
        parties=[],
        risk=LegalDemandRisk.medio,
        phase=phase,
        sla={},
        documents_required=[],
        responsible_user_id=responsible_user_id,
    )
    db_session.add(demand)
    await db_session.flush()
    return demand


@pytest.mark.asyncio
async def test_capacity_counts_only_active_load(db_session, auth_context):
    workspace_id = auth_context.workspace_id
    client = await _make_client(db_session, workspace_id)
    matter = await _make_matter(db_session, workspace_id, client.id)
    responsible = await _make_member(db_session, workspace_id, name="Advogada Carga")

    await _make_demand(db_session, workspace_id, client.id, responsible_user_id=responsible.id)
    await _make_demand(db_session, workspace_id, client.id, responsible_user_id=responsible.id)
    await _make_demand(
        db_session,
        workspace_id,
        client.id,
        responsible_user_id=responsible.id,
        phase=LegalDemandPhase.encerrado,
    )
    db_session.add_all(
        [
            Task(
                workspace_id=workspace_id,
                matter_id=matter.id,
                title="Aberta",
                status=TaskStatus.pending,
                assigned_to=responsible.id,
            ),
            Task(
                workspace_id=workspace_id,
                matter_id=matter.id,
                title="Concluída",
                status=TaskStatus.done,
                assigned_to=responsible.id,
            ),
        ]
    )
    await db_session.flush()
    await db_session.commit()

    service = LegalCapacityService(db_session, workspace_id)
    report = await service.get_capacity(now=NOW)

    row = next(r for r in report.por_responsavel if r.responsible_user_id == str(responsible.id))
    assert row.carga_demandas_ativas == 2  # a encerrada não conta
    assert row.carga_tarefas_abertas == 1  # a concluída não conta


@pytest.mark.asyncio
async def test_capacity_reports_configured_weekly_hours(db_session, auth_context):
    workspace_id = auth_context.workspace_id
    with_schedule = await _make_member(db_session, workspace_id, name="Com Agenda")
    without_schedule = await _make_member(db_session, workspace_id, name="Sem Agenda")

    profile = StaffAvailabilityProfile(
        workspace_id=workspace_id,
        staff_user_id=with_schedule.id,
        schedule_configured=True,
        availability_mode=AvailabilityMode.available,
    )
    db_session.add(profile)
    await db_session.flush()
    db_session.add(
        StaffRecurringAvailability(
            workspace_id=workspace_id,
            profile_id=profile.id,
            weekday=0,
            starts_at=time(9, 0),
            ends_at=time(12, 0),
            source_request_id=uuid4(),
        )
    )
    db_session.add(
        StaffRecurringAvailability(
            workspace_id=workspace_id,
            profile_id=profile.id,
            weekday=0,
            starts_at=time(13, 0),
            ends_at=time(18, 0),
            source_request_id=uuid4(),
        )
    )
    await db_session.flush()
    await db_session.commit()

    service = LegalCapacityService(db_session, workspace_id)
    report = await service.get_capacity(now=NOW)

    row_with = next(
        r for r in report.por_responsavel if r.responsible_user_id == str(with_schedule.id)
    )
    row_without = next(
        r for r in report.por_responsavel if r.responsible_user_id == str(without_schedule.id)
    )

    assert row_with.disponibilidade_configurada is True
    assert row_with.horas_semana_configuradas == pytest.approx(8.0)
    assert row_without.disponibilidade_configurada is False
    assert row_without.horas_semana_configuradas is None


@pytest.mark.asyncio
async def test_capacity_away_mode_blocks_availability_now(db_session, auth_context):
    workspace_id = auth_context.workspace_id
    away_member = await _make_member(db_session, workspace_id, name="Ausente")

    db_session.add(
        StaffAvailabilityProfile(
            workspace_id=workspace_id,
            staff_user_id=away_member.id,
            schedule_configured=False,
            availability_mode=AvailabilityMode.away,
            availability_starts_at=NOW - timedelta(hours=1),
            availability_ends_at=NOW + timedelta(hours=1),
        )
    )
    await db_session.flush()
    await db_session.commit()

    service = LegalCapacityService(db_session, workspace_id)
    report = await service.get_capacity(now=NOW)

    row = next(r for r in report.por_responsavel if r.responsible_user_id == str(away_member.id))
    assert row.disponivel_agora is False
    assert row.motivo_disponibilidade == "scheduled_away"


@pytest.mark.asyncio
async def test_capacity_blocking_exception_overrides_availability(db_session, auth_context):
    workspace_id = auth_context.workspace_id
    member = await _make_member(db_session, workspace_id, name="Em Férias")

    db_session.add(
        StaffAvailabilityException(
            workspace_id=workspace_id,
            scope=AvailabilityExceptionScope.staff,
            staff_user_id=member.id,
            kind=AvailabilityExceptionKind.vacation,
            title="Férias",
            starts_at=NOW - timedelta(hours=1),
            ends_at=NOW + timedelta(days=5),
            source_request_id=uuid4(),
        )
    )
    await db_session.flush()
    await db_session.commit()

    service = LegalCapacityService(db_session, workspace_id)
    report = await service.get_capacity(now=NOW)

    row = next(r for r in report.por_responsavel if r.responsible_user_id == str(member.id))
    assert row.disponivel_agora is False
    assert row.motivo_disponibilidade == AvailabilityExceptionKind.vacation.value


@pytest.mark.asyncio
async def test_capacity_groups_by_unit_and_team(db_session, auth_context):
    workspace_id = auth_context.workspace_id
    client = await _make_client(db_session, workspace_id)
    responsible = await _make_member(db_session, workspace_id, name="Advogada Unidade")

    db_session.add(
        LegalDemand(
            workspace_id=workspace_id,
            nature=LegalDemandNature.judicial,
            area="civel",
            client_id=client.id,
            parties=[],
            risk=LegalDemandRisk.medio,
            phase=LegalDemandPhase.em_andamento,
            sla={},
            documents_required=[],
            responsible_user_id=responsible.id,
            unit="Brasília",
            team="Cível",
        )
    )
    db_session.add(
        LegalDemand(
            workspace_id=workspace_id,
            nature=LegalDemandNature.judicial,
            area="civel",
            client_id=client.id,
            parties=[],
            risk=LegalDemandRisk.medio,
            phase=LegalDemandPhase.em_andamento,
            sla={},
            documents_required=[],
            responsible_user_id=responsible.id,
            unit=None,
            team=None,
        )
    )
    await db_session.flush()
    await db_session.commit()

    service = LegalCapacityService(db_session, workspace_id)
    report = await service.get_capacity(now=NOW)

    unidades = {row.key: row.carga_demandas_ativas for row in report.por_unidade}
    equipes = {row.key: row.carga_demandas_ativas for row in report.por_equipe}
    assert unidades.get("Brasília") == 1
    assert unidades.get("nao_informado") == 1
    assert equipes.get("Cível") == 1
    assert equipes.get("nao_informado") == 1
    assert "não há % de utilização" in " ".join(report.honesty.missing_fields)


@pytest.mark.asyncio
async def test_capacity_query_count_is_constant_regardless_of_members(db_session, auth_context):
    workspace_id = auth_context.workspace_id
    client = await _make_client(db_session, workspace_id)

    async def _seed():
        member = await _make_member(db_session, workspace_id, name=f"Membro {uuid4().hex[:6]}")
        await _make_demand(db_session, workspace_id, client.id, responsible_user_id=member.id)

    for _ in range(2):
        await _seed()
    await db_session.commit()

    service = LegalCapacityService(db_session, workspace_id)

    statement_count = {"value": 0}

    def _count_statement(*_args, **_kwargs):
        statement_count["value"] += 1

    event.listen(test_engine.sync_engine, "before_cursor_execute", _count_statement)
    try:
        await service.get_capacity(now=NOW)
        small_n_queries = statement_count["value"]
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", _count_statement)

    for _ in range(10):
        await _seed()
    await db_session.commit()

    statement_count["value"] = 0
    event.listen(test_engine.sync_engine, "before_cursor_execute", _count_statement)
    try:
        await service.get_capacity(now=NOW)
        large_n_queries = statement_count["value"]
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", _count_statement)

    assert small_n_queries == large_n_queries
