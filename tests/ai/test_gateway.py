from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.ai.accounting import ModelUsageReservation
from app.ai.base import AIMessage
from app.ai.cache import InMemoryResponseCache, NullResponseCache, cache_key
from app.ai.catalog import PriceCatalog, RouteCatalog
from app.ai.circuit import CircuitBreaker
from app.ai.errors import (
    BudgetResolutionConflictError,
    ModelAPIDisabledError,
    ModelRequestReplayError,
    NoRouteAvailableError,
    ProviderCallError,
    StructuredOutputError,
    UsageAccountingError,
)
from app.ai.event_store import InMemoryAiGatewayEventStore
from app.ai.gateway import ModelGateway
from app.ai.limits import InMemoryDailyBudgetLedger
from app.ai.models import (
    BudgetReservation,
    CacheEntry,
    MediaKind,
    ModelMediaPart,
    ModelRequest,
    ProviderName,
    ProviderResponse,
    RouteCandidate,
    TaskClass,
)
from app.ai.policy import ModelAPIGate, RequestPolicy
from app.ai.providers.base import ModelProviderAdapter
from app.ai.telemetry import InMemoryTelemetrySink


class FakeProvider(ModelProviderAdapter):
    def __init__(self, name: ProviderName, response=None, error=None) -> None:
        self.name = name
        self.response = response
        self.error = error
        self.config_checks = 0
        self.calls = 0

    def require_configured(self) -> None:
        self.config_checks += 1

    async def generate(self, request, route, *, timeout_seconds):
        self.calls += 1
        if self.error:
            raise self.error
        return self.response


def make_gateway(
    *,
    enabled=True,
    approved=True,
    routes=None,
    prices=None,
    providers=None,
    budget_ledger=None,
    circuit_breaker=None,
    response_cache=None,
    response_cache_enabled=False,
    usage_accountant=None,
    telemetry=None,
    event_store=None,
):
    route_catalog = RouteCatalog.from_json(
        routes
        or '{"version":"routes-test","classes":{"simple":['
        '{"provider":"openai","model":"test-cheap"}]},"agents":{}}'
    )
    price_catalog = PriceCatalog.from_json(
        prices
        or '{"version":"prices-test","currency":"USD","models":{'
        '"openai:test-cheap":{"input_per_million_usd":"1",'
        '"output_per_million_usd":"2"}}}'
    )
    resolved_telemetry = telemetry or InMemoryTelemetrySink()
    ledger = budget_ledger or InMemoryDailyBudgetLedger()
    gateway = ModelGateway(
        gate=ModelAPIGate(enabled, approved),
        route_catalog=route_catalog,
        price_catalog=price_catalog,
        providers=providers or {},
        model_allowlist=frozenset(
            route.key for values in route_catalog.classes.values() for route in values
        ),
        request_policy=RequestPolicy(
            max_input_tokens=10_000,
            max_output_tokens=2_000,
            max_cost_per_call_usd=Decimal("1"),
            default_workspace_daily_budget_usd=Decimal("5"),
            workspace_daily_budgets_usd={},
            agent_call_caps_usd={},
        ),
        budget_ledger=ledger,
        circuit_breaker=circuit_breaker or CircuitBreaker(failure_threshold=2, cooldown_seconds=60),
        telemetry=resolved_telemetry,
        response_cache=response_cache or NullResponseCache(),
        default_timeout_seconds=1,
        max_retries=0,
        retry_base_seconds=0,
        response_cache_enabled=response_cache_enabled,
        response_cache_ttl_seconds=60,
        usage_accountant=usage_accountant or RecordingUsageAccountant(),
        event_store=event_store,
    )
    return gateway, resolved_telemetry, ledger


