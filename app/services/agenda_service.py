"""Agenda operacional: disponibilidade, booking público e briefings de reunião."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime, time, timedelta
from secrets import token_urlsafe
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.lead_privacy import masked_lead_label, redact_sensitive_metadata
from app.core.security import create_booking_token, verify_booking_token
from app.db.tenant import set_workspace_context
from app.models.booking_link import BookingLink
from app.models.card_score import CardScore
from app.models.client import Client
from app.models.conversation_insight import ConversationInsight
from app.models.document import Document, DocumentStatus
from app.models.matter import Matter
from app.models.meeting import Meeting, MeetingEventType, MeetingNature, MeetingStatus
from app.models.process_monitoring import DeadlineStatus, MonitoredProcess, ProcessPublication
from app.models.task import Task, TaskParticipant, TaskParticipantRole
from app.models.workspace import Workspace
from app.schemas.agenda import (
    AgendaUnifiedEventRead,
    AvailabilityPreviewRead,
    BookingLinkCreate,
    BookingLinkRead,
    BookingSlotRead,
    CalendarConnectionStatus,
    CalendarProvider,
    CalendarSettingsRead,
    CalendarSettingsUpdate,
    DailyBriefingItemRead,
    DailyBriefingRead,
    InternalBookingCreate,
    MeetingBriefingRead,
    PublicBookingConfirmationRead,
    PublicBookingCreate,
    PublicBookingPageRead,
)
from app.schemas.control_plane import MeetingCreate, WorkspaceNotificationRecipientUpdate
from app.services.klm_operations_service import KlmOperationsService
from app.services.meeting_service import MeetingService
from app.services.process_monitoring_service import (
    ProcessMonitoringModuleNotContractedError,
    ProcessMonitoringService,
)
from app.services.workspace_policy_service import build_default_workspace_settings


class AgendaService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_settings(self, workspace_id: UUID) -> CalendarSettingsRead:
        workspace = await self._get_workspace(workspace_id)
        return self._settings_from_workspace(workspace)

    async def update_settings(
        self,
        workspace_id: UUID,
        payload: CalendarSettingsUpdate,
        *,
        actor_user_id: UUID,
        actor_label: str,
    ) -> CalendarSettingsRead:
        workspace = await self._get_workspace(workspace_id, for_update=True)
        MeetingService._validate_meeting_url(payload.default_meeting_url)
        before = self._settings_from_workspace(workspace)
        connection_status = self._connection_status(
            payload.provider,
            payload.default_meeting_url,
            previous=before.connection_status,
        )
        calendar_data = payload.model_dump(mode="json", by_alias=True)
        calendar_data["connectionStatus"] = connection_status.value
        settings_data = deepcopy(workspace.settings or {})
        settings_data["calendar"] = calendar_data
        workspace.settings = settings_data
        await self.db.flush()
        after = self._settings_from_workspace(workspace)
        await KlmOperationsService(self.db).record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label=actor_label,
            event_type="calendar_settings_updated",
            entity_type="workspace",
            entity_id=workspace_id,
            summary="Disponibilidade e página de agendamento atualizadas.",
            before_data=before.model_dump(mode="json", by_alias=True),
            after_data=after.model_dump(mode="json", by_alias=True),
        )
        return after

    async def create_booking_link(
        self,
        workspace_id: UUID,
        payload: BookingLinkCreate,
        *,
        actor_user_id: UUID,
        actor_label: str,
    ) -> BookingLinkRead:
        settings = await self.get_settings(workspace_id)
        MeetingService._validate_meeting_url(payload.meeting_url)
        matter_label = None
        if payload.matter_id:
            matter = await self._get_matter(workspace_id, payload.matter_id)
            matter_label = f"{matter.client.name} · {matter.demand_type}"
        if payload.responsible_user_id:
            await MeetingService(self.db)._require_active_workspace_member(
                workspace_id,
                payload.responsible_user_id,
            )
        now = datetime.now(UTC)
        expires_at = payload.expires_at or now + timedelta(days=30)
        expires_at = self._as_utc(expires_at)
        if expires_at <= now:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "BOOK-003", "message": "A validade do link deve estar no futuro."},
            )
        meeting_url = payload.meeting_url or settings.default_meeting_url
        provider = payload.meeting_provider or settings.provider
        link = BookingLink(
            workspace_id=workspace_id,
            matter_id=payload.matter_id,
            created_by_user_id=actor_user_id,
            responsible_user_id=payload.responsible_user_id,
            public_key=token_urlsafe(32),
            title=payload.title,
            description=payload.description,
            duration_minutes=payload.duration_minutes,
            meeting_provider=provider.value,
            meeting_url=meeting_url,
            expires_at=expires_at,
            max_uses=payload.max_uses,
            use_count=0,
            is_active=True,
            page_overrides={},
        )
        self.db.add(link)
        await self.db.flush()
        await KlmOperationsService(self.db).record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label=actor_label,
            event_type="booking_link_created",
            entity_type="booking_link",
            entity_id=link.id,
            summary=(
                "Link de agendamento criado e vinculado ao card."
                if link.matter_id
                else "Link geral de agendamento criado para bookings ainda sem card."
            ),
            after_data={
                "matterId": str(link.matter_id) if link.matter_id else None,
                "expiresAt": link.expires_at.isoformat(),
                "maxUses": link.max_uses,
            },
        )
        return self._booking_link_read(link, matter_label=matter_label)

    async def list_booking_links(
        self,
        workspace_id: UUID,
        *,
        matter_id: UUID | None = None,
    ) -> list[BookingLinkRead]:
        query = (
            select(BookingLink, Matter, Client)
            .outerjoin(
                Matter,
                (Matter.id == BookingLink.matter_id)
                & (Matter.workspace_id == BookingLink.workspace_id),
            )
            .outerjoin(Client, Client.id == Matter.client_id)
            .where(BookingLink.workspace_id == workspace_id)
        )
        if matter_id is not None:
            await self._get_matter(workspace_id, matter_id)
            query = query.where(BookingLink.matter_id == matter_id)
        rows = (
            await self.db.execute(
                query.order_by(BookingLink.created_at.desc())
            )
        ).all()
        return [
            self._booking_link_read(
                link,
                matter_label=(
                    f"{client.name} · {matter.demand_type}"
                    if matter is not None and client is not None
                    else None
                ),
            )
            for link, matter, client in rows
        ]

    async def revoke_booking_link(
        self,
        workspace_id: UUID,
        link_id: UUID,
        *,
        actor_user_id: UUID,
        actor_label: str,
    ) -> BookingLinkRead:
        link = await self._get_internal_link(workspace_id, link_id, for_update=True)
        link.is_active = False
        await self.db.flush()
        await KlmOperationsService(self.db).record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label=actor_label,
            event_type="booking_link_revoked",
            entity_type="booking_link",
            entity_id=link.id,
            summary="Link de agendamento revogado.",
            before_data={"isActive": True},
            after_data={"isActive": False},
        )
        return self._booking_link_read(link)

    async def get_public_booking_page(self, token: str) -> PublicBookingPageRead:
        link, workspace = await self._resolve_public_link(token)
        settings = self._settings_from_workspace(workspace)
        if not settings.booking_page.enabled:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "BOOK-001", "message": "Página de agendamento indisponível."},
            )
        slots = await self.available_slots(link, settings)
        return PublicBookingPageRead(
            office_name=workspace.name,
            title=link.title or settings.booking_page.title,
            description=link.description or settings.booking_page.description,
            duration_minutes=link.duration_minutes,
            timezone=settings.timezone,
            meeting_provider=CalendarProvider(link.meeting_provider),
            show_office_phone=settings.booking_page.show_office_phone,
            office_phone=settings.booking_page.office_phone,
            slots=slots,
        )

    async def create_public_booking(
        self,
        token: str,
        payload: PublicBookingCreate,
    ) -> PublicBookingConfirmationRead:
        link, workspace = await self._resolve_public_link(token, for_update=True)
        workspace = (
            await self.db.scalar(
                select(Workspace).where(Workspace.id == workspace.id).with_for_update()
            )
        ) or workspace
        settings = self._settings_from_workspace(workspace)
        if not settings.booking_page.enabled:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "BOOK-001", "message": "Página de agendamento indisponível."},
            )
        requested_start = self._as_utc(payload.starts_at)
        slots = await self.available_slots(link, settings)
        selected_slot = next(
            (item for item in slots if self._as_utc(item.starts_at) == requested_start),
            None,
        )
        if selected_slot is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "BOOK-004",
                    "message": "Esse horário não está mais disponível. Escolha outro horário.",
                },
            )
        provider = CalendarProvider(link.meeting_provider)
        meeting_url = link.meeting_url or settings.default_meeting_url
        location = self._provider_label(provider)
        recipient = WorkspaceNotificationRecipientUpdate(
            id=f"booking-{link.id}",
            name=payload.name,
            phone=payload.phone,
            email=payload.email,
            role="lead",
        )
        meeting = await MeetingService(self.db).create_for_workspace(
            workspace.id,
            MeetingCreate(
                matter_id=link.matter_id,
                responsible_user_id=link.responsible_user_id,
                title=f"Reunião inicial · {payload.name}",
                starts_at=selected_slot.starts_at,
                ends_at=selected_slot.ends_at,
                location=location,
                meeting_url=meeting_url,
                notes=payload.notes,
                recipient_overrides=[recipient],
            ),
        )
        link.use_count += 1
        if link.max_uses is not None and link.use_count >= link.max_uses:
            link.is_active = False
        await KlmOperationsService(self.db).record_audit(
            workspace_id=workspace.id,
            actor_user_id=None,
            actor_type="lead",
            actor_label=payload.name,
            event_type="public_booking_created",
            entity_type="meeting",
            entity_id=meeting.id,
            summary=(
                "Agendamento público criado e vinculado ao card."
                if link.matter_id
                else "Agendamento público criado sem card; vínculo interno pendente."
            ),
            after_data={
                "bookingLinkId": str(link.id),
                "matterId": str(link.matter_id) if link.matter_id else None,
                "startsAt": meeting.starts_at.isoformat(),
            },
        )
        await self.db.flush()
        return PublicBookingConfirmationRead(
            confirmation_id=meeting.id,
            starts_at=meeting.starts_at,
            ends_at=meeting.ends_at,
            timezone=settings.timezone,
            location=location,
            meeting_url=meeting.meeting_url,
            message=settings.booking_page.confirmation_message,
        )

    async def available_slots(
        self,
        link: BookingLink,
        settings: CalendarSettingsRead,
        *,
        now: datetime | None = None,
    ) -> list[BookingSlotRead]:
        return await self._available_slots_for_workspace(
            link.workspace_id,
            settings,
            duration_minutes=link.duration_minutes,
            responsible_user_id=link.responsible_user_id,
            now=now,
        )

    async def preview_availability(
        self,
        workspace_id: UUID,
        *,
        duration_minutes: int,
        responsible_user_id: UUID | None = None,
        now: datetime | None = None,
    ) -> AvailabilityPreviewRead:
        settings = await self.get_settings(workspace_id)
        if responsible_user_id is not None:
            await MeetingService(self.db)._require_active_workspace_member(
                workspace_id,
                responsible_user_id,
            )
        slots = await self._available_slots_for_workspace(
            workspace_id,
            settings,
            duration_minutes=duration_minutes,
            responsible_user_id=responsible_user_id,
            now=now,
        )
        return AvailabilityPreviewRead(
            timezone=settings.timezone,
            duration_minutes=duration_minutes,
            meeting_provider=settings.provider,
            connection_status=settings.connection_status,
            meeting_url=settings.default_meeting_url,
            slots=slots,
        )

    async def create_internal_booking(
        self,
        workspace_id: UUID,
        payload: InternalBookingCreate,
        *,
        actor_user_id: UUID,
        actor_label: str,
    ) -> Meeting:
        await self._get_workspace(workspace_id, for_update=True)
        matter = await self._get_matter(workspace_id, payload.matter_id)
        preview = await self.preview_availability(
            workspace_id,
            duration_minutes=payload.duration_minutes,
            responsible_user_id=payload.responsible_user_id,
        )
        requested_start = self._as_utc(payload.starts_at)
        selected = next(
            (slot for slot in preview.slots if self._as_utc(slot.starts_at) == requested_start),
            None,
        )
        if selected is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "BOOK-004",
                    "message": "Esse horário não está mais disponível. Escolha outro horário.",
                },
            )
        meeting = await MeetingService(self.db).create_for_workspace(
            workspace_id,
            MeetingCreate(
                matter_id=payload.matter_id,
                responsible_user_id=payload.responsible_user_id,
                title=payload.title,
                starts_at=selected.starts_at,
                ends_at=selected.ends_at,
                location=self._provider_label(preview.meeting_provider),
                meeting_url=preview.meeting_url,
                notes=payload.notes,
            ),
        )
        await KlmOperationsService(self.db).record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label=actor_label,
            event_type="internal_booking_created",
            entity_type="meeting",
            entity_id=meeting.id,
            summary="Reunião criada a partir de um horário livre e vinculada ao card.",
            after_data={
                "matterId": str(matter.id),
                "startsAt": meeting.starts_at.isoformat(),
                "durationMinutes": payload.duration_minutes,
            },
        )
        return meeting

    async def _available_slots_for_workspace(
        self,
        workspace_id: UUID,
        settings: CalendarSettingsRead,
        *,
        duration_minutes: int,
        responsible_user_id: UUID | None = None,
        now: datetime | None = None,
    ) -> list[BookingSlotRead]:
        now_utc = self._as_utc(now or datetime.now(UTC))
        zone = ZoneInfo(settings.timezone)
        local_today = now_utc.astimezone(zone).date()
        notice_cutoff = now_utc + timedelta(hours=settings.minimum_notice_hours)
        horizon_end_local = local_today + timedelta(days=settings.booking_horizon_days + 1)
        horizon_start_utc = datetime.combine(local_today, time.min, tzinfo=zone).astimezone(UTC)
        horizon_end_utc = datetime.combine(horizon_end_local, time.min, tzinfo=zone).astimezone(UTC)
        collision_query = select(Meeting).where(
            Meeting.workspace_id == workspace_id,
            Meeting.status.notin_([MeetingStatus.cancelled, MeetingStatus.no_show]),
            Meeting.starts_at < horizon_end_utc,
            func.coalesce(Meeting.ends_at, Meeting.starts_at) >= horizon_start_utc,
        )
        if responsible_user_id is not None:
            collision_query = collision_query.where(
                or_(
                    Meeting.responsible_user_id == responsible_user_id,
                    Meeting.responsible_user_id.is_(None),
                )
            )
        existing = list(
            (
                await self.db.scalars(
                    collision_query.order_by(Meeting.starts_at.asc())
                )
            ).all()
        )
        by_weekday = {item.weekday: item for item in settings.weekly_availability}
        duration = timedelta(minutes=duration_minutes)
        interval = timedelta(minutes=settings.slot_interval_minutes)
        before = timedelta(minutes=settings.buffer_before_minutes)
        after = timedelta(minutes=settings.buffer_after_minutes)
        slots: list[BookingSlotRead] = []

        for offset in range(settings.booking_horizon_days + 1):
            local_day = local_today + timedelta(days=offset)
            day_settings = by_weekday.get(local_day.weekday())
            if day_settings is None or not day_settings.enabled:
                continue
            for window in day_settings.windows:
                window_start = self._local_datetime(local_day, window.start, zone)
                window_end = self._local_datetime(local_day, window.end, zone)
                candidate = window_start
                while candidate + duration <= window_end:
                    candidate_utc = candidate.astimezone(UTC)
                    candidate_end_utc = (candidate + duration).astimezone(UTC)
                    if candidate_utc >= notice_cutoff and not self._has_collision(
                        candidate_utc,
                        candidate_end_utc,
                        existing,
                        before=before,
                        after=after,
                        default_duration=duration,
                    ):
                        slots.append(
                            BookingSlotRead(
                                starts_at=candidate_utc,
                                ends_at=candidate_end_utc,
                            )
                        )
                    candidate += interval
                    if len(slots) >= 180:
                        return slots
        return slots

    async def unified_agenda(
        self,
        workspace_id: UUID,
        *,
        include_tasks: bool,
        include_process_deadlines: bool,
        can_view_sensitive: bool,
        natureza: MeetingNature | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[AgendaUnifiedEventRead]:
        """Read-model unificado (`GET /agenda`): junta `Meeting` (comercial
        E jurídico — reunião/audiência/sustentação/prazo interno/revisão/
        personalizado, todos na mesma tabela) com o prazo fatal já
        CONFIRMADO do módulo de controladoria processual
        (`ProcessPublication.data_fatal`, sempre jurídico e sempre
        inviolável). Nunca recalcula prazo — reusa `dias`/`memoria_calculo`
        já produzidos por `app/services/prazo_calculator.py` na confirmação.

        `?natureza=` filtra por `comercial`/`juridico`; sem filtro (`None`,
        i.e. "todos"), as duas naturezas aparecem juntas, ordenadas por
        `starts_at`.
        """
        range_start = self._as_utc(start) if start else datetime.now(UTC)
        range_end = self._as_utc(end) if end else range_start + timedelta(days=30)

        events: list[AgendaUnifiedEventRead] = []

        meeting_query = select(Meeting).where(
            Meeting.workspace_id == workspace_id,
            Meeting.status != MeetingStatus.cancelled,
            Meeting.starts_at < range_end,
            func.coalesce(Meeting.ends_at, Meeting.starts_at) >= range_start,
        )
        if natureza is not None:
            meeting_query = meeting_query.where(Meeting.natureza == natureza)
        meetings = list(
            (await self.db.scalars(meeting_query.order_by(Meeting.starts_at.asc()))).all()
        )
        for meeting in meetings:
            meeting_title = meeting.title
            meeting_location = meeting.location
            meeting_url = meeting.meeting_url
            if not can_view_sensitive:
                meeting_title = (
                    f"Reunião do {masked_lead_label(meeting.matter_id or meeting.id)}"
                )
                meeting_location = "Online" if meeting.location or meeting.meeting_url else None
                meeting_url = None
            events.append(
                AgendaUnifiedEventRead(
                    id=f"meeting:{meeting.id}",
                    source="meeting",
                    source_id=meeting.id,
                    natureza=meeting.natureza,
                    event_type=meeting.event_type,
                    title=meeting_title,
                    starts_at=self._as_utc(meeting.starts_at),
                    ends_at=self._as_utc(meeting.ends_at) if meeting.ends_at else None,
                    matter_id=meeting.matter_id,
                    responsible_user_id=meeting.responsible_user_id,
                    status=meeting.status.value,
                    is_inviolable=False,
                    location=meeting_location,
                    meeting_url=meeting_url,
                )
            )

        # Tarefas continuam sendo a fonte autoritativa de seus próprios
        # prazos. A agenda apenas projeta os três relógios de forma explícita;
        # não copia nem recalcula datas e não usa fallback entre eles.
        if include_tasks and (natureza is None or natureza == MeetingNature.juridico):
            task_due_query = (
                select(Task, Matter, Client)
                .join(Matter, Matter.id == Task.matter_id)
                .join(
                    Client,
                    (Client.id == Matter.client_id)
                    & (Client.workspace_id == Matter.workspace_id),
                )
                .where(
                    Matter.workspace_id == workspace_id,
                    Task.cancelled_at.is_(None),
                    Task.due_at.is_not(None),
                    Task.due_at >= range_start,
                    Task.due_at <= range_end,
                )
            )
            for task, matter, client in (await self.db.execute(task_due_query)).all():
                task_title, matter_label, client_label = self._task_agenda_labels(
                    task.title,
                    matter,
                    client,
                    can_view_sensitive=can_view_sensitive,
                )
                events.append(
                    AgendaUnifiedEventRead(
                        id=f"task-due:{task.id}",
                        source="task_due",
                        source_id=task.id,
                        natureza=MeetingNature.juridico,
                        event_type=MeetingEventType.prazo_interno,
                        title=f"Tarefa · {task_title}",
                        starts_at=self._as_utc(task.due_at),
                        matter_id=matter.id,
                        matter_label=matter_label,
                        client_label=client_label,
                        task_id=task.id,
                        responsible_user_id=task.assigned_to,
                        status=task.status.value,
                        is_inviolable=False,
                    )
                )

            participant_due_query = (
                select(TaskParticipant, Task, Matter, Client)
                .join(
                    Task,
                    (Task.id == TaskParticipant.task_id)
                    & (Task.matter_id == TaskParticipant.matter_id),
                )
                .join(Matter, Matter.id == Task.matter_id)
                .join(
                    Client,
                    (Client.id == Matter.client_id)
                    & (Client.workspace_id == Matter.workspace_id),
                )
                .where(
                    TaskParticipant.workspace_id == workspace_id,
                    TaskParticipant.active.is_(True),
                    TaskParticipant.due_at.is_not(None),
                    TaskParticipant.due_at >= range_start,
                    TaskParticipant.due_at <= range_end,
                    Task.cancelled_at.is_(None),
                )
            )
            participant_role_label = {
                TaskParticipantRole.owner: "responsável",
                TaskParticipantRole.assignee: "executor",
                TaskParticipantRole.reviewer: "revisor",
                TaskParticipantRole.observer: "observador",
            }
            for participant, task, matter, client in (
                await self.db.execute(participant_due_query)
            ).all():
                role_label = participant_role_label[participant.role]
                task_title, matter_label, client_label = self._task_agenda_labels(
                    task.title,
                    matter,
                    client,
                    can_view_sensitive=can_view_sensitive,
                )
                events.append(
                    AgendaUnifiedEventRead(
                        id=f"task-participant-due:{participant.id}",
                        source="task_participant_due",
                        source_id=participant.id,
                        natureza=MeetingNature.juridico,
                        event_type=(
                            MeetingEventType.revisao
                            if participant.role == TaskParticipantRole.reviewer
                            else MeetingEventType.prazo_interno
                        ),
                        title=f"Prazo do {role_label} · {task_title}",
                        starts_at=self._as_utc(participant.due_at),
                        matter_id=matter.id,
                        matter_label=matter_label,
                        client_label=client_label,
                        task_id=task.id,
                        participant_role=participant.role.value,
                        responsible_label=participant.label_snapshot,
                        responsible_user_id=participant.user_id,
                        status=participant.state.value,
                        is_inviolable=False,
                    )
                )

            reviewer = TaskParticipant
            review_due_query = (
                select(Task, Matter, Client, reviewer)
                .join(Matter, Matter.id == Task.matter_id)
                .join(
                    Client,
                    (Client.id == Matter.client_id)
                    & (Client.workspace_id == Matter.workspace_id),
                )
                .outerjoin(
                    reviewer,
                    (reviewer.task_id == Task.id)
                    & (reviewer.matter_id == Task.matter_id)
                    & (reviewer.role == TaskParticipantRole.reviewer)
                    & reviewer.active.is_(True),
                )
                .where(
                    Matter.workspace_id == workspace_id,
                    Task.cancelled_at.is_(None),
                    Task.review_due_at.is_not(None),
                    Task.review_due_at >= range_start,
                    Task.review_due_at <= range_end,
                )
            )
            for task, matter, client, review_owner in (
                await self.db.execute(review_due_query)
            ).all():
                task_title, matter_label, client_label = self._task_agenda_labels(
                    task.title,
                    matter,
                    client,
                    can_view_sensitive=can_view_sensitive,
                )
                events.append(
                    AgendaUnifiedEventRead(
                        id=f"task-review-due:{task.id}",
                        source="task_review_due",
                        source_id=task.id,
                        natureza=MeetingNature.juridico,
                        event_type=MeetingEventType.revisao,
                        title=f"Revisão · {task_title}",
                        starts_at=self._as_utc(task.review_due_at),
                        matter_id=matter.id,
                        matter_label=matter_label,
                        client_label=client_label,
                        task_id=task.id,
                        participant_role=(
                            TaskParticipantRole.reviewer.value if review_owner else None
                        ),
                        responsible_label=(
                            review_owner.label_snapshot if review_owner else None
                        ),
                        responsible_user_id=(review_owner.user_id if review_owner else None),
                        status=task.review_state.value,
                        is_inviolable=False,
                    )
                )

        if include_process_deadlines and (
            natureza is None or natureza == MeetingNature.juridico
        ):
            try:
                await ProcessMonitoringService(self.db)._require_module_contracted(workspace_id)
            except ProcessMonitoringModuleNotContractedError:
                # Uma capability opcional ausente não derruba as demais fontes
                # que o usuário está autorizado a consultar.
                publication_rows = []
            else:
                publication_query = (
                    select(ProcessPublication, MonitoredProcess)
                    .outerjoin(
                        MonitoredProcess,
                        (MonitoredProcess.id == ProcessPublication.monitored_process_id)
                        & (MonitoredProcess.workspace_id == ProcessPublication.workspace_id),
                    )
                    .where(
                        ProcessPublication.workspace_id == workspace_id,
                        ProcessPublication.prazo_status == DeadlineStatus.confirmado,
                        ProcessPublication.data_fatal.is_not(None),
                        ProcessPublication.data_fatal >= range_start.date(),
                        ProcessPublication.data_fatal <= range_end.date(),
                    )
                )
                publication_rows = (await self.db.execute(publication_query)).all()

            for publication, monitored in publication_rows:
                starts_at = datetime.combine(publication.data_fatal, time.min, tzinfo=UTC)
                publication_title = f"Prazo fatal · {publication.numero_cnj}"
                memoria_calculo = publication.memoria_calculo
                if not can_view_sensitive:
                    publication_title = "Prazo fatal · Dados processuais protegidos"
                    memoria_calculo = None
                events.append(
                    AgendaUnifiedEventRead(
                        id=f"prazo:{publication.id}",
                        source="process_publication",
                        source_id=publication.id,
                        natureza=MeetingNature.juridico,
                        event_type=MeetingEventType.prazo_fatal,
                        title=publication_title,
                        starts_at=starts_at,
                        ends_at=None,
                        matter_id=monitored.matter_id if monitored else None,
                        responsible_user_id=(
                            (monitored.responsible_user_id if monitored else None)
                            or publication.confirmed_by_user_id
                        ),
                        status=(
                            publication.prazo_status.value if publication.prazo_status else None
                        ),
                        is_inviolable=True,
                        dias=publication.dias,
                        memoria_calculo=memoria_calculo,
                    )
                )

        events.sort(key=lambda item: self._as_utc(item.starts_at))
        return events

    @staticmethod
    def _task_agenda_labels(
        title: str,
        matter: Matter,
        client: Client,
        *,
        can_view_sensitive: bool,
    ) -> tuple[str, str, str]:
        matter_label = matter.case_number or matter.demand_type
        if can_view_sensitive:
            return title, matter_label, client.name
        sensitive_names = (client.name,)
        return (
            redact_sensitive_metadata(title, sensitive_names) or "Tarefa protegida",
            redact_sensitive_metadata(matter_label, sensitive_names) or "Caso",
            masked_lead_label(matter.id),
        )

    async def meeting_briefing(
        self,
        workspace_id: UUID,
        meeting_id: UUID,
        *,
        can_view_sensitive: bool,
    ) -> MeetingBriefingRead:
        meeting = await MeetingService(self.db).get_for_workspace(workspace_id, meeting_id)
        return await self._build_meeting_briefing(
            meeting,
            can_view_sensitive=can_view_sensitive,
        )

    async def daily_briefing(
        self,
        workspace_id: UUID,
        day: date,
        *,
        can_view_sensitive: bool,
    ) -> DailyBriefingRead:
        settings = await self.get_settings(workspace_id)
        zone = ZoneInfo(settings.timezone)
        start_utc = datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)
        end_utc = (datetime.combine(day, time.min, tzinfo=zone) + timedelta(days=1)).astimezone(UTC)
        meetings = list(
            (
                await self.db.scalars(
                    select(Meeting)
                    .options(
                        selectinload(Meeting.matter).selectinload(Matter.client),
                    )
                    .execution_options(populate_existing=True)
                    .where(
                        Meeting.workspace_id == workspace_id,
                        Meeting.starts_at >= start_utc,
                        Meeting.starts_at < end_utc,
                        Meeting.status != MeetingStatus.cancelled,
                    )
                    .order_by(Meeting.starts_at.asc())
                )
            ).all()
        )
        matter_ids = [meeting.matter_id for meeting in meetings if meeting.matter_id]
        insights = (
            {
                item.matter_id: item
                for item in (
                    await self.db.scalars(
                        select(ConversationInsight).where(
                            ConversationInsight.matter_id.in_(matter_ids)
                        )
                    )
                ).all()
            }
            if matter_ids
            else {}
        )
        items: list[DailyBriefingItemRead] = []
        priorities: list[str] = []
        for meeting in meetings:
            matter = meeting.matter
            insight = insights.get(meeting.matter_id)
            warnings: list[str] = []
            if meeting.matter_id is None:
                warnings.append("Conectar este agendamento a um card antes da reunião.")
            if not meeting.meeting_url:
                warnings.append("Inserir o link do Meet ou Teams.")
            if insight and insight.no_show_risk == "high":
                warnings.append("Risco alto de ausência; confirmar presença.")
            if matter and matter.medical_urgency_hours and matter.medical_urgency_hours <= 24:
                warnings.append("Urgência médica de até 24h.")
            priorities.extend(warnings)
            client_name = (
                matter.client.name
                if can_view_sensitive and matter and matter.client
                else "Agendamento sem card"
                if matter is None
                else "Dados protegidos"
            )
            readiness = "pronto"
            if meeting.matter_id is None:
                readiness = "vincular card"
            elif not meeting.meeting_url:
                readiness = "adicionar sala"
            elif warnings:
                readiness = "revisar"
            items.append(
                DailyBriefingItemRead(
                    meeting_id=meeting.id,
                    starts_at=meeting.starts_at,
                    title=meeting.title,
                    client_name=client_name,
                    matter_id=meeting.matter_id,
                    demand_type=matter.demand_type if can_view_sensitive and matter else None,
                    no_show_risk=insight.no_show_risk if can_view_sensitive and insight else None,
                    readiness=readiness,
                    meeting_url=meeting.meeting_url,
                    warnings=warnings,
                )
            )
        orphan_count = sum(1 for meeting in meetings if meeting.matter_id is None)
        missing_room_count = sum(1 for meeting in meetings if not meeting.meeting_url)
        summary = (
            f"{len(meetings)} reunião(ões) no dia, "
            f"{orphan_count} sem card e {missing_room_count} sem sala on-line."
            if meetings
            else "Nenhuma reunião agendada para este dia."
        )
        return DailyBriefingRead(
            day=day,
            timezone=settings.timezone,
            total=len(meetings),
            linked_count=len(meetings) - orphan_count,
            orphan_count=orphan_count,
            missing_room_count=missing_room_count,
            summary=summary,
            priorities=list(dict.fromkeys(priorities)),
            items=items,
            generated_at=datetime.now(UTC),
        )

    async def _build_meeting_briefing(
        self,
        meeting: Meeting,
        *,
        can_view_sensitive: bool,
    ) -> MeetingBriefingRead:
        generated_at = datetime.now(UTC)
        if meeting.matter_id is None or meeting.matter is None:
            return MeetingBriefingRead(
                meeting_id=meeting.id,
                linked_to_matter=False,
                client_name=self._recipient_name(meeting)
                if can_view_sensitive
                else "Dados protegidos",
                summary=(
                    "Agendamento ainda não conectado a um card. Vincule o lead para carregar "
                    "o contexto da reunião."
                ),
                warnings=[
                    "Agendamento órfão: selecione um card existente.",
                    *(
                        []
                        if meeting.meeting_url
                        else ["Link do Meet ou Teams ainda não informado."]
                    ),
                ],
                suggested_questions=[
                    "Qual é o principal problema que você precisa resolver?",
                    "Quais documentos e comunicações você já possui?",
                ],
                meeting_url=meeting.meeting_url,
                room_provider=self._room_provider(meeting.meeting_url, meeting.location),
                generated_at=generated_at,
            )

        matter = meeting.matter
        insight = await self.db.scalar(
            select(ConversationInsight).where(
                ConversationInsight.workspace_id == meeting.workspace_id,
                ConversationInsight.matter_id == matter.id,
            )
        )
        card_score = await self.db.scalar(select(CardScore).where(CardScore.matter_id == matter.id))
        documents = list(
            (await self.db.scalars(select(Document).where(Document.matter_id == matter.id))).all()
        )
        ready_documents = sum(1 for item in documents if item.status == DocumentStatus.completo)
        if not can_view_sensitive:
            return MeetingBriefingRead(
                meeting_id=meeting.id,
                matter_id=matter.id,
                linked_to_matter=True,
                client_name="Dados protegidos",
                summary="O contexto deste card está oculto pelas permissões do usuário.",
                warnings=[]
                if meeting.meeting_url
                else ["Link do Meet ou Teams ainda não informado."],
                meeting_url=meeting.meeting_url,
                room_provider=self._room_provider(meeting.meeting_url, meeting.location),
                generated_at=generated_at,
            )

        summary = (
            insight.summary
            if insight and insight.summary
            else matter.executive_summary
            or (
                "Contexto ainda incompleto; confirme fatos, urgência, documentos e expectativa "
                "do cliente."
            )
        )
        warnings: list[str] = []
        insights: list[str] = []
        questions = [
            "Qual resultado precisa ser garantido com maior urgência?",
            "O plano apresentou a negativa por escrito e com qual justificativa?",
            "Há prescrição e relatório médico atualizados?",
        ]
        if not meeting.meeting_url:
            warnings.append("Link do Meet ou Teams ainda não informado.")
        if matter.medical_urgency_hours and matter.medical_urgency_hours <= 24:
            warnings.append(f"Urgência médica registrada em {matter.medical_urgency_hours}h.")
            insights.append("Priorizar linha do tempo clínica e risco da demora.")
        if insight:
            if insight.no_show_risk == "high":
                warnings.append("Risco alto de ausência; confirmar presença pelo WhatsApp.")
            if insight.objections:
                insights.append(
                    "Objeções registradas: "
                    + "; ".join(str(item) for item in insight.objections[:3])
                )
            if insight.missing_information:
                insights.append(
                    "Informações pendentes: "
                    + "; ".join(str(item) for item in insight.missing_information[:3])
                )
        if ready_documents < len(documents):
            insights.append(f"Documentos prontos: {ready_documents} de {len(documents)} enviados.")
        if matter.health_object_name:
            questions.insert(
                0, f"Confirme indicação, dose e urgência de {matter.health_object_name}."
            )
        if card_score and card_score.explanation:
            insights.append(card_score.explanation)
        client = matter.client
        contact = client.phone or client.email if client else None
        return MeetingBriefingRead(
            meeting_id=meeting.id,
            matter_id=matter.id,
            linked_to_matter=True,
            client_name=client.name if client else "Cliente sem nome",
            contact=contact,
            demand_type=matter.demand_type,
            health_subtheme=matter.health_subtheme.value if matter.health_subtheme else None,
            health_object_name=matter.health_object_name,
            health_insurer=matter.health_insurer,
            medical_urgency_hours=matter.medical_urgency_hours,
            lead_score=matter.lead_score,
            document_count=len(documents),
            ready_document_count=ready_documents,
            no_show_risk=insight.no_show_risk if insight else None,
            summary=summary,
            insights=insights,
            suggested_questions=questions,
            warnings=warnings,
            next_action=(insight.recommended_action if insight else None) or matter.next_action,
            meeting_url=meeting.meeting_url,
            room_provider=self._room_provider(meeting.meeting_url, meeting.location),
            generated_at=generated_at,
        )

    async def _resolve_public_link(
        self,
        token: str,
        *,
        for_update: bool = False,
    ) -> tuple[BookingLink, Workspace]:
        claims = verify_booking_token(token)
        if claims is None:
            raise self._invalid_link()
        workspace_id = claims["workspace_id"]
        await set_workspace_context(self.db, workspace_id)
        query = select(BookingLink).where(
            BookingLink.id == claims["link_id"],
            BookingLink.workspace_id == workspace_id,
            BookingLink.public_key == claims["public_key"],
        )
        if for_update:
            query = query.with_for_update()
        link = await self.db.scalar(query)
        workspace = await self.db.get(Workspace, workspace_id)
        if link is None or workspace is None or workspace.deleted_at is not None:
            raise self._invalid_link()
        now = datetime.now(UTC)
        if (
            not link.is_active
            or self._as_utc(link.expires_at) <= now
            or (link.max_uses is not None and link.use_count >= link.max_uses)
        ):
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail={"code": "BOOK-002", "message": "Este link expirou ou já foi utilizado."},
            )
        return link, workspace

    async def _get_workspace(self, workspace_id: UUID, *, for_update: bool = False) -> Workspace:
        query = select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.deleted_at.is_(None),
            Workspace.is_active.is_(True),
        )
        if for_update:
            query = query.with_for_update()
        workspace = await self.db.scalar(query)
        if workspace is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "AGENDA-001", "message": "Workspace da agenda não encontrado."},
            )
        return workspace

    async def _get_matter(self, workspace_id: UUID, matter_id: UUID) -> Matter:
        matter = await self.db.scalar(
            select(Matter)
            .options(selectinload(Matter.client))
            .where(
                Matter.id == matter_id,
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
            )
        )
        if matter is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "BOOK-005",
                    "message": "O card selecionado não pertence a este workspace.",
                },
            )
        return matter

    async def _get_internal_link(
        self,
        workspace_id: UUID,
        link_id: UUID,
        *,
        for_update: bool = False,
    ) -> BookingLink:
        query = select(BookingLink).where(
            BookingLink.workspace_id == workspace_id,
            BookingLink.id == link_id,
        )
        if for_update:
            query = query.with_for_update()
        link = await self.db.scalar(query)
        if link is None:
            raise self._invalid_link()
        return link

    def _settings_from_workspace(self, workspace: Workspace) -> CalendarSettingsRead:
        default = deepcopy(build_default_workspace_settings()["calendar"])
        raw = deepcopy((workspace.settings or {}).get("calendar") or {})
        booking_page = deepcopy(default["bookingPage"])
        booking_page.update(raw.get("bookingPage") or raw.get("booking_page") or {})
        default.update(raw)
        default["bookingPage"] = booking_page
        return CalendarSettingsRead.model_validate(default)

    @staticmethod
    def _connection_status(
        provider: CalendarProvider,
        meeting_url: str | None,
        *,
        previous: CalendarConnectionStatus,
    ) -> CalendarConnectionStatus:
        if provider == CalendarProvider.manual:
            return (
                CalendarConnectionStatus.manual_ready
                if meeting_url
                else CalendarConnectionStatus.offline
            )
        if previous == CalendarConnectionStatus.connected:
            return CalendarConnectionStatus.connected
        return CalendarConnectionStatus.awaiting_api

    def _booking_link_read(
        self,
        link: BookingLink,
        *,
        matter_label: str | None = None,
    ) -> BookingLinkRead:
        token = create_booking_token(
            link.id,
            workspace_id=link.workspace_id,
            public_key=link.public_key,
            expires_at=link.expires_at,
        )
        return BookingLinkRead(
            id=link.id,
            matter_id=link.matter_id,
            responsible_user_id=link.responsible_user_id,
            matter_label=matter_label,
            title=link.title,
            description=link.description,
            duration_minutes=link.duration_minutes,
            meeting_provider=CalendarProvider(link.meeting_provider),
            expires_at=link.expires_at,
            max_uses=link.max_uses,
            use_count=link.use_count,
            is_active=link.is_active,
            booking_path=f"/agendar/{token}",
            created_at=link.created_at,
        )

    @staticmethod
    def _local_datetime(day: date, value: str, zone: ZoneInfo) -> datetime:
        hour, minute = (int(part) for part in value.split(":"))
        if hour == 24:
            return datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
        return datetime.combine(day, time(hour=hour, minute=minute), tzinfo=zone)

    @classmethod
    def _has_collision(
        cls,
        starts_at: datetime,
        ends_at: datetime,
        meetings: list[Meeting],
        *,
        before: timedelta,
        after: timedelta,
        default_duration: timedelta,
    ) -> bool:
        candidate_start = starts_at - before
        candidate_end = ends_at + after
        for meeting in meetings:
            existing_start = cls._as_utc(meeting.starts_at)
            existing_end = (
                cls._as_utc(meeting.ends_at)
                if meeting.ends_at
                else existing_start + default_duration
            )
            if candidate_start < existing_end + after and candidate_end > existing_start - before:
                return True
        return False

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _provider_label(provider: CalendarProvider) -> str:
        return {
            CalendarProvider.google_meet: "Google Meet",
            CalendarProvider.microsoft_teams: "Microsoft Teams",
            CalendarProvider.manual: "Reunião online",
        }[provider]

    @staticmethod
    def _room_provider(meeting_url: str | None, location: str | None) -> str | None:
        if meeting_url:
            hostname = (urlsplit(meeting_url).hostname or "").lower()
            if "meet.google" in hostname:
                return "Google Meet"
            if "teams.microsoft" in hostname or "teams.live" in hostname:
                return "Microsoft Teams"
            return hostname or "Sala on-line"
        return location

    @staticmethod
    def _recipient_name(meeting: Meeting) -> str:
        recipients = meeting.recipient_overrides or []
        if recipients and isinstance(recipients[0], dict):
            return str(recipients[0].get("name") or "Lead sem nome")
        return "Lead sem nome"

    @staticmethod
    def _invalid_link() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "BOOK-001", "message": "Link de agendamento inválido."},
        )


__all__ = ["AgendaService"]
