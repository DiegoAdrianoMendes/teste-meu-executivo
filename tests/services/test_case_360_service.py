from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event

from app.models.case_party import CaseParty, CasePartyPole
from app.models.client import Client
from app.models.conversation import Conversation
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.fee_arrangement import FeeArrangement, FeeArrangementKind
from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.legal_controladoria import (
    DistributionDecision,
    DistributionTrigger,
    SlaEscalationLevel,
    SlaEscalationLog,
)
from app.models.legal_demand import LegalDemand, LegalDemandNature, LegalDemandPhase
from app.models.legal_exception import ExceptionCenterStatus, ExceptionKind, LegalException
from app.models.matter import Matter, PracticeArea
from app.models.process_monitoring import (
    MonitoredProcess,
    MonitoredProcessStatus,
    ProcessPublication,
    ProcessPublicationSource,
    PublicationJourneyState,
)
from app.models.timeline_event import TimelineEvent, TimelineEventType
from app.models.user import AgentType, User, UserRole
from app.services.case_360_service import Case360NotFoundError, Case360Service
from tests.conftest import test_engine

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


async def _make_client(db_session, workspace_id, *, name: str = "Cliente Caso 360") -> Client:
    client = Client(workspace_id=workspace_id, name=name)
    db_session.add(client)
    await db_session.flush()
    return client


async def _make_user(db_session, *, name: str = "Advogado Responsável") -> User:
    user = User(
        name=name,
        email=f"caso360-{uuid4().hex[:10]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="AR",
        color="#223344",
        is_active=True,
        is_internal_staff=False,
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _make_matter(db_session, workspace_id, client_id) -> Matter:
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client_id,
        practice_area=PracticeArea.civel,
        demand_type="Caso 360 — teste",
        case_number=f"000{uuid4().hex[:1]}111-37.2026.8.07.0001",
    )
    db_session.add(matter)
    await db_session.flush()
    return matter


async def _make_full_demand(db_session, workspace_id, *, with_matter: bool = True) -> LegalDemand:
    client = await _make_client(db_session, workspace_id)
    responsible = await _make_user(db_session)

    matter = None
    if with_matter:
        matter = await _make_matter(db_session, workspace_id, client.id)

    demand = LegalDemand(
        workspace_id=workspace_id,
        nature=LegalDemandNature.judicial,
        area="trabalhista",
        client_id=client.id,
        parties=[{"name": "Contraparte Legado Ltda", "role": "réu"}],
        phase=LegalDemandPhase.em_andamento,
        risk="alto",
        sla={},
        documents_required=[],
        responsible_user_id=responsible.id,
        matter_id=matter.id if matter else None,
        strategy="Tese central: nulidade da cláusula X.",
        prazo_fatal_at=NOW + timedelta(days=5),
        sla_effective_at=NOW + timedelta(days=3),
        sla_effective_layer="prazo_fatal",
    )
    db_session.add(demand)
    await db_session.flush()

    if matter is not None:
        await _seed_matter_dependents(db_session, workspace_id, demand, matter)

    db_session.add(
        CaseParty(
            workspace_id=workspace_id,
            legal_demand_id=demand.id,
            name="Contraparte Ltda",
            pole=CasePartyPole.passivo,
            role="réu",
            qualification={"docNumber": "12.345.678/0001-90", "docType": "cnpj"},
        )
    )
    db_session.add(
        DistributionDecision(
            workspace_id=workspace_id,
            legal_demand_id=demand.id,
            trigger=DistributionTrigger.regra_automatica,
            new_user_id=responsible.id,
            reason="Primeira atribuição pela regra Trabalhista.",
        )
    )
    db_session.add(
        SlaEscalationLog(
            workspace_id=workspace_id,
            legal_demand_id=demand.id,
            level=SlaEscalationLevel.d3,
            due_at=NOW + timedelta(days=3),
            channel_results={},
        )
    )

    await db_session.flush()
    return demand


