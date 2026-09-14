#!/usr/bin/env python3
"""
Checklist operacional para Ollama com Qwen3 + Gemma 4.

Uso:
  python scripts/check_ollama_models.py

Com variáveis:
  OLLAMA_BASE_URL=http://localhost:11434
  OLLAMA_MODEL_SIMPLE=qwen3:4b
  OLLAMA_MODEL_MEDIUM=qwen3:14b
  OLLAMA_MODEL_COMPLEX=gemma4:12b
  OLLAMA_MODEL_PREMIUM=gemma4:12b
"""
from __future__ import annotations

import os
import sys
from typing import Iterable

import requests


BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
EXPECTED_MODELS = [
    os.getenv("OLLAMA_MODEL_SIMPLE", "qwen3:4b"),
    os.getenv("OLLAMA_MODEL_MEDIUM", "qwen3:14b"),
    os.getenv("OLLAMA_MODEL_COMPLEX", "gemma4:12b"),
    os.getenv("OLLAMA_MODEL_PREMIUM", "gemma4:12b"),
]


def _model_names(payload: dict) -> set[str]:
    names: set[str] = set()
    for model in payload.get("models", []):
        name = model.get("name") or model.get("model")
        if name:
            names.add(name)
    return names


def _is_available(installed: set[str], model: str) -> bool:
    if model in installed:
        return True
    if ":" not in model:
        return f"{model}:latest" in installed
    return False


def _print_pull_commands(models: Iterable[str]) -> None:
    for model in models:
        print(f"  ollama pull {model}")


def main() -> int:
    print("== Ollama readiness ==")
    print(f"Base URL: {BASE_URL}")
    print("Modelos esperados:")
    for model in EXPECTED_MODELS:
        print(f"- {model}")

    try:
        response = requests.get(f"{BASE_URL}/api/tags", timeout=10)
        response.raise_for_status()
    except Exception as exc:
        print(f"STATUS: Ollama indisponível ({exc})")
        print("Sugestão local: docker compose --profile ollama up -d ollama")
        return 1

    installed = _model_names(response.json())
    missing = [model for model in EXPECTED_MODELS if not _is_available(installed, model)]

    print(f"Modelos instalados: {', '.join(sorted(installed)) or '(nenhum)'}")
    if missing:
        print("STATUS: Ollama responde, mas faltam modelos.")
        print("Baixe com:")
        _print_pull_commands(missing)
        return 2

    smoke_model = os.getenv("OLLAMA_SMOKE_MODEL", EXPECTED_MODELS[0])
    try:
        smoke = requests.post(
            f"{BASE_URL}/api/chat",
            json={
                "model": smoke_model,
                "stream": False,
                "messages": [{"role": "user", "content": "Responda somente: ok"}],
                "options": {"temperature": 0, "num_predict": 8},
            },
            timeout=60,
        )
        smoke.raise_for_status()
    except Exception as exc:
        print(f"STATUS: modelos presentes, mas smoke falhou ({exc})")
        return 3

    print(f"Smoke: {smoke_model} respondeu.")
    print("STATUS: pronto para AI_PROVIDER=ollama.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
