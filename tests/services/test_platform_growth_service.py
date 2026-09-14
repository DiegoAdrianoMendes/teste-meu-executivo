"""Testes unitários do PlatformGrowthService (Growth CRM da plataforma)."""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.models.growth_crm import GrowthFunnelStage
from app.models.user import AgentType, User, UserRole
from app.services.platform_growth_service import (
    GrowthInvalidStageTransitionError,
    GrowthProspectNotFoundError,
    PlatformGrowthService,
)


def _staff_user() -> User:
    return User(
        name="Staff Growth",
        email=f"staff-growth-{uuid4().hex[:8]}@mv.com.br",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="SG",
        is_active=True,
        is_platform_admin=True,
    )


@pytest.mark.asyncio
async def test_create_prospect_starts_in_prospect_stage_and_logs_event(db_session):
    actor = _staff_user()
    db_session.add(actor)
    await db_session.commit()

    svc = PlatformGrowthService(db_session)
    prospect = await svc.create_prospect(
        nome_escritorio="Escritório Alpha",
        contato_email="contato@alpha.adv.br",
        origem="outbound",
        valor_estimado_cents=49700,
        actor=actor,
    )

    assert prospect.estagio == GrowthFunnelStage.prospect

    full = await svc.get_prospect(prospect.id)
    assert len(full.stage_events) == 1
    assert full.stage_events[0].to_estagio == GrowthFunnelStage.prospect
    assert full.stage_events[0].from_estagio is None


@pytest.mark.asyncio
async def test_move_stage_records_stage_event_and_sets_won_at(db_session):
    actor = _staff_user()
    db_session.add(actor)
    await db_session.commit()

    svc = PlatformGrowthService(db_session)
    prospect = await svc.create_prospect(nome_escritorio="Escritório Beta", actor=actor)

    updated = await svc.move_stage(
        prospect.id, to_estagio="contatado", motivo="Primeiro contato feito.", actor=actor
    )
    assert updated.estagio == GrowthFunnelStage.contatado

    won = await svc.move_stage(prospect.id, to_estagio="pagante", motivo="Fechado!", actor=actor)
    assert won.estagio == GrowthFunnelStage.pagante
    assert won.won_at is not None

    full = await svc.get_prospect(prospect.id)
    assert len(full.stage_events) == 3  # criação + contatado + pagante


@pytest.mark.asyncio
async def test_move_stage_invalid_target_raises(db_session):
    svc = PlatformGrowthService(db_session)
    prospect = await svc.create_prospect(nome_escritorio="Escritório Gamma")

    with pytest.raises(GrowthInvalidStageTransitionError):
        await svc.move_stage(prospect.id, to_estagio="nao_existe")


@pytest.mark.asyncio
async def test_move_stage_unknown_prospect_raises_not_found(db_session):
    svc = PlatformGrowthService(db_session)
    with pytest.raises(GrowthProspectNotFoundError):
        await svc.move_stage(uuid4(), to_estagio="contatado")


@pytest.mark.asyncio
async def test_add_activity_registers_touch(db_session):
    svc = PlatformGrowthService(db_session)
    prospect = await svc.create_prospect(nome_escritorio="Escritório Delta")

    activity = await svc.add_activity(prospect.id, tipo="ligacao", descricao="Primeira ligação.")
    assert activity.tipo.value == "ligacao"

    full = await svc.get_prospect(prospect.id)
    assert len(full.activities) == 1


@pytest.mark.asyncio
async def test_funnel_metrics_computes_conversion_and_win_rate(db_session):
    svc = PlatformGrowthService(db_session)
    p1 = await svc.create_prospect(nome_escritorio="Escritório Won", valor_estimado_cents=10000)
    p2 = await svc.create_prospect(nome_escritorio="Escritório Lost", valor_estimado_cents=20000)
    p3 = await svc.create_prospect(nome_escritorio="Escritório Open", valor_estimado_cents=30000)

    await svc.move_stage(p1.id, to_estagio="pagante", motivo="ganho")
    await svc.move_stage(p2.id, to_estagio="perdido", motivo="perdido")

    metrics = await svc.get_funnel_metrics()
    assert metrics["total_prospects"] == 3
    assert metrics["won"] == 1
    assert metrics["lost"] == 1
    assert metrics["win_rate_pct"] == 50.0
    assert metrics["pipeline_estimado_cents"] == 30000  # só o aberto (p3)


@pytest.mark.asyncio
async def test_pipeline_groups_by_stage(db_session):
    svc = PlatformGrowthService(db_session)
    await svc.create_prospect(nome_escritorio="Escritório Kanban 1")
    p2 = await svc.create_prospect(nome_escritorio="Escritório Kanban 2")
    await svc.move_stage(p2.id, to_estagio="demo_agendada")

    board = await svc.get_pipeline()
    assert len(board["prospect"]) == 1
    assert len(board["demo_agendada"]) == 1


@pytest.mark.asyncio
async def test_list_prospects_filters_by_search(db_session):
    svc = PlatformGrowthService(db_session)
    await svc.create_prospect(nome_escritorio="Escritório Buscável XYZ")
    await svc.create_prospect(nome_escritorio="Outro Nome")

    result = await svc.list_prospects(search="Buscável")
    assert result["count"] == 1
    assert result["items"][0].nome_escritorio == "Escritório Buscável XYZ"
