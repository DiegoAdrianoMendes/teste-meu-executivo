from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.subscription_access import (
    SubscriptionAccessState,
    SubscriptionAccessStatus,
    SubscriptionAccessTransition,
)
from app.schemas.subscription_access import SubscriptionAccessOperation
from app.services.subscription_access_policy import (
    DEFAULT_SUBSCRIPTION_GRACE_PERIOD,
    SubscriptionAccessDeniedError,
    SubscriptionAccessIdempotencyConflictError,
    SubscriptionAccessInvalidTransitionError,
    SubscriptionAccessNotFoundError,
    SubscriptionAccessPolicy,
    SubscriptionAccessTimeConflictError,
)

NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


async def _create_subscription(
    db: AsyncSession,
    workspace_id: UUID,
    *,
    status: SubscriptionStatus = SubscriptionStatus.active,
    trial_end: datetime | None = None,
    current_period_end: datetime | None = None,
    cancel_at_period_end: bool = False,
) -> Subscription:
    plan = Plan(
        name="Policy Test Plan",
        tier=PlanTier.free,
        price_monthly_cents=0,
        price_annually_cents=0,
        features={},
    )
    db.add(plan)
    await db.flush()
    subscription = Subscription(
        workspace_id=workspace_id,
        plan_id=plan.id,
        status=status,
        interval=BillingInterval.monthly,
        trial_end=trial_end,
        current_period_start=NOW,
        current_period_end=current_period_end or NOW + timedelta(days=30),
        cancel_at_period_end=cancel_at_period_end,
        amount_cents=0,
    )
    db.add(subscription)
    await db.flush()
    return subscription


async def _create_access_state(
    db: AsyncSession,
    workspace_id: UUID,
    subscription_id: UUID,
    status: SubscriptionAccessStatus,
) -> SubscriptionAccessState:
    state = SubscriptionAccessState(
        workspace_id=workspace_id,
        subscription_id=subscription_id,
        status=status,
        state_version=1,
        status_changed_at=NOW,
        trial_ends_at=(
            NOW + timedelta(days=14) if status == SubscriptionAccessStatus.trial else None
        ),
        grace_started_at=NOW if status == SubscriptionAccessStatus.grace else None,
        grace_ends_at=(
            NOW + timedelta(days=7) if status == SubscriptionAccessStatus.grace else None
        ),
    )
    db.add(state)
    await db.flush()
    return state


def test_access_status_contract_uses_provider_neutral_names() -> None:
    assert [status.value for status in SubscriptionAccessStatus] == [
        "trial",
        "active",
        "grace",
        "past_due",
        "canceled",
        "suspended",
    ]


@pytest.mark.parametrize(
    ("access_status", "read_allowed", "write_allowed", "reason_code"),
    [
        (SubscriptionAccessStatus.trial, True, True, "subscription_trial"),
        (SubscriptionAccessStatus.active, True, True, "subscription_active"),
        (SubscriptionAccessStatus.grace, True, True, "subscription_grace"),
        (
            SubscriptionAccessStatus.past_due,
            True,
            False,
            "subscription_past_due_read_only",
        ),
        (
            SubscriptionAccessStatus.canceled,
            True,
            False,
            "subscription_canceled_read_only",
        ),
        (
            SubscriptionAccessStatus.suspended,
            False,
            False,
            "subscription_suspended",
        ),
    ],
)
@pytest.mark.asyncio
async def test_read_write_matrix_is_deterministic_for_every_state(
    db_session: AsyncSession,
    auth_context,
    access_status: SubscriptionAccessStatus,
    read_allowed: bool,
    write_allowed: bool,
    reason_code: str,
) -> None:
    subscription = await _create_subscription(
        db_session,
        auth_context.workspace_id,
    )
    await _create_access_state(
        db_session,
        auth_context.workspace_id,
        subscription.id,
        access_status,
    )
    policy = SubscriptionAccessPolicy(db_session, clock=lambda: NOW)

    decision = await policy.decide(auth_context.workspace_id)

    assert decision.state == access_status
    assert decision.read_allowed is read_allowed
    assert decision.write_allowed is write_allowed
    assert decision.reason_code == reason_code
    assert decision.allows(SubscriptionAccessOperation.read) is read_allowed
    assert decision.allows(SubscriptionAccessOperation.write) is write_allowed


