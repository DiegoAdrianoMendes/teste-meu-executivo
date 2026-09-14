"""Handler da Lambda de relay do CNJ (deploy/lambda/cnj_relay/handler.py, S71).

O arquivo vive fora de `app/` porque é empacotado sozinho para a Lambda em
sa-east-1; o teste o carrega pelo caminho.
"""

from __future__ import annotations

import base64
import email.message
import gzip
import importlib.util
import io
import urllib.error
from pathlib import Path

import pytest

_HANDLER = Path(__file__).resolve().parents[2] / "deploy" / "lambda" / "cnj_relay" / "handler.py"


@pytest.fixture(scope="module")
def relay():
    spec = importlib.util.spec_from_file_location("cnj_relay_handler", _HANDLER)
    modulo = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(modulo)
    return modulo


class _RespostaFalsa:
    def __init__(self, status: int, corpo: bytes, cabecalhos: dict[str, str]) -> None:
        self.status = status
        self._corpo = corpo
        self.headers = email.message.Message()
        for nome, valor in cabecalhos.items():
            self.headers[nome] = valor

    def read(self) -> bytes:
        return self._corpo

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None


def _corpo(saida: dict) -> bytes:
    return gzip.decompress(base64.b64decode(saida["body_b64"]))


def test_recusa_host_fora_da_lista_e_esquema_sem_tls(relay) -> None:
    with pytest.raises(ValueError, match="host não permitido"):
        relay.handler({"url": "https://example.com/api"})
    with pytest.raises(ValueError, match="host não permitido"):
        relay.handler({"url": "http://comunicaapi.pje.jus.br/api/v1/comunicacao"})
    with pytest.raises(ValueError, match="método não permitido"):
        relay.handler({"url": "https://comunicaapi.pje.jus.br/x", "method": "DELETE"})


def test_devolve_status_corpo_e_so_os_cabecalhos_uteis(relay, monkeypatch) -> None:
    pedidos: list = []

    def _urlopen(requisicao, timeout):
        pedidos.append((requisicao, timeout))
        return _RespostaFalsa(
            200,
            b'{"items": [1]}',
            {"Content-Type": "application/json", "Set-Cookie": "a=b", "Via": "cloudfront"},
        )

    monkeypatch.setattr(relay.urllib.request, "urlopen", _urlopen)
    saida = relay.handler(
        {
            "method": "POST",
            "url": "https://api-publica.datajud.cnj.jus.br/api_publica_tjdft/_search",
            "headers": {
                "Authorization": "APIKey x",
                "Content-Type": "application/json",
                "Cookie": "n",
            },
            "body": '{"query": {}}',
            "timeout": 99,
        }
    )

    assert saida["status"] == 200
    assert _corpo(saida) == b'{"items": [1]}'
    assert saida["headers"] == {"content-type": "application/json", "via": "cloudfront"}
    [(requisicao, timeout)] = pedidos
    assert requisicao.get_method() == "POST"
    assert requisicao.data == b'{"query": {}}'
    assert requisicao.get_header("Authorization") == "APIKey x"
    assert requisicao.get_header("Cookie") is None
    assert timeout == relay.TIMEOUT_MAXIMO_SEGUNDOS  # 99 é acima do teto da própria função


def test_erro_http_volta_como_status_e_corpo_e_nao_como_excecao(relay, monkeypatch) -> None:
    def _urlopen(requisicao, timeout):
        cabecalhos = email.message.Message()
        cabecalhos["Content-Type"] = "text/html"
        raise urllib.error.HTTPError(
            requisicao.full_url, 403, "Forbidden", cabecalhos, io.BytesIO(b"<html>blocked</html>")
        )

    monkeypatch.setattr(relay.urllib.request, "urlopen", _urlopen)
    saida = relay.handler({"url": "https://comunicaapi.pje.jus.br/api/v1/comunicacao"})

    assert saida["status"] == 403
    assert _corpo(saida) == b"<html>blocked</html>"


def test_corpo_comprimido_pela_borda_volta_legivel(relay, monkeypatch) -> None:
    def _urlopen(requisicao, timeout):
        return _RespostaFalsa(200, gzip.compress(b'{"ok": true}'), {"Content-Encoding": "gzip"})

    monkeypatch.setattr(relay.urllib.request, "urlopen", _urlopen)
    saida = relay.handler({"url": "https://comunicaapi.pje.jus.br/api/v1/comunicacao"})

    assert _corpo(saida) == b'{"ok": true}'
    assert "content-encoding" not in saida["headers"]
