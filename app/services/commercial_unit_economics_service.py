"""Fail-closed publication gate for paid AI capacity.

Provider-confirmed receipts prove that every route currently selectable by the
runtime has actually been exercised. The active, versioned internal rate card
then prices input, output and cached input independently. Neither configured
credentials nor a blended historic ``cost_usd`` field are treated as proof of
the maximum cost of an offer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.billing import PlatformModelUsageLedger
from app.models.entitlement import EntitlementKey
from app.schemas.commercial_catalog import (
    CommercialOfferSnapshotWrite,
    CommercialUnitEconomicsBlockerRead,
    CommercialUnitEconomicsGateRead,
)

UNIT_ECONOMICS_ASSESSMENT_VERSION = "ai-active-route-rate-card-v3"
UNIT_ECONOMICS_WINDOW_DAYS = 90
UNIT_ECONOMICS_MIN_CONFIRMED_CALLS = 30
UNIT_ECONOMICS_MIN_DISTINCT_DAYS = 7
UNIT_ECONOMICS_MIN_OBSERVED_TOKENS = 50_000
UNIT_ECONOMICS_MAX_AI_COST_SHARE_BPS = 3_500

# These are explicit internal planning assumptions. They are intentionally not
# described as a current FX quote, provider invoice or market willingness to pay.
UNIT_ECONOMICS_USD_BRL_REFERENCE = Decimal("5.50")
UNIT_ECONOMICS_COST_SAFETY_FACTOR = Decimal("1.25")

_TOKEN_BOUNDED_AI_ENTITLEMENTS = frozenset(
    {
        EntitlementKey.tokens,
        EntitlementKey.ai_executions,
        EntitlementKey.ai_conversations,
        EntitlementKey.legal_ai_generations,
    }
)
_UNMODELED_PAID_DIMENSIONS: dict[EntitlementKey, str] = {
    EntitlementKey.audio_minutes: "ledger observado de custo por minuto de áudio",
}
_PAID_AI_ENTITLEMENTS = _TOKEN_BOUNDED_AI_ENTITLEMENTS | frozenset(
    _UNMODELED_PAID_DIMENSIONS
)


def _active_routes(route_catalog: object) -> dict[str, Any]:
    """Return every unique primary/fallback route from class and agent chains."""

    result: dict[str, Any] = {}
    for candidates in getattr(route_catalog, "classes").values():
        for candidate in candidates:
            result[candidate.key] = candidate
    for candidates in getattr(route_catalog, "agents").values():
        for candidate in candidates:
            result[candidate.key] = candidate
    return result


class CommercialUnitEconomicsService:
    """Evaluate whether one public offer has complete, current cost evidence."""

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _requires_evidence(snapshot: CommercialOfferSnapshotWrite) -> bool:
        return snapshot.is_public and any(
            snapshot.entitlements[key].enabled for key in _PAID_AI_ENTITLEMENTS
        )

    async def evaluate(
        self,
        snapshot: CommercialOfferSnapshotWrite,
    ) -> CommercialUnitEconomicsGateRead:
        evaluated_at = datetime.now(UTC)
        evidence_source = "provider_confirmed_usage_plus_internal_rate_card"
        if not self._requires_evidence(snapshot):
            reason = (
                "Oferta interna: limites e preços de IA podem permanecer em rascunho "
                "sem serem anunciados ao mercado."
                if not snapshot.is_public
                else "A oferta pública não concede capacidade paga de IA."
            )
            return CommercialUnitEconomicsGateRead(
                assessment_version=UNIT_ECONOMICS_ASSESSMENT_VERSION,
                status="not_required",
                eligible=True,
                requires_evidence=False,
                evidence_source=evidence_source,
                evaluated_at=evaluated_at,
                window_days=UNIT_ECONOMICS_WINDOW_DAYS,
                minimum_confirmed_calls=UNIT_ECONOMICS_MIN_CONFIRMED_CALLS,
                minimum_distinct_days=UNIT_ECONOMICS_MIN_DISTINCT_DAYS,
                minimum_observed_tokens=UNIT_ECONOMICS_MIN_OBSERVED_TOKENS,
                observed_calls=None,
                observed_days=None,
                observed_tokens=None,
                blockers=[],
                diagnostics=[reason],
            )

        blockers: list[CommercialUnitEconomicsBlockerRead] = []
        diagnostics: list[str] = []
        token_setting = snapshot.entitlements[EntitlementKey.tokens]
        for entitlement, required_source in _UNMODELED_PAID_DIMENSIONS.items():
            if snapshot.entitlements[entitlement].enabled:
                blockers.append(
                    CommercialUnitEconomicsBlockerRead(
                        code="PAID_DIMENSION_COST_MODEL_MISSING",
                        message=(
                            f"{entitlement.value} está habilitado, mas ainda não existe "
                            f"{required_source} e append-only. Evidência de tokens não "
                            "comprova esse custo."
                        ),
                    )
                )
        if not token_setting.enabled or token_setting.limit_quantity is None:
            blockers.append(
                CommercialUnitEconomicsBlockerRead(
                    code="AI_TOKEN_BUDGET_REQUIRED",
                    message=(
                        "Oferta pública com IA precisa de um teto finito de tokens para "
                        "que o custo máximo possa ser avaliado."
                    ),
                )
            )
        if snapshot.price_monthly_cents is None or snapshot.price_monthly_cents <= 0:
            blockers.append(
                CommercialUnitEconomicsBlockerRead(
                    code="PUBLIC_AI_PRICE_REQUIRED",
                    message=(
                        "Oferta pública com IA precisa de preço mensal positivo; proposta "
                        "sob consulta pode permanecer interna até a precificação."
                    ),
                )
            )

        try:
            # Lazy by design: importing ``app.ai`` while the commercial service
            # module is still initializing enters the gateway/accounting bridge
            # and creates a circular import through PlatformService.
            from app.ai.canonical_catalog import (
                resolve_price_catalog,
                resolve_route_catalog,
            )
            from app.ai.errors import AIConfigurationError

            route_catalog = resolve_route_catalog(settings.KLM_AI_ROUTE_CATALOG_JSON)
            price_catalog = resolve_price_catalog(settings.KLM_AI_PRICE_CATALOG_JSON)
            active_routes = _active_routes(route_catalog)
        except AIConfigurationError as exc:
            blockers.append(
                CommercialUnitEconomicsBlockerRead(
                    code="AI_ACTIVE_CATALOG_INVALID",
                    message=f"Catálogo ativo de IA inválido: {exc}",
                )
            )
            active_routes = {}
            route_catalog = None
            price_catalog = None

        if not active_routes:
            blockers.append(
                CommercialUnitEconomicsBlockerRead(
                    code="AI_ACTIVE_ROUTE_SET_EMPTY",
                    message="Nenhuma rota primária ou fallback está ativa para ser avaliada.",
                )
            )

        missing_rate_cards: list[str] = []
        if price_catalog is not None:
            missing_rate_cards = sorted(
                key for key in active_routes if key not in price_catalog.models
            )
        if missing_rate_cards:
            blockers.append(
                CommercialUnitEconomicsBlockerRead(
                    code="AI_ACTIVE_ROUTE_RATE_MISSING",
                    message=(
                        "A tabela interna de preços não cobre todas as rotas ativas: "
                        + ", ".join(missing_rate_cards)
                    ),
                )
            )

        observed_calls = 0
        observed_days = 0
        observed_tokens = 0
        observed_route_keys: set[str] = set()
        if route_catalog is not None and price_catalog is not None and active_routes:
            since = evaluated_at - timedelta(days=UNIT_ECONOMICS_WINDOW_DAYS)
            token_total = (
                PlatformModelUsageLedger.input_tokens
                + PlatformModelUsageLedger.output_tokens
            )
            active_route_filter = or_(
                *(
                    and_(
                        PlatformModelUsageLedger.provider == candidate.provider.value,
                        PlatformModelUsageLedger.model_used == candidate.model,
                    )
                    for candidate in active_routes.values()
                )
            )
            qualified_receipt = (
                PlatformModelUsageLedger.recorded_at >= since,
                func.length(func.trim(PlatformModelUsageLedger.provider_request_id)) > 0,
                PlatformModelUsageLedger.price_catalog_version == price_catalog.version,
                PlatformModelUsageLedger.route_catalog_version == route_catalog.version,
                token_total > 0,
                active_route_filter,
            )
            route_rows = (
                await self.db.execute(
                    select(
                        PlatformModelUsageLedger.provider,
                        PlatformModelUsageLedger.model_used,
                        func.count(PlatformModelUsageLedger.id),
                        func.coalesce(func.sum(token_total), 0),
                    )
                    .where(*qualified_receipt)
                    .group_by(
                        PlatformModelUsageLedger.provider,
                        PlatformModelUsageLedger.model_used,
                    )
                )
            ).all()
            for provider, model, call_count, route_tokens in route_rows:
                route_key = f"{provider}:{model}"
                if route_key not in active_routes:
                    continue
                observed_route_keys.add(route_key)
                observed_calls += int(call_count or 0)
                observed_tokens += int(route_tokens or 0)
            observed_days = int(
                await self.db.scalar(
                    select(
                        func.count(
                            func.distinct(func.date(PlatformModelUsageLedger.recorded_at))
                        )
                    ).where(*qualified_receipt)
                )
                or 0
            )

        missing_route_receipts = sorted(set(active_routes) - observed_route_keys)
        if missing_route_receipts:
            blockers.append(
                CommercialUnitEconomicsBlockerRead(
                    code="AI_ACTIVE_ROUTE_EVIDENCE_MISSING",
                    message=(
                        "Faltam recibos de uso confirmados, nas versões ativas, para: "
                        + ", ".join(missing_route_receipts)
                    ),
                )
            )
        if observed_calls < UNIT_ECONOMICS_MIN_CONFIRMED_CALLS:
            blockers.append(
                CommercialUnitEconomicsBlockerRead(
                    code="AI_COST_SAMPLE_TOO_SMALL",
                    message=(
                        f"Há {observed_calls} chamadas confirmadas nas rotas ativas; "
                        f"são exigidas {UNIT_ECONOMICS_MIN_CONFIRMED_CALLS}."
                    ),
                )
            )
        if observed_days < UNIT_ECONOMICS_MIN_DISTINCT_DAYS:
            blockers.append(
                CommercialUnitEconomicsBlockerRead(
                    code="AI_COST_WINDOW_TOO_NARROW",
                    message=(
                        f"A amostra cobre {observed_days} dia(s); são exigidos "
                        f"{UNIT_ECONOMICS_MIN_DISTINCT_DAYS} dias distintos."
                    ),
                )
            )
        if observed_tokens < UNIT_ECONOMICS_MIN_OBSERVED_TOKENS:
            blockers.append(
                CommercialUnitEconomicsBlockerRead(
                    code="AI_TOKEN_SAMPLE_TOO_SMALL",
                    message=(
                        f"A amostra contém {observed_tokens} tokens medidos; são exigidos "
                        f"{UNIT_ECONOMICS_MIN_OBSERVED_TOKENS}."
                    ),
                )
            )

        # A finite token allowance can be spent in any mix. Price the maximum
        # possible directional rate among input, output and cached input for
        # every active primary/fallback route; a cheap route cannot subsidize a
        # costly fallback and an observed blend cannot hide expensive output.
        input_rates: list[Decimal] = []
        output_rates: list[Decimal] = []
        cache_rates: list[Decimal] = []
        if price_catalog is not None and not missing_rate_cards:
            for route_key in active_routes:
                price = price_catalog.models[route_key]
                input_rates.append(price.input_per_million_usd)
                output_rates.append(price.output_per_million_usd)
                cache_rates.append(
                    price.cached_input_per_million_usd
                    if price.cached_input_per_million_usd is not None
                    else price.input_per_million_usd
                )
        directional_rates = input_rates + output_rates + cache_rates

        evidence_codes = {
            "AI_ACTIVE_CATALOG_INVALID",
            "AI_ACTIVE_ROUTE_SET_EMPTY",
            "AI_ACTIVE_ROUTE_RATE_MISSING",
            "AI_ACTIVE_ROUTE_EVIDENCE_MISSING",
            "AI_COST_SAMPLE_TOO_SMALL",
            "AI_COST_WINDOW_TOO_NARROW",
            "AI_TOKEN_SAMPLE_TOO_SMALL",
        }
        evidence_ready = not any(blocker.code in evidence_codes for blocker in blockers)
        status = "insufficient_data"
        if evidence_ready and not blockers:
            token_limit = token_setting.limit_quantity
            monthly_price_cents = snapshot.price_monthly_cents
            if (
                token_limit is None
                or monthly_price_cents is None
                or monthly_price_cents <= 0
                or not directional_rates
            ):
                blockers.append(
                    CommercialUnitEconomicsBlockerRead(
                        code="UNIT_ECONOMICS_INPUT_INCOMPLETE",
                        message=(
                            "A avaliação econômica não recebeu preço, franquia e tabela "
                            "direcional completos; a publicação permanece bloqueada."
                        ),
                    )
                )
            else:
                worst_directional_rate_per_token = max(directional_rates) / Decimal(1_000_000)
                projected_ai_cost_cents = (
                    worst_directional_rate_per_token
                    * Decimal(token_limit)
                    * UNIT_ECONOMICS_USD_BRL_REFERENCE
                    * UNIT_ECONOMICS_COST_SAFETY_FACTOR
                    * Decimal(100)
                )
                cost_share_bps = (
                    projected_ai_cost_cents * Decimal(10_000) / Decimal(monthly_price_cents)
                )
                if cost_share_bps > UNIT_ECONOMICS_MAX_AI_COST_SHARE_BPS:
                    blockers.append(
                        CommercialUnitEconomicsBlockerRead(
                            code="AI_COST_SHARE_ABOVE_INTERNAL_CEILING",
                            message=(
                                "O custo máximo projetado pela tabela interna excede o teto "
                                "da mensalidade. Reduza a franquia ou revise o preço."
                            ),
                        )
                    )
                    status = "unprofitable"
                else:
                    status = "ready"

        if directional_rates:
            diagnostics.append(
                "Tabela interna ativa separada por direção: "
                f"input até USD {max(input_rates)}/1M, "
                f"output até USD {max(output_rates)}/1M e "
                f"cache até USD {max(cache_rates)}/1M."
            )
        diagnostics.extend(
            [
                (
                    "Cobertura observada das rotas primárias e fallback ativas: "
                    f"{len(observed_route_keys)}/{len(active_routes)}."
                ),
                (
                    "Fonte: uso confirmado pelo provider combinado com a tabela interna "
                    "versionada; cost_usd histórico não define o custo projetado."
                ),
                (
                    "Hipótese interna: câmbio de referência R$ 5,50/USD e fator de "
                    "segurança 1,25. É estimativa operacional, não cotação de mercado, "
                    "fatura de provider ou prova de disposição a pagar."
                ),
                (
                    "Execuções, conversas e gerações de IA só são consideradas cobertas "
                    "quando subordinadas à franquia finita de tokens."
                ),
                (
                    "A evidência e o teto interno passaram."
                    if status == "ready"
                    else "A oferta pode permanecer interna; publicação pública continua bloqueada."
                ),
            ]
        )
        return CommercialUnitEconomicsGateRead(
            assessment_version=UNIT_ECONOMICS_ASSESSMENT_VERSION,
            status=status,
            eligible=not blockers and status == "ready",
            requires_evidence=True,
            evidence_source=evidence_source,
            evaluated_at=evaluated_at,
            window_days=UNIT_ECONOMICS_WINDOW_DAYS,
            minimum_confirmed_calls=UNIT_ECONOMICS_MIN_CONFIRMED_CALLS,
            minimum_distinct_days=UNIT_ECONOMICS_MIN_DISTINCT_DAYS,
            minimum_observed_tokens=UNIT_ECONOMICS_MIN_OBSERVED_TOKENS,
            observed_calls=observed_calls,
            observed_days=observed_days,
            observed_tokens=observed_tokens,
            blockers=blockers,
            diagnostics=diagnostics,
        )
