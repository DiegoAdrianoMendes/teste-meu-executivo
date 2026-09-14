from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import celery
import pytest

from app.ai.accounting import NullModelUsageAccountant, SqlAlchemyModelUsageAccountant
from app.ai.errors import AIConfigurationError
from app.ai.event_store import SqlAlchemyAiGatewayEventStore
from app.ai.factory import build_model_gateway
from app.ai.models import ProviderName
from app.ai.telemetry import InMemoryTelemetrySink
from app.db.session import (
    AsyncSessionLocal,
    WorkerSessionLocal,
    model_usage_session_factory,
)


def _external_settings() -> SimpleNamespace:
    return SimpleNamespace(
        AI_PROVIDER="api_gateway",
        KLM_AI_ALLOWED_BASE_URLS_JSON='["https://api.openai.com/v1"]',
        KLM_AI_MODEL_ALLOWLIST_JSON='["openai:test-model"]',
        KLM_AI_ROUTE_CATALOG_JSON=(
            '{"version":"routes-test","classes":{'
            '"simple":[{"provider":"openai","model":"test-model"}]'
            '},"agents":{}}'
        ),
        KLM_AI_PRICE_CATALOG_JSON=(
            '{"version":"prices-test","currency":"USD","models":{'
            '"openai:test-model":{"input_per_million_usd":"1",'
            '"output_per_million_usd":"2"}}}'
        ),
        KLM_AI_BUDGET_BACKEND="memory",
        KLM_AI_RESPONSE_CACHE_ENABLED=False,
        KLM_AI_CIRCUIT_BACKEND="memory",
        KLM_AI_CIRCUIT_FAILURE_THRESHOLD=2,
        KLM_AI_CIRCUIT_COOLDOWN_SECONDS=60,
        KLM_MODEL_APIS_ENABLED=True,
        KLM_ONLINE_APPROVED=True,
        KLM_AI_MAX_INPUT_TOKENS_PER_CALL=10_000,
        KLM_AI_MAX_OUTPUT_TOKENS_PER_CALL=2_000,
        KLM_AI_MAX_COST_PER_CALL_USD=Decimal("1"),
        KLM_AI_WORKSPACE_DAILY_BUDGET_USD=Decimal("5"),
        KLM_AI_WORKSPACE_DAILY_BUDGETS_JSON="{}",
        KLM_AI_AGENT_CALL_CAPS_JSON="{}",
        KLM_AI_HTTP_TIMEOUT_SECONDS=10,
        KLM_AI_MAX_RETRIES=0,
        KLM_AI_RETRY_BASE_SECONDS=0,
        KLM_AI_RESPONSE_CACHE_TTL_SECONDS=60,
    )


def test_external_factory_refuses_inactive_sql_accounting() -> None:
    with pytest.raises(AIConfigurationError, match="accountant SQL ativo"):
        build_model_gateway(
            _external_settings(),
            providers={ProviderName.OPENAI: object()},
            telemetry=InMemoryTelemetrySink(),
            usage_accountant=NullModelUsageAccountant(),
        )


def test_external_factory_resolves_unconfigured_catalogs_to_canonical_defaults() -> None:
    settings = _external_settings()
    settings.KLM_AI_MODEL_ALLOWLIST_JSON = "[]"
    settings.KLM_AI_ROUTE_CATALOG_JSON = '{"version":"unconfigured","classes":{},"agents":{}}'
    settings.KLM_AI_PRICE_CATALOG_JSON = '{"version":"unconfigured","currency":"USD","models":{}}'

    gateway = build_model_gateway(
        settings,
        providers={ProviderName.OPENAI: object()},
        telemetry=InMemoryTelemetrySink(),
        usage_accountant=SqlAlchemyModelUsageAccountant(),
    )

    assert gateway.route_catalog.version.startswith("klm-routes-")
    assert gateway.price_catalog.version.startswith("klm-prices-")
    assert gateway.model_allowlist


def test_external_factory_refuses_route_without_allowlist_or_price() -> None:
    settings = _external_settings()
    settings.KLM_AI_MODEL_ALLOWLIST_JSON = "[]"

    with pytest.raises(AIConfigurationError, match="allowlist"):
        build_model_gateway(
            settings,
            providers={ProviderName.OPENAI: object()},
            telemetry=InMemoryTelemetrySink(),
        )


def test_external_factory_wires_or_disables_sql_gateway_events() -> None:
    settings = _external_settings()
    gateway = build_model_gateway(
        settings,
        providers={ProviderName.OPENAI: object()},
        telemetry=InMemoryTelemetrySink(),
        usage_accountant=SqlAlchemyModelUsageAccountant(),
    )
    assert isinstance(gateway.event_store, SqlAlchemyAiGatewayEventStore)

    settings.KLM_AI_GATEWAY_EVENTS_ENABLED = False
    disabled_gateway = build_model_gateway(
        settings,
        providers={ProviderName.OPENAI: object()},
        telemetry=InMemoryTelemetrySink(),
        usage_accountant=SqlAlchemyModelUsageAccountant(),
    )
    assert disabled_gateway.event_store is None


def test_model_usage_session_factory_keeps_fastapi_pool_without_celery_task(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        celery,
        "current_task",
        SimpleNamespace(request=SimpleNamespace(id=None)),
    )

    assert model_usage_session_factory() is AsyncSessionLocal


def test_model_usage_session_factory_uses_worker_pool_inside_celery_task(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        celery,
        "current_task",
        SimpleNamespace(request=SimpleNamespace(id="celery-task-1")),
    )

    assert model_usage_session_factory() is WorkerSessionLocal
