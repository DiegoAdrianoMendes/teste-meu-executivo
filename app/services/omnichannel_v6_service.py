"""Canonical omnichannel orchestration for inbox, social, ads and automation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.klm_runtime import external_delivery_allowed
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.db.advisory import acquire_transaction_advisory_lock
from app.db.post_commit import queue_realtime_event
from app.models.client import Client
from app.models.klm_operations import (
    MessageAuthorType,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
    OmnichannelThread,
    ThreadStatus,
)
from app.models.matter import (
    HealthSubtheme,
    KanbanColumnId,
    Matter,
    MicroStatus,
    PracticeArea,
)
from app.models.omnichannel_v6 import (
    LeadCaptureReceipt,
    OmnichannelCallCostLedgerEntry,
    OmnichannelCallEvent,
    OmnichannelCallSession,
    OmnichannelChannelConnection,
    OmnichannelChannelEvent,
    OmnichannelEndpoint,
    OmnichannelIdentity,
    OmnichannelIdentityMutationEvent,
    OmnichannelIdentityReviewCase,
    OmnichannelMessageCostLedgerEntry,
    OmnichannelMessageDeliveryEvent,
    OmnichannelRoutingEvent,
    OmnichannelScheduledMessage,
    OmnichannelScheduledMessageEvent,
    OmnichannelThreadBinding,
    WorkspaceTeam,
    WorkspaceTeamMember,
)
from app.models.staff_availability import (
    AvailabilityRequestStatus,
    StaffAvailabilityChangeRequest,
    StaffAvailabilityException,
    StaffAvailabilityProfile,
)
from app.models.user import User
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.schemas.klm_operations import AcquisitionEventCreate, OmnichannelMessageCreate
from app.schemas.omnichannel_v6 import (
    CallSessionCreate,
    CallSessionTransition,
    CanonicalChannelEventIngest,
    IdentityMergeRequest,
    IdentitySplitRequest,
    InternalCallSessionTransition,
    InternalMessageDeliveryReceiptIngest,
    LeadCaptureIngest,
    MessageDeliveryReceiptIngest,
    ScheduledMessageCreate,
    ScheduledMessageUpdate,
    ThreadTransferRequest,
    WorkspaceTeamCreate,
    WorkspaceTeamMemberCreate,
    WorkspaceTeamUpdate,
)
from app.schemas.staff_availability import (
    EffectiveStaffAvailabilityRead,
    ResponsibilityResolutionRead,
)
from app.services.klm_operations_service import KlmOperationsService
from app.services.knowledge_service import KnowledgeService
from app.services.staff_availability_service import StaffAvailabilityService
from app.services.whatsapp.helpers import (
    normalize_phone_number,
    phone_number_lookup_variants,
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
        "signature",
        "token",
    }
)
_CALL_TRANSITIONS = {
    "requested": {"permission_requested", "approved_local", "cancelled", "failed"},
    "permission_requested": {"approved_local", "cancelled", "failed"},
    "approved_local": {"ringing", "cancelled", "failed"},
    "ringing": {"active", "cancelled", "failed"},
    "active": {"completed", "failed"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}
_DELIVERY_RANK = {
    MessageDeliveryStatus.queued: 1,
    MessageDeliveryStatus.sent: 2,
    MessageDeliveryStatus.delivered: 3,
    MessageDeliveryStatus.read: 4,
}
_DELIVERY_TERMINAL = {
    MessageDeliveryStatus.failed,
    MessageDeliveryStatus.blocked,
}
_LOCAL_DRAFT_PROVIDER_STATUSES = frozenset(
    {"draft", "local_created", "local_draft", "not_dispatched"}
)
_ACTIVE_SCHEDULE_CONNECTION_STATES = frozenset({"active", "configured_local", "connected"})
_SUPPORTED_ABSENCE_RESPONSE_TRANSPORTS = frozenset({"cloud_api", "coexistence", "official_api"})
_AVAILABILITY_ROUTING_POLICY = "approved-staff-availability-v1"
_PROFILE_AVATAR_CHANNEL_PRIORITY = {
    "whatsapp": 0,
    "instagram": 10,
    "messenger": 20,
    "facebook": 20,
    "tiktok": 30,
    "webchat": 40,
    "form": 50,
    "email": 60,
    "audio": 70,
    "internal": 80,
}


def _now() -> datetime:
    return datetime.now(UTC)


def _avatar_priority(channel: str | None) -> int:
    return _PROFILE_AVATAR_CHANNEL_PRIORITY.get(str(channel or "").lower(), 90)


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return _now()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _canonical_hash(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _transfer_request_correlation(
    thread_id: UUID,
    payload: ThreadTransferRequest,
) -> str:
    fingerprint = _canonical_hash(
        {
            "threadId": str(thread_id),
            "payload": payload.model_dump(mode="json", by_alias=False),
        }
    )
    return f"transfer-request:{fingerprint}"


@dataclass(frozen=True)
class _TransferRoutingDecision:
    requested_user_id: UUID | None
    requested_team_id: UUID | None
    effective_user_id: UUID | None
    effective_team_id: UUID | None
    evaluated_at: datetime
    applied: bool
    manual_override: bool
    reason: str
    path: tuple[UUID, ...]
    requested_availability: EffectiveStaffAvailabilityRead | None = None
    approval_state: dict[str, Any] | None = None


@dataclass(frozen=True)
class _AbsenceResponseRoute:
    connection: OmnichannelChannelConnection
    endpoint: OmnichannelEndpoint
    binding: OmnichannelThreadBinding
    inbound_message: OmnichannelMessage


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
            result[str(key)] = "[REDACTED]" if normalized_key in _SECRET_KEYS else _scrub(item)
        return result
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def _delivery_projection(
    current: MessageDeliveryStatus,
    incoming: MessageDeliveryStatus,
) -> tuple[MessageDeliveryStatus, bool, str | None]:
    """Reduce unordered provider receipts without allowing false downgrades."""

    if current == MessageDeliveryStatus.received:
        return current, False, "inbound_message"
    if current in _DELIVERY_TERMINAL:
        reason = "duplicate_terminal_status" if current == incoming else "terminal_status"
        return current, False, reason
    if incoming in _DELIVERY_TERMINAL:
        if current in {
            MessageDeliveryStatus.local_draft,
            MessageDeliveryStatus.queued,
            MessageDeliveryStatus.sent,
        }:
            return incoming, current != incoming, None
        return current, False, "terminal_after_delivery_ignored"

    incoming_rank = _DELIVERY_RANK[incoming]
    current_rank = (
        0 if current == MessageDeliveryStatus.local_draft else _DELIVERY_RANK.get(current)
    )
    if current_rank is None:
        return current, False, "unsupported_current_status"
    if incoming_rank <= current_rank:
        reason = "duplicate_status" if incoming_rank == current_rank else "stale_status"
        return current, False, reason
    return incoming, True, None


def _provider_delivery_status(
    raw_status: str,
    *,
    direction: MessageDirection,
) -> MessageDeliveryStatus:
    """Normalize provider and local-adapter status without inventing delivery proof."""

    normalized = raw_status.strip().lower()
    if direction == MessageDirection.inbound:
        return MessageDeliveryStatus.received
    if normalized in _LOCAL_DRAFT_PROVIDER_STATUSES:
        return MessageDeliveryStatus.local_draft
    try:
        status = MessageDeliveryStatus(normalized)
    except ValueError:
        return MessageDeliveryStatus.queued
    if status == MessageDeliveryStatus.received:
        return MessageDeliveryStatus.queued
    return status


def _apply_actual_cost_projection(
    current: Any,
    *,
    amount: Decimal,
    currency: str,
    provider: str,
    connection_key: str,
) -> dict[str, Any]:
    state = dict(current) if isinstance(current, dict) else {}
    currency = currency.upper()
    actual = (
        dict(state.get("actualCostsByCurrency"))
        if isinstance(state.get("actualCostsByCurrency"), dict)
        else {}
    )
    current_amount = Decimal(str(actual.get(currency) or "0"))
    actual[currency] = f"{current_amount + amount:.6f}"
    state["actualCostsByCurrency"] = actual
    state["costsByCurrency"] = dict(actual)
    state["currency"] = str(state.get("currency") or currency).upper()
    state["externalCost"] = str(actual.get(state["currency"], "0.000000"))
    state["actualChargeCount"] = max(0, int(state.get("actualChargeCount") or 0)) + 1
    state["lastCostProvider"] = provider
    state["lastCostConnectionKey"] = connection_key
    state["lastActualCostAt"] = _now().isoformat()
    return state


def _normalize_phone(value: str | None) -> str | None:
    canonical = normalize_phone_number(value)
    if canonical is not None:
        return f"+{canonical}"
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return None
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) in {10, 11}:
        digits = f"55{digits}"
    if len(digits) < 10 or len(digits) > 15:
        raise ValueError("Telefone inválido para identidade canônica.")
    return f"+{digits}"


def _phone_aliases(value: str | None) -> set[str]:
    """Return storage-compatible aliases for safe Brazilian mobile equality."""
    aliases = phone_number_lookup_variants(value)
    return aliases or ({value} if value else set())


def _phones_are_equivalent(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return left == right
    return bool(_phone_aliases(left) & _phone_aliases(right))


def _normalize_email(value: str | None) -> str | None:
    normalized = (value or "").strip().lower()
    if not normalized:
        return None
    if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
        raise ValueError("E-mail inválido para identidade canônica.")
    return normalized


class OmnichannelConflictError(ValueError):
    """Command conflicts with a committed canonical fact."""


class OmnichannelVersionConflictError(OmnichannelConflictError):
    def __init__(self, current_version: int):
        super().__init__(f"Conversa alterada por outro operador; versão atual: {current_version}.")
        self.current_version = current_version


@dataclass(frozen=True)
class CanonicalResolution:
    identity: OmnichannelIdentity
    endpoint: OmnichannelEndpoint
    binding: OmnichannelThreadBinding
    thread: OmnichannelThread


class OmnichannelV6Service:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.operations = KlmOperationsService(db)

    async def _identity(self, workspace_id: UUID, identity_id: UUID) -> OmnichannelIdentity:
        identity = await self.db.scalar(
            select(OmnichannelIdentity).where(
                OmnichannelIdentity.workspace_id == workspace_id,
                OmnichannelIdentity.id == identity_id,
            )
        )
        if not identity:
            raise LookupError("Identidade omnichannel não encontrada no workspace.")
        return identity

    async def _thread(self, workspace_id: UUID, thread_id: UUID, *, lock: bool = False):
        query = select(OmnichannelThread).where(
            OmnichannelThread.workspace_id == workspace_id,
            OmnichannelThread.id == thread_id,
        )
        if lock:
            query = query.with_for_update()
        thread = await self.db.scalar(query)
        if not thread:
            raise LookupError("Conversa não encontrada no workspace.")
        return thread

    async def _append_schedule_event(
        self,
        schedule: OmnichannelScheduledMessage,
        *,
        event_type: str,
        previous_state: str,
        next_state: str,
        idempotency_key: str,
        payload: Any,
        occurred_at: datetime | None = None,
        external_event_id: str | None = None,
        external_message_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OmnichannelScheduledMessageEvent:
        provider = str((schedule.provider_receipt or {}).get("provider") or "")
        if not provider and schedule.connection_id:
            provider = str(
                await self.db.scalar(
                    select(OmnichannelChannelConnection.provider).where(
                        OmnichannelChannelConnection.workspace_id == schedule.workspace_id,
                        OmnichannelChannelConnection.id == schedule.connection_id,
                    )
                )
                or ""
            )
        event = OmnichannelScheduledMessageEvent(
            workspace_id=schedule.workspace_id,
            scheduled_message_id=schedule.id,
            thread_id=schedule.thread_id,
            connection_id=schedule.connection_id,
            canonical_message_id=schedule.canonical_message_id,
            provider=provider or "local_unconfigured",
            event_type=event_type,
            previous_state=previous_state,
            next_state=next_state,
            idempotency_key=idempotency_key,
            payload_sha256=_canonical_hash(payload),
            lease_owner=schedule.lease_owner,
            lease_generation=schedule.lease_generation,
            external_event_id=external_event_id,
            external_message_id=external_message_id,
            error_code=error_code,
            error_message=error_message,
            occurred_at=_aware(occurred_at),
            event_metadata=_scrub(metadata or {}),
        )
        self.db.add(event)
        await self.db.flush()
        return event

    async def _team(self, workspace_id: UUID, team_id: UUID, *, active: bool = False):
        filters = [
            WorkspaceTeam.workspace_id == workspace_id,
            WorkspaceTeam.id == team_id,
        ]
        if active:
            filters.append(WorkspaceTeam.is_active.is_(True))
        team = await self.db.scalar(select(WorkspaceTeam).where(*filters))
        if not team:
            raise LookupError("Equipe não encontrada no workspace.")
        return team

    async def _active_membership(self, workspace_id: UUID, user_id: UUID) -> WorkspaceMembership:
        membership = await self.db.scalar(
            select(WorkspaceMembership)
            .join(User, User.id == WorkspaceMembership.user_id)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.status == MembershipStatus.active,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
        if not membership:
            raise LookupError("Usuário ativo não encontrado no workspace.")
        return membership

    async def _approved_availability_state(
        self,
        *,
        workspace_id: UUID,
        staff_user_id: UUID,
        effective: EffectiveStaffAvailabilityRead,
    ) -> dict[str, Any] | None:
        """Describe the effective aggregate without reading pending proposal payloads."""

        if effective.active_exception_id is not None:
            exception = await self.db.scalar(
                select(StaffAvailabilityException).where(
                    StaffAvailabilityException.workspace_id == workspace_id,
                    StaffAvailabilityException.id == effective.active_exception_id,
                    StaffAvailabilityException.staff_user_id == staff_user_id,
                    StaffAvailabilityException.deleted_at.is_(None),
                )
            )
            if exception is None:
                return None
            approved_request = await self.db.scalar(
                select(StaffAvailabilityChangeRequest).where(
                    StaffAvailabilityChangeRequest.workspace_id == workspace_id,
                    StaffAvailabilityChangeRequest.id == exception.source_request_id,
                    StaffAvailabilityChangeRequest.status == AvailabilityRequestStatus.approved,
                )
            )
            if approved_request is None:
                return None
            return {
                "kind": "exception",
                "entityId": str(exception.id),
                "version": exception.version,
                "approvedRequestId": str(approved_request.id),
            }

        profile = await self.db.scalar(
            select(StaffAvailabilityProfile).where(
                StaffAvailabilityProfile.workspace_id == workspace_id,
                StaffAvailabilityProfile.staff_user_id == staff_user_id,
            )
        )
        if profile is None:
            return None
        approved_request = await self.db.scalar(
            select(StaffAvailabilityChangeRequest)
            .where(
                StaffAvailabilityChangeRequest.workspace_id == workspace_id,
                StaffAvailabilityChangeRequest.target_user_id == staff_user_id,
                StaffAvailabilityChangeRequest.status == AvailabilityRequestStatus.approved,
                StaffAvailabilityChangeRequest.applied_version <= profile.version,
            )
            .order_by(
                StaffAvailabilityChangeRequest.applied_version.desc(),
                StaffAvailabilityChangeRequest.resolved_at.desc(),
            )
        )
        if approved_request is None:
            return None
        return {
            "kind": "profile",
            "entityId": str(profile.id),
            "version": profile.version,
            "approvedRequestId": str(approved_request.id),
        }

    async def _resolve_transfer_routing(
        self,
        *,
        workspace_id: UUID,
        payload: ThreadTransferRequest,
        evaluated_at: datetime,
    ) -> _TransferRoutingDecision:
        requested_user_id = payload.to_user_id
        requested_team_id = payload.to_team_id
        if requested_team_id is not None:
            await self._team(workspace_id, requested_team_id, active=True)
        if requested_user_id is None:
            return _TransferRoutingDecision(
                requested_user_id=None,
                requested_team_id=requested_team_id,
                effective_user_id=None,
                effective_team_id=requested_team_id,
                evaluated_at=evaluated_at,
                applied=False,
                manual_override=payload.source == "human",
                reason="no_user_destination",
                path=(),
            )

        await self._active_membership(workspace_id, requested_user_id)
        availability_service = StaffAvailabilityService(self.db)
        requested_availability = await availability_service.evaluate(
            workspace_id=workspace_id,
            staff_user_id=requested_user_id,
            at=evaluated_at,
        )
        approval_state = await self._approved_availability_state(
            workspace_id=workspace_id,
            staff_user_id=requested_user_id,
            effective=requested_availability,
        )

        if payload.source == "human":
            effective_user_id = requested_user_id
            effective_team_id = requested_team_id
            resolution_reason = (
                "manual_override"
                if not requested_availability.is_available
                else "responsible_available"
            )
            resolution_path = (requested_user_id,)
            routing_applied = False
            manual_override = True
        else:
            resolution: ResponsibilityResolutionRead = (
                await availability_service.resolve_responsibility(
                    workspace_id=workspace_id,
                    responsible_user_id=requested_user_id,
                    at=evaluated_at,
                )
            )
            if not resolution.changed and resolution.reason != "responsible_available":
                raise OmnichannelConflictError(
                    "Responsável indisponível sem redirecionamento automático seguro "
                    f"({resolution.reason})."
                )
            effective_user_id = resolution.effective_user_id
            effective_team_id = (
                resolution.effective_team_id if resolution.changed else requested_team_id
            )
            resolution_reason = resolution.reason
            resolution_path = tuple(resolution.path)
            routing_applied = resolution.changed
            manual_override = False

        if effective_team_id is not None:
            await self._team(workspace_id, effective_team_id, active=True)
        if effective_user_id is not None:
            await self._active_membership(workspace_id, effective_user_id)
        if effective_user_id is None and effective_team_id is None:
            raise OmnichannelConflictError(
                "Roteamento de disponibilidade não produziu um destino ativo."
            )

        if effective_user_id is not None and effective_team_id is not None:
            team_member = await self.db.scalar(
                select(WorkspaceTeamMember.id).where(
                    WorkspaceTeamMember.workspace_id == workspace_id,
                    WorkspaceTeamMember.team_id == effective_team_id,
                    WorkspaceTeamMember.user_id == effective_user_id,
                )
            )
            if team_member is None:
                if routing_applied and effective_user_id != requested_user_id:
                    # An approved direct-user redirect supersedes an unrelated requested team.
                    effective_team_id = None
                else:
                    raise OmnichannelConflictError(
                        "O usuário de destino não pertence à equipe informada."
                    )

        return _TransferRoutingDecision(
            requested_user_id=requested_user_id,
            requested_team_id=requested_team_id,
            effective_user_id=effective_user_id,
            effective_team_id=effective_team_id,
            evaluated_at=evaluated_at,
            applied=routing_applied,
            manual_override=manual_override,
            reason=resolution_reason,
            path=resolution_path,
            requested_availability=requested_availability,
            approval_state=approval_state,
        )

    @staticmethod
    def _routing_audit_data(decision: _TransferRoutingDecision) -> dict[str, Any]:
        availability = decision.requested_availability
        return {
            "policy": _AVAILABILITY_ROUTING_POLICY,
            "applied": decision.applied,
            "manualOverride": decision.manual_override,
            "reason": decision.reason,
            "requestedUserId": (
                str(decision.requested_user_id) if decision.requested_user_id else None
            ),
            "requestedTeamId": (
                str(decision.requested_team_id) if decision.requested_team_id else None
            ),
            "effectiveUserId": (
                str(decision.effective_user_id) if decision.effective_user_id else None
            ),
            "effectiveTeamId": (
                str(decision.effective_team_id) if decision.effective_team_id else None
            ),
            "evaluatedAt": decision.evaluated_at.isoformat(),
            "path": [str(item) for item in decision.path],
            "requestedAvailability": (
                {
                    "isAvailable": availability.is_available,
                    "reason": availability.reason,
                    "activeExceptionId": (
                        str(availability.active_exception_id)
                        if availability.active_exception_id
                        else None
                    ),
                    "approvedAbsenceMessagePresent": bool(availability.absence_message),
                }
                if availability is not None
                else None
            ),
            "approvalState": decision.approval_state,
        }

    async def _absence_response_route(
        self,
        *,
        workspace_id: UUID,
        thread: OmnichannelThread,
        evaluated_at: datetime,
    ) -> _AbsenceResponseRoute | None:
        if thread.status in {ThreadStatus.blocked, ThreadStatus.closed}:
            return None
        flags = thread.thread_metadata or {}
        if any(bool(flags.get(key)) for key in ("blockedByContact", "optedOut", "suppressed")):
            return None

        inbound_message = await self.db.scalar(
            select(OmnichannelMessage)
            .where(
                OmnichannelMessage.workspace_id == workspace_id,
                OmnichannelMessage.thread_id == thread.id,
                OmnichannelMessage.channel == OmnichannelChannel.whatsapp,
                OmnichannelMessage.direction == MessageDirection.inbound,
                OmnichannelMessage.endpoint_id.is_not(None),
                OmnichannelMessage.binding_id.is_not(None),
                OmnichannelMessage.connection_id.is_not(None),
            )
            .order_by(
                OmnichannelMessage.occurred_at.desc(),
                OmnichannelMessage.id.desc(),
            )
        )
        if inbound_message is None:
            return None

        connection = await self.db.scalar(
            select(OmnichannelChannelConnection).where(
                OmnichannelChannelConnection.workspace_id == workspace_id,
                OmnichannelChannelConnection.id == inbound_message.connection_id,
                OmnichannelChannelConnection.channel == OmnichannelChannel.whatsapp.value,
                OmnichannelChannelConnection.provider == "meta_cloud",
                OmnichannelChannelConnection.status.in_(_ACTIVE_SCHEDULE_CONNECTION_STATES),
                OmnichannelChannelConnection.transport_mode.in_(
                    _SUPPORTED_ABSENCE_RESPONSE_TRANSPORTS
                ),
            )
        )
        if connection is None:
            return None
        endpoint = await self.db.scalar(
            select(OmnichannelEndpoint).where(
                OmnichannelEndpoint.workspace_id == workspace_id,
                OmnichannelEndpoint.id == inbound_message.endpoint_id,
                OmnichannelEndpoint.connection_id == connection.id,
                OmnichannelEndpoint.channel == OmnichannelChannel.whatsapp.value,
                OmnichannelEndpoint.blocked_at.is_(None),
                OmnichannelEndpoint.consent_state.not_in({"blocked", "opted_out"}),
            )
        )
        if endpoint is None:
            return None
        binding = await self.db.scalar(
            select(OmnichannelThreadBinding).where(
                OmnichannelThreadBinding.workspace_id == workspace_id,
                OmnichannelThreadBinding.id == inbound_message.binding_id,
                OmnichannelThreadBinding.thread_id == thread.id,
                OmnichannelThreadBinding.endpoint_id == endpoint.id,
                OmnichannelThreadBinding.connection_id == connection.id,
                OmnichannelThreadBinding.binding_state == "active",
            )
        )
        if binding is None:
            return None

        try:
            service_window_hours = max(
                1,
                min(int((connection.routing_policy or {}).get("serviceWindowHours", 24)), 168),
            )
        except (TypeError, ValueError):
            service_window_hours = 24
        if evaluated_at > _aware(inbound_message.occurred_at) + timedelta(
            hours=service_window_hours
        ):
            return None
        return _AbsenceResponseRoute(connection, endpoint, binding, inbound_message)

    async def _schedule_approved_absence_response(
        self,
        *,
        workspace_id: UUID,
        thread: OmnichannelThread,
        routing_event: OmnichannelRoutingEvent,
        decision: _TransferRoutingDecision,
    ) -> OmnichannelScheduledMessage | None:
        availability = decision.requested_availability
        message_body = (availability.absence_message or "").strip() if availability else ""
        if (
            not decision.applied
            or decision.manual_override
            or availability is None
            or availability.is_available
            or not message_body
            or decision.approval_state is None
            or decision.requested_user_id is None
        ):
            return None

        route = await self._absence_response_route(
            workspace_id=workspace_id,
            thread=thread,
            evaluated_at=decision.evaluated_at,
        )
        if route is None:
            return None

        state_fingerprint = _canonical_hash(
            {
                "workspaceId": str(workspace_id),
                "threadId": str(thread.id),
                "staffUserId": str(decision.requested_user_id),
                "approvalState": decision.approval_state,
                "messageSha256": hashlib.sha256(message_body.encode("utf-8")).hexdigest(),
            }
        )
        idempotency_key = f"staff-absence:{state_fingerprint}"
        await acquire_transaction_advisory_lock(
            self.db,
            namespace="omnichannel_staff_absence_response",
            value=f"{workspace_id}:{idempotency_key}",
        )
        existing = await self.db.scalar(
            select(OmnichannelScheduledMessage).where(
                OmnichannelScheduledMessage.workspace_id == workspace_id,
                OmnichannelScheduledMessage.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if (
                existing.thread_id != thread.id
                or existing.created_by_user_id != decision.requested_user_id
                or existing.body != message_body
            ):
                raise OmnichannelConflictError(
                    "Chave da resposta de ausência já vinculada a outro conteúdo."
                )
            return existing

        author_label = str(
            await self.db.scalar(
                select(User.name)
                .join(WorkspaceMembership, WorkspaceMembership.user_id == User.id)
                .where(
                    User.id == decision.requested_user_id,
                    WorkspaceMembership.workspace_id == workspace_id,
                    WorkspaceMembership.status == MembershipStatus.active,
                )
            )
            or "Responsável indisponível"
        )
        availability_evidence = {
            "policy": _AVAILABILITY_ROUTING_POLICY,
            "routingEventId": str(routing_event.id),
            "requestedUserId": str(decision.requested_user_id),
            "effectiveUserId": (
                str(decision.effective_user_id) if decision.effective_user_id else None
            ),
            "effectiveTeamId": (
                str(decision.effective_team_id) if decision.effective_team_id else None
            ),
            "resolutionReason": decision.reason,
            "evaluatedAt": decision.evaluated_at.isoformat(),
            "approvalState": decision.approval_state,
        }
        scheduled = OmnichannelScheduledMessage(
            workspace_id=workspace_id,
            thread_id=thread.id,
            endpoint_id=route.endpoint.id,
            connection_id=route.connection.id,
            created_by_user_id=decision.requested_user_id,
            author_type="human",
            author_label=author_label,
            connection_key=route.connection.connection_key,
            channel=OmnichannelChannel.whatsapp.value,
            body=message_body,
            attachments=[],
            message_payload={
                "kind": "text",
                "body": message_body,
                "previewUrl": False,
            },
            scheduled_at=decision.evaluated_at,
            timezone=availability.timezone,
            state="scheduled",
            idempotency_key=idempotency_key,
            provider_receipt={
                "provider": route.connection.provider,
                "localOnly": not external_delivery_allowed(),
                "externalActionsExecuted": 0,
                "staffAvailability": availability_evidence,
            },
        )
        self.db.add(scheduled)
        await self.db.flush()
        schedule_event = await self._append_schedule_event(
            scheduled,
            event_type="created",
            previous_state="none",
            next_state="scheduled",
            idempotency_key=f"schedule-created:{idempotency_key}",
            payload={
                "threadId": str(thread.id),
                "channel": OmnichannelChannel.whatsapp.value,
                "connectionKey": route.connection.connection_key,
                "scheduledAt": decision.evaluated_at,
                "bodySha256": hashlib.sha256(message_body.encode("utf-8")).hexdigest(),
            },
            metadata={
                "source": "staff_availability_runtime",
                "availability": availability_evidence,
                "inboundMessageId": str(route.inbound_message.id),
            },
        )
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=None,
            actor_type="system",
            actor_label="Roteador de disponibilidade",
            event_type="omnichannel_staff_absence_response_scheduled",
            entity_type="omnichannel_scheduled_message",
            entity_id=scheduled.id,
            summary="Resposta humana de ausência aprovada foi agendada.",
            correlation_id=idempotency_key,
            after_data={
                "threadId": str(thread.id),
                "authoredByUserId": str(decision.requested_user_id),
                "routingEventId": str(routing_event.id),
                "scheduleEventId": str(schedule_event.id),
                "approvalState": decision.approval_state,
            },
        )
        queue_realtime_event(
            self.db,
            workspace_id=workspace_id,
            event_type="OMNICHANNEL_SCHEDULE_UPDATED",
            data={
                "thread_id": str(thread.id),
                "scheduled_message_id": str(scheduled.id),
                "state": scheduled.state,
                "event_id": str(schedule_event.id),
            },
        )
        return scheduled

    async def list_identity_reviews(
        self,
        workspace_id: UUID,
        *,
        status: str | None = "open",
        limit: int = 100,
    ) -> list[OmnichannelIdentityReviewCase]:
        filters = [OmnichannelIdentityReviewCase.workspace_id == workspace_id]
        if status:
            filters.append(OmnichannelIdentityReviewCase.status == status)
        result = await self.db.execute(
            select(OmnichannelIdentityReviewCase)
            .where(*filters)
            .order_by(OmnichannelIdentityReviewCase.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def _identity_snapshot(
        self, workspace_id: UUID, identity: OmnichannelIdentity
    ) -> dict[str, Any]:
        endpoints = list(
            (
                await self.db.execute(
                    select(OmnichannelEndpoint.id).where(
                        OmnichannelEndpoint.workspace_id == workspace_id,
                        OmnichannelEndpoint.identity_id == identity.id,
                    )
                )
            ).scalars()
        )
        threads = list(
            (
                await self.db.execute(
                    select(OmnichannelThread.id, OmnichannelThread.matter_id).where(
                        OmnichannelThread.workspace_id == workspace_id,
                        OmnichannelThread.identity_id == identity.id,
                    )
                )
            ).all()
        )
        return {
            "identityId": str(identity.id),
            "clientId": str(identity.client_id) if identity.client_id else None,
            "displayName": identity.display_name,
            "canonicalPhone": identity.canonical_phone,
            "canonicalEmail": identity.canonical_email,
            "state": identity.identity_state,
            "mergeVersion": identity.merge_version,
            "endpointIds": [str(item) for item in endpoints],
            "threads": [
                {
                    "threadId": str(thread_id),
                    "matterId": str(matter_id) if matter_id else None,
                }
                for thread_id, matter_id in threads
            ],
        }

    async def merge_identities(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        target_identity_id: UUID,
        payload: IdentityMergeRequest,
    ) -> dict[str, Any]:
        if payload.source_identity_id == target_identity_id:
            raise OmnichannelConflictError("A identidade de origem e destino devem ser diferentes.")
        existing_event = await self.db.scalar(
            select(OmnichannelIdentityMutationEvent).where(
                OmnichannelIdentityMutationEvent.workspace_id == workspace_id,
                OmnichannelIdentityMutationEvent.idempotency_key == payload.idempotency_key,
            )
        )
        if existing_event:
            if (
                existing_event.operation != "merge"
                or existing_event.source_identity_id != payload.source_identity_id
                or existing_event.target_identity_id != target_identity_id
            ):
                raise OmnichannelConflictError(
                    "A chave de idempotência já foi usada por outra operação de identidade."
                )
            source = await self._identity(workspace_id, existing_event.source_identity_id)
            target = await self._identity(workspace_id, existing_event.target_identity_id)
            after = existing_event.after_snapshot or {}
            return {
                "source": source,
                "target": target,
                "event": existing_event,
                "moved_endpoint_ids": [UUID(item) for item in after.get("movedEndpointIds", [])],
                "moved_thread_ids": [UUID(item) for item in after.get("movedThreadIds", [])],
                "deduplicated": True,
            }

        identities = list(
            (
                await self.db.execute(
                    select(OmnichannelIdentity)
                    .where(
                        OmnichannelIdentity.workspace_id == workspace_id,
                        OmnichannelIdentity.id.in_(
                            [payload.source_identity_id, target_identity_id]
                        ),
                    )
                    .with_for_update()
                )
            ).scalars()
        )
        by_id = {item.id: item for item in identities}
        source = by_id.get(payload.source_identity_id)
        target = by_id.get(target_identity_id)
        if source is None or target is None:
            raise LookupError("Identidade de origem ou destino não encontrada no workspace.")
        if source.merge_version != payload.expected_source_version:
            raise OmnichannelVersionConflictError(source.merge_version)
        if target.merge_version != payload.expected_target_version:
            raise OmnichannelVersionConflictError(target.merge_version)
        if source.identity_state == "merged":
            raise OmnichannelConflictError("A identidade de origem já foi incorporada.")
        if source.client_id and target.client_id and source.client_id != target.client_id:
            raise OmnichannelConflictError(
                "As identidades pertencem a clientes diferentes; revise antes de unir."
            )
        if source.canonical_phone and target.canonical_phone:
            if source.canonical_phone != target.canonical_phone:
                raise OmnichannelConflictError(
                    "Telefones verificados diferentes impedem a união automática."
                )
        if source.canonical_email and target.canonical_email:
            if source.canonical_email != target.canonical_email:
                raise OmnichannelConflictError(
                    "E-mails verificados diferentes impedem a união automática."
                )

        source_threads = list(
            (
                await self.db.execute(
                    select(OmnichannelThread)
                    .where(
                        OmnichannelThread.workspace_id == workspace_id,
                        OmnichannelThread.identity_id == source.id,
                    )
                    .with_for_update()
                )
            ).scalars()
        )
        target_threads = list(
            (
                await self.db.execute(
                    select(OmnichannelThread)
                    .where(
                        OmnichannelThread.workspace_id == workspace_id,
                        OmnichannelThread.identity_id == target.id,
                    )
                    .with_for_update()
                )
            ).scalars()
        )
        source_scopes = {item.matter_id for item in source_threads}
        target_scopes = {item.matter_id for item in target_threads}
        if source_scopes & target_scopes:
            raise OmnichannelConflictError(
                "As identidades possuem conversas no mesmo contexto de caso; "
                "resolva as conversas duplicadas antes de unir."
            )
        source_endpoints = list(
            (
                await self.db.execute(
                    select(OmnichannelEndpoint)
                    .where(
                        OmnichannelEndpoint.workspace_id == workspace_id,
                        OmnichannelEndpoint.identity_id == source.id,
                    )
                    .with_for_update()
                )
            ).scalars()
        )
        before = {
            "source": await self._identity_snapshot(workspace_id, source),
            "target": await self._identity_snapshot(workspace_id, target),
            "reason": payload.reason,
        }

        for endpoint in source_endpoints:
            endpoint.identity_id = target.id
        for thread in source_threads:
            thread.identity_id = target.id
        target.client_id = target.client_id or source.client_id
        target.canonical_phone = target.canonical_phone or source.canonical_phone
        target.canonical_email = target.canonical_email or source.canonical_email
        target.merge_version += 1
        target.updated_at = _now()
        source.canonical_phone = None
        source.canonical_email = None
        source.identity_state = "merged"
        source.merge_version += 1
        source_metadata = dict(source.identity_metadata or {})
        source_metadata.update(
            {
                "mergedIntoIdentityId": str(target.id),
                "mergedAt": _now().isoformat(),
                "mergeReason": payload.reason,
            }
        )
        source.identity_metadata = source_metadata
        source.updated_at = _now()
        await self.db.flush()
        moved_endpoint_ids = [item.id for item in source_endpoints]
        moved_thread_ids = [item.id for item in source_threads]
        after = {
            "source": await self._identity_snapshot(workspace_id, source),
            "target": await self._identity_snapshot(workspace_id, target),
            "movedEndpointIds": [str(item) for item in moved_endpoint_ids],
            "movedThreadIds": [str(item) for item in moved_thread_ids],
        }
        event = OmnichannelIdentityMutationEvent(
            workspace_id=workspace_id,
            source_identity_id=source.id,
            target_identity_id=target.id,
            actor_user_id=actor_user_id,
            operation="merge",
            idempotency_key=payload.idempotency_key,
            before_snapshot=before,
            after_snapshot=after,
        )
        self.db.add(event)
        await self.db.flush()
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label="Operador KLM",
            event_type="omnichannel_identity_merged",
            entity_type="omnichannel_identity",
            entity_id=target.id,
            summary=f"Identidade {source.id} incorporada com trilha auditável.",
            before_data=before,
            after_data=after,
        )
        return {
            "source": source,
            "target": target,
            "event": event,
            "moved_endpoint_ids": moved_endpoint_ids,
            "moved_thread_ids": moved_thread_ids,
            "deduplicated": False,
        }

    async def split_identity(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        source_identity_id: UUID,
        payload: IdentitySplitRequest,
    ) -> dict[str, Any]:
        existing_event = await self.db.scalar(
            select(OmnichannelIdentityMutationEvent).where(
                OmnichannelIdentityMutationEvent.workspace_id == workspace_id,
                OmnichannelIdentityMutationEvent.idempotency_key == payload.idempotency_key,
            )
        )
        if existing_event:
            if (
                existing_event.operation != "split"
                or existing_event.source_identity_id != source_identity_id
            ):
                raise OmnichannelConflictError(
                    "A chave de idempotência já foi usada por outra operação de identidade."
                )
            source = await self._identity(workspace_id, source_identity_id)
            target = await self._identity(workspace_id, existing_event.target_identity_id)
            after = existing_event.after_snapshot or {}
            return {
                "source": source,
                "target": target,
                "event": existing_event,
                "moved_endpoint_ids": [UUID(item) for item in after.get("movedEndpointIds", [])],
                "moved_thread_ids": [UUID(item) for item in after.get("movedThreadIds", [])],
                "deduplicated": True,
            }

        source = await self.db.scalar(
            select(OmnichannelIdentity)
            .where(
                OmnichannelIdentity.workspace_id == workspace_id,
                OmnichannelIdentity.id == source_identity_id,
            )
            .with_for_update()
        )
        if source is None:
            raise LookupError("Identidade omnichannel não encontrada no workspace.")
        if source.merge_version != payload.expected_source_version:
            raise OmnichannelVersionConflictError(source.merge_version)
        endpoint_ids = set(payload.endpoint_ids)
        thread_ids = set(payload.thread_ids)
        selected_endpoints = (
            list(
                (
                    await self.db.execute(
                        select(OmnichannelEndpoint)
                        .where(
                            OmnichannelEndpoint.workspace_id == workspace_id,
                            OmnichannelEndpoint.identity_id == source.id,
                            OmnichannelEndpoint.id.in_(endpoint_ids),
                        )
                        .with_for_update()
                    )
                ).scalars()
            )
            if endpoint_ids
            else []
        )
        selected_threads = (
            list(
                (
                    await self.db.execute(
                        select(OmnichannelThread)
                        .where(
                            OmnichannelThread.workspace_id == workspace_id,
                            OmnichannelThread.identity_id == source.id,
                            OmnichannelThread.id.in_(thread_ids),
                        )
                        .with_for_update()
                    )
                ).scalars()
            )
            if thread_ids
            else []
        )
        if len(selected_endpoints) != len(endpoint_ids) or len(selected_threads) != len(thread_ids):
            raise OmnichannelConflictError(
                "Endpoint ou conversa não pertence à identidade selecionada."
            )
        if thread_ids:
            bound_endpoint_ids = set(
                (
                    await self.db.execute(
                        select(OmnichannelThreadBinding.endpoint_id).where(
                            OmnichannelThreadBinding.workspace_id == workspace_id,
                            OmnichannelThreadBinding.thread_id.in_(thread_ids),
                            OmnichannelThreadBinding.endpoint_id.is_not(None),
                        )
                    )
                ).scalars()
            )
            missing = bound_endpoint_ids - endpoint_ids
            if missing:
                extra_endpoints = list(
                    (
                        await self.db.execute(
                            select(OmnichannelEndpoint)
                            .where(
                                OmnichannelEndpoint.workspace_id == workspace_id,
                                OmnichannelEndpoint.identity_id == source.id,
                                OmnichannelEndpoint.id.in_(missing),
                            )
                            .with_for_update()
                        )
                    ).scalars()
                )
                selected_endpoints.extend(extra_endpoints)
                endpoint_ids.update(item.id for item in extra_endpoints)
        if endpoint_ids:
            bound_thread_ids = set(
                (
                    await self.db.execute(
                        select(OmnichannelThreadBinding.thread_id).where(
                            OmnichannelThreadBinding.workspace_id == workspace_id,
                            OmnichannelThreadBinding.endpoint_id.in_(endpoint_ids),
                        )
                    )
                ).scalars()
            )
            if bound_thread_ids - thread_ids:
                raise OmnichannelConflictError(
                    "Um endpoint selecionado ainda possui vínculo com uma conversa não selecionada."
                )
        all_endpoint_count = int(
            await self.db.scalar(
                select(func.count(OmnichannelEndpoint.id)).where(
                    OmnichannelEndpoint.workspace_id == workspace_id,
                    OmnichannelEndpoint.identity_id == source.id,
                )
            )
            or 0
        )
        all_thread_count = int(
            await self.db.scalar(
                select(func.count(OmnichannelThread.id)).where(
                    OmnichannelThread.workspace_id == workspace_id,
                    OmnichannelThread.identity_id == source.id,
                )
            )
            or 0
        )
        if len(endpoint_ids) == all_endpoint_count and len(thread_ids) == all_thread_count:
            raise OmnichannelConflictError(
                "A separação precisa deixar ao menos um endpoint ou conversa "
                "na identidade original."
            )
        selected_client_ids = {item.client_id for item in selected_threads if item.client_id}
        if len(selected_client_ids) > 1:
            raise OmnichannelConflictError(
                "As conversas selecionadas pertencem a clientes diferentes; "
                "separe uma pessoa por vez."
            )
        target_client_id = next(iter(selected_client_ids), None)
        if target_client_id and source.client_id not in {None, target_client_id}:
            raise OmnichannelConflictError(
                "O cliente das conversas selecionadas diverge da identidade original."
            )
        before = {
            "source": await self._identity_snapshot(workspace_id, source),
            "reason": payload.reason,
        }
        phone = _normalize_phone(payload.canonical_phone)
        email = _normalize_email(payload.canonical_email)
        selected_addresses = {item.normalized_address for item in selected_endpoints}
        if not phone and source.canonical_phone in selected_addresses:
            phone = source.canonical_phone
        if not email and source.canonical_email in selected_addresses:
            email = source.canonical_email
        if phone == source.canonical_phone:
            source.canonical_phone = None
        if email == source.canonical_email:
            source.canonical_email = None
        target = OmnichannelIdentity(
            workspace_id=workspace_id,
            client_id=target_client_id,
            display_name=payload.display_name.strip(),
            canonical_phone=phone,
            canonical_email=email,
            identity_state="verified" if phone or email else "provisional",
            source="manual_split",
            identity_metadata={
                "splitFromIdentityId": str(source.id),
                "splitReason": payload.reason,
            },
        )
        self.db.add(target)
        await self.db.flush()
        for endpoint in selected_endpoints:
            endpoint.identity_id = target.id
        for thread in selected_threads:
            thread.identity_id = target.id
        source.merge_version += 1
        source.updated_at = _now()
        await self.db.flush()
        moved_endpoint_ids = [item.id for item in selected_endpoints]
        moved_thread_ids = [item.id for item in selected_threads]
        after = {
            "source": await self._identity_snapshot(workspace_id, source),
            "target": await self._identity_snapshot(workspace_id, target),
            "movedEndpointIds": [str(item) for item in moved_endpoint_ids],
            "movedThreadIds": [str(item) for item in moved_thread_ids],
        }
        event = OmnichannelIdentityMutationEvent(
            workspace_id=workspace_id,
            source_identity_id=source.id,
            target_identity_id=target.id,
            actor_user_id=actor_user_id,
            operation="split",
            idempotency_key=payload.idempotency_key,
            before_snapshot=before,
            after_snapshot=after,
        )
        self.db.add(event)
        await self.db.flush()
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label="Operador KLM",
            event_type="omnichannel_identity_split",
            entity_type="omnichannel_identity",
            entity_id=target.id,
            summary=f"Identidade {target.id} separada com trilha auditável.",
            before_data=before,
            after_data=after,
        )
        return {
            "source": source,
            "target": target,
            "event": event,
            "moved_endpoint_ids": moved_endpoint_ids,
            "moved_thread_ids": moved_thread_ids,
            "deduplicated": False,
        }

    async def create_team(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        payload: WorkspaceTeamCreate,
    ) -> WorkspaceTeam:
        duplicate = await self.db.scalar(
            select(WorkspaceTeam.id).where(
                WorkspaceTeam.workspace_id == workspace_id,
                WorkspaceTeam.slug == payload.slug,
            )
        )
        if duplicate:
            raise OmnichannelConflictError("Já existe uma equipe com esse identificador.")
        team = WorkspaceTeam(workspace_id=workspace_id, **payload.model_dump())
        self.db.add(team)
        await self.db.flush()
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="omnichannel_team_created",
            entity_type="workspace_team",
            entity_id=team.id,
            summary=f"Equipe {team.name} criada.",
        )
        queue_realtime_event(
            self.db,
            workspace_id=workspace_id,
            event_type="OMNICHANNEL_TEAM_CHANGED",
            data={"team_id": str(team.id), "operation": "created"},
        )
        return team

    async def update_team(
        self,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        payload: WorkspaceTeamUpdate,
    ) -> WorkspaceTeam:
        team = await self._team(workspace_id, team_id)
        changes = payload.model_dump(exclude_unset=True)
        before = {key: getattr(team, key) for key in changes}
        for key, value in changes.items():
            setattr(team, key, value)
        team.updated_at = _now()
        await self.db.flush()
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="omnichannel_team_updated",
            entity_type="workspace_team",
            entity_id=team.id,
            summary=f"Equipe {team.name} atualizada.",
            before_data={key: str(value) for key, value in before.items()},
            after_data={key: str(value) for key, value in changes.items()},
        )
        queue_realtime_event(
            self.db,
            workspace_id=workspace_id,
            event_type="OMNICHANNEL_TEAM_CHANGED",
            data={"team_id": str(team.id), "operation": "updated"},
        )
        return team

    async def list_teams(self, workspace_id: UUID) -> list[dict[str, Any]]:
        member_counts = (
            select(
                WorkspaceTeamMember.team_id.label("team_id"),
                func.count(WorkspaceTeamMember.id).label("member_count"),
                func.sum(case((WorkspaceTeamMember.is_available.is_(True), 1), else_=0)).label(
                    "available_count"
                ),
            )
            .where(WorkspaceTeamMember.workspace_id == workspace_id)
            .group_by(WorkspaceTeamMember.team_id)
            .subquery()
        )
        thread_counts = (
            select(
                OmnichannelThread.assigned_team_id.label("team_id"),
                func.count(OmnichannelThread.id).label("thread_count"),
            )
            .where(
                OmnichannelThread.workspace_id == workspace_id,
                OmnichannelThread.status.notin_([ThreadStatus.closed, ThreadStatus.blocked]),
            )
            .group_by(OmnichannelThread.assigned_team_id)
            .subquery()
        )
        result = await self.db.execute(
            select(
                WorkspaceTeam,
                func.coalesce(member_counts.c.member_count, 0),
                func.coalesce(member_counts.c.available_count, 0),
                func.coalesce(thread_counts.c.thread_count, 0),
            )
            .outerjoin(member_counts, member_counts.c.team_id == WorkspaceTeam.id)
            .outerjoin(thread_counts, thread_counts.c.team_id == WorkspaceTeam.id)
            .where(WorkspaceTeam.workspace_id == workspace_id)
            .order_by(WorkspaceTeam.is_active.desc(), WorkspaceTeam.name.asc())
        )
        return [
            {
                "team": team,
                "member_count": int(member_count or 0),
                "available_count": int(available_count or 0),
                "open_thread_count": int(thread_count or 0),
            }
            for team, member_count, available_count, thread_count in result.all()
        ]

    async def add_team_member(
        self,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        payload: WorkspaceTeamMemberCreate,
    ) -> tuple[WorkspaceTeamMember, User]:
        await self._team(workspace_id, team_id, active=True)
        membership = await self._active_membership(workspace_id, payload.user_id)
        existing = await self.db.scalar(
            select(WorkspaceTeamMember).where(
                WorkspaceTeamMember.workspace_id == workspace_id,
                WorkspaceTeamMember.team_id == team_id,
                WorkspaceTeamMember.user_id == payload.user_id,
            )
        )
        values = payload.model_dump(exclude={"user_id"})
        if existing:
            for key, value in values.items():
                setattr(existing, key, value)
            existing.updated_at = _now()
            member = existing
        else:
            member = WorkspaceTeamMember(
                workspace_id=workspace_id,
                team_id=team_id,
                membership_id=membership.id,
                user_id=payload.user_id,
                **values,
            )
            self.db.add(member)
        user = await self.db.scalar(select(User).where(User.id == payload.user_id))
        assert user is not None
        await self.db.flush()
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="omnichannel_team_member_upserted",
            entity_type="workspace_team_member",
            entity_id=member.id,
            summary=f"{user.name} incluído ou atualizado na equipe.",
        )
        return member, user

    async def list_team_members(
        self, workspace_id: UUID, team_id: UUID
    ) -> list[tuple[WorkspaceTeamMember, User]]:
        await self._team(workspace_id, team_id)
        result = await self.db.execute(
            select(WorkspaceTeamMember, User)
            .join(User, User.id == WorkspaceTeamMember.user_id)
            .where(
                WorkspaceTeamMember.workspace_id == workspace_id,
                WorkspaceTeamMember.team_id == team_id,
            )
            .order_by(WorkspaceTeamMember.member_role.desc(), User.name.asc())
        )
        return list(result.all())

    async def remove_team_member(
        self,
        workspace_id: UUID,
        team_id: UUID,
        user_id: UUID,
        actor_user_id: UUID,
    ) -> None:
        member = await self.db.scalar(
            select(WorkspaceTeamMember).where(
                WorkspaceTeamMember.workspace_id == workspace_id,
                WorkspaceTeamMember.team_id == team_id,
                WorkspaceTeamMember.user_id == user_id,
            )
        )
        if not member:
            raise LookupError("Membro não encontrado nessa equipe.")
        member_id = member.id
        await self.db.delete(member)
        await self.db.flush()
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="omnichannel_team_member_removed",
            entity_type="workspace_team_member",
            entity_id=member_id,
            summary="Membro removido da equipe.",
        )

    async def transfer_thread(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        actor_user_id: UUID,
        payload: ThreadTransferRequest,
    ) -> tuple[OmnichannelThread, OmnichannelRoutingEvent, bool]:
        request_correlation = _transfer_request_correlation(thread_id, payload)
        await acquire_transaction_advisory_lock(
            self.db,
            namespace="omnichannel_thread_transfer",
            value=f"{workspace_id}:{payload.idempotency_key}",
        )
        existing = await self.db.scalar(
            select(OmnichannelRoutingEvent).where(
                OmnichannelRoutingEvent.workspace_id == workspace_id,
                OmnichannelRoutingEvent.idempotency_key == payload.idempotency_key,
            )
        )
        if existing:
            legacy_request_matches = not (
                existing.thread_id != thread_id
                or existing.to_team_id != payload.to_team_id
                or existing.to_user_id != payload.to_user_id
                or existing.expected_thread_version != payload.expected_version
            )
            is_legacy_event = (existing.correlation_id or "").startswith("thread:")
            if existing.correlation_id != request_correlation and not (
                is_legacy_event and legacy_request_matches
            ):
                raise OmnichannelConflictError(
                    "Chave de idempotência já usada por outra transferência."
                )
            return await self._thread(workspace_id, thread_id), existing, True

        thread = await self._thread(workspace_id, thread_id, lock=True)
        if thread.version != payload.expected_version:
            raise OmnichannelVersionConflictError(thread.version)
        evaluated_at = _now()
        routing = await self._resolve_transfer_routing(
            workspace_id=workspace_id,
            payload=payload,
            evaluated_at=evaluated_at,
        )

        from_user_id = thread.assigned_human_id
        from_team_id = thread.assigned_team_id
        result_version = thread.version + 1
        event = OmnichannelRoutingEvent(
            workspace_id=workspace_id,
            thread_id=thread.id,
            actor_user_id=actor_user_id,
            from_user_id=from_user_id,
            to_user_id=routing.effective_user_id,
            from_team_id=from_team_id,
            to_team_id=routing.effective_team_id,
            action="automatic_swap" if routing.applied else "transfer",
            reason=payload.reason,
            source=payload.source,
            expected_thread_version=payload.expected_version,
            result_thread_version=result_version,
            idempotency_key=payload.idempotency_key,
            correlation_id=request_correlation,
            occurred_at=evaluated_at,
        )
        self.db.add(event)
        thread.assigned_team_id = routing.effective_team_id
        thread.assigned_human_id = routing.effective_user_id
        if payload.queue:
            thread.queue = payload.queue
        if routing.effective_user_id:
            thread.status = ThreadStatus.human_active
        elif routing.effective_team_id:
            thread.status = ThreadStatus.waiting_human
        elif thread.status == ThreadStatus.human_active:
            thread.status = ThreadStatus.waiting_human
        thread.version = result_version
        thread.updated_at = evaluated_at
        await self.db.flush()
        absence_response = await self._schedule_approved_absence_response(
            workspace_id=workspace_id,
            thread=thread,
            routing_event=event,
            decision=routing,
        )
        routing_audit = self._routing_audit_data(routing)
        routing_audit["absenceResponseScheduledId"] = (
            str(absence_response.id) if absence_response else None
        )
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="omnichannel_thread_transferred",
            entity_type="omnichannel_thread",
            entity_id=thread.id,
            summary="Conversa transferida com controle de versão.",
            correlation_id=payload.idempotency_key,
            before_data={
                "assignedTeamId": str(from_team_id) if from_team_id else None,
                "assignedHumanId": str(from_user_id) if from_user_id else None,
                "version": payload.expected_version,
            },
            after_data={
                "assignedTeamId": (
                    str(routing.effective_team_id) if routing.effective_team_id else None
                ),
                "assignedHumanId": (
                    str(routing.effective_user_id) if routing.effective_user_id else None
                ),
                "version": result_version,
                "availabilityRouting": routing_audit,
            },
        )
        queue_realtime_event(
            self.db,
            workspace_id=workspace_id,
            event_type="OMNICHANNEL_THREAD_TRANSFERRED",
            data={
                "thread_id": str(thread.id),
                "team_id": (str(routing.effective_team_id) if routing.effective_team_id else None),
                "user_id": (str(routing.effective_user_id) if routing.effective_user_id else None),
                "version": result_version,
            },
        )
        return thread, event, False

    async def _resolve_canonical(
        self,
        workspace_id: UUID,
        payload: CanonicalChannelEventIngest,
    ) -> CanonicalResolution:
        phone_source = payload.canonical_phone
        if phone_source is None and payload.channel.value in {"whatsapp", "audio"}:
            phone_source = payload.address
        phone = _normalize_phone(phone_source)
        email = _normalize_email(payload.canonical_email)
        connection = await self.db.scalar(
            select(OmnichannelChannelConnection).where(
                OmnichannelChannelConnection.workspace_id == workspace_id,
                OmnichannelChannelConnection.provider == payload.provider,
                OmnichannelChannelConnection.connection_key == payload.connection_key,
            )
        )
        binding = await self.db.scalar(
            select(OmnichannelThreadBinding).where(
                OmnichannelThreadBinding.workspace_id == workspace_id,
                OmnichannelThreadBinding.provider == payload.provider,
                OmnichannelThreadBinding.connection_key == payload.connection_key,
                OmnichannelThreadBinding.external_thread_id == payload.external_thread_id,
            )
        )
        bound_thread = (
            await self._thread(workspace_id, binding.thread_id, lock=True) if binding else None
        )
        # A provider thread binding is stronger evidence than a mutable phone
        # representation. Meta may alternate the Brazilian ninth digit while
        # keeping the same conversation/contact binding. Reusing the endpoint
        # already bound to that thread prevents a second canonical identity.
        endpoint = None
        if binding and binding.endpoint_id:
            endpoint = await self.db.scalar(
                select(OmnichannelEndpoint).where(
                    OmnichannelEndpoint.workspace_id == workspace_id,
                    OmnichannelEndpoint.id == binding.endpoint_id,
                )
            )
        if endpoint is None:
            external_user_aliases = _phone_aliases(payload.external_user_id)
            external_user_filters = [
                OmnichannelEndpoint.external_user_id == payload.external_user_id
            ]
            if payload.channel.value in {"whatsapp", "audio"} and external_user_aliases:
                external_user_filters = [
                    OmnichannelEndpoint.external_user_id.in_(external_user_aliases)
                ]
            endpoint = await self.db.scalar(
                select(OmnichannelEndpoint).where(
                    OmnichannelEndpoint.workspace_id == workspace_id,
                    OmnichannelEndpoint.provider == payload.provider,
                    OmnichannelEndpoint.connection_key == payload.connection_key,
                    *external_user_filters,
                )
            )
        identity = None
        if endpoint:
            identity = await self._identity(workspace_id, endpoint.identity_id)
            if (
                phone
                and identity.canonical_phone
                and not _phones_are_equivalent(phone, identity.canonical_phone)
            ):
                raise OmnichannelConflictError("Telefone diverge do endpoint já identificado.")
            if email and identity.canonical_email and email != identity.canonical_email:
                raise OmnichannelConflictError("E-mail diverge do endpoint já identificado.")
            if connection and endpoint.connection_id is None:
                endpoint.connection_id = connection.id
                endpoint.capabilities = dict(connection.capabilities or {})
                endpoint.external_account_id = (
                    endpoint.external_account_id or connection.external_account_id
                )
        else:
            filters = []
            if phone:
                filters.append(
                    OmnichannelIdentity.canonical_phone.in_(_phone_aliases(phone))
                )
            if email:
                filters.append(OmnichannelIdentity.canonical_email == email)
            if payload.client_id:
                filters.append(OmnichannelIdentity.client_id == payload.client_id)
            candidates: list[OmnichannelIdentity] = []
            if filters:
                result = await self.db.execute(
                    select(OmnichannelIdentity).where(
                        OmnichannelIdentity.workspace_id == workspace_id,
                        or_(*filters),
                    )
                )
                candidates = list(result.scalars().all())
            candidate_ids = {candidate.id for candidate in candidates}
            if len(candidate_ids) > 1:
                raise OmnichannelConflictError(
                    "Telefone, e-mail ou cliente apontam para identidades diferentes; "
                    "faça a revisão de identidade antes de unir."
                )
            identity = candidates[0] if candidates else None
            if identity is None:
                identity = OmnichannelIdentity(
                    workspace_id=workspace_id,
                    client_id=payload.client_id,
                    display_name=payload.display_name.strip(),
                    canonical_phone=phone,
                    canonical_email=email,
                    identity_state="verified" if phone or email else "provisional",
                    source=payload.provider,
                    identity_metadata={"firstChannel": payload.channel.value},
                )
                self.db.add(identity)
                try:
                    await self.db.flush()
                except IntegrityError as error:
                    raise OmnichannelConflictError(
                        "Outra ingestão criou a identidade; repita o evento com a mesma chave."
                    ) from error
            else:
                if payload.client_id and identity.client_id not in {None, payload.client_id}:
                    raise OmnichannelConflictError("Cliente diverge da identidade canônica.")
                identity.client_id = identity.client_id or payload.client_id
                if phone and not identity.canonical_phone:
                    identity.canonical_phone = phone
                if email and not identity.canonical_email:
                    identity.canonical_email = email
                if identity.display_name.lower().startswith(("lead ", "contato ")):
                    identity.display_name = payload.display_name.strip()
                identity.updated_at = _now()

            endpoint = OmnichannelEndpoint(
                workspace_id=workspace_id,
                identity_id=identity.id,
                channel=payload.channel.value,
                provider=payload.provider,
                connection_key=payload.connection_key,
                connection_id=connection.id if connection else None,
                external_account_id=(
                    payload.external_account_id
                    or (connection.external_account_id if connection else None)
                ),
                external_user_id=payload.external_user_id,
                address=payload.address,
                normalized_address=phone or email or payload.address,
                verified_at=_now() if phone or email else None,
                consent_state="unknown",
                capabilities=dict(connection.capabilities or {}) if connection else {},
                endpoint_metadata={},
            )
            self.db.add(endpoint)
            await self.db.flush()

        assert identity is not None and endpoint is not None
        endpoint_metadata = dict(endpoint.endpoint_metadata or {})
        supplied_avatar_url = payload.profile_avatar_url
        if supplied_avatar_url:
            endpoint_metadata.update(
                {
                    "profileAvatarUrl": supplied_avatar_url,
                    "profileAvatarSource": payload.channel.value,
                    "profileAvatarObservedAt": _now().isoformat(),
                }
            )
            endpoint.endpoint_metadata = endpoint_metadata
        resolved_avatar_url = supplied_avatar_url or endpoint_metadata.get("profileAvatarUrl")
        resolved_client_id = payload.client_id or identity.client_id
        if payload.matter_id:
            matter = await self.db.scalar(
                select(Matter).where(
                    Matter.workspace_id == workspace_id,
                    Matter.id == payload.matter_id,
                    Matter.deleted_at.is_(None),
                )
            )
            if matter is None:
                raise LookupError("Caso informado no evento não foi encontrado no workspace.")
            if resolved_client_id and matter.client_id != resolved_client_id:
                raise OmnichannelConflictError(
                    "O caso informado não pertence ao cliente da identidade canônica."
                )
            resolved_client_id = matter.client_id
        if identity.client_id and resolved_client_id and identity.client_id != resolved_client_id:
            raise OmnichannelConflictError("Cliente diverge da identidade canônica.")
        identity.client_id = identity.client_id or resolved_client_id

        if bound_thread:
            if bound_thread.identity_id not in {None, identity.id}:
                raise OmnichannelConflictError(
                    "A conversa externa está ligada a outra identidade canônica."
                )
            if payload.matter_id and bound_thread.matter_id not in {None, payload.matter_id}:
                raise OmnichannelConflictError(
                    "A conversa externa já está ligada a outro caso jurídico."
                )
            if payload.matter_id and bound_thread.matter_id is None:
                duplicate_scope = await self.db.scalar(
                    select(OmnichannelThread.id).where(
                        OmnichannelThread.workspace_id == workspace_id,
                        OmnichannelThread.identity_id == identity.id,
                        OmnichannelThread.matter_id == payload.matter_id,
                        OmnichannelThread.id != bound_thread.id,
                    )
                )
                if duplicate_scope:
                    raise OmnichannelConflictError(
                        "Já existe outra conversa canônica para esta pessoa e caso."
                    )
                bound_thread.matter_id = payload.matter_id
            bound_thread.identity_id = identity.id
            bound_thread.client_id = bound_thread.client_id or resolved_client_id
            thread = bound_thread
        else:
            thread_filters = [
                OmnichannelThread.workspace_id == workspace_id,
                OmnichannelThread.identity_id == identity.id,
            ]
            if payload.matter_id:
                thread_filters.append(OmnichannelThread.matter_id == payload.matter_id)
            else:
                thread_filters.append(OmnichannelThread.matter_id.is_(None))
            thread = await self.db.scalar(
                select(OmnichannelThread).where(*thread_filters).with_for_update()
            )
        if not thread:
            thread = OmnichannelThread(
                workspace_id=workspace_id,
                identity_id=identity.id,
                client_id=resolved_client_id,
                matter_id=payload.matter_id,
                primary_channel=payload.channel,
                preferred_channel=payload.channel.value,
                subject=f"Atendimento de {identity.display_name}",
                queue="new",
                thread_metadata={"canonical": True, "channels": [payload.channel.value]},
            )
            self.db.add(thread)
            await self.db.flush()
        else:
            thread.client_id = thread.client_id or resolved_client_id
            metadata = dict(thread.thread_metadata or {})
            channels = list(
                dict.fromkeys([*(metadata.get("channels") or []), payload.channel.value])
            )
            metadata["channels"] = channels
            metadata["canonical"] = True
            thread.thread_metadata = metadata

        if resolved_avatar_url:
            metadata = dict(thread.thread_metadata or {})
            current_avatar = metadata.get("profileAvatar")
            current_avatar = current_avatar if isinstance(current_avatar, dict) else {}
            current_source = str(
                current_avatar.get("sourceChannel") or current_avatar.get("channel") or ""
            )
            observed_at = endpoint_metadata.get("profileAvatarObservedAt") or _now().isoformat()
            profile_avatar = {
                "url": str(resolved_avatar_url),
                "sourceChannel": payload.channel.value,
                "provider": payload.provider,
                "observedAt": observed_at,
            }
            profiles = dict(metadata.get("profileAvatars") or {})
            profiles[payload.channel.value] = profile_avatar
            metadata["profileAvatars"] = profiles
            if not current_avatar.get("url") or _avatar_priority(payload.channel.value) <= _avatar_priority(current_source):
                metadata["profileAvatar"] = profile_avatar
            thread.thread_metadata = metadata

        identity_metadata = dict(identity.identity_metadata or {})
        if thread.matter_id:
            identity_metadata["relatedMatterIds"] = list(
                dict.fromkeys(
                    [
                        *(identity_metadata.get("relatedMatterIds") or []),
                        str(thread.matter_id),
                    ]
                )
            )
        identity.identity_metadata = identity_metadata
        if binding:
            if binding.thread_id != thread.id:
                raise OmnichannelConflictError(
                    "A conversa externa já está ligada a outro lead canônico."
                )
            if binding.endpoint_id not in {None, endpoint.id}:
                raise OmnichannelConflictError("O vínculo externo aponta para outro endpoint.")
        else:
            binding = OmnichannelThreadBinding(
                workspace_id=workspace_id,
                thread_id=thread.id,
                endpoint_id=endpoint.id,
                provider=payload.provider,
                connection_id=connection.id if connection else None,
                connection_key=payload.connection_key,
                external_thread_id=payload.external_thread_id,
                binding_metadata={"channel": payload.channel.value},
            )
            self.db.add(binding)
            await self.db.flush()
        return CanonicalResolution(identity, endpoint, binding, thread)

    async def list_lead_capture_receipts(
        self,
        workspace_id: UUID,
        *,
        provider: str | None = None,
        state: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        filters = [LeadCaptureReceipt.workspace_id == workspace_id]
        if provider:
            filters.append(LeadCaptureReceipt.provider == provider)
        if state:
            filters.append(LeadCaptureReceipt.state == state)

        total = int(
            await self.db.scalar(select(func.count(LeadCaptureReceipt.id)).where(*filters)) or 0
        )
        result = await self.db.execute(
            select(LeadCaptureReceipt)
            .where(*filters)
            .order_by(LeadCaptureReceipt.created_at.desc(), LeadCaptureReceipt.id.desc())
            .offset(offset)
            .limit(limit)
        )
        receipts = list(result.scalars().all())
        identity_ids = {item.identity_id for item in receipts if item.identity_id}
        thread_ids = {item.thread_id for item in receipts if item.thread_id}
        identities: dict[UUID, OmnichannelIdentity] = {}
        threads: dict[UUID, OmnichannelThread] = {}
        if identity_ids:
            identity_result = await self.db.execute(
                select(OmnichannelIdentity).where(
                    OmnichannelIdentity.workspace_id == workspace_id,
                    OmnichannelIdentity.id.in_(identity_ids),
                )
            )
            identities = {item.id: item for item in identity_result.scalars().all()}
        if thread_ids:
            thread_result = await self.db.execute(
                select(OmnichannelThread).where(
                    OmnichannelThread.workspace_id == workspace_id,
                    OmnichannelThread.id.in_(thread_ids),
                )
            )
            threads = {item.id: item for item in thread_result.scalars().all()}

        grouped = await self.db.execute(
            select(
                LeadCaptureReceipt.provider,
                LeadCaptureReceipt.state,
                func.count(LeadCaptureReceipt.id),
            )
            .where(LeadCaptureReceipt.workspace_id == workspace_id)
            .group_by(LeadCaptureReceipt.provider, LeadCaptureReceipt.state)
        )
        counts_by_provider: dict[str, int] = {}
        counts_by_state: dict[str, int] = {}
        for provider_key, state_key, count_value in grouped.all():
            count = int(count_value)
            counts_by_provider[provider_key] = counts_by_provider.get(provider_key, 0) + count
            counts_by_state[state_key] = counts_by_state.get(state_key, 0) + count

        return {
            "items": [
                {
                    "receipt": receipt,
                    "identity": identities.get(receipt.identity_id),
                    "thread": threads.get(receipt.thread_id),
                    "acquisition": (receipt.result or {}).get("acquisition", {}),
                }
                for receipt in receipts
            ],
            "total": total,
            "has_more": offset + len(receipts) < total,
            "counts_by_provider": counts_by_provider,
            "counts_by_state": counts_by_state,
            "generated_at": _now(),
        }

    async def _load_event_result(
        self, workspace_id: UUID, event: OmnichannelChannelEvent
    ) -> tuple[CanonicalResolution, OmnichannelMessage | None]:
        if event.identity_id is None or event.binding_id is None:
            raise RuntimeError("Evento canônico sem identidade ou vínculo íntegro.")
        identity = await self._identity(workspace_id, event.identity_id)
        thread = await self._thread(workspace_id, event.thread_id)
        binding = await self.db.scalar(
            select(OmnichannelThreadBinding).where(
                OmnichannelThreadBinding.workspace_id == workspace_id,
                OmnichannelThreadBinding.id == event.binding_id,
            )
        )
        if not binding:
            raise RuntimeError("Evento canônico sem vínculo de canal íntegro.")
        endpoint = await self.db.scalar(
            select(OmnichannelEndpoint).where(
                OmnichannelEndpoint.workspace_id == workspace_id,
                OmnichannelEndpoint.id == binding.endpoint_id,
            )
        )
        if not endpoint:
            raise RuntimeError("Evento canônico sem endpoint íntegro.")
        message = await self.db.scalar(
            select(OmnichannelMessage).where(
                OmnichannelMessage.workspace_id == workspace_id,
                OmnichannelMessage.idempotency_key == f"channel-event:{event.id}",
            )
        )
        return CanonicalResolution(identity, endpoint, binding, thread), message

    async def list_channel_events(
        self,
        workspace_id: UUID,
        *,
        channel: str | None = None,
        event_type: str | None = None,
        visibility: str | None = None,
        actionability: str | None = None,
        limit: int = 200,
    ) -> tuple[list[dict[str, Any]], bool]:
        statement = (
            select(
                OmnichannelChannelEvent,
                OmnichannelIdentity,
                OmnichannelThread,
                OmnichannelChannelConnection,
            )
            .join(
                OmnichannelThread,
                and_(
                    OmnichannelThread.workspace_id == OmnichannelChannelEvent.workspace_id,
                    OmnichannelThread.id == OmnichannelChannelEvent.thread_id,
                ),
            )
            .outerjoin(
                OmnichannelIdentity,
                and_(
                    OmnichannelIdentity.workspace_id == OmnichannelChannelEvent.workspace_id,
                    OmnichannelIdentity.id == OmnichannelChannelEvent.identity_id,
                ),
            )
            .outerjoin(
                OmnichannelChannelConnection,
                and_(
                    OmnichannelChannelConnection.workspace_id
                    == OmnichannelChannelEvent.workspace_id,
                    OmnichannelChannelConnection.provider == OmnichannelChannelEvent.provider,
                    OmnichannelChannelConnection.connection_key
                    == OmnichannelChannelEvent.connection_key,
                ),
            )
            .where(OmnichannelChannelEvent.workspace_id == workspace_id)
            .order_by(OmnichannelChannelEvent.occurred_at.desc())
        )
        if event_type:
            statement = statement.where(OmnichannelChannelEvent.event_type == event_type)
        if visibility:
            statement = statement.where(OmnichannelChannelEvent.visibility == visibility)
        if actionability:
            statement = statement.where(OmnichannelChannelEvent.actionability == actionability)

        fetch_limit = min(max(limit * (5 if channel else 1), limit + 1), 1000)
        rows = list((await self.db.execute(statement.limit(fetch_limit))).all())
        if channel:
            rows = [
                row
                for row in rows
                if str((row[0].event_metadata or {}).get("channel") or "") == channel
            ]
        has_more = len(rows) > limit or (len(rows) == fetch_limit and fetch_limit == 1000)
        selected_rows = rows[:limit]

        event_ids = [event.id for event, _, _, _ in selected_rows]
        message_by_event: dict[UUID, OmnichannelMessage] = {}
        if event_ids:
            idempotency_keys = [f"channel-event:{event_id}" for event_id in event_ids]
            messages = list(
                (
                    await self.db.execute(
                        select(OmnichannelMessage).where(
                            OmnichannelMessage.workspace_id == workspace_id,
                            OmnichannelMessage.idempotency_key.in_(idempotency_keys),
                        )
                    )
                ).scalars()
            )
            message_by_event = {
                UUID(message.idempotency_key.removeprefix("channel-event:")): message
                for message in messages
                if message.idempotency_key.startswith("channel-event:")
            }

        return [
            {
                "event": event,
                "identity": identity,
                "thread": thread,
                "connection": connection,
                "message": message_by_event.get(event.id),
                "channel": str((event.event_metadata or {}).get("channel") or "internal"),
            }
            for event, identity, thread, connection in selected_rows
        ], has_more

    async def _message_uses_connection(
        self,
        workspace_id: UUID,
        message: OmnichannelMessage,
        connection: OmnichannelChannelConnection,
    ) -> bool:
        if message.connection_id is not None:
            return message.connection_id == connection.id
        if message.binding_id is not None:
            binding_connection_id = await self.db.scalar(
                select(OmnichannelThreadBinding.connection_id).where(
                    OmnichannelThreadBinding.workspace_id == workspace_id,
                    OmnichannelThreadBinding.id == message.binding_id,
                )
            )
            if binding_connection_id is not None:
                return binding_connection_id == connection.id
        if message.endpoint_id is not None:
            endpoint_connection_id = await self.db.scalar(
                select(OmnichannelEndpoint.connection_id).where(
                    OmnichannelEndpoint.workspace_id == workspace_id,
                    OmnichannelEndpoint.id == message.endpoint_id,
                )
            )
            if endpoint_connection_id is not None:
                return endpoint_connection_id == connection.id
        metadata = message.message_metadata or {}
        return (
            str(metadata.get("connectionKey") or "") == connection.connection_key
            and str(metadata.get("provider") or message.provider or "") == connection.provider
        )

    async def _resolve_delivery_message(
        self,
        workspace_id: UUID,
        payload: MessageDeliveryReceiptIngest | InternalMessageDeliveryReceiptIngest,
        connection: OmnichannelChannelConnection,
    ) -> OmnichannelMessage:
        if payload.message_id is not None:
            message = await self.db.scalar(
                select(OmnichannelMessage)
                .where(
                    OmnichannelMessage.workspace_id == workspace_id,
                    OmnichannelMessage.id == payload.message_id,
                )
                .with_for_update()
            )
            candidates = [message] if message is not None else []
        else:
            candidates = list(
                (
                    await self.db.execute(
                        select(OmnichannelMessage)
                        .where(
                            OmnichannelMessage.workspace_id == workspace_id,
                            OmnichannelMessage.external_message_id == payload.external_message_id,
                        )
                        .with_for_update()
                    )
                ).scalars()
            )
        candidates = [
            item
            for item in candidates
            if item is not None
            and await self._message_uses_connection(workspace_id, item, connection)
        ]
        if not candidates:
            raise LookupError("Mensagem de saída não encontrada na conexão informada.")
        if len(candidates) > 1:
            raise OmnichannelConflictError(
                "O identificador externo corresponde a mais de uma mensagem; informe messageId."
            )
        message = candidates[0]
        if message.direction != MessageDirection.outbound:
            raise OmnichannelConflictError(
                "Recibos de entrega só podem alterar mensagens de saída."
            )
        if message.provider and message.provider != payload.provider:
            raise OmnichannelConflictError("O provedor do recibo diverge da mensagem.")
        if (
            message.external_message_id
            and message.external_message_id != payload.external_message_id
        ):
            raise OmnichannelConflictError("O ID externo do recibo diverge da mensagem.")
        return message

    async def ingest_delivery_receipt(
        self,
        workspace_id: UUID,
        payload: MessageDeliveryReceiptIngest | InternalMessageDeliveryReceiptIngest,
    ) -> tuple[
        OmnichannelMessage,
        OmnichannelMessageDeliveryEvent,
        OmnichannelMessageCostLedgerEntry | None,
        bool,
    ]:
        """Record provider evidence first, then update disposable projections."""

        cost_amount = getattr(payload, "cost_amount", None)
        cost_currency = getattr(payload, "cost_currency", None)
        cost_quantity = getattr(payload, "cost_quantity", Decimal("1"))
        cost_unit = getattr(payload, "cost_unit", "message")
        pricing_category = getattr(payload, "pricing_category", None)
        provider_invoice_reference = getattr(payload, "provider_invoice_reference", None)
        payload_dump = payload.model_dump(mode="json", by_alias=True)
        payload_hash = _canonical_hash(payload_dump)
        connection = await self.db.scalar(
            select(OmnichannelChannelConnection).where(
                OmnichannelChannelConnection.workspace_id == workspace_id,
                OmnichannelChannelConnection.provider == payload.provider,
                OmnichannelChannelConnection.connection_key == payload.connection_key,
            )
        )
        if connection is None:
            raise LookupError("Conexão do recibo não encontrada no workspace.")

        existing = await self.db.scalar(
            select(OmnichannelMessageDeliveryEvent).where(
                OmnichannelMessageDeliveryEvent.workspace_id == workspace_id,
                OmnichannelMessageDeliveryEvent.provider == payload.provider,
                OmnichannelMessageDeliveryEvent.connection_key == payload.connection_key,
                OmnichannelMessageDeliveryEvent.external_event_id == payload.external_event_id,
            )
        )
        if existing is not None:
            if existing.payload_sha256 != payload_hash:
                raise OmnichannelConflictError(
                    "O recibo externo já foi recebido com conteúdo diferente."
                )
            message = await self.db.scalar(
                select(OmnichannelMessage).where(
                    OmnichannelMessage.workspace_id == workspace_id,
                    OmnichannelMessage.id == existing.message_id,
                )
            )
            if message is None:
                raise RuntimeError("Recibo sem mensagem canônica vinculada.")
            cost_entry = await self.db.scalar(
                select(OmnichannelMessageCostLedgerEntry).where(
                    OmnichannelMessageCostLedgerEntry.workspace_id == workspace_id,
                    OmnichannelMessageCostLedgerEntry.delivery_event_id == existing.id,
                )
            )
            return message, existing, cost_entry, True

        message = await self._resolve_delivery_message(workspace_id, payload, connection)
        thread = await self.db.scalar(
            select(OmnichannelThread)
            .where(
                OmnichannelThread.workspace_id == workspace_id,
                OmnichannelThread.id == message.thread_id,
            )
            .with_for_update()
        )
        if thread is None:
            raise RuntimeError("Mensagem sem conversa canônica vinculada.")

        current = MessageDeliveryStatus(message.delivery_status)
        projected, applied, ignored_reason = _delivery_projection(current, payload.status)
        event = OmnichannelMessageDeliveryEvent(
            workspace_id=workspace_id,
            message_id=message.id,
            thread_id=message.thread_id,
            connection_id=connection.id,
            provider=payload.provider,
            connection_key=payload.connection_key,
            external_event_id=payload.external_event_id,
            external_message_id=payload.external_message_id,
            provider_status=payload.provider_status or payload.status.value,
            projected_status_before=current.value,
            projected_status_after=projected.value,
            applied=applied,
            ignored_reason=ignored_reason,
            payload_sha256=payload_hash,
            occurred_at=_aware(payload.occurred_at),
            error_code=payload.error_code,
            error_message=payload.error_message,
            event_payload=_scrub(payload.payload),
            event_metadata=_scrub(payload.metadata),
        )
        try:
            async with self.db.begin_nested():
                self.db.add(event)
                await self.db.flush()
        except IntegrityError:
            existing = await self.db.scalar(
                select(OmnichannelMessageDeliveryEvent).where(
                    OmnichannelMessageDeliveryEvent.workspace_id == workspace_id,
                    OmnichannelMessageDeliveryEvent.provider == payload.provider,
                    OmnichannelMessageDeliveryEvent.connection_key == payload.connection_key,
                    OmnichannelMessageDeliveryEvent.external_event_id == payload.external_event_id,
                )
            )
            if existing is None:
                raise
            if existing.payload_sha256 != payload_hash:
                raise OmnichannelConflictError(
                    "O recibo externo já foi recebido com conteúdo diferente."
                )
            cost_entry = await self.db.scalar(
                select(OmnichannelMessageCostLedgerEntry).where(
                    OmnichannelMessageCostLedgerEntry.workspace_id == workspace_id,
                    OmnichannelMessageCostLedgerEntry.delivery_event_id == existing.id,
                )
            )
            return message, existing, cost_entry, True

        if applied:
            message.delivery_status = projected
            message.provider_status = event.provider_status
            message.external_message_id = payload.external_message_id
            message.connection_id = connection.id
            message.provider = connection.provider
            metadata = dict(message.message_metadata or {})
            metadata["lastDeliveryReceiptId"] = str(event.id)
            metadata["lastDeliveryReceiptAt"] = event.occurred_at.isoformat()
            metadata["lastProviderStatus"] = event.provider_status
            message.message_metadata = metadata
            if bool(metadata.get("broadcast")):
                thread_metadata = dict(thread.thread_metadata or {})
                broadcast = (
                    dict(thread_metadata.get("broadcast") or {})
                    if isinstance(thread_metadata.get("broadcast"), dict)
                    else {}
                )
                if str(broadcast.get("lastMessageId") or "") == str(message.id):
                    broadcast["lastDeliveryStatus"] = projected.value
                    if projected in _DELIVERY_TERMINAL:
                        broadcast["awaitingReply"] = False
                        broadcast["deliveryFailed"] = True
                    thread_metadata["broadcast"] = broadcast
                    thread.thread_metadata = thread_metadata

        cost_entry = None
        if cost_amount is not None and cost_currency is not None:
            cost_entry = OmnichannelMessageCostLedgerEntry(
                workspace_id=workspace_id,
                message_id=message.id,
                thread_id=message.thread_id,
                delivery_event_id=event.id,
                provider=payload.provider,
                connection_key=payload.connection_key,
                amount=cost_amount,
                currency=cost_currency,
                quantity=cost_quantity,
                unit=cost_unit,
                pricing_category=pricing_category,
                provider_invoice_reference=provider_invoice_reference,
                idempotency_key=f"delivery:{event.id}:provider_charge",
                occurred_at=event.occurred_at,
                entry_metadata={"source": "provider_delivery_receipt"},
            )
            self.db.add(cost_entry)
            metadata = dict(message.message_metadata or {})
            actual = (
                dict(metadata.get("actualCostsByCurrency"))
                if isinstance(metadata.get("actualCostsByCurrency"), dict)
                else {}
            )
            currency = cost_currency.upper()
            total = Decimal(str(actual.get(currency) or "0")) + cost_amount
            actual[currency] = f"{total:.6f}"
            metadata["actualCostsByCurrency"] = actual
            message.message_metadata = metadata
            message.cost_amount = total if len(actual) == 1 else None
            message.cost_currency = currency if len(actual) == 1 else None
            thread.cost_state = _apply_actual_cost_projection(
                thread.cost_state,
                amount=cost_amount,
                currency=currency,
                provider=payload.provider,
                connection_key=payload.connection_key,
            )

        await self.db.flush()
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=None,
            actor_type="system",
            actor_label="Recibo do provedor",
            event_type="omnichannel_delivery_receipt_recorded",
            entity_type="omnichannel_message",
            entity_id=message.id,
            summary="Recibo de entrega registrado sem executar ação externa.",
            correlation_id=payload.external_event_id,
            before_data={"deliveryStatus": current.value},
            after_data={
                "deliveryStatus": projected.value,
                "applied": applied,
                "costLedgerEntryId": str(cost_entry.id) if cost_entry else None,
            },
        )
        queue_realtime_event(
            self.db,
            workspace_id=workspace_id,
            event_type="OMNICHANNEL_MESSAGE_DELIVERY_UPDATED",
            data={
                "thread_id": str(message.thread_id),
                "message_id": str(message.id),
                "delivery_event_id": str(event.id),
                "delivery_status": projected.value,
                "applied": applied,
            },
        )
        return message, event, cost_entry, False

    async def list_delivery_events(
        self,
        workspace_id: UUID,
        message_id: UUID,
        *,
        limit: int = 200,
    ) -> list[OmnichannelMessageDeliveryEvent]:
        message_exists = await self.db.scalar(
            select(OmnichannelMessage.id).where(
                OmnichannelMessage.workspace_id == workspace_id,
                OmnichannelMessage.id == message_id,
            )
        )
        if message_exists is None:
            raise LookupError("Mensagem não encontrada no workspace.")
        return list(
            (
                await self.db.execute(
                    select(OmnichannelMessageDeliveryEvent)
                    .where(
                        OmnichannelMessageDeliveryEvent.workspace_id == workspace_id,
                        OmnichannelMessageDeliveryEvent.message_id == message_id,
                    )
                    .order_by(
                        OmnichannelMessageDeliveryEvent.occurred_at.asc(),
                        OmnichannelMessageDeliveryEvent.received_at.asc(),
                    )
                    .limit(limit)
                )
            ).scalars()
        )

    async def ingest_event(
        self,
        workspace_id: UUID,
        payload: CanonicalChannelEventIngest,
    ) -> tuple[CanonicalResolution, OmnichannelChannelEvent, OmnichannelMessage | None, bool]:
        payload_dump = payload.model_dump(mode="json", by_alias=True)
        payload_hash = _canonical_hash(payload_dump)
        existing = await self.db.scalar(
            select(OmnichannelChannelEvent).where(
                OmnichannelChannelEvent.workspace_id == workspace_id,
                OmnichannelChannelEvent.provider == payload.provider,
                OmnichannelChannelEvent.connection_key == payload.connection_key,
                OmnichannelChannelEvent.external_event_id == payload.external_event_id,
            )
        )
        if existing:
            if existing.payload_sha256 != payload_hash:
                raise OmnichannelConflictError(
                    "O identificador externo já foi recebido com conteúdo diferente."
                )
            resolution, message = await self._load_event_result(workspace_id, existing)
            return resolution, existing, message, True

        resolution = await self._resolve_canonical(workspace_id, payload)
        parent_event = None
        if payload.parent_external_event_id:
            parent_event = await self.db.scalar(
                select(OmnichannelChannelEvent).where(
                    OmnichannelChannelEvent.workspace_id == workspace_id,
                    OmnichannelChannelEvent.provider == payload.provider,
                    OmnichannelChannelEvent.connection_key == payload.connection_key,
                    OmnichannelChannelEvent.external_event_id == payload.parent_external_event_id,
                )
            )
        event_metadata = _scrub(payload.metadata)
        event_metadata["channel"] = payload.channel.value
        if payload.parent_external_event_id and parent_event is None:
            event_metadata["unresolvedParentExternalEventId"] = payload.parent_external_event_id
        event = OmnichannelChannelEvent(
            workspace_id=workspace_id,
            identity_id=resolution.identity.id,
            thread_id=resolution.thread.id,
            binding_id=resolution.binding.id,
            parent_event_id=parent_event.id if parent_event else None,
            connection_id=resolution.binding.connection_id,
            provider=payload.provider,
            connection_key=payload.connection_key,
            event_type=payload.event_type,
            external_event_id=payload.external_event_id,
            payload_sha256=payload_hash,
            actor_external_id=payload.external_user_id,
            visibility=payload.visibility,
            public_url=payload.public_url,
            actionability=payload.actionability,
            deadline_at=payload.deadline_at,
            occurred_at=_aware(payload.occurred_at),
            correlation=_scrub(payload.correlation),
            event_payload=_scrub(payload.payload),
            event_metadata=event_metadata,
        )
        self.db.add(event)
        await self.db.flush()

        message = None
        if payload.direction is not None:
            author_type = payload.author_type or (
                MessageAuthorType.client
                if payload.direction == MessageDirection.inbound
                else MessageAuthorType.system
            )
            raw_provider_status = str(
                payload.metadata.get("providerStatus")
                or (
                    MessageDeliveryStatus.received.value
                    if payload.direction == MessageDirection.inbound
                    else MessageDeliveryStatus.queued.value
                )
            ).lower()
            delivery = _provider_delivery_status(
                raw_provider_status,
                direction=payload.direction,
            )
            external_message_id = str(
                payload.metadata.get("externalMessageId") or payload.external_event_id
            )
            message_payload = OmnichannelMessageCreate(
                endpoint_id=resolution.endpoint.id,
                binding_id=resolution.binding.id,
                channel=payload.channel,
                direction=payload.direction,
                author_type=author_type,
                author_label=payload.author_label or payload.display_name,
                body=(payload.body or "").strip(),
                audience=payload.visibility,
                content_type=payload.content_type,
                language=payload.language,
                provider_status=raw_provider_status,
                transport=payload.provider,
                external_message_id=external_message_id,
                idempotency_key=f"channel-event:{event.id}",
                delivery_status=delivery,
                occurred_at=event.occurred_at,
                message_metadata={
                    "channelEventId": str(event.id),
                    "provider": payload.provider,
                    "connectionKey": payload.connection_key,
                    "publicUrl": payload.public_url,
                    "actionability": payload.actionability,
                    "externalActionsExecuted": 0,
                    **{
                        key: _scrub(payload.metadata.get(key))
                        for key in (
                            "source",
                            "originSurface",
                            "transportMode",
                            "billableByMeta",
                            "broadcast",
                            "campaignId",
                            "campaignTitle",
                            "campaignRecipientId",
                            "metaTemplateId",
                            "metaTemplateName",
                            "followUpFlowId",
                            "flowStartMode",
                        )
                        if key in payload.metadata
                    },
                },
            )
            message, _ = await self.operations.create_message(
                workspace_id,
                resolution.thread.id,
                None,
                message_payload,
                trusted_external_receipt=True,
            )
            if (
                payload.direction == MessageDirection.outbound
                and delivery != MessageDeliveryStatus.local_draft
            ):
                receipt_status = delivery
                message, _, _, _ = await self.ingest_delivery_receipt(
                    workspace_id,
                    MessageDeliveryReceiptIngest(
                        message_id=message.id,
                        provider=payload.provider,
                        connection_key=payload.connection_key,
                        external_event_id=payload.external_event_id,
                        external_message_id=external_message_id,
                        status=receipt_status,
                        provider_status=raw_provider_status,
                        occurred_at=event.occurred_at,
                        payload=payload.payload,
                        metadata={
                            **payload.metadata,
                            "channelEventId": str(event.id),
                        },
                    ),
                )
            if payload.direction == MessageDirection.inbound:
                if resolution.thread.status == ThreadStatus.closed:
                    resolution.thread.status = ThreadStatus.open
                    resolution.thread.closed_at = None
                    resolution.thread.closed_reason = None
                    resolution.thread.version += 1
                resolution.thread.preferred_channel = payload.channel.value
                if payload.actionability == "urgent":
                    resolution.thread.priority = 100
                elif payload.actionability == "reply_required":
                    resolution.thread.priority = max(resolution.thread.priority, 70)
                if (
                    payload.channel
                    in (
                        OmnichannelChannel.instagram,
                        OmnichannelChannel.messenger,
                        OmnichannelChannel.facebook,
                    )
                    and resolution.thread.matter_id is None
                    and payload.event_type
                    in {"direct_message", "story_reply", "story_mention"}
                ):
                    from app.services.sdr_intake_service import (
                        handle_inbound_social_lead,
                    )

                    await handle_inbound_social_lead(
                        self.db,
                        workspace_id=workspace_id,
                        thread_id=resolution.thread.id,
                        identity_id=resolution.identity.id,
                        channel=payload.channel.value,
                        event_type=payload.event_type,
                        correlation=payload.correlation,
                        display_name=payload.display_name,
                    )
                elif (
                    payload.channel
                    in (
                        OmnichannelChannel.instagram,
                        OmnichannelChannel.messenger,
                        OmnichannelChannel.facebook,
                    )
                    and resolution.thread.matter_id is not None
                    and payload.event_type in {"direct_message", "message.received"}
                ):
                    # Thread já tem caso: se a qualificação SDR não terminou, roda o
                    # mesmo motor do WhatsApp (`run_sdr_on_social_message`) sobre o
                    # trilho social. Resposta sai como local_draft/nota interna
                    # gated (ver `agent_tasks.py:_draft_social_sdr_reply`), nunca
                    # transporte real — mesmo padrão de tolerância de
                    # `handle_inbound_social_lead` acima.
                    matter_for_sdr = await self.db.scalar(
                        select(Matter).where(
                            Matter.id == resolution.thread.matter_id,
                            Matter.workspace_id == workspace_id,
                        )
                    )
                    qualification = (matter_for_sdr.sdr_qualification or {}) if matter_for_sdr else {}
                    if matter_for_sdr is not None and not qualification.get("completed"):
                        from app.workers.event_bus import dispatch_social_message

                        try:
                            await dispatch_social_message(
                                matter_for_sdr.id,
                                resolution.thread.id,
                                (payload.body or "").strip(),
                                payload.channel.value,
                                workspace_id=workspace_id,
                                dedupe_key=f"social-sdr:{event.id}",
                            )
                        except Exception as exc:  # noqa: BLE001 — best-effort, mesmo padrão do intake
                            logger.error(
                                "omnichannel_v6.social_sdr_dispatch_failed",
                                matter_id=str(matter_for_sdr.id),
                                thread_id=str(resolution.thread.id),
                                error_type=safe_error_type(exc),
                            )
            thread_metadata = dict(resolution.thread.thread_metadata or {})
            broadcast = (
                dict(thread_metadata.get("broadcast") or {})
                if isinstance(thread_metadata.get("broadcast"), dict)
                else {}
            )
            if (
                payload.direction == MessageDirection.outbound
                and bool(payload.metadata.get("broadcast"))
                and payload.metadata.get("campaignId")
                and message is not None
            ):
                campaign_id = str(payload.metadata["campaignId"])
                campaign_ids = [str(value) for value in broadcast.get("campaignIds", []) if value]
                campaign_ids = list(dict.fromkeys([*campaign_ids, campaign_id]))[-50:]
                broadcast.update(
                    {
                        "totalSent": int(broadcast.get("totalSent") or 0) + 1,
                        "campaignIds": campaign_ids,
                        "lastCampaignId": campaign_id,
                        "lastCampaignTitle": payload.metadata.get("campaignTitle"),
                        "lastCampaignRecipientId": payload.metadata.get("campaignRecipientId"),
                        "lastTemplateName": payload.metadata.get("metaTemplateName"),
                        "lastSentAt": event.occurred_at.isoformat(),
                        "lastMessageId": str(message.id),
                        "lastDeliveryStatus": message.delivery_status.value,
                        "awaitingReply": True,
                        "deliveryFailed": False,
                    }
                )
                thread_metadata["broadcast"] = broadcast
                resolution.thread.thread_metadata = thread_metadata
            elif payload.direction == MessageDirection.inbound and broadcast:
                last_sent_at = None
                raw_last_sent_at = broadcast.get("lastSentAt")
                if raw_last_sent_at:
                    try:
                        last_sent_at = _aware(datetime.fromisoformat(str(raw_last_sent_at)))
                    except ValueError:
                        last_sent_at = None
                if last_sent_at is None or event.occurred_at >= last_sent_at:
                    broadcast.update(
                        {
                            "awaitingReply": False,
                            "lastReplyAt": event.occurred_at.isoformat(),
                            "lastReplyMessageId": str(message.id) if message is not None else None,
                        }
                    )
                    thread_metadata["broadcast"] = broadcast
                    resolution.thread.thread_metadata = thread_metadata
        mutated_at = _now()
        resolution.identity.updated_at = mutated_at
        resolution.endpoint.last_seen_at = event.occurred_at
        resolution.endpoint.updated_at = mutated_at
        resolution.binding.last_cursor = payload.cursor
        resolution.binding.last_synced_at = mutated_at
        resolution.binding.updated_at = mutated_at
        resolution.thread.updated_at = mutated_at
        await self.db.flush()
        queue_realtime_event(
            self.db,
            workspace_id=workspace_id,
            event_type="OMNICHANNEL_EVENT_INGESTED",
            data={
                "thread_id": str(resolution.thread.id),
                "event_id": str(event.id),
                "event_type": event.event_type,
                "channel": payload.channel.value,
                "message_id": str(message.id) if message else None,
            },
        )
        return resolution, event, message, False

    async def _ensure_lead_case(
        self,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        identity_id: UUID,
        thread_id: UUID,
        payload: LeadCaptureIngest,
    ) -> tuple[OmnichannelIdentity, Client, Matter, OmnichannelThread]:
        identity = await self.db.scalar(
            select(OmnichannelIdentity)
            .where(
                OmnichannelIdentity.workspace_id == workspace_id,
                OmnichannelIdentity.id == identity_id,
            )
            .with_for_update()
        )
        assert identity is not None
        client = None
        if identity.client_id:
            client = await self.db.scalar(
                select(Client).where(
                    Client.workspace_id == workspace_id,
                    Client.id == identity.client_id,
                    Client.deleted_at.is_(None),
                )
            )
        if not client:
            phone = _normalize_phone(payload.phone)
            email = _normalize_email(payload.email)
            filters = []
            if phone:
                filters.append(Client.phone.in_(phone_number_lookup_variants(phone)))
            if email:
                filters.append(func.lower(Client.email) == email)
            if filters:
                client = await self.db.scalar(
                    select(Client).where(
                        Client.workspace_id == workspace_id,
                        Client.deleted_at.is_(None),
                        or_(*filters),
                    )
                )
            if not client:
                client = Client(
                    workspace_id=workspace_id,
                    name=payload.name.strip(),
                    phone=phone,
                    email=email,
                    notes=f"Lead capturado por {payload.provider}.",
                )
                self.db.add(client)
                await self.db.flush()
            identity.client_id = client.id

        matter = None
        if payload.matter_id:
            matter = await self.db.scalar(
                select(Matter).where(
                    Matter.workspace_id == workspace_id,
                    Matter.id == payload.matter_id,
                    Matter.deleted_at.is_(None),
                )
            )
            if matter is None:
                raise LookupError("Caso informado na captação não foi encontrado no workspace.")
            if matter.client_id != client.id:
                raise OmnichannelConflictError(
                    "O caso informado na captação pertence a outro cliente."
                )
        if not matter:
            try:
                health_subtheme = (
                    HealthSubtheme(payload.health_subtheme)
                    if payload.health_subtheme
                    else HealthSubtheme.outro
                )
            except ValueError as error:
                raise ValueError("Subtema de saúde inválido no formulário.") from error
            matter = Matter(
                workspace_id=workspace_id,
                client_id=client.id,
                practice_area=PracticeArea.saude_suplementar,
                demand_type=payload.demand_type,
                lead_source=payload.source or payload.provider,
                executive_summary=(
                    f"Lead capturado por {payload.provider}; campanha "
                    f"{payload.campaign or 'não informada'}."
                ),
                health_subtheme=health_subtheme,
                health_object_name=payload.health_object_name,
                health_insurer=payload.health_insurer,
                health_denial_reason=payload.health_denial_reason,
                medical_urgency_hours=payload.medical_urgency_hours,
                column_id=KanbanColumnId.novo_lead,
                micro_status=MicroStatus.lead_novo,
                next_action="Triagem automática e confirmação dos documentos disponíveis.",
            )
            self.db.add(matter)
            await self.db.flush()

        thread = await self._thread(workspace_id, thread_id, lock=True)
        if thread.matter_id not in {None, matter.id}:
            raise OmnichannelConflictError(
                "A conversa da captação já pertence a outro caso jurídico."
            )
        thread.client_id = client.id
        thread.matter_id = matter.id
        metadata = dict(thread.thread_metadata or {})
        acquisition = {
            "provider": payload.provider,
            "source": payload.source,
            "campaign": payload.campaign,
            "adSet": payload.ad_set,
            "ad": payload.ad,
            "clickIds": payload.click_ids,
            "formId": payload.external_form_id,
        }
        metadata.setdefault("acquisition", acquisition)
        metadata["acquisitionTouches"] = list(
            {
                str(item.get("externalLeadId")): item
                for item in [
                    *(metadata.get("acquisitionTouches") or []),
                    {**acquisition, "externalLeadId": payload.external_lead_id},
                ]
            }.values()
        )
        thread.thread_metadata = metadata
        identity.identity_state = "verified"
        identity_metadata = dict(identity.identity_metadata or {})
        identity_metadata["relatedMatterIds"] = list(
            dict.fromkeys([*(identity_metadata.get("relatedMatterIds") or []), str(matter.id)])
        )
        identity.identity_metadata = identity_metadata
        other_matter_ids = list(
            (
                await self.db.execute(
                    select(OmnichannelThread.matter_id).where(
                        OmnichannelThread.workspace_id == workspace_id,
                        OmnichannelThread.identity_id == identity.id,
                        OmnichannelThread.id != thread.id,
                        OmnichannelThread.matter_id.is_not(None),
                    )
                )
            ).scalars()
        )
        if other_matter_ids and payload.matter_id is None:
            review_key = f"possible-matter-duplicate:{payload.provider}:{payload.external_lead_id}"
            review = await self.db.scalar(
                select(OmnichannelIdentityReviewCase).where(
                    OmnichannelIdentityReviewCase.workspace_id == workspace_id,
                    OmnichannelIdentityReviewCase.idempotency_key == review_key,
                )
            )
            if review is None:
                self.db.add(
                    OmnichannelIdentityReviewCase(
                        workspace_id=workspace_id,
                        identity_id=identity.id,
                        thread_id=thread.id,
                        matter_id=matter.id,
                        created_by_user_id=actor_user_id,
                        review_type="possible_duplicate_matter",
                        reason_code="same_verified_person_new_lead_capture",
                        idempotency_key=review_key,
                        evidence={
                            "provider": payload.provider,
                            "externalLeadId": payload.external_lead_id,
                            "newMatterId": str(matter.id),
                            "existingMatterIds": [str(item) for item in other_matter_ids],
                        },
                    )
                )
        mutated_at = _now()
        identity.updated_at = mutated_at
        thread.updated_at = mutated_at
        await self.db.flush()
        return identity, client, matter, thread

    async def ingest_lead_capture(
        self,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        payload: LeadCaptureIngest,
    ) -> dict[str, Any]:
        payload_hash = _canonical_hash(payload.model_dump(mode="json", by_alias=True))
        receipt = await self.db.scalar(
            select(LeadCaptureReceipt).where(
                LeadCaptureReceipt.workspace_id == workspace_id,
                LeadCaptureReceipt.provider == payload.provider,
                LeadCaptureReceipt.connection_key == payload.connection_key,
                LeadCaptureReceipt.external_lead_id == payload.external_lead_id,
            )
        )
        if receipt:
            if receipt.payload_sha256 != payload_hash:
                raise OmnichannelConflictError(
                    "O lead externo já foi recebido com conteúdo diferente."
                )
            if receipt.state != "completed":
                raise OmnichannelConflictError("A captação anterior ainda está em processamento.")
            if receipt.identity_id is None or receipt.thread_id is None:
                raise RuntimeError("Captação concluída sem identidade ou conversa íntegra.")
            result = receipt.result or {}
            try:
                binding_id = UUID(str(result["bindingId"]))
                endpoint_id = UUID(str(result["endpointId"]))
                client_id = UUID(str(result["clientId"]))
                matter_id = UUID(str(result["matterId"]))
            except (KeyError, TypeError, ValueError) as error:
                raise RuntimeError(
                    "Captação concluída com resultado persistido inválido."
                ) from error
            identity = await self._identity(workspace_id, receipt.identity_id)
            thread = await self._thread(workspace_id, receipt.thread_id)
            binding = await self.db.scalar(
                select(OmnichannelThreadBinding).where(
                    OmnichannelThreadBinding.workspace_id == workspace_id,
                    OmnichannelThreadBinding.id == binding_id,
                )
            )
            endpoint = await self.db.scalar(
                select(OmnichannelEndpoint).where(
                    OmnichannelEndpoint.workspace_id == workspace_id,
                    OmnichannelEndpoint.id == endpoint_id,
                )
            )
            if binding is None or endpoint is None:
                raise RuntimeError("Captação concluída sem endpoint ou vínculo íntegro.")
            return {
                "receipt": receipt,
                "identity": identity,
                "endpoint": endpoint,
                "binding": binding,
                "thread": thread,
                "message": None,
                "client_id": client_id,
                "matter_id": matter_id,
                "deduplicated": True,
            }

        receipt = LeadCaptureReceipt(
            workspace_id=workspace_id,
            provider=payload.provider,
            connection_key=payload.connection_key,
            external_lead_id=payload.external_lead_id,
            external_form_id=payload.external_form_id,
            payload_sha256=payload_hash,
            state="processing",
            mapping_version="v7",
            result={},
        )
        self.db.add(receipt)
        await self.db.flush()
        event_payload = CanonicalChannelEventIngest(
            provider=payload.provider,
            connection_key=payload.connection_key,
            channel=payload.channel,
            event_type="lead.captured",
            external_event_id=f"lead:{payload.external_lead_id}",
            external_thread_id=f"lead:{payload.external_lead_id}",
            external_user_id=payload.phone or payload.email or payload.external_lead_id,
            display_name=payload.name,
            address=payload.phone or payload.email,
            canonical_phone=payload.phone,
            canonical_email=payload.email,
            matter_id=payload.matter_id,
            occurred_at=payload.received_at,
            actionability="qualification",
            correlation={
                "campaign": payload.campaign,
                "adSet": payload.ad_set,
                "ad": payload.ad,
                "providerReferences": payload.provider_references,
            },
            payload={"fields": payload.fields, "clickIds": payload.click_ids},
            metadata={"formId": payload.external_form_id},
        )
        resolution, _event, message, _ = await self.ingest_event(workspace_id, event_payload)
        receipt.connection_id = resolution.binding.connection_id
        identity, client, matter, thread = await self._ensure_lead_case(
            workspace_id,
            actor_user_id,
            resolution.identity.id,
            resolution.thread.id,
            payload,
        )
        if message is not None and message.matter_id is None:
            message.matter_id = matter.id
        await KnowledgeService(self.db).index_thread_conversation(
            workspace_id=workspace_id,
            thread_id=thread.id,
            matter_id=matter.id,
        )
        acquisition_payload = AcquisitionEventCreate(
            matter_id=matter.id,
            client_id=client.id,
            event_name="lead_captured",
            channel=payload.channel.value,
            source=payload.source or payload.provider,
            medium="lead_form",
            campaign=payload.campaign,
            event_id=payload.external_lead_id,
            dedupe_key=f"lead-capture:{payload.provider}:{payload.external_lead_id}",
            happened_at=payload.received_at,
            click_ids=payload.click_ids,
            event_metadata={
                "provider": payload.provider,
                "formId": payload.external_form_id,
                "adSet": payload.ad_set,
                "ad": payload.ad,
                "providerReferences": payload.provider_references,
            },
        )
        await self.operations.record_acquisition_event(
            workspace_id, actor_user_id, acquisition_payload
        )
        scheduled_message_id = None
        proactive_route: dict[str, Any] | None = None
        if payload.proactive_message_template:
            outbound_channel = "whatsapp" if payload.phone else "email"
            connection = await self.db.scalar(
                select(OmnichannelChannelConnection)
                .where(
                    OmnichannelChannelConnection.workspace_id == workspace_id,
                    OmnichannelChannelConnection.channel == outbound_channel,
                    OmnichannelChannelConnection.status.not_in({"paused", "disconnected_local"}),
                )
                .order_by(
                    OmnichannelChannelConnection.is_default.desc(),
                    OmnichannelChannelConnection.created_at.asc(),
                )
            )
            outbound_endpoint_id = None
            connection_key = f"unconfigured:{outbound_channel}"
            schedule_state = "awaiting_connection"
            if connection:
                outbound = await self._resolve_canonical(
                    workspace_id,
                    CanonicalChannelEventIngest(
                        provider=connection.provider,
                        connection_key=connection.connection_key,
                        channel=outbound_channel,
                        event_type="proactive.route.prepared",
                        external_event_id=(
                            f"lead-route:{payload.provider}:{payload.external_lead_id}"
                        ),
                        external_thread_id=f"canonical:{identity.id}",
                        external_user_id=payload.phone or payload.email or str(identity.id),
                        external_account_id=connection.external_account_id,
                        display_name=identity.display_name,
                        address=payload.phone or payload.email,
                        canonical_phone=payload.phone,
                        canonical_email=payload.email,
                        client_id=client.id,
                        matter_id=matter.id,
                        actionability="commercial",
                        metadata={"preparedBy": "lead_capture"},
                    ),
                )
                outbound_endpoint_id = outbound.endpoint.id
                connection_key = connection.connection_key
                schedule_state = "scheduled"
            proactive_route = {
                "channel": outbound_channel,
                "connectionId": str(connection.id) if connection else None,
                "connectionKey": connection_key,
                "connectionName": connection.display_name if connection else None,
                "transportMode": connection.transport_mode if connection else None,
                "state": schedule_state,
                "externalActionsExecuted": 0,
            }
            scheduled = OmnichannelScheduledMessage(
                workspace_id=workspace_id,
                thread_id=thread.id,
                endpoint_id=outbound_endpoint_id,
                connection_id=connection.id if connection else None,
                author_type="agent",
                author_label="Agente SDR",
                connection_key=connection_key,
                channel=outbound_channel,
                template_id=payload.proactive_message_template,
                attachments=[],
                message_payload={
                    "kind": "template",
                    "name": payload.proactive_message_template,
                    "language": "pt_BR",
                    "components": [],
                },
                scheduled_at=_now(),
                timezone="America/Sao_Paulo",
                state=schedule_state,
                idempotency_key=(f"lead-proactive:{payload.provider}:{payload.external_lead_id}"),
                provider_receipt={
                    "provider": connection.provider if connection else "local_unconfigured",
                    "route": proactive_route,
                    "localOnly": True,
                    "externalActionsExecuted": 0,
                },
            )
            self.db.add(scheduled)
            await self.db.flush()
            await self._append_schedule_event(
                scheduled,
                event_type="created",
                previous_state="none",
                next_state=schedule_state,
                idempotency_key=f"schedule-created:{scheduled.idempotency_key}",
                payload={
                    "threadId": str(thread.id),
                    "channel": outbound_channel,
                    "connectionKey": connection_key,
                    "templateId": payload.proactive_message_template,
                    "scheduledAt": scheduled.scheduled_at,
                },
                metadata={"source": "lead_capture", "externalActionsExecuted": 0},
            )
            scheduled_message_id = scheduled.id

        receipt.identity_id = identity.id
        receipt.thread_id = thread.id
        receipt.matter_id = matter.id
        receipt.state = "completed"
        completed_at = _now()
        receipt.completed_at = completed_at
        receipt.updated_at = completed_at
        receipt.result = {
            "identityId": str(identity.id),
            "endpointId": str(resolution.endpoint.id),
            "bindingId": str(resolution.binding.id),
            "threadId": str(thread.id),
            "clientId": str(client.id),
            "matterId": str(matter.id),
            "scheduledMessageId": (str(scheduled_message_id) if scheduled_message_id else None),
            "proactiveRoute": proactive_route,
            "acquisition": {
                "provider": payload.provider,
                "source": payload.source,
                "campaign": payload.campaign,
                "adSet": payload.ad_set,
                "ad": payload.ad,
                "formId": payload.external_form_id,
                "providerReferences": payload.provider_references,
                "channel": payload.channel.value,
                "receivedAt": _aware(payload.received_at).isoformat(),
                "clickIdKeys": sorted(payload.click_ids),
            },
        }
        await self.db.flush()
        await self.db.refresh(thread)
        queue_realtime_event(
            self.db,
            workspace_id=workspace_id,
            event_type="OMNICHANNEL_LEAD_CAPTURED",
            data={
                "thread_id": str(thread.id),
                "matter_id": str(matter.id),
                "client_id": str(client.id),
                "provider": payload.provider,
            },
        )
        return {
            "receipt": receipt,
            "identity": identity,
            "endpoint": resolution.endpoint,
            "binding": resolution.binding,
            "thread": thread,
            "message": message,
            "client_id": client.id,
            "matter_id": matter.id,
            "deduplicated": False,
        }

    async def create_scheduled_message(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        payload: ScheduledMessageCreate,
        *,
        user_id: UUID | None = None,
        author_type: str | None = None,
        author_label: str | None = None,
    ) -> tuple[OmnichannelScheduledMessage, bool]:
        thread = await self._thread(workspace_id, thread_id)
        existing = await self.db.scalar(
            select(OmnichannelScheduledMessage).where(
                OmnichannelScheduledMessage.workspace_id == workspace_id,
                OmnichannelScheduledMessage.idempotency_key == payload.idempotency_key,
            )
        )
        scheduled_at = _aware(payload.scheduled_at)
        if existing:
            if (
                existing.thread_id != thread.id
                or existing.channel != payload.channel.value
                or existing.body != payload.body
                or existing.template_id != payload.template_id
                or existing.attachments != payload.attachments
                or existing.message_payload != payload.message_payload
                or existing.connection_key != payload.connection_key
                or existing.endpoint_id != payload.endpoint_id
                or _aware(existing.scheduled_at) != scheduled_at
                or existing.timezone != payload.timezone
            ):
                raise OmnichannelConflictError(
                    "Chave de idempotência já usada por outro agendamento."
                )
            return existing, True
        if scheduled_at < _now() - timedelta(seconds=30):
            raise ValueError("A mensagem não pode ser agendada no passado.")
        endpoint = None
        if payload.endpoint_id:
            endpoint = await self.db.scalar(
                select(OmnichannelEndpoint).where(
                    OmnichannelEndpoint.workspace_id == workspace_id,
                    OmnichannelEndpoint.id == payload.endpoint_id,
                )
            )
            if not endpoint or (thread.identity_id and endpoint.identity_id != thread.identity_id):
                raise LookupError("Endpoint não pertence à identidade da conversa.")
        connection_filters = [
            OmnichannelChannelConnection.workspace_id == workspace_id,
            OmnichannelChannelConnection.connection_key == payload.connection_key,
            OmnichannelChannelConnection.channel == payload.channel.value,
        ]
        if endpoint and endpoint.provider:
            connection_filters.append(OmnichannelChannelConnection.provider == endpoint.provider)
        connections = list(
            (
                await self.db.execute(
                    select(OmnichannelChannelConnection).where(*connection_filters).limit(2)
                )
            ).scalars()
        )
        if len(connections) > 1:
            raise OmnichannelConflictError(
                "A rota da mensagem é ambígua; selecione uma conexão exata."
            )
        connection = connections[0] if connections else None
        if endpoint and endpoint.connection_id:
            if connection is None or connection.id != endpoint.connection_id:
                raise OmnichannelConflictError(
                    "O endpoint e a conexão da mensagem agendada divergem."
                )
        resolved_author_type = author_type or ("human" if user_id else "system")
        if resolved_author_type not in {"agent", "human", "system"}:
            raise ValueError("Tipo de autor inválido para a mensagem agendada.")
        resolved_author_label = (author_label or "").strip()
        if user_id and not resolved_author_label:
            resolved_author_label = str(
                await self.db.scalar(
                    select(User.name).where(
                        User.id == user_id,
                        User.is_active.is_(True),
                        User.deleted_at.is_(None),
                    )
                )
                or "Usuário KLM"
            )
        if not resolved_author_label:
            resolved_author_label = (
                "Agente KLM" if resolved_author_type == "agent" else "Agendamento KLM"
            )
        initial_state = "scheduled" if connection else "awaiting_connection"
        scheduled = OmnichannelScheduledMessage(
            workspace_id=workspace_id,
            thread_id=thread.id,
            endpoint_id=payload.endpoint_id,
            connection_id=connection.id if connection else None,
            created_by_user_id=user_id,
            author_type=resolved_author_type,
            author_label=resolved_author_label,
            connection_key=payload.connection_key,
            channel=payload.channel.value,
            body=payload.body,
            template_id=payload.template_id,
            attachments=payload.attachments,
            message_payload=payload.message_payload,
            scheduled_at=scheduled_at,
            timezone=payload.timezone,
            state=initial_state,
            idempotency_key=payload.idempotency_key,
            provider_receipt={
                "provider": connection.provider if connection else "local_unconfigured",
                "localOnly": not external_delivery_allowed(),
                "externalActionsExecuted": 0,
            },
        )
        self.db.add(scheduled)
        await self.db.flush()
        event = await self._append_schedule_event(
            scheduled,
            event_type="created",
            previous_state="none",
            next_state=initial_state,
            idempotency_key=f"schedule-created:{payload.idempotency_key}",
            payload=payload.model_dump(mode="json", by_alias=True),
            metadata={"source": "api", "createdByUserId": str(user_id) if user_id else None},
        )
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type=resolved_author_type,
            actor_label=resolved_author_label,
            event_type="omnichannel_scheduled_message_created",
            entity_type="omnichannel_scheduled_message",
            entity_id=scheduled.id,
            summary="Mensagem agendada na conversa.",
            correlation_id=payload.idempotency_key,
            after_data={
                "state": scheduled.state,
                "scheduledAt": scheduled.scheduled_at.isoformat(),
                "scheduleEventId": str(event.id),
            },
        )
        queue_realtime_event(
            self.db,
            workspace_id=workspace_id,
            event_type="OMNICHANNEL_SCHEDULE_UPDATED",
            data={
                "thread_id": str(thread.id),
                "scheduled_message_id": str(scheduled.id),
                "state": scheduled.state,
                "event_id": str(event.id),
            },
        )
        return scheduled, False

    async def list_scheduled_messages(
        self, workspace_id: UUID, thread_id: UUID
    ) -> list[OmnichannelScheduledMessage]:
        await self._thread(workspace_id, thread_id)
        result = await self.db.execute(
            select(OmnichannelScheduledMessage)
            .where(
                OmnichannelScheduledMessage.workspace_id == workspace_id,
                OmnichannelScheduledMessage.thread_id == thread_id,
            )
            .order_by(OmnichannelScheduledMessage.scheduled_at.asc())
        )
        return list(result.scalars().all())

    async def update_scheduled_message(
        self,
        workspace_id: UUID,
        scheduled_message_id: UUID,
        payload: ScheduledMessageUpdate,
        *,
        user_id: UUID | None = None,
    ) -> OmnichannelScheduledMessage:
        message = await self.db.scalar(
            select(OmnichannelScheduledMessage)
            .where(
                OmnichannelScheduledMessage.workspace_id == workspace_id,
                OmnichannelScheduledMessage.id == scheduled_message_id,
            )
            .with_for_update()
        )
        if not message:
            raise LookupError("Mensagem agendada não encontrada no workspace.")
        if payload.idempotency_key:
            payload_hash = _canonical_hash(payload.model_dump(mode="json", by_alias=True))
            existing_event = await self.db.scalar(
                select(OmnichannelScheduledMessageEvent).where(
                    OmnichannelScheduledMessageEvent.workspace_id == workspace_id,
                    OmnichannelScheduledMessageEvent.idempotency_key == payload.idempotency_key,
                )
            )
            if existing_event is not None:
                if (
                    existing_event.scheduled_message_id != message.id
                    or existing_event.payload_sha256 != payload_hash
                ):
                    raise OmnichannelConflictError(
                        "A chave de idempotência já foi usada por outro controle de agendamento."
                    )
                return message
        if message.state in {"sent", "completed", "cancelled", "failed", "delivery_uncertain"}:
            raise OmnichannelConflictError("Esse agendamento já está em estado terminal.")
        previous_state = message.state
        if payload.action == "cancel":
            message.state = "cancelled"
            message.cancellation_reason = payload.reason
            message.completed_at = _now()
        elif payload.action == "pause":
            message.state = "paused"
        elif payload.action == "resume":
            if message.state != "paused":
                raise OmnichannelConflictError("Somente mensagens pausadas podem ser retomadas.")
            message.state = "scheduled"
        elif payload.action == "reschedule":
            if payload.scheduled_at is None:
                raise ValueError("Informe a nova data para reagendar.")
            rescheduled_at = _aware(payload.scheduled_at)
            if rescheduled_at < _now() - timedelta(seconds=30):
                raise ValueError("A mensagem não pode ser reagendada no passado.")
            message.scheduled_at = rescheduled_at
            message.timezone = payload.timezone or message.timezone
            message.state = "scheduled"
        if payload.action in {"resume", "reschedule"}:
            message.next_attempt_at = None
            message.last_error_code = None
            message.last_error_message = None
        message.updated_at = _now()
        await self.db.flush()
        event_idempotency_key = payload.idempotency_key or (
            f"schedule-control:{message.id}:{payload.action}:{uuid4()}"
        )
        event = await self._append_schedule_event(
            message,
            event_type=payload.action,
            previous_state=previous_state,
            next_state=message.state,
            idempotency_key=event_idempotency_key,
            payload=payload.model_dump(mode="json", by_alias=True),
            metadata={"reason": payload.reason},
        )
        actor_label = "Controle de agendamentos KLM"
        if user_id:
            actor_label = str(
                await self.db.scalar(select(User.name).where(User.id == user_id)) or "Usuário KLM"
            )
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human" if user_id else "system",
            actor_label=actor_label,
            event_type=f"omnichannel_scheduled_message_{payload.action}",
            entity_type="omnichannel_scheduled_message",
            entity_id=message.id,
            summary=f"Agendamento alterado: {payload.action}.",
            correlation_id=event_idempotency_key,
            before_data={"state": previous_state},
            after_data={"state": message.state, "scheduleEventId": str(event.id)},
        )
        queue_realtime_event(
            self.db,
            workspace_id=workspace_id,
            event_type="OMNICHANNEL_SCHEDULE_UPDATED",
            data={
                "thread_id": str(message.thread_id),
                "scheduled_message_id": str(message.id),
                "state": message.state,
                "event_id": str(event.id),
            },
        )
        return message

    async def list_scheduled_message_events(
        self,
        workspace_id: UUID,
        scheduled_message_id: UUID,
        *,
        limit: int = 200,
    ) -> list[OmnichannelScheduledMessageEvent]:
        exists = await self.db.scalar(
            select(OmnichannelScheduledMessage.id).where(
                OmnichannelScheduledMessage.workspace_id == workspace_id,
                OmnichannelScheduledMessage.id == scheduled_message_id,
            )
        )
        if exists is None:
            raise LookupError("Mensagem agendada não encontrada no workspace.")
        return list(
            (
                await self.db.execute(
                    select(OmnichannelScheduledMessageEvent)
                    .where(
                        OmnichannelScheduledMessageEvent.workspace_id == workspace_id,
                        OmnichannelScheduledMessageEvent.scheduled_message_id
                        == scheduled_message_id,
                    )
                    .order_by(
                        OmnichannelScheduledMessageEvent.occurred_at.asc(),
                        OmnichannelScheduledMessageEvent.id.asc(),
                    )
                    .limit(max(1, min(limit, 500)))
                )
            ).scalars()
        )

    async def create_call(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        payload: CallSessionCreate,
    ) -> tuple[OmnichannelCallSession, bool]:
        thread = await self._thread(workspace_id, thread_id)
        existing = await self.db.scalar(
            select(OmnichannelCallSession).where(
                OmnichannelCallSession.workspace_id == workspace_id,
                OmnichannelCallSession.idempotency_key == payload.idempotency_key,
            )
        )
        if existing:
            if (
                existing.thread_id != thread.id
                or existing.channel != payload.channel.value
                or existing.direction != payload.direction
            ):
                raise OmnichannelConflictError("Chave de idempotência já usada por outra chamada.")
            return existing, True
        endpoint = None
        if payload.endpoint_id:
            endpoint = await self.db.scalar(
                select(OmnichannelEndpoint).where(
                    OmnichannelEndpoint.workspace_id == workspace_id,
                    OmnichannelEndpoint.id == payload.endpoint_id,
                )
            )
            if not endpoint or (thread.identity_id and endpoint.identity_id != thread.identity_id):
                raise LookupError("Endpoint não pertence à identidade da conversa.")
        connection_filters = [
            OmnichannelChannelConnection.workspace_id == workspace_id,
            OmnichannelChannelConnection.connection_key == payload.connection_key,
            OmnichannelChannelConnection.channel == payload.channel.value,
        ]
        if endpoint and endpoint.provider:
            connection_filters.append(OmnichannelChannelConnection.provider == endpoint.provider)
        connections = list(
            (
                await self.db.execute(
                    select(OmnichannelChannelConnection).where(*connection_filters).limit(2)
                )
            ).scalars()
        )
        if len(connections) > 1:
            raise OmnichannelConflictError(
                "A rota da chamada é ambígua; selecione uma conexão exata."
            )
        connection = connections[0] if connections else None
        if endpoint and endpoint.connection_id:
            if connection is None or connection.id != endpoint.connection_id:
                raise OmnichannelConflictError("O endpoint e a conexão da chamada divergem.")
        call = OmnichannelCallSession(
            workspace_id=workspace_id,
            thread_id=thread.id,
            endpoint_id=payload.endpoint_id,
            connection_id=connection.id if connection else None,
            connection_key=payload.connection_key,
            channel=payload.channel.value,
            direction=payload.direction,
            state="requested",
            permission_state=payload.permission_state,
            idempotency_key=payload.idempotency_key,
            call_metadata={
                **payload.metadata,
                "provider": connection.provider if connection else "local_unconfigured",
                "localOnly": not external_delivery_allowed(),
                "externalActionsExecuted": 0,
            },
        )
        self.db.add(call)
        await self.db.flush()
        self.db.add(
            OmnichannelCallEvent(
                workspace_id=workspace_id,
                call_id=call.id,
                thread_id=thread.id,
                connection_id=call.connection_id,
                provider=connection.provider if connection else "local_unconfigured",
                idempotency_key=payload.idempotency_key,
                previous_state="none",
                next_state="requested",
                payload_sha256=_canonical_hash(payload.model_dump(mode="json", by_alias=True)),
                occurred_at=call.requested_at or _now(),
                event_metadata={"source": "call_created", "externalActionsExecuted": 0},
            )
        )
        await self.db.flush()
        return call, False

    async def transition_call(
        self,
        workspace_id: UUID,
        call_id: UUID,
        payload: CallSessionTransition | InternalCallSessionTransition,
    ) -> OmnichannelCallSession:
        cost_amount = getattr(payload, "cost_amount", None)
        cost_currency = getattr(payload, "cost_currency", None)
        pricing_category = getattr(payload, "pricing_category", None)
        provider_invoice_reference = getattr(payload, "provider_invoice_reference", None)
        payload_hash = _canonical_hash(payload.model_dump(mode="json", by_alias=True))
        call = await self.db.scalar(
            select(OmnichannelCallSession)
            .where(
                OmnichannelCallSession.workspace_id == workspace_id,
                OmnichannelCallSession.id == call_id,
            )
            .with_for_update()
        )
        if not call:
            raise LookupError("Chamada não encontrada no workspace.")
        existing_event = await self.db.scalar(
            select(OmnichannelCallEvent).where(
                OmnichannelCallEvent.workspace_id == workspace_id,
                OmnichannelCallEvent.idempotency_key == payload.idempotency_key,
            )
        )
        if existing_event is not None:
            if existing_event.call_id != call.id or existing_event.payload_sha256 != payload_hash:
                raise OmnichannelConflictError(
                    "A chave de idempotência já foi usada por outro evento de chamada."
                )
            return call
        if payload.state not in _CALL_TRANSITIONS.get(call.state, set()):
            raise OmnichannelConflictError(
                f"Transição de chamada inválida: {call.state} -> {payload.state}."
            )
        if payload.state in {"ringing", "active"} and not external_delivery_allowed():
            raise OmnichannelConflictError(
                "A chamada externa permanece bloqueada no runtime local."
            )
        if cost_amount is not None and payload.state not in {"completed", "failed"}:
            raise ValueError("Custos reais só podem ser registrados em eventos terminais.")
        now = _aware(payload.occurred_at)
        previous_state = call.state
        call.state = payload.state
        if payload.permission_state:
            call.permission_state = payload.permission_state
        if payload.provider_call_id:
            call.provider_call_id = payload.provider_call_id
        if payload.state == "approved_local":
            call.approved_at = now
        elif payload.state == "ringing":
            call.started_at = now
        elif payload.state == "active":
            call.answered_at = now
        elif payload.state in {"completed", "failed", "cancelled"}:
            call.ended_at = now
        if payload.duration_seconds is not None:
            call.duration_seconds = payload.duration_seconds
        if payload.failure_reason:
            call.failure_reason = payload.failure_reason
        if payload.summary:
            call.summary = payload.summary
        provider = str((call.call_metadata or {}).get("provider") or "local_unconfigured")
        event = OmnichannelCallEvent(
            workspace_id=workspace_id,
            call_id=call.id,
            thread_id=call.thread_id,
            connection_id=call.connection_id,
            provider=provider,
            idempotency_key=payload.idempotency_key,
            external_event_id=payload.external_event_id,
            previous_state=previous_state,
            next_state=payload.state,
            payload_sha256=payload_hash,
            provider_call_id=payload.provider_call_id or call.provider_call_id,
            duration_seconds=payload.duration_seconds,
            failure_reason=payload.failure_reason,
            occurred_at=now,
            event_metadata=_scrub(payload.provider_payload),
        )
        self.db.add(event)
        await self.db.flush()
        if cost_amount is not None and cost_currency is not None:
            currency = cost_currency.upper()
            ledger = OmnichannelCallCostLedgerEntry(
                workspace_id=workspace_id,
                call_id=call.id,
                thread_id=call.thread_id,
                call_event_id=event.id,
                provider=provider,
                amount=cost_amount,
                currency=currency,
                quantity=Decimal("1"),
                unit="call",
                pricing_category=pricing_category,
                provider_invoice_reference=provider_invoice_reference,
                idempotency_key=f"call-cost:{event.id}",
                occurred_at=now,
                entry_metadata={
                    "externalEventId": payload.external_event_id,
                    "providerCallId": payload.provider_call_id or call.provider_call_id,
                },
            )
            self.db.add(ledger)
            metadata = dict(call.call_metadata or {})
            actual = dict(metadata.get("actualCostsByCurrency") or {})
            actual[currency] = str(Decimal(str(actual.get(currency) or "0")) + cost_amount)
            metadata["actualCostsByCurrency"] = actual
            call.call_metadata = metadata
            if len(actual) == 1:
                call.cost_amount = Decimal(actual[currency])
                call.cost_currency = currency
            else:
                call.cost_amount = None
                call.cost_currency = None
        call.updated_at = now
        await self.db.flush()
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=None,
            actor_type="system",
            actor_label="Runtime de chamadas KLM",
            event_type="omnichannel_call_transition_recorded",
            entity_type="omnichannel_call_session",
            entity_id=call.id,
            summary=f"Chamada avançou de {previous_state} para {payload.state}.",
            correlation_id=payload.idempotency_key,
            before_data={"state": previous_state},
            after_data={
                "state": payload.state,
                "callEventId": str(event.id),
                "costRecorded": cost_amount is not None,
            },
        )
        queue_realtime_event(
            self.db,
            workspace_id=workspace_id,
            event_type="OMNICHANNEL_CALL_UPDATED",
            data={
                "thread_id": str(call.thread_id),
                "call_id": str(call.id),
                "state": call.state,
                "event_id": str(event.id),
            },
        )
        return call

    async def list_call_events(
        self, workspace_id: UUID, call_id: UUID
    ) -> list[tuple[OmnichannelCallEvent, list[OmnichannelCallCostLedgerEntry]]]:
        call = await self.db.scalar(
            select(OmnichannelCallSession).where(
                OmnichannelCallSession.workspace_id == workspace_id,
                OmnichannelCallSession.id == call_id,
            )
        )
        if call is None:
            raise LookupError("Chamada não encontrada no workspace.")
        events = list(
            (
                await self.db.execute(
                    select(OmnichannelCallEvent)
                    .where(
                        OmnichannelCallEvent.workspace_id == workspace_id,
                        OmnichannelCallEvent.call_id == call.id,
                    )
                    .order_by(
                        OmnichannelCallEvent.occurred_at.asc(),
                        OmnichannelCallEvent.received_at.asc(),
                    )
                )
            ).scalars()
        )
        if not events:
            return []
        costs = list(
            (
                await self.db.execute(
                    select(OmnichannelCallCostLedgerEntry).where(
                        OmnichannelCallCostLedgerEntry.workspace_id == workspace_id,
                        OmnichannelCallCostLedgerEntry.call_event_id.in_(
                            [item.id for item in events]
                        ),
                    )
                )
            ).scalars()
        )
        by_event: dict[UUID, list[OmnichannelCallCostLedgerEntry]] = {}
        for item in costs:
            by_event.setdefault(item.call_event_id, []).append(item)
        return [(item, by_event.get(item.id, [])) for item in events]

    async def list_calls(self, workspace_id: UUID, thread_id: UUID) -> list[OmnichannelCallSession]:
        await self._thread(workspace_id, thread_id)
        result = await self.db.execute(
            select(OmnichannelCallSession)
            .where(
                OmnichannelCallSession.workspace_id == workspace_id,
                OmnichannelCallSession.thread_id == thread_id,
            )
            .order_by(OmnichannelCallSession.requested_at.desc())
        )
        return list(result.scalars().all())

    async def timeline(
        self, workspace_id: UUID, thread_id: UUID, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        await self._thread(workspace_id, thread_id)
        messages = list(
            (
                await self.db.execute(
                    select(OmnichannelMessage)
                    .where(
                        OmnichannelMessage.workspace_id == workspace_id,
                        OmnichannelMessage.thread_id == thread_id,
                    )
                    .order_by(OmnichannelMessage.occurred_at.desc())
                    .limit(limit)
                )
            ).scalars()
        )
        events = list(
            (
                await self.db.execute(
                    select(OmnichannelChannelEvent)
                    .where(
                        OmnichannelChannelEvent.workspace_id == workspace_id,
                        OmnichannelChannelEvent.thread_id == thread_id,
                    )
                    .order_by(OmnichannelChannelEvent.occurred_at.desc())
                    .limit(limit)
                )
            ).scalars()
        )
        routing = list(
            (
                await self.db.execute(
                    select(OmnichannelRoutingEvent)
                    .where(
                        OmnichannelRoutingEvent.workspace_id == workspace_id,
                        OmnichannelRoutingEvent.thread_id == thread_id,
                    )
                    .order_by(OmnichannelRoutingEvent.occurred_at.desc())
                    .limit(limit)
                )
            ).scalars()
        )
        schedules = await self.list_scheduled_messages(workspace_id, thread_id)
        calls = await self.list_calls(workspace_id, thread_id)
        binding_ids = {event.binding_id for event in events if event.binding_id is not None}
        bindings = []
        if binding_ids:
            bindings = list(
                (
                    await self.db.execute(
                        select(OmnichannelThreadBinding).where(
                            OmnichannelThreadBinding.workspace_id == workspace_id,
                            OmnichannelThreadBinding.id.in_(binding_ids),
                        )
                    )
                ).scalars()
            )
        channel_by_binding = {
            binding.id: str((binding.binding_metadata or {}).get("channel") or "")
            for binding in bindings
        }
        items: list[dict[str, Any]] = []
        for message in messages:
            items.append(
                {
                    "id": message.id,
                    "kind": "message",
                    "occurred_at": message.occurred_at,
                    "channel": message.channel.value,
                    "event_type": "message",
                    "direction": message.direction.value,
                    "author_label": message.author_label,
                    "body": message.body,
                    "visibility": message.audience,
                    "status": message.delivery_status.value,
                    "metadata": message.message_metadata or {},
                }
            )
        for event in events:
            items.append(
                {
                    "id": event.id,
                    "kind": "channel_event",
                    "occurred_at": event.occurred_at,
                    "channel": (
                        channel_by_binding.get(event.binding_id)
                        or str((event.event_metadata or {}).get("channel") or "")
                        or None
                    ),
                    "event_type": event.event_type,
                    "direction": None,
                    "author_label": None,
                    "body": None,
                    "visibility": event.visibility,
                    "status": event.actionability,
                    "metadata": {
                        "publicUrl": event.public_url,
                        "correlation": event.correlation,
                    },
                }
            )
        for event in routing:
            items.append(
                {
                    "id": event.id,
                    "kind": "routing",
                    "occurred_at": event.occurred_at,
                    "channel": None,
                    "event_type": event.action,
                    "direction": None,
                    "author_label": None,
                    "body": event.reason,
                    "visibility": "internal",
                    "status": event.source,
                    "metadata": {
                        "fromTeamId": str(event.from_team_id) if event.from_team_id else None,
                        "toTeamId": str(event.to_team_id) if event.to_team_id else None,
                        "fromUserId": str(event.from_user_id) if event.from_user_id else None,
                        "toUserId": str(event.to_user_id) if event.to_user_id else None,
                    },
                }
            )
        for scheduled in schedules:
            items.append(
                {
                    "id": scheduled.id,
                    "kind": "scheduled_message",
                    "occurred_at": scheduled.scheduled_at,
                    "channel": scheduled.channel,
                    "event_type": "message_scheduled",
                    "direction": "outbound",
                    "author_label": "Meu Executivo",
                    "body": scheduled.body,
                    "visibility": "internal",
                    "status": scheduled.state,
                    "metadata": {"templateId": scheduled.template_id},
                }
            )
        for call in calls:
            items.append(
                {
                    "id": call.id,
                    "kind": "call",
                    "occurred_at": call.requested_at,
                    "channel": call.channel,
                    "event_type": "call",
                    "direction": call.direction,
                    "author_label": None,
                    "body": call.summary,
                    "visibility": "internal",
                    "status": call.state,
                    "metadata": {
                        "durationSeconds": call.duration_seconds,
                        "permissionState": call.permission_state,
                    },
                }
            )
        items.sort(key=lambda item: (_aware(item["occurred_at"]), str(item["id"])))
        return items[-limit:]
