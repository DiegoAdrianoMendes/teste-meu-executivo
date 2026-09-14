"""Catálogo canônico de rotas e preços — default de produção versionado.

Quando os envs ``KLM_AI_ROUTE_CATALOG_JSON`` / ``KLM_AI_PRICE_CATALOG_JSON``
chegam com a sentinela ``unconfigured`` (o default de fábrica), o runtime
aplica este catálogo canônico em vez de ficar sem rota nenhuma. Um catálogo
explícito no env SEMPRE vence o canônico; um catálogo explícito inválido
continua falhando fechado (nunca inventamos configuração).

Invariantes do canônico (cobertas por teste):
- toda classe de tarefa tem >= 2 provedores DISTINTOS (fallback real);
- todo par provider:model tem preço no catálogo de preços canônico;
- a allowlist canônica é exatamente o conjunto de rotas do catálogo;
- os nomes de modelo são os aceitos pelos adapters registrados na factory.
"""
from __future__ import annotations

import json
from functools import lru_cache

from app.ai.catalog import PriceCatalog, RouteCatalog, parse_json_string_set

CANONICAL_ROUTE_CATALOG_VERSION = "klm-routes-2026-09-08.2"
CANONICAL_PRICE_CATALOG_VERSION = "klm-prices-2026-09-08.2"

# Sentinelas tratadas como "não configurado" — espelham o conjunto rejeitado
# por _validate_external_catalogs na factory.
UNCONFIGURED_CATALOG_VERSIONS = frozenset({"", "unconfigured", "default"})

# ── Rotas por classe (ordem = prioridade de fallback) ───────────────────────
# Nomes de modelo conferidos contra os adapters existentes:
#   anthropic  → AnthropicMessagesAdapter (Messages API: claude-*)
#   openai     → OpenAIResponsesAdapter (Responses API: gpt-*)
#   gemini_api → GeminiInteractionsAdapter (generativelanguage: gemini-3.x; os 2.5 foram
#                 aposentados para novos usuários em 08/09/2026)
#   vertex_gemini → VertexGeminiAdapter (VERTEX_MODEL_PRO: gemini-2.5-pro)
#   maritaca   → MaritacaChatAdapter (sabiazinho-3)
_CANONICAL_CLASSES: dict[str, list[dict[str, str]]] = {
    "premium": [
        {"provider": "anthropic", "model": "claude-opus-4-8"},
        {"provider": "openai", "model": "gpt-5.6-sol"},
        {"provider": "vertex_gemini", "model": "gemini-2.5-pro"},
    ],
    "complex": [
        {"provider": "anthropic", "model": "claude-sonnet-5"},
        {"provider": "openai", "model": "gpt-5.6-terra"},
        {"provider": "gemini_api", "model": "gemini-3.6-flash"},
    ],
    # medium (leitura de documentos): decisão do titular em 08/09/2026 — OpenAI
    # gpt-5.6-luna primeiro (US$ 0,20 / 1,20 por 1M), Gemini Flash e Haiku só de reserva.
    "medium": [
        {"provider": "openai", "model": "gpt-5.6-luna"},
        {"provider": "gemini_api", "model": "gemini-3.6-flash"},
        {"provider": "anthropic", "model": "claude-haiku-4-5"},
    ],
    # simple (conversas): a Google aposentou os gemini-2.5 para novos usuários em
    # 08/09/2026 ("no longer available to new users"); 3.1 Flash-Lite é o Gemini mais
    # barato da tabela atual. Luna antes do Haiku por custo; Sabiá fora por decisão do
    # titular ("sem Maritaca").
    "simple": [
        {"provider": "gemini_api", "model": "gemini-3.1-flash-lite"},
        {"provider": "openai", "model": "gpt-5.6-luna"},
        {"provider": "anthropic", "model": "claude-haiku-4-5"},
    ],
}

