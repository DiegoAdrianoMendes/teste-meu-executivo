from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event

from app.models.client import Client
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.klm_operations import LegalArtifactState
from app.models.legal_demand import LegalDemand, LegalDemandNature, LegalDemandPhase
from app.models.legal_exception import ExceptionCenterStatus, ExceptionKind, LegalException
from app.models.matter import Matter, PracticeArea
from app.models.meeting import Meeting, MeetingStatus
from app.models.process_monitoring import (
    DeadlineCountingType,
    DeadlineStatus,
    MonitoredProcess,
    MonitoredProcessStatus,
    ProcessPublication,
    ProcessPublicationSource,
)
from app.models.user import AgentType, User, UserRole
from app.services.legal_controladoria_panel_service import LegalControladoriaPanelService
from tests.conftest import test_engine

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


async def _make_client(db_session, workspace_id, *, name: str = "Cliente Painel") -> Client:
    client = Client(workspace_id=workspace_id, name=name)
    db_session.add(client)
    await db_session.flush()
    return client


async def _make_matter(db_session, workspace_id, client_id, *, updated_at=None) -> Matter:
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client_id,
        practice_area=PracticeArea.civel,
        demand_type="Caso painel",
    )
    db_session.add(matter)
    await db_session.flush()
    if updated_at is not None:
        await db_session.execute(
            Matter.__table__.update().where(Matter.id == matter.id).values(updated_at=updated_at)
        )
        await db_session.flush()
    return matter


