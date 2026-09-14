"""Relay do CNJ em sa-east-1 (S71): o transporte troca a rede, não o contrato.

comunicaapi.pje.jus.br devolve 403 do CloudFront a IPs fora do Brasil e o
worker roda em us-east-1. Com KLM_CNJ_RELAY_FUNCTION_ARN preenchido, a chamada
é executada por uma Lambda em São Paulo e remontada num httpx.Response: retry,
recusa de 4xx e paginação continuam iguais.
"""

from __future__ import annotations

import base64
import gzip
import json

import httpx
import pytest

from app.services import djen_datajud_clients as mod
from app.services.process_monitoring_errors import (
    ProcessMonitoringUpstreamRejectedError,
    ProcessMonitoringUpstreamUnavailableError,
)

ARN = "arn:aws:lambda:sa-east-1:123456789012:function:meu-executivo-production-cnj-relay"
DJEN = "https://comunicaapi.pje.jus.br/api/v1/comunicacao"


def _payload(status: int, corpo: bytes, headers: dict[str, str] | None = None) -> dict:
    return {
        "status": status,
        "headers": headers or {},
        "body_b64": base64.b64encode(gzip.compress(corpo)).decode("ascii"),
    }


async def _dorme_nada(_segundos) -> None:
    return None


def test_sem_arn_o_cliente_fala_direto_com_o_cnj(monkeypatch) -> None:
    monkeypatch.setattr(mod.settings, "KLM_CNJ_RELAY_FUNCTION_ARN", "")
    assert mod._transport_para_cnj() is None


def test_com_arn_a_regiao_da_lambda_vem_do_proprio_arn(monkeypatch) -> None:
    monkeypatch.setattr(mod.settings, "KLM_CNJ_RELAY_FUNCTION_ARN", f"  {ARN} ")
    transporte = mod._transport_para_cnj()
    assert isinstance(transporte, mod._RelayLambdaTransport)
    assert transporte._region == "sa-east-1"
    assert transporte._function_arn == ARN


async def test_transporte_remonta_a_resposta_e_envia_so_o_que_o_relay_aceita(
    monkeypatch,
) -> None:
    eventos: list[dict] = []
    transporte = mod._RelayLambdaTransport(ARN)

    def _invocar(evento):
        eventos.append(evento)
        return _payload(
            200,
            json.dumps({"hits": 1}).encode(),
            {"content-type": "application/json", "content-encoding": "gzip", "retry-after": "3"},
        )

    monkeypatch.setattr(transporte, "_invocar", _invocar)
    async with httpx.AsyncClient(transport=transporte) as client:
        resposta = await client.post(
            "https://api-publica.datajud.cnj.jus.br/api_publica_tjdft/_search",
            headers={"Authorization": "APIKey segredo", "X-Interno": "nao-vai"},
            json={"query": {"match": {"numeroProcesso": "0001"}}},
        )

    assert resposta.status_code == 200
    assert resposta.json() == {"hits": 1}
    assert resposta.headers["retry-after"] == "3"
    # o corpo já chega descomprimido; o cabeçalho de codificação faria o httpx
    # tentar descomprimir de novo
    assert "content-encoding" not in resposta.headers
    [evento] = eventos
    assert evento["method"] == "POST"
    assert evento["url"] == "https://api-publica.datajud.cnj.jus.br/api_publica_tjdft/_search"
    assert json.loads(evento["body"]) == {"query": {"match": {"numeroProcesso": "0001"}}}
    assert evento["headers"]["authorization"] == "APIKey segredo"
    assert evento["headers"]["content-type"] == "application/json"
    assert "x-interno" not in evento["headers"]
    assert evento["timeout"] == mod._RELAY_TIMEOUT_SEGUNDOS


async def test_get_sem_corpo_vai_sem_corpo(monkeypatch) -> None:
    eventos: list[dict] = []
    transporte = mod._RelayLambdaTransport(ARN)

    def _invocar(evento):
        eventos.append(evento)
        return _payload(200, b'{"items": []}')

    monkeypatch.setattr(transporte, "_invocar", _invocar)
    async with httpx.AsyncClient(transport=transporte) as client:
        resposta = await client.get(DJEN, params={"numeroOab": "1", "ufOab": "DF"})

    assert resposta.json() == {"items": []}
    [evento] = eventos
    assert evento["method"] == "GET"
    assert evento["url"] == f"{DJEN}?numeroOab=1&ufOab=DF"
    assert evento["body"] is None


async def test_403_pelo_relay_continua_sendo_recusa_sem_retentativa(monkeypatch) -> None:
    monkeypatch.setattr(mod.settings, "KLM_CNJ_RELAY_FUNCTION_ARN", ARN)
    monkeypatch.setattr("app.services.djen_datajud_clients.asyncio.sleep", _dorme_nada)
    tentativas: list[int] = []

    def _invocar(self, evento):
        tentativas.append(1)
        return _payload(403, b"<html>ERROR: The request could not be satisfied</html>")

    monkeypatch.setattr(mod._RelayLambdaTransport, "_invocar", _invocar)
    with pytest.raises(ProcessMonitoringUpstreamRejectedError) as capturado:
        await mod._requisitar_com_retentativa(
            lambda client: client.get(DJEN), fonte="DJEN", alvo="OAB 1/DF"
        )

    assert capturado.value.status == 403
    assert "could not be satisfied" in capturado.value.detail
    assert len(tentativas) == 1


async def test_falha_na_invocacao_vira_falha_de_transporte_retentavel(monkeypatch) -> None:
    """AccessDenied, credencial ausente ou FunctionError: nada disso é resposta do CNJ."""
    monkeypatch.setattr(mod.settings, "KLM_CNJ_RELAY_FUNCTION_ARN", ARN)
    monkeypatch.setattr("app.services.djen_datajud_clients.asyncio.sleep", _dorme_nada)
    tentativas: list[int] = []

    def _invocar(self, evento):
        tentativas.append(1)
        raise RuntimeError("AccessDeniedException: not authorized to perform lambda:Invoke")

    monkeypatch.setattr(mod._RelayLambdaTransport, "_invocar", _invocar)
    with pytest.raises(ProcessMonitoringUpstreamUnavailableError) as capturado:
        await mod._requisitar_com_retentativa(
            lambda client: client.get(DJEN), fonte="DJEN", alvo="OAB 1/DF"
        )

    assert len(tentativas) == 3
    assert isinstance(capturado.value.__cause__, httpx.ConnectError)
    assert "AccessDeniedException" in str(capturado.value.__cause__)