class RecordingUsageAccountant:
    def __init__(self, order: list[str] | None = None) -> None:
        self.order = order
        self.reserve_calls = 0
        self.started_calls = 0
        self.not_started_calls = 0
        self.uncertain_calls = 0
        self.settle_calls = 0
        self.release_calls = 0
        self.uncertain_codes: list[str] = []
        self.reserve_error: BaseException | None = None
        self.settle_error: BaseException | None = None

    @property
    def active(self) -> bool:
        return True

    async def reserve(self, *, request, estimate, route):
        self.reserve_calls += 1
        if self.order is not None:
            self.order.append("usage-reservation")
        if self.reserve_error is not None:
            raise self.reserve_error
        return ModelUsageReservation(
            workspace_id=uuid4(),
            request_id=request.request_id,
            journal_id=uuid4(),
            request_fingerprint="test-fingerprint",
            period_start=date.today(),
            reserved_tokens=estimate.input_tokens + estimate.output_tokens,
            entitlement_reservation_id=uuid4(),
        )

    async def mark_started(self, **_kwargs):
        self.started_calls += 1

    async def mark_not_started(self, **_kwargs):
        self.not_started_calls += 1

    async def mark_uncertain(self, *, error_code, **_kwargs):
        self.uncertain_calls += 1
        self.uncertain_codes.append(error_code)

    async def settle(
        self,
        *,
        reservation,
        request,
        route,
        response,
        actual_cost_usd,
        price_catalog_version,
        route_catalog_version,
    ):
        self.settle_calls += 1
        if self.settle_error is not None:
            raise self.settle_error

    async def release(self, reservation):
        self.release_calls += 1


class ConcurrentClaimAccountant(RecordingUsageAccountant):
    def __init__(self) -> None:
        super().__init__()
        self._claim_lock = asyncio.Lock()
        self._claims: set[str] = set()

    async def reserve(self, *, request, estimate, route):
        async with self._claim_lock:
            if request.request_id in self._claims:
                raise ModelRequestReplayError(
                    request_id=request.request_id,
                    state="claimed",
                )
            self._claims.add(request.request_id)
        return await super().reserve(request=request, estimate=estimate, route=route)


class FailingTelemetrySink:
    async def require_ready(self) -> None:
        return None

    async def emit(self, _event) -> None:
        raise RuntimeError("telemetry unavailable")


class FailingEventStore:
    async def append(self, **_kwargs) -> None:
        raise RuntimeError("event store unavailable")


def request(**overrides):
    values = {
        "messages": [AIMessage("user", "Classifique o atendimento.")],
        "workspace_id": "workspace-a",
        "agent_name": "sdr",
        "task_class": TaskClass.SIMPLE,
        "max_output_tokens": 100,
    }
    values.update(overrides)
    return ModelRequest(**values)


@pytest.mark.asyncio
async def test_gate_blocks_before_provider_or_credentials_are_checked():
    provider = FakeProvider(ProviderName.OPENAI)
    gateway, _, _ = make_gateway(
        enabled=False,
        approved=False,
        providers={ProviderName.OPENAI: provider},
    )

    with pytest.raises(ModelAPIDisabledError):
        await gateway.generate(request())

    assert provider.config_checks == 0
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_unsupported_media_route_is_blocked_before_credentials_or_network():
    provider = FakeProvider(ProviderName.OPENAI)
    gateway, telemetry, ledger = make_gateway(
        providers={ProviderName.OPENAI: provider},
    )

    with pytest.raises(NoRouteAvailableError):
        await gateway.generate(
            request(media_parts=(ModelMediaPart(MediaKind.AUDIO, "audio/ogg", b"audio"),))
        )

    assert provider.config_checks == 0
    assert provider.calls == 0
    assert await ledger.spent("workspace-a") == Decimal("0")
    assert telemetry.events[-1].outcome.startswith("no_route:")


@pytest.mark.asyncio
async def test_structured_success_records_usage_cost_and_local_validation():
    provider = FakeProvider(
        ProviderName.OPENAI,
        response=ProviderResponse(
            text='{"qualified":true}',
            model="test-cheap",
            input_tokens=50,
            output_tokens=10,
            provider_request_id="provider-request",
        ),
    )
    gateway, telemetry, ledger = make_gateway(providers={ProviderName.OPENAI: provider})
    schema = {
        "type": "object",
        "properties": {"qualified": {"type": "boolean"}},
        "required": ["qualified"],
        "additionalProperties": False,
    }

    result = await gateway.generate(request(response_schema=schema))

    assert result.structured_data == {"qualified": True}
    assert result.cost_known is True
    assert result.price_catalog_version == "prices-test"
    assert result.provider_request_id == "provider-request"
    assert await ledger.spent("workspace-a") == Decimal("0.00007000")
    event = telemetry.events[-1]
    assert event.outcome == "success"
    assert event.route_key == "openai:test-cheap"
    assert event.provider_request_id == "provider-request"
    assert event.estimated_cost_usd is not None
    assert event.estimated_input_tokens > 0
    assert event.estimated_output_tokens == 100
    assert event.request_started is True
    assert event.billing_unknown is False


