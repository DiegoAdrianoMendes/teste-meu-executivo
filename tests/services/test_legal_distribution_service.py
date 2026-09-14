from __future__ import annotations

from uuid import uuid4

import pytest

from app.models.client import Client
from app.models.legal_controladoria import DistributionStrategy, DistributionTrigger
from app.models.legal_demand import (
    LegalDemand,
    LegalDemandNature,
    LegalDemandPhase,
    LegalDemandRisk,
)
from app.models.staff_availability import (
    AvailabilityMode,
    AvailabilityRedirectKind,
    StaffAvailabilityProfile,
)
from app.models.user import AgentType, User, UserRole
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.services.legal_distribution_service import (
    DistributionDemandNotFoundError,
    DistributionNoAvailableCandidateError,
    DistributionNoRuleMatchedError,
    DistributionReasonRequiredError,
    DistributionRuleNameConflictError,
    LegalDistributionService,
)


async def _make_client(db_session, workspace_id) -> Client:
    client = Client(workspace_id=workspace_id, name="Cliente Distribuição")
    db_session.add(client)
    await db_session.flush()
    return client


async def _make_member(db_session, workspace_id, *, name: str) -> User:
    user = User(
        name=name,
        email=f"advogado-{uuid4().hex[:10]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials=name[:2].upper(),
        color="#123456",
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
    db_session,
    workspace_id,
    client_id,
    *,
    nature=LegalDemandNature.judicial,
    area="trabalhista",
    risk=LegalDemandRisk.medio,
    unit=None,
    team=None,
    phase=LegalDemandPhase.em_andamento,
    responsible_user_id=None,
) -> LegalDemand:
    demand = LegalDemand(
        workspace_id=workspace_id,
        nature=nature,
        area=area,
        client_id=client_id,
        parties=[],
        risk=risk,
        unit=unit,
        team=team,
        phase=phase,
        sla={},
        documents_required=[],
        responsible_user_id=responsible_user_id,
    )
    db_session.add(demand)
    await db_session.flush()
    return demand


@pytest.mark.asyncio
async def test_create_rule_rejects_duplicate_name(db_session, auth_context):
    service = LegalDistributionService(db_session, auth_context.workspace_id)
    await service.create_rule(name="Regra única")
    with pytest.raises(DistributionRuleNameConflictError):
        await service.create_rule(name="Regra única")


@pytest.mark.asyncio
async def test_assign_no_matching_rule_raises(db_session, auth_context):
    client = await _make_client(db_session, auth_context.workspace_id)
    demand = await _make_demand(db_session, auth_context.workspace_id, client.id, area="tributario")
    service = LegalDistributionService(db_session, auth_context.workspace_id)

    with pytest.raises(DistributionNoRuleMatchedError):
        await service.assign(demand.id)


@pytest.mark.asyncio
async def test_assign_unknown_demand_raises(db_session, auth_context):
    service = LegalDistributionService(db_session, auth_context.workspace_id)
    with pytest.raises(DistributionDemandNotFoundError):
        await service.assign(uuid4())


@pytest.mark.asyncio
async def test_assign_matches_rule_and_records_history_with_reason(db_session, auth_context):
    client = await _make_client(db_session, auth_context.workspace_id)
    candidate = await _make_member(db_session, auth_context.workspace_id, name="Advogada Trabalhista")
    demand = await _make_demand(db_session, auth_context.workspace_id, client.id, area="trabalhista")

    service = LegalDistributionService(db_session, auth_context.workspace_id)
    await service.create_rule(
        name="Trabalhista SP",
        match_areas=["trabalhista"],
        strategy=DistributionStrategy.fixed,
        candidate_user_ids=[str(candidate.id)],
        respect_availability=False,
    )

    decision = await service.assign(demand.id, decided_by_user_id=None)

    assert decision.new_user_id == candidate.id
    assert decision.previous_user_id is None
    assert decision.trigger == DistributionTrigger.regra_automatica
    assert decision.reason  # nunca vazio — histórico sempre com motivo
    assert demand.responsible_user_id == candidate.id

    history = await service.list_history(demand.id)
    assert len(history) == 1
    assert history[0].id == decision.id