async def _seed_matter_dependents(db_session, workspace_id, demand, matter) -> None:
    numero_cnj = f"{uuid4().hex[:7]}-37.2026.8.07.0001"
    process = MonitoredProcess(
        workspace_id=workspace_id,
        matter_id=matter.id,
        numero_cnj=numero_cnj,
        status=MonitoredProcessStatus.ativo,
        created_by_user_id=demand.responsible_user_id,
        last_movement_at=NOW - timedelta(days=2),
    )
    db_session.add(process)
    await db_session.flush()

    db_session.add(
        ProcessPublication(
            workspace_id=workspace_id,
            monitored_process_id=process.id,
            fonte=ProcessPublicationSource.djen,
            external_id=f"ext-{uuid4().hex[:10]}",
            numero_cnj=numero_cnj,
            conteudo="Intimação para manifestação",
            published_at=NOW,
            journey_state=PublicationJourneyState.vinculada,
        )
    )

    db_session.add(
        Document(
            matter_id=matter.id,
            name="Procuração.pdf",
            doc_type=DocumentType.procuracao,
            status=DocumentStatus.completo,
        )
    )

    db_session.add(
        FinanceEntry(
            workspace_id=workspace_id,
            matter_id=matter.id,
            title="Honorários — entrada",
            entry_type=FinanceEntryType.receivable,
            status=FinanceEntryStatus.paid,
            amount=5000,
        )
    )
    db_session.add(
        FinanceEntry(
            workspace_id=workspace_id,
            matter_id=matter.id,
            title="Honorários — parcela 2",
            entry_type=FinanceEntryType.receivable,
            status=FinanceEntryStatus.overdue,
            amount=2500,
        )
    )
    db_session.add(
        FeeArrangement(
            workspace_id=workspace_id,
            matter_id=matter.id,
            kind=FeeArrangementKind.entrada_exito,
            fixed_value=5000,
            percent=20,
        )
    )

    db_session.add(
        Conversation(
            matter_id=matter.id,
            message="Cliente confirmou recebimento da petição.",
            is_internal=False,
        )
    )

    db_session.add(
        LegalException(
            workspace_id=workspace_id,
            matter_id=matter.id,
            exception_kind=ExceptionKind.prazo_ambiguo,
            origin="test.seed",
            dedupe_key=f"dedupe-{uuid4().hex[:8]}",
            title="Prazo ambíguo detectado",
            status=ExceptionCenterStatus.aberta,
        )
    )

    db_session.add(
        TimelineEvent(
            matter_id=matter.id,
            event_type=TimelineEventType.status_change,
            title="Caso movido para Em andamento",
        )
    )

    await db_session.flush()


@pytest.mark.asyncio
async def test_case_360_aggregates_all_blocks_with_real_data(db_session, auth_context):
    demand = await _make_full_demand(db_session, auth_context.workspace_id)
    await db_session.commit()

    service = Case360Service(db_session)
    result = await service.get_case_360(auth_context.workspace_id, demand.id)

    assert result.demand.id == demand.id
    assert result.client.id == demand.client_id
    assert result.responsible_user is not None
    assert result.matter is not None

    assert len(result.case_parties) == 1
    assert result.case_parties[0].pole == CasePartyPole.passivo

    assert len(result.processes) == 1
    process = result.processes[0]
    publications = result.publications_by_process[process.id]
    assert len(publications) == 1
    assert publications[0].journey_state == PublicationJourneyState.vinculada

    assert result.distribution_last_decision is not None
    assert result.distribution_last_decision.reason.startswith("Primeira atribuição")
    assert len(result.open_sla_escalations) == 1

    assert len(result.documents) == 1

    assert len(result.finance_entries) == 2
    assert result.finance_totals.total_receivable == 7500
    assert result.finance_totals.total_paid == 5000
    assert result.finance_totals.total_overdue == 2500
    assert len(result.fee_arrangements) == 1

    assert result.conversations_total == 1
    assert len(result.conversations_recent) == 1

    assert result.strategy_matrix.legal_demand_id == demand.id

    assert len(result.open_exceptions) == 1
    assert len(result.timeline) == 1


