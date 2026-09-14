"""Factories for the API-first gateway and explicitly retained legacy providers."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Any

from app.ai.accounting import (
    ModelUsageAccountant,
    NullModelUsageAccountant,
    SqlAlchemyModelUsageAccountant,
)
from app.ai.base import AIProvider
from app.ai.cache import (
    InMemoryResponseCache,
    NullResponseCache,
    RedisResponseCache,
    ResponseCache,
)
from app.ai.canonical_catalog import (
    resolve_model_allowlist,
    resolve_price_catalog,
    resolve_route_catalog,
)
from app.ai.catalog import (
    ModelPrice,
    PriceCatalog,
    RouteCatalog,
    parse_agent_cost_caps,
    parse_json_string_set,
    parse_workspace_budgets,
)
from app.ai.circuit import CircuitBreaker, CircuitBreakerProtocol, RedisCircuitBreaker
from app.ai.errors import AIConfigurationError, BaseURLNotAllowedError
from app.ai.event_store import AiGatewayEventStore, SqlAlchemyAiGatewayEventStore
from app.ai.gateway import GatewayAIProvider, ModelGateway
from app.ai.limits import (
    DailyBudgetLedger,
    InMemoryDailyBudgetLedger,
    RedisDailyBudgetLedger,
)
from app.ai.models import ProviderName, RouteCandidate, TaskClass
from app.ai.policy import ModelAPIGate, RequestPolicy
from app.ai.providers import (
    AnthropicMessagesAdapter,
    FakeModelConfig,
    FakeModelProviderAdapter,
    GeminiInteractionsAdapter,
    MaritacaChatAdapter,
    ModelProviderAdapter,
    OpenAIResponsesAdapter,
    VertexGeminiAdapter,
)
from app.ai.providers.fake import FAKE_PROVIDER_VERSION
from app.ai.security import require_allowed_base_url
from app.ai.telemetry import (
    CompositeTelemetrySink,
    LoggingTelemetrySink,
    RedisStreamTelemetrySink,
    TelemetrySink,
)
from app.ai.workspace_credentials import WorkspaceCredentialResolver

# Existing imports use TaskComplexity; the runtime now shares one canonical enum.
TaskComplexity = TaskClass


def _configured_usage_accountant(settings: Any) -> ModelUsageAccountant:
    if not bool(getattr(settings, "KLM_AI_USAGE_ACCOUNTING_ENABLED", False)):
        return NullModelUsageAccountant()
    return SqlAlchemyModelUsageAccountant()


def _configured_response_cache(settings: Any) -> ResponseCache:
    if not settings.KLM_AI_RESPONSE_CACHE_ENABLED:
        return NullResponseCache()
    max_value_bytes = getattr(
        settings,
        "KLM_AI_RESPONSE_CACHE_MAX_VALUE_BYTES",
        1_048_576,
    )
    if getattr(settings, "KLM_AI_RESPONSE_CACHE_BACKEND", "memory") == "redis":
        return RedisResponseCache(
            settings.REDIS_URL,
            max_value_bytes=max_value_bytes,
        )
    return InMemoryResponseCache(
        max_entries=getattr(settings, "KLM_AI_RESPONSE_CACHE_MAX_ENTRIES", 1_024),
        max_value_bytes=max_value_bytes,
    )


def _configured_telemetry(settings: Any) -> TelemetrySink:
    logging_sink = LoggingTelemetrySink()
    if getattr(settings, "KLM_AI_TELEMETRY_BACKEND", "logging") != "redis":
        return logging_sink
    return CompositeTelemetrySink(
        logging_sink,
        RedisStreamTelemetrySink(
            settings.REDIS_URL,
            max_length=getattr(settings, "KLM_AI_TELEMETRY_STREAM_MAXLEN", 100_000),
        ),
    )


def _configured_event_store(settings: Any) -> AiGatewayEventStore | None:
    """Sink SQL consultável de eventos do gateway (telemetria de fallback).

    Somente para o gateway externo: o runtime local fake permanece livre de
    acoplamento com o banco. A escrita é best-effort no gateway — falha de
    persistência nunca derruba uma chamada de modelo.
    """
    if not bool(getattr(settings, "KLM_AI_GATEWAY_EVENTS_ENABLED", True)):
        return None
    return SqlAlchemyAiGatewayEventStore(
        retention_days=int(getattr(settings, "KLM_AI_GATEWAY_EVENTS_RETENTION_DAYS", 90)),
    )


def _configured_circuit_breaker(settings: Any) -> CircuitBreakerProtocol:
    arguments = {
        "failure_threshold": settings.KLM_AI_CIRCUIT_FAILURE_THRESHOLD,
        "cooldown_seconds": settings.KLM_AI_CIRCUIT_COOLDOWN_SECONDS,
    }
    if getattr(settings, "KLM_AI_CIRCUIT_BACKEND", "memory") == "redis":
        return RedisCircuitBreaker(settings.REDIS_URL, **arguments)
    return CircuitBreaker(**arguments)


def _require_closed_local_fake_runtime(settings: Any) -> None:
    unsafe_flags = (
        "KLM_EXTERNAL_ACTIONS_ENABLED",
        "KLM_AWS_ENABLED",
        "KLM_ONLINE_APPROVED",
        "KLM_MODEL_APIS_ENABLED",
    )
    enabled = [name for name in unsafe_flags if bool(getattr(settings, name, True))]
    if getattr(settings, "KLM_RUNTIME_MODE", "") != "local_simulation" or enabled:
        details = ", ".join(enabled) if enabled else "runtime mode"
        raise AIConfigurationError(
            "Fake model factory requires local_simulation with every online/external gate "
            f"closed; invalid: {details}."
        )


def _validate_external_catalogs(
    settings: Any,
    *,
    route_catalog: RouteCatalog,
    price_catalog: PriceCatalog,
    model_allowlist: frozenset[str],
) -> None:
    """Fail closed before an external provider can be constructed.

    The local simulation deliberately has its own in-memory catalogs. Once an
    external gate is opened, however, a missing route, allowlist entry, or
    price would make model selection and cost control implicit. That is too
    late for the API boundary, so validate the complete catalog contract here.
    """
    if not (
        bool(getattr(settings, "KLM_MODEL_APIS_ENABLED", False))
        or bool(getattr(settings, "KLM_ONLINE_APPROVED", False))
    ):
        return

    if route_catalog.version.lower() in {"", "unconfigured", "default"}:
        raise AIConfigurationError(
            "APIs externas bloqueadas: configure uma versão publicada do catálogo de rotas."
        )
    if not route_catalog.classes and not route_catalog.agents:
        raise AIConfigurationError(
            "APIs externas bloqueadas: o catálogo de rotas não possui nenhuma rota ativa."
        )
    if price_catalog.version.lower() in {"", "unconfigured", "default"}:
        raise AIConfigurationError(
            "APIs externas bloqueadas: configure uma versão publicada do catálogo de preços."
        )
    if not price_catalog.models:
        raise AIConfigurationError(
            "APIs externas bloqueadas: o catálogo de preços não possui nenhum modelo."
        )
    if not model_allowlist:
        raise AIConfigurationError(
            "APIs externas bloqueadas: configure a allowlist de modelos antes de abrir os gates."
        )

    route_keys = {
        candidate.key
        for routes in (*route_catalog.classes.values(), *route_catalog.agents.values())
        for candidate in routes
    }
    missing_allowlist = sorted(route_keys - model_allowlist)
    if missing_allowlist:
        raise AIConfigurationError(
            "APIs externas bloqueadas: rotas fora da allowlist: "
            + ", ".join(missing_allowlist)
            + "."
        )
    missing_prices = sorted(route_keys - price_catalog.models.keys())
    if missing_prices:
        raise AIConfigurationError(
            "APIs externas bloqueadas: faltam preços para: " + ", ".join(missing_prices) + "."
        )


def build_local_fake_model_gateway(
    settings: Any,
    *,
    providers: dict[ProviderName, ModelProviderAdapter] | None = None,
    budget_ledger: DailyBudgetLedger | None = None,
    telemetry: TelemetrySink | None = None,
    response_cache: ResponseCache | None = None,
    usage_accountant: ModelUsageAccountant | None = None,
) -> ModelGateway:
    """Build a network-free gateway for the explicitly closed local runtime."""
    _require_closed_local_fake_runtime(settings)
    model = str(getattr(settings, "KLM_AI_FAKE_MODEL", "klm-fake-v1")).strip()
    if not model or ":" in model:
        raise AIConfigurationError("Fake model name is required and cannot contain ':'.")
    candidate = RouteCandidate(provider=ProviderName.FAKE, model=model)
    route_catalog = RouteCatalog(
        version=f"local-fake-routes-{FAKE_PROVIDER_VERSION}",
        classes={task_class: (candidate,) for task_class in TaskClass},
        agents={},
    )
    price_catalog = PriceCatalog(
        version=f"local-fake-prices-{FAKE_PROVIDER_VERSION}",
        currency="USD",
        effective_at=None,
        models={
            candidate.key: ModelPrice(
                input_per_million_usd=Decimal("0"),
                output_per_million_usd=Decimal("0"),
                cached_input_per_million_usd=Decimal("0"),
            )
        },
    )
    if providers is None:
        fake_config = FakeModelConfig.from_json(
            getattr(settings, "KLM_AI_FAKE_RESPONSES_JSON", "{}"),
            model=model,
            seed=getattr(settings, "KLM_AI_FAKE_SEED", "klm-local-fake-v1"),
            default_text=getattr(
                settings,
                "KLM_AI_FAKE_RESPONSE_TEXT",
                "Local deterministic simulation.",
            ),
        )
        providers = {ProviderName.FAKE: FakeModelProviderAdapter(fake_config)}
    elif set(providers) != {ProviderName.FAKE}:
        raise AIConfigurationError("Local fake gateway accepts only the fake provider.")

    if response_cache is None:
        response_cache = _configured_response_cache(settings)
    if budget_ledger is None:
        budget_ledger = (
            RedisDailyBudgetLedger(settings.REDIS_URL)
            if getattr(settings, "KLM_AI_BUDGET_BACKEND", "memory") == "redis"
            else InMemoryDailyBudgetLedger()
        )
    return ModelGateway(
        gate=ModelAPIGate(
            model_apis_enabled=False,
            online_approved=False,
            local_fake_enabled=True,
        ),
        route_catalog=route_catalog,
        price_catalog=price_catalog,
        providers=providers,
        model_allowlist=frozenset({candidate.key}),
        request_policy=RequestPolicy(
            max_input_tokens=settings.KLM_AI_MAX_INPUT_TOKENS_PER_CALL,
            max_output_tokens=settings.KLM_AI_MAX_OUTPUT_TOKENS_PER_CALL,
            max_cost_per_call_usd=settings.KLM_AI_MAX_COST_PER_CALL_USD,
            default_workspace_daily_budget_usd=(settings.KLM_AI_WORKSPACE_DAILY_BUDGET_USD),
            workspace_daily_budgets_usd=parse_workspace_budgets(
                settings.KLM_AI_WORKSPACE_DAILY_BUDGETS_JSON
            ),
            agent_call_caps_usd=parse_agent_cost_caps(settings.KLM_AI_AGENT_CALL_CAPS_JSON),
        ),
        budget_ledger=budget_ledger,
        circuit_breaker=_configured_circuit_breaker(settings),
        telemetry=telemetry or _configured_telemetry(settings),
        response_cache=response_cache,
        default_timeout_seconds=settings.KLM_AI_HTTP_TIMEOUT_SECONDS,
        max_retries=settings.KLM_AI_MAX_RETRIES,
        retry_base_seconds=settings.KLM_AI_RETRY_BASE_SECONDS,
        response_cache_enabled=settings.KLM_AI_RESPONSE_CACHE_ENABLED,
        response_cache_ttl_seconds=settings.KLM_AI_RESPONSE_CACHE_TTL_SECONDS,
        usage_accountant=usage_accountant or _configured_usage_accountant(settings),
    )


def build_model_gateway(
    settings: Any,
    *,
    providers: dict[ProviderName, ModelProviderAdapter] | None = None,
    budget_ledger: DailyBudgetLedger | None = None,
    telemetry: TelemetrySink | None = None,
    response_cache: ResponseCache | None = None,
    usage_accountant: ModelUsageAccountant | None = None,
) -> ModelGateway:
    if getattr(settings, "AI_PROVIDER", "api_gateway") == "fake":
        return build_local_fake_model_gateway(
            settings,
            providers=providers,
            budget_ledger=budget_ledger,
            telemetry=telemetry,
            response_cache=response_cache,
            usage_accountant=usage_accountant,
        )

    allowed_base_urls = parse_json_string_set(
        settings.KLM_AI_ALLOWED_BASE_URLS_JSON,
        "KLM_AI_ALLOWED_BASE_URLS_JSON",
    )
    # Default de produção: env com a sentinela 'unconfigured' recebe o
    # catálogo canônico versionado (klm-routes-2026-07-24). Env explícito
    # continua vencendo; env inválido continua falhando fechado.
    route_catalog = resolve_route_catalog(settings.KLM_AI_ROUTE_CATALOG_JSON)
    price_catalog = resolve_price_catalog(settings.KLM_AI_PRICE_CATALOG_JSON)
    model_allowlist = resolve_model_allowlist(
        settings.KLM_AI_MODEL_ALLOWLIST_JSON,
        route_catalog=route_catalog,
    )
    _validate_external_catalogs(
        settings,
        route_catalog=route_catalog,
        price_catalog=price_catalog,
        model_allowlist=model_allowlist,
    )

    workspace_credential_resolver: WorkspaceCredentialResolver | None = None
    if providers is None:
        openai_base_url = require_allowed_base_url(
            settings.KLM_AI_OPENAI_BASE_URL,
            allowed_base_urls,
        )
        anthropic_base_url = require_allowed_base_url(
            settings.KLM_AI_ANTHROPIC_BASE_URL,
            allowed_base_urls,
        )
        gemini_base_url = require_allowed_base_url(
            settings.KLM_AI_GEMINI_BASE_URL,
            allowed_base_urls,
        )
        # Maritaca é opcional: se a base URL não estiver na allowlist do
        # ambiente, o adapter simplesmente não é registrado (fail-closed —
        # uma rota maritaca no catálogo falha no preflight com erro visível,
        # sem quebrar a construção do gateway para configurações existentes).
        maritaca_base_url: str | None
        try:
            maritaca_base_url = require_allowed_base_url(
                getattr(settings, "KLM_AI_MARITACA_BASE_URL", "https://chat.maritaca.ai/api"),
                allowed_base_urls,
            )
        except BaseURLNotAllowedError:
            maritaca_base_url = None
        providers = {
            ProviderName.OPENAI: OpenAIResponsesAdapter(
                api_key=settings.OPENAI_API_KEY,
                base_url=openai_base_url,
            ),
            ProviderName.ANTHROPIC: AnthropicMessagesAdapter(
                api_key=settings.ANTHROPIC_API_KEY,
                base_url=anthropic_base_url,
            ),
            ProviderName.GEMINI_API: GeminiInteractionsAdapter(
                api_key=settings.GEMINI_API_KEY,
                base_url=gemini_base_url,
            ),
            ProviderName.VERTEX_GEMINI: VertexGeminiAdapter(
                project_id=settings.VERTEX_PROJECT_ID,
                location=settings.VERTEX_LOCATION,
            ),
        }
        if maritaca_base_url is not None:
            providers[ProviderName.MARITACA] = MaritacaChatAdapter(
                api_key=getattr(settings, "MARITACA_API_KEY", ""),
                base_url=maritaca_base_url,
            )
        # BYOK por workspace: os clones usam as MESMAS base URLs validadas
        # pela allowlist; somente a API key muda por workspace. Singleton da
        # plataforma permanece o fallback quando não há credencial ativa.
        adapter_factories: dict[ProviderName, Any] = {
            ProviderName.OPENAI: lambda key: OpenAIResponsesAdapter(
                api_key=key,
                base_url=openai_base_url,
            ),
            ProviderName.ANTHROPIC: lambda key: AnthropicMessagesAdapter(
                api_key=key,
                base_url=anthropic_base_url,
            ),
            ProviderName.GEMINI_API: lambda key: GeminiInteractionsAdapter(
                api_key=key,
                base_url=gemini_base_url,
            ),
        }
        if maritaca_base_url is not None:
            resolved_maritaca_base_url = maritaca_base_url
            adapter_factories[ProviderName.MARITACA] = lambda key: MaritacaChatAdapter(
                api_key=key,
                base_url=resolved_maritaca_base_url,
            )
        workspace_credential_resolver = WorkspaceCredentialResolver(
            adapter_factories=adapter_factories,
        )

    if budget_ledger is None:
        if settings.KLM_AI_BUDGET_BACKEND == "redis":
            budget_ledger = RedisDailyBudgetLedger(settings.REDIS_URL)
        else:
            budget_ledger = InMemoryDailyBudgetLedger()
    if response_cache is None:
        response_cache = _configured_response_cache(settings)

    resolved_usage_accountant = usage_accountant or _configured_usage_accountant(settings)
    if not resolved_usage_accountant.active:
        raise AIConfigurationError(
            "O gateway externo exige KLM_AI_USAGE_ACCOUNTING_ENABLED e accountant SQL ativo."
        )

    return ModelGateway(
        gate=ModelAPIGate(
            model_apis_enabled=settings.KLM_MODEL_APIS_ENABLED,
            online_approved=settings.KLM_ONLINE_APPROVED,
        ),
        route_catalog=route_catalog,
        price_catalog=price_catalog,
        providers=providers,
        model_allowlist=model_allowlist,
        request_policy=RequestPolicy(
            max_input_tokens=settings.KLM_AI_MAX_INPUT_TOKENS_PER_CALL,
            max_output_tokens=settings.KLM_AI_MAX_OUTPUT_TOKENS_PER_CALL,
            max_cost_per_call_usd=settings.KLM_AI_MAX_COST_PER_CALL_USD,
            default_workspace_daily_budget_usd=(settings.KLM_AI_WORKSPACE_DAILY_BUDGET_USD),
            workspace_daily_budgets_usd=parse_workspace_budgets(
                settings.KLM_AI_WORKSPACE_DAILY_BUDGETS_JSON
            ),
            agent_call_caps_usd=parse_agent_cost_caps(settings.KLM_AI_AGENT_CALL_CAPS_JSON),
        ),
        budget_ledger=budget_ledger,
        circuit_breaker=_configured_circuit_breaker(settings),
        telemetry=telemetry or _configured_telemetry(settings),
        response_cache=response_cache,
        default_timeout_seconds=settings.KLM_AI_HTTP_TIMEOUT_SECONDS,
        max_retries=settings.KLM_AI_MAX_RETRIES,
        retry_base_seconds=settings.KLM_AI_RETRY_BASE_SECONDS,
        response_cache_enabled=settings.KLM_AI_RESPONSE_CACHE_ENABLED,
        response_cache_ttl_seconds=settings.KLM_AI_RESPONSE_CACHE_TTL_SECONDS,
        usage_accountant=resolved_usage_accountant,
        workspace_credential_resolver=workspace_credential_resolver,
        prefer_maritaca_for_simple=bool(getattr(settings, "KLM_AI_SIMPLE_PREFER_MARITACA", False)),
        event_store=_configured_event_store(settings),
    )


@lru_cache(maxsize=1)
def get_model_gateway() -> ModelGateway:
    from app.core.config import settings

    return build_model_gateway(settings)


@lru_cache(maxsize=4)
def _gateway_provider(task_class: TaskClass) -> AIProvider:
    from app.core.config import settings

    return GatewayAIProvider(
        get_model_gateway(),
        task_class,
        default_max_output_tokens=settings.KLM_AI_DEFAULT_MAX_OUTPUT_TOKENS,
    )


def get_ai_provider() -> AIProvider:
    """Return the canonical gateway for every application model call."""
    return _gateway_provider(TaskClass.MEDIUM)


def get_provider_for_task(complexity: TaskClass) -> AIProvider:
    """Route every launch task through the API gateway, never through Ollama/Gemma."""
    return _gateway_provider(TaskClass(complexity))


def get_model_for_task(complexity: TaskClass) -> None:
    """The versioned route catalog owns model selection; callers must not override it."""
    return None


@lru_cache(maxsize=1)
def get_gemma_provider() -> AIProvider:
    """Compatibility name routed through the accounted canonical gateway."""
    return get_provider_for_task(TaskClass.SIMPLE)


@lru_cache(maxsize=1)
def get_gemini_pro_provider() -> AIProvider:
    """Compatibility name routed through the accounted canonical gateway."""
    return get_provider_for_task(TaskClass.COMPLEX)


@lru_cache(maxsize=1)
def get_ollama_provider() -> AIProvider:
    """Compatibility name routed through the accounted canonical gateway."""
    return get_provider_for_task(TaskClass.SIMPLE)


def clear_ai_factory_caches() -> None:
    get_model_gateway.cache_clear()
    _gateway_provider.cache_clear()
    get_gemma_provider.cache_clear()
    get_gemini_pro_provider.cache_clear()
    get_ollama_provider.cache_clear()