@pytest.mark.asyncio
async def test_batch_is_bounded_and_waits_for_started_calls_before_raising():
    active = 0
    peak = 0
    started = 0

    class BatchProvider(FakeProvider):
        async def generate(self, request, route, *, timeout_seconds):
            nonlocal active, peak, started
            started += 1
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.01)
                if request.request_id == "fail":
                    raise ProviderCallError("failed before provider", request_started=False)
                return ProviderResponse("ok", route.model, 1, 1)
            finally:
                active -= 1

    provider = BatchProvider(ProviderName.OPENAI)
    gateway, _, _ = make_gateway(providers={ProviderName.OPENAI: provider})
    requests = [request(request_id=str(index)) for index in range(5)]
    requests[2].request_id = "fail"

    with pytest.raises(NoRouteAvailableError):
        await gateway.generate_batch(requests, max_concurrency=2)

    assert peak == 2
    assert started == 5


@pytest.mark.asyncio
async def test_reservation_hook_is_durable_boundary_before_provider_call():
    order: list[str] = []

    class OrderedProvider(FakeProvider):
        async def generate(self, request, route, *, timeout_seconds):
            order.append("provider")
            return await super().generate(
                request,
                route,
                timeout_seconds=timeout_seconds,
            )

    provider = OrderedProvider(
        ProviderName.OPENAI,
        response=ProviderResponse(
            text="ok",
            model="test-cheap",
            input_tokens=10,
            output_tokens=5,
        ),
    )
    gateway, _, ledger = make_gateway(providers={ProviderName.OPENAI: provider})
    captured: list[BudgetReservation] = []

    async def persist_before_provider(reservation, estimate, route, model_request):
        assert await ledger.has_reservation(reservation) is True
        assert estimate.amount_usd > 0
        assert route.key == "openai:test-cheap"
        assert model_request.workspace_id == "workspace-a"
        captured.append(reservation)
        order.append("reservation")

    await gateway.generate(request(), on_reservation=persist_before_provider)

    assert order == ["reservation", "provider"]
    resolution = await ledger.resolution(captured[0])
    assert resolution is not None
    assert resolution.action == "settled"


@pytest.mark.asyncio
async def test_monthly_usage_reservation_is_required_before_provider_call():
    order: list[str] = []

    class OrderedProvider(FakeProvider):
        async def generate(self, request, route, *, timeout_seconds):
            order.append("provider")
            return await super().generate(request, route, timeout_seconds=timeout_seconds)

    accountant = RecordingUsageAccountant(order)
    provider = OrderedProvider(
        ProviderName.OPENAI,
        response=ProviderResponse("ok", "test-cheap", 10, 5),
    )
    gateway, _, _ = make_gateway(
        providers={ProviderName.OPENAI: provider},
        usage_accountant=accountant,
    )

    result = await gateway.generate(request())

    assert order == ["usage-reservation", "provider"]
    assert accountant.reserve_calls == accountant.settle_calls == 1
    assert result.usage_accounted is True


@pytest.mark.asyncio
async def test_concurrent_replay_has_one_claim_winner_and_one_provider_call():
    class SlowProvider(FakeProvider):
        async def generate(self, request, route, *, timeout_seconds):
            self.calls += 1
            await asyncio.sleep(0.02)
            return ProviderResponse("ok", route.model, 10, 5)

    accountant = ConcurrentClaimAccountant()
    provider = SlowProvider(ProviderName.OPENAI)
    gateway, _, _ = make_gateway(
        providers={ProviderName.OPENAI: provider},
        usage_accountant=accountant,
    )
    repeated_request = request(request_id="concurrent-replay-1")

    outcomes = await asyncio.gather(
        gateway.generate(repeated_request),
        gateway.generate(repeated_request),
        return_exceptions=True,
    )

    assert provider.calls == 1
    assert accountant.reserve_calls == 1
    assert accountant.settle_calls == 1
    assert sum(isinstance(value, ModelRequestReplayError) for value in outcomes) == 1
    assert sum(not isinstance(value, BaseException) for value in outcomes) == 1