@pytest.mark.parametrize(
    ("billing_status", "trial_end", "expected_state"),
    [
        (
            SubscriptionStatus.trialing,
            NOW + timedelta(days=14),
            SubscriptionAccessStatus.trial,
        ),
        (SubscriptionStatus.active, None, SubscriptionAccessStatus.active),
        (SubscriptionStatus.past_due, None, SubscriptionAccessStatus.grace),
        (SubscriptionStatus.cancelled, None, SubscriptionAccessStatus.canceled),
        (SubscriptionStatus.unpaid, None, SubscriptionAccessStatus.suspended),
        (SubscriptionStatus.paused, None, SubscriptionAccessStatus.suspended),
    ],
)
@pytest.mark.asyncio
async def test_bootstrap_projects_existing_billing_status_once(
    db_session: AsyncSession,
    auth_context,
    billing_status: SubscriptionStatus,
    trial_end: datetime | None,
    expected_state: SubscriptionAccessStatus,
) -> None:
    await _create_subscription(
        db_session,
        auth_context.workspace_id,
        status=billing_status,
        trial_end=trial_end,
    )
    policy = SubscriptionAccessPolicy(db_session, clock=lambda: NOW)

    first = await policy.reconcile(auth_context.workspace_id)
    second = await policy.decide(auth_context.workspace_id)
    audit = await policy.audit_trail(auth_context.workspace_id)

    assert first.state == expected_state
    assert second.state == expected_state
    assert first.state_version == second.state_version == 1
    assert len(audit) == 1
    assert audit[0].from_state is None
    assert audit[0].to_state == expected_state
    assert audit[0].source == "subscription.bootstrap"
    if expected_state == SubscriptionAccessStatus.grace:
        assert first.grace_ends_at == NOW + DEFAULT_SUBSCRIPTION_GRACE_PERIOD


@pytest.mark.parametrize("trial_end", [None, NOW, NOW - timedelta(seconds=1)])
@pytest.mark.asyncio
async def test_invalid_or_expired_billing_trial_bootstraps_suspended(
    db_session: AsyncSession,
    auth_context,
    trial_end: datetime | None,
) -> None:
    await _create_subscription(
        db_session,
        auth_context.workspace_id,
        status=SubscriptionStatus.trialing,
        trial_end=trial_end,
    )
    policy = SubscriptionAccessPolicy(db_session, clock=lambda: NOW)

    decision = await policy.reconcile(auth_context.workspace_id)

    assert decision.state == SubscriptionAccessStatus.suspended
    assert decision.read_allowed is False
    assert decision.write_allowed is False
    audit = await policy.audit_trail(auth_context.workspace_id)
    assert audit[0].reason == "subscription_trial_window_invalid"


@pytest.mark.asyncio
async def test_missing_subscription_fails_closed_for_decide_require_and_transition(
    db_session: AsyncSession,
    auth_context,
) -> None:
    policy = SubscriptionAccessPolicy(db_session, clock=lambda: NOW)

    decision = await policy.decide(auth_context.workspace_id)

    assert decision.state is None
    assert decision.state_version == 0
    assert decision.read_allowed is False
    assert decision.write_allowed is False
    assert decision.reason_code == "subscription_missing"
    with pytest.raises(SubscriptionAccessDeniedError) as denied:
        await policy.require(auth_context.workspace_id, SubscriptionAccessOperation.read)
    assert denied.value.decision == decision
    with pytest.raises(SubscriptionAccessNotFoundError):
        await policy.transition(
            auth_context.workspace_id,
            SubscriptionAccessStatus.active,
            idempotency_key="activate-without-subscription",
            source="test",
            reason="missing_subscription",
        )