@pytest.mark.asyncio
async def test_assign_round_robin_cycles_through_candidates(db_session, auth_context):
    client = await _make_client(db_session, auth_context.workspace_id)
    candidate_a = await _make_member(db_session, auth_context.workspace_id, name="Advogado A")
    candidate_b = await _make_member(db_session, auth_context.workspace_id, name="Advogado B")

    service = LegalDistributionService(db_session, auth_context.workspace_id)
    await service.create_rule(
        name="Rodízio Cível",
        match_areas=["civel"],
        strategy=DistributionStrategy.round_robin,
        candidate_user_ids=[str(candidate_a.id), str(candidate_b.id)],
        respect_availability=False,
    )

    demand_1 = await _make_demand(db_session, auth_context.workspace_id, client.id, area="civel")
    demand_2 = await _make_demand(db_session, auth_context.workspace_id, client.id, area="civel")
    demand_3 = await _make_demand(db_session, auth_context.workspace_id, client.id, area="civel")

    decision_1 = await service.assign(demand_1.id)
    decision_2 = await service.assign(demand_2.id)
    decision_3 = await service.assign(demand_3.id)

    assigned = [decision_1.new_user_id, decision_2.new_user_id, decision_3.new_user_id]
    # Alterna entre os dois candidatos — nunca repete o mesmo duas vezes seguidas.
    assert assigned[0] != assigned[1]
    assert assigned[1] != assigned[2]
    assert set(assigned) == {candidate_a.id, candidate_b.id}


@pytest.mark.asyncio
async def test_assign_least_load_prefers_candidate_with_fewer_active_demands(db_session, auth_context):
    client = await _make_client(db_session, auth_context.workspace_id)
    busy = await _make_member(db_session, auth_context.workspace_id, name="Advogado Ocupado")
    free = await _make_member(db_session, auth_context.workspace_id, name="Advogado Livre")

    # Duas demandas já ativas com o candidato "busy".
    await _make_demand(db_session, auth_context.workspace_id, client.id, area="consumidor", responsible_user_id=busy.id)
    await _make_demand(db_session, auth_context.workspace_id, client.id, area="consumidor", responsible_user_id=busy.id)

    service = LegalDistributionService(db_session, auth_context.workspace_id)
    await service.create_rule(
        name="Consumidor Least Load",
        match_areas=["consumidor"],
        strategy=DistributionStrategy.least_load,
        candidate_user_ids=[str(busy.id), str(free.id)],
        respect_availability=False,
    )

    new_demand = await _make_demand(db_session, auth_context.workspace_id, client.id, area="consumidor")
    decision = await service.assign(new_demand.id)

    assert decision.new_user_id == free.id


@pytest.mark.asyncio
async def test_assign_respects_capacity_and_moves_to_next_candidate(db_session, auth_context):
    client = await _make_client(db_session, auth_context.workspace_id)
    at_capacity = await _make_member(db_session, auth_context.workspace_id, name="Advogado Lotado")
    fallback = await _make_member(db_session, auth_context.workspace_id, name="Advogado Reserva")

    await _make_demand(db_session, auth_context.workspace_id, client.id, area="tributario", responsible_user_id=at_capacity.id)

    service = LegalDistributionService(db_session, auth_context.workspace_id)
    await service.create_rule(
        name="Tributário Capacidade 1",
        match_areas=["tributario"],
        strategy=DistributionStrategy.fixed,
        candidate_user_ids=[str(at_capacity.id), str(fallback.id)],
        max_active_per_candidate=1,
        respect_availability=False,
    )

    new_demand = await _make_demand(db_session, auth_context.workspace_id, client.id, area="tributario")
    decision = await service.assign(new_demand.id)

    assert decision.new_user_id == fallback.id