@pytest.mark.asyncio
async def test_usage_reservation_failure_blocks_network_and_releases_daily_budget():
    accountant = RecordingUsageAccountant()
    accountant.reserve_error = RuntimeError("usage database unavailable")
    provider = FakeProvider(
        ProviderName.OPENAI,
        response=ProviderResponse("ok", "test-cheap", 1, 1),
    )
    gateway, _, ledger = make_gateway(
        providers={ProviderName.OPENAI: provider},
        usage_accountant=accountant,
    )

    with pytest.raises(RuntimeError, match="usage database unavailable"):
        await gateway.generate(request())

    assert provider.calls == 0
    assert await ledger.spent("workspace-a") == Decimal("0")


@pytest.mark.asyncio
async def test_reservation_hook_failure_releases_budget_without_provider_call():
    provider = FakeProvider(
        ProviderName.OPENAI,
        response=ProviderResponse("ok", "test-cheap", 1, 1),
    )
    gateway, _, ledger = make_gateway(providers={ProviderName.OPENAI: provider})

    async def fail_persistence(*_args):
        raise RuntimeError("database checkpoint failed")

    with pytest.raises(RuntimeError, match="checkpoint failed"):
        await gateway.generate(request(), on_reservation=fail_persistence)

    assert provider.calls == 0
    assert await ledger.spent("workspace-a") == Decimal("0")


@pytest.mark.asyncio
async def test_budget_resolution_is_idempotent_and_rejects_conflicts():
    ledger = InMemoryDailyBudgetLedger()
    reservation = await ledger.reserve(
        "workspace-a",
        Decimal("0.10"),
        Decimal("1"),
    )

    first_total = await ledger.settle(reservation, Decimal("0.07"))
    repeated_total = await ledger.settle(reservation, Decimal("0.07"))

    assert first_total == repeated_total == Decimal("0.07")
    resolution = await ledger.resolution(reservation)
    assert resolution is not None
    assert resolution.action == "settled"
    assert resolution.actual_usd == Decimal("0.07")
    with pytest.raises(BudgetResolutionConflictError):
        await ledger.release(reservation)
    with pytest.raises(BudgetResolutionConflictError):
        await ledger.settle(reservation, Decimal("0.08"))


@pytest.mark.asyncio
async def test_unknown_cost_fails_before_network_call():
    provider = FakeProvider(ProviderName.OPENAI)
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=0)
    await breaker.failure("openai:test-cheap")
    gateway, telemetry, ledger = make_gateway(
        prices='{"version":"empty","currency":"USD","models":{}}',
        providers={ProviderName.OPENAI: provider},
        circuit_breaker=breaker,
    )

    with pytest.raises(NoRouteAvailableError):
        await gateway.generate(request())

    assert provider.calls == 0
    assert any("CostUnknownError" in event.outcome for event in telemetry.events)
    assert await breaker.acquire("openai:test-cheap") is True
    await breaker.release("openai:test-cheap")


@pytest.mark.asyncio
async def test_budget_block_after_half_open_acquire_releases_probe():
    provider = FakeProvider(ProviderName.OPENAI)
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=0)
    await breaker.failure("openai:test-cheap")
    ledger = InMemoryDailyBudgetLedger()
    await ledger.reserve("workspace-a", Decimal("5"), Decimal("5"))
    gateway, telemetry, _ = make_gateway(
        providers={ProviderName.OPENAI: provider},
        budget_ledger=ledger,
        circuit_breaker=breaker,
    )

    with pytest.raises(NoRouteAvailableError):
        await gateway.generate(request())

    assert provider.calls == 0
    assert any(event.outcome.startswith("budget_blocked:") for event in telemetry.events)
    assert await breaker.acquire("openai:test-cheap") is True
    await breaker.release("openai:test-cheap")


