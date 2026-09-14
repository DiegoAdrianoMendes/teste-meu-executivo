"""
Serviço de agenda interna do workspace.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.db.advisory import acquire_transaction_advisory_lock
from app.db.post_commit import (
    queue_meeting_notification_wakeup,
    queue_realtime_event,
)
from app.models.entitlement import EntitlementKey
from app.models.matter import Matter
from app.models.meeting import (
    Meeting,
    MeetingCreationReceipt,
    MeetingNotificationOutbox,
    MeetingNotificationStatus,
    MeetingParticipant,
    MeetingParticipantRole,
    MeetingStatus,
)
from app.models.task import Task
from app.models.user import User
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.schemas.control_plane import (
    MeetingCreate,
    MeetingParticipantWrite,
    MeetingRead,
    MeetingUpdate,
)
from app.services.entitlement_service import (
    EntitlementDisabledError,
    EntitlementNotConfiguredError,
    EntitlementResolver,
)
from app.services.klm_operations_service import KlmOperationsService
from app.services.platform_audit_event_service import sanitize_audit_value


@dataclass(frozen=True, slots=True)
class _ResolvedMeetingParticipant:
    payload: MeetingParticipantWrite
    membership_id: UUID
    label_snapshot: str


class MeetingService:
    ONLINE_LOCATION_ALLOWLIST = frozenset(
        {
            "chamada de video",
            "google meet",
            "meet",
            "microsoft teams",
            "online",
            "reuniao online",
            "reuniao remota",
            "remota",
            "remoto",
            "teams",
            "video conferencia",
            "videochamada",
            "videoconferencia",
            "zoom",
        }
    )

    # Duração assumida para um compromisso sem `ends_at` explícito, só para
    # fins de detecção de conflito de agenda — nunca persistida, nunca usada
    # fora de `_require_no_conflict` (ver método abaixo).
    DEFAULT_EVENT_DURATION = timedelta(minutes=30)

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @staticmethod
    def _meeting_create_payload_hash(payload: MeetingCreate) -> str:
        canonical = json.dumps(
            payload.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _validated_idempotency_key(value: str) -> str:
        key = value.strip()
        if len(key) < 8 or len(key) > 120:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "MEE-020",
                    "message": "Idempotency-Key deve ter entre 8 e 120 caracteres úteis.",
                },
            )
        return key

    async def _resolve_creation_replay(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        idempotency_key: str,
        payload_hash: str,
    ) -> Meeting | None:
        """Serialize a creation key and return its prior result, when any."""

        await acquire_transaction_advisory_lock(
            self.db,
            namespace="meeting-create-idempotency",
            value=f"{workspace_id}:{actor_user_id}:{idempotency_key}",
        )
        receipt = await self.db.scalar(
            select(MeetingCreationReceipt).where(
                MeetingCreationReceipt.workspace_id == workspace_id,
                MeetingCreationReceipt.actor_user_id == actor_user_id,
                MeetingCreationReceipt.idempotency_key == idempotency_key,
            )
        )
        if receipt is None:
            return None
        if not hmac.compare_digest(receipt.payload_hash, payload_hash):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "MEETING_IDEMPOTENCY_PAYLOAD_MISMATCH",
                    "message": "A chave idempotente já foi usada com outro conteúdo.",
                },
            )
        try:
            return await self.get_for_workspace(workspace_id, receipt.meeting_id)
        except HTTPException as error:
            if error.status_code != status.HTTP_404_NOT_FOUND:
                raise
            # A FK RESTRICT torna isso impossível em um schema íntegro. Fail
            # closed instead of silently creating a second appointment.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "MEETING_IDEMPOTENCY_RECEIPT_ORPHANED",
                    "message": (
                        "O recibo idempotente existe, mas seu compromisso não está disponível."
                    ),
                },
            ) from error

    @staticmethod
    def _audit_snapshot(
        meeting: Meeting,
        *,
        participant_conflict_reason: str | None = None,
    ) -> dict:
        """Return a stable, secret-safe snapshot for immutable tenant audit."""
        snapshot = {
            "id": meeting.id,
            "workspaceId": meeting.workspace_id,
            "matterId": meeting.matter_id,
            "responsibleUserId": meeting.responsible_user_id,
            "linkedTaskId": meeting.linked_task_id,
            "title": meeting.title,
            "startsAt": meeting.starts_at,
            "endsAt": meeting.ends_at,
            "status": meeting.status,
            "natureza": meeting.natureza,
            "eventType": meeting.event_type,
            "location": meeting.location,
            # Meeting links and provider IDs may embed opaque access material.
            # The audit records their operational state without becoming a
            # credential-recovery surface.
            "meetingUrlConfigured": bool(meeting.meeting_url),
            "googleCalendarEventConfigured": bool(meeting.google_calendar_event_id),
            "notes": meeting.notes,
            "recipientOverrides": [
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "role": item.get("role"),
                    "userId": item.get("user_id"),
                    "emailConfigured": bool(item.get("email")),
                    "phoneConfigured": bool(item.get("phone")),
                }
                for item in (meeting.recipient_overrides or [])
                if isinstance(item, dict)
            ],
            "reminderSentAt": meeting.reminder_sent_at,
            "version": meeting.version,
            "participants": [
                {
                    "userId": item.user_id,
                    "role": item.role,
                    "rsvp": item.rsvp,
                    "reminderMinutesBefore": item.reminder_minutes_before,
                    "labelSnapshot": item.label_snapshot,
                }
                for item in meeting.participants
                if item.active
            ],
        }
        normalized_conflict_reason = (participant_conflict_reason or "").strip()
        if normalized_conflict_reason:
            snapshot["participantConflictConfirmation"] = {
                "confirmed": True,
                "reason": normalized_conflict_reason,
            }
        sanitized = sanitize_audit_value(snapshot)
        return sanitized if isinstance(sanitized, dict) else {}

    async def _record_mutation_audit(
        self,
        *,
        workspace_id: UUID,
        meeting_id: UUID,
        event_type: str,
        summary: str,
        before: dict | None,
        after: dict | None,
        actor_user_id: UUID | None,
        correlation_id: str,
    ) -> None:
        await KlmOperationsService(self.db).record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human" if actor_user_id is not None else "system",
            actor_label=("Usuário da agenda" if actor_user_id is not None else "meeting_service"),
            event_type=event_type,
            entity_type="meeting",
            entity_id=meeting_id,
            summary=summary,
            correlation_id=correlation_id,
            before_data=before,
            after_data=after,
        )

    async def _enqueue_scheduled_notification(
        self,
        meeting: Meeting,
        *,
        correlation_id: str,
    ) -> MeetingNotificationOutbox:
        event = MeetingNotificationOutbox(
            workspace_id=meeting.workspace_id,
            meeting_id=meeting.id,
            meeting_version=meeting.version,
            event_key="meeting_scheduled",
            dedupe_key=f"meeting_scheduled:{meeting.id}:v{meeting.version}",
            correlation_id=correlation_id,
            status=MeetingNotificationStatus.pending,
        )
        self.db.add(event)
        await self.db.flush()
        queue_meeting_notification_wakeup(
            self.db,
            workspace_id=meeting.workspace_id,
        )
        return event

    async def _supersede_notification_intents(self, meeting: Meeting) -> bool:
        """Disable retryable intents for older versions of ``meeting``.

        Returns whether an older active intent existed.  A processing lease is
        not rewritten underneath a worker; the worker performs the same
        version check immediately before delivery.  The caller can then enqueue
        the current version in the same domain transaction.
        """

        active_statuses = (
            MeetingNotificationStatus.pending,
            MeetingNotificationStatus.failed,
            MeetingNotificationStatus.processing,
        )
        had_active_intent = bool(
            await self.db.scalar(
                select(MeetingNotificationOutbox.id)
                .where(
                    MeetingNotificationOutbox.workspace_id == meeting.workspace_id,
                    MeetingNotificationOutbox.meeting_id == meeting.id,
                    MeetingNotificationOutbox.meeting_version != meeting.version,
                    MeetingNotificationOutbox.status.in_(active_statuses),
                )
                .limit(1)
            )
        )
        await self.db.execute(
            update(MeetingNotificationOutbox)
            .where(
                MeetingNotificationOutbox.workspace_id == meeting.workspace_id,
                MeetingNotificationOutbox.meeting_id == meeting.id,
                MeetingNotificationOutbox.meeting_version != meeting.version,
                MeetingNotificationOutbox.status.in_(
                    (
                        MeetingNotificationStatus.pending,
                        MeetingNotificationStatus.failed,
                    )
                ),
            )
            .values(
                status=MeetingNotificationStatus.disabled,
                last_error_code=(
                    "meeting_cancelled"
                    if meeting.status == MeetingStatus.cancelled
                    else "meeting_superseded"
                ),
                claim_token=None,
                claimed_at=None,
            )
        )
        return had_active_intent

    async def _require_compatible_linked_task(
        self,
        workspace_id: UUID,
        *,
        linked_task_id: UUID | None,
        matter_id: UUID | None,
    ) -> None:
        if linked_task_id is None:
            return
        if matter_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "MEE-011",
                    "message": "Vincular uma tarefa exige selecionar o mesmo caso.",
                },
            )
        task_matter_id = await self.db.scalar(
            select(Task.matter_id)
            .join(Matter, Matter.id == Task.matter_id)
            .where(
                Task.id == linked_task_id,
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
            )
        )
        if task_matter_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "MEE-012", "message": "Tarefa vinculada não encontrada."},
            )
        if task_matter_id != matter_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "MEE-013",
                    "message": "O compromisso e a tarefa devem pertencer ao mesmo caso.",
                },
            )

    async def _normalize_participants(
        self,
        workspace_id: UUID,
        raw_participants: list[MeetingParticipantWrite] | list[dict],
        *,
        responsible_user_id: UUID | None,
        enforce_entitlement: bool = True,
    ) -> tuple[list[_ResolvedMeetingParticipant], UUID | None]:
        participants = [
            item
            if isinstance(item, MeetingParticipantWrite)
            else MeetingParticipantWrite.model_validate(item)
            for item in raw_participants
        ]
        organizers = [
            item for item in participants if item.role == MeetingParticipantRole.organizer
        ]
        if organizers and responsible_user_id is not None:
            if organizers[0].user_id != responsible_user_id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "MEE-014",
                        "message": "O responsável e o organizador devem ser a mesma pessoa.",
                    },
                )
        elif not organizers and responsible_user_id is not None:
            participants.append(
                MeetingParticipantWrite(
                    user_id=responsible_user_id,
                    role=MeetingParticipantRole.organizer,
                )
            )
            organizers = [participants[-1]]

        user_ids = [item.user_id for item in participants]
        if len(user_ids) != len(set(user_ids)):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "MEE-015",
                    "message": "Cada pessoa pode aparecer uma única vez no compromisso.",
                },
            )
        if len(organizers) > 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "MEE-016", "message": "Só pode existir um organizador."},
            )

        if participants and enforce_entitlement:
            try:
                entitlement = await EntitlementResolver(self.db).resolve(
                    workspace_id,
                    EntitlementKey.meeting_participants,
                )
            except (EntitlementNotConfiguredError, EntitlementDisabledError) as error:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "MEE-017",
                        "message": "Participantes de compromisso não estão contratados.",
                    },
                ) from error
            if not entitlement.enabled:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "MEE-017",
                        "message": "Participantes de compromisso não estão contratados.",
                    },
                )
            if (
                entitlement.limit_quantity is not None
                and len(participants) > entitlement.limit_quantity
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "MEE-018",
                        "message": "O compromisso excede o limite contratado de participantes.",
                        "limit": entitlement.limit_quantity,
                        "requested": len(participants),
                    },
                )

        rows = (
            await self.db.execute(
                select(WorkspaceMembership, User)
                .join(User, User.id == WorkspaceMembership.user_id)
                .where(
                    WorkspaceMembership.workspace_id == workspace_id,
                    WorkspaceMembership.user_id.in_(user_ids or [UUID(int=0)]),
                    WorkspaceMembership.status == MembershipStatus.active,
                    User.is_active.is_(True),
                    User.deleted_at.is_(None),
                )
            )
        ).all()
        members = {membership.user_id: (membership.id, user) for membership, user in rows}
        missing = [user_id for user_id in user_ids if user_id not in members]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "MEE-006",
                    "message": "Todos os participantes devem ser membros ativos deste workspace.",
                },
            )
        resolved = [
            _ResolvedMeetingParticipant(
                payload=item,
                membership_id=members[item.user_id][0],
                label_snapshot=(
                    (
                        members[item.user_id][1].name
                        or members[item.user_id][1].email
                        or str(item.user_id)
                    ).strip()
                ),
            )
            for item in participants
        ]
        organizer_id = organizers[0].user_id if organizers else None
        return resolved, organizer_id

    @staticmethod
    def _active_participant_payload(meeting: Meeting) -> list[MeetingParticipantWrite]:
        return [
            MeetingParticipantWrite(
                user_id=item.user_id,
                role=item.role,
                rsvp=item.rsvp,
                reminder_minutes_before=item.reminder_minutes_before,
            )
            for item in meeting.participants
            if item.active
        ]

    @staticmethod
    def _participant_signature(
        participants: list[
            _ResolvedMeetingParticipant | MeetingParticipantWrite | MeetingParticipant
        ],
    ) -> tuple[tuple[str, str, str, int | None], ...]:
        normalized: list[tuple[str, str, str, int | None]] = []
        for item in participants:
            if isinstance(item, _ResolvedMeetingParticipant):
                participant = item.payload
            else:
                participant = item
            if isinstance(participant, MeetingParticipant) and not participant.active:
                continue
            normalized.append(
                (
                    str(participant.user_id),
                    str(participant.role.value),
                    str(participant.rsvp.value),
                    participant.reminder_minutes_before,
                )
            )
        return tuple(sorted(normalized))

    @classmethod
    def _notification_payload_hash(cls, meeting: Meeting) -> str:
        """Digest transient delivery inputs without persisting recipient PII."""

        payload = {
            "matterId": meeting.matter_id,
            "responsibleUserId": meeting.responsible_user_id,
            "title": meeting.title,
            "startsAt": meeting.starts_at,
            "endsAt": meeting.ends_at,
            "status": meeting.status,
            "location": meeting.location,
            "meetingUrl": meeting.meeting_url,
            "recipientOverrides": meeting.recipient_overrides or [],
            "participants": cls._participant_signature(list(meeting.participants)),
        }
        canonical = json.dumps(
            payload,
            default=str,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @classmethod
    def _participant_payload_with_new_organizer(
        cls,
        meeting: Meeting,
        organizer_id: UUID | None,
    ) -> list[MeetingParticipantWrite]:
        retained = [
            item
            for item in cls._active_participant_payload(meeting)
            if item.role != MeetingParticipantRole.organizer and item.user_id != organizer_id
        ]
        if organizer_id is not None:
            retained.insert(
                0,
                MeetingParticipantWrite(
                    user_id=organizer_id,
                    role=MeetingParticipantRole.organizer,
                ),
            )
        return retained

    async def _replace_participants(
        self,
        meeting: Meeting,
        participants: list[_ResolvedMeetingParticipant],
    ) -> None:
        removed_at = datetime.now(UTC)
        for existing in meeting.participants:
            if existing.active:
                existing.active = False
                existing.removed_at = removed_at
        await self.db.flush()
        for item in participants:
            meeting.participants.append(
                MeetingParticipant(
                    workspace_id=meeting.workspace_id,
                    membership_id=item.membership_id,
                    user_id=item.payload.user_id,
                    role=item.payload.role,
                    rsvp=item.payload.rsvp,
                    reminder_minutes_before=item.payload.reminder_minutes_before,
                    label_snapshot=item.label_snapshot,
                    active=True,
                )
            )
        await self.db.flush()

    async def _validate_participant_conflicts(
        self,
        workspace_id: UUID,
        participants: list[_ResolvedMeetingParticipant] | list[MeetingParticipantWrite],
        *,
        starts_at: datetime,
        ends_at: datetime | None,
        exclude_meeting_id: UUID | None = None,
        confirmed: bool,
        conflict_reason: str | None,
    ) -> bool:
        normalized = [
            item.payload if isinstance(item, _ResolvedMeetingParticipant) else item
            for item in participants
        ]
        warned: list[UUID] = []
        for participant in normalized:
            conflicts = await self._find_conflicts(
                workspace_id,
                participant.user_id,
                starts_at,
                ends_at,
                exclude_meeting_id=exclude_meeting_id,
            )
            if not conflicts:
                continue
            if participant.role == MeetingParticipantRole.organizer:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "MEE-007",
                        "message": "O organizador já tem um compromisso nesse horário.",
                        "meetingIds": [str(item.id) for item in conflicts[:5]],
                    },
                )
            if participant.role == MeetingParticipantRole.attendee:
                warned.append(participant.user_id)
        if warned and not confirmed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "MEE-008",
                    "message": "Há participantes com conflito de agenda. Confirme com motivo.",
                    "participantUserIds": [str(item) for item in warned],
                },
            )
        if warned and len((conflict_reason or "").strip()) < 8:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "MEE-019",
                    "message": "Confirmar conflitos exige motivo de pelo menos 8 caracteres.",
                },
            )
        return bool(warned)

    async def _lock_participant_calendars(
        self,
        workspace_id: UUID,
        participants: list[_ResolvedMeetingParticipant] | list[MeetingParticipantWrite],
    ) -> None:
        """Serialize conflict checks for every affected internal calendar.

        PostgreSQL row locks cannot protect a participant that has no meeting
        in the candidate interval yet.  Stable, transaction-scoped advisory
        locks close that phantom-write window; the database exclusion
        constraint remains the final guard for the organizer.
        """

        user_ids = sorted(
            {
                (
                    item.payload.user_id
                    if isinstance(item, _ResolvedMeetingParticipant)
                    else item.user_id
                )
                for item in participants
            },
            key=str,
        )
        for user_id in user_ids:
            await acquire_transaction_advisory_lock(
                self.db,
                namespace="meeting-participant-calendar",
                value=f"{workspace_id}:{user_id}",
            )

    async def list_for_workspace(
        self,
        workspace_id: UUID,
        *,
        matter_id: UUID | None = None,
    ) -> list[MeetingRead]:
        query = select(Meeting).where(Meeting.workspace_id == workspace_id)
        if matter_id is not None:
            query = query.where(Meeting.matter_id == matter_id)
        result = await self.db.execute(query.order_by(Meeting.starts_at.asc()))
        return [MeetingRead.model_validate(item) for item in result.scalars().all()]

    async def get_for_workspace(
        self,
        workspace_id: UUID,
        meeting_id: UUID,
        *,
        for_update: bool = False,
    ) -> Meeting:
        query = (
            select(Meeting)
            .options(
                selectinload(Meeting.workspace),
                selectinload(Meeting.matter).selectinload(Matter.client),
                selectinload(Meeting.responsible_user),
                selectinload(Meeting.participants),
            )
            .where(Meeting.workspace_id == workspace_id, Meeting.id == meeting_id)
            # A mesma AsyncSession pode ter carregado a reunião antes de o
            # caso/cliente e os participantes serem gravados. Forçar a
            # repopulação impede que o identity map devolva relacionamentos
            # obsoletos para os briefings e para os snapshots de auditoria.
            .execution_options(populate_existing=True)
        )
        if for_update:
            query = query.with_for_update()
        result = await self.db.execute(query)
        meeting = result.scalar_one_or_none()
        if not meeting:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "MEE-001", "message": "Reunião não encontrada."},
            )
        return meeting

    async def create_for_workspace(
        self,
        workspace_id: UUID,
        payload: MeetingCreate,
        *,
        actor_user_id: UUID | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> Meeting:
        operation_id = (correlation_id or str(uuid4()))[:120]
        normalized_key: str | None = None
        payload_hash: str | None = None
        if idempotency_key is not None:
            if actor_user_id is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "MEE-021",
                        "message": "A criação idempotente exige um ator autenticado.",
                    },
                )
            normalized_key = self._validated_idempotency_key(idempotency_key)
            payload_hash = self._meeting_create_payload_hash(payload)
            replay = await self._resolve_creation_replay(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                idempotency_key=normalized_key,
                payload_hash=payload_hash,
            )
            if replay is not None:
                return replay
        self._validate_meeting_interval(payload.starts_at, payload.ends_at)
        if payload.matter_id:
            await self._lock_workspace_matters(workspace_id, payload.matter_id)
        await self._require_compatible_linked_task(
            workspace_id,
            linked_task_id=payload.linked_task_id,
            matter_id=payload.matter_id,
        )
        self._validate_meeting_url(payload.meeting_url)
        participants, organizer_id = await self._normalize_participants(
            workspace_id,
            payload.participants,
            responsible_user_id=payload.responsible_user_id,
            enforce_entitlement=bool(payload.participants),
        )
        await self._lock_participant_calendars(workspace_id, participants)
        participant_conflict_confirmed = await self._validate_participant_conflicts(
            workspace_id,
            participants,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            confirmed=payload.confirm_participant_conflicts,
            conflict_reason=payload.conflict_reason,
        )
        if payload.matter_id:
            await self._require_first_meeting_online(
                workspace_id,
                payload.matter_id,
                starts_at=payload.starts_at,
                location=payload.location,
                meeting_url=payload.meeting_url,
            )
        meeting = Meeting(
            workspace_id=workspace_id,
            matter_id=payload.matter_id,
            responsible_user_id=organizer_id,
            linked_task_id=payload.linked_task_id,
            title=payload.title,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            natureza=payload.natureza,
            event_type=payload.event_type,
            location=payload.location,
            meeting_url=payload.meeting_url,
            notes=payload.notes,
            recipient_overrides=[
                item.model_dump(by_alias=False) for item in payload.recipient_overrides
            ],
            google_calendar_event_id=payload.google_calendar_event_id,
            status=MeetingStatus.scheduled,
            participants=[],
        )
        self.db.add(meeting)
        await self.db.flush()
        await self._replace_participants(meeting, participants)
        if payload.matter_id is not None:
            await self._record_meeting_booked(workspace_id, meeting)
        self._queue_realtime_event(workspace_id, "MEETING_CREATED", meeting)
        created = await self.get_for_workspace(workspace_id, meeting.id)
        await self._record_mutation_audit(
            workspace_id=workspace_id,
            meeting_id=created.id,
            event_type="meeting_created",
            summary="Compromisso criado na agenda.",
            before=None,
            after=self._audit_snapshot(
                created,
                participant_conflict_reason=(
                    payload.conflict_reason if participant_conflict_confirmed else None
                ),
            ),
            actor_user_id=actor_user_id,
            correlation_id=operation_id,
        )
        await self._enqueue_scheduled_notification(
            created,
            correlation_id=operation_id,
        )
        if normalized_key is not None and payload_hash is not None and actor_user_id is not None:
            self.db.add(
                MeetingCreationReceipt(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    idempotency_key=normalized_key,
                    payload_hash=payload_hash,
                    meeting_id=created.id,
                )
            )
            await self.db.flush()
        return created

    async def _record_meeting_booked(self, workspace_id: UUID, meeting: Meeting) -> None:
        """Marco de funil: reunião real agendada vira AcquisitionEvent idempotente
        (auditoria 2026-07-26, item 7 — antes só existia no seed local)."""
        if meeting.matter_id is None:
            return
        from app.services.conversion_events_service import record_conversion_event

        matter = await self.db.scalar(
            select(Matter).where(
                Matter.workspace_id == workspace_id,
                Matter.id == meeting.matter_id,
            )
        )
        if matter is None:
            return
        try:
            await record_conversion_event(
                self.db,
                workspace_id=workspace_id,
                user_id=None,
                marco="reuniao_agendada",
                matter_id=matter.id,
                client_id=matter.client_id,
                channel="agenda",
                source=matter.lead_source,
                dedupe_suffix=str(meeting.id),
                happened_at=meeting.starts_at,
                event_metadata={
                    "meetingId": str(meeting.id),
                    "meetingUrl": meeting.meeting_url,
                    "responsibleUserId": (
                        str(meeting.responsible_user_id) if meeting.responsible_user_id else None
                    ),
                },
            )
        except ValueError as exc:  # noqa: BLE001 — marco de funil nunca derruba a agenda
            logger.warning(
                "meeting_service.acquisition_event_replay_mismatch",
                meeting_id=str(meeting.id),
                error_type=safe_error_type(exc),
            )

    async def update_for_workspace(
        self,
        workspace_id: UUID,
        meeting_id: UUID,
        payload: MeetingUpdate,
        *,
        actor_user_id: UUID | None = None,
        correlation_id: str | None = None,
    ) -> Meeting:
        operation_id = (correlation_id or str(uuid4()))[:120]
        meeting = await self.get_for_workspace(workspace_id, meeting_id, for_update=True)
        before = self._audit_snapshot(meeting)
        notification_before = self._notification_payload_hash(meeting)
        update_data = payload.model_dump(exclude_unset=True, by_alias=False)
        target_matter_id = update_data.get("matter_id", meeting.matter_id)
        target_linked_task_id = update_data.get("linked_task_id", meeting.linked_task_id)
        target_status = update_data.get("status", meeting.status)
        target_responsible_user_id = update_data.get(
            "responsible_user_id",
            meeting.responsible_user_id,
        )
        target_starts_at = update_data.get("starts_at", meeting.starts_at)
        target_ends_at = update_data.get("ends_at", meeting.ends_at)
        target_location = update_data.get("location", meeting.location)
        target_url = update_data.get("meeting_url", meeting.meeting_url)
        self._validate_meeting_interval(target_starts_at, target_ends_at)
        expected_version = update_data.pop("expected_version")
        if expected_version != meeting.version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "MEE-009",
                    "message": (
                        "O compromisso foi alterado por outra pessoa. Atualize e tente novamente."
                    ),
                    "currentVersion": meeting.version,
                },
            )
        await self._lock_workspace_matters(
            workspace_id,
            meeting.matter_id,
            target_matter_id,
        )
        await self._require_compatible_linked_task(
            workspace_id,
            linked_task_id=target_linked_task_id,
            matter_id=target_matter_id,
        )
        self._validate_meeting_url(target_url)
        participant_payload = update_data.pop("participants", None)
        participants_explicit = participant_payload is not None
        if participant_payload is None and "responsible_user_id" in update_data:
            participant_payload = self._participant_payload_with_new_organizer(
                meeting,
                target_responsible_user_id,
            )
        if participant_payload is not None:
            participants, organizer_id = await self._normalize_participants(
                workspace_id,
                participant_payload,
                responsible_user_id=(
                    target_responsible_user_id if "responsible_user_id" in update_data else None
                ),
                enforce_entitlement=participants_explicit,
            )
            target_responsible_user_id = organizer_id
            update_data["responsible_user_id"] = organizer_id
        else:
            participants = self._active_participant_payload(meeting)
        confirm_participant_conflicts = update_data.pop(
            "confirm_participant_conflicts",
            False,
        )
        conflict_reason = update_data.pop("conflict_reason", None)
        if "recipient_overrides" in update_data and update_data["recipient_overrides"] is not None:
            update_data["recipient_overrides"] = [
                item.model_dump(by_alias=False) if hasattr(item, "model_dump") else item
                for item in update_data["recipient_overrides"]
            ]
        participant_changed = participant_payload is not None and (
            self._participant_signature(list(participants))
            != self._participant_signature(list(meeting.participants))
        )
        scalar_changed = any(
            getattr(meeting, field) != value for field, value in update_data.items()
        )
        if not participant_changed and not scalar_changed:
            return meeting
        participant_conflict_confirmed = False
        if target_status != MeetingStatus.cancelled:
            await self._lock_participant_calendars(workspace_id, participants)
            participant_conflict_confirmed = await self._validate_participant_conflicts(
                workspace_id,
                participants,
                starts_at=target_starts_at,
                ends_at=target_ends_at,
                exclude_meeting_id=meeting.id,
                confirmed=confirm_participant_conflicts,
                conflict_reason=conflict_reason,
            )
        if (
            meeting.matter_id is not None
            and meeting.status != MeetingStatus.cancelled
            and (meeting.matter_id != target_matter_id or target_status == MeetingStatus.cancelled)
        ):
            await self._require_first_remaining_meeting_online(
                workspace_id,
                meeting.matter_id,
                removed_meeting_id=meeting.id,
            )
        if target_matter_id and target_status != MeetingStatus.cancelled:
            await self._require_first_meeting_online(
                workspace_id,
                target_matter_id,
                starts_at=target_starts_at,
                location=target_location,
                meeting_url=target_url,
                current_meeting_id=meeting.id,
            )
        schedule_changed = (
            target_starts_at != meeting.starts_at or target_ends_at != meeting.ends_at
        )
        for field, value in update_data.items():
            setattr(meeting, field, value)
        if schedule_changed:
            meeting.reminder_sent_at = None
        if participant_payload is not None:
            await self._replace_participants(meeting, participants)
        meeting.version += 1
        await self.db.flush()
        self._queue_realtime_event(workspace_id, "MEETING_UPDATED", meeting)
        updated = await self.get_for_workspace(workspace_id, meeting.id)
        after = self._audit_snapshot(
            updated,
            participant_conflict_reason=(
                conflict_reason if participant_conflict_confirmed else None
            ),
        )
        status_changed = before.get("status") != after.get("status")
        await self._record_mutation_audit(
            workspace_id=workspace_id,
            meeting_id=updated.id,
            event_type=("meeting_status_changed" if status_changed else "meeting_updated"),
            summary=(
                "Status do compromisso atualizado na agenda."
                if status_changed
                else "Compromisso atualizado na agenda."
            ),
            before=before,
            after=after,
            actor_user_id=actor_user_id,
            correlation_id=operation_id,
        )
        notification_changed = notification_before != self._notification_payload_hash(updated)
        had_active_intent = await self._supersede_notification_intents(updated)
        if updated.status == MeetingStatus.scheduled and (
            notification_changed or had_active_intent
        ):
            await self._enqueue_scheduled_notification(
                updated,
                correlation_id=operation_id,
            )
        return updated

    async def replace_participants_for_workspace(
        self,
        workspace_id: UUID,
        meeting_id: UUID,
        *,
        participants: list[MeetingParticipantWrite],
        expected_version: int,
        confirm_participant_conflicts: bool = False,
        conflict_reason: str | None = None,
        actor_user_id: UUID | None = None,
        correlation_id: str | None = None,
    ) -> Meeting:
        operation_id = (correlation_id or str(uuid4()))[:120]
        meeting = await self.get_for_workspace(workspace_id, meeting_id, for_update=True)
        before = self._audit_snapshot(meeting)
        if expected_version != meeting.version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "MEE-009",
                    "message": (
                        "O compromisso foi alterado por outra pessoa. Atualize e tente novamente."
                    ),
                    "currentVersion": meeting.version,
                },
            )
        normalized, organizer_id = await self._normalize_participants(
            workspace_id,
            participants,
            responsible_user_id=None,
        )
        if organizer_id == meeting.responsible_user_id and self._participant_signature(
            list(normalized)
        ) == self._participant_signature(list(meeting.participants)):
            return meeting
        participant_conflict_confirmed = False
        if meeting.status != MeetingStatus.cancelled:
            await self._lock_participant_calendars(workspace_id, normalized)
            participant_conflict_confirmed = await self._validate_participant_conflicts(
                workspace_id,
                normalized,
                starts_at=meeting.starts_at,
                ends_at=meeting.ends_at,
                exclude_meeting_id=meeting.id,
                confirmed=confirm_participant_conflicts,
                conflict_reason=conflict_reason,
            )
        meeting.responsible_user_id = organizer_id
        await self._replace_participants(meeting, normalized)
        meeting.version += 1
        await self.db.flush()
        self._queue_realtime_event(workspace_id, "MEETING_PARTICIPANTS_UPDATED", meeting)
        updated = await self.get_for_workspace(workspace_id, meeting.id)
        await self._record_mutation_audit(
            workspace_id=workspace_id,
            meeting_id=updated.id,
            event_type="meeting_participants_changed",
            summary="Participantes internos do compromisso atualizados.",
            before=before,
            after=self._audit_snapshot(
                updated,
                participant_conflict_reason=(
                    conflict_reason if participant_conflict_confirmed else None
                ),
            ),
            actor_user_id=actor_user_id,
            correlation_id=operation_id,
        )
        await self._supersede_notification_intents(updated)
        if updated.status == MeetingStatus.scheduled:
            await self._enqueue_scheduled_notification(
                updated,
                correlation_id=operation_id,
            )
        return updated

    async def delete_for_workspace(
        self,
        workspace_id: UUID,
        meeting_id: UUID,
        *,
        expected_version: int,
        actor_user_id: UUID | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Cancel a meeting while preserving its immutable operational history.

        The public DELETE route keeps its idempotent HTTP contract, but the
        domain resource is never physically removed. Notification outbox rows,
        participant snapshots and audit references must remain queryable.
        """
        operation_id = (correlation_id or str(uuid4()))[:120]
        meeting = await self.get_for_workspace(workspace_id, meeting_id, for_update=True)
        # DELETE is idempotent. A replay after a successful cancellation must
        # not increment the version or duplicate the audit event, even if the
        # caller repeats the original expectedVersion.
        if meeting.status == MeetingStatus.cancelled:
            return
        if expected_version != meeting.version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "MEE-009",
                    "message": (
                        "O compromisso foi alterado por outra pessoa. Atualize e tente novamente."
                    ),
                    "currentVersion": meeting.version,
                },
            )
        before = self._audit_snapshot(meeting)
        await self._lock_workspace_matters(workspace_id, meeting.matter_id)
        if meeting.matter_id is not None and meeting.status != MeetingStatus.cancelled:
            await self._require_first_remaining_meeting_online(
                workspace_id,
                meeting.matter_id,
                removed_meeting_id=meeting.id,
            )
        meeting.status = MeetingStatus.cancelled
        meeting.version += 1
        # Pending notification intents are evidence and therefore retained,
        # but they must not be delivered after cancellation. A row already
        # leased by a worker is left untouched; the worker re-checks the
        # meeting status before delivery and disables it safely.
        await self.db.execute(
            update(MeetingNotificationOutbox)
            .where(
                MeetingNotificationOutbox.workspace_id == workspace_id,
                MeetingNotificationOutbox.meeting_id == meeting.id,
                MeetingNotificationOutbox.status.in_(
                    (
                        MeetingNotificationStatus.pending,
                        MeetingNotificationStatus.failed,
                    )
                ),
            )
            .values(
                status=MeetingNotificationStatus.disabled,
                last_error_code="meeting_cancelled",
                claim_token=None,
                claimed_at=None,
            )
        )
        await self.db.flush()
        cancelled = await self.get_for_workspace(workspace_id, meeting.id)
        await self._record_mutation_audit(
            workspace_id=workspace_id,
            meeting_id=cancelled.id,
            event_type="meeting_cancelled",
            summary="Compromisso cancelado e preservado no histórico da agenda.",
            before=before,
            after=self._audit_snapshot(cancelled),
            actor_user_id=actor_user_id,
            correlation_id=operation_id,
        )
        self._queue_realtime_event(workspace_id, "MEETING_CANCELLED", cancelled)

    def _queue_realtime_event(
        self,
        workspace_id: UUID,
        event_type: str,
        meeting: Meeting,
    ) -> None:
        data = {
            "meeting_id": str(meeting.id),
            "title": meeting.title,
            "occurred_at": datetime.now(UTC).isoformat(),
        }
        if event_type != "MEETING_DELETED":
            data["starts_at"] = meeting.starts_at.isoformat()
        queue_realtime_event(
            self.db,
            workspace_id=workspace_id,
            event_type=event_type,
            data=data,
        )

    async def list_due_reminders(
        self,
        now: datetime | None = None,
        *,
        limit: int = 50,
    ) -> list[Meeting]:
        now = now or datetime.now(UTC)
        result = await self.db.execute(
            select(Meeting)
            .options(
                selectinload(Meeting.workspace),
                selectinload(Meeting.matter).selectinload(Matter.client),
                selectinload(Meeting.responsible_user),
            )
            .where(
                Meeting.status == MeetingStatus.scheduled,
                Meeting.starts_at >= now,
                Meeting.reminder_sent_at.is_(None),
            )
            .order_by(Meeting.starts_at.asc())
            .with_for_update(skip_locked=True, of=Meeting)
            .limit(max(1, min(int(limit), 200)))
        )
        return list(result.scalars().all())

    async def mark_reminder_sent(self, meeting: Meeting, sent_at: datetime | None = None) -> None:
        meeting.reminder_sent_at = sent_at or datetime.now(UTC)
        await self.db.flush()

    async def _lock_workspace_matters(
        self,
        workspace_id: UUID,
        *matter_ids: UUID | None,
    ) -> None:
        requested = sorted(
            {matter_id for matter_id in matter_ids if matter_id is not None},
            key=str,
        )
        if not requested:
            return
        locked = set(
            (
                await self.db.scalars(
                    select(Matter.id)
                    .where(
                        Matter.id.in_(requested),
                        Matter.workspace_id == workspace_id,
                        Matter.deleted_at.is_(None),
                    )
                    .order_by(Matter.id.asc())
                    .with_for_update()
                )
            ).all()
        )
        if locked != set(requested):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "MEE-002",
                    "message": "Caso não encontrado neste workspace.",
                },
            )

    async def _require_workspace_matter(self, workspace_id: UUID, matter_id: UUID) -> None:
        """Compatibilidade interna; valida e serializa alterações da agenda do caso."""
        await self._lock_workspace_matters(workspace_id, matter_id)

    async def _require_active_workspace_member(
        self,
        workspace_id: UUID,
        user_id: UUID,
    ) -> None:
        membership_id = await self.db.scalar(
            select(WorkspaceMembership.id)
            .join(User, User.id == WorkspaceMembership.user_id)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.status == MembershipStatus.active,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
        if membership_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "MEE-006",
                    "message": "O responsável deve ser um membro ativo deste workspace.",
                },
            )

    async def _require_no_conflict(
        self,
        workspace_id: UUID,
        responsible_user_id: UUID | None,
        starts_at: datetime,
        ends_at: datetime | None,
        *,
        exclude_meeting_id: UUID | None = None,
    ) -> None:
        """Conflito de agenda unificada: a MESMA pessoa não pode ter dois
        compromissos sobrepostos — comercial e jurídico convivem na mesma
        tabela `meetings`, então esta query já cobre as duas naturezas sem
        nenhuma distinção especial (ver `Meeting.natureza`)."""
        if responsible_user_id is None:
            return
        conflicts = await self._find_conflicts(
            workspace_id,
            responsible_user_id,
            starts_at,
            ends_at,
            exclude_meeting_id=exclude_meeting_id,
        )
        if conflicts:
            existing = conflicts[0]
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "MEE-007",
                    "message": (
                        "O responsável já tem um compromisso nesse horário "
                        f"(natureza: {existing.natureza.value})."
                    ),
                },
            )

    async def _find_conflicts(
        self,
        workspace_id: UUID,
        user_id: UUID,
        starts_at: datetime,
        ends_at: datetime | None,
        *,
        exclude_meeting_id: UUID | None = None,
    ) -> list[Meeting]:
        starts_at = self._as_utc(starts_at)
        ends_at = self._as_utc(ends_at) if ends_at is not None else None
        candidate_end = ends_at or (starts_at + self.DEFAULT_EVENT_DURATION)
        window_start = starts_at - self.DEFAULT_EVENT_DURATION
        window_end = candidate_end + self.DEFAULT_EVENT_DURATION
        participant_meetings = select(MeetingParticipant.meeting_id).where(
            MeetingParticipant.workspace_id == workspace_id,
            MeetingParticipant.user_id == user_id,
            MeetingParticipant.active.is_(True),
        )
        query = select(Meeting).where(
            Meeting.workspace_id == workspace_id,
            or_(
                Meeting.responsible_user_id == user_id,
                Meeting.id.in_(participant_meetings),
            ),
            Meeting.status.notin_([MeetingStatus.cancelled, MeetingStatus.no_show]),
            Meeting.starts_at >= window_start,
            Meeting.starts_at <= window_end,
        )
        if exclude_meeting_id is not None:
            query = query.where(Meeting.id != exclude_meeting_id)
        candidates = list((await self.db.scalars(query)).unique().all())
        conflicts: list[Meeting] = []
        for existing in candidates:
            existing_starts_at = self._as_utc(existing.starts_at)
            existing_end = (
                self._as_utc(existing.ends_at)
                if existing.ends_at is not None
                else existing_starts_at + self.DEFAULT_EVENT_DURATION
            )
            if existing_starts_at < candidate_end and existing_end > starts_at:
                conflicts.append(existing)
        return conflicts

    async def _require_first_meeting_online(
        self,
        workspace_id: UUID,
        matter_id: UUID,
        *,
        starts_at: datetime,
        location: str | None,
        meeting_url: str | None,
        current_meeting_id: UUID | None = None,
    ) -> None:
        prior_meeting_query = (
            select(Meeting.location, Meeting.meeting_url)
            .where(
                Meeting.workspace_id == workspace_id,
                Meeting.matter_id == matter_id,
                Meeting.status != MeetingStatus.cancelled,
                Meeting.starts_at < starts_at,
            )
            .order_by(Meeting.starts_at.asc(), Meeting.created_at.asc(), Meeting.id.asc())
        )
        if current_meeting_id is not None:
            prior_meeting_query = prior_meeting_query.where(Meeting.id != current_meeting_id)
        prior_meeting = (await self.db.execute(prior_meeting_query.limit(1))).first()
        if prior_meeting is not None:
            location = prior_meeting.location
            meeting_url = prior_meeting.meeting_url
        if self._has_online_evidence(location, meeting_url):
            return
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "MEE-003",
                "message": "A primeira reunião do caso deve ser online.",
            },
        )

    async def _require_first_remaining_meeting_online(
        self,
        workspace_id: UUID,
        matter_id: UUID,
        *,
        removed_meeting_id: UUID,
    ) -> None:
        first_remaining = (
            await self.db.execute(
                select(Meeting.location, Meeting.meeting_url)
                .where(
                    Meeting.workspace_id == workspace_id,
                    Meeting.matter_id == matter_id,
                    Meeting.id != removed_meeting_id,
                    Meeting.status != MeetingStatus.cancelled,
                )
                .order_by(Meeting.starts_at.asc(), Meeting.created_at.asc(), Meeting.id.asc())
                .limit(1)
            )
        ).first()
        if first_remaining is None or self._has_online_evidence(
            first_remaining.location,
            first_remaining.meeting_url,
        ):
            return
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "MEE-003",
                "message": "A primeira reunião do caso deve ser online.",
            },
        )

    @classmethod
    def _has_online_evidence(cls, location: str | None, meeting_url: str | None) -> bool:
        return cls._is_valid_meeting_url(meeting_url) or cls._is_online_location(location)

    @classmethod
    def _is_online_location(cls, location: str | None) -> bool:
        if not location:
            return False
        decomposed = unicodedata.normalize("NFKD", location).casefold()
        without_accents = "".join(
            character for character in decomposed if not unicodedata.combining(character)
        )
        normalized = " ".join(
            "".join(
                character if character.isalnum() else " " for character in without_accents
            ).split()
        )
        return normalized in cls.ONLINE_LOCATION_ALLOWLIST

    @staticmethod
    def _is_valid_meeting_url(meeting_url: str | None) -> bool:
        if not meeting_url:
            return False
        if meeting_url != meeting_url.strip() or any(
            character.isspace() or ord(character) < 32 for character in meeting_url
        ):
            return False
        try:
            parsed = urlsplit(meeting_url)
            parsed.port
        except ValueError:
            return False
        return bool(
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
        )

    @classmethod
    def _validate_meeting_url(cls, meeting_url: str | None) -> None:
        if meeting_url and not cls._is_valid_meeting_url(meeting_url):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "MEE-004",
                    "message": "O link da reunião deve usar HTTP ou HTTPS sem credenciais.",
                },
            )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        """SQLite não preserva `tzinfo` em `DateTime(timezone=True)` — o
        valor lido de volta do banco vem naive. Normalizar para UTC antes
        de comparar em Python evita `TypeError` (naive vs aware); em
        Postgres o valor já vem com tzinfo e `astimezone` é um no-op."""
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _validate_meeting_interval(
        starts_at: datetime | None,
        ends_at: datetime | None,
    ) -> None:
        valid_interval = starts_at is not None
        if valid_interval and ends_at is not None:
            try:
                valid_interval = ends_at > starts_at
            except TypeError:
                valid_interval = False
        if not valid_interval:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "MEE-005",
                    "message": "O término da reunião deve ser posterior ao início.",
                },
            )
