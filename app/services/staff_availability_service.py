"""Transactional workflow and deterministic routing for staff availability."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.core.rbac import has_effective_permission
from app.db.advisory import acquire_transaction_advisory_lock
from app.models.omnichannel_v6 import WorkspaceTeam
from app.models.staff_availability import (
    AvailabilityAuditAction,
    AvailabilityChangeType,
    AvailabilityExceptionKind,
    AvailabilityExceptionScope,
    AvailabilityMode,
    AvailabilityRedirectKind,
    AvailabilityRequestStatus,
    StaffAvailabilityAuditEvent,
    StaffAvailabilityChangeRequest,
    StaffAvailabilityException,
    StaffAvailabilityProfile,
    StaffRecurringAvailability,
)
from app.models.user import AgentType, User
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.schemas.staff_availability import (
    CreateAvailabilityExceptionChange,
    DeleteAvailabilityExceptionChange,
    EffectiveStaffAvailabilityRead,
    RecurringAvailabilitySlotRead,
    RedirectTargetWrite,
    ReplaceAvailabilityConfigurationChange,
    ReplaceRecurringScheduleChange,
    ResponsibilityResolutionRead,
    SetAbsenceRoutingChange,
    StaffAvailabilityAuditEventRead,
    StaffAvailabilityChangePayload,
    StaffAvailabilityChangeRequestCreate,
    StaffAvailabilityExceptionRead,
    StaffAvailabilityProfileRead,
    StaffAvailabilityStateRead,
    UpdateAvailabilityExceptionChange,
)

_CHANGE_ADAPTER = TypeAdapter(StaffAvailabilityChangePayload)
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{7,179}$")
_POLICY_VERSION = "staff-availability-approval-v1"
_EXCEPTION_PRIORITY = {
    AvailabilityExceptionKind.unavailable: 10,
    AvailabilityExceptionKind.holiday: 20,
    AvailabilityExceptionKind.vacation: 30,
    AvailabilityExceptionKind.available_override: 40,
}


class AvailabilityDomainError(RuntimeError):
    """Base error surfaced by the endpoint without leaking persistence details."""


class AvailabilityNotFoundError(AvailabilityDomainError):
    pass


class AvailabilityAuthorizationError(AvailabilityDomainError):
    pass


class AvailabilityConflictError(AvailabilityDomainError):
    pass


class AvailabilityValidationError(AvailabilityDomainError):
    pass


def _aware_utc(value: datetime | None = None) -> datetime:
    candidate = value or datetime.now(UTC)
    if candidate.tzinfo is None or candidate.utcoffset() is None:
        raise AvailabilityValidationError("Datetime values must include a UTC offset.")
    return candidate.astimezone(UTC)


def _loaded_utc(value: datetime) -> datetime:
    """SQLite drops offsets; production PostgreSQL preserves them."""

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _window_contains(
    instant: datetime,
    starts_at: datetime | None,
    ends_at: datetime | None,
) -> bool:
    if starts_at is not None and instant < _loaded_utc(starts_at):
        return False
    return ends_at is None or instant < _loaded_utc(ends_at)


def _iso(value: datetime | None) -> str | None:
    return _loaded_utc(value).isoformat() if value is not None else None


class StaffAvailabilityService:
    """Persist proposals, enforce four-eyes approval, and resolve routing decisions."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def submit_change(
        self,
        *,
        workspace_id: UUID,
        actor: AuthContext,
        payload: StaffAvailabilityChangeRequestCreate,
        idempotency_key: str,
    ) -> StaffAvailabilityChangeRequest:
        actor_membership = await self._actor_membership(workspace_id, actor)
        normalized_key = idempotency_key.strip()
        if not _IDEMPOTENCY_KEY.fullmatch(normalized_key):
            raise AvailabilityValidationError(
                "Idempotency-Key must contain 8-180 safe printable characters."
            )

        await acquire_transaction_advisory_lock(
            self.db,
            namespace="staff_availability_request",
            value=f"{workspace_id}:{actor.user_id}:{normalized_key}",
        )
        proposal = payload.proposed_change
        proposed_change = proposal.model_dump(mode="json", by_alias=False)
        existing = await self.db.scalar(
            select(StaffAvailabilityChangeRequest).where(
                StaffAvailabilityChangeRequest.workspace_id == workspace_id,
                StaffAvailabilityChangeRequest.requested_by_user_id == actor.user_id,
                StaffAvailabilityChangeRequest.idempotency_key == normalized_key,
            )
        )
        if existing is not None:
            retry_hash = self._request_hash(
                target_user_id=payload.target_user_id,
                proposed_change=proposed_change,
                base_version=existing.base_version,
                reason=payload.reason,
            )
            if existing.request_hash != retry_hash:
                raise AvailabilityConflictError(
                    "Idempotency-Key was already used with a different proposal."
                )
            return existing

        target_membership, target_entity_id = await self._validate_proposal_target(
            workspace_id=workspace_id,
            actor_membership=actor_membership,
            target_user_id=payload.target_user_id,
            proposal=proposal,
        )
        self._assert_can_submit(
            actor_membership=actor_membership,
            target_membership=target_membership,
        )

        base_version = await self._current_base_version(
            workspace_id=workspace_id,
            target_user_id=payload.target_user_id,
            proposal=proposal,
        )
        if payload.expected_version is not None and payload.expected_version != base_version:
            raise AvailabilityConflictError(
                f"Expected version {payload.expected_version}, current version is {base_version}."
            )

        request_hash = self._request_hash(
            target_user_id=payload.target_user_id,
            proposed_change=proposed_change,
            base_version=base_version,
            reason=payload.reason,
        )
        request = StaffAvailabilityChangeRequest(
            workspace_id=workspace_id,
            target_user_id=payload.target_user_id,
            requested_by_user_id=actor.user_id,
            change_type=AvailabilityChangeType(proposal.change_type),
            status=AvailabilityRequestStatus.pending,
            proposed_change=proposed_change,
            target_entity_id=target_entity_id,
            base_version=base_version,
            idempotency_key=normalized_key,
            request_hash=request_hash,
            reason=payload.reason,
        )
        self.db.add(request)
        await self.db.flush()
        await self._audit(
            workspace_id=workspace_id,
            staff_user_id=payload.target_user_id,
            request=request,
            actor_user_id=actor.user_id,
            action=AvailabilityAuditAction.request_submitted,
            entity_type="change_request",
            entity_id=request.id,
            summary="Availability change submitted for human approval.",
            before_data=None,
            after_data=self._request_snapshot(request),
        )
        return request

    async def approve_request(
        self,
        *,
        workspace_id: UUID,
        request_id: UUID,
        actor: AuthContext,
        note: str | None = None,
    ) -> StaffAvailabilityChangeRequest:
        request = await self._request(workspace_id, request_id)
        lock_target = str(request.target_user_id or request.target_entity_id or "workspace")
        await acquire_transaction_advisory_lock(
            self.db,
            namespace="staff_availability_aggregate",
            value=f"{workspace_id}:{lock_target}",
        )
        request = await self._request(workspace_id, request_id, for_update=True)
        self._assert_pending(request)
        actor_membership = await self._actor_membership(workspace_id, actor)
        target_membership = (
            await self._active_membership(workspace_id, request.target_user_id)
            if request.target_user_id is not None
            else None
        )
        decision_policy = self._approval_policy_evidence(
            request=request,
            actor_membership=actor_membership,
            target_membership=target_membership,
        )

        proposal = self._parse_stored_change(request.proposed_change)
        profile = None
        if request.target_user_id is not None:
            profile = await self._profile(
                workspace_id,
                request.target_user_id,
                for_update=True,
            )
            current_version = profile.version if profile is not None else 0
            if current_version != request.base_version:
                raise AvailabilityConflictError(
                    "The staff availability aggregate changed while this request was pending."
                )

        applied_version = await self._apply_approved_change(
            workspace_id=workspace_id,
            request=request,
            proposal=proposal,
            profile=profile,
            actor_user_id=actor.user_id,
        )
        before_request = self._request_snapshot(request)
        request.status = AvailabilityRequestStatus.approved
        request.resolved_by_user_id = actor.user_id
        request.resolved_at = datetime.now(UTC)
        request.decision_note = note
        request.decision_policy = decision_policy
        request.applied_version = applied_version
        await self.db.flush()
        await self._audit(
            workspace_id=workspace_id,
            staff_user_id=request.target_user_id,
            request=request,
            actor_user_id=actor.user_id,
            action=AvailabilityAuditAction.request_approved,
            entity_type="change_request",
            entity_id=request.id,
            summary="Availability change approved and applied atomically.",
            before_data=before_request,
            after_data=self._request_snapshot(request),
        )
        return request

    async def reject_request(
        self,
        *,
        workspace_id: UUID,
        request_id: UUID,
        actor: AuthContext,
        note: str | None = None,
    ) -> StaffAvailabilityChangeRequest:
        request = await self._request(workspace_id, request_id, for_update=True)
        self._assert_pending(request)
        actor_membership = await self._actor_membership(workspace_id, actor)
        target_membership = (
            await self._active_membership(workspace_id, request.target_user_id)
            if request.target_user_id is not None
            else None
        )
        decision_policy = self._approval_policy_evidence(
            request=request,
            actor_membership=actor_membership,
            target_membership=target_membership,
        )
        before = self._request_snapshot(request)
        request.status = AvailabilityRequestStatus.rejected
        request.resolved_by_user_id = actor.user_id
        request.resolved_at = datetime.now(UTC)
        request.decision_note = note
        request.decision_policy = decision_policy
        await self.db.flush()
        await self._audit(
            workspace_id=workspace_id,
            staff_user_id=request.target_user_id,
            request=request,
            actor_user_id=actor.user_id,
            action=AvailabilityAuditAction.request_rejected,
            entity_type="change_request",
            entity_id=request.id,
            summary="Availability change rejected without changing effective state.",
            before_data=before,
            after_data=self._request_snapshot(request),
        )
        return request

    async def cancel_request(
        self,
        *,
        workspace_id: UUID,
        request_id: UUID,
        actor: AuthContext,
        note: str | None = None,
    ) -> StaffAvailabilityChangeRequest:
        request = await self._request(workspace_id, request_id, for_update=True)
        self._assert_pending(request)
        await self._actor_membership(workspace_id, actor)
        if request.requested_by_user_id != actor.user_id:
            raise AvailabilityAuthorizationError(
                "Only the requester can cancel a pending availability change."
            )
        before = self._request_snapshot(request)
        request.status = AvailabilityRequestStatus.cancelled
        request.resolved_by_user_id = actor.user_id
        request.resolved_at = datetime.now(UTC)
        request.decision_note = note
        request.decision_policy = {
            "policyVersion": _POLICY_VERSION,
            "authorization": "requester_cancellation",
        }
        await self.db.flush()
        await self._audit(
            workspace_id=workspace_id,
            staff_user_id=request.target_user_id,
            request=request,
            actor_user_id=actor.user_id,
            action=AvailabilityAuditAction.request_cancelled,
            entity_type="change_request",
            entity_id=request.id,
            summary="Pending availability change cancelled by its requester.",
            before_data=before,
            after_data=self._request_snapshot(request),
        )
        return request

    async def get_request(
        self,
        *,
        workspace_id: UUID,
        request_id: UUID,
        actor: AuthContext,
    ) -> StaffAvailabilityChangeRequest:
        request = await self._request(workspace_id, request_id)
        membership = await self._actor_membership(workspace_id, actor)
        if not self._can_view_all_requests(membership) and actor.user_id not in {
            request.requested_by_user_id,
            request.target_user_id,
        }:
            raise AvailabilityAuthorizationError("This availability request is not visible to you.")
        return request

    async def list_requests(
        self,
        *,
        workspace_id: UUID,
        actor: AuthContext,
        request_status: AvailabilityRequestStatus | None = None,
        target_user_id: UUID | None = None,
        limit: int = 100,
    ) -> list[StaffAvailabilityChangeRequest]:
        membership = await self._actor_membership(workspace_id, actor)
        query = select(StaffAvailabilityChangeRequest).where(
            StaffAvailabilityChangeRequest.workspace_id == workspace_id
        )
        if request_status is not None:
            query = query.where(StaffAvailabilityChangeRequest.status == request_status)
        if target_user_id is not None:
            query = query.where(StaffAvailabilityChangeRequest.target_user_id == target_user_id)
        if not self._can_view_all_requests(membership):
            query = query.where(
                or_(
                    StaffAvailabilityChangeRequest.requested_by_user_id == actor.user_id,
                    StaffAvailabilityChangeRequest.target_user_id == actor.user_id,
                )
            )
        result = await self.db.scalars(
            query.order_by(StaffAvailabilityChangeRequest.created_at.desc()).limit(
                max(1, min(limit, 500))
            )
        )
        return list(result.all())

    async def get_state(
        self,
        *,
        workspace_id: UUID,
        staff_user_id: UUID,
        actor: AuthContext,
        include_past: bool = False,
    ) -> StaffAvailabilityStateRead:
        target = await self._active_membership(workspace_id, staff_user_id)
        actor_membership = await self._actor_membership(workspace_id, actor)
        self._assert_can_view_staff(actor_membership, target)
        profile = await self._profile(workspace_id, staff_user_id)
        slots: list[StaffRecurringAvailability] = []
        if profile is not None:
            result = await self.db.scalars(
                select(StaffRecurringAvailability)
                .where(
                    StaffRecurringAvailability.workspace_id == workspace_id,
                    StaffRecurringAvailability.profile_id == profile.id,
                )
                .order_by(
                    StaffRecurringAvailability.weekday.asc(),
                    StaffRecurringAvailability.starts_at.asc(),
                )
            )
            slots = list(result.all())
        exception_query = select(StaffAvailabilityException).where(
            StaffAvailabilityException.workspace_id == workspace_id,
            StaffAvailabilityException.deleted_at.is_(None),
            or_(
                StaffAvailabilityException.staff_user_id == staff_user_id,
                StaffAvailabilityException.scope == AvailabilityExceptionScope.workspace,
            ),
        )
        if not include_past:
            exception_query = exception_query.where(
                StaffAvailabilityException.ends_at > datetime.now(UTC)
            )
        exceptions = list(
            (
                await self.db.scalars(
                    exception_query.order_by(StaffAvailabilityException.starts_at.asc())
                )
            ).all()
        )
        return StaffAvailabilityStateRead(
            workspace_id=workspace_id,
            staff_user_id=staff_user_id,
            profile=(
                StaffAvailabilityProfileRead.model_validate(profile)
                if profile is not None
                else None
            ),
            recurring_slots=[RecurringAvailabilitySlotRead.model_validate(slot) for slot in slots],
            exceptions=[StaffAvailabilityExceptionRead.model_validate(item) for item in exceptions],
        )

    async def list_audit_history(
        self,
        *,
        workspace_id: UUID,
        actor: AuthContext,
        staff_user_id: UUID | None = None,
        request_id: UUID | None = None,
        limit: int = 200,
    ) -> list[StaffAvailabilityAuditEventRead]:
        membership = await self._actor_membership(workspace_id, actor)
        can_view_audit = has_effective_permission(
            membership.role,
            "audit",
            "view",
            membership.permission_grants,
            membership.permission_denials,
        )
        if not can_view_audit and staff_user_id != actor.user_id:
            raise AvailabilityAuthorizationError(
                "Workspace audit permission or self scope is required."
            )
        query = select(StaffAvailabilityAuditEvent).where(
            StaffAvailabilityAuditEvent.workspace_id == workspace_id
        )
        if staff_user_id is not None:
            query = query.where(StaffAvailabilityAuditEvent.staff_user_id == staff_user_id)
        elif not can_view_audit:
            query = query.where(StaffAvailabilityAuditEvent.staff_user_id == actor.user_id)
        if request_id is not None:
            query = query.where(StaffAvailabilityAuditEvent.request_id == request_id)
        rows = await self.db.scalars(
            query.order_by(StaffAvailabilityAuditEvent.occurred_at.desc()).limit(
                max(1, min(limit, 1000))
            )
        )
        return [StaffAvailabilityAuditEventRead.model_validate(row) for row in rows.all()]

    async def evaluate(
        self,
        *,
        workspace_id: UUID,
        staff_user_id: UUID,
        at: datetime | None = None,
    ) -> EffectiveStaffAvailabilityRead:
        instant = _aware_utc(at)
        await self._active_membership(workspace_id, staff_user_id)
        profile = await self._profile(workspace_id, staff_user_id)
        timezone_name = profile.timezone if profile is not None else "UTC"
        local = instant.astimezone(ZoneInfo(timezone_name))

        exception_rows = await self.db.scalars(
            select(StaffAvailabilityException).where(
                StaffAvailabilityException.workspace_id == workspace_id,
                StaffAvailabilityException.deleted_at.is_(None),
                StaffAvailabilityException.starts_at <= instant,
                StaffAvailabilityException.ends_at > instant,
                or_(
                    StaffAvailabilityException.staff_user_id == staff_user_id,
                    StaffAvailabilityException.scope == AvailabilityExceptionScope.workspace,
                ),
            )
        )
        exceptions = list(exception_rows.all())
        active_exception = max(
            exceptions,
            default=None,
            key=lambda item: (
                _EXCEPTION_PRIORITY[item.kind],
                item.scope is AvailabilityExceptionScope.staff,
                _loaded_utc(item.starts_at),
                str(item.id),
            ),
        )

        configured = profile is not None or active_exception is not None
        away_mode_active = (
            profile is not None
            and profile.availability_mode is AvailabilityMode.away
            and _window_contains(
                instant,
                profile.availability_starts_at,
                profile.availability_ends_at,
            )
        )
        if away_mode_active:
            is_available = False
            reason = (
                "scheduled_away"
                if profile.availability_starts_at is not None
                or profile.availability_ends_at is not None
                else "manual_away"
            )
        elif (
            active_exception is not None
            and active_exception.kind is AvailabilityExceptionKind.available_override
        ):
            is_available = True
            reason = "available_override"
        elif active_exception is not None:
            is_available = False
            reason = active_exception.kind.value
        elif profile is None or not profile.schedule_configured:
            is_available = True
            reason = "schedule_not_configured"
        else:
            slots = await self.db.scalars(
                select(StaffRecurringAvailability).where(
                    StaffRecurringAvailability.workspace_id == workspace_id,
                    StaffRecurringAvailability.profile_id == profile.id,
                    StaffRecurringAvailability.weekday == local.weekday(),
                )
            )
            local_date = local.date()
            local_time = local.time().replace(tzinfo=None)
            in_slot = any(
                slot.starts_at <= local_time < slot.ends_at
                and (slot.valid_from is None or slot.valid_from <= local_date)
                and (slot.valid_until is None or slot.valid_until >= local_date)
                for slot in slots.all()
            )
            is_available = in_slot
            reason = "recurring_schedule" if in_slot else "outside_recurring_schedule"

        absence_message = None
        redirect_kind = None
        redirect_user_id = None
        redirect_team_id = None
        auto_reassign = False
        if not is_available:
            if active_exception is not None and active_exception.absence_message:
                absence_message = active_exception.absence_message
            elif (
                profile is not None
                and profile.absence_message
                and _window_contains(
                    instant,
                    profile.absence_message_starts_at,
                    profile.absence_message_ends_at,
                )
            ):
                absence_message = profile.absence_message

            if active_exception is not None and active_exception.redirect_kind is not None:
                redirect_kind = active_exception.redirect_kind
                redirect_user_id = active_exception.redirect_user_id
                redirect_team_id = active_exception.redirect_team_id
            elif (
                profile is not None
                and profile.redirect_kind is not None
                and _window_contains(
                    instant,
                    profile.redirect_starts_at,
                    profile.redirect_ends_at,
                )
            ):
                redirect_kind = profile.redirect_kind
                redirect_user_id = profile.redirect_user_id
                redirect_team_id = profile.redirect_team_id

            if active_exception is not None and active_exception.auto_reassign is not None:
                auto_reassign = active_exception.auto_reassign
            elif profile is not None:
                auto_reassign = profile.auto_reassign_enabled

            redirect_is_active = await self._redirect_is_active(
                workspace_id=workspace_id,
                kind=redirect_kind,
                user_id=redirect_user_id,
                team_id=redirect_team_id,
            )
            if not redirect_is_active:
                redirect_kind = None
                redirect_user_id = None
                redirect_team_id = None
                auto_reassign = False

        return EffectiveStaffAvailabilityRead(
            workspace_id=workspace_id,
            staff_user_id=staff_user_id,
            evaluated_at=instant,
            timezone=timezone_name,
            configured=configured,
            is_available=is_available,
            reason=reason,
            active_exception_id=active_exception.id if active_exception else None,
            active_exception_kind=active_exception.kind if active_exception else None,
            absence_message=absence_message,
            redirect_kind=redirect_kind,
            redirect_user_id=redirect_user_id,
            redirect_team_id=redirect_team_id,
            auto_reassign_enabled=auto_reassign,
        )

    async def resolve_responsibility(
        self,
        *,
        workspace_id: UUID,
        responsible_user_id: UUID,
        at: datetime | None = None,
        max_hops: int = 8,
    ) -> ResponsibilityResolutionRead:
        """Return the approved automatic reassignment decision without mutating consumers."""

        instant = _aware_utc(at)
        await self._active_membership(workspace_id, responsible_user_id)
        current = responsible_user_id
        seen: set[UUID] = set()
        path: list[UUID] = []
        for _ in range(max(1, min(max_hops, 32))):
            if current in seen:
                return self._resolution(
                    workspace_id,
                    responsible_user_id,
                    instant,
                    path,
                    reason="redirect_cycle",
                )
            seen.add(current)
            path.append(current)
            effective = await self.evaluate(
                workspace_id=workspace_id,
                staff_user_id=current,
                at=instant,
            )
            if effective.is_available:
                return ResponsibilityResolutionRead(
                    workspace_id=workspace_id,
                    requested_user_id=responsible_user_id,
                    effective_user_id=current,
                    effective_team_id=None,
                    evaluated_at=instant,
                    changed=current != responsible_user_id,
                    reason=(
                        "responsible_available"
                        if current == responsible_user_id
                        else "redirected_to_available_user"
                    ),
                    path=path,
                )
            if not effective.auto_reassign_enabled or effective.redirect_kind is None:
                return self._resolution(
                    workspace_id,
                    responsible_user_id,
                    instant,
                    path,
                    reason="unavailable_without_active_redirect",
                )
            if effective.redirect_kind is AvailabilityRedirectKind.team:
                return ResponsibilityResolutionRead(
                    workspace_id=workspace_id,
                    requested_user_id=responsible_user_id,
                    effective_user_id=None,
                    effective_team_id=effective.redirect_team_id,
                    evaluated_at=instant,
                    changed=True,
                    reason="redirected_to_team",
                    path=path,
                )
            if effective.redirect_user_id is None:
                return self._resolution(
                    workspace_id,
                    responsible_user_id,
                    instant,
                    path,
                    reason="invalid_redirect_target",
                )
            current = effective.redirect_user_id
        return self._resolution(
            workspace_id,
            responsible_user_id,
            instant,
            path,
            reason="redirect_hop_limit",
        )

    async def _validate_proposal_target(
        self,
        *,
        workspace_id: UUID,
        actor_membership: WorkspaceMembership,
        target_user_id: UUID | None,
        proposal: StaffAvailabilityChangePayload,
    ) -> tuple[WorkspaceMembership | None, UUID | None]:
        target = (
            await self._active_membership(workspace_id, target_user_id)
            if target_user_id is not None
            else None
        )
        target_entity_id = None
        if isinstance(proposal, ReplaceAvailabilityConfigurationChange):
            if target is None:
                raise AvailabilityValidationError(
                    "Complete availability configuration requires target_user_id."
                )
            await self._validate_redirect(
                workspace_id,
                target_user_id,
                self._configuration_redirect(proposal),
            )
        elif isinstance(proposal, ReplaceRecurringScheduleChange | SetAbsenceRoutingChange):
            if target is None:
                raise AvailabilityValidationError(
                    "Recurring schedules and absence routing require target_user_id."
                )
            await self._validate_redirect(
                workspace_id,
                target_user_id,
                proposal.redirect if isinstance(proposal, SetAbsenceRoutingChange) else None,
            )
        elif isinstance(proposal, CreateAvailabilityExceptionChange):
            if proposal.scope is AvailabilityExceptionScope.staff and target is None:
                raise AvailabilityValidationError("Staff exceptions require target_user_id.")
            if (
                proposal.scope is AvailabilityExceptionScope.workspace
                and target_user_id is not None
            ):
                raise AvailabilityValidationError(
                    "Workspace holidays cannot include target_user_id."
                )
            await self._validate_redirect(workspace_id, target_user_id, proposal.redirect)
        elif isinstance(
            proposal,
            UpdateAvailabilityExceptionChange | DeleteAvailabilityExceptionChange,
        ):
            exception = await self._exception(
                workspace_id,
                proposal.exception_id,
                include_deleted=False,
            )
            target_entity_id = exception.id
            if exception.staff_user_id != target_user_id:
                raise AvailabilityValidationError(
                    "target_user_id does not match the exception tenant target."
                )
            if exception.version != proposal.expected_exception_version:
                raise AvailabilityConflictError(
                    "The exception changed before this proposal was submitted."
                )
            if isinstance(proposal, UpdateAvailabilityExceptionChange):
                if (
                    exception.scope is AvailabilityExceptionScope.workspace
                    and proposal.kind is not AvailabilityExceptionKind.holiday
                ):
                    raise AvailabilityValidationError(
                        "Workspace-wide exceptions must remain holidays."
                    )
                if exception.scope is AvailabilityExceptionScope.workspace and (
                    proposal.redirect is not None or proposal.auto_reassign is not None
                ):
                    raise AvailabilityValidationError(
                        "Workspace-wide holidays cannot define a shared redirect."
                    )
                await self._validate_redirect(
                    workspace_id,
                    target_user_id,
                    proposal.redirect,
                )
        else:  # pragma: no cover - discriminated schema prevents this
            raise AvailabilityValidationError("Unsupported availability proposal.")
        return target, target_entity_id

    async def _current_base_version(
        self,
        *,
        workspace_id: UUID,
        target_user_id: UUID | None,
        proposal: StaffAvailabilityChangePayload,
    ) -> int:
        if target_user_id is not None:
            profile = await self._profile(workspace_id, target_user_id)
            return profile.version if profile is not None else 0
        if isinstance(
            proposal,
            UpdateAvailabilityExceptionChange | DeleteAvailabilityExceptionChange,
        ):
            exception = await self._exception(workspace_id, proposal.exception_id)
            return exception.version
        return 0

    async def _apply_approved_change(
        self,
        *,
        workspace_id: UUID,
        request: StaffAvailabilityChangeRequest,
        proposal: StaffAvailabilityChangePayload,
        profile: StaffAvailabilityProfile | None,
        actor_user_id: UUID,
    ) -> int:
        if isinstance(proposal, ReplaceAvailabilityConfigurationChange):
            profile = await self._ensure_profile(
                workspace_id,
                request.target_user_id,
                profile,
                timezone=proposal.timezone,
            )
            redirect = self._configuration_redirect(proposal)
            await self._validate_redirect(
                workspace_id,
                request.target_user_id,
                redirect,
            )
            before = await self._configuration_snapshot(profile)
            activation_starts_at, activation_ends_at = self._configuration_activation_window(
                proposal
            )
            existing_exceptions = list(
                (
                    await self.db.scalars(
                        select(StaffAvailabilityException)
                        .where(
                            StaffAvailabilityException.workspace_id == workspace_id,
                            StaffAvailabilityException.staff_user_id == request.target_user_id,
                            StaffAvailabilityException.scope == AvailabilityExceptionScope.staff,
                            StaffAvailabilityException.deleted_at.is_(None),
                        )
                        .with_for_update()
                    )
                ).all()
            )
            await self.db.execute(
                delete(StaffRecurringAvailability)
                .where(
                    StaffRecurringAvailability.workspace_id == workspace_id,
                    StaffRecurringAvailability.profile_id == profile.id,
                )
                .execution_options(synchronize_session=False)
            )

            profile.timezone = proposal.timezone
            profile.schedule_configured = True
            profile.availability_mode = proposal.mode
            profile.availability_starts_at = activation_starts_at
            profile.availability_ends_at = activation_ends_at
            profile.absence_message = (
                proposal.absence_message.text if proposal.absence_message.enabled else None
            )
            profile.absence_message_starts_at = (
                activation_starts_at if proposal.absence_message.enabled else None
            )
            profile.absence_message_ends_at = (
                activation_ends_at if proposal.absence_message.enabled else None
            )
            profile.redirect_kind = redirect.kind if redirect else None
            profile.redirect_user_id = redirect.user_id if redirect else None
            profile.redirect_team_id = redirect.team_id if redirect else None
            profile.redirect_starts_at = activation_starts_at if redirect else None
            profile.redirect_ends_at = activation_ends_at if redirect else None
            profile.auto_reassign_enabled = proposal.automatic_swap
            profile.version += 1
            profile.updated_by_user_id = actor_user_id

            for day in proposal.weekly_availability:
                if not day.enabled:
                    continue
                for window in day.windows:
                    self.db.add(
                        StaffRecurringAvailability(
                            workspace_id=workspace_id,
                            profile_id=profile.id,
                            weekday=day.weekday,
                            starts_at=window.start,
                            ends_at=window.end,
                            valid_from=None,
                            valid_until=None,
                            label=None,
                            source_request_id=request.id,
                        )
                    )

            deleted_at = datetime.now(UTC)
            for exception in existing_exceptions:
                exception.deleted_at = deleted_at
                exception.version += 1
                exception.source_request_id = request.id
                exception.updated_by_user_id = actor_user_id
            for exception in self._configuration_exception_models(
                workspace_id=workspace_id,
                staff_user_id=profile.staff_user_id,
                request_id=request.id,
                actor_user_id=actor_user_id,
                proposal=proposal,
            ):
                self.db.add(exception)

            await self.db.flush()
            after = await self._configuration_snapshot(profile)
            await self._audit_effective_change(
                request,
                actor_user_id,
                AvailabilityAuditAction.configuration_replaced,
                "availability_profile",
                profile.id,
                "Approved complete availability configuration replaced atomically.",
                before,
                after,
            )
            return profile.version

        if isinstance(proposal, ReplaceRecurringScheduleChange):
            profile = await self._ensure_profile(
                workspace_id,
                request.target_user_id,
                profile,
                timezone=proposal.timezone,
            )
            before = await self._profile_snapshot(profile)
            await self.db.execute(
                delete(StaffRecurringAvailability)
                .where(
                    StaffRecurringAvailability.workspace_id == workspace_id,
                    StaffRecurringAvailability.profile_id == profile.id,
                )
                .execution_options(synchronize_session=False)
            )
            profile.timezone = proposal.timezone
            profile.schedule_configured = True
            profile.version += 1
            profile.updated_by_user_id = actor_user_id
            for slot in proposal.slots:
                self.db.add(
                    StaffRecurringAvailability(
                        workspace_id=workspace_id,
                        profile_id=profile.id,
                        weekday=slot.weekday,
                        starts_at=slot.starts_at,
                        ends_at=slot.ends_at,
                        valid_from=slot.valid_from,
                        valid_until=slot.valid_until,
                        label=slot.label,
                        source_request_id=request.id,
                    )
                )
            await self.db.flush()
            after = await self._profile_snapshot(profile)
            await self._audit_effective_change(
                request,
                actor_user_id,
                AvailabilityAuditAction.schedule_replaced,
                "availability_profile",
                profile.id,
                "Approved recurring schedule replaced.",
                before,
                after,
            )
            return profile.version

        if isinstance(proposal, SetAbsenceRoutingChange):
            profile = await self._ensure_profile(
                workspace_id,
                request.target_user_id,
                profile,
            )
            await self._validate_redirect(
                workspace_id,
                request.target_user_id,
                proposal.redirect,
            )
            before = await self._profile_snapshot(profile)
            profile.absence_message = proposal.absence_message
            profile.absence_message_starts_at = proposal.absence_message_starts_at
            profile.absence_message_ends_at = proposal.absence_message_ends_at
            self._assign_profile_redirect(profile, proposal)
            profile.auto_reassign_enabled = proposal.auto_reassign_enabled
            profile.version += 1
            profile.updated_by_user_id = actor_user_id
            await self.db.flush()
            after = await self._profile_snapshot(profile)
            await self._audit_effective_change(
                request,
                actor_user_id,
                AvailabilityAuditAction.absence_routing_changed,
                "availability_profile",
                profile.id,
                "Approved absence message and routing changed.",
                before,
                after,
            )
            return profile.version

        if isinstance(proposal, CreateAvailabilityExceptionChange):
            await self._validate_redirect(
                workspace_id,
                request.target_user_id,
                proposal.redirect,
            )
            exception = StaffAvailabilityException(
                workspace_id=workspace_id,
                scope=proposal.scope,
                staff_user_id=request.target_user_id,
                kind=proposal.kind,
                title=proposal.title,
                starts_at=proposal.starts_at,
                ends_at=proposal.ends_at,
                all_day=proposal.all_day,
                absence_message=proposal.absence_message,
                auto_reassign=proposal.auto_reassign,
                version=1,
                source_request_id=request.id,
                updated_by_user_id=actor_user_id,
            )
            self._assign_exception_redirect(exception, proposal.redirect)
            self.db.add(exception)
            if request.target_user_id is not None:
                profile = await self._ensure_profile(
                    workspace_id,
                    request.target_user_id,
                    profile,
                )
                profile.version += 1
                profile.updated_by_user_id = actor_user_id
            await self.db.flush()
            await self._audit_effective_change(
                request,
                actor_user_id,
                AvailabilityAuditAction.exception_created,
                "availability_exception",
                exception.id,
                "Approved availability exception created.",
                None,
                self._exception_snapshot(exception),
            )
            return profile.version if profile is not None else exception.version

        exception = await self._exception(
            workspace_id,
            proposal.exception_id,
            include_deleted=False,
            for_update=True,
        )
        if exception.version != proposal.expected_exception_version:
            raise AvailabilityConflictError("The exception changed while this request was pending.")
        if request.target_user_id is None and request.base_version != exception.version:
            raise AvailabilityConflictError(
                "The workspace holiday changed while this request was pending."
            )
        before_exception = self._exception_snapshot(exception)
        if isinstance(proposal, UpdateAvailabilityExceptionChange):
            await self._validate_redirect(
                workspace_id,
                request.target_user_id,
                proposal.redirect,
            )
            exception.kind = proposal.kind
            exception.title = proposal.title
            exception.starts_at = proposal.starts_at
            exception.ends_at = proposal.ends_at
            exception.all_day = proposal.all_day
            exception.absence_message = proposal.absence_message
            exception.auto_reassign = proposal.auto_reassign
            self._assign_exception_redirect(exception, proposal.redirect)
            action = AvailabilityAuditAction.exception_updated
            summary = "Approved availability exception updated."
        else:
            exception.deleted_at = datetime.now(UTC)
            action = AvailabilityAuditAction.exception_deleted
            summary = "Approved availability exception deleted."
        exception.version += 1
        exception.source_request_id = request.id
        exception.updated_by_user_id = actor_user_id
        if request.target_user_id is not None:
            profile = await self._ensure_profile(
                workspace_id,
                request.target_user_id,
                profile,
            )
            profile.version += 1
            profile.updated_by_user_id = actor_user_id
        await self.db.flush()
        await self._audit_effective_change(
            request,
            actor_user_id,
            action,
            "availability_exception",
            exception.id,
            summary,
            before_exception,
            self._exception_snapshot(exception),
        )
        return profile.version if profile is not None else exception.version

    async def _actor_membership(
        self,
        workspace_id: UUID,
        actor: AuthContext,
    ) -> WorkspaceMembership:
        if actor.is_support_access or actor.workspace_id != workspace_id:
            raise AvailabilityAuthorizationError(
                "A normal membership in the active workspace is required."
            )
        membership = await self._active_membership(workspace_id, actor.user_id)
        if actor.membership_id is not None and membership.id != actor.membership_id:
            raise AvailabilityAuthorizationError("The authenticated membership is stale.")
        return membership

    async def _active_membership(
        self,
        workspace_id: UUID,
        user_id: UUID | None,
    ) -> WorkspaceMembership:
        if user_id is None:
            raise AvailabilityNotFoundError("Workspace member not found.")
        membership = await self.db.scalar(
            select(WorkspaceMembership)
            .join(User, User.id == WorkspaceMembership.user_id)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.status == MembershipStatus.active,
                User.agent_type == AgentType.human,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
        if membership is None:
            raise AvailabilityNotFoundError("Active staff member not found in this workspace.")
        return membership

    @staticmethod
    def _assert_can_submit(
        *,
        actor_membership: WorkspaceMembership,
        target_membership: WorkspaceMembership | None,
    ) -> None:
        if target_membership is None:
            if actor_membership.role not in {
                WorkspaceRole.workspace_owner,
                WorkspaceRole.workspace_admin,
            }:
                raise AvailabilityAuthorizationError(
                    "Only an account master or workspace administrator may propose holidays."
                )
            return
        if actor_membership.user_id == target_membership.user_id:
            return
        if actor_membership.role is WorkspaceRole.workspace_owner:
            return
        if (
            actor_membership.role is WorkspaceRole.workspace_admin
            and target_membership.role is not WorkspaceRole.workspace_owner
        ):
            return
        if actor_membership.role is WorkspaceRole.legal_manager and target_membership.role in {
            WorkspaceRole.operator,
            WorkspaceRole.viewer,
        }:
            return
        raise AvailabilityAuthorizationError(
            "The requester cannot propose availability changes for this staff member."
        )

    def _approval_policy_evidence(
        self,
        *,
        request: StaffAvailabilityChangeRequest,
        actor_membership: WorkspaceMembership,
        target_membership: WorkspaceMembership | None,
    ) -> dict[str, object]:
        if request.requested_by_user_id == actor_membership.user_id:
            raise AvailabilityAuthorizationError(
                "Four-eyes policy forbids approving or rejecting your own request."
            )
        if actor_membership.role is WorkspaceRole.workspace_owner:
            return {
                "policyVersion": _POLICY_VERSION,
                "authorization": "account_master",
                "approverRole": actor_membership.role.value,
                "distinctApprover": True,
            }
        can_approve = has_effective_permission(
            actor_membership.role,
            "aprovacoes",
            "approve",
            actor_membership.permission_grants,
            actor_membership.permission_denials,
        )
        if not can_approve:
            raise AvailabilityAuthorizationError(
                "The manager lacks the effective approvals:approve permission."
            )
        if target_membership is None:
            allowed = actor_membership.role is WorkspaceRole.workspace_admin
        elif target_membership.role in {WorkspaceRole.operator, WorkspaceRole.viewer}:
            allowed = actor_membership.role in {
                WorkspaceRole.workspace_admin,
                WorkspaceRole.legal_manager,
            }
        else:
            allowed = False
        if not allowed:
            raise AvailabilityAuthorizationError(
                "This request requires approval by an account master under the role hierarchy."
            )
        return {
            "policyVersion": _POLICY_VERSION,
            "authorization": "authorized_manager",
            "approverRole": actor_membership.role.value,
            "effectivePermission": "aprovacoes:approve",
            "distinctApprover": True,
        }

    @staticmethod
    def _can_view_all_requests(membership: WorkspaceMembership) -> bool:
        return has_effective_permission(
            membership.role,
            "aprovacoes",
            "view",
            membership.permission_grants,
            membership.permission_denials,
        )

    @staticmethod
    def _assert_can_view_staff(
        actor_membership: WorkspaceMembership,
        target_membership: WorkspaceMembership,
    ) -> None:
        if actor_membership.user_id == target_membership.user_id:
            return
        if actor_membership.role in {
            WorkspaceRole.workspace_owner,
            WorkspaceRole.workspace_admin,
            WorkspaceRole.legal_manager,
        }:
            return
        raise AvailabilityAuthorizationError(
            "Detailed availability is restricted to the staff member and authorized managers."
        )

    async def _validate_redirect(
        self,
        workspace_id: UUID,
        source_user_id: UUID | None,
        redirect,
    ) -> None:
        if redirect is None:
            return
        if redirect.kind is AvailabilityRedirectKind.user:
            if redirect.user_id == source_user_id:
                raise AvailabilityValidationError("A staff member cannot redirect to themselves.")
            await self._active_membership(workspace_id, redirect.user_id)
            return
        team = await self.db.scalar(
            select(WorkspaceTeam).where(
                WorkspaceTeam.workspace_id == workspace_id,
                WorkspaceTeam.id == redirect.team_id,
                WorkspaceTeam.is_active.is_(True),
            )
        )
        if team is None:
            raise AvailabilityNotFoundError("Active redirect team not found in this workspace.")

    async def _redirect_is_active(
        self,
        *,
        workspace_id: UUID,
        kind: AvailabilityRedirectKind | None,
        user_id: UUID | None,
        team_id: UUID | None,
    ) -> bool:
        if kind is None:
            return False
        if kind is AvailabilityRedirectKind.user:
            try:
                await self._active_membership(workspace_id, user_id)
            except AvailabilityNotFoundError:
                return False
            return True
        return (
            await self.db.scalar(
                select(WorkspaceTeam.id).where(
                    WorkspaceTeam.workspace_id == workspace_id,
                    WorkspaceTeam.id == team_id,
                    WorkspaceTeam.is_active.is_(True),
                )
            )
            is not None
        )

    async def _profile(
        self,
        workspace_id: UUID,
        staff_user_id: UUID,
        *,
        for_update: bool = False,
    ) -> StaffAvailabilityProfile | None:
        query = select(StaffAvailabilityProfile).where(
            StaffAvailabilityProfile.workspace_id == workspace_id,
            StaffAvailabilityProfile.staff_user_id == staff_user_id,
        )
        if for_update:
            query = query.with_for_update()
        return await self.db.scalar(query)

    async def _ensure_profile(
        self,
        workspace_id: UUID,
        staff_user_id: UUID | None,
        profile: StaffAvailabilityProfile | None,
        *,
        timezone: str = "UTC",
    ) -> StaffAvailabilityProfile:
        if staff_user_id is None:
            raise AvailabilityValidationError("A staff profile requires target_user_id.")
        if profile is not None:
            return profile
        profile = StaffAvailabilityProfile(
            workspace_id=workspace_id,
            staff_user_id=staff_user_id,
            timezone=timezone,
            schedule_configured=False,
            version=0,
        )
        self.db.add(profile)
        await self.db.flush()
        return profile

    async def _request(
        self,
        workspace_id: UUID,
        request_id: UUID,
        *,
        for_update: bool = False,
    ) -> StaffAvailabilityChangeRequest:
        query = select(StaffAvailabilityChangeRequest).where(
            StaffAvailabilityChangeRequest.workspace_id == workspace_id,
            StaffAvailabilityChangeRequest.id == request_id,
        )
        if for_update:
            query = query.with_for_update()
        request = await self.db.scalar(query)
        if request is None:
            raise AvailabilityNotFoundError("Availability request not found.")
        return request

    async def _exception(
        self,
        workspace_id: UUID,
        exception_id: UUID,
        *,
        include_deleted: bool = False,
        for_update: bool = False,
    ) -> StaffAvailabilityException:
        query = select(StaffAvailabilityException).where(
            StaffAvailabilityException.workspace_id == workspace_id,
            StaffAvailabilityException.id == exception_id,
        )
        if not include_deleted:
            query = query.where(StaffAvailabilityException.deleted_at.is_(None))
        if for_update:
            query = query.with_for_update()
        exception = await self.db.scalar(query)
        if exception is None:
            raise AvailabilityNotFoundError("Availability exception not found.")
        return exception

    @staticmethod
    def _assert_pending(request: StaffAvailabilityChangeRequest) -> None:
        if request.status is not AvailabilityRequestStatus.pending:
            raise AvailabilityConflictError("Availability request is no longer pending.")

    @staticmethod
    def _parse_stored_change(payload: dict) -> StaffAvailabilityChangePayload:
        try:
            return _CHANGE_ADAPTER.validate_python(payload)
        except ValidationError as exc:  # immutable request evidence is malformed
            raise AvailabilityValidationError(
                "Stored availability proposal failed contract validation."
            ) from exc

    @staticmethod
    def _assign_profile_redirect(
        profile: StaffAvailabilityProfile,
        proposal: SetAbsenceRoutingChange,
    ) -> None:
        profile.redirect_kind = proposal.redirect.kind if proposal.redirect else None
        profile.redirect_user_id = proposal.redirect.user_id if proposal.redirect else None
        profile.redirect_team_id = proposal.redirect.team_id if proposal.redirect else None
        profile.redirect_starts_at = proposal.redirect_starts_at
        profile.redirect_ends_at = proposal.redirect_ends_at

    @staticmethod
    def _assign_exception_redirect(
        exception: StaffAvailabilityException,
        redirect,
    ) -> None:
        exception.redirect_kind = redirect.kind if redirect else None
        exception.redirect_user_id = redirect.user_id if redirect else None
        exception.redirect_team_id = redirect.team_id if redirect else None

    @staticmethod
    def _configuration_redirect(
        proposal: ReplaceAvailabilityConfigurationChange,
    ) -> RedirectTargetWrite | None:
        if proposal.redirect.kind == "none":
            return None
        return RedirectTargetWrite(
            kind=AvailabilityRedirectKind(proposal.redirect.kind),
            user_id=(proposal.redirect.target_id if proposal.redirect.kind == "user" else None),
            team_id=(proposal.redirect.target_id if proposal.redirect.kind == "team" else None),
        )

    @classmethod
    def _configuration_activation_window(
        cls,
        proposal: ReplaceAvailabilityConfigurationChange,
    ) -> tuple[datetime | None, datetime | None]:
        if proposal.activation.mode == "now":
            return None, None
        if (
            proposal.activation.starts_at is None or proposal.activation.ends_at is None
        ):  # pragma: no cover - schema enforces the pair
            raise AvailabilityValidationError("Scheduled activation requires a full window.")
        timezone = ZoneInfo(proposal.timezone)
        starts_at = cls._configuration_datetime_utc(
            proposal.activation.starts_at,
            timezone,
        )
        ends_at = cls._configuration_datetime_utc(
            proposal.activation.ends_at,
            timezone,
        )
        if ends_at <= starts_at:
            raise AvailabilityValidationError(
                "Scheduled activation collapses after timezone normalization."
            )
        return starts_at, ends_at

    @staticmethod
    def _configuration_datetime_utc(value: datetime, timezone: ZoneInfo) -> datetime:
        if value.tzinfo is not None and value.utcoffset() is not None:
            return value.astimezone(UTC)
        localized = value.replace(tzinfo=timezone)
        normalized = localized.astimezone(UTC).astimezone(timezone).replace(tzinfo=None)
        if normalized != value:
            raise AvailabilityValidationError(
                "Local configuration time does not exist in the selected timezone."
            )
        return localized.astimezone(UTC)

    @classmethod
    def _configuration_exception_models(
        cls,
        *,
        workspace_id: UUID,
        staff_user_id: UUID,
        request_id: UUID,
        actor_user_id: UUID,
        proposal: ReplaceAvailabilityConfigurationChange,
    ) -> list[StaffAvailabilityException]:
        timezone = ZoneInfo(proposal.timezone)
        kind_map = {
            "vacation": AvailabilityExceptionKind.vacation,
            "holiday": AvailabilityExceptionKind.holiday,
            "exception": AvailabilityExceptionKind.unavailable,
        }
        models: list[StaffAvailabilityException] = []
        for configured in proposal.exceptions:
            try:
                day_after_end = configured.ends_on + timedelta(days=1)
            except OverflowError as exc:
                raise AvailabilityValidationError(
                    "Exception end date exceeds the supported calendar range."
                ) from exc
            starts_at = cls._configuration_datetime_utc(
                datetime.combine(configured.starts_on, time.min),
                timezone,
            )
            ends_at = cls._configuration_datetime_utc(
                datetime.combine(day_after_end, time.min),
                timezone,
            )
            models.append(
                StaffAvailabilityException(
                    workspace_id=workspace_id,
                    scope=AvailabilityExceptionScope.staff,
                    staff_user_id=staff_user_id,
                    kind=kind_map[configured.kind],
                    title=configured.name,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    all_day=True,
                    absence_message=None,
                    redirect_kind=None,
                    redirect_user_id=None,
                    redirect_team_id=None,
                    auto_reassign=None,
                    version=1,
                    source_request_id=request_id,
                    updated_by_user_id=actor_user_id,
                )
            )
            if configured.mode != "custom_hours":
                continue
            current_day = configured.starts_on
            while current_day <= configured.ends_on:
                for window in configured.windows:
                    override_starts_at = cls._configuration_datetime_utc(
                        datetime.combine(current_day, window.start),
                        timezone,
                    )
                    override_ends_at = cls._configuration_datetime_utc(
                        datetime.combine(current_day, window.end),
                        timezone,
                    )
                    if override_ends_at <= override_starts_at:
                        raise AvailabilityValidationError(
                            "Custom-hours exception collapses after timezone normalization."
                        )
                    models.append(
                        StaffAvailabilityException(
                            workspace_id=workspace_id,
                            scope=AvailabilityExceptionScope.staff,
                            staff_user_id=staff_user_id,
                            kind=AvailabilityExceptionKind.available_override,
                            title=f"{configured.name} — custom hours",
                            starts_at=override_starts_at,
                            ends_at=override_ends_at,
                            all_day=False,
                            absence_message=None,
                            redirect_kind=None,
                            redirect_user_id=None,
                            redirect_team_id=None,
                            auto_reassign=None,
                            version=1,
                            source_request_id=request_id,
                            updated_by_user_id=actor_user_id,
                        )
                    )
                current_day += timedelta(days=1)
        return models

    async def _configuration_snapshot(self, profile: StaffAvailabilityProfile) -> dict:
        snapshot = await self._profile_snapshot(profile)
        rows = await self.db.scalars(
            select(StaffAvailabilityException)
            .where(
                StaffAvailabilityException.workspace_id == profile.workspace_id,
                StaffAvailabilityException.staff_user_id == profile.staff_user_id,
                StaffAvailabilityException.scope == AvailabilityExceptionScope.staff,
                StaffAvailabilityException.deleted_at.is_(None),
            )
            .order_by(
                StaffAvailabilityException.starts_at.asc(),
                StaffAvailabilityException.id.asc(),
            )
        )
        snapshot["exceptions"] = [self._exception_snapshot(exception) for exception in rows.all()]
        return snapshot

    async def _profile_snapshot(self, profile: StaffAvailabilityProfile) -> dict:
        rows = await self.db.scalars(
            select(StaffRecurringAvailability)
            .where(
                StaffRecurringAvailability.workspace_id == profile.workspace_id,
                StaffRecurringAvailability.profile_id == profile.id,
            )
            .order_by(
                StaffRecurringAvailability.weekday.asc(),
                StaffRecurringAvailability.starts_at.asc(),
            )
        )
        return {
            "id": str(profile.id),
            "staffUserId": str(profile.staff_user_id),
            "timezone": profile.timezone,
            "scheduleConfigured": profile.schedule_configured,
            "availabilityMode": profile.availability_mode.value,
            "availabilityStartsAt": _iso(profile.availability_starts_at),
            "availabilityEndsAt": _iso(profile.availability_ends_at),
            "version": profile.version,
            "absenceMessage": profile.absence_message,
            "absenceMessageStartsAt": _iso(profile.absence_message_starts_at),
            "absenceMessageEndsAt": _iso(profile.absence_message_ends_at),
            "redirectKind": profile.redirect_kind.value if profile.redirect_kind else None,
            "redirectUserId": str(profile.redirect_user_id) if profile.redirect_user_id else None,
            "redirectTeamId": str(profile.redirect_team_id) if profile.redirect_team_id else None,
            "redirectStartsAt": _iso(profile.redirect_starts_at),
            "redirectEndsAt": _iso(profile.redirect_ends_at),
            "autoReassignEnabled": profile.auto_reassign_enabled,
            "slots": [
                {
                    "weekday": slot.weekday,
                    "startsAt": slot.starts_at.isoformat(),
                    "endsAt": slot.ends_at.isoformat(),
                    "validFrom": slot.valid_from.isoformat() if slot.valid_from else None,
                    "validUntil": slot.valid_until.isoformat() if slot.valid_until else None,
                    "label": slot.label,
                }
                for slot in rows.all()
            ],
        }

    @staticmethod
    def _exception_snapshot(exception: StaffAvailabilityException) -> dict:
        return {
            "id": str(exception.id),
            "scope": exception.scope.value,
            "staffUserId": str(exception.staff_user_id) if exception.staff_user_id else None,
            "kind": exception.kind.value,
            "title": exception.title,
            "startsAt": _iso(exception.starts_at),
            "endsAt": _iso(exception.ends_at),
            "allDay": exception.all_day,
            "absenceMessage": exception.absence_message,
            "redirectKind": exception.redirect_kind.value if exception.redirect_kind else None,
            "redirectUserId": (
                str(exception.redirect_user_id) if exception.redirect_user_id else None
            ),
            "redirectTeamId": (
                str(exception.redirect_team_id) if exception.redirect_team_id else None
            ),
            "autoReassign": exception.auto_reassign,
            "version": exception.version,
            "deletedAt": _iso(exception.deleted_at),
        }

    @staticmethod
    def _request_snapshot(request: StaffAvailabilityChangeRequest) -> dict:
        return {
            "id": str(request.id),
            "targetUserId": str(request.target_user_id) if request.target_user_id else None,
            "requestedByUserId": str(request.requested_by_user_id),
            "resolvedByUserId": (
                str(request.resolved_by_user_id) if request.resolved_by_user_id else None
            ),
            "changeType": request.change_type.value,
            "status": request.status.value,
            "baseVersion": request.base_version,
            "appliedVersion": request.applied_version,
            "reason": request.reason,
            "decisionNote": request.decision_note,
            "decisionPolicy": request.decision_policy,
            "resolvedAt": _iso(request.resolved_at),
        }

    @staticmethod
    def _request_hash(
        *,
        target_user_id: UUID | None,
        proposed_change: dict,
        base_version: int,
        reason: str,
    ) -> str:
        canonical = json.dumps(
            {
                "targetUserId": str(target_user_id) if target_user_id else None,
                "proposedChange": proposed_change,
                "baseVersion": base_version,
                "reason": reason,
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(canonical) > 64 * 1024:
            raise AvailabilityValidationError("Availability proposal exceeds 64 KiB.")
        return hashlib.sha256(canonical).hexdigest()

    async def _audit_effective_change(
        self,
        request: StaffAvailabilityChangeRequest,
        actor_user_id: UUID,
        action: AvailabilityAuditAction,
        entity_type: str,
        entity_id: UUID,
        summary: str,
        before_data: dict | None,
        after_data: dict | None,
    ) -> None:
        await self._audit(
            workspace_id=request.workspace_id,
            staff_user_id=request.target_user_id,
            request=request,
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            summary=summary,
            before_data=before_data,
            after_data=after_data,
        )

    async def _audit(
        self,
        *,
        workspace_id: UUID,
        staff_user_id: UUID | None,
        request: StaffAvailabilityChangeRequest,
        actor_user_id: UUID,
        action: AvailabilityAuditAction,
        entity_type: str,
        entity_id: UUID | None,
        summary: str,
        before_data: dict | None,
        after_data: dict | None,
    ) -> None:
        self.db.add(
            StaffAvailabilityAuditEvent(
                workspace_id=workspace_id,
                staff_user_id=staff_user_id,
                request_id=request.id,
                actor_user_id=actor_user_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                summary=summary,
                before_data=before_data,
                after_data=after_data,
                correlation_id=request.idempotency_key,
            )
        )
        await self.db.flush()

    @staticmethod
    def _resolution(
        workspace_id: UUID,
        requested_user_id: UUID,
        instant: datetime,
        path: list[UUID],
        *,
        reason: str,
    ) -> ResponsibilityResolutionRead:
        return ResponsibilityResolutionRead(
            workspace_id=workspace_id,
            requested_user_id=requested_user_id,
            # Terminal negative outcomes are destination-less.  Returning the
            # requested person made callers treat an unavailable person, a
            # redirect cycle or a hop limit as an assignable human.
            effective_user_id=None,
            effective_team_id=None,
            evaluated_at=instant,
            changed=False,
            reason=reason,
            path=path,
        )


__all__ = [
    "AvailabilityAuthorizationError",
    "AvailabilityConflictError",
    "AvailabilityDomainError",
    "AvailabilityNotFoundError",
    "AvailabilityValidationError",
    "StaffAvailabilityService",
]
