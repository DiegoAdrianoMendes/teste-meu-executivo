#!/usr/bin/env python3
"""
Checklist operacional para Gemma no Vertex AI.

Uso:
  python scripts/check_gemma_vertex.py

O script valida:
  - projeto e região Vertex
  - modelos Gemma configurados
  - endpoint IDs configurados
  - quantidade de endpoints Vertex já existentes no projeto
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys

import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("AWS_PROFILE", "codex-hub")
os.environ.setdefault("AWS_REGION", "us-east-1")

from app.ai.vertex_gemini import _load_vertex_credentials
from app.core.config import settings


def _load_credentials() -> service_account.Credentials:
    secret = _load_vertex_credentials()
    credentials = service_account.Credentials.from_service_account_info(
        json.loads(secret),
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    credentials.refresh(Request())
    return credentials


def _list_endpoints(credentials: service_account.Credentials) -> tuple[int, dict]:
    url = (
        f"https://{settings.VERTEX_LOCATION}-aiplatform.googleapis.com/v1/"
        f"projects/{settings.VERTEX_PROJECT_ID}/locations/{settings.VERTEX_LOCATION}/endpoints"
    )
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {credentials.token}"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    return len(payload.get("endpoints", [])), payload


def main() -> int:
    print("== Gemma Vertex readiness ==")
    print(f"Project:  {settings.VERTEX_PROJECT_ID}")
    print(f"Location: {settings.VERTEX_LOCATION}")
    print(f"Simple:   {settings.GEMMA_MODEL_SIMPLE}")
    print(f"Medium:   {settings.GEMMA_MODEL_MEDIUM}")
    print(f"Endpoint simple configurado: {'yes' if settings.GEMMA_ENDPOINT_SIMPLE else 'no'}")
    print(f"Endpoint medium configurado: {'yes' if settings.GEMMA_ENDPOINT_MEDIUM else 'no'}")

    try:
        credentials = _load_credentials()
        count, payload = _list_endpoints(credentials)
    except Exception as exc:
        print(f"Falha ao consultar Vertex AI: {exc}")
        return 1

    print(f"Endpoints Vertex encontrados: {count}")
    for endpoint in payload.get("endpoints", [])[:10]:
        print(f"- {endpoint.get('name')} :: {endpoint.get('displayName', '(sem nome)')}")

    if settings.GEMMA_ENDPOINT_SIMPLE and (settings.GEMMA_ENDPOINT_MEDIUM or settings.GEMMA_ENDPOINT_SIMPLE):
        print("STATUS: pronto para tentar Gemma em runtime.")
        return 0

    print("STATUS: código pronto, mas ainda faltam endpoint IDs do Gemma configurados no ambiente.")
    print("Sugestão: provisionar endpoint Gemma no Vertex Model Garden e preencher GEMMA_ENDPOINT_SIMPLE/GEMMA_ENDPOINT_MEDIUM.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
