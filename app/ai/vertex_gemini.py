"""
Provider: Vertex AI — Gemini 2.5 Flash-Lite (default) + suporte a outros modelos Gemini.

Autentica via Service Account JSON armazenado no AWS Secrets Manager
(secret: prod/klm/vertex/service-account-json).

Rastreia tokens sem manter uma tabela de preços embutida. Chamadas diretas
consultam o catálogo versionado e ficam bloqueadas quando o custo é desconhecido.
"""
import json
import os
from typing import Any

import boto3

from app.ai.base import AICompletionResult, AIMessage, AIProvider
from app.ai.catalog import PriceCatalog, parse_json_string_set
from app.ai.models import ModelMediaPart, ProviderName, RouteCandidate
from app.ai.policy import require_legacy_direct_calls_enabled
from app.ai.security import require_allowed_model
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type


def _load_vertex_credentials() -> str:
    """
    Carrega o Service Account JSON do Vertex AI.

    Ordem de precedência:
    1. AWS Secrets Manager (prod/klm/vertex/service-account-json)
    2. Variável de ambiente GOOGLE_APPLICATION_CREDENTIALS (caminho para arquivo)
    """
    from app.core.config import settings

    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if creds_path and os.path.isfile(creds_path):
        with open(creds_path) as credentials_file:
            return credentials_file.read()

    if not settings.KLM_AWS_ENABLED:
        raise RuntimeError(
            "Credenciais locais do Vertex AI não encontradas e o gate AWS está desligado."
        )

    secret_name = "prod/klm/vertex/service-account-json"
    region = os.environ.get("AWS_REGION", "us-east-1")

    try:
        client = boto3.client("secretsmanager", region_name=region)
        response = client.get_secret_value(SecretId=secret_name)
        secret_str = response.get("SecretString", "")
        if not secret_str:
            raise ValueError("Secret vazio no Secrets Manager.")
        logger.info("vertex_gemini.credentials_loaded_from_secrets_manager")
        return secret_str
    except Exception as exc:
        logger.warning(
            "vertex_gemini.secrets_manager_failed",
            error_type=safe_error_type(exc),
            fallback="GOOGLE_APPLICATION_CREDENTIALS",
        )
        raise RuntimeError(
            "Credenciais do Vertex AI não encontradas. "
            "Configure o secret 'prod/klm/vertex/service-account-json' no AWS Secrets Manager."
        ) from exc


class VertexGeminiProvider(AIProvider):
    """
    Provider para modelos Gemini via Vertex AI SDK.

    Autenticação in-memory via service_account.Credentials (sem arquivo temporário em disco).
    Lazy initialization — cliente criado na primeira chamada.
    Rastreia tokens e custo em cada AICompletionResult.
    """

    def __init__(
        self,
        project_id: str,
        location: str,
        model: str,
        *,
        enforce_runtime_gate: bool = True,
    ):
        self.project_id = project_id
        self.location = location
        self.model = model
        self.enforce_runtime_gate = enforce_runtime_gate
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """Inicializa o cliente Vertex AI na primeira chamada (lazy init thread-safe via GIL)."""
        if self._initialized:
            return

        import vertexai
        from google.oauth2 import service_account

        sa_json = _load_vertex_credentials()
        credentials = service_account.Credentials.from_service_account_info(
            json.loads(sa_json),
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )

        vertexai.init(project=self.project_id, location=self.location, credentials=credentials)
        self._initialized = True
        logger.info(
            "vertex_gemini.initialized",
            project=self.project_id,
            location=self.location,
            model=self.model,
        )

    async def complete(
        self,
        messages: list[AIMessage],
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_schema: dict[str, Any] | None = None,
        model_override: str | None = None,
        *,
        media_parts: tuple[ModelMediaPart, ...] = (),
        **_gateway_context: Any,
    ) -> AICompletionResult:
        """Block direct use; compatibility getters now return ModelGateway."""
        require_legacy_direct_calls_enabled()
        raise AssertionError("unreachable")

    async def _complete_for_gateway(
        self,
        messages: list[AIMessage],
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_schema: dict[str, Any] | None = None,
        model_override: str | None = None,
        *,
        media_parts: tuple[ModelMediaPart, ...] = (),
        **_gateway_context: Any,
    ) -> AICompletionResult:
        """Send messages and optional media through Vertex Gemini with usage provenance."""
        import asyncio

        from app.core.config import settings

        route = RouteCandidate(ProviderName.VERTEX_GEMINI, model_override or self.model)
        prices = PriceCatalog.from_json(settings.KLM_AI_PRICE_CATALOG_JSON)
        allowlist = parse_json_string_set(
            settings.KLM_AI_MODEL_ALLOWLIST_JSON,
            "KLM_AI_MODEL_ALLOWLIST_JSON",
        )
        require_allowed_model(route, allowlist)
        prices.require_price(route)

        self._ensure_initialized()

        from vertexai.generative_models import Content, GenerationConfig, GenerativeModel, Part

        effective_model = model_override or self.model

        # Converter AIMessage → formato Vertex
        vertex_contents = []
        system_instruction = None

        for msg in messages:
            if msg.role == "system":
                system_instruction = msg.content
            elif msg.role == "user":
                vertex_contents.append(
                    Content(role="user", parts=[Part.from_text(msg.content)])
                )
            elif msg.role == "assistant":
                vertex_contents.append(
                    Content(role="model", parts=[Part.from_text(msg.content)])
                )

        if media_parts:
            vertex_contents.append(
                Content(
                    role="user",
                    parts=[
                        Part.from_data(data=part.data, mime_type=part.mime_type)
                        for part in media_parts
                    ],
                )
            )

        model = GenerativeModel(effective_model, system_instruction=system_instruction)

        gen_config_kwargs: dict[str, Any] = {"temperature": temperature}
        if max_tokens:
            gen_config_kwargs["max_output_tokens"] = max_tokens
        if response_schema:
            gen_config_kwargs["response_mime_type"] = "application/json"
            gen_config_kwargs["response_schema"] = response_schema

        gen_config = GenerationConfig(**gen_config_kwargs)

        response = await asyncio.to_thread(
            model.generate_content,
            vertex_contents,
            generation_config=gen_config,
        )

        # Extrair métricas de uso
        input_tokens = 0
        output_tokens = 0
        try:
            usage = response.usage_metadata
            input_tokens = getattr(usage, "prompt_token_count", 0) or 0
            output_tokens = getattr(usage, "candidates_token_count", 0) or 0
        except Exception:
            pass

        cost_usd = prices.compute_cost(
            route,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        logger.info(
            "vertex_gemini.complete",
            model=effective_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=str(cost_usd),
        )

        return AICompletionResult(
            text=response.text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=float(cost_usd),
            cost_known=True,
            model_used=effective_model,
            provider="vertex_gemini",
            price_catalog_version=prices.version,
        )