@pytest.mark.asyncio
async def test_assign_respects_availability_and_substitutes_on_leave(db_session, auth_context):
    client = await _make_client(db_session, auth_context.workspace_id)
    on_leave = await _make_member(db_session, auth_context.workspace_id, name="Advogado Férias")
    substitute = await _make_member(db_session, auth_context.workspace_id, name="Advogado Substituto")

    # Perfil de disponibilidade: em ausência (away), redirect ativo aprovado
    # para o substituto — mesmo mecanismo de StaffAvailabilityService.
    db_session.add(
        StaffAvailabilityProfile(
            workspace_id=auth_context.workspace_id,
            staff_user_id=on_leave.id,
            availability_mode=AvailabilityMode.away,
            auto_reassign_enabled=True,
            redirect_kind=AvailabilityRedirectKind.user,
            redirect_user_id=substitute.id,
        )
    )
    await db_session.flush()

    service = LegalDistributionService(db_session, auth_context.workspace_id)
    await service.create_rule(
        name="Previdenciário com substituição",
        match_areas=["previdenciario"],
        strategy=DistributionStrategy.fixed,
        candidate_user_ids=[str(on_leave.id)],
        respect_availability=True,
    )

    demand = await _make_demand(db_session, auth_context.workspace_id, client.id, area="previdenciario")
    decision = await service.assign(demand.id)

    assert decision.new_user_id == substitute.id
    assert "ausência" in decision.reason or "substitu" in decision.reason


@pytest.mark.asyncio
async def test_assign_no_available_candidate_raises_when_sole_candidate_on_leave_without_redirect(
    db_session, auth_context
):
    client = await _make_client(db_session, auth_context.workspace_id)
    on_leave = await _make_member(db_session, auth_context.workspace_id, name="Advogado Sem Substituto")

    db_session.add(
        StaffAvailabilityProfile(
            workspace_id=auth_context.workspace_id,
            staff_user_id=on_leave.id,
            availability_mode=AvailabilityMode.away,
            auto_reassign_enabled=False,
        )
    )
    await db_session.flush()

    service = LegalDistributionService(db_session, auth_context.workspace_id)
    await service.create_rule(
        name="Sem substituto configurado",
        match_areas=["ambiental"],
        strategy=DistributionStrategy.fixed,
        candidate_user_ids=[str(on_leave.id)],
        respect_availability=True,
    )

    demand = await _make_demand(db_session, auth_context.workspace_id, client.id, area="ambiental")
    with pytest.raises(DistributionNoAvailableCandidateError):
        await service.assign(demand.id)


@pytest.mark.asyncio
async def test_manual_redistribution_requires_reason(db_session, auth_context):
    client = await _make_client(db_session, auth_context.workspace_id)
    candidate = await _make_member(db_session, auth_context.workspace_id, name="Advogado Manual")
    demand = await _make_demand(db_session, auth_context.workspace_id, client.id, area="familia")

    service = LegalDistributionService(db_session, auth_context.workspace_id)
    with pytest.raises(DistributionReasonRequiredError):
        await service.assign(
            demand.id,
            trigger=DistributionTrigger.redistribuicao_manual,
            forced_user_id=candidate.id,
            reason=None,
        )


@pytest.mark.asyncio
async def test_manual_redistribution_records_previous_and_new_user(db_session, auth_context):
    client = await _make_client(db_session, auth_context.workspace_id)
    original = await _make_member(db_session, auth_context.workspace_id, name="Advogado Original")
    replacement = await _make_member(db_session, auth_context.workspace_id, name="Advogado Substituição Manual")
    demand = await _make_demand(
        db_session,
        auth_context.workspace_id,
        client.id,
        area="familia",
        responsible_user_id=original.id,
    )

    service = LegalDistributionService(db_session, auth_context.workspace_id)
    decision = await service.assign(
        demand.id,
        forced_user_id=replacement.id,
        reason="Original saiu de férias sem cobertura automática configurada",
        decided_by_user_id=auth_context.user_id,
    )

    assert decision.trigger == DistributionTrigger.redistribuicao_manual
    assert decision.previous_user_id == original.id
    assert decision.new_user_id == replacement.id
    assert decision.decided_by_user_id == auth_context.user_id
    assert demand.responsible_user_id == replacement.id

    history = await service.list_history(demand.id)
    assert len(history) == 1
