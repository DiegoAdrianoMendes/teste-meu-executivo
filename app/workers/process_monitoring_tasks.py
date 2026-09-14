"""Workers do módulo processual contratável à parte — DJEN e DataJud.

Beat 1x/dia (`djen_poll_tick` / `datajud_poll_tick`), cada workspace ATIVO é
despachado com jitter (hash do workspace_id, espalhado por
uma janela de 40 min) para não gerar um pico simultâneo contra a fonte — mesmo
racional de `app/workers/lead_hydration_tasks.py::_retry_at` e
`app/workers/omnichannel_scheduled_tasks.py::_retry_at`.

Fail-closed, com gate SEPARADO por fonte: `djen_live_delivery_allowed()` e
`datajud_live_delivery_allowed()`. DJEN e DataJud têm credencial e
disponibilidade próprias, e abrir uma nunca abre a outra.

Com o gate fechado há dois estados possíveis, e a diferença importa:

- **Fixture no disco** (árvore de código, CI, desenvolvimento local): o tick
  roda em MODO SIMULADO contra `tests/fixtures/djen/`, filtrada para os
  números CNJ monitorados no workspace, nunca vazando dado de outro tenant.
- **Fixture ausente** (imagem de produção, que exclui `tests/`): NO-OP
  registrado em log. Ler a fixture inexistente levantaria FileNotFoundError,
  que `autoretry_for` não cobre e que mataria o beat todo dia em silêncio.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.klm_runtime import (
    datajud_live_delivery_allowed,
    djen_live_delivery_allowed,
)
from app.core.logging import logger
from app.db.session import WorkerSessionLocal
from app.db.tenant import set_workspace_context
from app.models.process_monitoring import (
    MonitoredProcess,
    MonitoredProcessStatus,
    ProcessPublicationSource,
)
from app.models.workspace import Workspace, WorkspaceStatus
from app.services.cnj_numero import numero_cnj_digitos
from app.services.djen_datajud_clients import (
    DataJudClient,
    DjenComunicaClient,
    load_datajud_fixture,
    load_djen_fixture,
    process_monitoring_simulation_available,
)
from app.services.oab_numero import InvalidOabError, normalizar_oab
from app.services.prazo_texto_heuristica import extrair_prazo_do_texto
from app.services.process_monitoring_errors import (
    ProcessMonitoringUpstreamRejectedError,
    ProcessMonitoringUpstreamUnavailableError,
)
from app.services.process_monitoring_service import (
    ProcessMonitoringError,
    ProcessMonitoringModuleNotContractedError,
    ProcessMonitoringService,
    limpar_erro_de_fonte,
    registrar_erro_de_fonte,
    resolver_alias_tribunal,
)
from app.workers.celery_app import celery_app

# Jitter espalhado a partir do horário do beat, para não concentrar toda a base
# num único instante contra a fonte pública.
#
# A janela tem teto: com `task_acks_late` sobre Redis, um `countdown` maior que
# o `visibility_timeout` do transporte (default de 1h) faz o broker reentregar
# a mensagem, e o poll do mesmo workspace roda duas a quatro vezes. A ingestão
# é idempotente por external_id, então não duplica dado, mas duplica chamada
# externa e log. 50 minutos ficam com folga abaixo do teto sem mexer em
# `broker_transport_options`, que é global e mudaria a semântica de reentrega
# de toda a plataforma.
#
# A conta que fixa 40 minutos: jitter (2400s) + time_limit da task (300s) +
# folga para contenção de fila (600s) = 3300s, abaixo dos 3600s do default.
# A folga de 600s não é decorativa: a fila é única e a concorrência é 4,
# compartilhada com dezenas de beats de minuto, então um poll pode esperar
# por um slot. Sem essa folga, a espera vira reentrega e execução dupla.
_JITTER_WINDOW_SECONDS = 40 * 60

# As datas do DJEN vêm só com dia ("2026-09-01"). Meia-noite UTC disso é 21h do
# dia anterior em Brasília, e a tela mostrava um dia a menos. O dia é o dia
# brasileiro, e é assim que a data vira instante.
_FUSO_BRASIL = ZoneInfo("America/Sao_Paulo")
_DATAJUD_MAX_MOVIMENTOS_POR_POLL = 200


def _hoje() -> date:
    """Relógio das janelas de coleta (injetável nos testes)."""
    return datetime.now(_FUSO_BRASIL).date()


def _movimentos_a_ingerir(
    movimentos: list[dict],
    *,
    hoje: date,
    lookback_days: int,
    teto: int = _DATAJUD_MAX_MOVIMENTOS_POR_POLL,
) -> list[tuple[datetime, dict]]:
    """S59: o poll ingeria só o ÚLTIMO movimento; os intermediários entre dois
    polls diários se perdiam. Agora entram todos os movimentos da janela
    (`hoje - lookback_days`, a mesma do DJEN), em ordem cronológica, com teto
    pelos mais recentes; o último movimento entra SEMPRE, para o processo
    dormente recém-cadastrado não ficar sem andamento. Movimento sem `dataHora`
    ou `codigo` é pulado com log, nunca derruba o workspace."""
    parsed: list[tuple[datetime, dict]] = []
    for movimento in movimentos:
        try:
            quando = datetime.fromisoformat(str(movimento["dataHora"]).replace("Z", "+00:00"))
            movimento["codigo"]
        except (KeyError, TypeError, ValueError):
            logger.warning("process_monitoring.datajud_movimento_malformado")
            continue
        if quando.tzinfo is None:
            quando = quando.replace(tzinfo=UTC)
        parsed.append((quando, movimento))
    if not parsed:
        return []
    parsed.sort(key=lambda item: item[0])
    ultimo = parsed[-1]
    inicio = datetime.combine(hoje - timedelta(days=lookback_days), time.min, tzinfo=_FUSO_BRASIL)
    na_janela = [item for item in parsed if item[0] >= inicio]
    escolhidos = na_janela[-teto:] if na_janela else []
    if not escolhidos or escolhidos[-1] is not ultimo:
        escolhidos.append(ultimo)
    return escolhidos

# A OAB é lida pela mesma função que a valida na captura (`normalizar_oab`):
# valor legado fora da canônica é pulado com aviso, e a UI mostra "inválida".
# Consultar o Comunica com UF chutada traz as intimações de OUTRO advogado com
# o mesmo número: pior do que não consultar.
_TASK_SOFT_TIME_LIMIT = 240
_TASK_TIME_LIMIT = 300
assert (
    _JITTER_WINDOW_SECONDS + _TASK_TIME_LIMIT + 600 <= 3600
), "jitter + limite de tempo + folga tem de caber no visibility_timeout do Redis"


async def _gate_do_modulo_aberto(db: AsyncSession, workspace_id: UUID) -> bool:
    """Checa o add-on ANTES de qualquer chamada externa, e solta o lock em seguida.

    Antes, o entitlement só era verificado dentro de `ingest_publication`, isto é,
    depois de o DJEN ou o DataJud já terem sido consultados. Workspace que desligou
    o add-on continuava gerando consulta externa, e a economia só aparecia na hora
    de gravar, quando o custo já tinha sido pago.

    O `commit` logo depois não é zelo: `lock_workspace` toma FOR KEY SHARE na linha
    do workspace, e segurar isso durante as chamadas HTTP (até 15s por OAB)
    bloquearia o FOR UPDATE de `notification_tasks` por toda a duração do poll.
    O contexto de RLS é reaplicado pelo listener na transação seguinte.
    """
    service = ProcessMonitoringService(db)
    try:
        await service.ensure_module_contracted(workspace_id)
    except ProcessMonitoringModuleNotContractedError:
        await db.rollback()
        logger.info(
            "process_monitoring.workspace_sem_addon",
            workspace_id=str(workspace_id),
            reason="modulo_nao_contratado",
        )
        return False
    await db.commit()
    return True


def _has_executable_path(live_allowed: bool, *, fonte: str) -> bool:
    """False quando não existe caminho executável para esta fonte.

    Gate aberto: o caminho é a rede. Gate fechado com fixture no disco: o
    caminho é o modo simulado. Gate fechado e fixture ausente: NÃO HÁ caminho,
    e a resposta certa é no-op registrado, nunca tentar ler um arquivo que não
    veio na imagem.
    """
    if live_allowed:
        return True
    if process_monitoring_simulation_available():
        return True
    logger.info(
        "process_monitoring.no_op_sem_caminho_executavel",
        extra={
            "fonte": fonte,
            "motivo": "gate fechado e fixture do modo simulado ausente na imagem",
        },
    )
    return False


def jitter_seconds_for_workspace(workspace_id: UUID | str) -> int:
    digest = hashlib.sha256(str(workspace_id).encode()).hexdigest()
    return int(digest[:8], 16) % _JITTER_WINDOW_SECONDS


async def _active_workspace_ids(db: AsyncSession) -> list[UUID]:
    """Enumera workspaces ativos para o fan-out do beat.

    A consulta anterior lia `monitored_processes` direto, procurando só quem
    tem processo ativo. Parecia mais econômica e não funcionava: a tabela está
    em PROCESS_MONITORING_DIRECT_TENANT_TABLES com FORCE ROW LEVEL SECURITY e
    predicado fail-closed, e a sessão do beat não tem workspace no contexto.
    Em qualquer Postgres a consulta devolvia zero linhas, sem erro, e o tick
    registrava sucesso tendo despachado nada.

    Não dá para filtrar aqui: `set_workspace_context` recusa trocar de workspace
    na mesma sessão, então testar cada workspace exigiria uma sessão por
    workspace no tick, que é o mesmo custo de despachar. O filtro passou para a
    task de workspace, que morre barato no gate do add-on antes de qualquer rede.

    `workspaces` não está sob RLS de tenant, então esta leitura é legítima sem
    contexto. Mesmo idioma de `conversation_auditor_tasks._recover_active_workspaces`
    e `storage_tasks._active_workspace_ids`.

    Sem `limit`: teto com `order_by` fixo é truncamento determinístico, e o
    workspace de número N+1 nunca seria consultado, todo dia, sem uma linha de
    log. Seria o mesmo defeito que este módulo está corrigindo, num add-on pago.
    """
    result = await db.scalars(
        select(Workspace.id)
        .where(
            Workspace.deleted_at.is_(None),
            Workspace.is_active.is_(True),
            Workspace.status == WorkspaceStatus.active,
        )
        .order_by(Workspace.created_at, Workspace.id)
    )
    return list(result.all())


# ── DJEN ────────────────────────────────────────────────────────────────


async def run_djen_poll_for_workspace(db: AsyncSession, workspace_id: UUID) -> int:
    """Ingesta as publicações DJEN novas para os processos ativos do workspace.

    Retorna a quantidade de publicações efetivamente criadas (dedupe já
    exclui reprocessamentos). Nunca levanta em caso de módulo não
    contratado — apenas loga e retorna 0 (o beat não deve falhar por um
    workspace que desligou o add-on entre o cadastro e o tick).
    """
    if not _has_executable_path(djen_live_delivery_allowed(), fonte="djen"):
        return 0
    await set_workspace_context(db, workspace_id)
    if not await _gate_do_modulo_aberto(db, workspace_id):
        return 0
    service = ProcessMonitoringService(db)

    active_processes = await db.execute(
        select(MonitoredProcess).where(
            MonitoredProcess.workspace_id == workspace_id,
            MonitoredProcess.status == MonitoredProcessStatus.ativo,
        )
    )
    monitored = list(active_processes.scalars().all())
    if not monitored:
        return 0
    # A chave de casamento com a fonte são os 20 dígitos: o Comunica devolve
    # `numero_processo` sem máscara. Casar máscara contra dígitos era zero garantido.
    monitored_by_digitos = {numero_cnj_digitos(p.numero_cnj): p for p in monitored}

    created_count = 0
    if djen_live_delivery_allowed():
        por_oab: dict[tuple[str, str], list[MonitoredProcess]] = {}
        sem_oab: list[str] = []
        sem_uf: list[str] = []
        for process in monitored:
            if not process.oab_vinculada:
                sem_oab.append(process.numero_cnj)
                continue
            try:
                canonica = normalizar_oab(process.oab_vinculada)
            except InvalidOabError as error:
                sem_uf.append(process.numero_cnj)
                registrar_erro_de_fonte(process, "DJEN", f"OAB inválida: {error}")
                continue
            if canonica != process.oab_vinculada:
                # Toda escrita canoniza; o que sobra fora da canônica é legado. O
                # módulo é fail-closed e a tela diz "não consultado até corrigir":
                # o worker faz o que a tela diz, em vez de consultar por baixo.
                sem_uf.append(process.numero_cnj)
                registrar_erro_de_fonte(
                    process,
                    "DJEN",
                    f"OAB fora do formato 123456/UF ({process.oab_vinculada}); edite",
                )
                continue
            numero, uf = canonica.split("/")
            por_oab.setdefault((numero, uf), []).append(process)
        if sem_oab:
            # S38: em modo live, processo sem OAB simplesmente não era consultado,
            # e nada dizia isso. O conserto de verdade é dar como cadastrar a OAB
            # (S49); aqui apenas se para de esconder que esses processos não são vistos.
            logger.warning(
                "process_monitoring.processo_sem_oab",
                workspace_id=str(workspace_id),
                quantidade=len(sem_oab),
                numeros_cnj=sem_oab[:20],
            )
        if sem_uf:
            logger.warning(
                "process_monitoring.oab_invalida",
                workspace_id=str(workspace_id),
                quantidade=len(sem_uf),
                numeros_cnj=sem_uf[:20],
                formato_esperado="123456/UF",
            )
            await db.commit()  # os last_error gravados acima não ficam pendentes
        client = DjenComunicaClient()
        hoje = _hoje()
        inicio = hoje - timedelta(days=max(0, settings.KLM_DJEN_LOOKBACK_DAYS))
        # Uma OAB por vez, com ingestão e commit próprios. Antes, o fetch ficava
        # fora de qualquer try e um erro na primeira OAB abortava o ciclo inteiro
        # do workspace; e o único commit no fim significava que estourar o limite
        # de tempo no meio jogava fora tudo o que já tinha sido coletado.
        for (numero_oab, uf_oab), processos_da_oab in sorted(por_oab.items()):
            try:
                items_da_oab = await client.fetch_comunicacoes(
                    numero_oab=numero_oab,
                    uf_oab=uf_oab,
                    data_disponibilizacao_inicio=inicio,
                    data_disponibilizacao_fim=hoje,
                )
            except ProcessMonitoringUpstreamRejectedError as error:
                logger.warning(
                    "process_monitoring.djen_consulta_recusada",
                    workspace_id=str(workspace_id),
                    oab=f"{numero_oab}/{uf_oab}",
                    status=error.status,
                    detail=error.detail,
                )
                # O erro é do campo que esses processos compartilham; é neles
                # que o usuário precisa ver.
                for process in processos_da_oab:
                    registrar_erro_de_fonte(
                        process, "DJEN", f"HTTP {error.status} para a OAB {numero_oab}/{uf_oab}"
                    )
                await db.commit()
                continue
            except ProcessMonitoringUpstreamUnavailableError:
                logger.warning(
                    "process_monitoring.djen_fonte_indisponivel",
                    workspace_id=str(workspace_id),
                    oab=f"{numero_oab}/{uf_oab}",
                )
                continue
            created_count += await _ingerir_itens_djen(
                service, db, workspace_id, items_da_oab, monitored_by_digitos
            )
            for process in processos_da_oab:
                limpar_erro_de_fonte(process, "DJEN")
            await db.commit()
        return created_count

    items = list(load_djen_fixture().get("items", []))
    created_count += await _ingerir_itens_djen(
        service, db, workspace_id, items, monitored_by_digitos
    )
    await db.commit()
    return created_count


async def _ingerir_itens_djen(
    service: ProcessMonitoringService,
    db: AsyncSession,
    workspace_id: UUID,
    items: list[dict],
    monitored_by_digitos: dict[str, MonitoredProcess],
) -> int:
    created_count = 0
    for item in items:
        digitos = re.sub(r"\D", "", str(item.get("numero_processo") or ""))
        process = monitored_by_digitos.get(digitos)
        if process is None:
            # A consulta é por OAB e traz processo de outro escopo: nunca ingerir
            # fora do workspace.
            continue
        if item.get("ativo") is False or item.get("data_cancelamento"):
            logger.info(
                "process_monitoring.djen_comunicacao_cancelada",
                workspace_id=str(workspace_id),
                external_id=str(item.get("id")),
            )
            continue
        texto = item.get("texto", "")
        extraido = extrair_prazo_do_texto(texto)
        dia = date.fromisoformat(str(item["data_disponibilizacao"])[:10])
        published_at = datetime.combine(dia, time.min, tzinfo=_FUSO_BRASIL)
        try:
            # `numero_cnj` é o canônico do processo monitorado, e não o cru da
            # fonte: o serviço usa esse valor para VINCULAR a publicação ao
            # processo. Passar os dígitos criava publicação órfã.
            _, created = await service.ingest_publication(
                workspace_id,
                fonte=ProcessPublicationSource.djen,
                external_id=str(item["id"]),
                numero_cnj=process.numero_cnj,
                conteudo=texto,
                published_at=published_at,
                data_disponibilizacao=dia,
                tipo=item.get("tipoComunicacao"),
                tipo_ato=extraido.tipo_ato if extraido else None,
                dias=extraido.dias if extraido else None,
                tipo_contagem=extraido.tipo_contagem if extraido else None,
            )
        except ProcessMonitoringError:
            logger.warning(
                "process_monitoring.djen_ingest_skipped",
                workspace_id=str(workspace_id),
                external_id=str(item.get("id")),
            )
            continue
        created_count += int(created)
    return created_count


# ── DataJud ────────────────────────────────────────────────────────────


async def run_datajud_poll_for_workspace(db: AsyncSession, workspace_id: UUID) -> int:
    """Ingesta o andamento mais recente do DataJud para os processos ativos."""
    if not _has_executable_path(datajud_live_delivery_allowed(), fonte="datajud"):
        return 0
    await set_workspace_context(db, workspace_id)
    if not await _gate_do_modulo_aberto(db, workspace_id):
        return 0
    service = ProcessMonitoringService(db)

    active_processes = await db.execute(
        select(MonitoredProcess).where(
            MonitoredProcess.workspace_id == workspace_id,
            MonitoredProcess.status == MonitoredProcessStatus.ativo,
        )
    )
    monitored = list(active_processes.scalars().all())
    if not monitored:
        return 0

    created_count = 0
    recusados_por_chave = 0
    consultados = 0
    live = datajud_live_delivery_allowed()
    client = DataJudClient() if live else None
    hits_fixture = [] if live else list(load_datajud_fixture().get("hits", {}).get("hits", []))

    for process in monitored:
        digitos = numero_cnj_digitos(process.numero_cnj)
        # O alias do índice sai do próprio número (J e TR). Texto livre antigo
        # ("Tribunal de Justiça do DF") não é alias e é recalculado; sem alias
        # possível, o motivo vai para `last_error` e o processo é pulado.
        alias, motivo = resolver_alias_tribunal(process.numero_cnj, process.tribunal)
        if alias is None:
            registrar_erro_de_fonte(process, "DataJud", motivo or "tribunal não coberto")
            await db.commit()
            continue
        if process.tribunal != alias:
            process.tribunal = alias
        if live:
            assert client is not None
            # Um processo por vez, protegido. Antes, o fetch ficava fora de
            # qualquer try: o primeiro 404 de sigla de tribunal inválida (S50)
            # matava o ciclo de todos os outros processos do workspace.
            consultados += 1
            try:
                payload = await client.fetch_movimentos(
                    numero_processo_sem_formatacao=digitos,
                    tribunal_sigla=alias,
                )
            except ProcessMonitoringUpstreamRejectedError as error:
                logger.warning(
                    "process_monitoring.datajud_consulta_recusada",
                    workspace_id=str(workspace_id),
                    numero_cnj=process.numero_cnj,
                    tribunal=alias,
                    status=error.status,
                )
                if error.status in (401, 403):
                    recusados_por_chave += 1
                registrar_erro_de_fonte(process, "DataJud", f"HTTP {error.status} em {alias}")
                await db.commit()
                continue
            except ProcessMonitoringUpstreamUnavailableError:
                logger.warning(
                    "process_monitoring.datajud_fonte_indisponivel",
                    workspace_id=str(workspace_id),
                    numero_cnj=process.numero_cnj,
                )
                await db.commit()  # o alias recalculado acima não fica pendente
                continue
            hits = payload.get("hits", {}).get("hits", [])
        else:
            hits = hits_fixture
        # O `_id` é identificador de documento do Elasticsearch
        # (TRF1_436_JE_16403_0000832...), nunca o CNJ. A chave de negócio é
        # `_source.numeroProcesso`. Há um documento por grau: fica o mais recente.
        hit = _hit_mais_recente(hits, digitos)
        limpar_erro_de_fonte(process, "DataJud")

        if hit is None:
            await db.commit()
            continue
        movimentos = _movimentos_a_ingerir(
            list(hit.get("_source", {}).get("movimentos", []) or []),
            hoje=_hoje(),
            lookback_days=max(0, settings.KLM_DJEN_LOOKBACK_DAYS),
        )
        if not movimentos:
            await db.commit()
            continue
        for published_at, movimento in movimentos:
            external_id = f"{process.numero_cnj}:{movimento['codigo']}:{movimento['dataHora']}"
            try:
                # Savepoint por movimento: outro worker que ingira o mesmo
                # external_id não derruba o restante do processo.
                async with db.begin_nested():
                    _, created = await service.ingest_publication(
                        workspace_id,
                        fonte=ProcessPublicationSource.datajud,
                        external_id=external_id,
                        numero_cnj=process.numero_cnj,
                        conteudo=movimento.get("nome", "Movimento processual"),
                        published_at=published_at,
                        tipo=movimento.get("nome"),
                    )
            except IntegrityError:
                created = False
            except ProcessMonitoringError:
                logger.warning(
                    "process_monitoring.datajud_ingest_skipped",
                    workspace_id=str(workspace_id),
                    numero_cnj=process.numero_cnj,
                )
                break
            created_count += int(created)
        # Commit por processo: com uma chamada externa de até 15s cada, um
        # workspace com dezenas de processos estoura o limite de tempo, e com
        # um único commit no fim tudo o que já foi lido ia junto no rollback.
        # A ingestão é idempotente por external_id, então commitar cedo é seguro.
        await db.commit()
    await db.commit()
    if live and consultados and recusados_por_chave == consultados:
        # Chave rotacionada zera o módulo em silêncio: cada processo toma 401/403,
        # um warning cada, e nenhum erro no nível do ciclo. Aqui vira erro.
        # O denominador é quem foi de fato consultado: processo sem alias (STF,
        # CJF) nunca vai à rede e não pode mascarar o alerta numa carteira mista.
        logger.error(
            "process_monitoring.datajud_chave_recusada",
            workspace_id=str(workspace_id),
            processos_consultados=consultados,
        )
    return created_count


def _hit_mais_recente(hits: list[dict], digitos: str) -> dict | None:
    candidatos = [
        h for h in hits
        if re.sub(r"\D", "", str(h.get("_source", {}).get("numeroProcesso") or "")) == digitos
    ]
    if not candidatos:
        return None
    return max(
        candidatos,
        key=lambda h: str(h.get("_source", {}).get("dataHoraUltimaAtualizacao") or ""),
    )


# ── Celery: beat diário + fan-out por workspace com jitter ───────────────


def _run_async(coroutine):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coroutine)
    finally:
        loop.close()


async def _async_djen_poll_tick() -> dict[str, int]:
    if not _has_executable_path(djen_live_delivery_allowed(), fonte="djen"):
        return {"candidates": 0, "dispatched": 0}
    async with WorkerSessionLocal() as db:
        workspace_ids = await _active_workspace_ids(db)
    dispatched = 0
    for workspace_id in workspace_ids:
        djen_poll_workspace.apply_async(
            args=[str(workspace_id)],
            countdown=jitter_seconds_for_workspace(workspace_id),
        )
        dispatched += 1
    if not dispatched:
        # Com o caminho executável aberto e nenhum workspace ativo, o tick
        # não pode se registrar como sucesso mudo: foi assim que o fan-out
        # sob RLS despachou zero por meses sem ninguém notar.
        logger.error(
            "process_monitoring.tick_sem_workspace",
            fonte="djen",
            candidates=len(workspace_ids),
        )
    else:
        logger.info(
            "process_monitoring.tick_despachado",
            fonte="djen",
            candidates=len(workspace_ids),
            dispatched=dispatched,
        )
    return {"candidates": len(workspace_ids), "dispatched": dispatched}


async def _async_datajud_poll_tick() -> dict[str, int]:
    if not _has_executable_path(datajud_live_delivery_allowed(), fonte="datajud"):
        return {"candidates": 0, "dispatched": 0}
    async with WorkerSessionLocal() as db:
        workspace_ids = await _active_workspace_ids(db)
    dispatched = 0
    for workspace_id in workspace_ids:
        datajud_poll_workspace.apply_async(
            args=[str(workspace_id)],
            countdown=jitter_seconds_for_workspace(workspace_id),
        )
        dispatched += 1
    if not dispatched:
        # Com o caminho executável aberto e nenhum workspace ativo, o tick
        # não pode se registrar como sucesso mudo: foi assim que o fan-out
        # sob RLS despachou zero por meses sem ninguém notar.
        logger.error(
            "process_monitoring.tick_sem_workspace",
            fonte="datajud",
            candidates=len(workspace_ids),
        )
    else:
        logger.info(
            "process_monitoring.tick_despachado",
            fonte="datajud",
            candidates=len(workspace_ids),
            dispatched=dispatched,
        )
    return {"candidates": len(workspace_ids), "dispatched": dispatched}


@celery_app.task(name="process_monitoring_tasks.djen_poll_tick")
def djen_poll_tick() -> dict[str, int]:
    return _run_async(_async_djen_poll_tick())


@celery_app.task(name="process_monitoring_tasks.datajud_poll_tick")
def datajud_poll_tick() -> dict[str, int]:
    return _run_async(_async_datajud_poll_tick())


# `autoretry_for` fica declarado aqui, e não em `KLMResilientTask`, porque a base
# é herdada por dezenas de tasks do sistema: ampliar a tupla dela para
# `httpx.HTTPError` faria toda task que chama `raise_for_status()` retentar 4xx.
# Aqui a tupla é a estreita de propósito — o retry de rede vive no cliente, por
# chamada, e o que sobra para a task é só falha de transporte que escape dele
# (banco, Redis). Limite de tempo próprio: os 55s da base matam um poll que
# percorre várias OABs. Cada OAB tem teto próprio de KLM_DJEN_MAX_SECONDS_PER_OAB
# (120s), que cobre paginação, espera do limitador e retentativas; com 240s de
# soft limit cabem duas OABs degradadas, e o commit por OAB preserva as anteriores.
@celery_app.task(
    name="process_monitoring_tasks.djen_poll_workspace",
    autoretry_for=(ConnectionError, TimeoutError),
    soft_time_limit=_TASK_SOFT_TIME_LIMIT,
    time_limit=_TASK_TIME_LIMIT,
)
def djen_poll_workspace(workspace_id: str) -> int:
    async def _run() -> int:
        async with WorkerSessionLocal() as db:
            return await run_djen_poll_for_workspace(db, UUID(workspace_id))

    return _run_async(_run())


@celery_app.task(
    name="process_monitoring_tasks.datajud_poll_workspace",
    autoretry_for=(ConnectionError, TimeoutError),
    soft_time_limit=_TASK_SOFT_TIME_LIMIT,
    time_limit=_TASK_TIME_LIMIT,
)
def datajud_poll_workspace(workspace_id: str) -> int:
    async def _run() -> int:
        async with WorkerSessionLocal() as db:
            return await run_datajud_poll_for_workspace(db, UUID(workspace_id))

    return _run_async(_run())