@pytest.mark.asyncio
async def test_grace_period_keeps_full_access_then_expires_to_read_only_once(
    db_session: AsyncSession,
    auth_context,
) -> None:
    await _create_subscription(db_session, auth_context.workspace_id)
    policy = SubscriptionAccessPolicy(db_session, clock=lambda: NOW)
    await policy.reconcile(auth_context.workspace_id)

    entered = await policy.transition(
        auth_context.workspace_id,
        SubscriptionAccessStatus.grace,
        idempotency_key="invoice-failed-2026-07",
        source="billing.simulator",
        reason="invoice_payment_failed",
        effective_at=NOW,
    )
    during_grace = await policy.decide(
        auth_context.workspace_id,
        evaluated_at=NOW + DEFAULT_SUBSCRIPTION_GRACE_PERIOD - timedelta(microseconds=1),
    )
    at_boundary = await policy.reconcile(
        auth_context.workspace_id,
        evaluated_at=NOW + DEFAULT_SUBSCRIPTION_GRACE_PERIOD,
    )
    repeated = await policy.reconcile(
        auth_context.workspace_id,
        evaluated_at=NOW + DEFAULT_SUBSCRIPTION_GRACE_PERIOD + timedelta(days=1),
    )
    audit = await policy.audit_trail(auth_context.workspace_id)

    assert entered.grace_ends_at == NOW + DEFAULT_SUBSCRIPTION_GRACE_PERIOD
    assert during_grace.state == SubscriptionAccessStatus.grace
    assert during_grace.read_allowed is True
    assert during_grace.write_allowed is True
    assert at_boundary.state == SubscriptionAccessStatus.past_due
    assert at_boundary.read_allowed is True
    assert at_boundary.write_allowed is False
    assert repeated.state == SubscriptionAccessStatus.past_due
    assert [event.to_state for event in audit] == [
        SubscriptionAccessStatus.active,
        SubscriptionAccessStatus.grace,
        SubscriptionAccessStatus.past_due,
    ]
    assert audit[-1].reason == "subscription_grace_expired"


@pytest.mark.asyncio
async def test_trial_expires_to_suspended_at_exact_boundary_once(
    db_session: AsyncSession,
    auth_context,
) -> None:
    trial_end = NOW + timedelta(days=1)
    await _create_subscription(
        db_session,
        auth_context.workspace_id,
        status=SubscriptionStatus.trialing,
        trial_end=trial_end,
    )
    policy = SubscriptionAccessPolicy(db_session, clock=lambda: NOW)

    before = await policy.reconcile(
        auth_context.workspace_id,
        evaluated_at=trial_end - timedelta(microseconds=1),
    )
    at_boundary = await policy.reconcile(
        auth_context.workspace_id,
        evaluated_at=trial_end,
    )
    repeated = await policy.reconcile(
        auth_context.workspace_id,
        evaluated_at=trial_end + timedelta(days=1),
    )
    audit = await policy.audit_trail(auth_context.workspace_id)

    assert before.state == SubscriptionAccessStatus.trial
    assert before.write_allowed is True
    assert at_boundary.state == SubscriptionAccessStatus.suspended
    assert at_boundary.read_allowed is False
    assert repeated.state == SubscriptionAccessStatus.suspended
    assert [event.reason for event in audit] == [
        "subscription_trial_bootstrapped",
        "subscription_trial_expired",
    ]


@pytest.mark.asyncio
async def test_period_end_cancellation_reconciles_exactly_at_boundary_once(
    db_session: AsyncSession,
    auth_context,
) -> None:
    period_end = NOW + timedelta(days=30)
    subscription = await _create_subscription(
        db_session,
        auth_context.workspace_id,
        current_period_end=period_end,
        cancel_at_period_end=True,
    )
    state = await _create_access_state(
        db_session,
        auth_context.workspace_id,
        subscription.id,
        SubscriptionAccessStatus.active,
    )
    policy = SubscriptionAccessPolicy(db_session, clock=lambda: NOW)

    before = await policy.reconcile(
        auth_context.workspace_id,
        evaluated_at=period_end - timedelta(microseconds=1),
    )
    at_boundary = await policy.reconcile(
        auth_context.workspace_id,
        evaluated_at=period_end,
    )
    after = await policy.reconcile(
        auth_context.workspace_id,
        evaluated_at=period_end + timedelta(days=1),
    )
    events = list(
        (
            await db_session.scalars(
                select(SubscriptionAccessTransition).where(
                    SubscriptionAccessTransition.access_state_id == state.id
                )
            )
        ).all()
    )

    assert before.state == SubscriptionAccessStatus.active
    assert at_boundary.state == SubscriptionAccessStatus.canceled
    assert after.state == SubscriptionAccessStatus.canceled
    assert subscription.status == SubscriptionStatus.cancelled
    assert subscription.cancel_at_period_end is False
    assert subscription.cancelled_at is not None
    assert subscription.cancelled_at.replace(tzinfo=UTC) == period_end
    assert len(events) == 1
    assert events[0].idempotency_key == (
        f"subscription-policy:period-end-canceled:{subscription.id}:"
        f"{period_end.isoformat()}"
    )
    assert events[0].reason == "subscription_period_end_canceled"


