"""
Provider: Gemma via Vertex AI Model Garden endpoint.

Usa Gemma suportado oficialmente no Vertex AI através de endpoint dedicado
de Model Garden/Open Models, em vez de depender de IDs serverless que podem
não existir no projeto.

O fluxo esperado em produção é:
1. habilitar o modelo open model no Model Garden do projeto
2. fazer o deploy do modelo para um endpoint Vertex AI
3. configurar o endpoint ID nas variáveis de ambiente

Como endpoints de open models podem ser cobrados por infraestrutura, chamadas
diretas exigem uma entrada explícita no catálogo de custo. Sem ela, falham antes
do request em vez de registrar custo zero como se fosse conhecido.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account

from app.ai.base import AICompletionResult, AIMessage, AIProvider
from app.ai.catalog import PriceCatalog, parse_json_string_set
from app.ai.models import ProviderName, RouteCandidate
from app.ai.policy import require_legacy_direct_calls_enabled
from app.ai.security import require_allowed_model
from app.ai.vertex_gemini import _load_vertex_credentials
from app.core.config import settings
from app.core.logging import logger


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, round(len(text) / 4))


def _build_prompt(messages: List[AIMessage]) -> str:
    parts: list[str] = []
    for message in messages:
        if not message.content:
            continue
        role = message.role.upper()
        parts.append(f"{role}:\n{message.content.strip()}")
    return "\n\n".join(parts).strip()


def _extract_text(prediction: Any) -> str:
    if isinstance(prediction, str):
        return prediction.strip()
    if isinstance(prediction, list):
        return "\n".join(_extract_text(item) for item in prediction if item).strip()
    if isinstance(prediction, dict):
        for key in (
            "generated_text",
            "output",
            "text",
            "content",
            "prediction",
            "response",
        ):
            value = prediction.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list):
                rendered = "\n".join(_extract_text(item) for item in value if item).strip()
                if rendered:
                    return rendered
        candidates = prediction.get("predictions")
        if isinstance(candidates, list) and candidates:
            return _extract_text(candidates[0])
    return str(prediction).strip()


class VertexGemmaProvider(AIProvider):
    """Provider para Gemma em endpoint oficial do Vertex AI."""

    def __init__(
        self,
        project_id: str,
        location: str,
        default_model: str,
        simple_endpoint_id: str = "",
        medium_endpoint_id: str = "",
    ) -> None:
        self.project_id = project_id
        self.location = location
        self.default_model = default_model
        self.simple_endpoint_id = simple_endpoint_id
        self.medium_endpoint_id = medium_endpoint_id
        self._credentials: Optional[service_account.Credentials] = None

    def _ensure_credentials(self) -> service_account.Credentials:
        if self._credentials is not None:
            return self._credentials

        sa_json = _load_vertex_credentials()
        self._credentials = service_account.Credentials.from_service_account_info(
            json.loads(sa_json),
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        logger.info(
            "vertex_gemma_provider.initialized",
            project=self.project_id,
            location=self.location,
            simple_endpoint_id=bool(self.simple_endpoint_id),
            medium_endpoint_id=bool(self.medium_endpoint_id),
        )
        return self._credentials

    def _resolve_endpoint(self, model_override: Optional[str]) -> tuple[str, str]:
        effective_model = model_override or self.default_model
        simple_model = getattr(settings, "GEMMA_MODEL_SIMPLE", "google/gemma3@gemma-3-4b-it")
        medium_model = getattr(settings, "GEMMA_MODEL_MEDIUM", "google/gemma3@gemma-3-27b-it")

        if effective_model == simple_model:
            endpoint_id = self.simple_endpoint_id
        elif effective_model == medium_model:
            endpoint_id = self.medium_endpoint_id or self.simple_endpoint_id
        else:
            endpoint_id = self.medium_endpoint_id or self.simple_endpoint_id

        if not endpoint_id:
            raise RuntimeError(
                f"Endpoint do Gemma não configurado para o modelo '{effective_model}'. "
                "Defina GEMMA_ENDPOINT_SIMPLE e/ou GEMMA_ENDPOINT_MEDIUM."
            )

        return effective_model, endpoint_id

    async def complete(
        self,
        messages: List[AIMessage],
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        response_schema: Optional[Dict[str, Any]] = None,
        model_override: Optional[str] = None,
    ) -> AICompletionResult:
        import asyncio

        require_legacy_direct_calls_enabled()
        effective_model, endpoint_id = self._resolve_endpoint(model_override)
        route = RouteCandidate(ProviderName.VERTEX_GEMMA, effective_model)
        allowlist = parse_json_string_set(
            settings.KLM_AI_MODEL_ALLOWLIST_JSON,
            "KLM_AI_MODEL_ALLOWLIST_JSON",
        )
        require_allowed_model(route, allowlist)
        prices = PriceCatalog.from_json(settings.KLM_AI_PRICE_CATALOG_JSON)
        prices.require_price(route)
        credentials = self._ensure_credentials()
        prompt = _build_prompt(messages)

        if response_schema:
            prompt = (
                f"{prompt}\n\n"
                "Responda estritamente em JSON válido compatível com este schema conceitual:\n"
                f"{json.dumps(response_schema, ensure_ascii=False)}"
            )

        endpoint = (
            f"https://{self.location}-aiplatform.googleapis.com/v1/"
            f"projects/{self.project_id}/locations/{self.location}/endpoints/{endpoint_id}:predict"
        )

        request_credentials = credentials.with_scopes(["https://www.googleapis.com/auth/cloud-platform"])
        request_credentials.refresh(Request())
        access_token = request_credentials.token

        payload = {
            "instances": [
                {
                    "prompt": prompt,
                    "maxOutputTokens": max_tokens or 1024,
                    "temperature": temperature,
                    "topP": 0.95,
                    "topK": 40,
                }
            ]
        }

        def _perform_request() -> requests.Response:
            return requests.post(
                endpoint,
                json=payload,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                timeout=90,
            )

        response = await asyncio.to_thread(_perform_request)
        if response.status_code >= 400:
            logger.warning(
                "vertex_gemma_provider.predict_failed",
                model=effective_model,
                endpoint_id=endpoint_id,
                status_code=response.status_code,
                response_bytes=len(response.content),
            )
            response.raise_for_status()

        body = response.json()
        predictions = body.get("predictions", [])
        if not predictions:
            raise RuntimeError(f"Resposta vazia do endpoint Gemma: {body}")

        text = _extract_text(predictions[0])
        input_tokens = _estimate_tokens(prompt)
        output_tokens = _estimate_tokens(text)
        cost_usd = prices.compute_cost(
            route,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        logger.info(
            "vertex_gemma_provider.complete",
            model=effective_model,
            endpoint_id=endpoint_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        return AICompletionResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=float(cost_usd),
            cost_known=True,
            model_used=effective_model,
            provider="vertex_gemma",
            price_catalog_version=prices.version,
        )