@pytest.mark.asyncio
async def test_started_request_failure_does_not_fall_back_and_risk_double_charge():
    accountant = RecordingUsageAccountant()
    first = FakeProvider(
        ProviderName.OPENAI,
        error=ProviderCallError(
            "timeout",
            retryable=True,
            request_started=True,
            billing_unknown=True,
        ),
    )
    second = FakeProvider(
        ProviderName.ANTHROPIC,
        response=ProviderResponse("ok", "test-fallback", 1, 1),
    )
    routes = (
        '{"version":"routes-test","classes":{"simple":['
        '{"provider":"openai","model":"test-cheap"},'
        '{"provider":"anthropic","model":"test-fallback"}]},"agents":{}}'
    )
    prices = (
        '{"version":"prices-test","currency":"USD","models":{'
        '"openai:test-cheap":{"input_per_million_usd":"1",'
        '"output_per_million_usd":"2"},'
        '"anthropic:test-fallback":{"input_per_million_usd":"1",'
        '"output_per_million_usd":"2"}}}'
    )
    gateway, telemetry, ledger = make_gateway(
        routes=routes,
        prices=prices,
        providers={ProviderName.OPENAI: first, ProviderName.ANTHROPIC: second},
        usage_accountant=accountant,
    )

    with pytest.raises(ProviderCallError) as caught:
        await gateway.generate(request())

    assert first.calls == 1
    assert second.calls == 0
    assert accountant.uncertain_codes == ["transport"]
    error = caught.value
    assert error.route_key == "openai:test-cheap"
    assert error.provider == "openai"
    assert error.model == "test-cheap"
    assert Decimal(error.estimated_cost_usd) > 0
    assert error.estimated_input_tokens > 0
    assert error.estimated_output_tokens == 100
    assert error.price_catalog_version == "prices-test"
    assert error.route_catalog_version == "routes-test"
    assert error.budget_reservation_id
    assert error.budget_reservation_day
    assert Decimal(error.budget_reserved_amount_usd) == Decimal(error.estimated_cost_usd)
    reservation = BudgetReservation(
        error.budget_reservation_id,
        "workspace-a",
        Decimal(error.budget_reserved_amount_usd),
        error.budget_reservation_day,
    )
    assert await ledger.has_reservation(reservation) is True
    event = telemetry.events[-1]
    assert event.route_key == error.route_key
    assert event.estimated_cost_usd == error.estimated_cost_usd
    assert event.cost_usd is None
    assert event.request_started is True
    assert event.billing_unknown is True


@pytest.mark.asyncio
async def test_unexpected_provider_error_is_sanitized_and_never_falls_back():
    accountant = RecordingUsageAccountant()
    first = FakeProvider(ProviderName.OPENAI, error=RuntimeError("secret provider body"))
    second = FakeProvider(
        ProviderName.ANTHROPIC,
        response=ProviderResponse("must-not-run", "test-fallback", 1, 1),
    )
    routes = (
        '{"version":"routes-test","classes":{"simple":['
        '{"provider":"openai","model":"test-cheap"},'
        '{"provider":"anthropic","model":"test-fallback"}]},"agents":{}}'
    )
    prices = (
        '{"version":"prices-test","currency":"USD","models":{'
        '"openai:test-cheap":{"input_per_million_usd":"1",'
        '"output_per_million_usd":"2"},'
        '"anthropic:test-fallback":{"input_per_million_usd":"1",'
        '"output_per_million_usd":"2"}}}'
    )
    gateway, _, _ = make_gateway(
        routes=routes,
        prices=prices,
        providers={ProviderName.OPENAI: first, ProviderName.ANTHROPIC: second},
        usage_accountant=accountant,
    )

    with pytest.raises(ProviderCallError) as caught:
        await gateway.generate(request())

    assert first.calls == 1
    assert second.calls == 0
    assert accountant.uncertain_codes == ["provider_unexpected"]
    assert caught.value.request_started is True
    assert caught.value.billing_unknown is True
    assert "secret provider body" not in str(caught.value)


@pytest.mark.asyncio
async def test_invalid_structured_output_is_accounted_once_without_second_model_call():
    accountant = RecordingUsageAccountant()
    provider = FakeProvider(
        ProviderName.OPENAI,
        response=ProviderResponse(
            "not-json",
            "test-cheap",
            40,
            8,
            provider_request_id="p-1",
        ),
    )
    gateway, telemetry, ledger = make_gateway(
        providers={ProviderName.OPENAI: provider},
        usage_accountant=accountant,
    )
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }

    with pytest.raises(StructuredOutputError) as caught:
        await gateway.generate(request(response_schema=schema))

    assert provider.calls == 1
    assert accountant.reserve_calls == accountant.settle_calls == 1
    assert caught.value.request_started is True
    assert caught.value.billing_unknown is False
    assert caught.value.accounting_unknown is False
    assert caught.value.actual_input_tokens == 40
    assert caught.value.actual_output_tokens == 8
    assert await ledger.spent("workspace-a") == Decimal("0.00005600")
    assert telemetry.events[-1].outcome == "structured_output_invalid"


