from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.workers.process_monitoring_tasks as pmt
from app.core.deps import AuthContext
from app.models.billing import BillingInterval, Plan, PlanTier, Subscription, SubscriptionStatus
from app.models.entitlement import EntitlementKey, PlanEntitlement
from app.models.process_monitoring import (
    DeadlineStatus,
    MonitoredProcess,
    ProcessPublication,
    ProcessPublicationSource,
)
from app.services.process_monitoring_service import ProcessMonitoringService

VALID_CNJ_1 = "0001234-37.2026.8.07.0001"
VALID_CNJ_2 = "0007654-13.2026.8.07.0016"


async def _seed_plan(db: AsyncSession, workspace_id, *, limit_quantity: int | None = 10) -> None:
    plan = Plan(
        name=f"Plano worker {uuid4().hex[:8]}",
        tier=PlanTier.enterprise,
        price_monthly_cents=0,
        price_annually_cents=0,
        max_users=100,
        max_matters=None,
        max_tokens_per_month=1_000_000,
        max_ai_agents=100,
        features={},
        is_active=True,
        is_public=False,
        sort_order=1,
    )
    db.add(plan)
    await db.flush()
    db.add(
        Subscription(
            workspace_id=workspace_id,
            plan_id=plan.id,
            status=SubscriptionStatus.active,
            interval=BillingInterval.monthly,
            amount_cents=0,
            currency="BRL",
        )
    )
    db.add_all(
        [
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=EntitlementKey.legal_suite,
                enabled=True,
                limit_quantity=None,
                hard_limit=True,
            ),
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=EntitlementKey.monitored_processes,
                enabled=True,
                limit_quantity=limit_quantity,
                hard_limit=True,
            ),
        ]
    )
    await db.flush()


