"""API-first, budget-aware, fail-closed model gateway."""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal

from app.ai.accounting import (
    ModelUsageAccountant,
    ModelUsageReservation,
    NullModelUsageAccountant,
)
from app.ai.base import AICompletionResult, AIMessage, AIProvider
from app.ai.cache import ResponseCache, cache_key
from app.ai.catalog import PriceCatalog, RouteCatalog
from app.ai.circuit import CircuitBreakerProtocol
from app.ai.context import current_ai_context
from app.ai.errors import (
    AIRuntimeError,
    MissingWorkspaceContextError,
    NoRouteAvailableError,
    ProviderCallError,
    ProviderNotConfiguredError,
    StructuredOutputError,
    UsageAccountingError,
    UsageAccountingUnavailableError,
)
from app.ai.event_store import AiGatewayEventStore
from app.ai.limits import DailyBudgetLedger
from app.ai.models import (
    BudgetReservation,
    CacheEntry,
    CostEstimate,
    ModelAttribution,
    ModelMediaPart,
    ModelRequest,
    PromptCachePolicy,
    ProviderName,
    ProviderResponse,
    RouteCandidate,
    TaskClass,
)
from app.ai.policy import ModelAPIGate, RequestPolicy
from app.ai.providers.base import ModelProviderAdapter
from app.ai.routing import ModelRouter
from app.ai.security import require_allowed_model
from app.ai.structured_output import parse_and_validate, validate_response_schema
from app.ai.telemetry import (
    ModelTelemetryEvent,
    TelemetrySink,
    safe_provider_request_id,
    scoped_ref,
    source_refs_digest,
    workspace_ref,
)
from app.ai.workspace_credentials import WorkspaceCredentialResolver
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type

_SAFE_PROVIDER_ERROR_CODES = frozenset(
    {
        "provider_model_mismatch",
        "provider_model_unapproved",
        "response_incomplete",
        "response_not_completed",
    }
)

BudgetReservationHook = Callable[
    [BudgetReservation, CostEstimate, RouteCandidate, ModelRequest],
    Awaitable[None],
]


def estimate_input_tokens(request: ModelRequest) -> int:
    """Conservative local preflight; authoritative counts come from provider usage."""
    characters = sum(len(message.content) for message in request.messages)
    if request.response_schema is not None:
        characters += len(json.dumps(request.response_schema, ensure_ascii=False, sort_keys=True))
    media_tokens = sum(part.estimated_input_tokens for part in request.media_parts)
    return max(1, math.ceil(characters / 3) + 24 * len(request.messages) + media_tokens)