async def _make_user(db_session) -> User:
    user = User(
        name="Responsável Painel",
        email=f"painel-{uuid4().hex[:10]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="RP",
        color="#334455",
        is_active=True,
        is_internal_staff=False,
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _seed_full_workspace(db_session, workspace_id) -> None:
    client = await _make_client(db_session, workspace_id)
    matter = await _make_matter(db_session, workspace_id, client.id)
    user = await _make_user(db_session)
    suffix = uuid4().hex[:4]
    numero_cnj = f"000{suffix[:1]}234-37.2026.8.07.0001"

    # Publicações: 1 não tratada (sem prazo), 1 sugerida, 1 confirmada D-3.
    db_session.add_all(
        [
            ProcessPublication(
                workspace_id=workspace_id,
                fonte=ProcessPublicationSource.djen,
                external_id=f"ext-{uuid4().hex[:10]}",
                numero_cnj=numero_cnj,
                conteudo="Andamento informativo",
                published_at=NOW,
            ),
            ProcessPublication(
                workspace_id=workspace_id,
                fonte=ProcessPublicationSource.djen,
                external_id=f"ext-{uuid4().hex[:10]}",
                numero_cnj=numero_cnj,
                conteudo="Intimação com prazo sugerido",
                published_at=NOW,
                dias=15,
                tipo_contagem=DeadlineCountingType.dias_uteis,
                data_fatal=(NOW + timedelta(days=20)).date(),
                prazo_status=DeadlineStatus.prazo_sugerido,
            ),
            ProcessPublication(
                workspace_id=workspace_id,
                fonte=ProcessPublicationSource.djen,
                external_id=f"ext-{uuid4().hex[:10]}",
                numero_cnj=numero_cnj,
                conteudo="Intimação confirmada",
                published_at=NOW,
                dias=15,
                tipo_contagem=DeadlineCountingType.dias_uteis,
                data_fatal=(NOW + timedelta(days=3)).date(),
                prazo_status=DeadlineStatus.confirmado,
                confirmed_by_user_id=user.id,
                confirmed_at=NOW,
            ),
        ]
    )

    # Processo monitorado sem movimentação há 40 dias.
    db_session.add(
        MonitoredProcess(
            workspace_id=workspace_id,
            numero_cnj=f"{uuid4().hex[:7]}-13.2026.8.07.0016",
            status=MonitoredProcessStatus.ativo,
            created_by_user_id=user.id,
            last_movement_at=NOW - timedelta(days=40),
        )
    )

    # Demanda jurídica sem responsável.
    db_session.add(
        LegalDemand(
            workspace_id=workspace_id,
            nature=LegalDemandNature.judicial,
            area="trabalhista",
            client_id=client.id,
            parties=[],
            phase=LegalDemandPhase.triagem,
            sla={},
            documents_required=[],
            responsible_user_id=None,
        )
    )

    # Audiência (Meeting) próxima em 2 dias.
    db_session.add(
        Meeting(
            workspace_id=workspace_id,
            matter_id=matter.id,
            title="Audiência de instrução",
            starts_at=NOW + timedelta(days=2),
            status=MeetingStatus.scheduled,
        )
    )

    # Documento pendente.
    db_session.add(
        Document(
            matter_id=matter.id,
            name="Procuração.pdf",
            doc_type=DocumentType.outro,
            status=DocumentStatus.aguardando,
        )
    )

    # Minuta bloqueada.
    db_session.add(
        LegalArtifactState(
            workspace_id=workspace_id,
            artifact_version_id=uuid4(),
            matter_id=matter.id,
            status="changes_requested",
        )
    )

    # Exceção aberta.
    db_session.add(
        LegalException(
            workspace_id=workspace_id,
            exception_kind=ExceptionKind.homonimo,
            origin="test.seed",
            dedupe_key=f"dedupe-{uuid4().hex[:8]}",
            title="Homônimo detectado",
            status=ExceptionCenterStatus.aberta,
        )
    )

    # Cliente sem atualização (matter tocado há 40 dias).
    stale_client = await _make_client(db_session, workspace_id, name="Cliente Parado")
    await _make_matter(db_session, workspace_id, stale_client.id, updated_at=NOW - timedelta(days=40))

    await db_session.flush()


@pytest.mark.asyncio
async def test_painel_aggregates_all_categories_with_real_data(db_session, auth_context):
    await _seed_full_workspace(db_session, auth_context.workspace_id)
    service = LegalControladoriaPanelService(db_session, auth_context.workspace_id)

    painel = await service.get_panel(now=NOW)

    assert painel.publicacoes_nao_tratadas == 2  # sem prazo + sugerida
    assert painel.prazos_sugeridos == 1
    assert painel.prazos_fatais == 1  # confirmado
    assert painel.prazos_por_janela.d3 == 1
    assert painel.tarefas_sem_responsavel == 1
    assert painel.processos_sem_movimentacao == 1
    assert painel.audiencias_proximas == 1
    assert painel.documentos_pendentes == 1
    assert painel.minutas_bloqueadas == 1
    assert painel.excecoes_de_agentes == 1
    assert painel.clientes_sem_atualizacao == 1


@pytest.mark.asyncio
async def test_painel_empty_workspace_returns_zeros(db_session, auth_context):
    service = LegalControladoriaPanelService(db_session, auth_context.workspace_id)
    painel = await service.get_panel(now=NOW)

    assert painel.publicacoes_nao_tratadas == 0
    assert painel.tarefas_sem_responsavel == 0
    assert painel.prazos_por_janela.atrasados == 0
    assert painel.clientes_sem_atualizacao == 0


@pytest.mark.asyncio
async def test_painel_query_count_is_constant_regardless_of_volume(db_session, auth_context):
    """Sem N+1: o número de statements SQL não pode crescer com o volume
    de itens agregados — cada métrica é UMA query, não um loop por item."""
    await _seed_full_workspace(db_session, auth_context.workspace_id)
    # Duplica o volume várias vezes para garantir que N crescer não muda o
    # número de queries emitidas pelo painel.
    for _ in range(3):
        await _seed_full_workspace(db_session, auth_context.workspace_id)
    await db_session.commit()

    service = LegalControladoriaPanelService(db_session, auth_context.workspace_id)

    statement_count = {"value": 0}

    def _count_statement(*_args, **_kwargs):
        statement_count["value"] += 1

    event.listen(test_engine.sync_engine, "before_cursor_execute", _count_statement)
    try:
        await service.get_panel(now=NOW)
        small_n_queries = statement_count["value"]
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", _count_statement)

    # Roda de novo com MUITO mais volume no mesmo workspace.
    for _ in range(20):
        await _seed_full_workspace(db_session, auth_context.workspace_id)
    await db_session.commit()

    statement_count["value"] = 0
    event.listen(test_engine.sync_engine, "before_cursor_execute", _count_statement)
    try:
        await service.get_panel(now=NOW)
        large_n_queries = statement_count["value"]
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", _count_statement)

    assert small_n_queries == large_n_queries
    # Uma métrica por categoria agregada — um número pequeno e fixo de
    # statements, nunca proporcional ao volume de linhas.
    assert small_n_queries <= 12
