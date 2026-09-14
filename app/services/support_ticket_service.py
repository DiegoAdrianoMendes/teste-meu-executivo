"""Regras de negócio e isolamento do SAC nativo."""

from __future__ import annotations

import base64
import enum
import hashlib
import json
import math
import re
import statistics
import uuid
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.platform_rbac import has_platform_permission
from app.db.post_commit import queue_realtime_event
from app.models.configuration import PlatformConfigDocument
from app.models.error_log import ErrorLog
from app.models.klm_operations import (
    TechnicalRequest,
    TechnicalRequestSeverity,
    TechnicalRequestStatus,
)
from app.models.notification import NotificationType
from app.models.platform_incident import PlatformIncident
from app.models.platform_release import PlatformRelease
from app.models.storage_object import StorageObject, StorageObjectState
from app.models.support_access import SupportAccessGrant
from app.models.support_ticket import (
    SupportDeliveryChannel,
    SupportDeliveryStatus,
    SupportMessageVisibility,
    SupportNotificationOutbox,
    SupportSavedFilter,
    SupportTicket,
    SupportTicketAttachment,
    SupportTicketCollaborator,
    SupportTicketEvent,
    SupportTicketMessage,
    SupportTicketPriority,
    SupportTicketQueue,
    SupportTicketSeverity,
    SupportTicketStatus,
    SupportTicketType,
    SupportTranscriptEmail,
)
from app.models.user import AgentType, User
from app.models.workspace import Workspace
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.schemas.storage_object import StorageUploadRequest
from app.schemas.support_ticket import (
    SupportAvailabilitySettings,
    SupportAvailabilitySettingsUpdate,
    SupportAvailabilityWeekday,
    SupportEscalationSettings,
    SupportEscalationSettingsUpdate,
    SupportMessageCreate,
    SupportRatingCreate,
    SupportTechnicalContext,
    SupportTicketCreate,
    SupportTicketUpdate,
)
from app.services.configuration_service import (
    ConfigConflictError,
    ConfigurationService,
    ConfigValidationError,
)
from app.services.local_object_storage_service import LocalObjectStorageService
from app.services.notification_service import NotificationService


class SupportTicketNotFoundError(LookupError):
    pass


class SupportTicketAccessError(PermissionError):
    pass


class SupportTicketConflictError(RuntimeError):
    pass


class SupportTicketValidationError(ValueError):
    pass


_ADMIN_ROLES = {WorkspaceRole.workspace_owner, WorkspaceRole.workspace_admin}
_OPEN_STATUSES = {
    SupportTicketStatus.new,
    SupportTicketStatus.triage,
    SupportTicketStatus.in_progress,
    SupportTicketStatus.waiting_customer,
    SupportTicketStatus.waiting_internal,
    SupportTicketStatus.validating,
}
_FINAL_STATUSES = {
    SupportTicketStatus.resolved,
    SupportTicketStatus.closed,
    SupportTicketStatus.cancelled,
}
_TRANSITIONS: dict[SupportTicketStatus, set[SupportTicketStatus]] = {
    SupportTicketStatus.new: {
        SupportTicketStatus.triage,
        SupportTicketStatus.in_progress,
        SupportTicketStatus.cancelled,
    },
    SupportTicketStatus.triage: {
        SupportTicketStatus.in_progress,
        SupportTicketStatus.waiting_customer,
        SupportTicketStatus.waiting_internal,
        SupportTicketStatus.cancelled,
    },
    SupportTicketStatus.in_progress: {
        SupportTicketStatus.waiting_customer,
        SupportTicketStatus.waiting_internal,
        SupportTicketStatus.validating,
        SupportTicketStatus.resolved,
        SupportTicketStatus.cancelled,
    },
    SupportTicketStatus.waiting_customer: {
        SupportTicketStatus.in_progress,
        SupportTicketStatus.validating,
        SupportTicketStatus.resolved,
        SupportTicketStatus.cancelled,
    },
    SupportTicketStatus.waiting_internal: {
        SupportTicketStatus.in_progress,
        SupportTicketStatus.validating,
        SupportTicketStatus.resolved,
        SupportTicketStatus.cancelled,
    },
    SupportTicketStatus.validating: {
        SupportTicketStatus.in_progress,
        SupportTicketStatus.resolved,
        SupportTicketStatus.cancelled,
    },
    SupportTicketStatus.resolved: {
        SupportTicketStatus.closed,
        SupportTicketStatus.in_progress,
    },
    SupportTicketStatus.closed: {SupportTicketStatus.in_progress},
    SupportTicketStatus.cancelled: {SupportTicketStatus.in_progress},
}
_SLA = {
    SupportTicketSeverity.low: (timedelta(hours=8), timedelta(days=5)),
    SupportTicketSeverity.medium: (timedelta(hours=4), timedelta(days=3)),
    SupportTicketSeverity.high: (timedelta(hours=1), timedelta(hours=24)),
    SupportTicketSeverity.critical: (timedelta(minutes=15), timedelta(hours=4)),
}
_LEGACY_STATUS = {
    TechnicalRequestStatus.received: SupportTicketStatus.new,
    TechnicalRequestStatus.analyzing: SupportTicketStatus.in_progress,
    TechnicalRequestStatus.patch_ready: SupportTicketStatus.validating,
    TechnicalRequestStatus.validating: SupportTicketStatus.validating,
    TechnicalRequestStatus.resolved: SupportTicketStatus.resolved,
    TechnicalRequestStatus.rejected: SupportTicketStatus.cancelled,
}
_LEGACY_SEVERITY = {
    TechnicalRequestSeverity.low: SupportTicketSeverity.low,
    TechnicalRequestSeverity.medium: SupportTicketSeverity.medium,
    TechnicalRequestSeverity.high: SupportTicketSeverity.high,
    TechnicalRequestSeverity.critical: SupportTicketSeverity.critical,
}
_SAFE_CONTEXT_KEYS = {
    "page",
    "action",
    "frontendVersion",
    "browser",
    "device",
    "viewport",
    "occurredAt",
    "realtimeStatus",
    "failedRequestIds",
}
_SUPPORT_AVAILABILITY_NAMESPACE = "support.availability"
_SUPPORT_ESCALATION_NAMESPACE = "support.escalation"
_SUPPORT_ESCALATION_TIMEZONE = "America/Sao_Paulo"
_WEEKDAYS = tuple(SupportAvailabilityWeekday)

