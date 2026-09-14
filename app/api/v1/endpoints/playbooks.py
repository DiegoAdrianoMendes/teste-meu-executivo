"""Native Playbook Studio API."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    AuthContext,
    get_current_auth_context,
    get_current_workspace_id,
    get_db,
    require_workspace_admin,
)
from app.models.playbook import PlaybookLifecycleState, PlaybookScopeDimension
from app.schemas.playbook import (
    PlaybookActionWrite,
    PlaybookClone,
    PlaybookCreate,
    PlaybookDetailRead,
    PlaybookDiffRead,
    PlaybookDraftWrite,
    PlaybookMetricsRead,
    PlaybookOfficeTemplate,
    PlaybookOfficeTemplateKey,
    PlaybookSimulationRead,
    PlaybookSimulationWrite,
    PlaybookSummaryRead,
)
from app.services.playbook_service import (
    PlaybookConflictError,
    PlaybookNotFoundError,
    PlaybookOfficeTemplateMissingError,
    PlaybookService,
    PlaybookTransitionError,
    PlaybookValidationError,
)

router = APIRouter(prefix="/playbooks", tags=["Playbook Studio"])


def _raise_playbook_error(exc: Exception) -> None:
    if isinstance(exc, PlaybookOfficeTemplateMissingError):
        # Fail-closed: nunca cai num modelo genérico. 409 porque o bloqueio é
        # uma pré-condição de estado (falta enviar o timbrado), não um erro de
        # forma do payload.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "PB-409-OFFICE-TEMPLATE-MISSING", "message": str(exc)},
        ) from exc
    if isinstance(exc, PlaybookNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PB-404", "message": str(exc)},
        ) from exc
    if isinstance(exc, PlaybookConflictError):
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"code": "PB-412", "message": str(exc)},
        ) from exc
    if isinstance(exc, PlaybookTransitionError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "PB-409", "message": str(exc)},
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": "PB-422", "message": str(exc)},
    ) from exc


@router.get(
    "/effective",
    response_model=PlaybookDetailRead,
    summary="Resolve the most specific published playbook",
)
async def get_effective_playbook(
    area: str | None = Query(default=None, max_length=160),
    channel: str | None = Query(default=None, max_length=160),
    stage: str | None = Query(default=None, max_length=160),
    agent: str | None = Query(default=None, max_length=160),
    offer: str | None = Query(default=None, max_length=160),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(get_current_auth_context),
):
    try:
        return await PlaybookService(db).effective(
            workspace_id=workspace_id,
            selectors={
                PlaybookScopeDimension.area: area,
                PlaybookScopeDimension.channel: channel,
                PlaybookScopeDimension.stage: stage,
                PlaybookScopeDimension.agent: agent,
                PlaybookScopeDimension.offer: offer,
            },
        )
    except (
        PlaybookNotFoundError,
        PlaybookConflictError,
        PlaybookTransitionError,
        PlaybookValidationError,
    ) as exc:
        _raise_playbook_error(exc)


@router.get(
    "/office-templates/{key}/resolve",
    response_model=PlaybookOfficeTemplate,
    summary=(
        "Resolve the firm's own letterhead template for a document type "
        "(fail-closed — every generation flow must call this first)"
    ),
)
async def resolve_office_template(
    key: PlaybookOfficeTemplateKey,
    area: str | None = Query(default=None, max_length=160),
    channel: str | None = Query(default=None, max_length=160),
    stage: str | None = Query(default=None, max_length=160),
    agent: str | None = Query(default=None, max_length=160),
    offer: str | None = Query(default=None, max_length=160),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(get_current_auth_context),
):
    try:
        return await PlaybookService(db).resolve_office_template(
            workspace_id=workspace_id,
            key=key,
            selectors={
                PlaybookScopeDimension.area: area,
                PlaybookScopeDimension.channel: channel,
                PlaybookScopeDimension.stage: stage,
                PlaybookScopeDimension.agent: agent,
                PlaybookScopeDimension.offer: offer,
            },
        )
    except (
        PlaybookOfficeTemplateMissingError,
        PlaybookNotFoundError,
        PlaybookConflictError,
        PlaybookTransitionError,
        PlaybookValidationError,
    ) as exc:
        _raise_playbook_error(exc)


@router.get("", response_model=list[PlaybookSummaryRead])
async def list_playbooks(
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(get_current_auth_context),
):
    return await PlaybookService(db).list_summaries(workspace_id)


@router.post("", response_model=PlaybookDetailRead, status_code=status.HTTP_201_CREATED)
async def create_playbook(
    data: PlaybookCreate,
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
):
    try:
        return await PlaybookService(db).create(
            workspace_id=workspace_id,
            actor_user_id=context.user_id,
            data=data,
        )
    except (
        PlaybookNotFoundError,
        PlaybookConflictError,
        PlaybookTransitionError,
        PlaybookValidationError,
    ) as exc:
        _raise_playbook_error(exc)


@router.get("/{playbook_id}", response_model=PlaybookDetailRead)
async def get_playbook(
    playbook_id: UUID,
    response: Response,
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(get_current_auth_context),
):
    try:
        detail = await PlaybookService(db).detail(
            workspace_id=workspace_id,
            document_id=playbook_id,
        )
    except (
        PlaybookNotFoundError,
        PlaybookConflictError,
        PlaybookTransitionError,
        PlaybookValidationError,
    ) as exc:
        _raise_playbook_error(exc)
    response.headers["ETag"] = f'"{detail.etag}"'
    return detail


@router.post("/{playbook_id}/clone", response_model=PlaybookDetailRead)
async def clone_playbook(
    playbook_id: UUID,
    data: PlaybookClone,
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
):
    try:
        return await PlaybookService(db).clone(
            workspace_id=workspace_id,
            actor_user_id=context.user_id,
            document_id=playbook_id,
            name=data.name,
        )
    except (
        PlaybookNotFoundError,
        PlaybookConflictError,
        PlaybookTransitionError,
        PlaybookValidationError,
    ) as exc:
        _raise_playbook_error(exc)


@router.put("/{playbook_id}/draft", response_model=PlaybookDetailRead)
async def save_playbook_draft(
    playbook_id: UUID,
    data: PlaybookDraftWrite,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
):
    try:
        detail = await PlaybookService(db).save_draft(
            workspace_id=workspace_id,
            actor_user_id=context.user_id,
            document_id=playbook_id,
            data=data,
            expected_etag=if_match,
        )
    except (
        PlaybookNotFoundError,
        PlaybookConflictError,
        PlaybookTransitionError,
        PlaybookValidationError,
    ) as exc:
        _raise_playbook_error(exc)
    response.headers["ETag"] = f'"{detail.etag}"'
    return detail


async def _transition(
    *,
    service: PlaybookService,
    workspace_id: UUID,
    actor_user_id: UUID,
    playbook_id: UUID,
    data: PlaybookActionWrite,
    target: PlaybookLifecycleState,
) -> PlaybookDetailRead:
    try:
        return await service.transition(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            document_id=playbook_id,
            target=target,
            reason=data.reason,
        )
    except (
        PlaybookNotFoundError,
        PlaybookConflictError,
        PlaybookTransitionError,
        PlaybookValidationError,
    ) as exc:
        _raise_playbook_error(exc)


@router.post("/{playbook_id}/submit", response_model=PlaybookDetailRead)
async def submit_playbook(
    playbook_id: UUID,
    data: PlaybookActionWrite,
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
):
    return await _transition(
        service=PlaybookService(db),
        workspace_id=workspace_id,
        actor_user_id=context.user_id,
        playbook_id=playbook_id,
        data=data,
        target=PlaybookLifecycleState.in_review,
    )


@router.post("/{playbook_id}/approve", response_model=PlaybookDetailRead)
async def approve_playbook(
    playbook_id: UUID,
    data: PlaybookActionWrite,
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
):
    return await _transition(
        service=PlaybookService(db),
        workspace_id=workspace_id,
        actor_user_id=context.user_id,
        playbook_id=playbook_id,
        data=data,
        target=PlaybookLifecycleState.approved,
    )


@router.post("/{playbook_id}/publish", response_model=PlaybookDetailRead)
async def publish_playbook(
    playbook_id: UUID,
    data: PlaybookActionWrite,
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
):
    return await _transition(
        service=PlaybookService(db),
        workspace_id=workspace_id,
        actor_user_id=context.user_id,
        playbook_id=playbook_id,
        data=data,
        target=PlaybookLifecycleState.published,
    )


@router.post("/{playbook_id}/rollback/{version}", response_model=PlaybookDetailRead)
async def rollback_playbook(
    playbook_id: UUID,
    version: int,
    data: PlaybookActionWrite,
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
):
    try:
        return await PlaybookService(db).rollback(
            workspace_id=workspace_id,
            actor_user_id=context.user_id,
            document_id=playbook_id,
            target_version=version,
            reason=data.reason,
        )
    except (
        PlaybookNotFoundError,
        PlaybookConflictError,
        PlaybookTransitionError,
        PlaybookValidationError,
    ) as exc:
        _raise_playbook_error(exc)


@router.get("/{playbook_id}/diff", response_model=PlaybookDiffRead)
async def diff_playbook(
    playbook_id: UUID,
    from_version: int = Query(ge=1),
    to_version: int = Query(ge=1),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(get_current_auth_context),
):
    try:
        return await PlaybookService(db).diff(
            workspace_id=workspace_id,
            document_id=playbook_id,
            from_version=from_version,
            to_version=to_version,
        )
    except (
        PlaybookNotFoundError,
        PlaybookConflictError,
        PlaybookTransitionError,
        PlaybookValidationError,
    ) as exc:
        _raise_playbook_error(exc)


@router.get("/{playbook_id}/metrics", response_model=PlaybookMetricsRead)
async def get_playbook_metrics(
    playbook_id: UUID,
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(get_current_auth_context),
):
    try:
        return await PlaybookService(db).metrics(
            workspace_id=workspace_id,
            document_id=playbook_id,
        )
    except (
        PlaybookNotFoundError,
        PlaybookConflictError,
        PlaybookTransitionError,
        PlaybookValidationError,
    ) as exc:
        _raise_playbook_error(exc)


@router.get("/{playbook_id}/simulations", response_model=list[PlaybookSimulationRead])
async def list_playbook_simulations(
    playbook_id: UUID,
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(get_current_auth_context),
):
    try:
        return await PlaybookService(db).simulations(
            workspace_id=workspace_id,
            document_id=playbook_id,
        )
    except (
        PlaybookNotFoundError,
        PlaybookConflictError,
        PlaybookTransitionError,
        PlaybookValidationError,
    ) as exc:
        _raise_playbook_error(exc)


@router.post("/{playbook_id}/simulations", response_model=PlaybookSimulationRead)
async def simulate_playbook(
    playbook_id: UUID,
    data: PlaybookSimulationWrite,
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
):
    try:
        return await PlaybookService(db).simulate(
            workspace_id=workspace_id,
            actor_user_id=context.user_id,
            document_id=playbook_id,
            data=data,
        )
    except (
        PlaybookNotFoundError,
        PlaybookConflictError,
        PlaybookTransitionError,
        PlaybookValidationError,
    ) as exc:
        _raise_playbook_error(exc)
