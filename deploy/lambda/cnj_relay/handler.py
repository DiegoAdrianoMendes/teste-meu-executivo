"""Relay do CNJ em sa-east-1 (S71, 11/09/2026).

comunicaapi.pje.jus.br (DJEN) responde 403 do CloudFront a requisições vindas
de fora do Brasil, e o worker de produção roda em us-east-1. Esta função roda
em São Paulo, com IP brasileiro, e executa a requisição no lugar do worker.

Contrato da invocação síncrona (o outro lado é `_RelayLambdaTransport` em
app/services/djen_datajud_clients.py):

  entrada: {"method": "GET"|"POST", "url": "https://<host permitido>/...",
            "headers": {...}, "body": str|null, "timeout": segundos}
  saída:   {"status": int, "headers": {...}, "body_b64": base64(gzip(corpo))}

Só os dois hosts do CNJ são aceitos: a função não é um proxy genérico. Falha
de transporte entre a Lambda e o CNJ (DNS, timeout, conexão) sobe como
exceção, que o invocador recebe como FunctionError e trata como falha
retentável. Resposta HTTP, inclusive 4xx e 5xx, volta como veio.

O evento carrega o cabeçalho Authorization do DataJud: nada aqui registra o
evento em log. Sem dependência fora da biblioteca padrão: o pacote é só este
arquivo, empacotado por scripts de instalação fora do repositório.
"""

from __future__ import annotations

import base64
import gzip
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

HOSTS_PERMITIDOS = frozenset({"comunicaapi.pje.jus.br", "api-publica.datajud.cnj.jus.br"})
METODOS_PERMITIDOS = frozenset({"GET", "POST"})
CABECALHOS_ENVIADOS = frozenset({"authorization", "accept", "content-type", "user-agent"})
CABECALHOS_DEVOLVIDOS = frozenset(
    {"content-type", "retry-after", "date", "via", "x-amz-cf-pop", "x-amz-cf-id"}
)
TIMEOUT_MAXIMO_SEGUNDOS = 25.0
# A resposta síncrona da Lambda tem teto de 6 MB. Uma página de 100 itens do
# DJEN mede ~0,5 MB bruta (medido em 11/09/2026) e o gzip de JSON reduz ~8x;
# o limite abaixo é do corpo já codificado, que é o que conta para o teto.
LIMITE_CORPO_CODIFICADO = 5 * 1024 * 1024


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    url = str(event.get("url") or "")
    partes = urllib.parse.urlsplit(url)
    if partes.scheme != "https" or partes.hostname not in HOSTS_PERMITIDOS:
        raise ValueError(f"host não permitido pelo relay: {partes.hostname!r}")
    metodo = str(event.get("method") or "GET").upper()
    if metodo not in METODOS_PERMITIDOS:
        raise ValueError(f"método não permitido pelo relay: {metodo}")
    cabecalhos = {
        nome: str(valor)
        for nome, valor in dict(event.get("headers") or {}).items()
        if nome.lower() in CABECALHOS_ENVIADOS
    }
    corpo = event.get("body")
    dados = corpo.encode("utf-8") if isinstance(corpo, str) else None
    timeout = min(float(event.get("timeout") or 15.0), TIMEOUT_MAXIMO_SEGUNDOS)

    requisicao = urllib.request.Request(url, data=dados, method=metodo, headers=cabecalhos)
    try:
        with urllib.request.urlopen(requisicao, timeout=timeout) as resposta:
            status = int(resposta.status)
            bruto = resposta.read()
            recebidos = {nome.lower(): valor for nome, valor in resposta.headers.items()}
    except urllib.error.HTTPError as erro:
        status = int(erro.code)
        bruto = erro.read()
        recebidos = {nome.lower(): valor for nome, valor in erro.headers.items()}
    # urllib não pede Accept-Encoding, mas se a borda comprimir mesmo assim o
    # corpo volta legível: quem consome não precisa saber disso.
    if recebidos.get("content-encoding", "").lower() == "gzip":
        bruto = gzip.decompress(bruto)
    corpo_b64 = base64.b64encode(gzip.compress(bruto, compresslevel=6)).decode("ascii")
    if len(corpo_b64) > LIMITE_CORPO_CODIFICADO:
        raise ValueError(f"corpo da resposta acima do teto do relay ({len(corpo_b64)} bytes)")
    return {
        "status": status,
        "headers": {
            nome: valor for nome, valor in recebidos.items() if nome in CABECALHOS_DEVOLVIDOS
        },
        "body_b64": corpo_b64,
    }
