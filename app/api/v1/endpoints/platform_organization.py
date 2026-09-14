"""Blackbox teams, assignments, milestones, invitations and review workflows."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.platform_access import require_platform_permission
from app.core.platform_rbac import has_platform_permission
from app.models.platform_organization import (
    PlatformOnboardingMilestone,
    PlatformOperationalAssignment,
    PlatformReviewRequest,
    PlatformStaffInvitation,
    PlatformWorkspaceAssignment,
)
from app.models.user import User
from app.schemas.platform_organization import (
    PlatformDashboardPreferenceRead,
    PlatformDashboardPreferenceWrite,
    PlatformOnboardingMilestonePageRead,
    PlatformOnboardingMilestoneRead,
    PlatformOnboardingMilestoneWrite,
    PlatformOperationalAssignmentPageRead,
    PlatformOperationalAssignmentRead,
    PlatformOperationalAssignmentWrite,
    PlatformReasonWrite,
    PlatformReviewRequestPageRead,
    PlatformReviewRequestRead,
    PlatformReviewRequestWrite,
    PlatformStaffInvitationPageRead,
    PlatformStaffInvitationRead,
    PlatformStaffInvitationWrite,
    PlatformTeamMembershipWrite,
    PlatformTeamPageRead,
    PlatformTeamRead,
    PlatformTeamWrite,
    PlatformWorkspaceAssignmentPageRead,
    PlatformWorkspaceAssignmentRead,
    PlatformWorkspaceAssignmentWrite,
)
from app.services.platform_organization_service import (
    PlatformOrganizationError,
    PlatformOrganizationService,
)

router = APIRouter(prefix="/platform/organization", tags=["Platform Organization"])
_dashboard_view = require_platform_permission("dashboard", "view")
_staff_view = require_platform_permission("staff", "view")
_staff_manage = require_platform_permission("staff", "manage")
_customers_manage = require_platform_permission("customers", "manage")
_onboarding_manage = require_platform_permission("onboarding", "manage")


def _raise(error: PlatformOrganizationError) -> None:
    raise HTTPException(
        status_code=error.status_code, detail={"code": error.code, "message": error.message}
    ) from error


def _canonical(request: Request) -> None:
    request.state.canonical_platform_audit_recorded = True


def _command_service(
    db: AsyncSession,
    request: Request,
    context: AuthContext,
) -> PlatformOrganizationService:
    return PlatformOrganizationService(db, request=request, context=context)


@router.get("/teams", response_model=list[PlatformTeamRead], deprecated=True)
async def list_teams(db: AsyncSession = Depends(get_db), _: User = Depends(_staff_view)):
    """Compatibility route; Blackbox selectors use the paginated endpoint."""
    return await PlatformOrganizationService(db).list_teams()


@router.get("/teams/page", response_model=PlatformTeamPageRead)
async def list_teams_page(
    search: str | None = Query(default=None),
    active: bool | None = Query(default=None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_staff_view),
):
    from app.models.platform_organization import PlatformTeam

    filters = []
    normalized_search = (search or "").strip()
    if normalized_search:
        escaped = normalized_search.replace("%", r"\%").replace("_", r"\_")
        pattern = f"%{escaped}%"
        exact_id = None
        try:
            exact_id = UUID(normalized_search)
        except ValueError:
            pass
        filters.append(
            or_(
                PlatformTeam.id == exact_id if exact_id is not None else False,
                PlatformTeam.name.ilike(pattern, escape="\\"),
                PlatformTeam.slug.ilike(pattern, escape="\\"),
            )
        )
    if active is not None:
        filters.append(PlatformTeam.active.is_(active))
    total = int(await db.scalar(select(func.count(PlatformTeam.id)).where(*filters)) or 0)
    items = list(
        (
            await db.scalars(
                select(PlatformTeam)
                .where(*filters)
                .order_by(PlatformTeam.name.asc(), PlatformTeam.id.asc())
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    return PlatformTeamPageRead(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < total,
        sort=[
            {"field": "name", "direction": "asc"},
            {"field": "id", "direction": "asc"},
        ],
    )


@router.post("/teams", response_model=PlatformTeamRead, status_code=status.HTTP_201_CREATED)
async def create_team(
    payload: PlatformTeamWrite,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_staff_manage),
):
    data = payload.model_dump(exclude={"reason", "expected_version"})
    try:
        row = await _command_service(db, request, context).save_team(
            actor=actor, team_id=None, values=data, reason=payload.reason
        )
    except PlatformOrganizationError as error:
        _raise(error)
    _canonical(request)
    return row


@router.patch("/teams/{team_id}", response_model=PlatformTeamRead)
async def update_team(
    team_id: UUID,
    payload: PlatformTeamWrite,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_staff_manage),
):
    data = payload.model_dump(exclude={"reason"})
    try:
        row = await _command_service(db, request, context).save_team(
            actor=actor, team_id=team_id, values=data, reason=payload.reason
        )
    except PlatformOrganizationError as error:
        _raise(error)
    _canonical(request)
    return row


@router.post("/teams/{team_id}/members", status_code=status.HTTP_201_CREATED)
async def add_team_member(
    team_id: UUID,
    payload: PlatformTeamMembershipWrite,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_staff_manage),
):
    try:
        row = await _command_service(db, request, context).add_team_member(
            actor=actor,
            team_id=team_id,
            values=payload.model_dump(exclude={"reason"}),
            reason=payload.reason,
        )
    except PlatformOrganizationError as error:
        _raise(error)
    _canonical(request)
    return {
        "id": row.id,
        "teamId": row.team_id,
        "userId": row.user_id,
        "isPrimary": row.is_primary,
        "active": row.active,
        "version": row.version,
    }


@router.get(
    "/workspaces/{workspace_id}/assignments",
    response_model=list[PlatformWorkspaceAssignmentRead],
    deprecated=True,
)
async def list_workspace_assignments(
    workspace_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(_dashboard_view)
):
    return list(
        (
            await db.scalars(
                select(PlatformWorkspaceAssignment)
                .where(PlatformWorkspaceAssignment.workspace_id == workspace_id)
                .order_by(PlatformWorkspaceAssignment.assignment_type)
            )
        ).all()
    )


@router.get(
    "/workspaces/{workspace_id}/assignments/page",
    response_model=PlatformWorkspaceAssignmentPageRead,
)
async def list_workspace_assignments_page(
    workspace_id: UUID,
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_dashboard_view),
):
    filters = [PlatformWorkspaceAssignment.workspace_id == workspace_id]
    total = int(
        await db.scalar(select(func.count(PlatformWorkspaceAssignment.id)).where(*filters)) or 0
    )
    items = list(
        (
            await db.scalars(
                select(PlatformWorkspaceAssignment)
                .where(*filters)
                .order_by(
                    PlatformWorkspaceAssignment.assignment_type.asc(),
                    PlatformWorkspaceAssignment.id.asc(),
                )
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    return PlatformWorkspaceAssignmentPageRead(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < total,
        sort=[
            {"field": "assignmentType", "direction": "asc"},
            {"field": "id", "direction": "asc"},
        ],
    )


@router.put(
    "/workspaces/{workspace_id}/assignments", response_model=PlatformWorkspaceAssignmentRead
)
async def save_workspace_assignment(
    workspace_id: UUID,
    payload: PlatformWorkspaceAssignmentWrite,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_customers_manage),
):
    try:
        row = await _command_service(db, request, context).save_workspace_assignment(
            actor=actor,
            workspace_id=workspace_id,
            values=payload.model_dump(exclude={"reason"}),
            reason=payload.reason,
        )
    except PlatformOrganizationError as error:
        _raise(error)
    _canonical(request)
    return row


@router.get("/dashboard-preference", response_model=PlatformDashboardPreferenceRead | None)
async def get_dashboard_preference(
    db: AsyncSession = Depends(get_db), actor: User = Depends(_dashboard_view)
):
    return await PlatformOrganizationService(db).get_preference(actor.id)


@router.put("/dashboard-preference", response_model=PlatformDashboardPreferenceRead)
async def save_dashboard_preference(
    payload: PlatformDashboardPreferenceWrite,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_dashboard_view),
):
    try:
        row = await _command_service(db, request, context).save_preference(
            actor=actor, values=payload.model_dump()
        )
    except PlatformOrganizationError as error:
        _raise(error)
    _canonical(request)
    return row


@router.get(
    "/staff-invitations",
    response_model=list[PlatformStaffInvitationRead],
    deprecated=True,
)
async def list_staff_invitations(
    limit: int = Query(100, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_staff_view),
):
    return list(
        (
            await db.scalars(
                select(PlatformStaffInvitation)
                .order_by(PlatformStaffInvitation.created_at.desc())
                .limit(limit)
            )
        ).all()
    )


@router.get(
    "/staff-invitations/page",
    response_model=PlatformStaffInvitationPageRead,
)
async def list_staff_invitations_page(
    search: str | None = Query(default=None),
    invitation_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_staff_view),
):
    filters = []
    normalized_search = (search or "").strip()
    if normalized_search:
        pattern = f"%{normalized_search.replace('%', r'\%').replace('_', r'\_')}%"
        filters.append(
            or_(
                PlatformStaffInvitation.name.ilike(pattern, escape="\\"),
                PlatformStaffInvitation.email.ilike(pattern, escape="\\"),
            )
        )
    if invitation_status:
        filters.append(PlatformStaffInvitation.status == invitation_status)
    total = int(
        await db.scalar(select(func.count(PlatformStaffInvitation.id)).where(*filters)) or 0
    )
    items = list(
        (
            await db.scalars(
                select(PlatformStaffInvitation)
                .where(*filters)
                .order_by(
                    PlatformStaffInvitation.created_at.desc(),
                    PlatformStaffInvitation.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    return PlatformStaffInvitationPageRead(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < total,
        sort=[
            {"field": "createdAt", "direction": "desc"},
            {"field": "id", "direction": "desc"},
        ],
    )


@router.post(
    "/staff-invitations",
    response_model=PlatformStaffInvitationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_staff_invitation(
    payload: PlatformStaffInvitationWrite,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_staff_manage),
):
    try:
        row = await _command_service(db, request, context).create_staff_invitation(
            actor=actor, values=payload.model_dump(exclude={"reason"}), reason=payload.reason
        )
    except PlatformOrganizationError as error:
        _raise(error)
    _canonical(request)
    return row


@router.post(
    "/staff-invitations/{invitation_id}/revoke", response_model=PlatformStaffInvitationRead
)
async def revoke_staff_invitation(
    invitation_id: UUID,
    payload: PlatformReasonWrite,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_staff_manage),
):
    try:
        row = await _command_service(db, request, context).revoke_staff_invitation(
            actor=actor, invitation_id=invitation_id, reason=payload.reason
        )
    except PlatformOrganizationError as error:
        _raise(error)
    _canonical(request)
    return row


@router.get(
    "/workspaces/{workspace_id}/milestones",
    response_model=list[PlatformOnboardingMilestoneRead],
    deprecated=True,
)
async def list_milestones(
    workspace_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(_dashboard_view)
):
    return list(
        (
            await db.scalars(
                select(PlatformOnboardingMilestone)
                .where(PlatformOnboardingMilestone.workspace_id == workspace_id)
                .order_by(PlatformOnboardingMilestone.due_at, PlatformOnboardingMilestone.key)
            )
        ).all()
    )


@router.get(
    "/workspaces/{workspace_id}/milestones/page",
    response_model=PlatformOnboardingMilestonePageRead,
)
async def list_milestones_page(
    workspace_id: UUID,
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_dashboard_view),
):
    filters = [PlatformOnboardingMilestone.workspace_id == workspace_id]
    total = int(
        await db.scalar(select(func.count(PlatformOnboardingMilestone.id)).where(*filters)) or 0
    )
    items = list(
        (
            await db.scalars(
                select(PlatformOnboardingMilestone)
                .where(*filters)
                .order_by(
                    PlatformOnboardingMilestone.due_at.asc(),
                    PlatformOnboardingMilestone.key.asc(),
                    PlatformOnboardingMilestone.id.asc(),
                )
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    return PlatformOnboardingMilestonePageRead(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < total,
        sort=[
            {"field": "dueAt", "direction": "asc"},
            {"field": "key", "direction": "asc"},
            {"field": "id", "direction": "asc"},
        ],
    )


@router.put("/workspaces/{workspace_id}/milestones", response_model=PlatformOnboardingMilestoneRead)
async def save_milestone(
    workspace_id: UUID,
    payload: PlatformOnboardingMilestoneWrite,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_onboarding_manage),
):
    try:
        row = await _command_service(db, request, context).save_milestone(
            actor=actor,
            workspace_id=workspace_id,
            values=payload.model_dump(exclude={"reason"}),
            reason=payload.reason,
        )
    except PlatformOrganizationError as error:
        _raise(error)
    _canonical(request)
    return row


@router.get(
    "/operational-assignments",
    response_model=list[PlatformOperationalAssignmentRead],
    deprecated=True,
)
async def list_operational_assignments(
    source_type: str | None = Query(None, alias="sourceType"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_dashboard_view),
):
    query = select(PlatformOperationalAssignment)
    if source_type:
        query = query.where(PlatformOperationalAssignment.source_type == source_type)
    return list(
        (
            await db.scalars(
                query.order_by(
                    PlatformOperationalAssignment.due_at, PlatformOperationalAssignment.id
                ).limit(500)
            )
        ).all()
    )


@router.get(
    "/operational-assignments/page",
    response_model=PlatformOperationalAssignmentPageRead,
)
async def list_operational_assignments_page(
    source_type: str | None = Query(None, alias="sourceType"),
    state: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_dashboard_view),
):
    filters = []
    if source_type:
        filters.append(PlatformOperationalAssignment.source_type == source_type)
    if state:
        filters.append(PlatformOperationalAssignment.state == state)
    total = int(
        await db.scalar(select(func.count(PlatformOperationalAssignment.id)).where(*filters)) or 0
    )
    items = list(
        (
            await db.scalars(
                select(PlatformOperationalAssignment)
                .where(*filters)
                .order_by(
                    PlatformOperationalAssignment.due_at.asc().nulls_last(),
                    PlatformOperationalAssignment.id.asc(),
                )
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    return PlatformOperationalAssignmentPageRead(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < total,
        sort=[
            {"field": "dueAt", "direction": "asc_nulls_last"},
            {"field": "id", "direction": "asc"},
        ],
    )


@router.put("/operational-assignments", response_model=PlatformOperationalAssignmentRead)
async def save_operational_assignment(
    payload: PlatformOperationalAssignmentWrite,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_dashboard_view),
):
    permission = {
        "billing": "billing",
        "lead": "sales",
        "ticket": "support",
        "publication": "announcements",
        "configuration": "settings",
    }[payload.source_type]
    if not has_platform_permission(actor, permission, "manage"):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "PLATFORM_ASSIGNMENT_FORBIDDEN",
                "message": "Atribuição não autorizada.",
            },
        )
    try:
        row = await _command_service(db, request, context).save_operational_assignment(
            actor=actor, values=payload.model_dump(exclude={"reason"}), reason=payload.reason
        )
    except PlatformOrganizationError as error:
        _raise(error)
    _canonical(request)
    return row


@router.get(
    "/reviews",
    response_model=list[PlatformReviewRequestRead],
    deprecated=True,
)
async def list_reviews(
    source_type: str | None = Query(None, alias="sourceType"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_dashboard_view),
):
    query = select(PlatformReviewRequest)
    if source_type:
        query = query.where(PlatformReviewRequest.source_type == source_type)
    return list(
        (
            await db.scalars(
                query.order_by(PlatformReviewRequest.review_due_at, PlatformReviewRequest.id).limit(
                    500
                )
            )
        ).all()
    )


@router.get("/reviews/page", response_model=PlatformReviewRequestPageRead)
async def list_reviews_page(
    source_type: str | None = Query(None, alias="sourceType"),
    state: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_dashboard_view),
):
    filters = []
    if source_type:
        filters.append(PlatformReviewRequest.source_type == source_type)
    if state:
        filters.append(PlatformReviewRequest.state == state)
    total = int(await db.scalar(select(func.count(PlatformReviewRequest.id)).where(*filters)) or 0)
    items = list(
        (
            await db.scalars(
                select(PlatformReviewRequest)
                .where(*filters)
                .order_by(
                    PlatformReviewRequest.review_due_at.asc().nulls_last(),
                    PlatformReviewRequest.id.asc(),
                )
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    return PlatformReviewRequestPageRead(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < total,
        sort=[
            {"field": "reviewDueAt", "direction": "asc_nulls_last"},
            {"field": "id", "direction": "asc"},
        ],
    )


@router.put("/reviews", response_model=PlatformReviewRequestRead)
async def save_review(
    payload: PlatformReviewRequestWrite,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_dashboard_view),
):
    module = "announcements" if payload.source_type == "publication" else "settings"
    if not has_platform_permission(actor, module, "manage"):
        raise HTTPException(
            status_code=403,
            detail={"code": "PLATFORM_REVIEW_FORBIDDEN", "message": "Revisão não autorizada."},
        )
    try:
        row = await _command_service(db, request, context).save_review(
            actor=actor, values=payload.model_dump(exclude={"reason"}), reason=payload.reason
        )
    except PlatformOrganizationError as error:
        _raise(error)
    _canonical(request)
    return row
