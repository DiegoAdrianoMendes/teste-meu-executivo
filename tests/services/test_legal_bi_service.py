from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import event

from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.client import Client
from app.models.human_override import HumanOverride, HumanOverrideDecision
from app.models.klm_operations import KlmAuditEvent
from app.models.legal_demand import (
    LegalDemand,
    LegalDemandNature,
    LegalDemandPhase,
    LegalDemandRisk,
)
from app.models.legal_exception import ExceptionCenterStatus, ExceptionKind, LegalException
from app.models.matter import KanbanColumnId, Matter, PracticeArea
from app.models.process_monitoring import ProcessPublication, ProcessPublicationSource
from app.models.task import Task, TaskStatus
from app.models.user import AgentType, User, UserRole
from app.services.legal_bi_service import LegalBiService
from tests.conftest import test_engine

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


async def _make_client(db_session, workspace_id, *, name: str = "Cliente BI") -> Client:
    client = Client(workspace_id=workspace_id, name=name)
    db_session.add(client)
    await db_session.flush()
    return client


async def _make_matter(db_session, workspace_id, client_id) -> Matter:
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client_id,
        practice_area=PracticeArea.civel,
        demand_type="Caso BI",
    )
    db_session.add(matter)
    await db_session.flush()
    return matter


async def _make_user(db_session, *, name: str = "Responsável BI") -> User:
    user = User(
        name=name,
        email=f"bi-{uuid4().hex[:10]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="BI",
        color="#112233",
        is_active=True,
        is_internal_staff=False,
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _make_publication(
    db_session, workspace_id, *, created_at: datetime
) -> ProcessPublication:
    pub = ProcessPublication(
        workspace_id=workspace_id,
        fonte=ProcessPublicationSource.djen,
        external_id=f"ext-{uuid4().hex[:10]}",
        numero_cnj="0001234-37.2026.8.07.0001",
        conteudo="Andamento",
        published_at=created_at,
    )
    db_session.add(pub)
    await db_session.flush()
    await db_session.execute(
        ProcessPublication.__table__.update()
        .where(ProcessPublication.id == pub.id)
        .values(created_at=created_at)
    )
    return pub


def _journey_event(
    workspace_id, publication_id, *, from_state: str, to_state: str, occurred_at: datetime
) -> KlmAuditEvent:
    return KlmAuditEvent(
        workspace_id=workspace_id,
        actor_type="human",
        actor_label="Teste",
        event_type="legal_journey_advanced",
        entity_type="process_publication",
        entity_id=publication_id,
        correlation_id=f"{from_state}->{to_state}",
        summary=f"{from_state}->{to_state}",
        before_data={"journeyState": from_state},
        after_data={"journeyState": to_state},
        occurred_at=occurred_at,
    )


@pytest.mark.asyncio
async def test_operation_bi_journey_timing_and_rework(db_session, auth_context):
    workspace_id = auth_context.workspace_id
    publication = await _make_publication(
        db_session, workspace_id, created_at=NOW - timedelta(days=2)
    )

    db_session.add_all(
        [
            _journey_event(
                workspace_id,
                publication.id,
                from_state="vinculada",
                to_state="triagem_concluida",
                occurred_at=NOW - timedelta(days=2) + timedelta(hours=6),
            ),
            _journey_event(
                workspace_id,
                publication.id,
                from_state="correcoes_pendentes",
                to_state="minuta_em_producao",
                occurred_at=NOW - timedelta(hours=1),
            ),
        ]
    )
    await db_session.flush()
    await db_session.commit()

    service = LegalBiService(db_session, workspace_id)
    result = await service.get_operation_bi(
        period_from=NOW - timedelta(days=30), period_to=NOW, now=NOW
    )

    triagem = next(row for row in result.tempo_por_marco if row.to_state == "triagem_concluida")
    assert triagem.sample_size == 1
    assert triagem.avg_hours == pytest.approx(6.0, abs=0.01)
    assert result.retrabalho_correcoes_pendentes == 1
    assert result.honesty.sample_size >= 0
    assert result.honesty.period_from == NOW - timedelta(days=30)
    assert result.honesty.period_to == NOW


@pytest.mark.asyncio
async def test_operation_bi_tasks_backlog_and_exceptions(db_session, auth_context):
    workspace_id = auth_context.workspace_id
    client = await _make_client(db_session, workspace_id)
    matter = await _make_matter(db_session, workspace_id, client.id)
    responsible = await _make_user(db_session)

    today_str = NOW.date().isoformat()
    tomorrow_str = (NOW.date() + timedelta(days=1)).isoformat()
    overdue_str = (NOW.date() - timedelta(days=5)).isoformat()

    db_session.add_all(
        [
            Task(
                workspace_id=workspace_id,
                matter_id=matter.id,
                title="D0",
                status=TaskStatus.pending,
                due_date=today_str,
                assigned_to=responsible.id,
            ),
            Task(
                workspace_id=workspace_id,
                matter_id=matter.id,
                title="D1",
                status=TaskStatus.pending,
                due_date=tomorrow_str,
                assigned_to=responsible.id,
            ),
            Task(
                workspace_id=workspace_id,
                matter_id=matter.id,
                title="Atrasada",
                status=TaskStatus.pending,
                due_date=overdue_str,
                assigned_to=None,
            ),
            Task(
                workspace_id=workspace_id,
                matter_id=matter.id,
                title="Sem data classificável",
                status=TaskStatus.pending,
                due_date="assim que possível",
            ),
            Task(
                workspace_id=workspace_id,
                matter_id=matter.id,
                title="Concluída não conta",
                status=TaskStatus.done,
                due_date=overdue_str,
            ),
        ]
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
        )
    )
    db_session.add(
        LegalException(
            workspace_id=workspace_id,
            exception_kind=ExceptionKind.prazo_ambiguo,
            origin="legal_journey_service.advance",
            dedupe_key=f"pub-{uuid4().hex[:10]}:prazo_sugerido",
            title="Prazo ambíguo",
            description="teste",
            status=ExceptionCenterStatus.aberta,
            # Ancorado em NOW. Sem isto o created_at nasce do relógio real e a
            # linha cai FORA da janela consultada, que termina em NOW (12h UTC):
            # o teste só passava quando a suíte rodava antes das 12h UTC.
            created_at=NOW - timedelta(days=1),
        )
    )
    await db_session.flush()
    await db_session.commit()

    service = LegalBiService(db_session, workspace_id)
    result = await service.get_operation_bi(now=NOW)

    assert result.tarefas_d0 == 1
    assert result.tarefas_d1 == 1
    assert result.tarefas_atrasadas == 1
    assert result.tarefas_sem_data_classificavel == 1
    assert result.tarefas_sem_responsavel == 2  # "atrasada" e "sem data" não têm assigned_to
    assert len(result.backlog_por_responsavel) == 1
    assert result.backlog_por_responsavel[0].key == str(responsible.id)
    assert result.backlog_por_responsavel[0].count == 1
    assert len(result.excecoes_por_origem) == 1
    assert result.excecoes_por_origem[0].count == 1