@pytest.mark.asyncio
async def test_post_response_accounting_failure_requires_reconciliation_without_fallback():
    accountant = RecordingUsageAccountant()
    accountant.settle_error = RuntimeError("ledger unavailable")
    provider = FakeProvider(
        ProviderName.OPENAI,
        response=ProviderResponse(
            "ok",
            "test-cheap",
            12,
            3,
            provider_request_id="p-2",
        ),
    )
    gateway, telemetry, _ = make_gateway(
        providers={ProviderName.OPENAI: provider},
        usage_accountant=accountant,
    )

    with pytest.raises(UsageAccountingError) as caught:
        await gateway.generate(request())

    assert provider.calls == 1
    assert accountant.settle_calls == 1
    assert accountant.uncertain_codes == ["usage_accounting_failed"]
    assert caught.value.request_started is True
    assert caught.value.accounting_unknown is True
    assert caught.value.actual_input_tokens == 12
    assert caught.value.actual_output_tokens == 3
    assert telemetry.events[-1].outcome == "usage_accounting_failed"


@pytest.mark.asyncio
async def test_provider_model_must_match_route_even_if_alternative_is_allowed_and_priced():
    accountant = RecordingUsageAccountant()
    provider = FakeProvider(
        ProviderName.OPENAI,
        response=ProviderResponse(
            text="conteúdo que não deve aparecer em erro ou telemetria",
            model="test-other",
            input_tokens=10,
            output_tokens=5,
            provider_request_id="provider-mismatch",
        ),
    )
    routes = (
        '{"version":"routes-test","classes":{"simple":['
        '{"provider":"openai","model":"test-cheap"},'
        '{"provider":"openai","model":"test-other"}]},"agents":{}}'
    )
    prices = (
        '{"version":"prices-test","currency":"USD","models":{'
        '"openai:test-cheap":{"input_per_million_usd":"1",'
        '"output_per_million_usd":"2"},'
        '"openai:test-other":{"input_per_million_usd":"3",'
        '"output_per_million_usd":"4"}}}'
    )
    gateway, telemetry, ledger = make_gateway(
        routes=routes,
        prices=prices,
        providers={ProviderName.OPENAI: provider},
        usage_accountant=accountant,
    )

    with pytest.raises(ProviderCallError) as caught:
        await gateway.generate(request())

    error = caught.value
    assert error.error_code == "provider_model_mismatch"
    assert error.route_key == "openai:test-cheap"
    assert error.provider_request_id == "provider-mismatch"
    assert "test-other" not in str(error)
    assert "conteúdo" not in str(error)
    assert provider.calls == 1
    assert accountant.uncertain_codes == ["provider_model_mismatch"]
    assert await ledger.spent("workspace-a") == Decimal(error.estimated_cost_usd)
    event = telemetry.events[-1]
    assert event.outcome == "provider_error:provider_model_mismatch"
    assert event.model == "test-cheap"
    assert event.route_key == "openai:test-cheap"
    assert event.provider_request_id == "provider-mismatch"
    assert event.cost_usd is None
    assert event.estimated_cost_usd == error.estimated_cost_usd
    assert "conteúdo" not in repr(event)


@pytest.mark.asyncio
async def test_cached_response_with_wrong_model_is_rejected_before_use():
    provider = FakeProvider(
        ProviderName.OPENAI,
        response=ProviderResponse(
            text="provider-safe",
            model="test-cheap",
            input_tokens=10,
            output_tokens=5,
            provider_request_id="provider-safe-request",
        ),
    )
    response_cache = InMemoryResponseCache()
    model_request = request(contains_sensitive_data=False, allow_response_cache=True)
    key = cache_key(
        model_request,
        RouteCandidate(ProviderName.OPENAI, "test-cheap"),
    )
    assert key is not None
    await response_cache.set(
        key,
        CacheEntry(
            response=ProviderResponse(
                text="cached-untrusted",
                model="different-model",
                input_tokens=1,
                output_tokens=1,
                provider_request_id="cached-request",
            ),
            cost_usd=Decimal("0.01"),
            structured_data=None,
        ),
        ttl_seconds=60,
    )
    gateway, telemetry, _ = make_gateway(
        providers={ProviderName.OPENAI: provider},
        response_cache=response_cache,
        response_cache_enabled=True,
    )

    result = await gateway.generate(model_request)

    assert result.text == "provider-safe"
    assert result.model_used == "test-cheap"
    assert provider.calls == 1
    assert [event.outcome for event in telemetry.events] == [
        "cache_rejected:provider_model_mismatch",
        "success",
    ]


