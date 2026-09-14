"""Durable provider ownership, token entitlement, and measured usage accounting."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.errors import ModelRequestReplayError, UsageAccountingError
from app.ai.models import (
    CostEstimate,
    ModelRequest,
    ProviderName,
    ProviderResponse,
    RouteCandidate,
)
from app.db.tenant import set_workspace_context
from app.models.billing import (
    ModelProviderCallJournal,
    ModelProviderCallState,
    PlatformModelUsageLedger,
)
from app.models.entitlement import EntitlementKey
from app.services.entitlement_service import (
    EntitlementEnforcementService,
    EntitlementNotConfiguredError,
    entitlement_idempotency_key,
    utc_month_start,
)
from app.services.platform_service import PlatformService


@dataclass(frozen=True, slots=True)
class ModelUsageReservation:
    """SQL claim and finite token reservation acquired before provider I/O."""

    workspace_id: UUID
    request_id: str
    journal_id: UUID
    request_fingerprint: str
    period_start: date
    reserved_tokens: int
    entitlement_reservation_id: UUID | None


class ModelUsageAccountant(Protocol):
    """Mandatory lifecycle around every canonical external provider call."""

    @property
    def active(self) -> bool: ...

    async def reserve(
        self,
        *,
        request: ModelRequest,
        estimate: CostEstimate,
        route: RouteCandidate,
    ) -> ModelUsageReservation | None: ...

    async def mark_started(
        self,
        *,
        reservation: ModelUsageReservation,
        route: RouteCandidate,
        estimate: CostEstimate,
    ) -> None: ...

    async def mark_not_started(
        self,
        *,
        reservation: ModelUsageReservation,
        route: RouteCandidate,
        error_code: str,
    ) -> None: ...

    async def mark_uncertain(
        self,
        *,
        reservation: ModelUsageReservation,
        route: RouteCandidate,
        error_code: str,
        provider_request_id: str = "",
    ) -> None: ...

    async def settle(
        self,
        *,
        reservation: ModelUsageReservation,
        request: ModelRequest,
        route: RouteCandidate,
        response: ProviderResponse,
        actual_cost_usd: Decimal,
        price_catalog_version: str,
        route_catalog_version: str,
    ) -> None: ...

    async def release(self, reservation: ModelUsageReservation) -> None: ...


class NullModelUsageAccountant:
    """No-op reserved for explicitly network-free local simulation and unit tests."""

    @property
    def active(self) -> bool:
        return False

    async def reserve(self, **_kwargs) -> None:
        return None

    async def mark_started(self, **_kwargs) -> None:
        return None

    async def mark_not_started(self, **_kwargs) -> None:
        return None

    async def mark_uncertain(self, **_kwargs) -> None:
        return None

    async def settle(self, **_kwargs) -> None:
        return None

    async def release(self, _reservation: ModelUsageReservation) -> None:
        return None


SessionFactory = Callable[[], AsyncSession]


def model_request_fingerprint(request: ModelRequest) -> str:
    """Hash execution material without persisting prompts, schemas, or media."""

    def digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    schema = (
        json.dumps(
            request.response_schema,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if request.response_schema is not None
        else ""
    )
    material = {
        "agent": request.agent_name.strip().lower(),
        "attribution": {
            "context": request.attribution.context_digest,
            "matter": request.attribution.matter_id,
            "operation": request.attribution.operation,
            "playbook": request.attribution.playbook_version,
            "sources": [digest(value) for value in request.attribution.source_refs],
        },
        "idempotency_key": digest(request.idempotency_key or ""),
        "max_output_tokens": request.max_output_tokens,
        "media": [part.descriptor() for part in request.media_parts],
        "messages": [
            {
                "length": len(message.content),
                "role": message.role,
                "sha256": digest(message.content),
            }
            for message in request.messages
        ],
        "prompt_cache_key": digest(request.prompt_cache_key or ""),
        "prompt_cache_policy": request.prompt_cache_policy.value,
        "requested_model": request.requested_model,
        "response_schema_sha256": digest(schema),
        "task_class": request.task_class.value,
        "temperature": str(request.temperature),
        "workspace_id": request.workspace_id,
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SqlAlchemyModelUsageAccountant:
    """Own a provider request and settle quota plus usage in one SQL transaction."""

    def __init__(self, session_factory: SessionFactory | None = None) -> None:
        self._session_factory = session_factory

    @property
    def active(self) -> bool:
        return True

    def _session(self) -> AsyncSession:
        if self._session_factory is not None:
            return self._session_factory()
        from app.db.session import model_usage_session_factory

        return model_usage_session_factory()()

    @staticmethod
    def reservation_key(request: ModelRequest) -> str:
        return entitlement_idempotency_key(
            "model-gateway-provider-call-v2",
            request.request_id,
        )

    @staticmethod
    def _state(journal: ModelProviderCallJournal) -> str:
        state = journal.state
        return state.value if isinstance(state, ModelProviderCallState) else str(state)

    @staticmethod
    def _raise_replay(
        journal: ModelProviderCallJournal,
        *,
        fingerprint: str,
    ) -> None:
        raise ModelRequestReplayError(
            request_id=journal.request_id,
            state=SqlAlchemyModelUsageAccountant._state(journal),
            divergent=journal.request_fingerprint != fingerprint,
        )

    async def _journal_for_update(
        self,
        db: AsyncSession,
        reservation: ModelUsageReservation,
    ) -> ModelProviderCallJournal:
        journal = await db.scalar(
            select(ModelProviderCallJournal)
            .where(
                ModelProviderCallJournal.id == reservation.journal_id,
                ModelProviderCallJournal.workspace_id == reservation.workspace_id,
                ModelProviderCallJournal.request_id == reservation.request_id,
            )
            .with_for_update()
        )
        if journal is None:
            raise UsageAccountingError("Claim SQL da chamada de modelo não foi encontrado.")
        if journal.request_fingerprint != reservation.request_fingerprint:
            self._raise_replay(journal, fingerprint=reservation.request_fingerprint)
        return journal

    @staticmethod
    def _apply_route(journal: ModelProviderCallJournal, route: RouteCandidate) -> None:
        journal.provider = route.provider.value
        journal.model_used = route.model
        journal.route_key = route.key

    async def reserve(
        self,
        *,
        request: ModelRequest,
        estimate: CostEstimate,
        route: RouteCandidate,
    ) -> ModelUsageReservation:
        workspace_id = UUID(str(request.workspace_id))
        request_id = str(request.request_id or "").strip()
        if not request_id or len(request_id) > 180:
            raise ValueError("request_id de IA deve ter entre 1 e 180 caracteres.")
        fingerprint = model_request_fingerprint(request)
        reserved_tokens = max(0, estimate.input_tokens) + max(0, estimate.output_tokens)
        period_start = utc_month_start()

        async with self._session() as db:
            try:
                await set_workspace_context(db, workspace_id)
                measured = await db.scalar(
                    select(PlatformModelUsageLedger.id).where(
                        PlatformModelUsageLedger.workspace_id == workspace_id,
                        PlatformModelUsageLedger.request_id == request_id,
                    )
                )
                if measured is not None:
                    raise ModelRequestReplayError(
                        request_id=request_id,
                        state=ModelProviderCallState.succeeded.value,
                    )

                journal = ModelProviderCallJournal(
                    workspace_id=workspace_id,
                    request_id=request_id,
                    request_fingerprint=fingerprint,
                    state=ModelProviderCallState.claimed,
                    task_class=request.task_class.value,
                    operation=request.attribution.operation,
                    provider=route.provider.value,
                    model_used=route.model,
                    route_key=route.key,
                    estimated_input_tokens=estimate.input_tokens,
                    estimated_output_tokens=estimate.output_tokens,
                    estimated_cost_usd=estimate.amount_usd,
                    reserved_tokens=reserved_tokens,
                    period_start=period_start,
                    provider_request_id="",
                    actual_input_tokens=0,
                    actual_output_tokens=0,
                    cached_input_tokens=0,
                    actual_cost_usd=None,
                    price_catalog_version=estimate.catalog_version,
                    route_catalog_version="",
                    attempt_count=0,
                    error_code="",
                )
                try:
                    db.add(journal)
                    await db.flush()
                except IntegrityError:
                    await db.rollback()
                    await set_workspace_context(db, workspace_id)
                    existing = await db.scalar(
                        select(ModelProviderCallJournal).where(
                            ModelProviderCallJournal.workspace_id == workspace_id,
                            ModelProviderCallJournal.request_id == request_id,
                        )
                    )
                    if existing is None:
                        raise
                    self._raise_replay(existing, fingerprint=fingerprint)

                entitlement = await EntitlementEnforcementService(db).reserve_metered(
                    workspace_id=workspace_id,
                    entitlement=EntitlementKey.tokens,
                    quantity=reserved_tokens,
                    idempotency_key=self.reservation_key(request),
                )
                if entitlement is None and route.provider is not ProviderName.FAKE:
                    raise EntitlementNotConfiguredError(
                        "Chamadas externas de IA exigem plano e franquia finita de tokens."
                    )
                journal.entitlement_reservation_id = entitlement.id if entitlement else None
                await db.commit()
                return ModelUsageReservation(
                    workspace_id=workspace_id,
                    request_id=request_id,
                    journal_id=journal.id,
                    request_fingerprint=fingerprint,
                    period_start=period_start,
                    reserved_tokens=reserved_tokens,
                    entitlement_reservation_id=journal.entitlement_reservation_id,
                )
            except BaseException:
                await db.rollback()
                raise

    async def mark_started(
        self,
        *,
        reservation: ModelUsageReservation,
        route: RouteCandidate,
        estimate: CostEstimate,
    ) -> None:
        async with self._session() as db:
            await set_workspace_context(db, reservation.workspace_id)
            journal = await self._journal_for_update(db, reservation)
            state = ModelProviderCallState(self._state(journal))
            if state not in {
                ModelProviderCallState.claimed,
                ModelProviderCallState.uncertain,
            }:
                self._raise_replay(journal, fingerprint=reservation.request_fingerprint)
            self._apply_route(journal, route)
            journal.estimated_input_tokens = estimate.input_tokens
            journal.estimated_output_tokens = estimate.output_tokens
            journal.estimated_cost_usd = estimate.amount_usd
            journal.price_catalog_version = estimate.catalog_version
            journal.state = ModelProviderCallState.provider_started
            journal.attempt_count += 1
            journal.error_code = ""
            journal.provider_started_at = datetime.now(UTC)
            journal.resolved_at = None
            await db.commit()

    async def mark_not_started(
        self,
        *,
        reservation: ModelUsageReservation,
        route: RouteCandidate,
        error_code: str,
    ) -> None:
        async with self._session() as db:
            await set_workspace_context(db, reservation.workspace_id)
            journal = await self._journal_for_update(db, reservation)
            state = ModelProviderCallState(self._state(journal))
            if state is ModelProviderCallState.claimed:
                return
            if state is not ModelProviderCallState.provider_started:
                self._raise_replay(journal, fingerprint=reservation.request_fingerprint)
            self._apply_route(journal, route)
            journal.state = ModelProviderCallState.claimed
            journal.error_code = str(error_code or "not_started")[:120]
            journal.provider_started_at = None
            await db.commit()

    async def mark_uncertain(
        self,
        *,
        reservation: ModelUsageReservation,
        route: RouteCandidate,
        error_code: str,
        provider_request_id: str = "",
    ) -> None:
        async with self._session() as db:
            await set_workspace_context(db, reservation.workspace_id)
            journal = await self._journal_for_update(db, reservation)
            state = ModelProviderCallState(self._state(journal))
            if state is ModelProviderCallState.succeeded:
                return
            if state not in {
                ModelProviderCallState.provider_started,
                ModelProviderCallState.uncertain,
            }:
                self._raise_replay(journal, fingerprint=reservation.request_fingerprint)
            self._apply_route(journal, route)
            journal.state = ModelProviderCallState.uncertain
            journal.error_code = str(error_code or "post_start_failure")[:120]
            journal.provider_request_id = str(provider_request_id or "")[:180]
            journal.resolved_at = None
            await db.commit()

    async def settle(
        self,
        *,
        reservation: ModelUsageReservation,
        request: ModelRequest,
        route: RouteCandidate,
        response: ProviderResponse,
        actual_cost_usd: Decimal,
        price_catalog_version: str,
        route_catalog_version: str,
    ) -> None:
        actual_tokens = max(0, int(response.input_tokens)) + max(0, int(response.output_tokens))
        provider_request_id = str(response.provider_request_id or "")[:180]
        async with self._session() as db:
            await set_workspace_context(db, reservation.workspace_id)
            journal = await self._journal_for_update(db, reservation)
            state = ModelProviderCallState(self._state(journal))
            if state is ModelProviderCallState.succeeded:
                expected = (
                    int(response.input_tokens),
                    int(response.output_tokens),
                    int(response.cached_input_tokens),
                    Decimal(actual_cost_usd),
                    provider_request_id,
                )
                persisted = (
                    journal.actual_input_tokens,
                    journal.actual_output_tokens,
                    journal.cached_input_tokens,
                    Decimal(journal.actual_cost_usd or 0),
                    journal.provider_request_id,
                )
                if persisted != expected:
                    raise ValueError("Replay de sucesso contém medição divergente.")
                return
            if state not in {
                ModelProviderCallState.provider_started,
                ModelProviderCallState.uncertain,
            }:
                self._raise_replay(journal, fingerprint=reservation.request_fingerprint)

            if reservation.entitlement_reservation_id is not None:
                await EntitlementEnforcementService(db).commit_metered_by_id(
                    workspace_id=reservation.workspace_id,
                    reservation_id=reservation.entitlement_reservation_id,
                    actual_quantity=actual_tokens,
                )
            await PlatformService.log_model_usage(
                db,
                workspace_id=reservation.workspace_id,
                model_used=route.model,
                provider=route.provider.value,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cost_usd=actual_cost_usd,
                task_class=request.task_class.value,
                request_id=request.request_id,
                operation=request.attribution.operation,
                cached_input_tokens=response.cached_input_tokens,
                provider_request_id=provider_request_id,
                price_catalog_version=price_catalog_version,
                route_catalog_version=route_catalog_version,
            )
            self._apply_route(journal, route)
            journal.state = ModelProviderCallState.succeeded
            journal.provider_request_id = provider_request_id
            journal.actual_input_tokens = max(0, int(response.input_tokens))
            journal.actual_output_tokens = max(0, int(response.output_tokens))
            journal.cached_input_tokens = max(0, int(response.cached_input_tokens))
            journal.actual_cost_usd = Decimal(actual_cost_usd)
            journal.price_catalog_version = str(price_catalog_version or "")[:120]
            journal.route_catalog_version = str(route_catalog_version or "")[:120]
            journal.error_code = ""
            journal.resolved_at = datetime.now(UTC)
            await db.commit()

    async def release(self, reservation: ModelUsageReservation) -> None:
        async with self._session() as db:
            await set_workspace_context(db, reservation.workspace_id)
            journal = await self._journal_for_update(db, reservation)
            state = ModelProviderCallState(self._state(journal))
            if state is ModelProviderCallState.failed_pre_provider:
                return
            if state is not ModelProviderCallState.claimed:
                raise UsageAccountingError(
                    "Uma reserva não pode ser liberada depois do início do provider."
                )
            if reservation.entitlement_reservation_id is not None:
                await EntitlementEnforcementService(db).release_metered_by_id(
                    workspace_id=reservation.workspace_id,
                    reservation_id=reservation.entitlement_reservation_id,
                )
            journal.state = ModelProviderCallState.failed_pre_provider
            journal.error_code = "released_before_provider"
            journal.resolved_at = datetime.now(UTC)
            await db.commit()


__all__ = [
    "ModelUsageAccountant",
    "ModelUsageReservation",
    "NullModelUsageAccountant",
    "SqlAlchemyModelUsageAccountant",
    "model_request_fingerprint",
]