@pytest.mark.asyncio
async def test_case_360_natureza_sem_matter_nao_quebra_e_devolve_blocos_vazios(
    db_session, auth_context
):
    client = await _make_client(db_session, auth_context.workspace_id, name="Cliente Consultivo")
    demand = LegalDemand(
        workspace_id=auth_context.workspace_id,
        nature=LegalDemandNature.consultivo,
        area="tributario",
        client_id=client.id,
        parties=[],
        phase=LegalDemandPhase.triagem,
        sla={},
        documents_required=[],
    )
    db_session.add(demand)
    await db_session.flush()
    await db_session.commit()

    service = Case360Service(db_session)
    result = await service.get_case_360(auth_context.workspace_id, demand.id)

    assert result.matter is None
    assert result.processes == []
    assert result.publications_by_process == {}
    assert result.documents == []
    assert result.finance_entries == []
    assert result.finance_totals.total_receivable == 0
    assert result.fee_arrangements == []
    assert result.conversations_recent == []
    assert result.conversations_total == 0
    assert result.open_exceptions == []
    assert result.timeline == []
    # A matriz fato-prova-pedido continua acessível (não depende de Matter).
    assert result.strategy_matrix.legal_demand_id == demand.id
    assert result.strategy_matrix.facts == []


@pytest.mark.asyncio
async def test_case_360_unknown_demand_raises_not_found(db_session, auth_context):
    service = Case360Service(db_session)
    with pytest.raises(Case360NotFoundError):
        await service.get_case_360(auth_context.workspace_id, uuid4())


@pytest.mark.asyncio
async def test_case_360_oportunidade_e_demanda_contratada_sao_distintas(db_session, auth_context):
    """O bloco 'oportunidade' (CRM/Matter) nunca se confunde com o bloco
    'demanda contratada' (dados jurídicos da LegalDemand) — ver
    Case360Result.matter vs Case360Result.demand."""
    demand = await _make_full_demand(db_session, auth_context.workspace_id)
    await db_session.commit()

    service = Case360Service(db_session)
    result = await service.get_case_360(auth_context.workspace_id, demand.id)

    assert result.demand.nature == LegalDemandNature.judicial
    assert result.demand.phase == LegalDemandPhase.em_andamento
    assert result.matter is not None
    assert result.matter.id != result.demand.id
    assert result.matter.case_number is not None


@pytest.mark.asyncio
async def test_case_360_query_count_is_constant_regardless_of_volume(db_session, auth_context):
    """Sem N+1: o número de statements SQL não pode crescer com o volume de
    publicações/documentos/lançamentos financeiros agregados."""
    demand = await _make_full_demand(db_session, auth_context.workspace_id)
    matter = await db_session.get(Matter, demand.matter_id)
    for _ in range(5):
        await _seed_matter_dependents(db_session, auth_context.workspace_id, demand, matter)
    await db_session.commit()

    service = Case360Service(db_session)

    statement_count = {"value": 0}

    def _count_statement(*_args, **_kwargs):
        statement_count["value"] += 1

    event.listen(test_engine.sync_engine, "before_cursor_execute", _count_statement)
    try:
        await service.get_case_360(auth_context.workspace_id, demand.id)
        small_n_queries = statement_count["value"]
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", _count_statement)

    for _ in range(20):
        await _seed_matter_dependents(db_session, auth_context.workspace_id, demand, matter)
    await db_session.commit()

    statement_count["value"] = 0
    event.listen(test_engine.sync_engine, "before_cursor_execute", _count_statement)
    try:
        await service.get_case_360(auth_context.workspace_id, demand.id)
        large_n_queries = statement_count["value"]
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", _count_statement)

    assert small_n_queries == large_n_queries
