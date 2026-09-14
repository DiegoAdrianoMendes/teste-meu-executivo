"""Limitador global de requisições às fontes públicas (DJEN e DataJud).

O Comunica devolve `X-RateLimit-Limit: 20` por janela curta, contado por IP de
saída. Todos os workspaces saem pelo mesmo IP, então o limite é da plataforma,
não do workspace. O jitter do beat espalha workspaces, não requisições: com
concorrência 4, paginação e várias OABs, dois workspaces coincidindo já estouram.
Por isso a chave é global por fonte, e não por workspace.

Janela fixa em Redis, atômica em Lua: INCR e EXPIRE na mesma operação, para não
existir a corrida clássica (INCR feito, processo morre antes do EXPIRE, chave
sem TTL para sempre). Quem não acha vaga dorme até a janela virar e tenta de
novo, com teto de espera; estourado o teto, a chamada é tratada como fonte
indisponível e o chamador pula esta OAB ou este processo.

Fail-open quando o Redis falha, com aviso: sem Redis o worker nem teria recebido
a task, porque o broker do Celery é o mesmo Redis. Travar o ciclo por isso seria
pior do que correr o risco de um 429, que o cliente já trata.

A janela real do Comunica não foi medida com precisão (pode ser menor que um
minuto). 15 por minuto é mais restritivo do que 20 por 30s, então a margem vale
em qualquer das hipóteses. O teto do DataJud NÃO foi verificado: é conservador
e configurável.
"""
from __future__ import annotations

import asyncio
import time

from app.core.config import settings
from app.core.logging import logger
from app.services.process_monitoring_errors import ProcessMonitoringUpstreamUnavailableError

_JANELA_SEGUNDOS = 60
_ESPERA_MAXIMA_SEGUNDOS = 90.0

# Devolve {contagem, ttl}. INCR e EXPIRE no mesmo script: atômico.
_LUA_JANELA = """
local atual = redis.call('INCR', KEYS[1])
if atual == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
  ttl = tonumber(ARGV[1])
end
return {atual, ttl}
"""


def _teto(fonte: str) -> int:
    if fonte == "djen":
        return max(1, settings.KLM_DJEN_RATE_LIMIT_PER_MINUTE)
    if fonte == "datajud":
        return max(1, settings.KLM_DATAJUD_RATE_LIMIT_PER_MINUTE)
    raise ValueError(f"fonte desconhecida: {fonte}")


async def _redis():
    from app.core.cache import get_redis

    return await get_redis()


async def aguardar_vaga(
    fonte: str,
    *,
    redis=None,
    dormir=asyncio.sleep,
    relogio=time.monotonic,
    espera_maxima: float | None = None,
) -> None:
    """Bloqueia até haver vaga na janela da fonte. Levanta se esperar demais.

    `redis`, `dormir` e `relogio` são injetáveis para teste; em produção vêm do
    cache compartilhado e do `asyncio`.
    """
    if settings.uses_in_memory_redis and redis is None:
        return
    teto = _teto(fonte)
    limite_de_espera = _ESPERA_MAXIMA_SEGUNDOS
    if espera_maxima:
        limite_de_espera = min(_ESPERA_MAXIMA_SEGUNDOS, espera_maxima)
    chave = f"klm:process_monitoring:ratelimit:{fonte}"
    inicio = relogio()
    while True:
        try:
            cliente = redis if redis is not None else await _redis()
            atual, ttl = await cliente.eval(_LUA_JANELA, 1, chave, _JANELA_SEGUNDOS)
        except Exception as error:  # noqa: BLE001 - fail-open deliberado, ver docstring
            logger.warning(
                "process_monitoring.ratelimit_indisponivel",
                fonte=fonte,
                error_type=type(error).__name__,
            )
            return
        if int(atual) <= teto:
            return
        espera = max(1, int(ttl))
        if relogio() - inicio + espera > limite_de_espera:
            raise ProcessMonitoringUpstreamUnavailableError(
                f"{fonte}: sem vaga no limite de {teto}/min depois de "
                f"{int(relogio() - inicio)}s de espera."
            )
        logger.info(
            "process_monitoring.ratelimit_aguardando",
            fonte=fonte,
            contagem=int(atual),
            teto=teto,
            espera_segundos=espera,
        )
        await dormir(espera)


__all__ = ["aguardar_vaga"]