@pytest.mark.asyncio
async def test_cost_failure_after_provider_response_is_durably_uncertain(monkeypatch):
    accountant = RecordingUsageAccountant()
    provider = FakeProvider(
        ProviderName.OPENAI,
        response=ProviderResponse(
            "ok",
            "test-cheap",
            15,
            4,
            provider_request_id="provider-cost-failure",
        ),
    )
    gateway, telemetry, _ = make_gateway(
        providers={ProviderName.OPENAI: provider},
        usage_accountant=accountant,
    )
    original_compute_cost = PriceCatalog.compute_cost
    calls = 0

    def fail_second_cost_calculation(self, route, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_compute_cost(self, route, **kwargs)
        raise RuntimeError("price catalog unavailable after response")

    monkeypatch.setattr(PriceCatalog, "compute_cost", fail_second_cost_calculation)

    with pytest.raises(UsageAccountingError):
        await gateway.generate(request(request_id="post-provider-cost-failure"))

    assert provider.calls == 1
    assert accountant.settle_calls == 0
    assert accountant.uncertain_codes == ["post_provider_cost_failed"]
    assert telemetry.events[-1].outcome == "post_provider_cost_failed"


@pytest.mark.asyncio
async def test_cancellation_after_provider_start_waits_for_uncertain_transition():
    provider_started = asyncio.Event()
    accountant = RecordingUsageAccountant()

    class BlockingProvider(FakeProvider):
        async def generate(self, request, route, *, timeout_seconds):
            self.calls += 1
            provider_started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    provider = BlockingProvider(ProviderName.OPENAI)
    gateway, _, _ = make_gateway(
        providers={ProviderName.OPENAI: provider},
        usage_accountant=accountant,
    )
    operation = asyncio.create_task(gateway.generate(request(request_id="cancel-after-start")))
    await provider_started.wait()
    operation.cancel()

    with pytest.raises(ProviderCallError, match="cancelada"):
        await operation

    assert provider.calls == 1
    assert accountant.uncertain_codes == ["provider_cancelled"]


@pytest.mark.asyncio
async def test_gateway_persists_the_same_safe_event_emitted_to_telemetry():
    workspace_id = str(uuid4())
    event_store = InMemoryAiGatewayEventStore()
    provider = FakeProvider(
        ProviderName.OPENAI,
        response=ProviderResponse(
            "ok",
            "test-cheap",
            15,
            4,
            provider_request_id="provider-event-store",
        ),
    )
    gateway, telemetry, _ = make_gateway(
        providers={ProviderName.OPENAI: provider},
        event_store=event_store,
    )

    result = await gateway.generate(
        request(
            workspace_id=workspace_id,
            request_id="persist-safe-event",
        )
    )

    assert result.text == "ok"
    assert len(event_store.events) == 1
    assert event_store.events[0][1] == telemetry.events[0]
    assert event_store.events[0][1].outcome == "success"


@pytest.mark.asyncio
async def test_gateway_sinks_are_independent_and_best_effort():
    workspace_id = str(uuid4())
    event_store = InMemoryAiGatewayEventStore()
    provider = FakeProvider(
        ProviderName.OPENAI,
        response=ProviderResponse(
            "ok",
            "test-cheap",
            12,
            3,
            provider_request_id="provider-independent-sinks",
        ),
    )
    gateway, _, _ = make_gateway(
        providers={ProviderName.OPENAI: provider},
        telemetry=FailingTelemetrySink(),
        event_store=event_store,
    )

    result = await gateway.generate(
        request(
            workspace_id=workspace_id,
            request_id="independent-sinks",
        )
    )

    assert result.text == "ok"
    assert event_store.events[0][1].outcome == "success"

    gateway_with_failed_store, telemetry, _ = make_gateway(
        providers={ProviderName.OPENAI: provider},
        event_store=FailingEventStore(),
    )
    second_result = await gateway_with_failed_store.generate(
        request(
            workspace_id=workspace_id,
            request_id="failed-store-does-not-block",
        )
    )

    assert second_result.text == "ok"
    assert telemetry.events[-1].outcome == "success"
