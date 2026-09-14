"""Durable asynchronous processing for signed WhatsApp webhook envelopes.

The public endpoint authenticates and validates JSON, then atomically stores
one immutable envelope plus one mutable processing state before enqueueing.
Every task invocation must claim that state. Exact Meta redeliveries and
duplicate Celery deliveries therefore converge on the same envelope and cannot
start a second active attempt.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, case, or_, select

from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.db.session import WorkerSessionLocal as AsyncSessionLocal
from app.models.omnichannel_v6 import OmnichannelProviderWebhookEnvelope
from app.services.message_evidence_service import (
    DEFAULT_PROVIDER_WEBHOOK_MAX_ATTEMPTS,
    MessageEvidenceService,
    ProviderEnvelopeContext,
    ProviderWebhookProcessingClaim,
    ProviderWebhookProcessingLeaseLostError,
)
from app.workers.celery_app import celery_app

# Celery counts retries after the first invocation. The durable state permits
# nine domain attempts, so one initial invocation plus eight task retries is
# the coherent upper bound for a single dispatch chain. The periodic recovery
# scanner starts a fresh transport chain for due durable work if broker/lease
# deferrals consume those retries without consuming a domain attempt.
WEBHOOK_TASK_MAX_RETRIES = DEFAULT_PROVIDER_WEBHOOK_MAX_ATTEMPTS - 1
WEBHOOK_TASK_RETRY_BACKOFF_MAX = 600
WEBHOOK_RECOVERY_BATCH_SIZE = 250


@dataclass(frozen=True)
class WebhookTaskOutcome:
    status: str
    retry_after_seconds: int | None = None
    error_code: str | None = None

    @property
    def should_retry(self) -> bool:
        return self.retry_after_seconds is not None


def _run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _envelope_id(value: UUID | str) -> UUID:
    try:
        return UUID(str(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("envelope_id must be a valid UUID") from exc


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _retry_after(value: datetime | None, *, minimum: int = 1) -> int:
    due_at = _utc(value)
    if due_at is None:
        return minimum
    remaining = int((due_at - datetime.now(UTC)).total_seconds())
    return max(minimum, min(remaining, WEBHOOK_TASK_RETRY_BACKOFF_MAX))


class WebhookEnvelopeNotFoundError(RuntimeError):
    """Envelope is not yet visible to the worker session."""


class WebhookProcessingDeferredError(RuntimeError):
    """Sanitized task retry marker; raw provider/customer data is never attached."""


@celery_app.task(
    name="whatsapp_webhook_tasks.process_inbound_webhook",
    bind=True,
    max_retries=WEBHOOK_TASK_MAX_RETRIES,
    retry_backoff=True,
    retry_backoff_max=WEBHOOK_TASK_RETRY_BACKOFF_MAX,
    retry_jitter=True,
)
def process_inbound_webhook(self, envelope_id: str) -> None:
    """Process or defer one durable envelope without exposing raw exceptions."""

    try:
        outcome = _run_async(_async_process_inbound_webhook(envelope_id))
    except WebhookEnvelopeNotFoundError as exc:
        raise self.retry(
            exc=WebhookProcessingDeferredError("WebhookEnvelopeNotFound"),
            countdown=5,
        ) from exc
    except ValueError:
        # Invalid task arguments cannot be repaired by retry and do not
        # correspond to a persisted envelope state.
        raise
    except Exception as exc:
        error_code = safe_error_type(exc)
        logger.error(
            "whatsapp_webhook_task.infrastructure_failure",
            envelope_id=str(envelope_id),
            error_type=error_code,
        )
        raise self.retry(
            exc=WebhookProcessingDeferredError(error_code),
            countdown=5,
        ) from None

    if outcome.should_retry:
        raise self.retry(
            exc=WebhookProcessingDeferredError(
                outcome.error_code or "WebhookProcessingDeferred"
            ),
            countdown=outcome.retry_after_seconds,
        )


@celery_app.task(name="whatsapp_webhook_tasks.recover")
def recover_provider_webhooks() -> dict[str, int]:
    """Wake due durable states after a failed ingress or worker dispatch."""

    return _run_async(_async_recover_provider_webhooks())


async def _async_recover_provider_webhooks(
    *,
    session_factory=None,
    publish=None,
    limit: int = WEBHOOK_RECOVERY_BATCH_SIZE,
    now: datetime | None = None,
) -> dict[str, int]:
    if (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= 1000
    ):
        raise ValueError("limit must be between 1 and 1000")
    reference_now = _utc(now or datetime.now(UTC))
    assert reference_now is not None
    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as db:
        from app.models.omnichannel_v6 import (
            OmnichannelProviderWebhookProcessingState,
        )

        due = or_(
            OmnichannelProviderWebhookProcessingState.status == "queued",
            and_(
                OmnichannelProviderWebhookProcessingState.status
                == "retryable_failed",
                OmnichannelProviderWebhookProcessingState.next_retry_at
                <= reference_now,
            ),
            and_(
                OmnichannelProviderWebhookProcessingState.status == "processing",
                OmnichannelProviderWebhookProcessingState.lease_expires_at
                <= reference_now,
            ),
        )
        due_order = case(
            (
                OmnichannelProviderWebhookProcessingState.status == "processing",
                OmnichannelProviderWebhookProcessingState.lease_expires_at,
            ),
            (
                OmnichannelProviderWebhookProcessingState.status
                == "retryable_failed",
                OmnichannelProviderWebhookProcessingState.next_retry_at,
            ),
            else_=OmnichannelProviderWebhookProcessingState.queued_at,
        )
        envelope_ids = list(
            (
                await db.scalars(
                    select(
                        OmnichannelProviderWebhookProcessingState.envelope_id
                    )
                    .where(due)
                    .order_by(
                        due_order,
                        OmnichannelProviderWebhookProcessingState.envelope_id,
                    )
                    .limit(limit)
                )
            ).all()
        )

    def default_publish(envelope_id: str) -> None:
        process_inbound_webhook.apply_async(
            kwargs={"envelope_id": envelope_id},
        )

    dispatcher = publish or default_publish
    dispatched = 0
    publish_failed = 0
    for envelope_id in envelope_ids:
        try:
            dispatcher(str(envelope_id))
            dispatched += 1
        except Exception as exc:
            publish_failed += 1
            # The durable row is deliberately untouched; the next beat sweep
            # will find it again after broker recovery.
            logger.warning(
                "whatsapp_webhook_recovery.publish_failed",
                envelope_id=str(envelope_id),
                error_type=safe_error_type(exc),
            )
    result = {
        "attempted": len(envelope_ids),
        "dispatched": dispatched,
        "publishFailed": publish_failed,
    }
    logger.info("whatsapp_webhook_recovery.completed", **result)
    return result


async def _async_process_inbound_webhook(
    envelope_id: UUID | str,
    *,
    session_factory=None,
) -> WebhookTaskOutcome:
    resolved_envelope_id = _envelope_id(envelope_id)
    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as db:
        envelope_row = await db.get(
            OmnichannelProviderWebhookEnvelope,
            resolved_envelope_id,
        )
        if envelope_row is None:
            logger.warning(
                "whatsapp_webhook_task.envelope_not_found",
                envelope_id=str(resolved_envelope_id),
            )
            raise WebhookEnvelopeNotFoundError(str(resolved_envelope_id))

        evidence = MessageEvidenceService(db)
        claim = await evidence.claim_provider_webhook(
            resolved_envelope_id,
            max_attempts=DEFAULT_PROVIDER_WEBHOOK_MAX_ATTEMPTS,
        )
        if claim is None:
            await db.commit()
            state = await evidence.get_provider_webhook_processing_state(
                resolved_envelope_id
            )
            if state is None:
                raise RuntimeError("WebhookProcessingStateMissing")
            if state.status in {"processed", "terminal_failed"}:
                logger.info(
                    "whatsapp_webhook_task.idempotent_noop",
                    envelope_id=str(resolved_envelope_id),
                    processing_status=state.status,
                )
                return WebhookTaskOutcome(status=state.status)
            retry_at = (
                state.lease_expires_at
                if state.status == "processing"
                else state.next_retry_at
            )
            return WebhookTaskOutcome(
                status=state.status,
                retry_after_seconds=_retry_after(retry_at),
                error_code=state.last_error_code or "WebhookProcessingLeaseBusy",
            )

        # Claim state must survive a worker crash so a later delivery can
        # recover it after lease expiry.
        await db.commit()

        try:
            payload = json.loads(bytes(envelope_row.raw_payload))
        except (UnicodeDecodeError, ValueError, TypeError):
            return await _settle_failure(
                db,
                evidence=evidence,
                claim=claim,
                error_code="InvalidStoredWebhookJson",
                retryable=False,
            )
        if not isinstance(payload, dict):
            return await _settle_failure(
                db,
                evidence=evidence,
                claim=claim,
                error_code="InvalidStoredWebhookRoot",
                retryable=False,
            )

        envelope_context = ProviderEnvelopeContext(
            id=envelope_row.id,
            payload_sha256=envelope_row.payload_sha256,
            signature_sha256=envelope_row.signature_sha256,
            received_at=envelope_row.received_at,
            deduplicated=False,
        )

        try:
            # Platform-owned numbers share the authenticated public ingress,
            # but their domain effect also runs under the same durable claim.
            from app.services.platform_inbox_whatsapp_service import (
                PlatformInboxWhatsAppService,
            )

            await PlatformInboxWhatsAppService(db).ingest(payload)
            # One Meta envelope may batch platform-owned and tenant-owned phone
            # numbers. The tenant router safely ignores unknown platform
            # numbers, so it must always see the complete authenticated batch;
            # treating one platform match as envelope-wide would drop tenants.
            from app.services.whatsapp_service import WhatsAppService

            await WhatsAppService(db).process_webhook(
                payload,
                envelope=envelope_context,
            )
            # Domain services may commit checkpoints internally. This final
            # commit still closes any remaining effect before success state.
            await db.commit()
        except Exception as exc:
            await db.rollback()
            error_code = safe_error_type(exc)
            logger.warning(
                "whatsapp_webhook_task.processing_failed",
                envelope_id=str(resolved_envelope_id),
                attempt=claim.attempt,
                error_type=error_code,
            )
            return await _settle_failure(
                db,
                evidence=evidence,
                claim=claim,
                error_code=error_code,
                retryable=True,
            )

        try:
            await evidence.mark_provider_webhook_processed(claim)
            await db.commit()
        except ProviderWebhookProcessingLeaseLostError:
            await db.rollback()
            # The domain effects are already committed, but a newer lease owns
            # the projection. Defer so that invocation can observe its result;
            # domain-level wamid/idempotency guards make the replay safe.
            return WebhookTaskOutcome(
                status="processing",
                retry_after_seconds=5,
                error_code="WebhookProcessingLeaseLost",
            )

        logger.info(
            "whatsapp_webhook_task.processed",
            envelope_id=str(resolved_envelope_id),
            attempt=claim.attempt,
        )
        return WebhookTaskOutcome(status="processed")


async def _settle_failure(
    db,
    *,
    evidence: MessageEvidenceService,
    claim: ProviderWebhookProcessingClaim,
    error_code: str,
    retryable: bool,
) -> WebhookTaskOutcome:
    try:
        status = await evidence.mark_provider_webhook_failed(
            claim,
            error_code=error_code,
            retryable=retryable,
            max_attempts=DEFAULT_PROVIDER_WEBHOOK_MAX_ATTEMPTS,
        )
        await db.commit()
    except ProviderWebhookProcessingLeaseLostError:
        await db.rollback()
        return WebhookTaskOutcome(
            status="processing",
            retry_after_seconds=5,
            error_code="WebhookProcessingLeaseLost",
        )
    state = await evidence.get_provider_webhook_processing_state(claim.envelope_id)
    if state is None:
        raise RuntimeError("WebhookProcessingStateMissing")
    logger.error(
        "whatsapp_webhook_task.failure_persisted",
        envelope_id=str(claim.envelope_id),
        processing_status=status,
        attempt=claim.attempt,
        error_type=state.last_error_code,
    )
    return WebhookTaskOutcome(
        status=status,
        retry_after_seconds=(
            _retry_after(state.next_retry_at)
            if status == "retryable_failed"
            else None
        ),
        error_code=state.last_error_code,
    )


__all__ = [
    "WEBHOOK_RECOVERY_BATCH_SIZE",
    "WEBHOOK_TASK_MAX_RETRIES",
    "WebhookEnvelopeNotFoundError",
    "WebhookTaskOutcome",
    "_async_process_inbound_webhook",
    "_async_recover_provider_webhooks",
    "process_inbound_webhook",
    "recover_provider_webhooks",
]