@pytest.mark.asyncio
async def test_exact_idempotent_replay_returns_original_event_without_new_version(
    db_session: AsyncSession,
    auth_context,
) -> None:
    await _create_subscription(db_session, auth_context.workspace_id)
    policy = SubscriptionAccessPolicy(db_session, clock=lambda: NOW)
    await policy.reconcile(auth_context.workspace_id)
    command = dict(
        idempotency_key="invoice-payment-failed-0001",
        source="billing.simulator",
        reason="invoice_payment_failed",
        actor_user_id=auth_context.user_id,
    )

    first = await policy.transition(
        auth_context.workspace_id,
        SubscriptionAccessStatus.grace,
        **command,
    )
    replay = await policy.transition(
        auth_context.workspace_id,
        SubscriptionAccessStatus.grace,
        **command,
    )
    audit = await policy.audit_trail(auth_context.workspace_id)

    assert first.event_id == replay.event_id
    assert first.to_version == replay.to_version == 2
    assert first.grace_ends_at == replay.grace_ends_at
    assert first.replayed is False
    assert replay.replayed is True
    assert len(audit) == 2


@pytest.mark.asyncio
async def test_conflicting_idempotency_replay_is_rejected_without_mutation(
    db_session: AsyncSession,
    auth_context,
) -> None:
    await _create_subscription(db_session, auth_context.workspace_id)
    policy = SubscriptionAccessPolicy(db_session, clock=lambda: NOW)
    await policy.reconcile(auth_context.workspace_id)
    await policy.transition(
        auth_context.workspace_id,
        SubscriptionAccessStatus.grace,
        idempotency_key="billing-event-0001",
        source="billing.simulator",
        reason="invoice_payment_failed",
    )

    with pytest.raises(SubscriptionAccessIdempotencyConflictError):
        await policy.transition(
            auth_context.workspace_id,
            SubscriptionAccessStatus.canceled,
            idempotency_key="billing-event-0001",
            source="billing.simulator",
            reason="subscription_canceled",
        )

    decision = await policy.decide(auth_context.workspace_id)
    audit = await policy.audit_trail(auth_context.workspace_id)
    assert decision.state == SubscriptionAccessStatus.grace
    assert decision.state_version == 2
    assert len(audit) == 2


@pytest.mark.asyncio
async def test_same_state_noop_is_audited_and_replay_does_not_duplicate_it(
    db_session: AsyncSession,
    auth_context,
) -> None:
    await _create_subscription(db_session, auth_context.workspace_id)
    policy = SubscriptionAccessPolicy(db_session, clock=lambda: NOW)
    await policy.reconcile(auth_context.workspace_id)

    first = await policy.transition(
        auth_context.workspace_id,
        SubscriptionAccessStatus.active,
        idempotency_key="active-confirmation-0001",
        source="billing.simulator",
        reason="payment_confirmed",
    )
    replay = await policy.transition(
        auth_context.workspace_id,
        SubscriptionAccessStatus.active,
        idempotency_key="active-confirmation-0001",
        source="billing.simulator",
        reason="payment_confirmed",
    )
    audit = await policy.audit_trail(auth_context.workspace_id)

    assert first.applied is False
    assert first.from_version == first.to_version == 1
    assert replay.event_id == first.event_id
    assert replay.replayed is True
    assert len(audit) == 2
    assert [event.source for event in audit] == [
        "subscription.bootstrap",
        "billing.simulator",
    ]


