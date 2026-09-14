"""Configuração, booking público e briefings da Agenda KLM."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.entitlement_errors import (
    enforce_workspace_feature_or_http,
    entitlement_http_exception,
)
from app.core.deps import (
    AuthContext,
    can_view_sensitive_lead_data,
    get_current_permissions,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.core.rbac import UNRESTRICTED_PERMISSION_SCOPES, Permission
from app.models.entitlement import EntitlementKey
from app.models.meeting import MeetingNature
from app.models.user import User
from app.schemas.agenda import (
    AgendaUnifiedEventRead,
    AvailabilityPreviewRead,
    BookingLinkCreate,
    BookingLinkRead,
    CalendarProvider,
    CalendarSettingsRead,
    CalendarSettingsUpdate,
    DailyBriefingRead,
    InternalBookingCreate,
    MeetingBriefingRead,
    PublicBookingConfirmationRead,
    PublicBookingCreate,
    PublicBookingPageRead,
)
from app.schemas.control_plane import MeetingRead
from app.services.agenda_service import AgendaService
from app.services.entitlement_service import EntitlementError

router = APIRouter(prefix="/agenda", tags=["Agenda"])


def _can_read_agenda_source(
    auth_context: AuthContext,
    permissions: list[Permission],
    *,
    module: str,
    support_scope: str,
) -> bool:
    """Resolve a autorização da fonte sem transformar a agenda num bypass.

    A rota-base continua exigindo ``lista:view``. Cada projeção agregada exige
    também a permissão do seu domínio; sessões administrativas usam apenas o
    escopo explícito correspondente do grant temporário.
    """

    if auth_context.is_support_access:
        return support_scope in auth_context.support_scopes
    return any(
        permission.module == module
        and permission.action == "view"
        and permission.scope in UNRESTRICTED_PERMISSION_SCOPES
        for permission in permissions
    )


async def _actor_label(db: AsyncSession, auth_context: AuthContext) -> str:
    actor = await db.get(User, auth_context.user_id)
    return actor.name if actor else "Usuário da agenda"


@router.get(
    "",
    response_model=list[AgendaUnifiedEventRead],
    summary="Agenda unificada — comercial + jurídico numa única lista",
)
async def get_unified_agenda(
    natureza: str = Query(default="todos"),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth_context: AuthContext = Depends(require_permission("lista", "view")),
    permissions: list[Permission] = Depends(get_current_permissions),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> list[AgendaUnifiedEventRead]:
    parsed_natureza: MeetingNature | None = None
    if natureza not in ("todos", ""):
        try:
            parsed_natureza = MeetingNature(natureza)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "AGENDA-002",
                    "message": "natureza deve ser 'comercial', 'juridico' ou 'todos'.",
                },
            ) from None
    return await AgendaService(db).unified_agenda(
        workspace_id,
        include_tasks=_can_read_agenda_source(
            auth_context,
            permissions,
            module="tasks",
            support_scope="tasks:read",
        ),
        include_process_deadlines=_can_read_agenda_source(
            auth_context,
            permissions,
            module="processos",
            support_scope="matters:read",
        ),
        can_view_sensitive=can_view_sensitive,
        natureza=parsed_natureza,
        start=start,
        end=end,
    )


@router.get("/settings", response_model=CalendarSettingsRead)
async def get_calendar_settings(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("lista", "view")),
    db: AsyncSession = Depends(get_db),
) -> CalendarSettingsRead:
    return await AgendaService(db).get_settings(workspace_id)


@router.put("/settings", response_model=CalendarSettingsRead)
async def update_calendar_settings(
    payload: CalendarSettingsUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth_context: AuthContext = Depends(require_permission("configuracoes", "edit")),
    db: AsyncSession = Depends(get_db),
) -> CalendarSettingsRead:
    # ``calendar`` is the commercial capability for an external calendar
    # connection/synchronisation.  Availability, manual scheduling and the
    # internal agenda are core product operations and must not depend on it.
    if payload.provider is not CalendarProvider.manual:
        await enforce_workspace_feature_or_http(db, workspace_id, EntitlementKey.calendar)
    result = await AgendaService(db).update_settings(
        workspace_id,
        payload,
        actor_user_id=auth_context.user_id,
        actor_label=await _actor_label(db, auth_context),
    )
    await db.commit()
    return result


@router.get("/booking-links", response_model=list[BookingLinkRead])
async def list_booking_links(
    matter_id: UUID | None = Query(default=None),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("kanban", "edit")),
    db: AsyncSession = Depends(get_db),
) -> list[BookingLinkRead]:
    return await AgendaService(db).list_booking_links(workspace_id, matter_id=matter_id)


@router.get("/availability", response_model=AvailabilityPreviewRead)
async def preview_availability(
    duration_minutes: int = Query(default=45, ge=15, le=240),
    responsible_user_id: UUID | None = Query(default=None),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("lista", "view")),
    db: AsyncSession = Depends(get_db),
) -> AvailabilityPreviewRead:
    return await AgendaService(db).preview_availability(
        workspace_id,
        duration_minutes=duration_minutes,
        responsible_user_id=responsible_user_id,
    )


@router.post(
    "/internal-bookings",
    response_model=MeetingRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_internal_booking(
    payload: InternalBookingCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth_context: AuthContext = Depends(require_permission("kanban", "edit")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> MeetingRead:
    result = await AgendaService(db).create_internal_booking(
        workspace_id,
        payload,
        actor_user_id=auth_context.user_id,
        actor_label=await _actor_label(db, auth_context),
    )
    await db.commit()
    response = MeetingRead.model_validate(result)
    if can_view_sensitive:
        return response
    from app.core.lead_privacy import mask_meeting

    return mask_meeting(response)


@router.post(
    "/booking-links",
    response_model=BookingLinkRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_booking_link(
    payload: BookingLinkCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth_context: AuthContext = Depends(require_permission("kanban", "edit")),
    db: AsyncSession = Depends(get_db),
) -> BookingLinkRead:
    result = await AgendaService(db).create_booking_link(
        workspace_id,
        payload,
        actor_user_id=auth_context.user_id,
        actor_label=await _actor_label(db, auth_context),
    )
    await db.commit()
    return result


@router.post("/booking-links/{link_id}/revoke", response_model=BookingLinkRead)
async def revoke_booking_link(
    link_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth_context: AuthContext = Depends(require_permission("kanban", "edit")),
    db: AsyncSession = Depends(get_db),
) -> BookingLinkRead:
    result = await AgendaService(db).revoke_booking_link(
        workspace_id,
        link_id,
        actor_user_id=auth_context.user_id,
        actor_label=await _actor_label(db, auth_context),
    )
    await db.commit()
    return result


@router.get("/meetings/{meeting_id}/briefing", response_model=MeetingBriefingRead)
async def get_meeting_briefing(
    meeting_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("lista", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> MeetingBriefingRead:
    return await AgendaService(db).meeting_briefing(
        workspace_id,
        meeting_id,
        can_view_sensitive=can_view_sensitive,
    )


@router.get("/daily-briefing", response_model=DailyBriefingRead)
async def get_daily_briefing(
    day: date = Query(default_factory=date.today),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("lista", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> DailyBriefingRead:
    return await AgendaService(db).daily_briefing(
        workspace_id,
        day,
        can_view_sensitive=can_view_sensitive,
    )


@router.get("/public/{token}", response_model=PublicBookingPageRead)
async def get_public_booking_page(
    token: str,
    db: AsyncSession = Depends(get_db),
) -> PublicBookingPageRead:
    return await AgendaService(db).get_public_booking_page(token)


@router.post(
    "/public/{token}",
    response_model=PublicBookingConfirmationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_public_booking(
    token: str,
    payload: PublicBookingCreate,
    db: AsyncSession = Depends(get_db),
) -> PublicBookingConfirmationRead:
    try:
        result = await AgendaService(db).create_public_booking(token, payload)
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    await db.commit()
    return result


__all__ = ["router"]
