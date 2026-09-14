"""Immutable message history, provider envelope custody and chain verification."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.advisory import acquire_transaction_advisory_lock
from app.models.klm_operations import OmnichannelMessage
from app.models.omnichannel_v6 import (
    OmnichannelMessageEvidenceEvent,
    OmnichannelProviderWebhookEnvelope,
    OmnichannelProviderWebhookProcessingState,
)
from app.models.storage_object import StorageObject

CONTENT_EVENT_TYPES = frozenset({"captured", "edited", "deleted"})
ALLOWED_EVENT_TYPES = CONTENT_EVENT_TYPES | frozenset(
    {"provider_receipt", "media_captured", "media_unavailable", "provider_unsupported"}
)
ALLOWED_OBSERVATION_STATES = frozenset(
    {"observed", "provider_signal", "inferred", "unavailable"}
)
_SECRET_KEYS = frozenset(
    {
        "access_token",
        "accesstoken",
        "authorization",
        "cookie",
        "password",
        "refresh_token",
        "refreshtoken",
        "secret",
        "token",
    }
)
PROVIDER_WEBHOOK_PROCESSING_STATUSES = frozenset(
    {
        "queued",
        "processing",
        "processed",
        "retryable_failed",
        "terminal_failed",
    }
)
DEFAULT_PROVIDER_WEBHOOK_MAX_ATTEMPTS = 9
DEFAULT_PROVIDER_WEBHOOK_LEASE_DURATION = timedelta(minutes=2)
DEFAULT_PROVIDER_WEBHOOK_RETRY_BASE_SECONDS = 5
DEFAULT_PROVIDER_WEBHOOK_RETRY_MAX_SECONDS = 600
_PROVIDER_DELIVERY_FINGERPRINT_VERSION = b"klm-provider-delivery/v1"


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _json_default(value: Any) -> str:
    if isinstance(value, UUID | datetime):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    raise TypeError(f"Unsupported canonical evidence value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def provider_delivery_fingerprint(
    *,
    provider: str,
    raw_payload: bytes,
    signature_header: str,
    signature_scheme: str,
) -> str:
    """Fingerprint one exact signed delivery without relying on invented headers.

    The key frames every field with its byte length, so different boundaries
    cannot produce the same digest input. Request metadata is deliberately not
    included: Meta may redeliver the same signed bytes over a new HTTP request.
    """

    digest = hashlib.sha256()
    for part in (
        _PROVIDER_DELIVERY_FINGERPRINT_VERSION,
        provider.encode("utf-8"),
        signature_scheme.encode("utf-8"),
        signature_header.encode("utf-8"),
        bytes(raw_payload),
    ):
        digest.update(len(part).to_bytes(8, byteorder="big", signed=False))
        digest.update(part)
    return digest.hexdigest()


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if str(key).lower() in _SECRET_KEYS else _scrub(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, tuple):
        return [_scrub(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def deterministic_message_chain_id(
    workspace_id: UUID,
    provider: str,
    connection_key: str,
    external_message_id: str,
) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"klm-message-evidence:{workspace_id}:{provider}:{connection_key}:{external_message_id}",
    )


@dataclass(frozen=True)
class ProviderEnvelopeContext:
    id: UUID
    payload_sha256: str
    signature_sha256: str
    received_at: datetime
    deduplicated: bool = False


@dataclass(frozen=True)
class ProviderWebhookProcessingClaim:
    envelope_id: UUID
    lease_token: str
    attempt: int
    lease_expires_at: datetime


class ProviderEnvelopeFingerprintCollisionError(RuntimeError):
    """A digest matched but the immutable signed bytes did not."""


class ProviderWebhookProcessingStateMissingError(RuntimeError):
    """An envelope exists without its required mutable processing projection."""


class ProviderWebhookProcessingLeaseLostError(RuntimeError):
    """A stale worker tried to settle a claim replaced by another worker."""


def _event_hash_values(event: OmnichannelMessageEvidenceEvent) -> dict[str, Any]:
    return {
        "schemaVersion": "klm-message-evidence/v1",
        "id": event.id,
        "workspaceId": event.workspace_id,
        "chainId": event.chain_id,
        "revision": event.revision,
        "contentRevision": event.content_revision,
        "messageId": event.message_id,
        "threadId": event.thread_id,
        "channelEventId": event.channel_event_id,
        "connectionId": event.connection_id,
        "legacyWhatsAppMessageId": event.legacy_whatsapp_message_id,
        "envelopeId": event.envelope_id,
        "previousEventId": event.previous_event_id,
        "storageObjectId": event.storage_object_id,
        "provider": event.provider,
        "connectionKey": event.connection_key,
        "externalMessageId": event.external_message_id,
        "eventType": event.event_type,
        "observationState": event.observation_state,
        "contentType": event.content_type,
        "bodySnapshot": event.body_snapshot,
        "structuredContent": event.structured_content,
        "eventPayloadSha256": event.event_payload_sha256,
        "sourceEnvelopeSha256": event.source_envelope_sha256,
        "signatureSha256": event.signature_sha256,
        "mediaSha256": event.media_sha256,
        "mediaByteSize": event.media_byte_size,
        "mediaMimeType": event.media_mime_type,
        "mediaFilename": event.media_filename,
        "providerMediaId": event.provider_media_id,
        "storageObjectVersion": event.storage_object_version,
        "previousEvidenceSha256": event.previous_evidence_sha256,
        "providerOccurredAt": _aware(event.provider_occurred_at),
        "receivedAt": _aware(event.received_at),
    }


class MessageEvidenceService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @staticmethod
    def _validate_replay(
        replay: OmnichannelMessageEvidenceEvent,
        *,
        chain_id: UUID,
        event_payload_sha256: str,
    ) -> None:
        """Reject reuse of an evidence idempotency key for another event."""

        if (
            replay.chain_id != chain_id
            or replay.event_payload_sha256 != event_payload_sha256
        ):
            raise ValueError("A chave probatória já foi usada para outro conteúdo.")

    async def capture_provider_envelope(
        self,
        *,
        provider: str,
        raw_payload: bytes,
        signature_header: str,
        signature_scheme: str,
        signature_verified: bool,
        request_metadata: dict[str, Any] | None = None,
    ) -> ProviderEnvelopeContext:
        received_at = _now()
        payload_sha256 = hashlib.sha256(raw_payload).hexdigest()
        signature_sha256 = hashlib.sha256(signature_header.encode("utf-8")).hexdigest()
        delivery_fingerprint_sha256 = provider_delivery_fingerprint(
            provider=provider,
            raw_payload=raw_payload,
            signature_header=signature_header,
            signature_scheme=signature_scheme,
        )
        existing = await self._exact_provider_envelope(
            provider=provider,
            raw_payload=raw_payload,
            payload_sha256=payload_sha256,
            signature_header=signature_header,
            signature_sha256=signature_sha256,
            signature_scheme=signature_scheme,
            delivery_fingerprint_sha256=delivery_fingerprint_sha256,
        )
        if existing is not None:
            await self._require_processing_state(existing.id)
            return self._envelope_context(existing, deduplicated=True)

        envelope = OmnichannelProviderWebhookEnvelope(
            id=uuid4(),
            provider=provider,
            payload_sha256=payload_sha256,
            delivery_fingerprint_sha256=delivery_fingerprint_sha256,
            raw_payload=bytes(raw_payload),
            byte_size=len(raw_payload),
            signature_header=signature_header,
            signature_sha256=signature_sha256,
            signature_scheme=signature_scheme,
            signature_verified=signature_verified,
            request_metadata=_scrub(request_metadata or {}),
            received_at=received_at,
        )
        processing_state = OmnichannelProviderWebhookProcessingState(
            envelope_id=envelope.id,
            status="queued",
            attempts=0,
            queued_at=received_at,
        )
        try:
            # The savepoint preserves unrelated request work while the unique
            # fingerprint serializes two simultaneous deliveries.
            async with self.db.begin_nested():
                self.db.add_all((envelope, processing_state))
                await self.db.flush()
        except IntegrityError:
            concurrent = await self._exact_provider_envelope(
                provider=provider,
                raw_payload=raw_payload,
                payload_sha256=payload_sha256,
                signature_header=signature_header,
                signature_sha256=signature_sha256,
                signature_scheme=signature_scheme,
                delivery_fingerprint_sha256=delivery_fingerprint_sha256,
            )
            if concurrent is None:
                raise ProviderEnvelopeFingerprintCollisionError(
                    "Provider delivery fingerprint collision detected."
                ) from None
            await self._require_processing_state(concurrent.id)
            return self._envelope_context(concurrent, deduplicated=True)
        return self._envelope_context(envelope, deduplicated=False)

    async def get_provider_webhook_processing_state(
        self,
        envelope_id: UUID,
    ) -> OmnichannelProviderWebhookProcessingState | None:
        return await self.db.get(
            OmnichannelProviderWebhookProcessingState,
            envelope_id,
            populate_existing=True,
        )

    async def claim_provider_webhook(
        self,
        envelope_id: UUID,
        *,
        max_attempts: int = DEFAULT_PROVIDER_WEBHOOK_MAX_ATTEMPTS,
        lease_duration: timedelta = DEFAULT_PROVIDER_WEBHOOK_LEASE_DURATION,
        now: datetime | None = None,
    ) -> ProviderWebhookProcessingClaim | None:
        """Atomically claim one envelope or recover its expired orphan lease."""

        if (
            not isinstance(max_attempts, int)
            or isinstance(max_attempts, bool)
            or not 1 <= max_attempts <= 100
        ):
            raise ValueError("max_attempts must be between 1 and 100")
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        claimed_at = _aware(now or _now())
        assert claimed_at is not None
        lease_expires_at = claimed_at + lease_duration
        lease_token = uuid4().hex
        claimable = or_(
            OmnichannelProviderWebhookProcessingState.status == "queued",
            and_(
                OmnichannelProviderWebhookProcessingState.status == "retryable_failed",
                OmnichannelProviderWebhookProcessingState.next_retry_at <= claimed_at,
            ),
            and_(
                OmnichannelProviderWebhookProcessingState.status == "processing",
                OmnichannelProviderWebhookProcessingState.lease_expires_at <= claimed_at,
            ),
        )
        statement = (
            update(OmnichannelProviderWebhookProcessingState)
            .where(
                OmnichannelProviderWebhookProcessingState.envelope_id == envelope_id,
                OmnichannelProviderWebhookProcessingState.attempts < max_attempts,
                claimable,
            )
            .values(
                status="processing",
                attempts=OmnichannelProviderWebhookProcessingState.attempts + 1,
                lease_token=lease_token,
                lease_expires_at=lease_expires_at,
                processing_started_at=claimed_at,
                last_attempted_at=claimed_at,
                next_retry_at=None,
                processed_at=None,
                terminal_failed_at=None,
                updated_at=claimed_at,
            )
            .returning(OmnichannelProviderWebhookProcessingState.attempts)
            .execution_options(synchronize_session=False)
        )
        attempt = (await self.db.scalars(statement)).one_or_none()
        if attempt is not None:
            await self.db.flush()
            return ProviderWebhookProcessingClaim(
                envelope_id=envelope_id,
                lease_token=lease_token,
                attempt=int(attempt),
                lease_expires_at=lease_expires_at,
            )

        state = await self._require_processing_state(envelope_id)
        state_lease_expires = _aware(state.lease_expires_at)
        exhausted_claimable = (
            state.attempts >= max_attempts
            and state.status not in {"processed", "terminal_failed"}
            and (
                state.status in {"queued", "retryable_failed"}
                or (
                    state.status == "processing"
                    and (
                        state_lease_expires is None
                        or state_lease_expires <= claimed_at
                    )
                )
            )
        )
        if exhausted_claimable:
            exhausted = await self.db.execute(
                update(OmnichannelProviderWebhookProcessingState)
                .where(
                    OmnichannelProviderWebhookProcessingState.envelope_id == envelope_id,
                    OmnichannelProviderWebhookProcessingState.attempts >= max_attempts,
                    OmnichannelProviderWebhookProcessingState.status.not_in(
                        ("processed", "terminal_failed")
                    ),
                    or_(
                        OmnichannelProviderWebhookProcessingState.status.in_(
                            ("queued", "retryable_failed")
                        ),
                        and_(
                            OmnichannelProviderWebhookProcessingState.status
                            == "processing",
                            OmnichannelProviderWebhookProcessingState.lease_expires_at
                            <= claimed_at,
                        ),
                    ),
                )
                .values(
                    status="terminal_failed",
                    lease_token=None,
                    lease_expires_at=None,
                    next_retry_at=None,
                    processed_at=None,
                    terminal_failed_at=claimed_at,
                    last_failed_at=claimed_at,
                    last_error_code="WebhookLeaseAttemptsExhausted",
                    last_error_message=(
                        "Webhook processing stopped after the durable attempt limit."
                    ),
                    updated_at=claimed_at,
                )
                .execution_options(synchronize_session=False)
            )
            if exhausted.rowcount:
                await self.db.flush()
        return None

    async def mark_provider_webhook_processed(
        self,
        claim: ProviderWebhookProcessingClaim,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Mark success only for the still-current durable claim."""

        processed_at = _aware(now or _now())
        assert processed_at is not None
        settled = await self.db.execute(
            update(OmnichannelProviderWebhookProcessingState)
            .where(
                OmnichannelProviderWebhookProcessingState.envelope_id
                == claim.envelope_id,
                OmnichannelProviderWebhookProcessingState.status == "processing",
                OmnichannelProviderWebhookProcessingState.attempts == claim.attempt,
                OmnichannelProviderWebhookProcessingState.lease_token
                == claim.lease_token,
            )
            .values(
                status="processed",
                lease_token=None,
                lease_expires_at=None,
                next_retry_at=None,
                processed_at=processed_at,
                terminal_failed_at=None,
                last_error_code=None,
                last_error_message=None,
                updated_at=processed_at,
            )
            .execution_options(synchronize_session=False)
        )
        await self.db.flush()
        if settled.rowcount == 1:
            return True
        state = await self._require_processing_state(claim.envelope_id)
        if (
            state.status == "processed"
            and state.attempts == claim.attempt
            and state.processed_at is not None
        ):
            return False
        raise ProviderWebhookProcessingLeaseLostError(
            "Provider webhook processing lease was superseded before success."
        )

    async def mark_provider_webhook_failed(
        self,
        claim: ProviderWebhookProcessingClaim,
        *,
        error_code: str,
        retryable: bool,
        max_attempts: int = DEFAULT_PROVIDER_WEBHOOK_MAX_ATTEMPTS,
        now: datetime | None = None,
    ) -> str:
        """Persist a sanitized retry or terminal failure for the active claim."""

        if (
            not isinstance(max_attempts, int)
            or isinstance(max_attempts, bool)
            or not 1 <= max_attempts <= 100
        ):
            raise ValueError("max_attempts must be between 1 and 100")
        failed_at = _aware(now or _now())
        assert failed_at is not None
        retry_scheduled = retryable and claim.attempt < max_attempts
        status = "retryable_failed" if retry_scheduled else "terminal_failed"
        retry_at = (
            failed_at + timedelta(seconds=self._retry_delay_seconds(claim.attempt))
            if retry_scheduled
            else None
        )
        safe_code = "".join(
            character
            for character in str(error_code or "WebhookProcessingError")
            if character.isalnum() or character in {"_", "-", "."}
        )[:120] or "WebhookProcessingError"
        # Never persist exception text or provider/customer content. Only
        # fixed messages selected by a sanitized code enter the mutable state.
        safe_message = {
            "InvalidStoredWebhookJson": "Stored webhook envelope is not valid JSON.",
            "InvalidStoredWebhookRoot": (
                "Stored webhook envelope root is not a JSON object."
            ),
        }.get(
            safe_code,
            "Webhook processing failed; inspect the sanitized error code.",
        )
        settled = await self.db.execute(
            update(OmnichannelProviderWebhookProcessingState)
            .where(
                OmnichannelProviderWebhookProcessingState.envelope_id
                == claim.envelope_id,
                OmnichannelProviderWebhookProcessingState.status == "processing",
                OmnichannelProviderWebhookProcessingState.attempts == claim.attempt,
                OmnichannelProviderWebhookProcessingState.lease_token
                == claim.lease_token,
            )
            .values(
                status=status,
                lease_token=None,
                lease_expires_at=None,
                next_retry_at=retry_at,
                processed_at=None,
                last_failed_at=failed_at,
                terminal_failed_at=failed_at if status == "terminal_failed" else None,
                last_error_code=safe_code,
                last_error_message=safe_message,
                updated_at=failed_at,
            )
            .execution_options(synchronize_session=False)
        )
        await self.db.flush()
        if settled.rowcount == 1:
            return status
        state = await self._require_processing_state(claim.envelope_id)
        if state.status == status and state.attempts == claim.attempt:
            return status
        raise ProviderWebhookProcessingLeaseLostError(
            "Provider webhook processing lease was superseded before failure settlement."
        )

    @staticmethod
    def _retry_delay_seconds(attempt: int) -> int:
        exponent = max(0, min(attempt - 1, 30))
        return min(
            DEFAULT_PROVIDER_WEBHOOK_RETRY_MAX_SECONDS,
            DEFAULT_PROVIDER_WEBHOOK_RETRY_BASE_SECONDS * (2**exponent),
        )

    async def _exact_provider_envelope(
        self,
        *,
        provider: str,
        raw_payload: bytes,
        payload_sha256: str,
        signature_header: str,
        signature_sha256: str,
        signature_scheme: str,
        delivery_fingerprint_sha256: str,
    ) -> OmnichannelProviderWebhookEnvelope | None:
        fingerprint_rows = list(
            (
                await self.db.scalars(
                    select(OmnichannelProviderWebhookEnvelope)
                    .where(
                        OmnichannelProviderWebhookEnvelope.delivery_fingerprint_sha256
                        == delivery_fingerprint_sha256
                    )
                    .order_by(
                        OmnichannelProviderWebhookEnvelope.received_at,
                        OmnichannelProviderWebhookEnvelope.id,
                    )
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        for row in fingerprint_rows:
            if self._same_signed_envelope(
                row,
                provider=provider,
                raw_payload=raw_payload,
                signature_header=signature_header,
                signature_scheme=signature_scheme,
            ):
                return row
        if fingerprint_rows:
            raise ProviderEnvelopeFingerprintCollisionError(
                "Provider delivery fingerprint collision detected."
            )

        # Rows captured before the migration intentionally keep a NULL
        # fingerprint. Narrow by their stored hashes, then compare immutable
        # bytes so a hash collision cannot collapse two legitimate deliveries.
        legacy_rows = list(
            (
                await self.db.scalars(
                    select(OmnichannelProviderWebhookEnvelope)
                    .where(
                        OmnichannelProviderWebhookEnvelope.delivery_fingerprint_sha256.is_(
                            None
                        ),
                        OmnichannelProviderWebhookEnvelope.provider == provider,
                        OmnichannelProviderWebhookEnvelope.payload_sha256
                        == payload_sha256,
                        OmnichannelProviderWebhookEnvelope.signature_sha256
                        == signature_sha256,
                        OmnichannelProviderWebhookEnvelope.signature_scheme
                        == signature_scheme,
                        OmnichannelProviderWebhookEnvelope.byte_size
                        == len(raw_payload),
                    )
                    .order_by(
                        OmnichannelProviderWebhookEnvelope.received_at,
                        OmnichannelProviderWebhookEnvelope.id,
                    )
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        return next(
            (
                row
                for row in legacy_rows
                if self._same_signed_envelope(
                    row,
                    provider=provider,
                    raw_payload=raw_payload,
                    signature_header=signature_header,
                    signature_scheme=signature_scheme,
                )
            ),
            None,
        )

    @staticmethod
    def _same_signed_envelope(
        row: OmnichannelProviderWebhookEnvelope,
        *,
        provider: str,
        raw_payload: bytes,
        signature_header: str,
        signature_scheme: str,
    ) -> bool:
        return bool(
            row.provider == provider
            and row.signature_scheme == signature_scheme
            and hmac.compare_digest(row.signature_header, signature_header)
            and hmac.compare_digest(bytes(row.raw_payload), bytes(raw_payload))
        )

    async def _require_processing_state(
        self,
        envelope_id: UUID,
    ) -> OmnichannelProviderWebhookProcessingState:
        state = await self.get_provider_webhook_processing_state(envelope_id)
        if state is None:
            raise ProviderWebhookProcessingStateMissingError(
                "Provider envelope is missing its processing state."
            )
        if state.status not in PROVIDER_WEBHOOK_PROCESSING_STATUSES:
            raise ProviderWebhookProcessingStateMissingError(
                "Provider envelope processing state is invalid."
            )
        return state

    @staticmethod
    def _envelope_context(
        envelope: OmnichannelProviderWebhookEnvelope,
        *,
        deduplicated: bool,
    ) -> ProviderEnvelopeContext:
        return ProviderEnvelopeContext(
            id=envelope.id,
            payload_sha256=envelope.payload_sha256,
            signature_sha256=envelope.signature_sha256,
            received_at=envelope.received_at,
            deduplicated=deduplicated,
        )

    async def record_projection_capture(
        self,
        message: OmnichannelMessage,
    ) -> tuple[OmnichannelMessageEvidenceEvent, bool]:
        captured_at = _now()
        metadata = dict(message.message_metadata or {})
        structured = metadata.get("structuredContent")
        if not isinstance(structured, dict):
            structured = {}
        provider = str(message.provider or metadata.get("provider") or message.transport or "local")
        connection_key = str(
            metadata.get("connectionKey")
            or (
                f"connection:{message.connection_id}"
                if message.connection_id
                else f"channel:{message.channel.value}"
            )
        )
        external_message_id = str(
            message.external_message_id or message.idempotency_key or message.id
        )
        channel_event_id = None
        raw_channel_event_id = metadata.get("channelEventId")
        if raw_channel_event_id:
            try:
                channel_event_id = UUID(str(raw_channel_event_id))
            except ValueError:
                channel_event_id = None
        return await self.record_event(
            workspace_id=message.workspace_id,
            provider=provider,
            connection_key=connection_key,
            external_message_id=external_message_id,
            event_type="captured",
            observation_state="observed",
            message_id=message.id,
            thread_id=message.thread_id,
            channel_event_id=channel_event_id,
            connection_id=message.connection_id,
            body_snapshot=message.body,
            content_type=message.content_type,
            structured_content=structured,
            event_payload={
                "captureBasis": "canonical_projection",
                "direction": message.direction.value,
                "authorType": message.author_type.value,
                "audience": message.audience,
                "deliveryStatus": message.delivery_status.value,
                "metadata": metadata,
            },
            provider_occurred_at=message.occurred_at,
            received_at=captured_at,
        )

    async def backfill_missing_projection_captures(self, *, limit: int = 10_000) -> int:
        """Create a verifiable baseline for messages stored before evidence v1."""
        messages = list(
            (
                await self.db.scalars(
                    select(OmnichannelMessage)
                    .where(OmnichannelMessage.evidence_event_count == 0)
                    .order_by(OmnichannelMessage.occurred_at.asc(), OmnichannelMessage.id.asc())
                    .limit(limit)
                )
            ).all()
        )
        created = 0
        for message in messages:
            _, deduplicated = await self.record_projection_capture(message)
            if not deduplicated:
                created += 1
        return created

    async def record_event(
        self,
        *,
        workspace_id: UUID,
        provider: str,
        connection_key: str,
        external_message_id: str,
        event_type: str,
        observation_state: str = "observed",
        message_id: UUID | None = None,
        thread_id: UUID | None = None,
        channel_event_id: UUID | None = None,
        connection_id: UUID | None = None,
        legacy_whatsapp_message_id: UUID | None = None,
        envelope: ProviderEnvelopeContext | None = None,
        body_snapshot: str | None = None,
        content_type: str | None = None,
        structured_content: dict[str, Any] | None = None,
        event_payload: dict[str, Any] | None = None,
        storage_object_id: UUID | None = None,
        storage_object_version: int | None = None,
        media_sha256: str | None = None,
        media_byte_size: int | None = None,
        media_mime_type: str | None = None,
        media_filename: str | None = None,
        provider_media_id: str | None = None,
        provider_occurred_at: datetime | None = None,
        received_at: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[OmnichannelMessageEvidenceEvent, bool]:
        provider = provider.strip()
        connection_key = connection_key.strip()
        external_message_id = external_message_id.strip()
        if not provider or not connection_key or not external_message_id:
            raise ValueError("Provider, conexão e identificador externo são obrigatórios.")
        if event_type not in ALLOWED_EVENT_TYPES:
            raise ValueError("Tipo de evento probatório não suportado.")
        if observation_state not in ALLOWED_OBSERVATION_STATES:
            raise ValueError("Estado de observação probatória não suportado.")

        chain_id = deterministic_message_chain_id(
            workspace_id,
            provider,
            connection_key,
            external_message_id,
        )
        clean_structured = _scrub(structured_content or {})
        clean_payload = _scrub(event_payload or {})
        event_payload_sha256 = canonical_sha256(clean_payload)
        occurred_at = _aware(provider_occurred_at)
        observed_at = _aware(received_at) or _now()
        if idempotency_key is None:
            idempotency_digest = canonical_sha256(
                {
                    "workspaceId": workspace_id,
                    "chainId": chain_id,
                    "eventType": event_type,
                    "eventPayloadSha256": event_payload_sha256,
                    "bodySnapshot": body_snapshot,
                    "contentType": content_type,
                    "structuredContent": clean_structured,
                    "providerOccurredAt": occurred_at,
                    "mediaSha256": media_sha256,
                    "providerMediaId": provider_media_id,
                }
            )
            idempotency_key = f"message-evidence:{idempotency_digest}"
        idempotency_key = idempotency_key[:180]

        # Evidence rows are append-only and the runtime role deliberately has
        # no UPDATE privilege. PostgreSQL row locks would therefore require an
        # authority this service must not possess. Serialize first by the
        # workspace-scoped idempotency key and then by logical chain. This
        # fixed order prevents a caller from racing the same explicit key
        # across two different chains while preserving revision ordering.
        await acquire_transaction_advisory_lock(
            self.db,
            namespace="message-evidence-idempotency",
            value=f"{workspace_id}:{idempotency_key}",
        )
        await acquire_transaction_advisory_lock(
            self.db,
            namespace="message-evidence-chain",
            value=f"{workspace_id}:{chain_id}",
        )

        existing = await self.db.scalar(
            select(OmnichannelMessageEvidenceEvent).where(
                OmnichannelMessageEvidenceEvent.workspace_id == workspace_id,
                OmnichannelMessageEvidenceEvent.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            self._validate_replay(
                existing,
                chain_id=chain_id,
                event_payload_sha256=event_payload_sha256,
            )
            return existing, True

        latest = await self.db.scalar(
            select(OmnichannelMessageEvidenceEvent)
            .where(
                OmnichannelMessageEvidenceEvent.workspace_id == workspace_id,
                OmnichannelMessageEvidenceEvent.chain_id == chain_id,
            )
            .order_by(OmnichannelMessageEvidenceEvent.revision.desc())
            .limit(1)
        )
        next_revision = (latest.revision if latest else 0) + 1
        previous_content_revision = latest.content_revision if latest else None
        content_revision = (
            (previous_content_revision or 0) + 1
            if event_type in CONTENT_EVENT_TYPES
            else previous_content_revision
        )
        evidence = OmnichannelMessageEvidenceEvent(
            id=uuid4(),
            workspace_id=workspace_id,
            chain_id=chain_id,
            revision=next_revision,
            content_revision=content_revision,
            message_id=message_id,
            thread_id=thread_id,
            channel_event_id=channel_event_id,
            connection_id=connection_id,
            legacy_whatsapp_message_id=legacy_whatsapp_message_id,
            envelope_id=envelope.id if envelope else None,
            previous_event_id=latest.id if latest else None,
            storage_object_id=storage_object_id,
            provider=provider,
            connection_key=connection_key,
            external_message_id=external_message_id,
            event_type=event_type,
            observation_state=observation_state,
            content_type=content_type,
            body_snapshot=body_snapshot,
            structured_content=clean_structured,
            event_payload=clean_payload,
            event_payload_sha256=event_payload_sha256,
            source_envelope_sha256=envelope.payload_sha256 if envelope else None,
            signature_sha256=envelope.signature_sha256 if envelope else None,
            media_sha256=media_sha256,
            media_byte_size=media_byte_size,
            media_mime_type=media_mime_type,
            media_filename=media_filename,
            provider_media_id=provider_media_id,
            storage_object_version=storage_object_version,
            previous_evidence_sha256=latest.evidence_sha256 if latest else None,
            evidence_sha256="0" * 64,
            idempotency_key=idempotency_key,
            provider_occurred_at=occurred_at,
            received_at=observed_at,
        )
        evidence.evidence_sha256 = canonical_sha256(_event_hash_values(evidence))
        try:
            async with self.db.begin_nested():
                self.db.add(evidence)
                await self.db.flush()
        except IntegrityError:
            replay = await self.db.scalar(
                select(OmnichannelMessageEvidenceEvent).where(
                    OmnichannelMessageEvidenceEvent.workspace_id == workspace_id,
                    OmnichannelMessageEvidenceEvent.idempotency_key == idempotency_key,
                )
            )
            if replay is not None:
                self._validate_replay(
                    replay,
                    chain_id=chain_id,
                    event_payload_sha256=event_payload_sha256,
                )
                return replay, True
            raise

        if message_id is not None:
            message = await self.db.scalar(
                select(OmnichannelMessage)
                .where(
                    OmnichannelMessage.workspace_id == workspace_id,
                    OmnichannelMessage.id == message_id,
                )
                .with_for_update()
            )
            if message is None:
                raise LookupError("Mensagem canônica não encontrada no workspace.")
            if thread_id is not None and message.thread_id != thread_id:
                raise ValueError("Mensagem e thread da prova não pertencem à mesma conversa.")
            message.evidence_event_count = max(message.evidence_event_count, next_revision)
            message.evidence_revision_count = max(
                message.evidence_revision_count,
                content_revision or 0,
            )
            message.evidence_chain_sha256 = evidence.evidence_sha256
            if event_type == "edited":
                if body_snapshot is not None:
                    message.body = body_snapshot
                if content_type:
                    message.content_type = content_type
                message.content_lifecycle_state = "edited"
                message.content_edited_at = observed_at
            elif event_type == "deleted":
                message.content_lifecycle_state = "deleted"
                message.content_deleted_at = observed_at
            elif event_type == "provider_unsupported":
                message.content_lifecycle_state = "provider_unsupported"
            await self.db.flush()
        return evidence, False

    async def _events_for_message(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        message_id: UUID,
    ) -> tuple[OmnichannelMessage, list[OmnichannelMessageEvidenceEvent]]:
        message = await self.db.scalar(
            select(OmnichannelMessage).where(
                OmnichannelMessage.workspace_id == workspace_id,
                OmnichannelMessage.thread_id == thread_id,
                OmnichannelMessage.id == message_id,
            )
        )
        if message is None:
            raise LookupError("Mensagem não encontrada nesta conversa.")
        first = await self.db.scalar(
            select(OmnichannelMessageEvidenceEvent)
            .where(
                OmnichannelMessageEvidenceEvent.workspace_id == workspace_id,
                OmnichannelMessageEvidenceEvent.message_id == message_id,
            )
            .order_by(OmnichannelMessageEvidenceEvent.revision.asc())
            .limit(1)
        )
        if first is None:
            return message, []
        events = list(
            (
                await self.db.execute(
                    select(OmnichannelMessageEvidenceEvent)
                    .where(
                        OmnichannelMessageEvidenceEvent.workspace_id == workspace_id,
                        OmnichannelMessageEvidenceEvent.chain_id == first.chain_id,
                        or_(
                            OmnichannelMessageEvidenceEvent.message_id == message_id,
                            OmnichannelMessageEvidenceEvent.message_id.is_(None),
                        ),
                    )
                    .order_by(OmnichannelMessageEvidenceEvent.revision.asc())
                )
            ).scalars()
        )
        return message, events

    @staticmethod
    def verify_chain(events: list[OmnichannelMessageEvidenceEvent]) -> bool:
        if not events:
            return False
        previous_hash = None
        previous_id = None
        for expected_revision, event in enumerate(events, start=1):
            if event.revision != expected_revision:
                return False
            if event.previous_event_id != previous_id:
                return False
            if event.previous_evidence_sha256 != previous_hash:
                return False
            if canonical_sha256(_event_hash_values(event)) != event.evidence_sha256:
                return False
            previous_id = event.id
            previous_hash = event.evidence_sha256
        return True

    async def read_chain(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        message_id: UUID,
        *,
        include_sensitive: bool = True,
    ) -> dict[str, Any]:
        message, events = await self._events_for_message(workspace_id, thread_id, message_id)
        verified = self.verify_chain(events)
        storage_record_ids = {
            item.storage_object_id for item in events if item.storage_object_id is not None
        }
        storage_records = (
            {
                item.id: item
                for item in (
                    await self.db.scalars(
                        select(StorageObject).where(
                            StorageObject.workspace_id == workspace_id,
                            StorageObject.id.in_(storage_record_ids),
                        )
                    )
                ).all()
            }
            if storage_record_ids
            else {}
        )
        original = next((item for item in events if item.event_type == "captured"), None)
        latest_content = next(
            (item for item in reversed(events) if item.event_type in CONTENT_EVENT_TYPES),
            None,
        )

        def serialize(event: OmnichannelMessageEvidenceEvent) -> dict[str, Any]:
            storage_record = storage_records.get(event.storage_object_id)
            return {
                "id": event.id,
                "chainId": event.chain_id,
                "revision": event.revision,
                "contentRevision": event.content_revision,
                "messageId": event.message_id,
                "threadId": event.thread_id,
                "eventType": event.event_type,
                "observationState": event.observation_state,
                "provider": event.provider,
                "connectionKey": event.connection_key,
                "externalMessageId": event.external_message_id,
                "contentType": event.content_type,
                "bodySnapshot": event.body_snapshot if include_sensitive else None,
                "structuredContent": event.structured_content if include_sensitive else {},
                "eventPayload": event.event_payload if include_sensitive else {},
                "eventPayloadSha256": event.event_payload_sha256,
                "sourceEnvelopeSha256": event.source_envelope_sha256,
                "signatureSha256": event.signature_sha256,
                "previousEvidenceSha256": event.previous_evidence_sha256,
                "evidenceSha256": event.evidence_sha256,
                "media": {
                    "storageObjectId": event.storage_object_id,
                    "objectId": (
                        storage_record.object_id
                        if storage_record is not None and include_sensitive
                        else None
                    ),
                    "storageObjectVersion": event.storage_object_version,
                    "sha256": event.media_sha256,
                    "byteSize": event.media_byte_size,
                    "mimeType": event.media_mime_type,
                    "filename": event.media_filename,
                    "providerMediaId": event.provider_media_id,
                },
                "providerOccurredAt": event.provider_occurred_at,
                "receivedAt": event.received_at,
            }

        provider_notes = [
            "O histórico registra apenas eventos efetivamente entregues pelo provedor.",
            (
                "Um evento capturado sem hash de envelope assinado representa a linha de "
                "base observada pelo KLM naquele momento, não uma confirmação retroativa "
                "do estado anterior no provedor."
            ),
            (
                "No WhatsApp Cloud API, alguns apagamentos podem chegar como evento não "
                "suportado; o KLM preserva o original já capturado e identifica a "
                "sinalização recebida."
            ),
            (
                "Hashes, assinatura do webhook e cadeia append-only reforçam integridade "
                "e custódia, mas a valoração jurídica final depende do caso e da "
                "autoridade competente."
            ),
        ]
        return {
            "messageId": message.id,
            "threadId": message.thread_id,
            "chainId": events[0].chain_id if events else None,
            "contentLifecycleState": message.content_lifecycle_state,
            "eventCount": len(events),
            "contentRevisionCount": max(
                (item.content_revision or 0 for item in events),
                default=0,
            ),
            "integrityStatus": (
                "unavailable" if not events else "verified" if verified else "failed"
            ),
            "chainHeadSha256": events[-1].evidence_sha256 if events else None,
            "originalSnapshot": serialize(original) if original else None,
            "currentSnapshot": serialize(latest_content) if latest_content else None,
            "events": [serialize(item) for item in events],
            "providerNotes": provider_notes,
            "sensitiveDataHidden": not include_sensitive,
            "generatedAt": _now(),
        }

    async def export_manifest(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        message_id: UUID,
        *,
        include_sensitive: bool = True,
    ) -> dict[str, Any]:
        chain = await self.read_chain(
            workspace_id,
            thread_id,
            message_id,
            include_sensitive=include_sensitive,
        )
        manifest = {
            "schemaVersion": "klm-message-evidence-export/v1",
            "exportedAt": _now(),
            "chain": chain,
            "rawProviderEnvelopeIncluded": False,
            "rawProviderEnvelopeNote": (
                "O envelope bruto assinado permanece no cofre global e não é exposto "
                "por uma API de tenant."
            ),
        }
        manifest["manifestSha256"] = canonical_sha256(manifest)
        return manifest


__all__ = [
    "CONTENT_EVENT_TYPES",
    "DEFAULT_PROVIDER_WEBHOOK_LEASE_DURATION",
    "DEFAULT_PROVIDER_WEBHOOK_MAX_ATTEMPTS",
    "MessageEvidenceService",
    "ProviderEnvelopeFingerprintCollisionError",
    "ProviderEnvelopeContext",
    "ProviderWebhookProcessingClaim",
    "ProviderWebhookProcessingLeaseLostError",
    "ProviderWebhookProcessingStateMissingError",
    "canonical_sha256",
    "deterministic_message_chain_id",
    "provider_delivery_fingerprint",
]