@pytest.mark.asyncio
async def test_djen_poll_ingests_only_publications_for_monitored_numbers(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id)
    service = ProcessMonitoringService(db_session)
    await service.register_process(
        auth_context.workspace_id,
        numero_cnj=VALID_CNJ_1,
        created_by_user_id=auth_context.user_id,
    )
    await db_session.commit()

    created = await pmt.run_djen_poll_for_workspace(db_session, auth_context.workspace_id)

    # Fixture tem 2 itens para VALID_CNJ_1 (ids 900000001 e 900000003) e 1
    # para VALID_CNJ_2 (não cadastrado neste workspace) — só os 2 primeiros
    # devem ser ingeridos.
    assert created == 2

    publications = (
        (
            await db_session.execute(
                select(ProcessPublication).where(
                    ProcessPublication.workspace_id == auth_context.workspace_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(publications) == 2
    assert {p.numero_cnj for p in publications} == {VALID_CNJ_1}

    com_prazo = next(p for p in publications if p.dias is not None)
    assert com_prazo.dias == 15
    assert com_prazo.prazo_status == DeadlineStatus.prazo_sugerido
    assert com_prazo.urgencia == "critica"

    sem_prazo = next(p for p in publications if p.dias is None)
    assert sem_prazo.prazo_status is None


@pytest.mark.asyncio
async def test_djen_poll_is_idempotent_across_two_ticks(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id)
    service = ProcessMonitoringService(db_session)
    await service.register_process(
        auth_context.workspace_id,
        numero_cnj=VALID_CNJ_1,
        created_by_user_id=auth_context.user_id,
    )
    await db_session.commit()

    first_tick = await pmt.run_djen_poll_for_workspace(db_session, auth_context.workspace_id)
    second_tick = await pmt.run_djen_poll_for_workspace(db_session, auth_context.workspace_id)

    assert first_tick == 2
    assert second_tick == 0

    publications = (
        (
            await db_session.execute(
                select(ProcessPublication).where(
                    ProcessPublication.workspace_id == auth_context.workspace_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(publications) == 2


@pytest.mark.asyncio
async def test_djen_poll_never_calls_live_client_when_gate_closed(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
) -> None:
    async def _explode(*args, **kwargs):
        raise AssertionError("cliente real do DJEN não deveria ser chamado com o gate fechado")

    monkeypatch.setattr(pmt.DjenComunicaClient, "fetch_comunicacoes", _explode)
    await _seed_plan(db_session, auth_context.workspace_id)
    service = ProcessMonitoringService(db_session)
    await service.register_process(
        auth_context.workspace_id,
        numero_cnj=VALID_CNJ_1,
        created_by_user_id=auth_context.user_id,
    )
    await db_session.commit()

    created = await pmt.run_djen_poll_for_workspace(db_session, auth_context.workspace_id)
    assert created == 2  # rodou em modo simulado contra a fixture, sem tocar o cliente real


@pytest.mark.asyncio
async def test_djen_poll_returns_zero_when_module_not_contracted(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    # Sem _seed_plan: workspace não tem assinatura — o worker não deve
    # derrubar o tick, apenas não ingerir nada.
    created = await pmt.run_djen_poll_for_workspace(db_session, auth_context.workspace_id)
    assert created == 0


@pytest.mark.asyncio
async def test_datajud_poll_ingests_latest_movement_from_fixture(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id)
    service = ProcessMonitoringService(db_session)
    await service.register_process(
        auth_context.workspace_id,
        numero_cnj=VALID_CNJ_1,
        tribunal="TJDFT",
        created_by_user_id=auth_context.user_id,
    )
    await db_session.commit()

    created = await pmt.run_datajud_poll_for_workspace(db_session, auth_context.workspace_id)
    assert created == 1

    publication = (
        await db_session.execute(
            select(ProcessPublication).where(
                ProcessPublication.workspace_id == auth_context.workspace_id,
                ProcessPublication.fonte == ProcessPublicationSource.datajud,
            )
        )
    ).scalar_one()
    # O último movimento da fixture (por dataHora) é a "Decisão" de
    # 2026-07-20T16:45, não a "Juntada de Petição" mais cedo no mesmo dia.
    assert publication.tipo == "Decisão"
    assert publication.dias is None  # DataJud é andamento, não gera prazo por si só


@pytest.mark.asyncio
async def test_datajud_poll_is_idempotent_across_two_ticks(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id)
    service = ProcessMonitoringService(db_session)
    await service.register_process(
        auth_context.workspace_id,
        numero_cnj=VALID_CNJ_1,
        tribunal="TJDFT",
        created_by_user_id=auth_context.user_id,
    )
    await db_session.commit()

    first_tick = await pmt.run_datajud_poll_for_workspace(db_session, auth_context.workspace_id)
    second_tick = await pmt.run_datajud_poll_for_workspace(db_session, auth_context.workspace_id)
    assert first_tick == 1
    assert second_tick == 0


def test_jitter_seconds_for_workspace_is_deterministic_and_within_window() -> None:
    workspace_id = uuid4()
    first = pmt.jitter_seconds_for_workspace(workspace_id)
    second = pmt.jitter_seconds_for_workspace(workspace_id)
    assert first == second
    assert 0 <= first < pmt._JITTER_WINDOW_SECONDS


# ── Gates separados por fonte, e o no-op quando não há caminho executável ──
#
# Estes testes cobrem o defeito que a suíte não pegava: com o gate fechado e a
# fixture ausente (o caso da imagem de produção, que exclui `tests/`), o worker
# lia um arquivo inexistente e levantava FileNotFoundError, que `autoretry_for`
# não cobre. Ver docstring de app/workers/process_monitoring_tasks.py.


@pytest.mark.asyncio
async def test_poll_e_no_op_quando_gate_fechado_e_fixture_ausente(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
) -> None:
    def _fixture_nunca_lida(*args, **kwargs):
        raise AssertionError(
            "a fixture não pode ser lida quando não está no disco: "
            "é exatamente o FileNotFoundError diário que este guard evita"
        )

    monkeypatch.setattr(pmt, "process_monitoring_simulation_available", lambda: False)
    monkeypatch.setattr(pmt, "load_djen_fixture", _fixture_nunca_lida)
    monkeypatch.setattr(pmt, "load_datajud_fixture", _fixture_nunca_lida)

    await _seed_plan(db_session, auth_context.workspace_id)
    service = ProcessMonitoringService(db_session)
    await service.register_process(
        auth_context.workspace_id,
        numero_cnj=VALID_CNJ_1,
        created_by_user_id=auth_context.user_id,
    )
    await db_session.commit()

    assert await pmt.run_djen_poll_for_workspace(db_session, auth_context.workspace_id) == 0
    assert await pmt.run_datajud_poll_for_workspace(db_session, auth_context.workspace_id) == 0


@pytest.mark.asyncio
async def test_tick_nao_despacha_fan_out_sem_caminho_executavel(monkeypatch) -> None:
    def _nunca_despacha(*args, **kwargs):
        raise AssertionError("o tick não pode despachar workspace sem caminho executável")

    monkeypatch.setattr(pmt, "process_monitoring_simulation_available", lambda: False)
    monkeypatch.setattr(pmt.djen_poll_workspace, "apply_async", _nunca_despacha)
    monkeypatch.setattr(pmt.datajud_poll_workspace, "apply_async", _nunca_despacha)

    assert await pmt._async_djen_poll_tick() == {"candidates": 0, "dispatched": 0}
    assert await pmt._async_datajud_poll_tick() == {"candidates": 0, "dispatched": 0}


@pytest.mark.asyncio
async def test_gate_do_djen_aberto_nao_abre_o_do_datajud(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
) -> None:
    """DJEN e DataJud são fontes independentes: abrir uma não abre a outra."""
    chamadas: list[str] = []

    async def _djen_live(*args, **kwargs):
        chamadas.append("djen_live")
        return []

    async def _datajud_live(*args, **kwargs):
        raise AssertionError("cliente do DataJud não pode ser chamado com o gate dele fechado")

    def _djen_fixture_nao_lida(*args, **kwargs):
        raise AssertionError("com o gate do DJEN aberto, o modo simulado não deve ser usado")

    monkeypatch.setattr(pmt, "djen_live_delivery_allowed", lambda: True)
    monkeypatch.setattr(pmt, "datajud_live_delivery_allowed", lambda: False)
    monkeypatch.setattr(pmt.DjenComunicaClient, "fetch_comunicacoes", _djen_live)
    monkeypatch.setattr(pmt.DataJudClient, "fetch_movimentos", _datajud_live)
    monkeypatch.setattr(pmt, "load_djen_fixture", _djen_fixture_nao_lida)

    await _seed_plan(db_session, auth_context.workspace_id)
    service = ProcessMonitoringService(db_session)
    await service.register_process(
        auth_context.workspace_id,
        numero_cnj=VALID_CNJ_1,
        created_by_user_id=auth_context.user_id,
        oab_vinculada="68812/DF",
    )
    await db_session.commit()

    await pmt.run_djen_poll_for_workspace(db_session, auth_context.workspace_id)
    assert chamadas == ["djen_live"]

    # O DataJud, com o gate fechado, roda em modo simulado contra a fixture,
    # sem tocar o cliente real: a abertura do DJEN não vazou para ele.
    assert await pmt.run_datajud_poll_for_workspace(db_session, auth_context.workspace_id) == 1


# ── Sprint 4 PR A: o módulo passa a ter caminho executável ──────────────


def test_janela_de_jitter_cabe_no_visibility_timeout() -> None:
    """jitter + limite de tempo + folga de fila têm de caber em 1h.

    Com `task_acks_late` sobre Redis, estourar o `visibility_timeout` faz o
    broker reentregar e o mesmo poll rodar duas vezes em paralelo. A folga de
    600s cobre espera por slot: a fila é única e a concorrência é 4.
    """
    assert pmt._JITTER_WINDOW_SECONDS + pmt._TASK_TIME_LIMIT + 600 <= 3600


def test_erro_de_gate_e_erro_de_dominio_do_modulo() -> None:
    """S43: herdava de RuntimeError e escapava de todo `except ProcessMonitoringError`."""
    from app.services.process_monitoring_errors import (
        ProcessMonitoringError,
        ProcessMonitoringLiveDeliveryBlockedError,
    )

    assert issubclass(ProcessMonitoringLiveDeliveryBlockedError, ProcessMonitoringError)


@pytest.mark.asyncio
async def test_workspace_sem_addon_nao_chega_a_consultar_a_fonte(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
) -> None:
    """S42: o entitlement só era checado na ingestão, depois da consulta paga."""
    monkeypatch.setattr(pmt, "djen_live_delivery_allowed", lambda: True)
    monkeypatch.setattr(pmt, "_has_executable_path", lambda *a, **k: True)

    async def _nunca(*args, **kwargs):
        raise AssertionError("workspace sem add-on não pode gerar consulta externa")

    monkeypatch.setattr(pmt.DjenComunicaClient, "fetch_comunicacoes", _nunca)

    # sem _seed_plan: o add-on não está contratado
    created = await pmt.run_djen_poll_for_workspace(db_session, auth_context.workspace_id)
    assert created == 0


@pytest.mark.asyncio
async def test_falha_em_uma_oab_nao_derruba_as_outras(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
) -> None:
    """S43: o fetch ficava fora de qualquer try e a primeira falha abortava o ciclo."""
    from app.services.process_monitoring_errors import ProcessMonitoringUpstreamUnavailableError

    await _seed_plan(db_session, auth_context.workspace_id)
    service = ProcessMonitoringService(db_session)
    for numero, oab in ((VALID_CNJ_1, "111111/DF"), (VALID_CNJ_2, "222222/DF")):
        processo = await service.register_process(
            auth_context.workspace_id,
            numero_cnj=numero,
            created_by_user_id=auth_context.user_id,
        )
        processo.oab_vinculada = oab
    await db_session.commit()

    monkeypatch.setattr(pmt, "djen_live_delivery_allowed", lambda: True)
    monkeypatch.setattr(pmt, "_has_executable_path", lambda *a, **k: True)

    consultadas: list[str] = []

    async def _fetch(self, *, numero_oab, uf_oab, **kwargs):
        consultadas.append(numero_oab)
        if numero_oab == "111111":
            raise ProcessMonitoringUpstreamUnavailableError("DJEN fora do ar")
        return [
            {
                "id": 9001,
                "numero_processo": VALID_CNJ_2,
                "texto": "Fica intimada a parte para manifestar em 15 dias.",
                "data_disponibilizacao": "2026-09-03T12:00:00",
                "tipoComunicacao": "Intimação",
            }
        ]

    monkeypatch.setattr(pmt.DjenComunicaClient, "fetch_comunicacoes", _fetch)

    created = await pmt.run_djen_poll_for_workspace(db_session, auth_context.workspace_id)

    assert consultadas == ["111111", "222222"]  # não parou na primeira
    assert created == 1  # e o que a segunda trouxe foi ingerido


@pytest.mark.asyncio
async def test_o_que_a_primeira_oab_trouxe_persiste_quando_a_segunda_explode(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
) -> None:
    """Commit incremental: com um único commit no fim, um estouro de limite de
    tempo no meio jogava fora tudo o que já tinha sido coletado."""
    await _seed_plan(db_session, auth_context.workspace_id)
    service = ProcessMonitoringService(db_session)
    for numero, oab in ((VALID_CNJ_1, "111111/DF"), (VALID_CNJ_2, "222222/DF")):
        processo = await service.register_process(
            auth_context.workspace_id,
            numero_cnj=numero,
            created_by_user_id=auth_context.user_id,
        )
        processo.oab_vinculada = oab
    await db_session.commit()

    monkeypatch.setattr(pmt, "djen_live_delivery_allowed", lambda: True)
    monkeypatch.setattr(pmt, "_has_executable_path", lambda *a, **k: True)

    class SoftTimeLimitExceededSimulado(BaseException):
        """Fora de Exception, como o SoftTimeLimitExceeded do Celery."""

    async def _fetch(self, *, numero_oab, uf_oab, **kwargs):
        if numero_oab == "111111":
            return [
                {
                    "id": 9002,
                    "numero_processo": VALID_CNJ_1,
                    "texto": "Fica intimada a parte para manifestar em 15 dias.",
                    "data_disponibilizacao": "2026-09-03T12:00:00",
                    "tipoComunicacao": "Intimação",
                }
            ]
        raise SoftTimeLimitExceededSimulado()

    monkeypatch.setattr(pmt.DjenComunicaClient, "fetch_comunicacoes", _fetch)

    with pytest.raises(SoftTimeLimitExceededSimulado):
        await pmt.run_djen_poll_for_workspace(db_session, auth_context.workspace_id)

    await db_session.rollback()
    publicacoes = (
        await db_session.scalars(
            select(ProcessPublication).where(
                ProcessPublication.workspace_id == auth_context.workspace_id
            )
        )
    ).all()
    assert len(publicacoes) == 1  # o da primeira OAB sobreviveu ao estouro da segunda


@pytest.mark.asyncio
async def test_tick_enumera_workspaces_e_relata_quantos_despachou(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
) -> None:
    """Contrato novo do S44: o tick conta candidatos e despachados.

    Em SQLite não há RLS, então este teste NÃO prova o defeito — hoje a consulta
    antiga também devolveria linhas aqui. A prova do S44 vive no harness
    PostgreSQL, em tests/db/test_legal_crm_rls_postgresql.py, onde a policy
    fail-closed de `monitored_processes` existe de verdade.
    """
    despachados: list[str] = []
    monkeypatch.setattr(pmt, "_has_executable_path", lambda *a, **k: True)
    monkeypatch.setattr(
        pmt.djen_poll_workspace,
        "apply_async",
        lambda args, countdown=0, **kwargs: despachados.append(args[0]),
    )

    monkeypatch.setattr(pmt, "WorkerSessionLocal", lambda: _SessaoDeTeste(db_session))

    resultado = await pmt._async_djen_poll_tick()

    assert resultado["dispatched"] == resultado["candidates"]
    assert str(auth_context.workspace_id) in despachados


class _SessaoDeTeste:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *args):
        return False


@pytest.mark.asyncio
async def test_workspace_sem_assinatura_nao_derruba_a_task_fora_do_modo_local(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
) -> None:
    """S58: `lock_workspace` ficava fora do try de `_require_module_contracted`.

    Workspace sem registro de assinatura (piloto, demo, provisionamento em
    andamento) levantava EntitlementNotConfiguredError crua sempre que
    `is_local_only` fosse False, que é o caso de produção. O teste que já
    existia passava trivialmente porque, em modo local, `lock_workspace`
    devolve False em vez de levantar — ele não pegava este defeito.

    Com o fan-out por workspace ativo, isto passaria a ser um estouro diário
    por workspace sem assinatura. Tem de virar "não contratou" e devolver 0.
    """
    from app.core.config import settings

    monkeypatch.setattr(type(settings), "is_local_only", property(lambda self: False))

    async def _nunca(*args, **kwargs):
        raise AssertionError("workspace sem assinatura não pode gerar consulta externa")

    monkeypatch.setattr(pmt.DjenComunicaClient, "fetch_comunicacoes", _nunca)
    monkeypatch.setattr(pmt, "djen_live_delivery_allowed", lambda: True)
    monkeypatch.setattr(pmt, "_has_executable_path", lambda *a, **k: True)

    # sem _seed_plan: nenhuma Subscription para este workspace
    created = await pmt.run_djen_poll_for_workspace(db_session, auth_context.workspace_id)
    assert created == 0


# ── Sprint 4 PR B1: o módulo passa a ingerir de verdade ─────────────────


def _item_djen(numero_cnj_mascarado: str, *, id_: int, dia: str = "2026-09-01") -> dict:
    """Formato real do Comunica: 20 dígitos em numero_processo, máscara à parte, só data."""
    return {
        "id": id_,
        "numero_processo": numero_cnj_mascarado.replace("-", "").replace(".", ""),
        "numeroprocessocommascara": numero_cnj_mascarado,
        "data_disponibilizacao": dia,
        "tipoComunicacao": "Intimação",
        "texto": "Fica intimada a parte para manifestar em 15 dias.",
        "status": "P",
        "ativo": True,
    }


async def _processo_com_oab(db, auth_context, numero, oab):
    processo = await ProcessMonitoringService(db).register_process(
        auth_context.workspace_id, numero_cnj=numero, created_by_user_id=auth_context.user_id
    )
    processo.oab_vinculada = oab
    await db.commit()
    return processo


def _live_djen(monkeypatch):
    monkeypatch.setattr(pmt, "djen_live_delivery_allowed", lambda: True)
    monkeypatch.setattr(pmt, "_has_executable_path", lambda *a, **k: True)


@pytest.mark.asyncio
async def test_djen_casa_por_digitos_e_vincula_ao_processo_monitorado(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
) -> None:
    """S48/S65: a fonte manda 20 dígitos; casar máscara contra dígitos era zero
    garantido, e passar os dígitos ao serviço criava publicação sem vínculo."""
    await _seed_plan(db_session, auth_context.workspace_id)
    processo = await _processo_com_oab(db_session, auth_context, VALID_CNJ_1, "68812/DF")
    _live_djen(monkeypatch)

    async def _fetch(self, **kwargs):
        return [_item_djen(VALID_CNJ_1, id_=7001)]

    monkeypatch.setattr(pmt.DjenComunicaClient, "fetch_comunicacoes", _fetch)

    assert await pmt.run_djen_poll_for_workspace(db_session, auth_context.workspace_id) == 1
    publicacao = (
        await db_session.scalars(
            select(ProcessPublication).where(ProcessPublication.external_id == "7001")
        )
    ).one()
    assert publicacao.monitored_process_id == processo.id
    assert publicacao.numero_cnj == VALID_CNJ_1  # canônico, não os dígitos crus


@pytest.mark.asyncio
async def test_djen_consulta_uma_janela_de_dias_e_com_a_uf_da_propria_oab(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
) -> None:
    """S39 + UF: janela [hoje−N, hoje] e a UF sai do campo, nunca "DF" fixo."""
    await _seed_plan(db_session, auth_context.workspace_id)
    await _processo_com_oab(db_session, auth_context, VALID_CNJ_1, "123456/SP")
    _live_djen(monkeypatch)
    monkeypatch.setattr(pmt.settings, "KLM_DJEN_LOOKBACK_DAYS", 5)
    chamadas: list[dict] = []

    async def _fetch(self, **kwargs):
        chamadas.append(kwargs)
        return []

    monkeypatch.setattr(pmt.DjenComunicaClient, "fetch_comunicacoes", _fetch)

    await pmt.run_djen_poll_for_workspace(db_session, auth_context.workspace_id)

    assert len(chamadas) == 1
    assert chamadas[0]["numero_oab"] == "123456"
    assert chamadas[0]["uf_oab"] == "SP"
    janela = chamadas[0]["data_disponibilizacao_fim"] - chamadas[0]["data_disponibilizacao_inicio"]
    assert janela.days == 5


@pytest.mark.asyncio
async def test_oab_sem_uf_nao_e_consultada_com_uf_chutada(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch, capsys
) -> None:
    """Consultar com UF chutada ingere intimação de OUTRO advogado com o mesmo número."""
    await _seed_plan(db_session, auth_context.workspace_id)
    await _processo_com_oab(db_session, auth_context, VALID_CNJ_1, "68812")
    _live_djen(monkeypatch)

    async def _nunca(self, **kwargs):
        raise AssertionError("OAB sem UF não pode gerar consulta")

    monkeypatch.setattr(pmt.DjenComunicaClient, "fetch_comunicacoes", _nunca)

    assert await pmt.run_djen_poll_for_workspace(db_session, auth_context.workspace_id) == 0
    assert "oab_invalida" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_data_do_djen_e_o_dia_brasileiro_e_nao_a_vespera_em_utc(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
) -> None:
    """S61: "2026-09-01" à meia-noite UTC é 21h de 31/08 em Brasília."""
    await _seed_plan(db_session, auth_context.workspace_id)
    await _processo_com_oab(db_session, auth_context, VALID_CNJ_1, "68812/DF")
    _live_djen(monkeypatch)

    async def _fetch(self, **kwargs):
        return [_item_djen(VALID_CNJ_1, id_=7002, dia="2026-09-01")]

    monkeypatch.setattr(pmt.DjenComunicaClient, "fetch_comunicacoes", _fetch)
    await pmt.run_djen_poll_for_workspace(db_session, auth_context.workspace_id)

    publicacao = (
        await db_session.scalars(
            select(ProcessPublication).where(ProcessPublication.external_id == "7002")
        )
    ).one()
    assert publicacao.published_at.astimezone(pmt._FUSO_BRASIL).date().isoformat() == "2026-09-01"


@pytest.mark.asyncio
async def test_djen_4xx_grava_last_error_e_o_sucesso_seguinte_limpa(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
) -> None:
    from app.services.process_monitoring_errors import ProcessMonitoringUpstreamRejectedError

    await _seed_plan(db_session, auth_context.workspace_id)
    processo = await _processo_com_oab(db_session, auth_context, VALID_CNJ_1, "68812/DF")
    _live_djen(monkeypatch)
    respostas = [ProcessMonitoringUpstreamRejectedError("400", status=400), []]

    async def _fetch(self, **kwargs):
        resposta = respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    monkeypatch.setattr(pmt.DjenComunicaClient, "fetch_comunicacoes", _fetch)

    await pmt.run_djen_poll_for_workspace(db_session, auth_context.workspace_id)
    await db_session.refresh(processo)
    assert processo.last_error == "DJEN: HTTP 400 para a OAB 68812/DF"

    await pmt.run_djen_poll_for_workspace(db_session, auth_context.workspace_id)
    await db_session.refresh(processo)
    assert processo.last_error is None


@pytest.mark.asyncio
async def test_sucesso_do_djen_nao_apaga_erro_do_datajud(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id)
    processo = await _processo_com_oab(db_session, auth_context, VALID_CNJ_1, "68812/DF")
    processo.last_error = "DataJud: HTTP 404 em tjdft"
    await db_session.commit()
    _live_djen(monkeypatch)

    async def _fetch(self, **kwargs):
        return []

    monkeypatch.setattr(pmt.DjenComunicaClient, "fetch_comunicacoes", _fetch)
    await pmt.run_djen_poll_for_workspace(db_session, auth_context.workspace_id)
    await db_session.refresh(processo)
    assert processo.last_error == "DataJud: HTTP 404 em tjdft"  # continua verdadeiro


@pytest.mark.asyncio
async def test_cadastro_grava_o_alias_do_datajud_e_o_motivo_quando_nao_ha(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    """S50: o tribunal sai do próprio número; sem alias, o erro nasce no cadastro."""
    await _seed_plan(db_session, auth_context.workspace_id)
    service = ProcessMonitoringService(db_session)

    coberto = await service.register_process(
        auth_context.workspace_id, numero_cnj=VALID_CNJ_1, tribunal="TJDFT",
        created_by_user_id=auth_context.user_id,
    )
    assert coberto.tribunal == "tjdft"
    assert coberto.last_error is None

    texto_livre = await service.register_process(
        auth_context.workspace_id, numero_cnj="0000001-68.2026.8.26.0100",
        tribunal="Tribunal de Justiça de São Paulo", created_by_user_id=auth_context.user_id,
    )
    assert texto_livre.tribunal == "tjsp"  # derivado do 8.26, não do texto

    cjf = await service.register_process(
        auth_context.workspace_id, numero_cnj="0000001-31.2026.4.90.0000",
        created_by_user_id=auth_context.user_id,
    )
    assert cjf.tribunal is None
    assert cjf.last_error and cjf.last_error.startswith("DataJud: Tribunal não coberto")


@pytest.mark.asyncio
async def test_datajud_escolhe_o_documento_mais_recente_entre_os_graus(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    """S63: a fixture traz G2 (antigo) antes de G1 (recente), de propósito."""
    await _seed_plan(db_session, auth_context.workspace_id)
    await ProcessMonitoringService(db_session).register_process(
        auth_context.workspace_id, numero_cnj=VALID_CNJ_1, created_by_user_id=auth_context.user_id,
    )
    await db_session.commit()

    assert await pmt.run_datajud_poll_for_workspace(db_session, auth_context.workspace_id) == 1
    publicacao = (
        await db_session.scalars(
            select(ProcessPublication).where(
                ProcessPublication.fonte == ProcessPublicationSource.datajud
            )
        )
    ).one()
    assert publicacao.tipo == "Decisão"  # último movimento do G1, e não a Distribuição do G2


@pytest.mark.asyncio
async def test_datajud_com_a_chave_recusada_em_todos_os_processos_vira_erro_de_ciclo(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch, capsys
) -> None:
    """S64: chave rotacionada dava um warning por processo e nenhum erro."""
    from app.services.process_monitoring_errors import ProcessMonitoringUpstreamRejectedError

    await _seed_plan(db_session, auth_context.workspace_id)
    service = ProcessMonitoringService(db_session)
    for numero in (VALID_CNJ_1, VALID_CNJ_2):
        await service.register_process(
            auth_context.workspace_id, numero_cnj=numero, created_by_user_id=auth_context.user_id
        )
    await db_session.commit()
    monkeypatch.setattr(pmt, "datajud_live_delivery_allowed", lambda: True)
    monkeypatch.setattr(pmt, "_has_executable_path", lambda *a, **k: True)

    async def _401(self, **kwargs):
        raise ProcessMonitoringUpstreamRejectedError("401", status=401)

    monkeypatch.setattr(pmt.DataJudClient, "fetch_movimentos", _401)

    assert await pmt.run_datajud_poll_for_workspace(db_session, auth_context.workspace_id) == 0
    saida = capsys.readouterr().out
    assert "datajud_chave_recusada" in saida
    processos = (await db_session.scalars(select(MonitoredProcess))).all()
    assert {p.last_error for p in processos} == {"DataJud: HTTP 401 em tjdft"}


@pytest.mark.asyncio
async def test_alerta_de_chave_recusada_dispara_mesmo_com_processo_nao_coberto_na_carteira(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch, capsys
) -> None:
    """Revisão do #34: com len(monitored) no denominador, um processo do CJF (nunca
    consultado) mascarava o alerta mesmo com 100% dos consultados em 401."""
    from app.services.process_monitoring_errors import ProcessMonitoringUpstreamRejectedError

    await _seed_plan(db_session, auth_context.workspace_id)
    service = ProcessMonitoringService(db_session)
    for numero in (VALID_CNJ_1, VALID_CNJ_2, "0000001-31.2026.4.90.0000"):
        await service.register_process(
            auth_context.workspace_id, numero_cnj=numero, created_by_user_id=auth_context.user_id
        )
    await db_session.commit()
    monkeypatch.setattr(pmt, "datajud_live_delivery_allowed", lambda: True)
    monkeypatch.setattr(pmt, "_has_executable_path", lambda *a, **k: True)

    async def _401(self, **kwargs):
        raise ProcessMonitoringUpstreamRejectedError("401", status=401)

    monkeypatch.setattr(pmt.DataJudClient, "fetch_movimentos", _401)

    await pmt.run_datajud_poll_for_workspace(db_session, auth_context.workspace_id)
    assert "datajud_chave_recusada" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_alias_informado_errado_perde_para_o_derivado_do_cnj(
    db_session: AsyncSession, auth_context: AuthContext, capsys
) -> None:
    """Revisão do #34: "tjsp" num 8.07 consultaria o índice errado para sempre, sem 404."""
    await _seed_plan(db_session, auth_context.workspace_id)
    processo = await ProcessMonitoringService(db_session).register_process(
        auth_context.workspace_id, numero_cnj=VALID_CNJ_1, tribunal="tjsp",
        created_by_user_id=auth_context.user_id,
    )
    assert processo.tribunal == "tjdft"
    assert "tribunal_informado_diverge_do_cnj" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_ponta_a_ponta_sem_oab_nao_consulta_e_depois_do_patch_consulta_com_a_uf_certa(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch, capsys
) -> None:
    """B2: o produto nunca mandava OAB, então nenhum processo era consultado no DJEN."""
    await _seed_plan(db_session, auth_context.workspace_id)
    service = ProcessMonitoringService(db_session)
    processo = await service.register_process(
        auth_context.workspace_id, numero_cnj=VALID_CNJ_1, created_by_user_id=auth_context.user_id,
    )
    await db_session.commit()
    _live_djen(monkeypatch)
    chamadas: list[dict] = []

    async def _fetch(self, **kwargs):
        chamadas.append(kwargs)
        return [_item_djen(VALID_CNJ_1, id_=7010)]

    monkeypatch.setattr(pmt.DjenComunicaClient, "fetch_comunicacoes", _fetch)

    assert await pmt.run_djen_poll_for_workspace(db_session, auth_context.workspace_id) == 0
    assert chamadas == []
    assert "processo_sem_oab" in capsys.readouterr().out

    await service.update_process(
        auth_context.workspace_id, processo.id, campos={"oab_vinculada": "OAB/DF 68.812"}
    )
    await db_session.commit()

    assert await pmt.run_djen_poll_for_workspace(db_session, auth_context.workspace_id) == 1
    assert len(chamadas) == 1
    assert (chamadas[0]["numero_oab"], chamadas[0]["uf_oab"]) == ("68812", "DF")
    publicacao = (
        await db_session.scalars(
            select(ProcessPublication).where(ProcessPublication.external_id == "7010")
        )
    ).one()
    assert publicacao.monitored_process_id == processo.id


@pytest.mark.asyncio
async def test_legado_fora_da_canonica_nao_e_consultado_e_ganha_last_error(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
) -> None:
    """Revisão do #35: a tela diz "não consultado até corrigir"; o worker faz o que a tela diz."""
    await _seed_plan(db_session, auth_context.workspace_id)
    # gravado direto no banco, fora da canônica, como um legado seria
    processo = await _processo_com_oab(db_session, auth_context, VALID_CNJ_1, "068812/DF")
    _live_djen(monkeypatch)

    async def _nunca(self, **kwargs):
        raise AssertionError("legado fora da canônica não pode gerar consulta")

    monkeypatch.setattr(pmt.DjenComunicaClient, "fetch_comunicacoes", _nunca)

    assert await pmt.run_djen_poll_for_workspace(db_session, auth_context.workspace_id) == 0
    await db_session.refresh(processo)
    assert processo.last_error and processo.last_error.startswith("DJEN: OAB fora do formato")


# ── S59: todos os movimentos da janela, não só o último ─────────────────


@pytest.mark.asyncio
async def test_datajud_ingere_todos_os_movimentos_da_janela_em_ordem(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
) -> None:
    """Com o relógio em 21/07/2026 e janela de 5 dias, os dois movimentos de
    20/07 entram (Juntada e Decisão), em ordem; os de março e maio ficam fora."""
    from datetime import date as _date

    monkeypatch.setattr(pmt, "_hoje", lambda: _date(2026, 7, 21))
    monkeypatch.setattr(pmt.settings, "KLM_DJEN_LOOKBACK_DAYS", 5)
    await _seed_plan(db_session, auth_context.workspace_id)
    await ProcessMonitoringService(db_session).register_process(
        auth_context.workspace_id, numero_cnj=VALID_CNJ_1, created_by_user_id=auth_context.user_id
    )
    await db_session.commit()

    assert await pmt.run_datajud_poll_for_workspace(db_session, auth_context.workspace_id) == 2
    tipos = (
        await db_session.scalars(
            select(ProcessPublication.tipo)
            .where(ProcessPublication.fonte == ProcessPublicationSource.datajud)
            .order_by(ProcessPublication.published_at.asc())
        )
    ).all()
    assert tipos == ["Juntada de Petição", "Decisão"]
    assert await pmt.run_datajud_poll_for_workspace(db_session, auth_context.workspace_id) == 0


@pytest.mark.asyncio
async def test_datajud_fora_da_janela_so_o_ultimo_movimento_entra(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
) -> None:
    """Processo dormente cadastrado meses depois: o último andamento entra sempre."""
    from datetime import date as _date

    monkeypatch.setattr(pmt, "_hoje", lambda: _date(2026, 12, 1))
    monkeypatch.setattr(pmt.settings, "KLM_DJEN_LOOKBACK_DAYS", 5)
    await _seed_plan(db_session, auth_context.workspace_id)
    await ProcessMonitoringService(db_session).register_process(
        auth_context.workspace_id, numero_cnj=VALID_CNJ_1, created_by_user_id=auth_context.user_id
    )
    await db_session.commit()

    assert await pmt.run_datajud_poll_for_workspace(db_session, auth_context.workspace_id) == 1
    (tipo,) = (
        await db_session.scalars(
            select(ProcessPublication.tipo).where(
                ProcessPublication.fonte == ProcessPublicationSource.datajud
            )
        )
    ).all()
    assert tipo == "Decisão"


def test_movimentos_a_ingerir_ignora_malformado_ordena_e_respeita_o_teto() -> None:
    from datetime import date as _date

    movimentos = [
        {"codigo": 1, "dataHora": "2026-07-20T10:00:00.000Z", "nome": "B"},
        {"codigo": 2, "nome": "sem data"},
        {"codigo": 3, "dataHora": "2026-07-19T10:00:00.000Z", "nome": "A"},
        {"dataHora": "2026-07-20T12:00:00.000Z", "nome": "sem codigo"},
        {"codigo": 4, "dataHora": "2026-01-01T10:00:00.000Z", "nome": "antigo"},
    ]
    escolhidos = pmt._movimentos_a_ingerir(
        movimentos, hoje=_date(2026, 7, 21), lookback_days=5, teto=1
    )
    assert [m["nome"] for _, m in escolhidos] == ["B"], "teto pelos mais recentes; malformados fora"
    todos = pmt._movimentos_a_ingerir(movimentos, hoje=_date(2026, 7, 21), lookback_days=5)
    assert [m["nome"] for _, m in todos] == ["A", "B"]
    dormente = pmt._movimentos_a_ingerir(movimentos, hoje=_date(2026, 12, 1), lookback_days=5)
    assert [m["nome"] for _, m in dormente] == ["B"], "o último entra sempre"
