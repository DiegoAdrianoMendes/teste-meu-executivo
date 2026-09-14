"""Tenant-scoped processing state machine for the native FunctionUnit outbox.

The producer writes ``OSSNativeOutboxEvent`` in its domain transaction. This service
only owns the local hand-off lifecycle; it never invokes a network or provider client.
Transaction commit/rollback remains with the caller so a worker can commit the claim
before starting work and settle the result in a separate short transaction.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.tenant import set_workspace_context
from app.models.oss_native import (
    OSSNativeOutboxEvent,
    OSSNativeOutboxStatus,
    OSSNativeSecretError,
    assert_secret_free_json,
)

DEFAULT_LEASE_DURATION = timedelta(minutes=5)
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BASE_BACKOFF = timedelta(seconds=30)
DEFAULT_MAX_BACKOFF = timedelta(hours=1)
MAX_CLAIM_BATCH = 100
MAX_ERROR_LENGTH = 240
EXHAUSTED_LEASE_ERROR = "lease expired after maximum attempts"


class OSSNativeOutboxError(RuntimeError):
    """Base error for the local native outbox processor."""


class OSSNativeOutboxEventNotFoundError(OSSNativeOutboxError):
    """Raised when an event is absent from the bound workspace."""


class OSSNativeOutboxLeaseLostError(OSSNativeOutboxError):
    """Raised when a stale or expired claim tries to mutate an event."""


@dataclass(frozen=True)
class OSSNativeOutboxClaim:
    """Immutable work item whose attempt number is the lease fencing token."""

    event_id: UUID
    workspace_id: UUID
    attempt: int
    lease_expires_at: datetime
    provider: str
    capability: str
    unit_id: str
    binding_id: str
    topic: str
    dedupe_key: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class OSSNativeOutboxTransition:
    event_id: UUID
    workspace_id: UUID
    status: OSSNativeOutboxStatus
    attempt: int
    available_at: datetime
    lease_expires_at: datetime | None
    completed_at: datetime | None
    last_error: str | None
    idempotent_replay: bool


@dataclass(frozen=True)
class OSSNativeOutboxEventView:
    event_id: UUID
    workspace_id: UUID
    status: OSSNativeOutboxStatus
    attempts: int
    available_at: datetime
    lease_expires_at: datetime | None
    completed_at: datetime | None
    last_error: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "eventId": str(self.event_id),
            "workspaceId": str(self.workspace_id),
            "status": self.status.value,
            "attempts": self.attempts,
            "availableAt": self.available_at.isoformat(),
            "leaseExpiresAt": (
                self.lease_expires_at.isoformat() if self.lease_expires_at else None
            ),
            "completedAt": self.completed_at.isoformat() if self.completed_at else None,
            "lastError": self.last_error,
        }


@dataclass(frozen=True)
class OSSNativeOutboxMetrics:
    workspace_id: UUID
    total: int
    pending: int
    processing: int
    completed: int
    failed: int
    claimable: int
    retry_scheduled: int
    expired_leases: int
    attempts_total: int
    oldest_claimable_at: datetime | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "workspaceId": str(self.workspace_id),
            "total": self.total,
            "status": {
                "pending": self.pending,
                "processing": self.processing,
                "completed": self.completed,
                "failed": self.failed,
            },
            "claimable": self.claimable,
            "retryScheduled": self.retry_scheduled,
            "expiredLeases": self.expired_leases,
            "attemptsTotal": self.attempts_total,
            "oldestClaimableAt": (
                self.oldest_claimable_at.isoformat() if self.oldest_claimable_at else None
            ),
        }


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _claimable_condition(now: datetime, max_attempts: int):
    return and_(
        OSSNativeOutboxEvent.attempts < max_attempts,
        or_(
            and_(
                OSSNativeOutboxEvent.status == OSSNativeOutboxStatus.pending,
                OSSNativeOutboxEvent.available_at <= now,
            ),
            and_(
                OSSNativeOutboxEvent.status == OSSNativeOutboxStatus.processing,
                or_(
                    OSSNativeOutboxEvent.lease_expires_at.is_(None),
                    OSSNativeOutboxEvent.lease_expires_at <= now,
                ),
            ),
        ),
    )


def _postgresql_claim_candidates(
    workspace_id: UUID,
    *,
    now: datetime,
    max_attempts: int,
    limit: int,
):
    """Build the exact row-locking statement used by PostgreSQL workers."""

    return (
        select(OSSNativeOutboxEvent)
        .where(
            OSSNativeOutboxEvent.workspace_id == workspace_id,
            _claimable_condition(now, max_attempts),
        )
        .order_by(
            OSSNativeOutboxEvent.available_at,
            OSSNativeOutboxEvent.created_at,
            OSSNativeOutboxEvent.id,
        )
        .limit(limit)
        .with_for_update(skip_locked=True)
    )


class OSSNativeOutboxService:
    """Claim and settle native outbox events without executing external effects."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        lease_duration: timedelta = DEFAULT_LEASE_DURATION,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        base_backoff: timedelta = DEFAULT_BASE_BACKOFF,
        max_backoff: timedelta = DEFAULT_MAX_BACKOFF,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        if (
            isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or not 1 <= max_attempts <= 100
        ):
            raise ValueError("max_attempts must be between 1 and 100")
        if base_backoff <= timedelta(0):
            raise ValueError("base_backoff must be positive")
        if max_backoff < base_backoff:
            raise ValueError("max_backoff must be greater than or equal to base_backoff")
        self.db = db
        self.lease_duration = lease_duration
        self.max_attempts = max_attempts
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff
        self.clock = clock

    def retry_delay(self, attempt: int) -> timedelta:
        """Return deterministic capped exponential backoff for a failed attempt."""

        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise ValueError("attempt must be a positive integer")
        delay = self.base_backoff
        for _ in range(attempt - 1):
            if delay >= self.max_backoff:
                return self.max_backoff
            delay = min(delay * 2, self.max_backoff)
        return delay

    async def claim_one(
        self,
        workspace_id: UUID,
        *,
        now: datetime | None = None,
    ) -> OSSNativeOutboxClaim | None:
        claims = await self.claim_batch(workspace_id, limit=1, now=now)
        return claims[0] if claims else None

    async def claim_batch(
        self,
        workspace_id: UUID,
        *,
        limit: int = 1,
        now: datetime | None = None,
    ) -> tuple[OSSNativeOutboxClaim, ...]:
        """Claim a bounded batch; the caller must commit before processing it."""

        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_CLAIM_BATCH
        ):
            raise ValueError(f"limit must be between 1 and {MAX_CLAIM_BATCH}")
        claimed_at = self._now(now)
        lease_expires_at = claimed_at + self.lease_duration
        await set_workspace_context(self.db, workspace_id)
        await self._fail_exhausted_leases(workspace_id, claimed_at)

        if self.db.get_bind().dialect.name == "postgresql":
            statement = _postgresql_claim_candidates(
                workspace_id,
                now=claimed_at,
                max_attempts=self.max_attempts,
                limit=limit,
            ).execution_options(populate_existing=True)
            events = list((await self.db.scalars(statement)).all())
            for event in events:
                event.status = OSSNativeOutboxStatus.processing
                event.attempts += 1
                event.lease_expires_at = lease_expires_at
                event.completed_at = None
            await self.db.flush()
        else:
            events = await self._claim_with_atomic_update(
                workspace_id,
                now=claimed_at,
                lease_expires_at=lease_expires_at,
                limit=limit,
            )

        events.sort(
            key=lambda event: (
                _as_utc(event.available_at) or claimed_at,
                _as_utc(event.created_at) or claimed_at,
                str(event.id),
            )
        )
        return tuple(self._to_claim(event) for event in events)

    async def complete(
        self,
        claim: OSSNativeOutboxClaim,
        *,
        now: datetime | None = None,
    ) -> OSSNativeOutboxTransition:
        """Complete only the active attempt and replay the same completion safely."""

        completed_at = self._now(now)
        await set_workspace_context(self.db, claim.workspace_id)
        event = await self._guarded_transition(
            claim,
            now=completed_at,
            values={
                "status": OSSNativeOutboxStatus.completed,
                "lease_expires_at": None,
                "completed_at": completed_at,
                "last_error": None,
            },
        )
        if event is not None:
            return self._to_transition(event, idempotent_replay=False)
        return await self._resolve_replay(claim, operation="complete")

    async def fail(
        self,
        claim: OSSNativeOutboxClaim,
        error: BaseException | str,
        *,
        retryable: bool = True,
        now: datetime | None = None,
    ) -> OSSNativeOutboxTransition:
        """Release a failed lease into retry backoff or the terminal failed state."""

        failed_at = self._now(now)
        await set_workspace_context(self.db, claim.workspace_id)
        retry_scheduled = retryable and claim.attempt < self.max_attempts
        next_status = (
            OSSNativeOutboxStatus.pending if retry_scheduled else OSSNativeOutboxStatus.failed
        )
        available_at = failed_at + self.retry_delay(claim.attempt) if retry_scheduled else failed_at
        event = await self._guarded_transition(
            claim,
            now=failed_at,
            values={
                "status": next_status,
                "available_at": available_at,
                "lease_expires_at": None,
                "completed_at": None,
                "last_error": self._safe_error(error),
            },
        )
        if event is not None:
            return self._to_transition(event, idempotent_replay=False)
        return await self._resolve_replay(claim, operation="fail")

    async def get_status(
        self,
        workspace_id: UUID,
        event_id: UUID,
    ) -> OSSNativeOutboxEventView:
        await set_workspace_context(self.db, workspace_id)
        event = await self._event_in_workspace(workspace_id, event_id)
        if event is None:
            raise OSSNativeOutboxEventNotFoundError("Native outbox event not found")
        return self._to_view(event)

    async def metrics(
        self,
        workspace_id: UUID,
        *,
        now: datetime | None = None,
    ) -> OSSNativeOutboxMetrics:
        """Return workspace-local queue health without exposing event payloads."""

        measured_at = self._now(now)
        await set_workspace_context(self.db, workspace_id)
        pending = OSSNativeOutboxEvent.status == OSSNativeOutboxStatus.pending
        processing = OSSNativeOutboxEvent.status == OSSNativeOutboxStatus.processing
        completed = OSSNativeOutboxEvent.status == OSSNativeOutboxStatus.completed
        failed = OSSNativeOutboxEvent.status == OSSNativeOutboxStatus.failed
        expired_lease = and_(
            processing,
            or_(
                OSSNativeOutboxEvent.lease_expires_at.is_(None),
                OSSNativeOutboxEvent.lease_expires_at <= measured_at,
            ),
        )
        claimable_pending = and_(
            pending,
            OSSNativeOutboxEvent.attempts < self.max_attempts,
            OSSNativeOutboxEvent.available_at <= measured_at,
        )
        claimable_expired_lease = and_(
            expired_lease,
            OSSNativeOutboxEvent.attempts < self.max_attempts,
        )
        claimable = _claimable_condition(measured_at, self.max_attempts)
        retry_scheduled = and_(
            pending,
            OSSNativeOutboxEvent.attempts > 0,
            OSSNativeOutboxEvent.attempts < self.max_attempts,
            OSSNativeOutboxEvent.available_at > measured_at,
        )

        def count_when(condition):
            return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)

        oldest_claimable = case(
            (
                claimable_pending,
                OSSNativeOutboxEvent.available_at,
            ),
            (
                claimable_expired_lease,
                func.coalesce(
                    OSSNativeOutboxEvent.lease_expires_at,
                    OSSNativeOutboxEvent.created_at,
                ),
            ),
            else_=None,
        )
        row = (
            await self.db.execute(
                select(
                    func.count(OSSNativeOutboxEvent.id),
                    count_when(pending),
                    count_when(processing),
                    count_when(completed),
                    count_when(failed),
                    count_when(claimable),
                    count_when(retry_scheduled),
                    count_when(expired_lease),
                    func.coalesce(func.sum(OSSNativeOutboxEvent.attempts), 0),
                    func.min(oldest_claimable),
                ).where(OSSNativeOutboxEvent.workspace_id == workspace_id)
            )
        ).one()
        return OSSNativeOutboxMetrics(
            workspace_id=workspace_id,
            total=int(row[0]),
            pending=int(row[1]),
            processing=int(row[2]),
            completed=int(row[3]),
            failed=int(row[4]),
            claimable=int(row[5]),
            retry_scheduled=int(row[6]),
            expired_leases=int(row[7]),
            attempts_total=int(row[8]),
            oldest_claimable_at=_as_utc(row[9]),
        )

    async def _claim_with_atomic_update(
        self,
        workspace_id: UUID,
        *,
        now: datetime,
        lease_expires_at: datetime,
        limit: int,
    ) -> list[OSSNativeOutboxEvent]:
        """SQLite fallback: choose and fence rows in one write statement."""

        candidate_ids = (
            select(OSSNativeOutboxEvent.id)
            .where(
                OSSNativeOutboxEvent.workspace_id == workspace_id,
                _claimable_condition(now, self.max_attempts),
            )
            .order_by(
                OSSNativeOutboxEvent.available_at,
                OSSNativeOutboxEvent.created_at,
                OSSNativeOutboxEvent.id,
            )
            .limit(limit)
        )
        statement = (
            update(OSSNativeOutboxEvent)
            .where(
                OSSNativeOutboxEvent.workspace_id == workspace_id,
                OSSNativeOutboxEvent.id.in_(candidate_ids),
                _claimable_condition(now, self.max_attempts),
            )
            .values(
                status=OSSNativeOutboxStatus.processing,
                attempts=OSSNativeOutboxEvent.attempts + 1,
                lease_expires_at=lease_expires_at,
                completed_at=None,
            )
            .returning(OSSNativeOutboxEvent.id)
            .execution_options(synchronize_session=False)
        )
        event_ids = list((await self.db.scalars(statement)).all())
        await self.db.flush()
        if not event_ids:
            return []
        return list(
            (
                await self.db.scalars(
                    select(OSSNativeOutboxEvent)
                    .where(
                        OSSNativeOutboxEvent.workspace_id == workspace_id,
                        OSSNativeOutboxEvent.id.in_(event_ids),
                    )
                    .execution_options(populate_existing=True)
                )
            ).all()
        )

    async def _fail_exhausted_leases(
        self,
        workspace_id: UUID,
        now: datetime,
    ) -> None:
        expired_processing = and_(
            OSSNativeOutboxEvent.status == OSSNativeOutboxStatus.processing,
            or_(
                OSSNativeOutboxEvent.lease_expires_at.is_(None),
                OSSNativeOutboxEvent.lease_expires_at <= now,
            ),
        )
        await self.db.execute(
            update(OSSNativeOutboxEvent)
            .where(
                OSSNativeOutboxEvent.workspace_id == workspace_id,
                OSSNativeOutboxEvent.attempts >= self.max_attempts,
                or_(
                    OSSNativeOutboxEvent.status == OSSNativeOutboxStatus.pending,
                    expired_processing,
                ),
            )
            .values(
                status=OSSNativeOutboxStatus.failed,
                available_at=now,
                lease_expires_at=None,
                completed_at=None,
                last_error=EXHAUSTED_LEASE_ERROR,
            )
            .execution_options(synchronize_session=False)
        )

    async def _guarded_transition(
        self,
        claim: OSSNativeOutboxClaim,
        *,
        now: datetime,
        values: dict[str, Any],
    ) -> OSSNativeOutboxEvent | None:
        statement = (
            update(OSSNativeOutboxEvent)
            .where(
                OSSNativeOutboxEvent.id == claim.event_id,
                OSSNativeOutboxEvent.workspace_id == claim.workspace_id,
                OSSNativeOutboxEvent.status == OSSNativeOutboxStatus.processing,
                OSSNativeOutboxEvent.attempts == claim.attempt,
                OSSNativeOutboxEvent.lease_expires_at.is_not(None),
                OSSNativeOutboxEvent.lease_expires_at > now,
            )
            .values(**values)
            .returning(OSSNativeOutboxEvent.id)
            .execution_options(synchronize_session=False)
        )
        event_id = (await self.db.scalars(statement)).one_or_none()
        await self.db.flush()
        if event_id is None:
            return None
        return await self._event_in_workspace(claim.workspace_id, event_id)

    async def _resolve_replay(
        self,
        claim: OSSNativeOutboxClaim,
        *,
        operation: str,
    ) -> OSSNativeOutboxTransition:
        event = await self._event_in_workspace(claim.workspace_id, claim.event_id)
        if event is None:
            raise OSSNativeOutboxEventNotFoundError("Native outbox event not found")
        same_attempt = event.attempts == claim.attempt
        if (
            operation == "complete"
            and same_attempt
            and event.status == OSSNativeOutboxStatus.completed
            and event.completed_at is not None
        ):
            return self._to_transition(event, idempotent_replay=True)
        if (
            operation == "fail"
            and same_attempt
            and event.status in {OSSNativeOutboxStatus.pending, OSSNativeOutboxStatus.failed}
            and event.lease_expires_at is None
            and event.last_error is not None
            and event.last_error != EXHAUSTED_LEASE_ERROR
        ):
            return self._to_transition(event, idempotent_replay=True)
        raise OSSNativeOutboxLeaseLostError(
            "Native outbox lease is expired, superseded, or owned by another attempt"
        )

    async def _event_in_workspace(
        self,
        workspace_id: UUID,
        event_id: UUID,
    ) -> OSSNativeOutboxEvent | None:
        return await self.db.scalar(
            select(OSSNativeOutboxEvent)
            .where(
                OSSNativeOutboxEvent.id == event_id,
                OSSNativeOutboxEvent.workspace_id == workspace_id,
            )
            .execution_options(populate_existing=True)
        )

    def _to_claim(self, event: OSSNativeOutboxEvent) -> OSSNativeOutboxClaim:
        lease_expires_at = _as_utc(event.lease_expires_at)
        if lease_expires_at is None:
            raise OSSNativeOutboxLeaseLostError("Claimed event has no active lease")
        payload = assert_secret_free_json(
            event.payload,
            field_name="native outbox payload",
        )
        return OSSNativeOutboxClaim(
            event_id=event.id,
            workspace_id=event.workspace_id,
            attempt=event.attempts,
            lease_expires_at=lease_expires_at,
            provider=event.provider,
            capability=event.capability,
            unit_id=event.unit_id,
            binding_id=event.binding_id,
            topic=event.topic,
            dedupe_key=event.dedupe_key,
            payload=payload,
        )

    @staticmethod
    def _to_transition(
        event: OSSNativeOutboxEvent,
        *,
        idempotent_replay: bool,
    ) -> OSSNativeOutboxTransition:
        available_at = _as_utc(event.available_at)
        if available_at is None:
            raise OSSNativeOutboxError("Native outbox event has no availability timestamp")
        return OSSNativeOutboxTransition(
            event_id=event.id,
            workspace_id=event.workspace_id,
            status=event.status,
            attempt=event.attempts,
            available_at=available_at,
            lease_expires_at=_as_utc(event.lease_expires_at),
            completed_at=_as_utc(event.completed_at),
            last_error=event.last_error,
            idempotent_replay=idempotent_replay,
        )

    @staticmethod
    def _to_view(event: OSSNativeOutboxEvent) -> OSSNativeOutboxEventView:
        available_at = _as_utc(event.available_at)
        if available_at is None:
            raise OSSNativeOutboxError("Native outbox event has no availability timestamp")
        return OSSNativeOutboxEventView(
            event_id=event.id,
            workspace_id=event.workspace_id,
            status=event.status,
            attempts=event.attempts,
            available_at=available_at,
            lease_expires_at=_as_utc(event.lease_expires_at),
            completed_at=_as_utc(event.completed_at),
            last_error=event.last_error,
        )

    def _now(self, value: datetime | None) -> datetime:
        resolved = _as_utc(value if value is not None else self.clock())
        if resolved is None:
            raise ValueError("clock must return a datetime")
        return resolved

    @staticmethod
    def _safe_error(error: BaseException | str) -> str:
        text = " ".join(str(error).split())
        if not text:
            text = (
                error.__class__.__name__ if isinstance(error, BaseException) else "processing error"
            )
        inspected_text = text[:4096]
        try:
            assert_secret_free_json(
                {"message": inspected_text},
                field_name="native outbox error",
                max_bytes=8 * 1024,
            )
        except (OSSNativeSecretError, TypeError, ValueError):
            return "processing error redacted"
        return inspected_text[:MAX_ERROR_LENGTH]


__all__ = [
    "DEFAULT_BASE_BACKOFF",
    "DEFAULT_LEASE_DURATION",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_BACKOFF",
    "MAX_CLAIM_BATCH",
    "OSSNativeOutboxClaim",
    "OSSNativeOutboxError",
    "OSSNativeOutboxEventNotFoundError",
    "OSSNativeOutboxEventView",
    "OSSNativeOutboxLeaseLostError",
    "OSSNativeOutboxMetrics",
    "OSSNativeOutboxService",
    "OSSNativeOutboxTransition",
]