@pytest.mark.asyncio
async def test_grace_extension_is_versioned_without_resetting_its_start(
    db_session: AsyncSession,
    auth_context,
) -> None:
    await _create_subscription(db_session, auth_context.workspace_id)
    policy = SubscriptionAccessPolicy(db_session, clock=lambda: NOW)
    await policy.reconcile(auth_context.workspace_id)
    first_end = NOW + timedelta(days=2)
    extended_end = NOW + timedelta(days=5)

    await policy.transition(
        auth_context.workspace_id,
        SubscriptionAccessStatus.grace,
        idempotency_key="grace-start-0001",
        source="billing.simulator",
        reason="invoice_payment_failed",
        effective_at=NOW,
        grace_ends_at=first_end,
    )
    extended = await policy.transition(
        auth_context.workspace_id,
        SubscriptionAccessStatus.grace,
        idempotency_key="grace-extension-0001",
        source="billing.operator",
        reason="grace_extension_approved",
        actor_user_id=auth_context.user_id,
        effective_at=NOW + timedelta(days=1),
        grace_ends_at=extended_end,
    )
    state = await db_session.get(SubscriptionAccessState, extended.access_state_id)

    assert extended.applied is True
    assert extended.from_state == extended.to_state == SubscriptionAccessStatus.grace
    assert extended.from_version == 2
    assert extended.to_version == 3
    assert extended.grace_ends_at == extended_end
    assert state is not None
    assert state.grace_started_at.replace(tzinfo=UTC) == NOW


@pytest.mark.asyncio
async def test_invalid_transition_and_backdated_event_leave_audit_unchanged(
    db_session: AsyncSession,
    auth_context,
) -> None:
    await _create_subscription(
        db_session,
        auth_context.workspace_id,
        status=SubscriptionStatus.trialing,
        trial_end=NOW + timedelta(days=14),
    )
    policy = SubscriptionAccessPolicy(db_session, clock=lambda: NOW)
    await policy.reconcile(auth_context.workspace_id)

    with pytest.raises(SubscriptionAccessInvalidTransitionError):
        await policy.transition(
            auth_context.workspace_id,
            SubscriptionAccessStatus.past_due,
            idempotency_key="invalid-trial-past-due",
            source="test",
            reason="invalid_transition",
        )
    with pytest.raises(SubscriptionAccessTimeConflictError):
        await policy.transition(
            auth_context.workspace_id,
            SubscriptionAccessStatus.active,
            idempotency_key="backdated-activation",
            source="test",
            reason="backdated_transition",
            effective_at=NOW - timedelta(seconds=1),
        )

    audit = await policy.audit_trail(auth_context.workspace_id)
    assert len(audit) == 1
    assert audit[0].to_state == SubscriptionAccessStatus.trial


@pytest.mark.asyncio
async def test_transition_windows_are_validated_before_audit(
    db_session: AsyncSession,
    auth_context,
) -> None:
    await _create_subscription(db_session, auth_context.workspace_id)
    policy = SubscriptionAccessPolicy(db_session, clock=lambda: NOW)
    await policy.reconcile(auth_context.workspace_id)

    with pytest.raises(ValueError, match="future grace_ends_at"):
        await policy.transition(
            auth_context.workspace_id,
            SubscriptionAccessStatus.grace,
            idempotency_key="invalid-grace-window",
            source="test",
            reason="invalid_window",
            effective_at=NOW,
            grace_ends_at=NOW,
        )
    await policy.transition(
        auth_context.workspace_id,
        SubscriptionAccessStatus.canceled,
        idempotency_key="cancel-before-retrial",
        source="test",
        reason="canceled",
        effective_at=NOW,
    )
    with pytest.raises(ValueError, match="future trial_ends_at"):
        await policy.transition(
            auth_context.workspace_id,
            SubscriptionAccessStatus.trial,
            idempotency_key="invalid-trial-window",
            source="test",
            reason="invalid_window",
            effective_at=NOW + timedelta(seconds=1),
            trial_ends_at=NOW + timedelta(seconds=1),
        )

    audit = await policy.audit_trail(auth_context.workspace_id)
    assert [event.reason for event in audit] == [
        "subscription_active_bootstrapped",
        "canceled",
    ]


