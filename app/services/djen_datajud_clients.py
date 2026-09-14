"""Clientes de leitura pública DJEN (comunicaapi.pje.jus.br) e DataJud
(api-publica.datajud.cnj.jus.br) — ver dossiê
`app/agents/knowledge/processual_monitoramento.py` seções 1 e 2.

Ambos são GET/POST públicos e não destrutivos, mas cada um passa pelo seu
próprio gate fail-closed: `djen_live_delivery_allowed()` e
`datajud_live_delivery_allowed()`. São fontes independentes, com credencial e
disponibilidade próprias, e abrir uma não abre a outra.

Com o gate fechado o worker (app/workers/process_monitoring_tasks.py) nunca
instancia estes clientes. Ele lê a fixture comitada em `tests/fixtures/djen/`,
mas só quando ela existe no disco: a imagem de produção não a carrega, e nesse
caso o tick faz no-op registrado em log. Ver
`process_monitoring_simulation_available()` abaixo.
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import json
import time
from collections.abc import Awaitable, Callable
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import boto3
import httpx
from botocore.config import Config as BotoConfig

from app.core.config import settings
from app.core.klm_runtime import (
    datajud_live_delivery_allowed,
    djen_live_delivery_allowed,
)
from app.core.logging import logger
from app.services.process_monitoring_errors import (
    ProcessMonitoringLiveDeliveryBlockedError,
    ProcessMonitoringUpstreamRejectedError,
    ProcessMonitoringUpstreamUnavailableError,
)
from app.services.process_monitoring_rate_limit import aguardar_vaga

_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "djen"
DJEN_FIXTURE_PATH = _FIXTURES_DIR / "comunica_pje_sample.json"
DATAJUD_FIXTURE_PATH = _FIXTURES_DIR / "datajud_sample.json"


_MAX_TENTATIVAS = 3
_ESPERA_BASE_SEGUNDOS = 1.0
_ESPERA_MAXIMA_SEGUNDOS = 20.0
_TIMEOUT_SEGUNDOS = 15.0

# Relay do CNJ (S71): o que sai daqui para a Lambda e o que volta dela. O
# Authorization do DataJud vai; cabeçalhos internos do httpx ficam. Na volta,
# content-encoding e afins são descartados porque o corpo já chega inteiro e
# descomprimido, e o httpx tentaria decodificar de novo.
_RELAY_CABECALHOS_ENVIADOS = frozenset({"authorization", "accept", "content-type", "user-agent"})
_RELAY_CABECALHOS_IGNORADOS = frozenset(
    {"content-encoding", "content-length", "transfer-encoding", "connection"}
)
_RELAY_TIMEOUT_SEGUNDOS = int(_TIMEOUT_SEGUNDOS)


@lru_cache(maxsize=4)
def _cliente_lambda(region: str) -> Any:
    # Um cliente por região e por processo: criar o cliente carrega o modelo do
    # serviço, e a paginação do DJEN pode invocar dezenas de vezes por OAB.
    # A retentativa é a de _requisitar_com_retentativa; o botocore não retenta.
    return boto3.client(
        "lambda",
        region_name=region,
        config=BotoConfig(
            connect_timeout=5, read_timeout=40, retries={"total_max_attempts": 1}
        ),
    )


class _RelayLambdaTransport(httpx.AsyncBaseTransport):
    """Executa a requisição por uma Lambda em sa-east-1 em vez de abrir conexão daqui.

    comunicaapi.pje.jus.br responde 403 do CloudFront a IPs fora do Brasil, e o
    worker roda em us-east-1 (S71, 11/09/2026). A função em São Paulo faz a
    chamada e devolve status, cabeçalhos e corpo (gzip+base64), remontados aqui
    num httpx.Response: retentativa, recusa de 4xx e paginação não sabem que a
    rede foi outra. Contrato do evento em deploy/lambda/cnj_relay/handler.py.

    Falha da própria invocação (credencial, AccessDenied, timeout, FunctionError)
    vira httpx.ConnectError: não é resposta do CNJ, e a retentativa trata como
    transporte. O motivo fica no log, porque o erro final só diz "indisponível".
    """

    def __init__(self, function_arn: str) -> None:
        self._function_arn = function_arn
        self._region = function_arn.split(":")[3]

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        evento = {
            "method": request.method,
            "url": str(request.url),
            "headers": {
                nome.lower(): valor
                for nome, valor in request.headers.items()
                if nome.lower() in _RELAY_CABECALHOS_ENVIADOS
            },
            "body": request.content.decode("utf-8") if request.content else None,
            "timeout": _RELAY_TIMEOUT_SEGUNDOS,
        }
        try:
            resposta = await asyncio.to_thread(self._invocar, evento)
        except Exception as error:
            motivo = repr(error)[:300]
            logger.warning(
                "process_monitoring.relay_cnj_falhou",
                funcao=self._function_arn,
                url=str(request.url.copy_with(query=None)),
                erro=motivo,
            )
            raise httpx.ConnectError(f"relay do CNJ falhou: {motivo}", request=request) from error
        codificado = resposta.get("body_b64") or ""
        corpo = gzip.decompress(base64.b64decode(codificado)) if codificado else b""
        cabecalhos = {
            nome: valor
            for nome, valor in dict(resposta.get("headers") or {}).items()
            if nome.lower() not in _RELAY_CABECALHOS_IGNORADOS
        }
        return httpx.Response(
            int(resposta["status"]), headers=cabecalhos, content=corpo, request=request
        )

    def _invocar(self, evento: dict[str, Any]) -> dict[str, Any]:
        saida = _cliente_lambda(self._region).invoke(
            FunctionName=self._function_arn,
            InvocationType="RequestResponse",
            Payload=json.dumps(evento).encode("utf-8"),
        )
        payload = json.loads(saida["Payload"].read() or b"null")
        if saida.get("FunctionError"):
            raise RuntimeError(f"FunctionError {saida['FunctionError']}: {str(payload)[:200]}")
        if not isinstance(payload, dict) or "status" not in payload:
            raise RuntimeError(f"resposta do relay sem status: {str(payload)[:200]}")
        return payload


def _transport_para_cnj() -> httpx.AsyncBaseTransport | None:
    """None fala direto com o CNJ; com o ARN preenchido, tudo passa pelo relay."""
    arn = settings.KLM_CNJ_RELAY_FUNCTION_ARN.strip()
    return _RelayLambdaTransport(arn) if arn else None


async def _requisitar_com_retentativa(
    enviar: Callable[[httpx.AsyncClient], Awaitable[httpx.Response]],
    *,
    fonte: str,
    alvo: str,
) -> httpx.Response:
    """Executa a chamada e retenta só o que repetir conserta.

    O retry vive aqui, e não no `autoretry_for` da task, por dois motivos.
    Primeiro, `KLMResilientTask` é a base de dezenas de tasks do sistema, e
    ampliar a tupla dela para `httpx.HTTPError` faria toda task que chama
    `raise_for_status()` retentar 4xx três vezes. Segundo, a task de workspace
    percorre várias OABs: retentar no nível da task refaria o lote inteiro por
    causa de uma OAB, enquanto aqui a retentativa é da chamada que falhou.

    Retenta transporte, timeout, 429 e 5xx. Não retenta os demais 4xx, porque
    repetir uma OAB mal formada ou uma sigla de tribunal inexistente é gastar
    janela do beat para receber o mesmo não.
    """
    ultimo_erro: Exception | None = None
    for tentativa in range(1, _MAX_TENTATIVAS + 1):
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT_SEGUNDOS, transport=_transport_para_cnj()
            ) as client:
                response = await enviar(client)
        except (httpx.TransportError, httpx.TimeoutException) as error:
            ultimo_erro = error
        else:
            if response.status_code < 400:
                return response
            if response.status_code != 429 and response.status_code < 500:
                raise ProcessMonitoringUpstreamRejectedError(
                    f"{fonte} recusou a consulta a {alvo} com HTTP {response.status_code}.",
                    status=response.status_code,
                    detail=response.text[:300],
                )
            ultimo_erro = httpx.HTTPStatusError(
                f"HTTP {response.status_code}", request=response.request, response=response
            )
            espera_do_servidor = _retry_after_segundos(response)
            if espera_do_servidor is not None and tentativa < _MAX_TENTATIVAS:
                await asyncio.sleep(min(espera_do_servidor, _ESPERA_MAXIMA_SEGUNDOS))
                continue
        if tentativa < _MAX_TENTATIVAS:
            await asyncio.sleep(
                min(_ESPERA_BASE_SEGUNDOS * (2 ** (tentativa - 1)), _ESPERA_MAXIMA_SEGUNDOS)
            )
    raise ProcessMonitoringUpstreamUnavailableError(
        f"{fonte} não respondeu a {alvo} depois de {_MAX_TENTATIVAS} tentativas."
    ) from ultimo_erro


def _retry_after_segundos(response: httpx.Response) -> float | None:
    bruto = response.headers.get("Retry-After")
    if not bruto:
        return None
    try:
        return max(0.0, float(bruto))
    except ValueError:
        return None  # forma de data HTTP: cai no backoff exponencial


def process_monitoring_simulation_available() -> bool:
    """As fixtures do modo simulado vivem em tests/, excluído da imagem pelo
    .dockerignore. Fora de uma árvore de código elas não existem, e ler delas
    é FileNotFoundError, não modo degradado.

    Checa o fato (o arquivo está no disco?) e não o modo declarado: o
    docker-compose de desenvolvimento roda com KLM_RUNTIME_MODE=local_simulation
    sobre uma imagem buildada sem tests/, onde olhar só o modo mentiria.
    """
    return DJEN_FIXTURE_PATH.is_file() and DATAJUD_FIXTURE_PATH.is_file()


def load_djen_fixture() -> dict[str, Any]:
    """Só existe em árvore de código. Ver process_monitoring_simulation_available()."""
    return json.loads(DJEN_FIXTURE_PATH.read_text())


def load_datajud_fixture() -> dict[str, Any]:
    """Só existe em árvore de código. Ver process_monitoring_simulation_available()."""
    return json.loads(DATAJUD_FIXTURE_PATH.read_text())


class DjenComunicaClient:
    """GET público em comunicaapi.pje.jus.br/api/v1/comunicacao."""

    def __init__(self) -> None:
        self._base_url = "https://comunicaapi.pje.jus.br/api/v1/comunicacao"

    async def fetch_comunicacoes(
        self,
        *,
        numero_oab: str,
        uf_oab: str,
        data_disponibilizacao_inicio: date,
        data_disponibilizacao_fim: date,
    ) -> list[dict[str, Any]]:
        if not djen_live_delivery_allowed():
            raise ProcessMonitoringLiveDeliveryBlockedError(
                "Consulta real ao DJEN bloqueada: gate do DJEN fechado."
            )
        # Paginação verificada contra produção em 03/09/2026 (não consta do OpenAPI
        # que o CNJ publica): `pagina` é 1-based, `itensPorPagina` tem mínimo 5, e
        # sem os dois a API devolve 100 itens, que era o teto silencioso de hoje.
        # `count` não serve para parar: devolve 10000 (teto do track_total_hits),
        # o tamanho da página ou o total, conforme a chamada. A regra é página
        # cheia continua, página curta para. Os dois tetos (páginas e tempo)
        # são logados em `error` quando batem, para não virar truncamento mudo.
        tamanho_pagina = max(5, settings.KLM_DJEN_PAGE_SIZE)
        max_paginas = max(1, settings.KLM_DJEN_MAX_PAGES)
        max_segundos = max(1, settings.KLM_DJEN_MAX_SECONDS_PER_OAB)
        alvo = f"OAB {numero_oab}/{uf_oab}"
        inicio = time.monotonic()
        itens: list[dict[str, Any]] = []
        vistos: set[Any] = set()
        pagina = 1
        while True:
            restante = max_segundos - (time.monotonic() - inicio)
            if restante <= 0:
                logger.error(
                    "process_monitoring.djen_paginacao_truncada",
                    oab=alvo, paginas=pagina - 1, itens=len(itens),
                    segundos=int(time.monotonic() - inicio), motivo="teto_de_tempo",
                )
                return itens
            # A espera por vaga não pode ultrapassar o que resta do teto por OAB,
            # senão uma única página consome o orçamento inteiro da task.
            await aguardar_vaga("djen", espera_maxima=restante)
            response = await _requisitar_com_retentativa(
                lambda client, pagina=pagina: client.get(
                    self._base_url,
                    params={
                        "numeroOab": numero_oab,
                        "ufOab": uf_oab,
                        "dataDisponibilizacaoInicio": data_disponibilizacao_inicio.isoformat(),
                        "dataDisponibilizacaoFim": data_disponibilizacao_fim.isoformat(),
                        "itensPorPagina": tamanho_pagina,
                        "pagina": pagina,
                    },
                ),
                fonte="DJEN",
                alvo=f"{alvo} página {pagina}",
            )
            pagina_itens = list(response.json().get("items", []))
            for item in pagina_itens:
                chave = item.get("id")
                if chave in vistos:
                    continue  # a ordem entre páginas não é estável; dedupe por id
                vistos.add(chave)
                itens.append(item)
            if len(pagina_itens) < tamanho_pagina:
                return itens
            decorrido = time.monotonic() - inicio
            if pagina >= max_paginas or decorrido >= max_segundos:
                logger.error(
                    "process_monitoring.djen_paginacao_truncada",
                    oab=alvo,
                    paginas=pagina,
                    itens=len(itens),
                    segundos=int(decorrido),
                    motivo="teto_de_paginas" if pagina >= max_paginas else "teto_de_tempo",
                )
                return itens
            pagina += 1


class DataJudClient:
    """POST público em api-publica.datajud.cnj.jus.br/api_publica_<tribunal>/_search."""

    def __init__(self) -> None:
        self._base_url = "https://api-publica.datajud.cnj.jus.br"

    async def fetch_movimentos(
        self,
        *,
        numero_processo_sem_formatacao: str,
        tribunal_sigla: str,
    ) -> dict[str, Any]:
        if not datajud_live_delivery_allowed():
            raise ProcessMonitoringLiveDeliveryBlockedError(
                "Consulta real ao DataJud bloqueada: gate do DataJud fechado."
            )
        # Redundante com o gate, que já exige a chave. Mantido de propósito:
        # o cliente é a última linha antes da rede e pode ganhar outro chamador.
        if not settings.DATAJUD_API_KEY:
            raise ProcessMonitoringLiveDeliveryBlockedError(
                "DATAJUD_API_KEY não configurada."
            )
        url = f"{self._base_url}/api_publica_{tribunal_sigla.lower()}/_search"
        await aguardar_vaga("datajud")
        response = await _requisitar_com_retentativa(
            lambda client: client.post(
                url,
                headers={"Authorization": f"APIKey {settings.DATAJUD_API_KEY}"},
                json={"query": {"match": {"numeroProcesso": numero_processo_sem_formatacao}}},
            ),
            fonte="DataJud",
            alvo=f"processo {numero_processo_sem_formatacao} em {tribunal_sigla.upper()}",
        )
        payload: dict[str, Any] = response.json()
        return payload
