"""Limitador global por fonte: janela fixa atômica em Redis."""
from __future__ import annotations

import pytest

from app.services.process_monitoring_errors import ProcessMonitoringUpstreamUnavailableError
from app.services.process_monitoring_rate_limit import aguardar_vaga


class _RedisFalso:
    """Reproduz o script Lua: INCR + EXPIRE na primeira chamada, TTL decrescente."""

    def __init__(self) -> None:
        self.contagem = 0
        self.ttl = -1
        self.chamadas = 0

    async def eval(self, script, numkeys, chave, janela):
        self.chamadas += 1
        self.contagem += 1
        if self.contagem == 1 or self.ttl < 0:
            self.ttl = int(janela)
        return [self.contagem, self.ttl]

    def virar_janela(self) -> None:
        self.contagem = 0
        self.ttl = -1


@pytest.mark.asyncio
async def test_passa_direto_enquanto_ha_vaga(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.process_monitoring_rate_limit.settings.KLM_DJEN_RATE_LIMIT_PER_MINUTE", 3
    )
    redis = _RedisFalso()
    esperas: list[int] = []

    async def _dorme(s):
        esperas.append(s)

    for _ in range(3):
        await aguardar_vaga("djen", redis=redis, dormir=_dorme)
    assert esperas == []


@pytest.mark.asyncio
async def test_espera_a_janela_virar_quando_o_teto_bate(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.process_monitoring_rate_limit.settings.KLM_DJEN_RATE_LIMIT_PER_MINUTE", 2
    )
    redis = _RedisFalso()
    esperas: list[int] = []

    async def _dorme(s):
        esperas.append(s)
        redis.virar_janela()  # o tempo passou: a chave expirou

    await aguardar_vaga("djen", redis=redis, dormir=_dorme)
    await aguardar_vaga("djen", redis=redis, dormir=_dorme)
    await aguardar_vaga("djen", redis=redis, dormir=_dorme)  # 3ª: acima do teto, dorme e reentra

    assert esperas == [60]
    assert redis.chamadas == 4


@pytest.mark.asyncio
async def test_desiste_como_fonte_indisponivel_depois_do_teto_de_espera(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.process_monitoring_rate_limit.settings.KLM_DJEN_RATE_LIMIT_PER_MINUTE", 1
    )
    redis = _RedisFalso()
    agora = [0.0]

    async def _dorme(s):
        agora[0] += s  # a janela nunca vira: alguém segue consumindo

    await aguardar_vaga("djen", redis=redis, dormir=_dorme, relogio=lambda: agora[0])
    with pytest.raises(ProcessMonitoringUpstreamUnavailableError):
        await aguardar_vaga("djen", redis=redis, dormir=_dorme, relogio=lambda: agora[0])


@pytest.mark.asyncio
async def test_redis_fora_do_ar_nao_trava_o_ciclo() -> None:
    class _Quebrado:
        async def eval(self, *args):
            raise ConnectionError("redis fora")

    await aguardar_vaga("djen", redis=_Quebrado())  # fail-open, com aviso


@pytest.mark.asyncio
async def test_espera_maxima_do_chamador_encurta_a_espera_por_vaga(monkeypatch) -> None:
    """Revisão do #34: a espera por vaga tem de caber no que resta do teto por OAB."""
    monkeypatch.setattr(
        "app.services.process_monitoring_rate_limit.settings.KLM_DJEN_RATE_LIMIT_PER_MINUTE", 1
    )
    redis = _RedisFalso()
    await aguardar_vaga("djen", redis=redis)
    with pytest.raises(ProcessMonitoringUpstreamUnavailableError):
        # a janela pede 60s de espera; o chamador só tem 10s
        await aguardar_vaga("djen", redis=redis, espera_maxima=10)
