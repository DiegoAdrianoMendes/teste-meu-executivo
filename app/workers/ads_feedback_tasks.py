"""Dispatch worker for prepared ads conversion feedback (Sprint F2.3-CAPI).

``ExternalActionIntent(action_type="ads_conversion_feedback")`` rows are
prepared once (see ``KlmOperationsService._ensure_conversion_feedback_intent``)
and never consumed by anything else. This module is the consumer: it reads
those intents, and for each one either

  * writes a **simulated, clearly labelled** ``provider_receipt`` record when
    the external gate is closed, the intent is not yet approved, or required
    configuration is missing (never fakes a real send), or
  * performs the real POST to the Meta Conversions API dataset endpoint when
    ``external_delivery_allowed()`` is true, the intent has been approved
    locally (``approved_local``) and the envelope is ready for dispatch.

Idempotency is by (intent id, provider_event_id/payload hash): once a
``provider_receipt`` exists for the current envelope hash, later runs skip it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.klm_runtime import external_delivery_allowed
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.db.session import WorkerSessionLocal
from app.db.tenant import set_workspace_context
from app.db.worker_discovery import set_worker_discovery_context
from app.models.conversion_feedback import AdsConversionFeedbackRecord
from app.models.finance import IntegrationConnection, IntegrationProvider, IntegrationStatus
from app.models.klm_operations import ExternalActionIntent, ExternalActionIntentStatus
from app.models.workspace import Workspace
from app.services.google_conversions_service import (
    GoogleDataManagerConfig,
    dispatch_google_conversion,
    load_google_data_manager_config,
)
from app.services.integration_secrets import reveal_integration_secret
from app.workers.celery_app import celery_app
from app.workers.fanout import FanoutMetrics, bounded_batch_size

MEASUREMENT_PROVIDER = IntegrationProvider.custom
MEASUREMENT_LABEL = "meta_capi"
WORKSPACE_BATCH_SIZE = 32
TOTAL_BATCH_SIZE = 100
_META_GRAPH_BASE = "https://graph.facebook.com"

_DUE_STATUSES = (
    ExternalActionIntentStatus.awaiting_approval,
    ExternalActionIntentStatus.approved_local,
)


@dataclass(frozen=True)
class MetaCapiConfig:
    dataset_id: str | None
    pixel_id: str | None
    access_token: str | None
    connection_id: UUID | None

    @property
    def configured(self) -> bool:
        return bool((self.dataset_id or self.pixel_id) and self.access_token)


async def load_meta_capi_config(
    db: AsyncSession, workspace_id: UUID
) -> MetaCapiConfig:
    connection = await db.scalar(
        select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.provider == MEASUREMENT_PROVIDER,
            IntegrationConnection.label == MEASUREMENT_LABEL,
        )
    )
    if connection is None:
        return MetaCapiConfig(None, None, None, None)
    stored = connection.settings or {}
    token = reveal_integration_secret(stored, "apiToken") or None
    return MetaCapiConfig(
        dataset_id=stored.get("datasetId") or None,
        pixel_id=stored.get("pixelId") or None,
        access_token=token,
        connection_id=connection.id,
    )


def _due_predicate():
    return (
        ExternalActionIntent.action_type == "ads_conversion_feedback",
        ExternalActionIntent.status.in_(_DUE_STATUSES),
    )


async def _due_workspace_ids(
    *,
    session_factory=WorkerSessionLocal,
    batch_size: int = WORKSPACE_BATCH_SIZE,
) -> list[UUID]:
    async with session_factory() as db:
        # Sem isto, a leitura sob FORCE RLS devolve zero linhas e o beat despacha
        # nada: ver app/db/worker_discovery.py (S6).
        await set_worker_discovery_context(db, family="ads_feedback")
        return list(
            (
                await db.scalars(
                    select(ExternalActionIntent.workspace_id)
                    .join(Workspace, Workspace.id == ExternalActionIntent.workspace_id)
                    .where(
                        Workspace.deleted_at.is_(None),
                        Workspace.is_active.is_(True),
                        *_due_predicate(),
                    )
                    .group_by(ExternalActionIntent.workspace_id)
                    .order_by(func.min(ExternalActionIntent.created_at))
                    .limit(bounded_batch_size(batch_size))
                )
            ).all()
        )


async def _existing_receipt_for_hash(
    db: AsyncSession, intent: ExternalActionIntent, payload_sha256: str
) -> AdsConversionFeedbackRecord | None:
    # Só recibos TERMINAIS (entrega concluída/recusada pela Meta) tornam o
    # despacho idempotente. Estados não-terminais (`blocked` com gate fechado,
    # `retryable` após falha de transporte) NÃO podem sombrear o envio real —
    # senão a conversão preparada antes da aprovação/abertura do gate ficaria
    # presa para sempre.
    return await db.scalar(
        select(AdsConversionFeedbackRecord)
        .where(
            AdsConversionFeedbackRecord.workspace_id == intent.workspace_id,
            AdsConversionFeedbackRecord.intent_id == intent.id,
            AdsConversionFeedbackRecord.record_type == "provider_receipt",
            AdsConversionFeedbackRecord.payload_sha256 == payload_sha256,
            AdsConversionFeedbackRecord.state.in_(("accepted", "rejected")),
        )
        .order_by(AdsConversionFeedbackRecord.attempt_number.desc())
        .limit(1)
    )


async def _existing_blocked_receipt_for_hash(
    db: AsyncSession, intent: ExternalActionIntent, payload_sha256: str
) -> AdsConversionFeedbackRecord | None:
    """Recibo `blocked` já existente para este envelope (evita reescrita a cada
    ciclo do beat enquanto o gate segue fechado)."""
    return await db.scalar(
        select(AdsConversionFeedbackRecord)
        .where(
            AdsConversionFeedbackRecord.workspace_id == intent.workspace_id,
            AdsConversionFeedbackRecord.intent_id == intent.id,
            AdsConversionFeedbackRecord.record_type == "provider_receipt",
            AdsConversionFeedbackRecord.payload_sha256 == payload_sha256,
            AdsConversionFeedbackRecord.state == "blocked",
        )
        .order_by(AdsConversionFeedbackRecord.attempt_number.desc())
        .limit(1)
    )


async def _next_attempt_number(db: AsyncSession, intent: ExternalActionIntent) -> int:
    count = await db.scalar(
        select(func.count(AdsConversionFeedbackRecord.id)).where(
            AdsConversionFeedbackRecord.workspace_id == intent.workspace_id,
            AdsConversionFeedbackRecord.intent_id == intent.id,
            AdsConversionFeedbackRecord.record_type == "provider_receipt",
        )
    )
    return int(count or 0)


async def _write_receipt(
    db: AsyncSession,
    *,
    intent: ExternalActionIntent,
    payload_sha256: str,
    provider_event_id: str,
    state: str,
    provider_receipt: dict[str, Any],
    http_status: int | None,
    error_code: str | None,
    external_actions_executed: int,
) -> AdsConversionFeedbackRecord:
    attempt_number = await _next_attempt_number(db, intent)
    record = AdsConversionFeedbackRecord(
        workspace_id=intent.workspace_id,
        intent_id=intent.id,
        provider=intent.provider or "unknown",
        record_type="provider_receipt",
        state=state,
        attempt_number=attempt_number,
        provider_event_id=provider_event_id,
        idempotency_key=intent.idempotency_key,
        payload_sha256=payload_sha256,
        request_envelope=(intent.payload or {}).get("providerEnvelope") or {},
        provider_receipt=provider_receipt,
        http_status=http_status,
        error_code=error_code,
        external_actions_executed=external_actions_executed,
    )
    try:
        async with db.begin_nested():
            db.add(record)
            await db.flush()
    except IntegrityError:
        existing = await db.scalar(
            select(AdsConversionFeedbackRecord).where(
                AdsConversionFeedbackRecord.workspace_id == intent.workspace_id,
                AdsConversionFeedbackRecord.intent_id == intent.id,
                AdsConversionFeedbackRecord.record_type == "provider_receipt",
                AdsConversionFeedbackRecord.attempt_number == attempt_number,
            )
        )
        if existing is None:
            raise
        return existing
    await db.commit()
    return record


_DISPATCHABLE_PROVIDERS = frozenset({"meta_ads", "google_ads"})

_PROVIDER_CONFIG_LABEL = {
    "meta_ads": "Mensuração Meta (dataset/pixel + token CAPI)",
    "google_ads": (
        "Mensuração Google Ads (customer id + developer token + access token do Data Manager)"
    ),
}


def _blocked_reason(
    *,
    gate_open: bool,
    approved: bool,
    ready: bool,
    config: MetaCapiConfig | GoogleDataManagerConfig,
    provider: str,
) -> str:
    if provider not in _DISPATCHABLE_PROVIDERS:
        return f"Despacho automático ainda não implementado para o provedor '{provider}'."
    if not gate_open:
        return "Gate de ações externas (KLM_EXTERNAL_ACTIONS_ENABLED) está fechado."
    if not approved:
        return "Intenção aguarda aprovação local antes do envio externo."
    if not ready:
        return "Envelope com configuração de consentimento/destino incompleta."
    if not config.configured:
        label = _PROVIDER_CONFIG_LABEL.get(provider, f"Mensuração {provider}")
        return f"{label} não configurada no workspace."
    return "Bloqueado por política de despacho."


async def _dispatch_meta_real(
    *, envelope: dict[str, Any], config: MetaCapiConfig
) -> tuple[dict[str, Any], int, str | None]:
    """POST a single event to the Meta Conversions API dataset endpoint.

    Only ever reached when ``external_delivery_allowed()`` is true — callers
    must check the gate before invoking this function.
    """
    dataset_id = config.dataset_id or config.pixel_id
    api_version = settings.META_GRAPH_API_VERSION
    url = f"{_META_GRAPH_BASE}/{api_version}/{dataset_id}/events"
    provider_payload = envelope.get("providerPayload") or {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            url,
            params={"access_token": config.access_token},
            json=provider_payload,
        )
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text[:500]}
    return body, response.status_code, None


async def _dispatch_google_real(
    *, envelope: dict[str, Any], config: GoogleDataManagerConfig
) -> tuple[dict[str, Any], int, str | None]:
    """POST a single event batch to the Google Data Manager API. Only ever
    reached when `external_delivery_allowed()` is true — same contract as
    `_dispatch_meta_real`. Thin wrapper so tests can monkeypatch this name,
    mirroring the Meta dispatch function."""
    return await dispatch_google_conversion(envelope=envelope, config=config)


async def _load_provider_config(
    db: AsyncSession, workspace_id: UUID, provider: str
) -> MetaCapiConfig | GoogleDataManagerConfig:
    if provider == "meta_ads":
        return await load_meta_capi_config(db, workspace_id)
    if provider == "google_ads":
        return await load_google_data_manager_config(db, workspace_id)
    return MetaCapiConfig(None, None, None, None)


async def dispatch_intent(
    db: AsyncSession,
    intent: ExternalActionIntent,
) -> dict[str, Any]:
    """Process a single prepared conversion feedback intent. Idempotent."""

    payload = intent.payload or {}
    envelope = payload.get("providerEnvelope") or {}
    payload_sha256 = str(envelope.get("payloadSha256") or payload.get("payloadSha256") or "")
    provider_event_id = str(payload.get("providerEventId") or "")
    provider = intent.provider or "unknown"
    if not payload_sha256:
        return {"outcome": "skipped_invalid_envelope", "intentId": str(intent.id)}

    existing = await _existing_receipt_for_hash(db, intent, payload_sha256)
    if existing is not None:
        return {
            "outcome": "already_dispatched",
            "intentId": str(intent.id),
            "state": existing.state,
            "simulated": bool((existing.provider_receipt or {}).get("simulated")),
        }

    gate_open = external_delivery_allowed()
    approved = intent.status == ExternalActionIntentStatus.approved_local
    ready = bool(payload.get("readyForDispatch"))
    config = await _load_provider_config(db, intent.workspace_id, provider)
    can_attempt_real = (
        gate_open
        and approved
        and ready
        and provider in _DISPATCHABLE_PROVIDERS
        and config.configured
    )

    if not can_attempt_real:
        # Não reescreve um recibo `blocked` idêntico a cada minuto do beat: se
        # já existe um bloqueio para este mesmo envelope, apenas o reporta. Ele
        # NÃO impede o despacho real depois (o check terminal acima só olha
        # accepted/rejected), então a conversão não fica presa.
        prior_block = await _existing_blocked_receipt_for_hash(db, intent, payload_sha256)
        if prior_block is not None:
            return {
                "outcome": "already_dispatched",
                "intentId": str(intent.id),
                "state": prior_block.state,
                "simulated": bool((prior_block.provider_receipt or {}).get("simulated")),
            }
        reason = _blocked_reason(
            gate_open=gate_open, approved=approved, ready=ready, config=config, provider=provider
        )
        record = await _write_receipt(
            db,
            intent=intent,
            payload_sha256=payload_sha256,
            provider_event_id=provider_event_id,
            state="blocked",
            provider_receipt={
                "simulated": True,
                "reason": reason,
                "generatedAt": datetime.now(UTC).isoformat(),
            },
            http_status=None,
            error_code="blocked_external",
            external_actions_executed=0,
        )
        return {
            "outcome": "blocked_external",
            "intentId": str(intent.id),
            "recordId": str(record.id),
            "simulated": True,
            "reason": reason,
        }

    dispatch_fn = _dispatch_meta_real if provider == "meta_ads" else _dispatch_google_real
    try:
        body, http_status, error_code = await dispatch_fn(envelope=envelope, config=config)
    except httpx.HTTPError as error:
        logger.error(
            "ads_feedback.dispatch_transport_error",
            intent_id=str(intent.id),
            error_type=safe_error_type(error),
        )
        record = await _write_receipt(
            db,
            intent=intent,
            payload_sha256=payload_sha256,
            provider_event_id=provider_event_id,
            state="retryable",
            provider_receipt={
                "simulated": False,
                "error": "transport_error",
                "generatedAt": datetime.now(UTC).isoformat(),
            },
            http_status=None,
            error_code="transport_error",
            external_actions_executed=1,
        )
        raise _RetryableDispatchError(str(record.id)) from error

    accepted = 200 <= http_status < 300 and not body.get("error")
    record = await _write_receipt(
        db,
        intent=intent,
        payload_sha256=payload_sha256,
        provider_event_id=provider_event_id,
        state="accepted" if accepted else "rejected",
        provider_receipt={"simulated": False, "response": body},
        http_status=http_status,
        error_code=None if accepted else str((body.get("error") or {}).get("code") or "rejected"),
        external_actions_executed=1,
    )
    return {
        "outcome": "accepted" if accepted else "rejected",
        "intentId": str(intent.id),
        "recordId": str(record.id),
        "simulated": False,
        "httpStatus": http_status,
    }


class _RetryableDispatchError(RuntimeError):
    def __init__(self, record_id: str) -> None:
        super().__init__("Falha transitória ao despachar feedback de conversão.")
        self.record_id = record_id


async def dispatch_workspace_conversion_feedback(
    workspace_id: UUID,
    *,
    db: AsyncSession | None = None,
    session_factory=WorkerSessionLocal,
    limit: int = TOTAL_BATCH_SIZE,
) -> dict[str, int]:
    """Process due intents for one workspace. Reused by the API endpoint
    (immediate, request-scoped dispatch) and by the periodic celery task."""

    async def _run(session: AsyncSession) -> dict[str, int]:
        await set_workspace_context(session, workspace_id)
        intents = list(
            (
                await session.scalars(
                    select(ExternalActionIntent)
                    .where(
                        ExternalActionIntent.workspace_id == workspace_id,
                        *_due_predicate(),
                    )
                    .order_by(ExternalActionIntent.created_at.asc())
                    .limit(bounded_batch_size(limit, maximum=TOTAL_BATCH_SIZE))
                )
            ).all()
        )
        counts: dict[str, int] = {
            "preparedProcessed": 0,
            "alreadyDispatched": 0,
            "blockedSimulated": 0,
            "dispatchedReal": 0,
            "failed": 0,
        }
        for intent in intents:
            counts["preparedProcessed"] += 1
            try:
                outcome = await dispatch_intent(session, intent)
            except _RetryableDispatchError:
                counts["failed"] += 1
                continue
            match outcome["outcome"]:
                case "already_dispatched":
                    counts["alreadyDispatched"] += 1
                case "blocked_external":
                    counts["blockedSimulated"] += 1
                case "accepted":
                    counts["dispatchedReal"] += 1
                case "rejected":
                    counts["failed"] += 1
                case _:
                    pass
        return counts

    if db is not None:
        return await _run(db)
    async with session_factory() as session:
        return await _run(session)


async def process_due_conversion_feedback(
    *,
    session_factory=WorkerSessionLocal,
    batch_size: int = WORKSPACE_BATCH_SIZE,
) -> dict[str, int]:
    workspace_ids = await _due_workspace_ids(
        session_factory=session_factory, batch_size=batch_size
    )
    metrics = FanoutMetrics(
        event="ads_conversion_feedback_due",
        batch_limit=bounded_batch_size(batch_size),
        discovery_rows=len(workspace_ids),
        discovered_workspaces=len(workspace_ids),
    )
    totals: dict[str, int] = {}
    for workspace_id in workspace_ids:
        try:
            counts = await dispatch_workspace_conversion_feedback(
                workspace_id, session_factory=session_factory
            )
            metrics.enqueued_workspaces += 1
            for key, value in counts.items():
                totals[key] = totals.get(key, 0) + value
        except Exception as error:  # pragma: no cover - defensive, logged not swallowed
            metrics.publish_failures += 1
            logger.error(
                "ads_feedback.workspace_failed",
                workspace_id=str(workspace_id),
                error_type=safe_error_type(error),
            )
            raise
    metrics.emit()
    return {"workspaces": len(workspace_ids), **totals}


def _run(coro):
    return asyncio.run(coro)


@celery_app.task(name="ads_feedback_tasks.process_due")
def process_due(batch_size: int = WORKSPACE_BATCH_SIZE) -> dict[str, int]:
    return _run(process_due_conversion_feedback(batch_size=batch_size))


@celery_app.task(name="ads_feedback_tasks.process_workspace")
def process_workspace(workspace_id: str) -> dict[str, int]:
    return _run(dispatch_workspace_conversion_feedback(UUID(workspace_id)))


__all__ = [
    "MetaCapiConfig",
    "MEASUREMENT_LABEL",
    "MEASUREMENT_PROVIDER",
    "dispatch_intent",
    "dispatch_workspace_conversion_feedback",
    "load_meta_capi_config",
    "load_google_data_manager_config",
    "process_due",
    "process_due_conversion_feedback",
    "process_workspace",
]
