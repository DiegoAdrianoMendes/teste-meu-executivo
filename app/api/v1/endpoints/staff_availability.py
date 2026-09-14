"""Tenant-scoped API for proposal-based staff availability."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    AuthContext,
    get_current_auth_context,
    get_current_workspace_id,
    get_db,
)
from app.models.staff_availability import AvailabilityRequestStatus
from app.schemas.staff_availability import (
    EffectiveStaffAvailabilityRead,
    ResponsibilityResolutionRead,
    StaffAvailabilityAuditEventRead,
    StaffAvailabilityChangeRequestCreate,
    StaffAvailabilityChangeRequestRead,
    StaffAvailabilityDecisionWrite,
    StaffAvailabilityStateRead,
)
from app.services.staff_availability_service import (
    AvailabilityAuthorizationError,
    AvailabilityConflictError,
    AvailabilityDomainError,
    AvailabilityNotFoundError,
    AvailabilityValidationError,
    StaffAvailabilityService,
)

router = APIRouter(
    prefix="/staff-availability",
    tags=["Disponibilidade da equipe"],
)


def _raise_http(exc: AvailabilityDomainError) -> None:
    if isinstance(exc, AvailabilityAuthorizationError):
        code = "STAFF-AVAILABILITY-FORBIDDEN"
        status_code = status.HTTP_403_FORBIDDEN
    elif isinstance(exc, AvailabilityNotFoundError):
        code = "STAFF-AVAILABILITY-NOT-FOUND"
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, AvailabilityConflictError):
        code = "STAFF-AVAILABILITY-CONFLICT"
        status_code = status.HTTP_409_CONFLICT
    elif isinstance(exc, AvailabilityValidationError):
        code = "STAFF-AVAILABILITY-INVALID"
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    else:  # pragma: no cover - all public domain errors are mapped above
        code = "STAFF-AVAILABILITY-ERROR"
        status_code = status.HTTP_400_BAD_REQUEST
    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": str(exc)},
    ) from exc


@router.post(
    "/requests",
    response_model=StaffAvailabilityChangeRequestRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submeter alteração pendente de disponibilidade",
)
async def submit_availability_change(
    payload: StaffAvailabilityChangeRequestCreate,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=180,
    ),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await StaffAvailabilityService(db).submit_change(
            workspace_id=workspace_id,
            actor=context,
            payload=payload,
            idempotency_key=idempotency_key,
        )
    except AvailabilityDomainError as exc:
        _raise_http(exc)


@router.get(
    "/requests",
    response_model=list[StaffAvailabilityChangeRequestRead],
    summary="Listar solicitações visíveis de disponibilidade",
)
async def list_availability_requests(
    request_status: AvailabilityRequestStatus | None = Query(
        default=None,
        alias="status",
    ),
    target_user_id: UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await StaffAvailabilityService(db).list_requests(
            workspace_id=workspace_id,
            actor=context,
            request_status=request_status,
            target_user_id=target_user_id,
            limit=limit,
        )
    except AvailabilityDomainError as exc:
        _raise_http(exc)


@router.get(
    "/requests/{request_id}",
    response_model=StaffAvailabilityChangeRequestRead,
    summary="Detalhar solicitação de disponibilidade",
)
async def get_availability_request(
    request_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await StaffAvailabilityService(db).get_request(
            workspace_id=workspace_id,
            request_id=request_id,
            actor=context,
        )
    except AvailabilityDomainError as exc:
        _raise_http(exc)


@router.post(
    "/requests/{request_id}/approve",
    response_model=StaffAvailabilityChangeRequestRead,
    summary="Aprovar e aplicar solicitação de disponibilidade",
)
async def approve_availability_request(
    request_id: UUID,
    payload: StaffAvailabilityDecisionWrite,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await StaffAvailabilityService(db).approve_request(
            workspace_id=workspace_id,
            request_id=request_id,
            actor=context,
            note=payload.note,
        )
    except AvailabilityDomainError as exc:
        _raise_http(exc)


@router.post(
    "/requests/{request_id}/reject",
    response_model=StaffAvailabilityChangeRequestRead,
    summary="Rejeitar solicitação sem alterar disponibilidade",
)
async def reject_availability_request(
    request_id: UUID,
    payload: StaffAvailabilityDecisionWrite,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await StaffAvailabilityService(db).reject_request(
            workspace_id=workspace_id,
            request_id=request_id,
            actor=context,
            note=payload.note,
        )
    except AvailabilityDomainError as exc:
        _raise_http(exc)


@router.post(
    "/requests/{request_id}/cancel",
    response_model=StaffAvailabilityChangeRequestRead,
    summary="Cancelar solicitação pendente própria",
)
async def cancel_availability_request(
    request_id: UUID,
    payload: StaffAvailabilityDecisionWrite,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await StaffAvailabilityService(db).cancel_request(
            workspace_id=workspace_id,
            request_id=request_id,
            actor=context,
            note=payload.note,
        )
    except AvailabilityDomainError as exc:
        _raise_http(exc)


@router.get(
    "/staff/{staff_user_id}",
    response_model=StaffAvailabilityStateRead,
    summary="Ler configuração efetiva e exceções aprovadas",
)
async def get_staff_availability_state(
    staff_user_id: UUID,
    include_past: bool = Query(default=False),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await StaffAvailabilityService(db).get_state(
            workspace_id=workspace_id,
            staff_user_id=staff_user_id,
            actor=context,
            include_past=include_past,
        )
    except AvailabilityDomainError as exc:
        _raise_http(exc)


@router.get(
    "/staff/{staff_user_id}/effective",
    response_model=EffectiveStaffAvailabilityRead,
    summary="Avaliar disponibilidade em um instante",
)
async def evaluate_staff_availability(
    staff_user_id: UUID,
    at: datetime | None = Query(default=None),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await StaffAvailabilityService(db).evaluate(
            workspace_id=workspace_id,
            staff_user_id=staff_user_id,
            at=at,
        )
    except AvailabilityDomainError as exc:
        _raise_http(exc)


@router.get(
    "/staff/{staff_user_id}/resolve-responsibility",
    response_model=ResponsibilityResolutionRead,
    summary="Resolver troca automática de responsável",
)
async def resolve_staff_responsibility(
    staff_user_id: UUID,
    at: datetime | None = Query(default=None),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await StaffAvailabilityService(db).resolve_responsibility(
            workspace_id=workspace_id,
            responsible_user_id=staff_user_id,
            at=at,
        )
    except AvailabilityDomainError as exc:
        _raise_http(exc)


@router.get(
    "/audit",
    response_model=list[StaffAvailabilityAuditEventRead],
    summary="Consultar histórico imutável de disponibilidade",
)
async def list_staff_availability_audit(
    staff_user_id: UUID | None = Query(default=None),
    request_id: UUID | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await StaffAvailabilityService(db).list_audit_history(
            workspace_id=workspace_id,
            actor=context,
            staff_user_id=staff_user_id,
            request_id=request_id,
            limit=limit,
        )
    except AvailabilityDomainError as exc:
        _raise_http(exc)


__all__ = ["router"]
