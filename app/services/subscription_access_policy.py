"""Central read/write policy for the local SaaS subscription lifecycle."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.tenant import set_workspace_context
from app.models.billing import Subscription, SubscriptionStatus
from app.models.subscription_access import (
    SubscriptionAccessState,
    SubscriptionAccessStatus,
    SubscriptionAccessTransition,
)
from app.schemas.subscription_access import (
    SubscriptionAccessDecision,
    SubscriptionAccessOperation,
    SubscriptionAccessTransitionResult,
)

DEFAULT_SUBSCRIPTION_GRACE_PERIOD = timedelta(days=7)

_ALLOWED_TRANSITIONS: dict[
    SubscriptionAccessStatus, frozenset[SubscriptionAccessStatus]
] = {
    SubscriptionAccessStatus.trial: frozenset(
        {
            SubscriptionAccessStatus.active,
            SubscriptionAccessStatus.grace,
            SubscriptionAccessStatus.canceled,
            SubscriptionAccessStatus.suspended,
        }
    ),
    SubscriptionAccessStatus.active: frozenset(
        {
            SubscriptionAccessStatus.grace,
            SubscriptionAccessStatus.canceled,
            SubscriptionAccessStatus.suspended,
        }
    ),
    SubscriptionAccessStatus.grace: frozenset(
        {
            SubscriptionAccessStatus.active,
            SubscriptionAccessStatus.past_due,
            SubscriptionAccessStatus.canceled,
            SubscriptionAccessStatus.suspended,
        }
    ),
    SubscriptionAccessStatus.past_due: frozenset(
        {
            SubscriptionAccessStatus.active,
            SubscriptionAccessStatus.canceled,
            SubscriptionAccessStatus.suspended,
        }
    ),
    SubscriptionAccessStatus.canceled: frozenset(
        {
            SubscriptionAccessStatus.trial,
            SubscriptionAccessStatus.active,
            SubscriptionAccessStatus.suspended,
        }
    ),
    SubscriptionAccessStatus.suspended: frozenset(
        {
            SubscriptionAccessStatus.trial,
            SubscriptionAccessStatus.active,
            SubscriptionAccessStatus.canceled,
        }
    ),
}

_ACCESS_MATRIX: dict[SubscriptionAccessStatus, tuple[bool, bool, str]] = {
    SubscriptionAccessStatus.trial: (True, True, "subscription_trial"),
    SubscriptionAccessStatus.active: (True, True, "subscription_active"),
    SubscriptionAccessStatus.grace: (True, True, "subscription_grace"),
    SubscriptionAccessStatus.past_due: (
        True,
        False,
        "subscription_past_due_read_only",
    ),
    SubscriptionAccessStatus.canceled: (
        True,
        False,
        "subscription_canceled_read_only",
    ),
    SubscriptionAccessStatus.suspended: (False, False, "subscription_suspended"),
}


class SubscriptionAccessPolicyError(RuntimeError):
    """Base error for subscription access policy operations."""


class SubscriptionAccessNotFoundError(SubscriptionAccessPolicyError):
    """Raised when a workspace has no local subscription to project."""


class SubscriptionAccessInvalidTransitionError(SubscriptionAccessPolicyError):
    """Raised when a lifecycle transition is not part of the explicit state graph."""


class SubscriptionAccessIdempotencyConflictError(SubscriptionAccessPolicyError):
    """Raised when an idempotency key is reused for a different transition request."""


class SubscriptionAccessTimeConflictError(SubscriptionAccessPolicyError):
    """Raised when an event attempts to predate the current local projection."""


class SubscriptionAccessDeniedError(SubscriptionAccessPolicyError):
    def __init__(
        self,
        operation: SubscriptionAccessOperation,
        decision: SubscriptionAccessDecision,
    ) -> None:
        self.operation = operation
        self.decision = decision
        super().__init__(
            f"Subscription access denied for {operation.value}: {decision.reason_code}."
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return _utc(value).isoformat() if value is not None else None


def _bounded_text(value: str, *, field_name: str, max_length: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty.")
    if len(normalized) > max_length:
        raise ValueError(f"{field_name} must be at most {max_length} characters.")
    return normalized


class SubscriptionAccessPolicy:
    """Owns deterministic access decisions and serialized, auditable transitions."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        grace_period: timedelta = DEFAULT_SUBSCRIPTION_GRACE_PERIOD,
        clock: Callable[[], datetime] | None = None,
        bind_workspace: bool = True,
    ) -> None:
        if grace_period <= timedelta(0):
            raise ValueError("grace_period must be positive.")
        self.db = db
        self.grace_period = grace_period
        self.clock = clock or (lambda: datetime.now(UTC))
        self.bind_workspace = bind_workspace

    def _now(self, value: datetime | None = None) -> datetime:
        return _utc(value if value is not None else self.clock())

    async def _bind(self, workspace_id: UUID) -> None:
        if self.bind_workspace:
            await set_workspace_context(self.db, workspace_id)

    async def _state(
        self,
        workspace_id: UUID,
        *,
        for_update: bool = False,
    ) -> SubscriptionAccessState | None:
        query = select(SubscriptionAccessState).where(
            SubscriptionAccessState.workspace_id == workspace_id
        )
        if for_update:
            query = query.with_for_update()
        return await self.db.scalar(query)

    async def _subscription(
        self,
        workspace_id: UUID,
        *,
        for_update: bool = False,
    ) -> Subscription | None:
        query = select(Subscription).where(Subscription.workspace_id == workspace_id)
        if for_update:
            query = query.with_for_update()
        return await self.db.scalar(query)

    @staticmethod
    def _fingerprint(
        *,
        to_state: SubscriptionAccessStatus,
        actor_user_id: UUID | None,
        source: str,
        reason: str,
        effective_at: datetime | None,
        trial_ends_at: datetime | None,
        grace_ends_at: datetime | None,
    ) -> str:
        payload = {
            "actor_user_id": str(actor_user_id) if actor_user_id else None,
            "effective_at": _iso(effective_at),
            "grace_ends_at": _iso(grace_ends_at),
            "reason": reason,
            "source": source,
            "to_state": to_state.value,
            "trial_ends_at": _iso(trial_ends_at),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _transition_result(
        event: SubscriptionAccessTransition,
        *,
        replayed: bool,
    ) -> SubscriptionAccessTransitionResult:
        return SubscriptionAccessTransitionResult(
            event_id=event.id,
            access_state_id=event.access_state_id,
            workspace_id=event.workspace_id,
            subscription_id=event.subscription_id,
            from_state=event.from_status,
            to_state=event.to_status,
            from_version=event.from_version,
            to_version=event.to_version,
            applied=event.applied,
            replayed=replayed,
            source=event.source,
            reason=event.reason,
            actor_user_id=event.actor_user_id,
            occurred_at=_utc(event.occurred_at),
            trial_ends_at=(
                _utc(event.trial_ends_at) if event.trial_ends_at is not None else None
            ),
            grace_ends_at=(
                _utc(event.grace_ends_at) if event.grace_ends_at is not None else None
            ),
        )

    def _initial_state(
        self,
        subscription: Subscription,
        now: datetime,
    ) -> tuple[SubscriptionAccessStatus, datetime | None, datetime | None, str]:
        if subscription.status == SubscriptionStatus.trialing:
            trial_end = _utc(subscription.trial_end) if subscription.trial_end else None
            if trial_end is not None and trial_end > now:
                return (
                    SubscriptionAccessStatus.trial,
                    trial_end,
                    None,
                    "subscription_trial_bootstrapped",
                )
            return (
                SubscriptionAccessStatus.suspended,
                None,
                None,
                "subscription_trial_window_invalid",
            )
        if subscription.status == SubscriptionStatus.active:
            return SubscriptionAccessStatus.active, None, None, "subscription_active_bootstrapped"
        if subscription.status == SubscriptionStatus.past_due:
            return (
                SubscriptionAccessStatus.grace,
                None,
                now + self.grace_period,
                "subscription_past_due_grace_started",
            )
        if subscription.status == SubscriptionStatus.cancelled:
            return (
                SubscriptionAccessStatus.canceled,
                None,
                None,
                "subscription_canceled_bootstrapped",
            )
        return (
            SubscriptionAccessStatus.suspended,
            None,
            None,
            "subscription_blocked_status_bootstrapped",
        )

    async def _bootstrap(
        self,
        workspace_id: UUID,
        *,
        now: datetime,
    ) -> SubscriptionAccessState | None:
        subscription = await self._subscription(workspace_id, for_update=True)
        if subscription is None:
            return None

        state = await self._state(workspace_id, for_update=True)
        if state is not None:
            return state

        status, trial_end, grace_end, reason = self._initial_state(subscription, now)
        state = SubscriptionAccessState(
            workspace_id=workspace_id,
            subscription_id=subscription.id,
            status=status,
            state_version=1,
            status_changed_at=now,
            trial_ends_at=trial_end,
            grace_started_at=now if status == SubscriptionAccessStatus.grace else None,
            grace_ends_at=grace_end,
        )
        self.db.add(state)
        await self.db.flush()

        source = "subscription.bootstrap"
        event = SubscriptionAccessTransition(
            workspace_id=workspace_id,
            access_state_id=state.id,
            subscription_id=subscription.id,
            idempotency_key=f"subscription-bootstrap:{subscription.id}",
            request_fingerprint=self._fingerprint(
                to_state=status,
                actor_user_id=None,
                source=source,
                reason=reason,
                effective_at=now,
                trial_ends_at=trial_end,
                grace_ends_at=grace_end,
            ),
            from_status=None,
            to_status=status,
            from_version=0,
            to_version=1,
            applied=True,
            source=source,
            reason=reason,
            actor_user_id=None,
            trial_ends_at=trial_end,
            grace_ends_at=grace_end,
            occurred_at=now,
        )
        self.db.add(event)
        await self.db.flush()
        return state

    async def transition(
        self,
        workspace_id: UUID,
        to_state: SubscriptionAccessStatus,
        *,
        idempotency_key: str,
        source: str,
        reason: str,
        actor_user_id: UUID | None = None,
        effective_at: datetime | None = None,
        trial_ends_at: datetime | None = None,
        grace_ends_at: datetime | None = None,
    ) -> SubscriptionAccessTransitionResult:
        """Apply one serialized transition; exact replays return the original event."""

        await self._bind(workspace_id)
        key = _bounded_text(idempotency_key, field_name="idempotency_key", max_length=200)
        normalized_source = _bounded_text(source, field_name="source", max_length=80)
        normalized_reason = _bounded_text(reason, field_name="reason", max_length=240)
        requested_effective_at = _utc(effective_at) if effective_at is not None else None
        requested_trial_end = _utc(trial_ends_at) if trial_ends_at is not None else None
        requested_grace_end = _utc(grace_ends_at) if grace_ends_at is not None else None
        fingerprint = self._fingerprint(
            to_state=to_state,
            actor_user_id=actor_user_id,
            source=normalized_source,
            reason=normalized_reason,
            effective_at=requested_effective_at,
            trial_ends_at=requested_trial_end,
            grace_ends_at=requested_grace_end,
        )

        state = await self._state(workspace_id, for_update=True)
        if state is None:
            state = await self._bootstrap(workspace_id, now=self._now(effective_at))
        if state is None:
            raise SubscriptionAccessNotFoundError(
                f"Workspace {workspace_id} has no subscription access state."
            )

        existing = await self.db.scalar(
            select(SubscriptionAccessTransition).where(
                SubscriptionAccessTransition.workspace_id == workspace_id,
                SubscriptionAccessTransition.idempotency_key == key,
            )
        )
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise SubscriptionAccessIdempotencyConflictError(
                    "The idempotency key is already bound to another transition request."
                )
            return self._transition_result(existing, replayed=True)

        occurred_at = self._now(effective_at)
        current_changed_at = _utc(state.status_changed_at)
        latest_event_at = await self.db.scalar(
            select(SubscriptionAccessTransition.occurred_at)
            .where(SubscriptionAccessTransition.workspace_id == workspace_id)
            .order_by(
                SubscriptionAccessTransition.occurred_at.desc(),
                SubscriptionAccessTransition.id.desc(),
            )
            .limit(1)
        )
        temporal_floor = current_changed_at
        if latest_event_at is not None:
            temporal_floor = max(temporal_floor, _utc(latest_event_at))
        if occurred_at < temporal_floor:
            raise SubscriptionAccessTimeConflictError(
                "The transition effective_at predates the latest persisted access event."
            )

        from_state = state.status
        if (
            from_state == SubscriptionAccessStatus.canceled
            and to_state
            in {
                SubscriptionAccessStatus.active,
                SubscriptionAccessStatus.trial,
            }
            and occurred_at == temporal_floor
        ):
            raise SubscriptionAccessTimeConflictError(
                "The transition effective_at must be strictly after the latest "
                "persisted access event."
            )
        if to_state != from_state and to_state not in _ALLOWED_TRANSITIONS[from_state]:
            raise SubscriptionAccessInvalidTransitionError(
                f"Transition {from_state.value} -> {to_state.value} is not allowed."
            )

        current_trial_end = _utc(state.trial_ends_at) if state.trial_ends_at else None
        current_grace_start = _utc(state.grace_started_at) if state.grace_started_at else None
        current_grace_end = _utc(state.grace_ends_at) if state.grace_ends_at else None

        if to_state == SubscriptionAccessStatus.trial:
            next_trial_end = requested_trial_end or current_trial_end
            if next_trial_end is None or next_trial_end <= occurred_at:
                raise ValueError("A trial transition requires a future trial_ends_at.")
            next_grace_start = None
            next_grace_end = None
        elif to_state == SubscriptionAccessStatus.grace:
            next_trial_end = None
            next_grace_end = requested_grace_end
            if next_grace_end is None:
                next_grace_end = (
                    current_grace_end
                    if from_state == SubscriptionAccessStatus.grace
                    else occurred_at + self.grace_period
                )
            if next_grace_end is None or next_grace_end <= occurred_at:
                raise ValueError("A grace transition requires a future grace_ends_at.")
            next_grace_start = (
                current_grace_start
                if from_state == SubscriptionAccessStatus.grace
                else occurred_at
            )
        else:
            next_trial_end = None
            next_grace_start = None
            next_grace_end = None

        applied = (
            to_state != from_state
            or next_trial_end != current_trial_end
            or next_grace_start != current_grace_start
            or next_grace_end != current_grace_end
        )
        from_version = state.state_version
        to_version = from_version + 1 if applied else from_version

        if applied:
            state.status = to_state
            state.state_version = to_version
            state.status_changed_at = occurred_at
            state.trial_ends_at = next_trial_end
            state.grace_started_at = next_grace_start
            state.grace_ends_at = next_grace_end

        event = SubscriptionAccessTransition(
            workspace_id=workspace_id,
            access_state_id=state.id,
            subscription_id=state.subscription_id,
            idempotency_key=key,
            request_fingerprint=fingerprint,
            from_status=from_state,
            to_status=to_state,
            from_version=from_version,
            to_version=to_version,
            applied=applied,
            source=normalized_source,
            reason=normalized_reason,
            actor_user_id=actor_user_id,
            trial_ends_at=next_trial_end,
            grace_ends_at=next_grace_end,
            occurred_at=occurred_at,
        )
        self.db.add(event)
        await self.db.flush()
        return self._transition_result(event, replayed=False)

    async def _advance_expired_state(
        self,
        state: SubscriptionAccessState,
        *,
        now: datetime,
    ) -> bool:
        if state.status == SubscriptionAccessStatus.trial and state.trial_ends_at is not None:
            trial_end = _utc(state.trial_ends_at)
            if trial_end <= now:
                result = await self.transition(
                    state.workspace_id,
                    SubscriptionAccessStatus.suspended,
                    idempotency_key=(
                        f"subscription-policy:trial-expired:{state.id}:{trial_end.isoformat()}"
                    ),
                    source="subscription.policy",
                    reason="subscription_trial_expired",
                    effective_at=trial_end,
                )
                return result.applied and not result.replayed
        elif state.status == SubscriptionAccessStatus.grace and state.grace_ends_at is not None:
            grace_end = _utc(state.grace_ends_at)
            if grace_end <= now:
                result = await self.transition(
                    state.workspace_id,
                    SubscriptionAccessStatus.past_due,
                    idempotency_key=(
                        f"subscription-policy:grace-expired:{state.id}:{grace_end.isoformat()}"
                    ),
                    source="subscription.policy",
                    reason="subscription_grace_expired",
                    effective_at=grace_end,
                )
                return result.applied and not result.replayed
        return False

    async def _finalize_period_end_cancellation(
        self,
        subscription: Subscription,
        *,
        now: datetime,
    ) -> bool:
        if (
            not subscription.cancel_at_period_end
            or subscription.current_period_end is None
        ):
            return False

        period_end = _utc(subscription.current_period_end)
        if period_end > now:
            return False

        source_changed = (
            subscription.status != SubscriptionStatus.cancelled
            or subscription.cancel_at_period_end
            or subscription.cancelled_at is None
            or _utc(subscription.cancelled_at) != period_end
        )
        result = await self.transition(
            subscription.workspace_id,
            SubscriptionAccessStatus.canceled,
            idempotency_key=(
                "subscription-policy:period-end-canceled:"
                f"{subscription.id}:{period_end.isoformat()}"
            ),
            source="subscription.policy",
            reason="subscription_period_end_canceled",
            effective_at=period_end,
        )
        subscription.status = SubscriptionStatus.cancelled
        subscription.cancel_at_period_end = False
        subscription.cancelled_at = period_end
        await self.db.flush()
        return source_changed or (result.applied and not result.replayed)

    @staticmethod
    def _decision(
        workspace_id: UUID,
        state: SubscriptionAccessState | None,
        *,
        now: datetime,
    ) -> SubscriptionAccessDecision:
        if state is None:
            return SubscriptionAccessDecision(
                workspace_id=workspace_id,
                subscription_id=None,
                state=None,
                state_version=0,
                read_allowed=False,
                write_allowed=False,
                reason_code="subscription_missing",
                evaluated_at=now,
            )

        read_allowed, write_allowed, reason_code = _ACCESS_MATRIX[state.status]
        return SubscriptionAccessDecision(
            workspace_id=workspace_id,
            subscription_id=state.subscription_id,
            state=state.status,
            state_version=state.state_version,
            read_allowed=read_allowed,
            write_allowed=write_allowed,
            reason_code=reason_code,
            evaluated_at=now,
            trial_ends_at=_utc(state.trial_ends_at) if state.trial_ends_at else None,
            grace_ends_at=_utc(state.grace_ends_at) if state.grace_ends_at else None,
        )

    async def _evaluate(
        self,
        workspace_id: UUID,
        *,
        evaluated_at: datetime | None = None,
    ) -> tuple[SubscriptionAccessDecision, bool]:
        await self._bind(workspace_id)
        now = self._now(evaluated_at)
        subscription = await self._subscription(workspace_id, for_update=True)
        cancellation_finalized = (
            await self._finalize_period_end_cancellation(subscription, now=now)
            if subscription is not None
            else False
        )
        state = await self._state(workspace_id, for_update=True)
        bootstrapped = state is None
        if state is None:
            state = await self._bootstrap(workspace_id, now=now)
        if state is None:
            return self._decision(workspace_id, None, now=now), False

        advanced = await self._advance_expired_state(state, now=now)
        return (
            self._decision(workspace_id, state, now=now),
            cancellation_finalized or bootstrapped or advanced,
        )

    async def decide(
        self,
        workspace_id: UUID,
        *,
        evaluated_at: datetime | None = None,
    ) -> SubscriptionAccessDecision:
        """Read the persisted access projection without bootstrap or reconciliation."""

        await self._bind(workspace_id)
        now = self._now(evaluated_at)
        state = await self._state(workspace_id)
        return self._decision(workspace_id, state, now=now)

    async def reconcile(
        self,
        workspace_id: UUID,
        *,
        evaluated_at: datetime | None = None,
    ) -> SubscriptionAccessDecision:
        """Reconcile deterministic time transitions in the caller's transaction."""

        decision, _ = await self._evaluate(workspace_id, evaluated_at=evaluated_at)
        return decision

    async def require(
        self,
        workspace_id: UUID,
        operation: SubscriptionAccessOperation,
        *,
        evaluated_at: datetime | None = None,
    ) -> SubscriptionAccessDecision:
        decision, state_changed = await self._evaluate(
            workspace_id,
            evaluated_at=evaluated_at,
        )
        if state_changed:
            # Dependency failures trigger a request rollback. Commit policy-owned,
            # deterministic time transitions before raising the access denial.
            await self.db.commit()
        if not decision.allows(operation):
            raise SubscriptionAccessDeniedError(operation, decision)
        return decision

    async def audit_trail(
        self,
        workspace_id: UUID,
        *,
        limit: int = 100,
    ) -> list[SubscriptionAccessTransitionResult]:
        await self._bind(workspace_id)
        events = list(
            (
                await self.db.scalars(
                    select(SubscriptionAccessTransition)
                    .where(SubscriptionAccessTransition.workspace_id == workspace_id)
                    .order_by(
                        SubscriptionAccessTransition.to_version.asc(),
                        SubscriptionAccessTransition.occurred_at.asc(),
                        SubscriptionAccessTransition.applied.desc(),
                        SubscriptionAccessTransition.id.asc(),
                    )
                    .limit(max(1, min(limit, 500)))
                )
            ).all()
        )
        return [self._transition_result(event, replayed=False) for event in events]