# F15 — cópia da conversa por e-mail ao encerrar o chamado.
_TRANSCRIPT_CLOSING_STATUSES = {SupportTicketStatus.closed, SupportTicketStatus.cancelled}
_TRANSCRIPT_STATUS_LABEL_PT = {
    SupportTicketStatus.closed: "Encerrado",
    SupportTicketStatus.cancelled: "Cancelado",
}
_TRANSCRIPT_UNSATISFIED_SCORE_THRESHOLD = 2
_ESCALATION_DAY_LABELS_PT = {
    SupportAvailabilityWeekday.monday: "Seg",
    SupportAvailabilityWeekday.tuesday: "Ter",
    SupportAvailabilityWeekday.wednesday: "Qua",
    SupportAvailabilityWeekday.thursday: "Qui",
    SupportAvailabilityWeekday.friday: "Sex",
    SupportAvailabilityWeekday.saturday: "Sáb",
    SupportAvailabilityWeekday.sunday: "Dom",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _json_safe(value: Any) -> Any:
    """Normaliza valores de domínio antes de persistir em colunas JSON."""
    if isinstance(value, enum.Enum):
        return _json_safe(value.value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        normalized = _as_aware(value)
        return normalized.isoformat().replace("+00:00", "Z") if normalized else None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_safe(item) for item in value]
    return value


def _public_number(ticket_id: UUID, created_at: datetime) -> str:
    return f"SAC-{created_at:%Y%m%d}-{ticket_id.hex[:8].upper()}"


def _fingerprint(
    *,
    workspace_id: UUID,
    route: str | None,
    subject: str,
    context: dict[str, Any],
) -> str:
    failed_ids = ",".join(sorted(context.get("failedRequestIds") or []))
    material = "|".join(
        (
            str(workspace_id),
            (route or "").strip().lower(),
            re.sub(r"\s+", " ", subject.strip().lower()),
            str(context.get("frontendVersion") or ""),
            failed_ids,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _duration_summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {
            "average_seconds": 0.0,
            "median_seconds": 0.0,
            "p90_seconds": 0.0,
            "p95_seconds": 0.0,
        }
    return {
        "average_seconds": round(statistics.fmean(values), 2),
        "median_seconds": round(statistics.median(values), 2),
        "p90_seconds": round(_percentile(values, 0.90), 2),
        "p95_seconds": round(_percentile(values, 0.95), 2),
    }


class SupportTicketService:
    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def sanitize_context(context: SupportTechnicalContext | dict[str, Any]) -> dict[str, Any]:
        raw = (
            context.model_dump(mode="json", by_alias=True, exclude_none=True)
            if isinstance(context, SupportTechnicalContext)
            else dict(context)
        )
        sanitized = {
            key: _json_safe(raw[key]) for key in _SAFE_CONTEXT_KEYS if key in raw
        }
        request_ids = sanitized.get("failedRequestIds")
        if isinstance(request_ids, list):
            sanitized["failedRequestIds"] = [
                value
                for value in request_ids[:20]
                if isinstance(value, str)
                and len(value) <= 120
                and re.fullmatch(r"[A-Za-z0-9_:.\-]+", value)
            ]
        return sanitized

    @staticmethod
    def _availability_state(
        settings: SupportAvailabilitySettings,
        *,
        now: datetime | None = None,
    ) -> tuple[str, datetime | None]:
        timezone = ZoneInfo(settings.timezone)
        reference = _as_aware(now or _now()) or _now()
        local_now = reference.astimezone(timezone)
        by_day = {item.day: item for item in settings.days}
        current_day = by_day[_WEEKDAYS[local_now.weekday()]]
        current_time = local_now.strftime("%H:%M")
        online = (
            settings.enabled
            and current_day.enabled
            and current_day.start_time <= current_time < current_day.end_time
        )

        next_change: datetime | None = None
        if settings.enabled:
            candidates: list[datetime] = []
            for offset in range(8):
                target_date = local_now.date() + timedelta(days=offset)
                day = by_day[_WEEKDAYS[target_date.weekday()]]
                if not day.enabled:
                    continue
                for raw_time in (day.start_time, day.end_time):
                    candidate = datetime.combine(
                        target_date,
                        datetime.strptime(raw_time, "%H:%M").time(),
                        tzinfo=timezone,
                    )
                    if candidate > local_now:
                        candidates.append(candidate)
            if candidates:
                next_change = min(candidates).astimezone(UTC)
        return ("online" if online else "offline"), next_change

    async def availability_settings(self) -> dict[str, Any]:
        document = await self.db.scalar(
            select(PlatformConfigDocument).where(
                PlatformConfigDocument.namespace == _SUPPORT_AVAILABILITY_NAMESPACE
            )
        )
        try:
            settings = SupportAvailabilitySettings.model_validate(
                document.payload if document is not None else {}
            )
        except ValueError as error:
            raise SupportTicketValidationError(
                "A configuração persistida do SAC é inválida."
            ) from error
        current_state, next_change_at = self._availability_state(settings)
        return {
            **settings.model_dump(),
            "etag": document.etag if document is not None else None,
            "version": document.current_version if document is not None else 0,
            "updated_at": document.updated_at if document is not None else None,
            "current_state": current_state,
            "next_change_at": next_change_at,
        }

    async def update_availability_settings(
        self,
        *,
        payload: SupportAvailabilitySettingsUpdate,
        actor_user_id: UUID,
    ) -> dict[str, Any]:
        current = await self.db.scalar(
            select(PlatformConfigDocument)
            .where(
                PlatformConfigDocument.namespace == _SUPPORT_AVAILABILITY_NAMESPACE
            )
            .with_for_update()
        )
        if current is None:
            if payload.expected_etag is not None:
                raise SupportTicketConflictError(
                    "A configuração do SAC mudou. Recarregue antes de salvar."
                )
            expected_etag = None
        else:
            if payload.expected_etag != current.etag:
                raise SupportTicketConflictError(
                    "A configuração do SAC mudou. Recarregue antes de salvar."
                )
            expected_etag = current.etag

        settings = SupportAvailabilitySettings.model_validate(
            payload.model_dump(exclude={"expected_etag"})
        )
        try:
            await ConfigurationService(self.db).write_platform(
                namespace=_SUPPORT_AVAILABILITY_NAMESPACE,
                payload=settings.model_dump(mode="json", by_alias=True),
                actor_user_id=actor_user_id,
                schema_version="1.0",
                expected_etag=expected_etag,
                reason="Horário de atendimento ao vivo do SAC atualizado",
            )
        except (ConfigConflictError, IntegrityError) as error:
            raise SupportTicketConflictError(
                "A configuração do SAC mudou. Recarregue antes de salvar."
            ) from error
        except ConfigValidationError as error:
            raise SupportTicketValidationError(str(error)) from error
        return await self.availability_settings()

    async def availability_status(self) -> dict[str, Any]:
        snapshot = await self.availability_settings()
        online = snapshot["current_state"] == "online"
        return {
            "state": snapshot["current_state"],
            "message": (
                "Atendimento ao vivo disponível."
                if online
                else snapshot["offline_message"]
            ),
        }

    @staticmethod
    def _escalation_window(
        settings: SupportEscalationSettings,
        *,
        now: datetime | None = None,
    ) -> tuple[bool, datetime | None]:
        timezone = ZoneInfo(_SUPPORT_ESCALATION_TIMEZONE)
        reference = _as_aware(now or _now()) or _now()
        local_now = reference.astimezone(timezone)
        configured_days = set(settings.business_days)
        current_day = _WEEKDAYS[local_now.weekday()]
        current_time = local_now.strftime("%H:%M")
        available_now = (
            settings.enabled
            and current_day in configured_days
            and settings.business_hours_start <= current_time < settings.business_hours_end
        )

        next_window: datetime | None = None
        if settings.enabled and not available_now:
            for offset in range(8):
                target_date = local_now.date() + timedelta(days=offset)
                if _WEEKDAYS[target_date.weekday()] not in configured_days:
                    continue
                candidate = datetime.combine(
                    target_date,
                    datetime.strptime(settings.business_hours_start, "%H:%M").time(),
                    tzinfo=timezone,
                )
                if candidate > local_now:
                    next_window = candidate.astimezone(UTC)
                    break
        return available_now, next_window

    async def escalation_settings(self) -> dict[str, Any]:
        document = await self.db.scalar(
            select(PlatformConfigDocument).where(
                PlatformConfigDocument.namespace == _SUPPORT_ESCALATION_NAMESPACE
            )
        )
        try:
            settings = SupportEscalationSettings.model_validate(
                document.payload if document is not None else {}
            )
        except ValueError as error:
            raise SupportTicketValidationError(
                "A configuração persistida de escalonamento humano é inválida."
            ) from error
        return {
            **settings.model_dump(),
            "etag": document.etag if document is not None else None,
            "version": document.current_version if document is not None else 0,
            "updated_at": document.updated_at if document is not None else None,
        }

    async def update_escalation_settings(
        self,
        *,
        payload: SupportEscalationSettingsUpdate,
        actor_user_id: UUID,
    ) -> dict[str, Any]:
        current = await self.db.scalar(
            select(PlatformConfigDocument)
            .where(PlatformConfigDocument.namespace == _SUPPORT_ESCALATION_NAMESPACE)
            .with_for_update()
        )
        if current is None:
            if payload.expected_etag is not None:
                raise SupportTicketConflictError(
                    "A configuração de escalonamento humano mudou. Recarregue antes de salvar."
                )
            expected_etag = None
        else:
            if payload.expected_etag != current.etag:
                raise SupportTicketConflictError(
                    "A configuração de escalonamento humano mudou. Recarregue antes de salvar."
                )
            expected_etag = current.etag

        settings = SupportEscalationSettings.model_validate(
            payload.model_dump(exclude={"expected_etag"})
        )
        try:
            await ConfigurationService(self.db).write_platform(
                namespace=_SUPPORT_ESCALATION_NAMESPACE,
                payload=settings.model_dump(mode="json", by_alias=True),
                actor_user_id=actor_user_id,
                schema_version="1.0",
                expected_etag=expected_etag,
                reason="Configuração de escalonamento humano (Falar com um humano) atualizada",
            )
        except (ConfigConflictError, IntegrityError) as error:
            raise SupportTicketConflictError(
                "A configuração de escalonamento humano mudou. Recarregue antes de salvar."
            ) from error
        except ConfigValidationError as error:
            raise SupportTicketValidationError(str(error)) from error
        return await self.escalation_settings()

    async def escalation_status(self) -> dict[str, Any]:
        snapshot = await self.escalation_settings()
        settings = SupportEscalationSettings.model_validate(
            {
                key: value
                for key, value in snapshot.items()
                if key not in {"etag", "version", "updated_at"}
            }
        )
        if not settings.enabled:
            return {
                "available_now": False,
                "scheduling_url": None,
                "whatsapp_link": None,
                "next_window": None,
            }
        available_now, next_window = self._escalation_window(settings)
        whatsapp_link = (
            f"https://wa.me/{re.sub(r'\\D', '', settings.whatsapp_number)}"
            if settings.whatsapp_number
            else None
        )
        return {
            "available_now": available_now,
            "scheduling_url": settings.scheduling_url,
            "whatsapp_link": whatsapp_link,
            "next_window": next_window,
        }

    async def _transcript_escalation_snapshot(self) -> dict[str, str]:
        """F16 (namespace ``support.escalation``) → bloco do e-mail F15.

        Lê com default vazio e omite graciosamente cada campo ausente,
        inválido ou com o recurso desabilitado — nunca falha o encerramento
        do chamado por causa de uma configuração incompleta.
        """
        try:
            settings = await self.escalation_settings()
        except SupportTicketValidationError:
            return {}
        if not settings.get("enabled"):
            return {}
        snapshot: dict[str, str] = {}
        scheduling_url = settings.get("scheduling_url")
        if isinstance(scheduling_url, str) and scheduling_url.strip():
            snapshot["schedulingUrl"] = scheduling_url.strip()
        whatsapp_number = settings.get("whatsapp_number")
        if isinstance(whatsapp_number, str) and whatsapp_number.strip():
            snapshot["whatsappNumber"] = whatsapp_number.strip()
        business_days = settings.get("business_days") or []
        business_hours_start = settings.get("business_hours_start")
        business_hours_end = settings.get("business_hours_end")
        if business_days and business_hours_start and business_hours_end:
            days_label = ", ".join(
                _ESCALATION_DAY_LABELS_PT.get(day, str(day)) for day in business_days
            )
            snapshot["businessHours"] = f"{days_label}, {business_hours_start}–{business_hours_end}"
        return snapshot

    async def _transcript_author_kinds(
        self,
        messages: list[SupportTicketMessage],
    ) -> dict[UUID, AgentType]:
        author_ids = {
            message.author_user_id
            for message in messages
            if message.author_user_id is not None
        }
        if not author_ids:
            return {}
        rows = (
            await self.db.execute(
                select(User.id, User.agent_type).where(User.id.in_(author_ids))
            )
        ).all()
        return dict(rows)

    async def _dispatch_closure_transcript_email(self, ticket: SupportTicket) -> None:
        """F15 — enfileira a cópia integral da conversa ao encerrar o chamado.

        Idempotente por ``ticket_id`` + versão do fechamento
        (``closed:{version}``): reencerrar o mesmo fechamento não duplica o
        registro. O envio real acontece no worker assíncrono
        (``app.workers.support_transcript_tasks``), que já é fail-closed —
        aqui só persistimos o conteúdo íntegro para nunca ser perdido.
        """
        if ticket.status not in _TRANSCRIPT_CLOSING_STATUSES:
            return
        closure_event_key = f"closed:{ticket.version}"
        existing = await self.db.scalar(
            select(SupportTranscriptEmail.id).where(
                SupportTranscriptEmail.ticket_id == ticket.id,
                SupportTranscriptEmail.closure_event_key == closure_event_key,
            )
        )
        if existing is not None:
            return

        messages = list(
            (
                await self.db.scalars(
                    select(SupportTicketMessage)
                    .where(
                        SupportTicketMessage.ticket_id == ticket.id,
                        SupportTicketMessage.visibility == SupportMessageVisibility.public,
                    )
                    .order_by(SupportTicketMessage.created_at.asc())
                )
            ).all()
        )
        author_kinds = await self._transcript_author_kinds(messages)

        def _author_kind(message: SupportTicketMessage) -> str:
            if message.author_user_id is None:
                return "IA"
            return (
                "IA"
                if author_kinds.get(message.author_user_id) == AgentType.ai
                else "Humano"
            )

        transcript_messages = [
            {
                "authorLabel": message.author_label,
                "authorKind": _author_kind(message),
                "createdAt": _json_safe(message.created_at),
                "body": message.body,
            }
            for message in messages
        ]

        not_resolved = ticket.status == SupportTicketStatus.cancelled
        unsatisfied = (
            ticket.satisfaction_score is not None
            and ticket.satisfaction_score <= _TRANSCRIPT_UNSATISFIED_SCORE_THRESHOLD
        )
        escalation = (
            await self._transcript_escalation_snapshot()
            if (not_resolved or unsatisfied)
            else {}
        )
        status_label = _TRANSCRIPT_STATUS_LABEL_PT.get(ticket.status, ticket.status.value)

        self.db.add(
            SupportTranscriptEmail(
                ticket_id=ticket.id,
                workspace_id=ticket.workspace_id,
                recipient_user_id=ticket.reporter_user_id,
                closure_event_key=closure_event_key,
                final_status=ticket.status.value,
                resolved=not not_resolved,
                unsatisfied=unsatisfied,
                subject=f"Cópia do atendimento — Chamado {ticket.public_number}",
                transcript_snapshot={
                    "ticketNumber": ticket.public_number,
                    "finalStatus": ticket.status.value,
                    "finalStatusLabel": status_label,
                    "resolved": not not_resolved,
                    "unsatisfied": unsatisfied,
                    "messages": transcript_messages,
                    "escalation": escalation,
                },
                status=SupportDeliveryStatus.pending,
            )
        )
        await self.db.flush()

    async def _membership(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
    ) -> WorkspaceMembership | None:
        return await self.db.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.status == MembershipStatus.active,
            )
        )

    async def _can_access(
        self,
        ticket: SupportTicket,
        *,
        user_id: UUID,
    ) -> bool:
        if ticket.reporter_user_id == user_id:
            return True
        membership = await self._membership(
            workspace_id=ticket.workspace_id,
            user_id=user_id,
        )
        if membership is not None and membership.role in _ADMIN_ROLES:
            return True
        collaborator = await self.db.scalar(
            select(SupportTicketCollaborator.id).where(
                SupportTicketCollaborator.ticket_id == ticket.id,
                SupportTicketCollaborator.workspace_id == ticket.workspace_id,
                SupportTicketCollaborator.user_id == user_id,
            )
        )
        return collaborator is not None

    async def get_ticket(
        self,
        ticket_id: UUID,
        *,
        workspace_id: UUID | None = None,
        user_id: UUID | None = None,
        platform: bool = False,
        for_update: bool = False,
    ) -> SupportTicket:
        clauses = [SupportTicket.id == ticket_id]
        if workspace_id is not None:
            clauses.append(SupportTicket.workspace_id == workspace_id)
        query = select(SupportTicket).where(*clauses)
        if for_update:
            query = query.with_for_update()
        ticket = await self.db.scalar(query)
        if ticket is None:
            raise SupportTicketNotFoundError("Chamado não encontrado.")
        if not platform:
            if user_id is None or not await self._can_access(ticket, user_id=user_id):
                raise SupportTicketAccessError("Você não tem acesso a este chamado.")
        return ticket

    async def _default_assignee(
        self,
        queue: SupportTicketQueue,
    ) -> User | None:
        preferred = (
            ("technical_support", "platform_admin", "platform_owner")
            if queue == SupportTicketQueue.technical
            else ("support", "technical_support", "platform_admin", "platform_owner")
        )
        users = list(
            (
                await self.db.scalars(
                    select(User).where(
                        User.is_active.is_(True),
                        User.deleted_at.is_(None),
                        User.platform_access_active.is_(True),
                        User.platform_role_package.in_(preferred),
                    )
                )
            ).all()
        )
        users = [
            user
            for user in users
            if has_platform_permission(user, "support", "view")
        ]
        rank = {role: index for index, role in enumerate(preferred)}
        users.sort(key=lambda user: rank.get(user.platform_role_package or "", len(rank)))
        return users[0] if users else None

    @staticmethod
    def _queue_for(
        ticket_type: SupportTicketType,
        severity: SupportTicketSeverity,
    ) -> SupportTicketQueue:
        if (
            ticket_type == SupportTicketType.instability
            or severity == SupportTicketSeverity.critical
        ):
            return SupportTicketQueue.technical
        return SupportTicketQueue.support

    async def _find_similar(
        self,
        *,
        workspace_id: UUID,
        fingerprint: str,
    ) -> SupportTicket | None:
        cutoff = _now() - timedelta(days=7)
        return await self.db.scalar(
            select(SupportTicket)
            .where(
                SupportTicket.workspace_id == workspace_id,
                SupportTicket.error_fingerprint == fingerprint,
                SupportTicket.created_at >= cutoff,
                SupportTicket.status.in_(_OPEN_STATUSES),
            )
            .order_by(SupportTicket.created_at.desc())
            .limit(1)
        )

    async def _event(
        self,
        ticket: SupportTicket,
        *,
        actor_user_id: UUID | None,
        event_type: str,
        summary: str,
        before_data: dict[str, Any] | None = None,
        after_data: dict[str, Any] | None = None,
    ) -> SupportTicketEvent:
        event = SupportTicketEvent(
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            actor_user_id=actor_user_id,
            event_type=event_type,
            summary=summary,
            before_data=_json_safe(before_data or {}),
            after_data=_json_safe(after_data or {}),
        )
        self.db.add(event)
        await self.db.flush()
        return event

    async def _queue_delivery(
        self,
        ticket: SupportTicket,
        *,
        recipient_user_id: UUID,
        event_key: str,
        channel: SupportDeliveryChannel = SupportDeliveryChannel.email,
    ) -> None:
        existing = await self.db.scalar(
            select(SupportNotificationOutbox.id).where(
                SupportNotificationOutbox.ticket_id == ticket.id,
                SupportNotificationOutbox.recipient_user_id == recipient_user_id,
                SupportNotificationOutbox.channel == channel,
                SupportNotificationOutbox.event_key == event_key,
            )
        )
        if existing is not None:
            return
        self.db.add(
            SupportNotificationOutbox(
                ticket_id=ticket.id,
                workspace_id=ticket.workspace_id,
                recipient_user_id=recipient_user_id,
                channel=channel,
                status=(
                    SupportDeliveryStatus.disabled
                    if channel == SupportDeliveryChannel.whatsapp
                    else SupportDeliveryStatus.pending
                ),
                event_key=event_key,
                payload={
                    "ticketNumber": ticket.public_number,
                    "status": ticket.status.value,
                    "securePath": f"/app?support={ticket.id}",
                },
            )
        )
        await self.db.flush()

    async def _notify_user(
        self,
        ticket: SupportTicket,
        *,
        user_id: UUID,
        title: str,
        message: str,
        notification_type: NotificationType = NotificationType.info,
        event_key: str,
    ) -> None:
        await NotificationService(self.db).create(
            user_id=user_id,
            workspace_id=ticket.workspace_id,
            title=title,
            message=message,
            notification_type=notification_type,
        )
        await self._queue_delivery(
            ticket,
            recipient_user_id=user_id,
            event_key=event_key,
        )

    def _signal(self, ticket: SupportTicket, event_type: str) -> None:
        queue_realtime_event(
            self.db,
            workspace_id=ticket.workspace_id,
            event_type=event_type,
            data={"ticketId": str(ticket.id), "version": ticket.version},
        )

    async def process_lifecycle(
        self,
        *,
        workspace_id: UUID,
        now: datetime | None = None,
        limit: int = 100,
    ) -> dict[str, int]:
        """Aplica lembretes, encerramento automático e alertas operacionais.

        Os chamados são bloqueados durante a varredura e cada ação deixa um
        evento imutável. Assim, retries do worker não repetem notificações.
        """

        reference_now = _as_aware(now) or _now()
        tickets = list(
            (
                await self.db.scalars(
                    select(SupportTicket)
                    .where(
                        SupportTicket.workspace_id == workspace_id,
                        SupportTicket.status.in_(
                            (*_OPEN_STATUSES, SupportTicketStatus.resolved)
                        ),
                    )
                    .order_by(SupportTicket.updated_at, SupportTicket.id)
                    .with_for_update(skip_locked=True)
                    .limit(max(1, min(limit, 250)))
                )
            ).all()
        )
        if not tickets:
            return {
                "processed": 0,
                "reminded": 0,
                "auto_closed": 0,
                "alerts": 0,
            }

        event_rows = (
            await self.db.execute(
                select(SupportTicketEvent.ticket_id, SupportTicketEvent.event_type).where(
                    SupportTicketEvent.ticket_id.in_([ticket.id for ticket in tickets]),
                    SupportTicketEvent.event_type.in_(
                        {
                            "resolution_reminder_sent",
                            "auto_closed_without_customer_response",
                            "unassigned_alert",
                            "first_response_overdue_alert",
                            "resolution_overdue_alert",
                            "delivery_failure_alert",
                        }
                    ),
                )
            )
        ).all()
        recorded = {(ticket_id, event_type) for ticket_id, event_type in event_rows}
        failed_delivery_ticket_ids = set(
            (
                await self.db.scalars(
                    select(SupportNotificationOutbox.ticket_id).where(
                        SupportNotificationOutbox.workspace_id == workspace_id,
                        SupportNotificationOutbox.status == SupportDeliveryStatus.failed,
                        SupportNotificationOutbox.last_error_code.is_not(None),
                        SupportNotificationOutbox.last_error_code != "processing",
                    )
                )
            ).all()
        )
        result = {
            "processed": len(tickets),
            "reminded": 0,
            "auto_closed": 0,
            "alerts": 0,
        }
        fallback_assignees: dict[SupportTicketQueue, User | None] = {}

        async def record_once(
            ticket: SupportTicket,
            *,
            event_type: str,
            summary: str,
        ) -> bool:
            key = (ticket.id, event_type)
            if key in recorded:
                return False
            await self._event(
                ticket,
                actor_user_id=None,
                event_type=event_type,
                summary=summary,
            )
            recorded.add(key)
            return True

        async def operational_recipient(ticket: SupportTicket) -> User | None:
            if ticket.assigned_to_user_id is not None:
                return await self.db.get(User, ticket.assigned_to_user_id)
            if ticket.queue not in fallback_assignees:
                fallback_assignees[ticket.queue] = await self._default_assignee(ticket.queue)
            return fallback_assignees[ticket.queue]

        for ticket in tickets:
            resolved_at = _as_aware(ticket.resolved_at)
            if ticket.status == SupportTicketStatus.resolved and resolved_at is not None:
                if resolved_at <= reference_now - timedelta(days=7):
                    if await record_once(
                        ticket,
                        event_type="auto_closed_without_customer_response",
                        summary=(
                            "Chamado encerrado sete dias após a resolução sem retorno; "
                            "reabertura permanece disponível por trinta dias."
                        ),
                    ):
                        ticket.status = SupportTicketStatus.closed
                        ticket.closed_at = reference_now
                        ticket.version += 1
                        await self._notify_user(
                            ticket,
                            user_id=ticket.reporter_user_id,
                            title=f"Chamado {ticket.public_number} encerrado",
                            message=(
                                "O chamado foi encerrado após sete dias sem retorno. "
                                "A reabertura continua disponível por trinta dias."
                            ),
                            event_key=f"auto-close:{ticket.version}",
                        )
                        await self._dispatch_closure_transcript_email(ticket)
                        self._signal(ticket, "SUPPORT_TICKET_UPDATED")
                        result["auto_closed"] += 1
                    continue
                if resolved_at <= reference_now - timedelta(days=3):
                    if await record_once(
                        ticket,
                        event_type="resolution_reminder_sent",
                        summary="Lembrete de confirmação da solução enviado ao cliente.",
                    ):
                        await self._notify_user(
                            ticket,
                            user_id=ticket.reporter_user_id,
                            title=f"Confirme a solução do chamado {ticket.public_number}",
                            message=(
                                "A solução está aguardando sua confirmação. "
                                "Abra o aplicativo para validar ou responder."
                            ),
                            event_key="resolution-reminder:day-3",
                        )
                        self._signal(ticket, "SUPPORT_TICKET_UPDATED")
                        result["reminded"] += 1
                continue

            alert_candidates: list[tuple[str, bool, str]] = [
                (
                    "unassigned_alert",
                    ticket.assigned_to_user_id is None,
                    "Chamado aberto sem responsável individual.",
                ),
                (
                    "first_response_overdue_alert",
                    ticket.first_responded_at is None
                    and _as_aware(ticket.first_response_due_at) < reference_now,
                    "Prazo de primeira resposta vencido.",
                ),
                (
                    "resolution_overdue_alert",
                    _as_aware(ticket.resolution_due_at) < reference_now,
                    "Prazo de resolução vencido.",
                ),
                (
                    "delivery_failure_alert",
                    ticket.id in failed_delivery_ticket_ids,
                    "Falha de entrega de notificação detectada.",
                ),
            ]
            for event_type, condition, summary in alert_candidates:
                if not condition or not await record_once(
                    ticket,
                    event_type=event_type,
                    summary=summary,
                ):
                    continue
                recipient = await operational_recipient(ticket)
                if recipient is not None:
                    await self._notify_user(
                        ticket,
                        user_id=recipient.id,
                        title=f"Alerta no chamado {ticket.public_number}",
                        message=summary,
                        notification_type=(
                            NotificationType.urgent
                            if ticket.severity == SupportTicketSeverity.critical
                            else NotificationType.warning
                        ),
                        event_key=f"alert:{event_type}",
                    )
                self._signal(ticket, "SUPPORT_SLA_CHANGED")
                result["alerts"] += 1

        await self.db.flush()
        return result

    async def create_ticket(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        payload: SupportTicketCreate,
        idempotency_key: str,
        queue_override: SupportTicketQueue | None = None,
        event_actor_user_id: UUID | None = None,
    ) -> tuple[SupportTicket, bool]:
        key = idempotency_key.strip()
        if not key or len(key) > 120:
            raise SupportTicketValidationError("Idempotency-Key inválida.")
        existing = await self.db.scalar(
            select(SupportTicket).where(
                SupportTicket.workspace_id == workspace_id,
                SupportTicket.reporter_user_id == user_id,
                SupportTicket.idempotency_key == key,
            )
        )
        if existing is not None:
            return existing, False

        context = self.sanitize_context(payload.technical_context)
        route = str(context.get("page") or "").strip() or None
        fingerprint = _fingerprint(
            workspace_id=workspace_id,
            route=route,
            subject=payload.subject,
            context=context,
        )
        queue = queue_override or self._queue_for(payload.ticket_type, payload.severity)
        first_sla, resolution_sla = _SLA[payload.severity]
        created_at = _now()
        ticket_id = uuid.uuid4()
        similar = await self._find_similar(
            workspace_id=workspace_id,
            fingerprint=fingerprint,
        )
        assignee = (
            await self._default_assignee(queue)
            if payload.severity == SupportTicketSeverity.critical
            else None
        )
        ticket = SupportTicket(
            id=ticket_id,
            public_number=_public_number(ticket_id, created_at),
            workspace_id=workspace_id,
            reporter_user_id=user_id,
            assigned_to_user_id=assignee.id if assignee else None,
            ticket_type=payload.ticket_type,
            priority=payload.priority,
            severity=payload.severity,
            queue=queue,
            status=SupportTicketStatus.new,
            subject=payload.subject,
            description=payload.description,
            route=route,
            technical_context=context,
            error_fingerprint=fingerprint,
            duplicate_of_ticket_id=similar.id if similar else None,
            idempotency_key=key,
            first_response_due_at=created_at + first_sla,
            resolution_due_at=created_at + resolution_sla,
        )
        self.db.add(ticket)
        await self.db.flush()
        await self._event(
            ticket,
            actor_user_id=event_actor_user_id or user_id,
            event_type="created",
            summary=f"Chamado {ticket.public_number} aberto.",
            after_data={
                "status": ticket.status.value,
                "queue": ticket.queue.value,
                "severity": ticket.severity.value,
                "openedByPlatform": event_actor_user_id is not None,
                "similarTicketSuggested": str(similar.id) if similar else None,
            },
        )
        if (
            payload.ticket_type == SupportTicketType.instability
            and payload.severity == SupportTicketSeverity.critical
        ):
            await self._event(
                ticket,
                actor_user_id=None,
                event_type="incident_candidate_created",
                summary=(
                    "Instabilidade crítica sinalizada como candidata a incidente; "
                    "publicação depende de autorização."
                ),
            )
        if assignee is not None:
            await self._event(
                ticket,
                actor_user_id=None,
                event_type="assignment_changed",
                summary=f"Chamado crítico atribuído automaticamente a {assignee.name}.",
                after_data={"assignedToUserId": str(assignee.id)},
            )
        elif payload.severity == SupportTicketSeverity.critical:
            await self._event(
                ticket,
                actor_user_id=None,
                event_type="critical_unassigned_alert",
                summary="Chamado crítico sem responsável; alerta operacional obrigatório criado.",
            )
        await self._notify_user(
            ticket,
            user_id=user_id,
            title=f"Chamado {ticket.public_number} recebido",
            message="O chamado foi confirmado e já está na fila responsável.",
            event_key=f"created:{ticket.version}",
        )
        self._signal(ticket, "SUPPORT_TICKET_CREATED")
        return ticket, True

    async def list_for_user(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        limit: int = 30,
        cursor: tuple[datetime, UUID | None] | None = None,
        statuses: Iterable[SupportTicketStatus] | None = None,
    ) -> tuple[list[SupportTicket], str | None]:
        await self.migrate_legacy_requests(workspace_id=workspace_id)
        membership = await self._membership(workspace_id=workspace_id, user_id=user_id)
        access = [SupportTicket.reporter_user_id == user_id]
        if membership is not None and membership.role in _ADMIN_ROLES:
            access = [SupportTicket.workspace_id == workspace_id]
        else:
            collaborator_ids = select(SupportTicketCollaborator.ticket_id).where(
                SupportTicketCollaborator.workspace_id == workspace_id,
                SupportTicketCollaborator.user_id == user_id,
            )
            access.append(SupportTicket.id.in_(collaborator_ids))
        clauses = [
            SupportTicket.workspace_id == workspace_id,
            or_(*access),
        ]
        if cursor is not None:
            clauses.append(SupportTicket.created_at < cursor)
        if statuses:
            clauses.append(SupportTicket.status.in_(list(statuses)))
        items = list(
            (
                await self.db.scalars(
                    select(SupportTicket)
                    .where(*clauses)
                    .order_by(SupportTicket.created_at.desc())
                    .limit(limit + 1)
                )
            ).all()
        )
        has_more = len(items) > limit
        items = items[:limit]
        next_cursor = items[-1].created_at.isoformat() if has_more and items else None
        return items, next_cursor

    def _platform_filter_clauses(
        self,
        *,
        cursor: datetime | None = None,
        workspace_id: UUID | None = None,
        statuses: Iterable[SupportTicketStatus] | None = None,
        queues: Iterable[SupportTicketQueue] | None = None,
        severities: Iterable[SupportTicketSeverity] | None = None,
        ticket_types: Iterable[SupportTicketType] | None = None,
        assigned_to_user_id: UUID | None = None,
        overdue: bool | None = None,
        search: str | None = None,
    ) -> list[Any]:
        clauses: list[Any] = []
        if cursor is not None:
            cursor_time, cursor_id = cursor
            if cursor_id is None:
                clauses.append(SupportTicket.created_at < cursor_time)
            else:
                clauses.append(or_(
                    SupportTicket.created_at < cursor_time,
                    and_(SupportTicket.created_at == cursor_time, SupportTicket.id < cursor_id),
                ))
        if workspace_id is not None:
            clauses.append(SupportTicket.workspace_id == workspace_id)
        if statuses:
            clauses.append(SupportTicket.status.in_(list(statuses)))
        if queues:
            clauses.append(SupportTicket.queue.in_(list(queues)))
        if severities:
            clauses.append(SupportTicket.severity.in_(list(severities)))
        if ticket_types:
            clauses.append(SupportTicket.ticket_type.in_(list(ticket_types)))
        if assigned_to_user_id is not None:
            clauses.append(SupportTicket.assigned_to_user_id == assigned_to_user_id)
        if overdue is not None:
            overdue_clause = and_(
                SupportTicket.status.in_(_OPEN_STATUSES),
                SupportTicket.first_responded_at.is_(None),
                SupportTicket.first_response_due_at < _now(),
            )
            clauses.append(overdue_clause if overdue else ~overdue_clause)
        if search:
            term = f"%{search.strip()}%"
            clauses.append(
                or_(
                    SupportTicket.public_number.ilike(term),
                    SupportTicket.subject.ilike(term),
                    SupportTicket.description.ilike(term),
                    SupportTicket.error_fingerprint.ilike(term),
                )
            )
        return clauses

    async def list_platform(
        self,
        *,
        limit: int = 50,
        cursor: tuple[datetime, UUID | None] | None = None,
        workspace_id: UUID | None = None,
        statuses: Iterable[SupportTicketStatus] | None = None,
        queues: Iterable[SupportTicketQueue] | None = None,
        severities: Iterable[SupportTicketSeverity] | None = None,
        ticket_types: Iterable[SupportTicketType] | None = None,
        assigned_to_user_id: UUID | None = None,
        overdue: bool | None = None,
        search: str | None = None,
    ) -> tuple[list[SupportTicket], str | None]:
        clauses = self._platform_filter_clauses(
            cursor=cursor,
            workspace_id=workspace_id,
            statuses=statuses,
            queues=queues,
            severities=severities,
            ticket_types=ticket_types,
            assigned_to_user_id=assigned_to_user_id,
            overdue=overdue,
            search=search,
        )
        query = select(SupportTicket)
        if clauses:
            query = query.where(*clauses)
        items = list(
            (
                await self.db.scalars(
                    query.order_by(
                        SupportTicket.created_at.desc(), SupportTicket.id.desc()
                    ).limit(limit + 1)
                )
            ).all()
        )
        has_more = len(items) > limit
        items = items[:limit]
        next_cursor = None
        if has_more and items:
            last = items[-1]
            payload = json.dumps(
                {"timestamp": last.created_at.isoformat(), "id": str(last.id)},
                separators=(",", ":"),
            )
            next_cursor = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
        return items, next_cursor

    async def export_platform(
        self,
        *,
        workspace_id: UUID | None = None,
        statuses: Iterable[SupportTicketStatus] | None = None,
        queues: Iterable[SupportTicketQueue] | None = None,
        severities: Iterable[SupportTicketSeverity] | None = None,
        ticket_types: Iterable[SupportTicketType] | None = None,
        assigned_to_user_id: UUID | None = None,
        overdue: bool | None = None,
        search: str | None = None,
        max_items: int = 20_000,
    ) -> tuple[list[SupportTicket], bool]:
        clauses = self._platform_filter_clauses(
            workspace_id=workspace_id,
            statuses=statuses,
            queues=queues,
            severities=severities,
            ticket_types=ticket_types,
            assigned_to_user_id=assigned_to_user_id,
            overdue=overdue,
            search=search,
        )
        query = select(SupportTicket)
        if clauses:
            query = query.where(*clauses)
        rows = list(
            (
                await self.db.scalars(
                    query.order_by(SupportTicket.created_at.desc()).limit(max_items + 1)
                )
            ).all()
        )
        return rows[:max_items], len(rows) > max_items

    async def ticket_detail(
        self,
        ticket: SupportTicket,
        *,
        include_internal: bool,
    ) -> dict[str, Any]:
        message_query = select(SupportTicketMessage).where(
            SupportTicketMessage.ticket_id == ticket.id
        )
        if not include_internal:
            message_query = message_query.where(
                SupportTicketMessage.visibility == SupportMessageVisibility.public
            )
        messages = list(
            (
                await self.db.scalars(
                    message_query.order_by(SupportTicketMessage.created_at.asc())
                )
            ).all()
        )
        attachment_query = (
            select(SupportTicketAttachment, StorageObject)
            .join(
                StorageObject,
                StorageObject.id
                == SupportTicketAttachment.storage_object_version_id,
            )
            .where(SupportTicketAttachment.ticket_id == ticket.id)
        )
        if not include_internal:
            attachment_query = attachment_query.outerjoin(
                SupportTicketMessage,
                SupportTicketMessage.id == SupportTicketAttachment.message_id,
            ).where(
                or_(
                    SupportTicketAttachment.message_id.is_(None),
                    SupportTicketMessage.visibility
                    == SupportMessageVisibility.public,
                )
            )
        attachments = list(
            (
                await self.db.execute(
                    attachment_query.order_by(
                        SupportTicketAttachment.created_at.asc()
                    )
                )
            ).all()
        )
        events = list(
            (
                await self.db.scalars(
                    select(SupportTicketEvent)
                    .where(SupportTicketEvent.ticket_id == ticket.id)
                    .order_by(SupportTicketEvent.created_at.asc())
                )
            ).all()
        )
        collaborators = list(
            (
                await self.db.scalars(
                    select(SupportTicketCollaborator.user_id).where(
                        SupportTicketCollaborator.ticket_id == ticket.id
                    )
                )
            ).all()
        )
        user_ids = {
            value
            for value in (ticket.reporter_user_id, ticket.assigned_to_user_id)
            if value is not None
        }
        users = {
            user.id: user
            for user in (
                await self.db.scalars(select(User).where(User.id.in_(user_ids)))
            ).all()
        }
        return {
            "ticket": ticket,
            "messages": messages,
            "attachments": attachments,
            "events": events,
            "collaborators": collaborators,
            "reporter": users.get(ticket.reporter_user_id),
            "assignee": users.get(ticket.assigned_to_user_id),
        }

    async def add_message(
        self,
        ticket: SupportTicket,
        *,
        actor_user_id: UUID,
        actor_label: str,
        payload: SupportMessageCreate,
        idempotency_key: str,
        platform: bool,
    ) -> tuple[SupportTicketMessage, bool]:
        if payload.visibility == SupportMessageVisibility.internal and not platform:
            raise SupportTicketAccessError("Notas internas são restritas à equipe do SaaS.")
        key = idempotency_key.strip()
        if not key or len(key) > 120:
            raise SupportTicketValidationError("Idempotency-Key inválida.")
        existing = await self.db.scalar(
            select(SupportTicketMessage).where(
                SupportTicketMessage.ticket_id == ticket.id,
                SupportTicketMessage.author_user_id == actor_user_id,
                SupportTicketMessage.idempotency_key == key,
            )
        )
        if existing is not None:
            return existing, False
        if ticket.status in {SupportTicketStatus.closed, SupportTicketStatus.cancelled}:
            raise SupportTicketConflictError("Reabra o chamado antes de responder.")
        message = SupportTicketMessage(
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            author_user_id=actor_user_id,
            author_label=actor_label,
            visibility=payload.visibility,
            body=payload.body,
            idempotency_key=key,
        )
        self.db.add(message)
        if platform and payload.visibility == SupportMessageVisibility.public:
            if ticket.first_responded_at is None:
                ticket.first_responded_at = _now()
            if ticket.status in {
                SupportTicketStatus.new,
                SupportTicketStatus.triage,
                SupportTicketStatus.in_progress,
            }:
                ticket.status = SupportTicketStatus.waiting_customer
            ticket.version += 1
        elif not platform and ticket.status == SupportTicketStatus.waiting_customer:
            ticket.status = SupportTicketStatus.in_progress
            ticket.version += 1
        await self.db.flush()
        await self._event(
            ticket,
            actor_user_id=actor_user_id,
            event_type=(
                "internal_note_added"
                if payload.visibility == SupportMessageVisibility.internal
                else "public_message_added"
            ),
            summary=(
                "Nota interna adicionada."
                if payload.visibility == SupportMessageVisibility.internal
                else "Nova resposta pública adicionada."
            ),
        )
        if platform and payload.visibility == SupportMessageVisibility.public:
            await self._notify_user(
                ticket,
                user_id=ticket.reporter_user_id,
                title=f"Nova resposta no chamado {ticket.public_number}",
                message="A equipe respondeu ao seu chamado. Abra o aplicativo para consultar.",
                event_key=f"message:{message.id}",
            )
        self._signal(ticket, "SUPPORT_MESSAGE_ADDED")
        return message, True

    async def record_legacy_adapter_evidence(
        self,
        ticket: SupportTicket,
        *,
        actor_user_id: UUID,
        evidence: list[dict[str, Any]],
    ) -> None:
        if not evidence:
            return
        await self._event(
            ticket,
            actor_user_id=actor_user_id,
            event_type="legacy_adapter_evidence_added",
            summary="Evidência recebida pela API técnica legada e preservada no SAC.",
            after_data={"legacyEvidence": evidence},
        )
        self._signal(ticket, "SUPPORT_TICKET_UPDATED")

    async def update_ticket(
        self,
        ticket: SupportTicket,
        *,
        actor_user_id: UUID,
        payload: SupportTicketUpdate,
    ) -> SupportTicket:
        if ticket.version != payload.expected_version:
            raise SupportTicketConflictError(
                "O chamado foi alterado em outra sessão. Recarregue antes de continuar."
            )
        changes = payload.model_dump(
            exclude_unset=True,
            exclude={"expected_version"},
        )
        if "status" in changes:
            target = changes["status"]
            if target != ticket.status and target not in _TRANSITIONS[ticket.status]:
                raise SupportTicketValidationError(
                    f"Transição de {ticket.status.value} para {target.value} não permitida."
                )
        assignment_changed = (
            "assigned_to_user_id" in changes
            and changes["assigned_to_user_id"] != ticket.assigned_to_user_id
        )
        if assignment_changed:
            actor = await self.db.get(User, actor_user_id)
            if not has_platform_permission(actor, "support", "assign"):
                raise SupportTicketAccessError(
                    "A operação exige a permissão support:assign."
                )
            if changes["assigned_to_user_id"] is not None:
                assignee = await self.db.get(User, changes["assigned_to_user_id"])
                if not has_platform_permission(assignee, "support", "view"):
                    raise SupportTicketValidationError(
                        "Responsável precisa ter acesso ativo ao SAC."
                    )
        await self._validate_evidence_links(ticket, changes)
        before: dict[str, Any] = {}
        after: dict[str, Any] = {}
        for field, value in changes.items():
            current = getattr(ticket, field)
            if current == value:
                continue
            before[field] = current.value if isinstance(current, enum.Enum) else current
            after[field] = value.value if isinstance(value, enum.Enum) else value
            setattr(ticket, field, value)
        if not after:
            return ticket
        now = _now()
        if "status" in after:
            if ticket.status == SupportTicketStatus.resolved:
                ticket.resolved_at = now
            elif ticket.status == SupportTicketStatus.closed:
                ticket.closed_at = now
            elif ticket.status == SupportTicketStatus.in_progress and before.get("status") in {
                SupportTicketStatus.resolved.value,
                SupportTicketStatus.closed.value,
                SupportTicketStatus.cancelled.value,
            }:
                ticket.reopened_at = now
                ticket.resolved_at = None
                ticket.closed_at = None
        if "severity" in after:
            first_sla, resolution_sla = _SLA[ticket.severity]
            ticket.first_response_due_at = now + first_sla
            ticket.resolution_due_at = now + resolution_sla
        ticket.version += 1
        await self.db.flush()
        await self._event(
            ticket,
            actor_user_id=actor_user_id,
            event_type=(
                "assignment_changed"
                if "assigned_to_user_id" in after
                else "ticket_updated"
            ),
            summary=f"Chamado {ticket.public_number} atualizado.",
            before_data=before,
            after_data=after,
        )
        await self._notify_user(
            ticket,
            user_id=ticket.reporter_user_id,
            title=f"Chamado {ticket.public_number} atualizado",
            message=f"Situação atual: {ticket.status.value}.",
            event_key=f"updated:{ticket.version}",
        )
        if after.get("status") in {
            SupportTicketStatus.closed.value,
            SupportTicketStatus.cancelled.value,
        }:
            await self._dispatch_closure_transcript_email(ticket)
        self._signal(
            ticket,
            (
                "SUPPORT_ASSIGNMENT_CHANGED"
                if "assigned_to_user_id" in after
                else "SUPPORT_TICKET_UPDATED"
            ),
        )
        return ticket

    async def _validate_evidence_links(
        self,
        ticket: SupportTicket,
        changes: dict[str, Any],
    ) -> None:
        error_log_id = changes.get("linked_error_log_id")
        if (
            "linked_error_log_id" in changes
            and error_log_id is not None
            and error_log_id != ticket.linked_error_log_id
        ):
            exists = await self.db.scalar(
                select(ErrorLog.id).where(
                    ErrorLog.id == error_log_id,
                    ErrorLog.workspace_id == ticket.workspace_id,
                )
            )
            if exists is None:
                raise SupportTicketValidationError(
                    "Log de erro vinculado não existe neste workspace."
                )

        support_access_id = changes.get("support_access_request_id")
        if (
            "support_access_request_id" in changes
            and support_access_id is not None
            and support_access_id != ticket.support_access_request_id
        ):
            exists = await self.db.scalar(
                select(SupportAccessGrant.id).where(
                    SupportAccessGrant.id == support_access_id,
                    SupportAccessGrant.workspace_id == ticket.workspace_id,
                )
            )
            if exists is None:
                raise SupportTicketValidationError(
                    "Solicitação de acesso vinculada não existe neste workspace."
                )

        incident_id = changes.get("linked_incident_id")
        if (
            "linked_incident_id" in changes
            and incident_id is not None
            and incident_id != ticket.linked_incident_id
        ):
            exists = await self.db.scalar(
                select(PlatformIncident.id).where(PlatformIncident.id == incident_id)
            )
            if exists is None:
                raise SupportTicketValidationError(
                    "Incidente vinculado não existe na plataforma."
                )

        release_id = changes.get("linked_release_id")
        if "linked_release_id" in changes and release_id != ticket.linked_release_id:
            if ticket.linked_release_id is not None:
                current_exists = await self.db.scalar(
                    select(PlatformRelease.id).where(
                        PlatformRelease.id == ticket.linked_release_id
                    )
                )
                if current_exists is None:
                    raise SupportTicketValidationError(
                        "O vínculo legado de release precisa ser reconciliado "
                        "antes de ser alterado."
                    )
            if release_id is not None:
                exists = await self.db.scalar(
                    select(PlatformRelease.id).where(PlatformRelease.id == release_id)
                )
                if exists is None:
                    raise SupportTicketValidationError(
                        "Release vinculada não existe na plataforma."
                    )

    async def close_by_customer(
        self,
        ticket: SupportTicket,
        *,
        actor_user_id: UUID,
    ) -> SupportTicket:
        if ticket.status not in {SupportTicketStatus.resolved, SupportTicketStatus.validating}:
            raise SupportTicketConflictError(
                "O chamado precisa estar resolvido ou em validação para ser encerrado."
            )
        ticket.status = SupportTicketStatus.closed
        ticket.closed_at = _now()
        ticket.version += 1
        await self.db.flush()
        await self._event(
            ticket,
            actor_user_id=actor_user_id,
            event_type="closed_by_customer",
            summary="Solução confirmada pelo cliente.",
        )
        await self._dispatch_closure_transcript_email(ticket)
        self._signal(ticket, "SUPPORT_TICKET_UPDATED")
        return ticket

    async def reopen(
        self,
        ticket: SupportTicket,
        *,
        actor_user_id: UUID,
    ) -> SupportTicket:
        reference = _as_aware(ticket.closed_at or ticket.resolved_at)
        if ticket.status not in {
            SupportTicketStatus.resolved,
            SupportTicketStatus.closed,
            SupportTicketStatus.cancelled,
        }:
            raise SupportTicketConflictError("Este chamado já está aberto.")
        if reference is not None and _now() - reference > timedelta(days=30):
            raise SupportTicketConflictError(
                "O prazo de trinta dias para reabertura terminou. Abra um novo chamado."
            )
        ticket.status = SupportTicketStatus.in_progress
        ticket.reopened_at = _now()
        ticket.resolved_at = None
        ticket.closed_at = None
        ticket.version += 1
        await self.db.flush()
        await self._event(
            ticket,
            actor_user_id=actor_user_id,
            event_type="reopened",
            summary="Chamado reaberto.",
        )
        self._signal(ticket, "SUPPORT_TICKET_UPDATED")
        return ticket

    async def rate(
        self,
        ticket: SupportTicket,
        *,
        actor_user_id: UUID,
        payload: SupportRatingCreate,
    ) -> SupportTicket:
        if ticket.reporter_user_id != actor_user_id:
            raise SupportTicketAccessError("Somente o autor pode avaliar o atendimento.")
        if ticket.status not in {SupportTicketStatus.resolved, SupportTicketStatus.closed}:
            raise SupportTicketConflictError("Avaliação disponível após a resolução.")
        ticket.satisfaction_score = payload.score
        ticket.satisfaction_comment = payload.comment
        ticket.version += 1
        await self.db.flush()
        await self._event(
            ticket,
            actor_user_id=actor_user_id,
            event_type="rated",
            summary=f"Atendimento avaliado com {payload.score} de 5.",
            after_data={"score": payload.score},
        )
        self._signal(ticket, "SUPPORT_TICKET_UPDATED")
        return ticket

    async def add_collaborator(
        self,
        ticket: SupportTicket,
        *,
        actor_user_id: UUID,
        collaborator_user_id: UUID,
    ) -> SupportTicketCollaborator:
        membership = await self._membership(
            workspace_id=ticket.workspace_id,
            user_id=collaborator_user_id,
        )
        if membership is None:
            raise SupportTicketValidationError(
                "Colaborador precisa pertencer à mesma conta."
            )
        existing = await self.db.scalar(
            select(SupportTicketCollaborator).where(
                SupportTicketCollaborator.ticket_id == ticket.id,
                SupportTicketCollaborator.user_id == collaborator_user_id,
            )
        )
        if existing is not None:
            return existing
        collaborator = SupportTicketCollaborator(
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            user_id=collaborator_user_id,
            invited_by_user_id=actor_user_id,
        )
        self.db.add(collaborator)
        await self.db.flush()
        await self._event(
            ticket,
            actor_user_id=actor_user_id,
            event_type="collaborator_added",
            summary="Colaborador convidado para acompanhar o chamado.",
            after_data={"userId": str(collaborator_user_id)},
        )
        self._signal(ticket, "SUPPORT_TICKET_UPDATED")
        return collaborator

    async def authorize_attachment(
        self,
        ticket: SupportTicket,
        *,
        actor_user_id: UUID,
        payload: StorageUploadRequest,
        message_id: UUID | None,
        idempotency_key: str | None = None,
    ) -> tuple[SupportTicketAttachment, Any]:
        if message_id is not None:
            message_exists = await self.db.scalar(
                select(SupportTicketMessage.id).where(
                    SupportTicketMessage.id == message_id,
                    SupportTicketMessage.ticket_id == ticket.id,
                )
            )
            if message_exists is None:
                raise SupportTicketValidationError("Mensagem vinculada não encontrada.")
        common = {
            "workspace_id": ticket.workspace_id,
            "actor_user_id": actor_user_id,
            "filename": payload.filename,
            "content_type": payload.content_type,
            "byte_size": payload.byte_size,
            "checksum_sha256": payload.checksum_sha256,
            "retention_until": payload.retention_until,
            "expires_in": payload.expires_in,
        }
        from app.core.klm_runtime import aws_external_access_allowed

        if aws_external_access_allowed():
            from app.services.private_document_storage_service import (
                PrivateDocumentStorageService,
            )

            authorization = await PrivateDocumentStorageService(
                self.db
            ).create_upload_authorization(**common, idempotency_key=idempotency_key)
        else:
            authorization = await LocalObjectStorageService(
                self.db
            ).create_upload_authorization(**common, idempotency_key=idempotency_key)
        attachment = await self.db.scalar(
            select(SupportTicketAttachment).where(
                SupportTicketAttachment.ticket_id == ticket.id,
                SupportTicketAttachment.storage_object_version_id
                == authorization.storage_object.id,
            )
        )
        if attachment is not None:
            return attachment, authorization
        attachment = SupportTicketAttachment(
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            message_id=message_id,
            storage_object_version_id=authorization.storage_object.id,
            uploaded_by_user_id=actor_user_id,
            original_filename=payload.filename,
            content_type=payload.content_type,
            byte_size=payload.byte_size,
        )
        self.db.add(attachment)
        await self.db.flush()
        await self._event(
            ticket,
            actor_user_id=actor_user_id,
            event_type="attachment_authorized",
            summary=f"Anexo '{payload.filename}' autorizado para quarentena.",
            after_data={"attachmentId": str(attachment.id)},
        )
        self._signal(ticket, "SUPPORT_TICKET_UPDATED")
        return attachment, authorization

    async def _attachment_storage(
        self,
        ticket: SupportTicket,
        *,
        attachment_id: UUID,
    ) -> tuple[SupportTicketAttachment, StorageObject]:
        row = (
            await self.db.execute(
                select(SupportTicketAttachment, StorageObject)
                .join(
                    StorageObject,
                    StorageObject.id
                    == SupportTicketAttachment.storage_object_version_id,
                )
                .where(
                    SupportTicketAttachment.id == attachment_id,
                    SupportTicketAttachment.ticket_id == ticket.id,
                    SupportTicketAttachment.workspace_id == ticket.workspace_id,
                    StorageObject.workspace_id == ticket.workspace_id,
                )
            )
        ).one_or_none()
        if row is None:
            raise SupportTicketNotFoundError("Anexo não encontrado.")
        return row[0], row[1]

    async def confirm_attachment_upload(
        self,
        ticket: SupportTicket,
        *,
        attachment_id: UUID,
        actor_user_id: UUID,
    ) -> tuple[SupportTicketAttachment, StorageObject]:
        attachment, storage = await self._attachment_storage(
            ticket,
            attachment_id=attachment_id,
        )
        from app.core.klm_runtime import aws_external_access_allowed

        was_uploaded = storage.uploaded_at is not None
        if aws_external_access_allowed():
            from app.services.private_document_storage_service import (
                PrivateDocumentStorageService,
            )

            storage = await PrivateDocumentStorageService(self.db).confirm_upload(
                workspace_id=ticket.workspace_id,
                object_id=storage.object_id,
                actor_user_id=actor_user_id,
            )
        elif storage.state is StorageObjectState.pending:
            # In local development the upload endpoint finalizes the object itself.
            # A pending state therefore proves that the PUT was never completed.
            raise SupportTicketConflictError("O upload do anexo ainda não foi concluído.")

        attachment.byte_size = storage.byte_size or attachment.byte_size
        if not was_uploaded and storage.uploaded_at is not None:
            await self._event(
                ticket,
                actor_user_id=actor_user_id,
                event_type="attachment_upload_confirmed",
                summary=f"Upload do anexo '{attachment.original_filename}' confirmado.",
                after_data={
                    "attachmentId": str(attachment.id),
                    "state": storage.state.value,
                    "avStatus": storage.av_status.value,
                },
            )
            self._signal(ticket, "SUPPORT_TICKET_UPDATED")
        await self.db.flush()
        return attachment, storage

    async def create_attachment_download_authorization(
        self,
        ticket: SupportTicket,
        *,
        attachment_id: UUID,
        actor_user_id: UUID,
        expires_in: int = 300,
    ) -> tuple[SupportTicketAttachment, Any]:
        attachment, storage = await self._attachment_storage(
            ticket,
            attachment_id=attachment_id,
        )
        from app.core.klm_runtime import aws_external_access_allowed

        if aws_external_access_allowed():
            from app.services.private_document_storage_service import (
                PrivateDocumentStorageService,
            )

            authorization = await PrivateDocumentStorageService(
                self.db
            ).create_download_authorization(
                workspace_id=ticket.workspace_id,
                object_id=storage.object_id,
                version=storage.version,
                expires_in=expires_in,
                actor_user_id=actor_user_id,
            )
        else:
            authorization = await LocalObjectStorageService(
                self.db
            ).create_download_authorization(
                workspace_id=ticket.workspace_id,
                object_id=storage.object_id,
                version=storage.version,
                expires_in=expires_in,
            )
        return attachment, authorization

    async def saved_filters(self, user_id: UUID) -> list[SupportSavedFilter]:
        return list(
            (
                await self.db.scalars(
                    select(SupportSavedFilter)
                    .where(SupportSavedFilter.owner_user_id == user_id)
                    .order_by(SupportSavedFilter.name.asc())
                )
            ).all()
        )

    async def save_filter(
        self,
        *,
        user_id: UUID,
        name: str,
        filters: dict[str, Any],
        is_default: bool,
    ) -> SupportSavedFilter:
        existing = await self.db.scalar(
            select(SupportSavedFilter).where(
                SupportSavedFilter.owner_user_id == user_id,
                func.lower(SupportSavedFilter.name) == name.strip().lower(),
            )
        )
        if is_default:
            for saved in await self.saved_filters(user_id):
                saved.is_default = False
        if existing is None:
            existing = SupportSavedFilter(
                owner_user_id=user_id,
                name=name.strip(),
                filters=filters,
                is_default=is_default,
            )
            self.db.add(existing)
        else:
            existing.filters = filters
            existing.is_default = is_default
        await self.db.flush()
        return existing

    async def metrics(self, *, period_days: int) -> dict[str, Any]:
        now = _now()
        start = now - timedelta(days=period_days)
        previous_start = start - timedelta(days=period_days)
        period_tickets = list(
            (
                await self.db.scalars(
                    select(SupportTicket).where(SupportTicket.created_at >= previous_start)
                )
            ).all()
        )
        current = [
            ticket
            for ticket in period_tickets
            if _as_aware(ticket.created_at) >= start
        ]
        previous = [
            ticket
            for ticket in period_tickets
            if previous_start <= _as_aware(ticket.created_at) < start
        ]

        def count_new(rows: list[SupportTicket]) -> dict[str, int]:
            return {
                "new": len(rows),
                "resolved": sum(
                    ticket.status in {SupportTicketStatus.resolved, SupportTicketStatus.closed}
                    for ticket in rows
                ),
                "reopened": sum(ticket.reopened_at is not None for ticket in rows),
            }

        current_counts = count_new(current)
        previous_counts = count_new(previous)
        changes: dict[str, float] = {}
        for key, value in current_counts.items():
            old = previous_counts[key]
            changes[key] = (
                round(((value - old) / old) * 100, 2)
                if old
                else (100.0 if value else 0.0)
            )
        all_open = list(
            (
                await self.db.scalars(
                    select(SupportTicket).where(
                        SupportTicket.status.in_(_OPEN_STATUSES)
                    )
                )
            ).all()
        )
        overdue = [
            ticket
            for ticket in all_open
            if ticket.first_responded_at is None
            and _as_aware(ticket.first_response_due_at) < now
        ]
        near_due = [
            ticket
            for ticket in all_open
            if ticket.first_responded_at is None
            and now <= _as_aware(ticket.first_response_due_at) <= now + timedelta(hours=1)
        ]
        response_seconds = [
            (_as_aware(ticket.first_responded_at) - _as_aware(ticket.created_at)).total_seconds()
            for ticket in current
            if ticket.first_responded_at is not None
        ]
        resolution_seconds = [
            (_as_aware(ticket.resolved_at) - _as_aware(ticket.created_at)).total_seconds()
            for ticket in current
            if ticket.resolved_at is not None
        ]
        scores = [
            ticket.satisfaction_score
            for ticket in current
            if ticket.satisfaction_score is not None
        ]
        resolved_current = [
            ticket
            for ticket in current
            if ticket.status in {SupportTicketStatus.resolved, SupportTicketStatus.closed}
        ]
        assignee_ids = {
            ticket.assigned_to_user_id
            for ticket in resolved_current
            if ticket.assigned_to_user_id is not None
        }
        assignee_names = {
            user.id: user.name
            for user in (
                await self.db.scalars(select(User).where(User.id.in_(assignee_ids)))
            ).all()
        } if assignee_ids else {}

        def distribution(values: Iterable[str]) -> list[dict[str, Any]]:
            return [
                {"label": label, "value": value}
                for label, value in Counter(values).most_common()
            ]

        age_values: list[str] = []
        for ticket in all_open:
            age = now - _as_aware(ticket.created_at)
            age_values.append(
                "Até 24h"
                if age <= timedelta(days=1)
                else "1 a 3 dias"
                if age <= timedelta(days=3)
                else "4 a 7 dias"
                if age <= timedelta(days=7)
                else "Mais de 7 dias"
            )
        instability = [
            ticket for ticket in current if ticket.ticket_type == SupportTicketType.instability
        ]
        recurrence = Counter(str(ticket.workspace_id) for ticket in current)
        workspace_names = {
            str(workspace.id): workspace.name
            for workspace in (
                await self.db.scalars(
                    select(Workspace).where(
                        Workspace.id.in_([UUID(value) for value in recurrence])
                    )
                )
            ).all()
        } if recurrence else {}
        return {
            "period_days": period_days,
            "previous_period_change_pct": changes,
            **current_counts,
            "open": len(all_open),
            "overdue": len(overdue),
            "near_due": len(near_due),
            "reopen_rate_pct": round(
                (current_counts["reopened"] / len(current)) * 100, 2
            )
            if current
            else 0.0,
            "satisfaction_average": round(statistics.fmean(scores), 2) if scores else None,
            "first_response": _duration_summary(response_seconds),
            "resolution": _duration_summary(resolution_seconds),
            "backlog_by_queue": distribution(ticket.queue.value for ticket in all_open),
            "backlog_by_severity": distribution(ticket.severity.value for ticket in all_open),
            "backlog_by_age": distribution(age_values),
            "performance_by_queue": distribution(
                ticket.queue.value for ticket in resolved_current
            ),
            "performance_by_assignee": distribution(
                assignee_names.get(ticket.assigned_to_user_id, "Sem responsável")
                for ticket in resolved_current
            ),
            "instability_by_version": distribution(
                str(ticket.technical_context.get("frontendVersion") or "não informada")
                for ticket in instability
            ),
            "instability_by_page": distribution(
                str(ticket.route or "não informada") for ticket in instability
            ),
            "instability_by_browser": distribution(
                str(ticket.technical_context.get("browser") or "não informado")
                for ticket in instability
            ),
            "suggestions_by_destination": distribution(
                ticket.queue.value
                for ticket in current
                if ticket.ticket_type == SupportTicketType.suggestion
            ),
            "recurrence_by_workspace": [
                {"label": workspace_names.get(label, label), "value": value}
                for label, value in recurrence.most_common(10)
            ],
            "related_incidents": len(
                {
                    ticket.linked_incident_id
                    for ticket in current
                    if ticket.linked_incident_id is not None
                }
            ),
        }

    async def migrate_legacy_requests(
        self,
        *,
        workspace_id: UUID | None = None,
    ) -> int:
        clauses = [
            ~TechnicalRequest.id.in_(
                select(SupportTicket.legacy_technical_request_id).where(
                    SupportTicket.legacy_technical_request_id.is_not(None)
                )
            )
        ]
        if workspace_id is not None:
            clauses.append(TechnicalRequest.workspace_id == workspace_id)
        legacy_rows = list(
            (
                await self.db.scalars(
                    select(TechnicalRequest).where(*clauses).order_by(TechnicalRequest.created_at)
                )
            ).all()
        )
        migrated = 0
        for legacy in legacy_rows:
            created_at = _as_aware(legacy.created_at) or _now()
            severity = _LEGACY_SEVERITY[legacy.severity]
            first_sla, resolution_sla = _SLA[severity]
            context = self.sanitize_context(
                {
                    "page": legacy.route,
                    "action": "Solicitação técnica migrada",
                    "failedRequestIds": [],
                }
            )
            ticket = SupportTicket(
                id=legacy.id,
                public_number=_public_number(legacy.id, created_at),
                workspace_id=legacy.workspace_id,
                reporter_user_id=legacy.created_by_user_id,
                assigned_to_user_id=legacy.assigned_to_user_id,
                ticket_type=SupportTicketType.problem,
                priority=SupportTicketPriority.normal,
                severity=severity,
                queue=SupportTicketQueue.technical,
                status=_LEGACY_STATUS[legacy.status],
                subject=legacy.title,
                description=legacy.description,
                route=legacy.route,
                technical_context=context,
                error_fingerprint=_fingerprint(
                    workspace_id=legacy.workspace_id,
                    route=legacy.route,
                    subject=legacy.title,
                    context=context,
                ),
                legacy_technical_request_id=legacy.id,
                idempotency_key=f"legacy:{legacy.id}",
                first_response_due_at=created_at + first_sla,
                resolution_due_at=created_at + resolution_sla,
                resolved_at=(
                    _as_aware(legacy.updated_at)
                    if legacy.status == TechnicalRequestStatus.resolved
                    else None
                ),
                created_at=legacy.created_at,
                updated_at=legacy.updated_at,
            )
            self.db.add(ticket)
            await self.db.flush()
            await self._event(
                ticket,
                actor_user_id=legacy.created_by_user_id,
                event_type="legacy_technical_request_migrated",
                summary="Solicitação técnica incorporada ao núcleo único de SAC.",
                after_data={
                    "legacyTechnicalRequestId": str(legacy.id),
                    "legacyEvidence": legacy.evidence,
                    "legacyResolution": legacy.resolution,
                },
            )
            migrated += 1
        return migrated