# ── Preços USD por 1M de tokens (lista jul/2026) ────────────────────────────
# cached_input só onde a tarifa de cache é conhecida e estável; ausência de
# cached_input cobra o token cacheado como cheio (conservador, fail-safe).
# sabiazinho-3: tabela Maritaca ~R$1 (input) / ~R$3 (output) por 1M —
# convertido a USD com margem cambial => 0.20 / 0.60.
_CANONICAL_PRICES: dict[str, dict[str, str]] = {
    "anthropic:claude-opus-4-8": {
        "input_per_million_usd": "5.00",
        "output_per_million_usd": "25.00",
        "cached_input_per_million_usd": "0.50",
    },
    "anthropic:claude-sonnet-5": {
        "input_per_million_usd": "3.00",
        "output_per_million_usd": "15.00",
        "cached_input_per_million_usd": "0.30",
    },
    "anthropic:claude-haiku-4-5": {
        "input_per_million_usd": "1.00",
        "output_per_million_usd": "5.00",
        "cached_input_per_million_usd": "0.10",
    },
    # gpt-5.6-sol: tabela OpenAI de 08/09/2026 (contexto curto), preço promocional
    # garantido até 21/11/2026: 4.00 entrada, 0.40 cache, 20.00 saída.
    "openai:gpt-5.6-sol": {
        "input_per_million_usd": "4.00",
        "output_per_million_usd": "20.00",
        "cached_input_per_million_usd": "0.40",
    },
    # gpt-5.6-terra: tabela OpenAI de 08/09/2026 (contexto curto): 2.00 / 0.20 / 12.00.
    "openai:gpt-5.6-terra": {
        "input_per_million_usd": "2.00",
        "output_per_million_usd": "12.00",
        "cached_input_per_million_usd": "0.20",
    },
    # gpt-5.6-luna: tabela OpenAI de 08/09/2026 (contexto curto): 0.20 entrada,
    # 0.02 entrada em cache, 1.20 saída por 1M.
    "openai:gpt-5.6-luna": {
        "input_per_million_usd": "0.20",
        "output_per_million_usd": "1.20",
        "cached_input_per_million_usd": "0.02",
    },
    # gemini-3.6-flash: tabela Google de 08/09/2026 (pago, texto), preço válido até
    # 31/12/2026: 0.75 entrada, 3.75 saída (inclui pensamento), 0.075 cache. A partir
    # de 01/01/2027 a tabela anuncia 1.50 / 7.50 / 0.15.
    "gemini_api:gemini-3.6-flash": {
        "input_per_million_usd": "0.75",
        "output_per_million_usd": "3.75",
        "cached_input_per_million_usd": "0.075",
    },
    # gemini-3.1-flash-lite: tabela Google de 08/09/2026 (pago, texto): 0.25 / 1.50, cache 0.025.
    "gemini_api:gemini-3.1-flash-lite": {
        "input_per_million_usd": "0.25",
        "output_per_million_usd": "1.50",
        "cached_input_per_million_usd": "0.025",
    },
    "vertex_gemini:gemini-2.5-pro": {
        "input_per_million_usd": "1.25",
        "output_per_million_usd": "10.00",
    },
    "maritaca:sabiazinho-3": {
        "input_per_million_usd": "0.20",
        "output_per_million_usd": "0.60",
    },
}

CANONICAL_ROUTE_CATALOG_JSON = json.dumps(
    {
        "version": CANONICAL_ROUTE_CATALOG_VERSION,
        "classes": _CANONICAL_CLASSES,
        "agents": {},
    },
    ensure_ascii=False,
    sort_keys=True,
)

CANONICAL_PRICE_CATALOG_JSON = json.dumps(
    {
        "version": CANONICAL_PRICE_CATALOG_VERSION,
        "currency": "USD",
        "effective_at": "2026-07-24",
        "models": _CANONICAL_PRICES,
    },
    ensure_ascii=False,
    sort_keys=True,
)

CANONICAL_MODEL_ALLOWLIST: frozenset[str] = frozenset(
    f"{route['provider']}:{route['model']}"
    for routes in _CANONICAL_CLASSES.values()
    for route in routes
)


@lru_cache(maxsize=1)
def canonical_route_catalog() -> RouteCatalog:
    """Catálogo canônico parseado pelo MESMO caminho usado para o env."""
    return RouteCatalog.from_json(CANONICAL_ROUTE_CATALOG_JSON)


@lru_cache(maxsize=1)
def canonical_price_catalog() -> PriceCatalog:
    return PriceCatalog.from_json(CANONICAL_PRICE_CATALOG_JSON)


def _is_unconfigured_route_catalog(catalog: RouteCatalog) -> bool:
    return (
        catalog.version.strip().lower() in UNCONFIGURED_CATALOG_VERSIONS
        or (not catalog.classes and not catalog.agents)
    )


def _is_unconfigured_price_catalog(catalog: PriceCatalog) -> bool:
    return (
        catalog.version.strip().lower() in UNCONFIGURED_CATALOG_VERSIONS
        or not catalog.models
    )


def resolve_route_catalog(raw_json: str) -> RouteCatalog:
    """Env explícito vence; sentinela 'unconfigured' aplica o canônico.

    JSON inválido continua levantando ``AIConfigurationError`` (fail-closed):
    configuração explícita quebrada nunca é substituída silenciosamente.
    """
    configured = RouteCatalog.from_json(raw_json)
    if _is_unconfigured_route_catalog(configured):
        return canonical_route_catalog()
    return configured


def resolve_price_catalog(raw_json: str) -> PriceCatalog:
    configured = PriceCatalog.from_json(raw_json)
    if _is_unconfigured_price_catalog(configured):
        return canonical_price_catalog()
    return configured


def resolve_model_allowlist(
    raw_json: str,
    *,
    route_catalog: RouteCatalog,
) -> frozenset[str]:
    """Allowlist do env vence; vazia + catálogo canônico => allowlist canônica.

    A allowlist canônica só é aplicada quando o catálogo de rotas EM USO é o
    canônico — uma allowlist vazia com catálogo customizado permanece vazia
    (e as APIs externas seguem bloqueadas pela validação da factory).
    """
    configured = parse_json_string_set(raw_json, "KLM_AI_MODEL_ALLOWLIST_JSON")
    if not configured and route_catalog.version == CANONICAL_ROUTE_CATALOG_VERSION:
        return CANONICAL_MODEL_ALLOWLIST
    return configured


__all__ = [
    "CANONICAL_MODEL_ALLOWLIST",
    "CANONICAL_PRICE_CATALOG_JSON",
    "CANONICAL_PRICE_CATALOG_VERSION",
    "CANONICAL_ROUTE_CATALOG_JSON",
    "CANONICAL_ROUTE_CATALOG_VERSION",
    "UNCONFIGURED_CATALOG_VERSIONS",
    "canonical_price_catalog",
    "canonical_route_catalog",
    "resolve_model_allowlist",
    "resolve_price_catalog",
    "resolve_route_catalog",
]
