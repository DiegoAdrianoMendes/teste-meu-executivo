"""Retentativa dos clientes DJEN/DataJud.

O retry vive no cliente, por chamada, e não no `autoretry_for` da task: a base
`KLMResilientTask` é herdada por dezenas de tasks, e ampliá-la para
`httpx.HTTPError` faria toda task que chama `raise_for_status()` retentar 4xx.
"""
from __future__ import annotations

import httpx
import pytest

from app.services.djen_datajud_clients import _requisitar_com_retentativa
from app.services.process_monitoring_errors import (
    ProcessMonitoringUpstreamRejectedError,
    ProcessMonitoringUpstreamUnavailableError,
)


def _resposta(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status,
        headers=headers or {},
        request=httpx.Request("GET", "https://exemplo.invalido/consulta"),
    )


@pytest.mark.asyncio
async def test_retenta_falha_de_transporte_e_devolve_o_sucesso(monkeypatch) -> None:
    monkeypatch.setattr("app.services.djen_datajud_clients.asyncio.sleep", _dorme_nada)
    tentativas: list[int] = []

    async def _enviar(client):
        tentativas.append(1)
        if len(tentativas) < 3:
            raise httpx.ConnectError("recusou conexão")
        return _resposta(200)

    resposta = await _requisitar_com_retentativa(_enviar, fonte="DJEN", alvo="OAB 1/DF")

    assert resposta.status_code == 200
    assert len(tentativas) == 3


@pytest.mark.asyncio
async def test_nao_retenta_4xx_e_carrega_o_status(monkeypatch) -> None:
    """404 é sigla de tribunal inválida (S50) e 400 é OAB mal formada.

    Repetir qualquer um deles é gastar janela do beat para receber o mesmo não.
    """
    monkeypatch.setattr("app.services.djen_datajud_clients.asyncio.sleep", _dorme_nada)
    tentativas: list[int] = []

    async def _enviar(client):
        tentativas.append(1)
        return _resposta(404)

    with pytest.raises(ProcessMonitoringUpstreamRejectedError) as capturado:
        await _requisitar_com_retentativa(_enviar, fonte="DataJud", alvo="processo X")

    assert capturado.value.status == 404
    assert len(tentativas) == 1



@pytest.mark.asyncio
async def test_4xx_carrega_o_corpo_truncado_para_o_log(monkeypatch) -> None:
    """403 do DJEN ao IP da AWS (09/09/2026): sem o corpo não se sabe se é WAF ou pedido."""
    monkeypatch.setattr("app.services.djen_datajud_clients.asyncio.sleep", _dorme_nada)
    corpo = "<html>Access denied by upstream firewall</html>" + "x" * 400

    async def _enviar(client):
        return httpx.Response(
            403,
            text=corpo,
            request=httpx.Request("GET", "https://exemplo.invalido/consulta"),
        )

    with pytest.raises(ProcessMonitoringUpstreamRejectedError) as capturado:
        await _requisitar_com_retentativa(_enviar, fonte="DJEN", alvo="OAB 1/DF")

    assert capturado.value.status == 403
    assert capturado.value.detail.startswith("<html>Access denied")
    assert len(capturado.value.detail) == 300

@pytest.mark.asyncio
async def test_retenta_429_e_5xx_e_desiste_como_indisponivel(monkeypatch) -> None:
    monkeypatch.setattr("app.services.djen_datajud_clients.asyncio.sleep", _dorme_nada)
    tentativas: list[int] = []

    async def _enviar(client):
        tentativas.append(1)
        return _resposta(503)

    with pytest.raises(ProcessMonitoringUpstreamUnavailableError):
        await _requisitar_com_retentativa(_enviar, fonte="DJEN", alvo="OAB 1/DF")

    assert len(tentativas) == 3


@pytest.mark.asyncio
async def test_respeita_retry_after_numerico(monkeypatch) -> None:
    esperas: list[float] = []

    async def _registra(segundos):
        esperas.append(segundos)

    monkeypatch.setattr("app.services.djen_datajud_clients.asyncio.sleep", _registra)

    async def _enviar(client):
        return _resposta(429, {"Retry-After": "2"})

    with pytest.raises(ProcessMonitoringUpstreamUnavailableError):
        await _requisitar_com_retentativa(_enviar, fonte="DJEN", alvo="OAB 1/DF")

    assert 2.0 in esperas


async def _dorme_nada(_segundos) -> None:
    return None


# ── paginação (S47): contrato verificado contra produção em 03/09/2026 ──


def _pagina(itens: list[dict]) -> httpx.Response:
    return httpx.Response(
        200,
        json={"status": "success", "count": 10000, "items": itens},  # count mente de propósito
        request=httpx.Request("GET", "https://exemplo.invalido/comunicacao"),
    )


@pytest.mark.asyncio
async def test_percorre_as_paginas_ate_a_pagina_curta_e_deduplica(monkeypatch) -> None:
    from datetime import date

    from app.services import djen_datajud_clients as mod

    monkeypatch.setattr(mod.settings, "KLM_DJEN_PAGE_SIZE", 100)
    monkeypatch.setattr(mod, "djen_live_delivery_allowed", lambda: True)

    async def _sem_limite(fonte, **kwargs):
        return None

    monkeypatch.setattr(mod, "aguardar_vaga", _sem_limite)
    paginas_pedidas: list[int] = []

    async def _requisita(enviar, *, fonte, alvo):
        pagina = int(alvo.rsplit(" ", 1)[-1])
        paginas_pedidas.append(pagina)
        if pagina == 1:
            return _pagina([{"id": i} for i in range(100)])
        return _pagina([{"id": 99}] + [{"id": 100 + i} for i in range(39)])  # 99 repetido

    monkeypatch.setattr(mod, "_requisitar_com_retentativa", _requisita)

    itens = await mod.DjenComunicaClient().fetch_comunicacoes(
        numero_oab="68812", uf_oab="DF",
        data_disponibilizacao_inicio=date(2026, 9, 1), data_disponibilizacao_fim=date(2026, 9, 3),
    )

    assert paginas_pedidas == [1, 2]
    assert len(itens) == 139  # 100 + 40 − 1 duplicado


@pytest.mark.asyncio
async def test_teto_de_paginas_e_logado_e_nao_silencioso(monkeypatch, capsys) -> None:
    from datetime import date

    from app.services import djen_datajud_clients as mod

    monkeypatch.setattr(mod.settings, "KLM_DJEN_PAGE_SIZE", 5)
    monkeypatch.setattr(mod.settings, "KLM_DJEN_MAX_PAGES", 3)
    monkeypatch.setattr(mod, "djen_live_delivery_allowed", lambda: True)

    async def _sem_limite(fonte, **kwargs):
        return None

    monkeypatch.setattr(mod, "aguardar_vaga", _sem_limite)
    contador = [0]

    async def _sempre_cheia(enviar, *, fonte, alvo):
        contador[0] += 1
        base = contador[0] * 1000
        return _pagina([{"id": base + i} for i in range(5)])

    monkeypatch.setattr(mod, "_requisitar_com_retentativa", _sempre_cheia)

    itens = await mod.DjenComunicaClient().fetch_comunicacoes(
        numero_oab="68812", uf_oab="DF",
        data_disponibilizacao_inicio=date(2026, 9, 1), data_disponibilizacao_fim=date(2026, 9, 3),
    )

    assert contador[0] == 3
    assert len(itens) == 15
    assert "djen_paginacao_truncada" in capsys.readouterr().out