class ModelGateway:
    def __init__(
        self,
        *,
        gate: ModelAPIGate,
        route_catalog: RouteCatalog,
        price_catalog: PriceCatalog,
        providers: dict[ProviderName, ModelProviderAdapter],
        model_allowlist: frozenset[str],
        request_policy: RequestPolicy,
        budget_ledger: DailyBudgetLedger,
        circuit_breaker: CircuitBreakerProtocol,
        telemetry: TelemetrySink,
        response_cache: ResponseCache,
        default_timeout_seconds: float,
        max_retries: int,
        retry_base_seconds: float,
        response_cache_enabled: bool,
        response_cache_ttl_seconds: int,
        usage_accountant: ModelUsageAccountant | None = None,
        workspace_credential_resolver: WorkspaceCredentialResolver | None = None,
        prefer_maritaca_for_simple: bool = False,
        event_store: AiGatewayEventStore | None = None,
    ) -> None:
        self.gate = gate
        self.route_catalog = route_catalog
        self.price_catalog = price_catalog
        self.router = ModelRouter(
            route_catalog,
            prefer_maritaca_for_simple=prefer_maritaca_for_simple,
        )
        self.providers = providers
        self.model_allowlist = model_allowlist
        self.request_policy = request_policy
        self.budget_ledger = budget_ledger
        self.circuit_breaker = circuit_breaker
        self.telemetry = telemetry
        self.response_cache = response_cache
        self.default_timeout_seconds = default_timeout_seconds
        self.max_retries = max_retries
        self.retry_base_seconds = retry_base_seconds
        self.response_cache_enabled = response_cache_enabled
        self.response_cache_ttl_seconds = response_cache_ttl_seconds
        self.usage_accountant = usage_accountant or NullModelUsageAccountant()
        # Ponto de resolução BYOK: quando presente, a credencial do workspace
        # ativo substitui o adapter da plataforma por chamada (com cache por
        # workspace+provider); ausente, o singleton da plataforma segue igual.
        self.workspace_credential_resolver = workspace_credential_resolver
        self.event_store = event_store

    def _estimate(self, request: ModelRequest, route, input_tokens: int) -> CostEstimate:
        amount = self.price_catalog.compute_cost(
            route,
            input_tokens=input_tokens,
            output_tokens=request.max_output_tokens,
        )
        return CostEstimate(
            amount_usd=amount,
            input_tokens=input_tokens,
            output_tokens=request.max_output_tokens,
            catalog_version=self.price_catalog.version,
        )

    def _validate_provider_response(
        self,
        route: RouteCandidate,
        response: ProviderResponse,
    ) -> None:
        provider_request_id = safe_provider_request_id(response.provider_request_id)
        if not isinstance(response.model, str) or response.model != route.model:
            error = ProviderCallError(
                "Provider retornou um modelo diferente da rota autorizada.",
                request_started=True,
                billing_unknown=True,
                provider_request_id=provider_request_id,
            )
            error.error_code = "provider_model_mismatch"
            raise error

        returned_route = RouteCandidate(provider=route.provider, model=response.model)
        try:
            require_allowed_model(returned_route, self.model_allowlist)
            self.price_catalog.require_price(returned_route)
        except AIRuntimeError as exc:
            error = ProviderCallError(
                "Provider retornou um modelo sem autorização e preço vigentes.",
                request_started=True,
                billing_unknown=True,
                provider_request_id=provider_request_id,
            )
            error.error_code = "provider_model_unapproved"
            raise error from exc

    def _enrich_provider_error(
        self,
        error: ProviderCallError,
        *,
        request: ModelRequest,
        route: RouteCandidate,
        estimate: CostEstimate,
        reservation: BudgetReservation | None = None,
        usage_reservation: ModelUsageReservation | None = None,
    ) -> ProviderCallError:
        error.provider_request_id = safe_provider_request_id(error.provider_request_id)
        error.gateway_request_id = request.request_id
        error.provider = route.provider.value
        error.model = route.model
        error.route_key = route.key
        error.estimated_cost_usd = str(estimate.amount_usd)
        error.estimated_input_tokens = estimate.input_tokens
        error.estimated_output_tokens = estimate.output_tokens
        error.price_catalog_version = self.price_catalog.version
        error.route_catalog_version = self.route_catalog.version
        if reservation is not None:
            error.budget_reservation_id = reservation.reservation_id
            error.budget_reservation_day = reservation.day
            error.budget_reserved_amount_usd = str(reservation.amount_usd)
        if usage_reservation is not None:
            error.usage_reservation_id = str(usage_reservation.entitlement_reservation_id or "")
            error.usage_reserved_tokens = usage_reservation.reserved_tokens
        return error

    @staticmethod
    def _accounting_error(
        *,
        request: ModelRequest,
        route: RouteCandidate,
        estimate: CostEstimate,
        budget_reservation: BudgetReservation,
        usage_reservation: ModelUsageReservation | None,
        response: ProviderResponse,
    ) -> UsageAccountingError:
        error = UsageAccountingError(
            "A resposta do modelo foi recebida, mas a contabilidade de tokens exige conciliação."
        )
        error.gateway_request_id = request.request_id
        error.provider = route.provider.value
        error.model = route.model
        error.route_key = route.key
        error.provider_request_id = safe_provider_request_id(response.provider_request_id)
        error.estimated_cost_usd = str(estimate.amount_usd)
        error.estimated_input_tokens = estimate.input_tokens
        error.estimated_output_tokens = estimate.output_tokens
        error.actual_input_tokens = response.input_tokens
        error.actual_output_tokens = response.output_tokens
        error.price_catalog_version = estimate.catalog_version
        error.budget_reservation_id = budget_reservation.reservation_id
        error.budget_reservation_day = budget_reservation.day
        error.budget_reserved_amount_usd = str(budget_reservation.amount_usd)
        if usage_reservation is not None:
            error.usage_reservation_id = str(usage_reservation.entitlement_reservation_id or "")
            error.usage_reserved_tokens = usage_reservation.reserved_tokens
        return error

    @staticmethod
    def _provider_error_code(error: ProviderCallError) -> str:
        code = getattr(error, "error_code", "")
        if code in _SAFE_PROVIDER_ERROR_CODES:
            return code
        return str(error.status_code or "transport")

    async def _emit(
        self,
        request: ModelRequest,
        *,
        provider: str,
        model: str,
        outcome: str,
        started_at: float,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_input_tokens: int = 0,
        cost_usd: Decimal | None = None,
        estimate: CostEstimate | None = None,
        route_key: str = "",
        provider_request_id: str = "",
        request_started: bool = False,
        billing_unknown: bool = False,
        fallback_used: bool = False,
        cache_hit: bool = False,
    ) -> None:
        attribution = request.attribution
        matter_ref = (
            scoped_ref(request.workspace_id, attribution.matter_id, namespace="matter")
            if attribution.matter_id
            else ""
        )
        event = ModelTelemetryEvent(
            request_id=request.request_id,
            workspace_ref=workspace_ref(request.workspace_id),
            agent_name=request.agent_name,
            task_class=request.task_class.value,
            provider=provider,
            model=model,
            outcome=outcome,
            latency_ms=max(0, int((time.monotonic() - started_at) * 1000)),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            cost_usd=str(cost_usd) if cost_usd is not None else None,
            estimated_cost_usd=(str(estimate.amount_usd) if estimate is not None else None),
            estimated_input_tokens=(estimate.input_tokens if estimate is not None else 0),
            estimated_output_tokens=(estimate.output_tokens if estimate is not None else 0),
            route_key=route_key,
            provider_request_id=safe_provider_request_id(provider_request_id),
            request_started=request_started,
            billing_unknown=billing_unknown,
            price_catalog_version=self.price_catalog.version,
            route_catalog_version=self.route_catalog.version,
            fallback_used=fallback_used,
            cache_hit=cache_hit,
            operation=attribution.operation,
            scope_type="matter" if attribution.matter_id else "workspace",
            matter_ref=matter_ref,
            context_digest=attribution.context_digest,
            playbook_ref=(
                scoped_ref(
                    request.workspace_id,
                    attribution.playbook_version,
                    namespace="playbook",
                )
                if attribution.playbook_version
                else ""
            ),
            source_refs_digest=source_refs_digest(
                request.workspace_id,
                attribution.source_refs,
            ),
            source_count=len(attribution.source_refs),
            attribution_complete=bool(
                request.workspace_id
                and request.agent_name
                and attribution.operation
                and (not attribution.matter_id or matter_ref)
            ),
            prompt_cache_policy=request.prompt_cache_policy.value,
        )
        try:
            await self.telemetry.emit(event)
        except Exception as error:
            logger.warning(
                "model_gateway.telemetry_best_effort_failed",
                error_type=safe_error_type(error),
                outcome=outcome,
                request_id=request.request_id,
            )
        if self.event_store is None:
            return
        try:
            await self.event_store.append(
                workspace_id=request.workspace_id,
                event=event,
            )
        except Exception as error:
            logger.warning(
                "model_gateway.event_store_best_effort_failed",
                error_type=safe_error_type(error),
                outcome=outcome,
                request_id=request.request_id,
            )

    @staticmethod
    async def _best_effort(operation: str, awaitable: Awaitable[object]) -> None:
        try:
            await awaitable
        except Exception as error:
            logger.warning(
                "model_gateway.post_provider_best_effort_failed",
                operation=operation,
                error_type=safe_error_type(error),
            )

    async def _mark_uncertain_durably(
        self,
        *,
        request: ModelRequest,
        reservation: ModelUsageReservation,
        route: RouteCandidate,
        error_code: str,
        provider_request_id: str = "",
    ) -> None:
        """Finish the SQL transition even when the caller is being cancelled."""

        task = asyncio.create_task(
            self.usage_accountant.mark_uncertain(
                reservation=reservation,
                route=route,
                error_code=error_code,
                provider_request_id=provider_request_id,
            )
        )
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            except BaseException as error:
                raise self._uncertain_persistence_error(
                    request=request,
                    route=route,
                    error_code=error_code,
                ) from error
            raise
        except BaseException as error:
            raise self._uncertain_persistence_error(
                request=request,
                route=route,
                error_code=error_code,
            ) from error

    @staticmethod
    def _uncertain_persistence_error(
        *,
        request: ModelRequest,
        route: RouteCandidate,
        error_code: str,
    ) -> UsageAccountingError:
        error = UsageAccountingError(
            "Não foi possível persistir o estado incerto de uma chamada iniciada."
        )
        error.gateway_request_id = request.request_id
        error.provider = route.provider.value
        error.model = route.model
        error.route_key = route.key
        error.error_code = error_code
        logger.error(
            "model_gateway.uncertain_persistence_failed",
            request_id=request.request_id,
            provider=route.provider.value,
            model=route.model,
            error_code=error_code,
        )
        return error

    async def generate(
        self,
        request: ModelRequest,
        *,
        on_reservation: BudgetReservationHook | None = None,
    ) -> AICompletionResult:
        started_at = time.monotonic()
        self.gate.require_open()
        if not self.gate.local_fake_enabled and not self.usage_accountant.active:
            raise UsageAccountingUnavailableError(
                "Chamadas externas exigem claim e contabilidade SQL ativos."
            )
        await self._best_effort("telemetry-readiness", self.telemetry.require_ready())
        estimated_input_tokens = estimate_input_tokens(request)
        self.request_policy.validate_request(request, estimated_input_tokens)
        if request.response_schema is not None:
            validate_response_schema(
                request.response_schema,
                contains_sensitive_data=request.schema_contains_sensitive_data,
            )

        candidates = self.router.candidates(request)
        preflight_errors: list[AIRuntimeError] = []
        usage_reservation: ModelUsageReservation | None = None
        for index, route in enumerate(candidates):
            fallback_used = index > 0
            route_started_at = time.monotonic()
            half_open_probe = False
            estimate: CostEstimate | None = None
            try:
                require_allowed_model(route, self.model_allowlist)
                provider = self.providers.get(route.provider)
                if provider is None:
                    raise ProviderNotConfiguredError(
                        f"Adapter não configurado para {route.provider.value}."
                    )
                if self.workspace_credential_resolver is not None:
                    workspace_provider = await self.workspace_credential_resolver.resolve_adapter(
                        request.workspace_id, route.provider
                    )
                    if workspace_provider is not None:
                        provider = workspace_provider
                provider.require_supported(request)
                provider.require_configured()
                half_open_probe = await self.circuit_breaker.acquire(route.key)
                estimate = self._estimate(request, route, estimated_input_tokens)
                self.request_policy.require_estimate_within_call_cap(request, estimate)
            except AIRuntimeError as exc:
                if half_open_probe:
                    await self._best_effort(
                        "circuit-release-preflight",
                        self.circuit_breaker.release(route.key),
                    )
                preflight_errors.append(exc)
                await self._emit(
                    request,
                    provider=route.provider.value,
                    model=route.model,
                    outcome=f"preflight_blocked:{type(exc).__name__}",
                    started_at=route_started_at,
                    estimate=estimate,
                    route_key=route.key,
                    fallback_used=fallback_used,
                )
                continue

            assert estimate is not None

            key = cache_key(request, route) if self.response_cache_enabled else None
            if key is not None:
                try:
                    cached = await self.response_cache.get(key)
                except Exception as cache_error:
                    logger.warning(
                        "model_gateway.response_cache_get_failed",
                        error_type=safe_error_type(cache_error),
                        request_id=request.request_id,
                    )
                    cached = None
                if cached is not None:
                    try:
                        self._validate_provider_response(route, cached.response)
                    except ProviderCallError as error:
                        await self._emit(
                            request,
                            provider=route.provider.value,
                            model=route.model,
                            outcome=f"cache_rejected:{self._provider_error_code(error)}",
                            started_at=route_started_at,
                            estimate=estimate,
                            route_key=route.key,
                            provider_request_id=error.provider_request_id,
                            fallback_used=fallback_used,
                        )
                        cached = None
                if cached is not None:
                    if half_open_probe:
                        await self._best_effort(
                            "circuit-release-cache-hit",
                            self.circuit_breaker.release(route.key),
                        )
                    if usage_reservation is not None:
                        await self.usage_accountant.release(usage_reservation)
                        usage_reservation = None
                    provider_request_id = safe_provider_request_id(
                        cached.response.provider_request_id
                    )
                    await self._emit(
                        request,
                        provider=route.provider.value,
                        model=route.model,
                        outcome="cache_hit",
                        started_at=route_started_at,
                        cost_usd=Decimal("0"),
                        estimate=estimate,
                        route_key=route.key,
                        provider_request_id=provider_request_id,
                        fallback_used=fallback_used,
                        cache_hit=True,
                    )
                    return AICompletionResult(
                        text=cached.response.text,
                        cost_usd=0.0,
                        cost_known=True,
                        model_used=route.model,
                        provider=route.provider.value,
                        request_id=request.request_id,
                        provider_request_id=provider_request_id,
                        price_catalog_version=self.price_catalog.version,
                        route_catalog_version=self.route_catalog.version,
                        fallback_used=fallback_used,
                        cache_hit=True,
                        usage_accounted=True,
                        structured_data=cached.structured_data,
                    )

            try:
                reservation = await self.budget_ledger.reserve(
                    request.workspace_id,
                    estimate.amount_usd,
                    self.request_policy.daily_budget(request.workspace_id),
                )
            except AIRuntimeError as exc:
                if half_open_probe:
                    await self._best_effort(
                        "circuit-release-budget-blocked",
                        self.circuit_breaker.release(route.key),
                    )
                preflight_errors.append(exc)
                await self._emit(
                    request,
                    provider=route.provider.value,
                    model=route.model,
                    outcome=f"budget_blocked:{type(exc).__name__}",
                    started_at=route_started_at,
                    estimate=estimate,
                    route_key=route.key,
                    fallback_used=fallback_used,
                )
                continue

            if usage_reservation is None:
                try:
                    usage_reservation = await self.usage_accountant.reserve(
                        request=request,
                        estimate=estimate,
                        route=route,
                    )
                except BaseException:
                    await self._best_effort(
                        "daily-budget-release-after-claim-failure",
                        self.budget_ledger.release(reservation),
                    )
                    if half_open_probe:
                        await self._best_effort(
                            "circuit-release-after-claim-failure",
                            self.circuit_breaker.release(route.key),
                        )
                    raise

            if on_reservation is not None:
                try:
                    await on_reservation(reservation, estimate, route, request)
                except BaseException:
                    await self._best_effort(
                        "daily-budget-release-after-hook-failure",
                        self.budget_ledger.release(reservation),
                    )
                    if usage_reservation is not None:
                        await self.usage_accountant.release(usage_reservation)
                        usage_reservation = None
                    if half_open_probe:
                        await self._best_effort(
                            "circuit-release-after-hook-failure",
                            self.circuit_breaker.release(route.key),
                        )
                    raise

            try:
                if usage_reservation is not None:
                    await self.usage_accountant.mark_started(
                        reservation=usage_reservation,
                        estimate=estimate,
                        route=route,
                    )
            except BaseException:
                await self._best_effort(
                    "daily-budget-release-after-start-persistence-failure",
                    self.budget_ledger.release(reservation),
                )
                if usage_reservation is not None:
                    try:
                        await self.usage_accountant.release(usage_reservation)
                        usage_reservation = None
                    except Exception as release_error:
                        logger.warning(
                            "model_gateway.claim_release_failed",
                            error_type=safe_error_type(release_error),
                            request_id=request.request_id,
                        )
                if half_open_probe:
                    await self._best_effort(
                        "circuit-release-after-start-persistence-failure",
                        self.circuit_breaker.release(route.key),
                    )
                raise

            response: ProviderResponse | None = None
            attempt = 0
            timeout_seconds = route.timeout_seconds or self.default_timeout_seconds
            while True:
                try:
                    response = await asyncio.wait_for(
                        provider.generate(
                            request,
                            route,
                            timeout_seconds=timeout_seconds,
                        ),
                        timeout=timeout_seconds + 0.25,
                    )
                    self._validate_provider_response(route, response)
                    break
                except TimeoutError as exc:
                    error = self._enrich_provider_error(
                        ProviderCallError(
                            f"Timeout do provider {route.provider.value}.",
                            retryable=True,
                            request_started=True,
                            billing_unknown=True,
                        ),
                        request=request,
                        route=route,
                        estimate=estimate,
                        reservation=reservation,
                        usage_reservation=usage_reservation,
                    )
                    if usage_reservation is not None:
                        await self._mark_uncertain_durably(
                            request=request,
                            reservation=usage_reservation,
                            route=route,
                            error_code="provider_timeout",
                            provider_request_id=error.provider_request_id,
                        )
                    await self._best_effort(
                        "circuit-failure-timeout",
                        self.circuit_breaker.failure(route.key),
                    )
                    if not self._can_retry(request, provider, error, attempt):
                        await self._emit(
                            request,
                            provider=route.provider.value,
                            model=route.model,
                            outcome="provider_timeout",
                            started_at=route_started_at,
                            estimate=estimate,
                            route_key=route.key,
                            provider_request_id=error.provider_request_id,
                            request_started=error.request_started,
                            billing_unknown=error.billing_unknown,
                            fallback_used=fallback_used,
                        )
                        raise error from exc
                except ProviderCallError as error:
                    error = self._enrich_provider_error(
                        error,
                        request=request,
                        route=route,
                        estimate=estimate,
                        reservation=reservation,
                        usage_reservation=usage_reservation,
                    )
                    if usage_reservation is not None:
                        if error.request_started:
                            await self._mark_uncertain_durably(
                                request=request,
                                reservation=usage_reservation,
                                route=route,
                                error_code=self._provider_error_code(error),
                                provider_request_id=error.provider_request_id,
                            )
                        else:
                            await self.usage_accountant.mark_not_started(
                                reservation=usage_reservation,
                                route=route,
                                error_code=self._provider_error_code(error),
                            )
                    await self._best_effort(
                        "circuit-failure-provider-error",
                        self.circuit_breaker.failure(route.key),
                    )
                    can_retry = self._can_retry(request, provider, error, attempt)
                    if not can_retry:
                        if not error.request_started:
                            await self._best_effort(
                                "daily-budget-release-not-started",
                                self.budget_ledger.release(reservation),
                            )
                        await self._emit(
                            request,
                            provider=route.provider.value,
                            model=route.model,
                            outcome=f"provider_error:{self._provider_error_code(error)}",
                            started_at=route_started_at,
                            cost_usd=None if error.billing_unknown else Decimal("0"),
                            estimate=estimate,
                            route_key=route.key,
                            provider_request_id=error.provider_request_id,
                            request_started=error.request_started,
                            billing_unknown=error.billing_unknown,
                            fallback_used=fallback_used,
                        )
                        if not error.request_started:
                            preflight_errors.append(error)
                            break
                        raise
                except asyncio.CancelledError:
                    error = self._enrich_provider_error(
                        ProviderCallError(
                            f"Chamada ao provider {route.provider.value} foi cancelada.",
                            request_started=True,
                            billing_unknown=True,
                        ),
                        request=request,
                        route=route,
                        estimate=estimate,
                        reservation=reservation,
                        usage_reservation=usage_reservation,
                    )
                    if usage_reservation is not None:
                        await self._mark_uncertain_durably(
                            request=request,
                            reservation=usage_reservation,
                            route=route,
                            error_code="provider_cancelled",
                        )
                    await self._best_effort(
                        "circuit-failure-cancelled",
                        self.circuit_breaker.failure(route.key),
                    )
                    await self._emit(
                        request,
                        provider=route.provider.value,
                        model=route.model,
                        outcome="provider_cancelled",
                        started_at=route_started_at,
                        estimate=estimate,
                        route_key=route.key,
                        request_started=True,
                        billing_unknown=True,
                        fallback_used=fallback_used,
                    )
                    raise error
                except Exception as exc:
                    error = self._enrich_provider_error(
                        ProviderCallError(
                            f"Falha inesperada no provider {route.provider.value}.",
                            request_started=True,
                            billing_unknown=True,
                        ),
                        request=request,
                        route=route,
                        estimate=estimate,
                        reservation=reservation,
                        usage_reservation=usage_reservation,
                    )
                    if usage_reservation is not None:
                        await self._mark_uncertain_durably(
                            request=request,
                            reservation=usage_reservation,
                            route=route,
                            error_code="provider_unexpected",
                        )
                    await self._best_effort(
                        "circuit-failure-unexpected",
                        self.circuit_breaker.failure(route.key),
                    )
                    await self._emit(
                        request,
                        provider=route.provider.value,
                        model=route.model,
                        outcome="provider_error:unexpected",
                        started_at=route_started_at,
                        estimate=estimate,
                        route_key=route.key,
                        request_started=True,
                        billing_unknown=True,
                        fallback_used=fallback_used,
                    )
                    raise error from exc
                attempt += 1
                await asyncio.sleep(self.retry_base_seconds * (2 ** (attempt - 1)))
                if usage_reservation is not None:
                    await self.usage_accountant.mark_started(
                        reservation=usage_reservation,
                        route=route,
                        estimate=estimate,
                    )

            if response is None:
                continue

            provider_request_id = safe_provider_request_id(response.provider_request_id)
            try:
                actual_cost = self.price_catalog.compute_cost(
                    route,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cached_input_tokens=response.cached_input_tokens,
                )
            except BaseException as cost_error:
                if usage_reservation is not None:
                    await self._mark_uncertain_durably(
                        request=request,
                        reservation=usage_reservation,
                        route=route,
                        error_code="post_provider_cost_failed",
                        provider_request_id=provider_request_id,
                    )
                await self._emit(
                    request,
                    provider=route.provider.value,
                    model=route.model,
                    outcome="post_provider_cost_failed",
                    started_at=route_started_at,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cached_input_tokens=response.cached_input_tokens,
                    estimate=estimate,
                    route_key=route.key,
                    provider_request_id=provider_request_id,
                    request_started=True,
                    billing_unknown=True,
                    fallback_used=fallback_used,
                )
                raise self._accounting_error(
                    request=request,
                    route=route,
                    estimate=estimate,
                    budget_reservation=reservation,
                    usage_reservation=usage_reservation,
                    response=response,
                ) from cost_error
            structured_data = None
            try:
                if request.response_schema is not None:
                    structured_data = parse_and_validate(response.text, request.response_schema)
            except StructuredOutputError as error:
                if usage_reservation is not None:
                    try:
                        await self.usage_accountant.settle(
                            reservation=usage_reservation,
                            request=request,
                            route=route,
                            response=response,
                            actual_cost_usd=actual_cost,
                            price_catalog_version=self.price_catalog.version,
                            route_catalog_version=self.route_catalog.version,
                        )
                    except BaseException as accounting_error:
                        await self._mark_uncertain_durably(
                            request=request,
                            reservation=usage_reservation,
                            route=route,
                            error_code="usage_accounting_failed",
                            provider_request_id=provider_request_id,
                        )
                        await self._emit(
                            request,
                            provider=route.provider.value,
                            model=route.model,
                            outcome="usage_accounting_failed",
                            started_at=route_started_at,
                            input_tokens=response.input_tokens,
                            output_tokens=response.output_tokens,
                            cached_input_tokens=response.cached_input_tokens,
                            cost_usd=actual_cost,
                            estimate=estimate,
                            route_key=route.key,
                            provider_request_id=provider_request_id,
                            request_started=True,
                            fallback_used=fallback_used,
                        )
                        raise self._accounting_error(
                            request=request,
                            route=route,
                            estimate=estimate,
                            budget_reservation=reservation,
                            usage_reservation=usage_reservation,
                            response=response,
                        ) from accounting_error
                await self._best_effort(
                    "daily-budget-settle-structured-error",
                    self.budget_ledger.settle(reservation, actual_cost),
                )
                await self._best_effort(
                    "circuit-success-structured-error",
                    self.circuit_breaker.success(route.key),
                )
                error.actual_input_tokens = response.input_tokens
                error.actual_output_tokens = response.output_tokens
                error.usage_accounted = usage_reservation is not None
                error.request_started = True
                error.billing_unknown = False
                error.accounting_unknown = False
                error.gateway_request_id = request.request_id
                error.provider_request_id = provider_request_id
                error.provider = route.provider.value
                error.model = route.model
                error.route_key = route.key
                error.estimated_cost_usd = str(estimate.amount_usd)
                error.estimated_input_tokens = estimate.input_tokens
                error.estimated_output_tokens = estimate.output_tokens
                error.price_catalog_version = self.price_catalog.version
                error.route_catalog_version = self.route_catalog.version
                error.budget_reservation_id = reservation.reservation_id
                error.budget_reservation_day = reservation.day
                error.budget_reserved_amount_usd = str(reservation.amount_usd)
                if usage_reservation is not None:
                    error.usage_reservation_id = str(
                        usage_reservation.entitlement_reservation_id or ""
                    )
                    error.usage_reserved_tokens = usage_reservation.reserved_tokens
                await self._emit(
                    request,
                    provider=route.provider.value,
                    model=route.model,
                    outcome="structured_output_invalid",
                    started_at=route_started_at,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cached_input_tokens=response.cached_input_tokens,
                    cost_usd=actual_cost,
                    estimate=estimate,
                    route_key=route.key,
                    provider_request_id=provider_request_id,
                    request_started=True,
                    fallback_used=fallback_used,
                )
                raise
            except BaseException as post_provider_error:
                if usage_reservation is not None:
                    await self._mark_uncertain_durably(
                        request=request,
                        reservation=usage_reservation,
                        route=route,
                        error_code="post_provider_processing_failed",
                        provider_request_id=provider_request_id,
                    )
                await self._emit(
                    request,
                    provider=route.provider.value,
                    model=route.model,
                    outcome="post_provider_processing_failed",
                    started_at=route_started_at,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cached_input_tokens=response.cached_input_tokens,
                    cost_usd=actual_cost,
                    estimate=estimate,
                    route_key=route.key,
                    provider_request_id=provider_request_id,
                    request_started=True,
                    billing_unknown=True,
                    fallback_used=fallback_used,
                )
                raise self._accounting_error(
                    request=request,
                    route=route,
                    estimate=estimate,
                    budget_reservation=reservation,
                    usage_reservation=usage_reservation,
                    response=response,
                ) from post_provider_error

            if usage_reservation is not None:
                try:
                    await self.usage_accountant.settle(
                        reservation=usage_reservation,
                        request=request,
                        route=route,
                        response=response,
                        actual_cost_usd=actual_cost,
                        price_catalog_version=self.price_catalog.version,
                        route_catalog_version=self.route_catalog.version,
                    )
                except BaseException as accounting_error:
                    await self._mark_uncertain_durably(
                        request=request,
                        reservation=usage_reservation,
                        route=route,
                        error_code="usage_accounting_failed",
                        provider_request_id=provider_request_id,
                    )
                    await self._emit(
                        request,
                        provider=route.provider.value,
                        model=route.model,
                        outcome="usage_accounting_failed",
                        started_at=route_started_at,
                        input_tokens=response.input_tokens,
                        output_tokens=response.output_tokens,
                        cached_input_tokens=response.cached_input_tokens,
                        cost_usd=actual_cost,
                        estimate=estimate,
                        route_key=route.key,
                        provider_request_id=provider_request_id,
                        request_started=True,
                        fallback_used=fallback_used,
                    )
                    raise self._accounting_error(
                        request=request,
                        route=route,
                        estimate=estimate,
                        budget_reservation=reservation,
                        usage_reservation=usage_reservation,
                        response=response,
                    ) from accounting_error
            await self._best_effort(
                "daily-budget-settle-success",
                self.budget_ledger.settle(reservation, actual_cost),
            )
            await self._best_effort(
                "circuit-success",
                self.circuit_breaker.success(route.key),
            )
            if key is not None:
                await self._best_effort(
                    "response-cache-set",
                    self.response_cache.set(
                        key,
                        CacheEntry(
                            response=response,
                            cost_usd=actual_cost,
                            structured_data=structured_data,
                        ),
                        self.response_cache_ttl_seconds,
                    ),
                )
            await self._emit(
                request,
                provider=route.provider.value,
                model=route.model,
                outcome="success",
                started_at=route_started_at,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cached_input_tokens=response.cached_input_tokens,
                cost_usd=actual_cost,
                estimate=estimate,
                route_key=route.key,
                provider_request_id=provider_request_id,
                request_started=True,
                fallback_used=fallback_used,
            )
            return AICompletionResult(
                text=response.text,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cached_input_tokens=response.cached_input_tokens,
                cost_usd=float(actual_cost),
                cost_known=True,
                model_used=route.model,
                provider=route.provider.value,
                request_id=request.request_id,
                provider_request_id=provider_request_id,
                price_catalog_version=self.price_catalog.version,
                route_catalog_version=self.route_catalog.version,
                fallback_used=fallback_used,
                usage_accounted=usage_reservation is not None,
                structured_data=structured_data,
            )

        if usage_reservation is not None:
            await self.usage_accountant.release(usage_reservation)
        last = type(preflight_errors[-1]).__name__ if preflight_errors else "sem candidatos"
        await self._emit(
            request,
            provider="none",
            model="none",
            outcome=f"no_route:{last}",
            started_at=started_at,
        )
        raise NoRouteAvailableError(
            f"Nenhuma rota de modelo passou no preflight. Último bloqueio: {last}."
        )

    async def generate_batch(
        self,
        requests: list[ModelRequest],
        *,
        max_concurrency: int = 4,
    ) -> list[AICompletionResult]:
        """Execute a bounded batch while allowing every started call to reconcile."""
        if not requests:
            return []
        if len(requests) > 500:
            raise ValueError("A model batch cannot contain more than 500 requests.")
        if max_concurrency <= 0 or max_concurrency > 32:
            raise ValueError("max_concurrency must be between 1 and 32.")

        semaphore = asyncio.Semaphore(max_concurrency)

        async def bounded(request: ModelRequest) -> AICompletionResult:
            async with semaphore:
                return await self.generate(request)

        # Do not let one exception cancel requests that may already have reached a
        # billable provider boundary. All reservations resolve before we surface
        # the first failure to the caller.
        outcomes = await asyncio.gather(
            *(bounded(request) for request in requests),
            return_exceptions=True,
        )
        first_error = next(
            (outcome for outcome in outcomes if isinstance(outcome, BaseException)),
            None,
        )
        if first_error is not None:
            raise first_error
        return [outcome for outcome in outcomes if isinstance(outcome, AICompletionResult)]

    def _can_retry(
        self,
        request: ModelRequest,
        provider: ModelProviderAdapter,
        error: ProviderCallError,
        attempt: int,
    ) -> bool:
        return bool(
            error.retryable
            and request.idempotent
            and request.idempotency_key
            and provider.supports_idempotent_retry
            and attempt < self.max_retries
        )