@pytest.mark.asyncio
async def test_legal_bi_estoque_valores_e_resultado(db_session, auth_context):
    workspace_id = auth_context.workspace_id
    client = await _make_client(db_session, workspace_id)
    matter = await _make_matter(db_session, workspace_id, client.id)
    await db_session.execute(
        Matter.__table__.update()
        .where(Matter.id == matter.id)
        .values(column_id=KanbanColumnId.ganho)
    )

    db_session.add_all(
        [
            LegalDemand(
                workspace_id=workspace_id,
                nature=LegalDemandNature.judicial,
                area="civel",
                client_id=client.id,
                parties=[],
                risk=LegalDemandRisk.alto,
                phase=LegalDemandPhase.em_andamento,
                sla={},
                documents_required=[],
                value=Decimal("1000.00"),
                created_at=NOW,
            ),
            LegalDemand(
                workspace_id=workspace_id,
                nature=LegalDemandNature.judicial,
                area="civel",
                client_id=client.id,
                matter_id=matter.id,
                parties=[],
                risk=LegalDemandRisk.baixo,
                phase=LegalDemandPhase.concluido,
                sla={},
                documents_required=[],
                value=Decimal("500.00"),
                created_at=NOW,
            ),
            LegalDemand(
                workspace_id=workspace_id,
                nature=LegalDemandNature.acordo,
                area="civel",
                client_id=client.id,
                parties=[],
                risk=LegalDemandRisk.medio,
                phase=LegalDemandPhase.concluido,
                sla={},
                documents_required=[],
                strategy="Tese X",
                created_at=NOW,
            ),
        ]
    )
    await db_session.flush()
    await db_session.commit()

    service = LegalBiService(db_session, workspace_id)
    result = await service.get_legal_bi(
        period_from=NOW - timedelta(days=365), period_to=NOW + timedelta(days=1), now=NOW
    )

    estoque = {row.key: row.count for row in result.estoque_por_fase}
    assert estoque[LegalDemandPhase.em_andamento.value] == 1
    assert estoque[LegalDemandPhase.concluido.value] == 2

    contingencia = {row.key: row for row in result.contingencia_por_risco}
    assert contingencia[LegalDemandRisk.alto.value].total_value == pytest.approx(1000.0)

    resultado = {row.key: row.count for row in result.resultado_por_coluna_kanban}
    assert resultado.get(KanbanColumnId.ganho.value) == 1
    assert result.resultado_sem_matter_vinculado == 1
    assert result.acordos_no_periodo == 1
    assert result.demandas_com_estrategia_registrada == 1


