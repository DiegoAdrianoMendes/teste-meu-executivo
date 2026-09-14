"""
Provider: Ollama local/remoto.

Permite usar modelos open-weight servidos pela API oficial do Ollama, como
Qwen3 e Gemma 4, sem trocar o provider padrão da produção.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

import requests

from app.ai.base import AICompletionResult, AIMessage, AIProvider
from app.ai.policy import require_legacy_direct_calls_enabled
from app.core.logging import logger


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, round(len(text) / 4))


def _normalise_base_url(base_url: str) -> str:
    return (base_url or "http://localhost:11434").rstrip("/")


class OllamaProvider(AIProvider):
    """Provider para modelos publicados no Ollama."""

    def __init__(
        self,
        base_url: str,
        default_model: str,
        timeout_seconds: int = 120,
        keep_alive: str = "10m",
        num_ctx: int = 8192,
    ) -> None:
        self.base_url = _normalise_base_url(base_url)
        self.default_model = default_model
        self.timeout_seconds = timeout_seconds
        self.keep_alive = keep_alive
        self.num_ctx = num_ctx

    async def complete(
        self,
        messages: List[AIMessage],
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        response_schema: Optional[Dict[str, Any]] = None,
        model_override: Optional[str] = None,
    ) -> AICompletionResult:
        require_legacy_direct_calls_enabled()
        effective_model = model_override or self.default_model
        rendered_messages = [message.to_dict() for message in messages if message.content]

        if response_schema:
            rendered_messages.append(
                {
                    "role": "system",
                    "content": (
                        "Responda estritamente em JSON válido compatível com este schema:\n"
                        f"{json.dumps(response_schema, ensure_ascii=False)}"
                    ),
                }
            )

        payload: dict[str, Any] = {
            "model": effective_model,
            "messages": rendered_messages,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": temperature,
                "num_ctx": self.num_ctx,
            },
        }
        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens
        if response_schema:
            payload["format"] = response_schema

        def _perform_request() -> requests.Response:
            return requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout_seconds,
            )

        response = await asyncio.to_thread(_perform_request)
        if response.status_code >= 400:
            logger.warning(
                "ollama_provider.chat_failed",
                model=effective_model,
                status_code=response.status_code,
                response_bytes=len(response.content),
            )
            response.raise_for_status()

        body = response.json()
        message = body.get("message") or {}
        text = (message.get("content") or body.get("response") or "").strip()
        input_tokens = int(body.get("prompt_eval_count") or _estimate_tokens("\n".join(m.content for m in messages)))
        output_tokens = int(body.get("eval_count") or _estimate_tokens(text))

        logger.info(
            "ollama_provider.complete",
            model=effective_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        return AICompletionResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=0.0,
            cost_known=True,
            model_used=effective_model,
            provider="ollama",
        )