class GatewayAIProvider(AIProvider):
    """Compatibility adapter that requires an explicit workspace context."""

    def __init__(
        self,
        gateway: ModelGateway,
        task_class: TaskClass,
        *,
        default_max_output_tokens: int,
    ) -> None:
        self.gateway = gateway
        self.task_class = task_class
        self.default_max_output_tokens = default_max_output_tokens

    async def complete(
        self,
        messages: list[AIMessage],
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_schema: dict | None = None,
        model_override: str | None = None,
        *,
        workspace_id: str | None = None,
        agent_name: str | None = None,
        idempotent: bool = False,
        idempotency_key: str | None = None,
        contains_sensitive_data: bool = True,
        allow_response_cache: bool = False,
        schema_contains_sensitive_data: bool = False,
        max_cost_usd: Decimal | None = None,
        media_parts: tuple[ModelMediaPart, ...] = (),
        attribution: ModelAttribution | None = None,
        prompt_cache_policy: PromptCachePolicy | None = None,
        prompt_cache_key: str | None = None,
    ) -> AICompletionResult:
        context = current_ai_context()
        effective_workspace = workspace_id or (context.workspace_id if context else "")
        effective_agent = agent_name or (context.agent_name if context else "")
        effective_attribution = attribution or (
            context.attribution if context else ModelAttribution(operation=effective_agent)
        )
        if not effective_workspace or not effective_agent:
            raise MissingWorkspaceContextError(
                "Use workspace_id/agent_name ou use_ai_context antes de chamar o gateway."
            )
        return await self.gateway.generate(
            ModelRequest(
                messages=messages,
                workspace_id=effective_workspace,
                agent_name=effective_agent,
                task_class=self.task_class,
                temperature=temperature,
                max_output_tokens=max_tokens or self.default_max_output_tokens,
                response_schema=response_schema,
                requested_model=model_override,
                idempotent=idempotent,
                idempotency_key=idempotency_key,
                contains_sensitive_data=contains_sensitive_data,
                schema_contains_sensitive_data=schema_contains_sensitive_data,
                allow_response_cache=allow_response_cache,
                max_cost_usd=max_cost_usd,
                media_parts=media_parts,
                attribution=effective_attribution,
                prompt_cache_policy=prompt_cache_policy or PromptCachePolicy.OFF,
                prompt_cache_key=prompt_cache_key,
            )
        )