@pytest.mark.asyncio
async def test_ai_bi_agent_runs_and_human_overrides(db_session, auth_context):
    workspace_id = auth_context.workspace_id
    client = await _make_client(db_session, workspace_id)
    matter = await _make_matter(db_session, workspace_id, client.id)
    triggering_user = await _make_user(db_session)

    completed_run = AgentRun(
        workspace_id=workspace_id,
        matter_id=matter.id,
        triggered_by_user_id=triggering_user.id,
        agent_name="klm_drafter",
        trigger_source="manual",
        status=AgentRunStatus.completed,
        input_tokens=1000,
        output_tokens=500,
        cost_usd=Decimal("0.42"),
        # Ancoradas em NOW: com o created_at vindo do relogio real, as tres
        # linhas caem fora da janela que termina em NOW e o BI conta zero.
        created_at=NOW - timedelta(days=1),
    )
    failed_run = AgentRun(
        workspace_id=workspace_id,
        matter_id=matter.id,
        triggered_by_user_id=triggering_user.id,
        agent_name="klm_drafter",
        trigger_source="manual",
        status=AgentRunStatus.failed,
        cost_usd=Decimal("0.10"),
        created_at=NOW - timedelta(days=1),
    )
    waiting_run = AgentRun(
        workspace_id=workspace_id,
        matter_id=matter.id,
        triggered_by_user_id=triggering_user.id,
        agent_name="klm_analyst",
        trigger_source="manual",
        status=AgentRunStatus.waiting_human,
        cost_usd=Decimal("0.05"),
        created_at=NOW - timedelta(days=1),
    )
    db_session.add_all([completed_run, failed_run, waiting_run])
    await db_session.flush()

    db_session.add(
        HumanOverride(
            workspace_id=workspace_id,
            matter_id=matter.id,
            agent_run_id=completed_run.id,
            decision=HumanOverrideDecision.accepted,
            created_at=NOW - timedelta(days=1),
        )
    )
    await db_session.flush()
    await db_session.commit()

    service = LegalBiService(db_session, workspace_id)
    result = await service.get_ai_bi(now=NOW)

    assert result.execucoes_totais == 3
    assert result.execucoes_falhas == 1
    assert result.execucoes_aguardando_humano == 1
    assert result.intervencoes_humanas_total == 1
    assert result.decisoes_aceitas == 1
    assert result.taxa_intervencao_humana == pytest.approx(1 / 3, abs=1e-4)

    custo = {row.key: row.total_value for row in result.custo_usd_por_agente}
    assert custo["klm_drafter"] == pytest.approx(0.52)
    assert result.tokens_input_totais == 1000
    assert result.tokens_output_totais == 500

    # Honestidade: nunca fabrica tempo_economizado/rollback.
    missing_text = " ".join(result.honesty.missing_fields)
    assert "tempo_economizado" in missing_text
    assert "rollback" in missing_text


@pytest.mark.asyncio
async def test_operation_bi_query_count_is_constant_regardless_of_volume(db_session, auth_context):
    workspace_id = auth_context.workspace_id
    client = await _make_client(db_session, workspace_id)
    matter = await _make_matter(db_session, workspace_id, client.id)
    responsible = await _make_user(db_session)

    async def _seed():
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
            )
        )
        db_session.add(
            Task(
                workspace_id=workspace_id,
                matter_id=matter.id,
                title="Tarefa",
                status=TaskStatus.pending,
                due_date=NOW.date().isoformat(),
                assigned_to=responsible.id,
            )
        )

    for _ in range(3):
        await _seed()
    await db_session.commit()

    service = LegalBiService(db_session, workspace_id)

    statement_count = {"value": 0}

    def _count_statement(*_args, **_kwargs):
        statement_count["value"] += 1

    event.listen(test_engine.sync_engine, "before_cursor_execute", _count_statement)
    try:
        await service.get_operation_bi(now=NOW)
        small_n_queries = statement_count["value"]
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", _count_statement)

    for _ in range(15):
        await _seed()
    await db_session.commit()

    statement_count["value"] = 0
    event.listen(test_engine.sync_engine, "before_cursor_execute", _count_statement)
    try:
        await service.get_operation_bi(now=NOW)
        large_n_queries = statement_count["value"]
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", _count_statement)

    assert small_n_queries == large_n_queries