@pytest.mark.asyncio
async def test_canceled_subscription_can_start_a_new_bounded_trial(
    db_session: AsyncSession,
    auth_context,
) -> None:
    await _create_subscription(db_session, auth_context.workspace_id)
    policy = SubscriptionAccessPolicy(db_session, clock=lambda: NOW)
    await policy.reconcile(auth_context.workspace_id)
    await policy.transition(
        auth_context.workspace_id,
        SubscriptionAccessStatus.canceled,
        idempotency_key="cancel-before-new-trial",
        source="billing.operator",
        reason="subscription_canceled",
        effective_at=NOW,
    )
    trial_end = NOW + timedelta(days=5)

    restarted = await policy.transition(
        auth_context.workspace_id,
        SubscriptionAccessStatus.trial,
        idempotency_key="new-trial-0001",
        source="billing.operator",
        reason="new_trial_approved",
        actor_user_id=auth_context.user_id,
        effective_at=NOW + timedelta(minutes=1),
        trial_ends_at=trial_end,
    )
    decision = await policy.decide(
        auth_context.workspace_id,
        evaluated_at=NOW + timedelta(minutes=2),
    )

    assert restarted.from_state == SubscriptionAccessStatus.canceled
    assert restarted.to_state == SubscriptionAccessStatus.trial
    assert restarted.trial_ends_at == trial_end
    assert restarted.grace_ends_at is None
    assert decision.state == SubscriptionAccessStatus.trial
    assert decision.write_allowed is True


@pytest.mark.parametrize(
    "to_state",
    [SubscriptionAccessStatus.active, SubscriptionAccessStatus.trial],
)
@pytest.mark.parametrize(
    ("offset", "should_succeed"),
    [
        pytest.param(timedelta(seconds=-1), False, id="before"),
        pytest.param(timedelta(0), False, id="exact"),
        pytest.param(timedelta(seconds=1), True, id="after"),
    ],
)
@pytest.mark.asyncio
async def test_canceled_reentry_requires_strictly_later_event_time(
    db_session: AsyncSession,
    auth_context,
    to_state: SubscriptionAccessStatus,
    offset: timedelta,
    should_succeed: bool,
) -> None:
    await _create_subscription(db_session, auth_context.workspace_id)
    policy = SubscriptionAccessPolicy(db_session, clock=lambda: NOW)
    await policy.reconcile(auth_context.workspace_id)
    temporal_floor = NOW + timedelta(minutes=1)
    await policy.transition(
        auth_context.workspace_id,
        SubscriptionAccessStatus.canceled,
        idempotency_key=f"strict-reentry-cancel-{to_state.value}-{offset.total_seconds()}",
        source="test",
        reason="subscription_canceled",
        effective_at=temporal_floor,
    )
    effective_at = temporal_floor + offset
    kwargs = (
        {"trial_ends_at": effective_at + timedelta(days=5)}
        if to_state == SubscriptionAccessStatus.trial
        else {}
    )

    if should_succeed:
        reentry_key = f"strict-reentry-{to_state.value}-{offset.total_seconds()}"
        result = await policy.transition(
            auth_context.workspace_id,
            to_state,
            idempotency_key=reentry_key,
            source="test",
            reason="subscription_reentered",
            effective_at=effective_at,
            **kwargs,
        )
        assert result.from_state == SubscriptionAccessStatus.canceled
        assert result.to_state == to_state
        assert result.occurred_at == effective_at
        await policy.transition(
            auth_context.workspace_id,
            SubscriptionAccessStatus.canceled,
            idempotency_key=(
                f"strict-reentry-later-cancel-{to_state.value}-{offset.total_seconds()}"
            ),
            source="test",
            reason="subscription_canceled_again",
            effective_at=effective_at + timedelta(seconds=1),
        )
        replay = await policy.transition(
            auth_context.workspace_id,
            to_state,
            idempotency_key=reentry_key,
            source="test",
            reason="subscription_reentered",
            effective_at=effective_at,
            **kwargs,
        )
        decision = await policy.decide(auth_context.workspace_id)

        assert replay.replayed is True
        assert replay.event_id == result.event_id
        assert decision.state == SubscriptionAccessStatus.canceled
    else:
        with pytest.raises(
            SubscriptionAccessTimeConflictError,
            match="latest persisted access event",
        ):
            await policy.transition(
                auth_context.workspace_id,
                to_state,
                idempotency_key=(
                    f"strict-reentry-{to_state.value}-{offset.total_seconds()}"
                ),
                source="test",
                reason="subscription_reentered",
                effective_at=effective_at,
                **kwargs,
            )
        decision = await policy.decide(auth_context.workspace_id)
        assert decision.state == SubscriptionAccessStatus.canceled


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("idempotency_key", "   ", "idempotency_key must not be empty"),
        ("source", "s" * 81, "source must be at most 80 characters"),
        ("reason", "r" * 241, "reason must be at most 240 characters"),
    ],
)
@pytest.mark.asyncio
async def test_transition_text_boundaries_fail_before_persistence(
    db_session: AsyncSession,
    auth_context,
    field: str,
    value: str,
    error: str,
) -> None:
    await _create_subscription(db_session, auth_context.workspace_id)
    policy = SubscriptionAccessPolicy(db_session, clock=lambda: NOW)
    await policy.reconcile(auth_context.workspace_id)
    command = {
        "idempotency_key": "bounded-command-0001",
        "source": "test",
        "reason": "boundary_validation",
    }
    command[field] = value

    with pytest.raises(ValueError, match=error):
        await policy.transition(
            auth_context.workspace_id,
            SubscriptionAccessStatus.canceled,
            **command,
        )

    assert len(await policy.audit_trail(auth_context.workspace_id)) == 1


@pytest.mark.parametrize(
    "blocked_state",
    [SubscriptionAccessStatus.canceled, SubscriptionAccessStatus.suspended],
)
@pytest.mark.asyncio
async def test_explicit_reactivation_restores_read_and_write_access(
    db_session: AsyncSession,
    auth_context,
    blocked_state: SubscriptionAccessStatus,
) -> None:
    await _create_subscription(db_session, auth_context.workspace_id)
    policy = SubscriptionAccessPolicy(db_session, clock=lambda: NOW)
    await policy.reconcile(auth_context.workspace_id)
    await policy.transition(
        auth_context.workspace_id,
        blocked_state,
        idempotency_key=f"block-{blocked_state.value}",
        source="billing.operator",
        reason=f"workspace_{blocked_state.value}",
        actor_user_id=auth_context.user_id,
    )

    reactivated = await policy.transition(
        auth_context.workspace_id,
        SubscriptionAccessStatus.active,
        idempotency_key=f"reactivate-{blocked_state.value}",
        source="billing.operator",
        reason="payment_recovered",
        actor_user_id=auth_context.user_id,
        effective_at=(
            NOW + timedelta(seconds=1)
            if blocked_state == SubscriptionAccessStatus.canceled
            else NOW
        ),
    )
    decision = await policy.require(
        auth_context.workspace_id,
        SubscriptionAccessOperation.write,
    )

    assert reactivated.from_state == blocked_state
    assert reactivated.to_state == SubscriptionAccessStatus.active
    assert decision.read_allowed is True
    assert decision.write_allowed is True


@pytest.mark.asyncio
async def test_audit_trail_preserves_actor_source_reason_and_versions(
    db_session: AsyncSession,
    auth_context,
) -> None:
    subscription = await _create_subscription(db_session, auth_context.workspace_id)
    policy = SubscriptionAccessPolicy(db_session, clock=lambda: NOW)
    await policy.reconcile(auth_context.workspace_id)

    result = await policy.transition(
        auth_context.workspace_id,
        SubscriptionAccessStatus.canceled,
        idempotency_key="operator-cancel-0001",
        source="billing.operator",
        reason="customer_requested_cancellation",
        actor_user_id=auth_context.user_id,
        effective_at=NOW + timedelta(minutes=1),
    )
    audit = await policy.audit_trail(auth_context.workspace_id)

    assert result.subscription_id == subscription.id
    assert result.from_state == SubscriptionAccessStatus.active
    assert result.to_state == SubscriptionAccessStatus.canceled
    assert result.from_version == 1
    assert result.to_version == 2
    assert result.actor_user_id == auth_context.user_id
    assert result.source == "billing.operator"
    assert result.reason == "customer_requested_cancellation"
    assert [event.to_version for event in audit] == [1, 2]


def test_grace_period_must_be_positive() -> None:
    with pytest.raises(ValueError, match="grace_period must be positive"):
        SubscriptionAccessPolicy(None, grace_period=timedelta(0))  # type: ignore[arg-type]
