"""Super-Admin API for immutable plan-policy publication and rollout."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.platform_access import require_platform_permission, require_platform_root
from app.models.user import User
from app.schemas.plan_policy import (
    PlanPolicyAuditEventRead,
    PlanPolicyDraftRead,
    PlanPolicyDraftWrite,
    PlanPolicyEditorRead,
    PlanPolicyEntitlementCatalogItemRead,
    PlanPolicyMaterializationRead,
    PlanPolicyPlanPageRead,
    PlanPolicyPlanSummaryRead,
    PlanPolicyPreviewRead,
    PlanPolicyPreviewRequest,
    PlanPolicyPublicationRead,
    PlanPolicyPublishRequest,
    PlanPolicyVersionRead,
    PlanPolicyVersionPageRead,
    WorkspacePlanPolicyResolutionRead,
)
from app.services.plan_policy_service import (
    PlanPolicyConflictError,
    PlanPolicyNotFoundError,
    PlanPolicyService,
    PlanPolicyValidationError,
)

router = APIRouter(
    prefix="/platform/plan-policies",
    tags=["Platform Plan Policies"],
)

_plans_view = require_platform_permission("plans", "view")
_plans_manage = require_platform_permission("plans", "manage")
_plans_draft = require_platform_permission("plans", "draft")
_plans_preview = require_platform_permission("plans", "preview")


def _raise_plan_policy_error(error: Exception) -> None:
    if isinstance(error, PlanPolicyNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PLAN-POLICY-NOT-FOUND", "message": str(error)},
        ) from error
    if isinstance(error, PlanPolicyConflictError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "PLAN-POLICY-CONFLICT", "message": str(error)},
        ) from error
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": "PLAN-POLICY-INVALID", "message": str(error)},
    ) from error


@router.get(
    "",
    response_model=list[PlanPolicyPlanSummaryRead],
    summary="Listar estado versionado dos pacotes de planos",
)
async def list_plan_policies(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_plans_view),
) -> list[PlanPolicyPlanSummaryRead]:
    return await PlanPolicyService(db).list_plan_summaries()


@router.get(
    "/page",
    response_model=PlanPolicyPlanPageRead,
    summary="Listar pacotes de planos com paginação real",
)
async def list_plan_policies_page(
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_plans_view),
) -> PlanPolicyPlanPageRead:
    items, total = await PlanPolicyService(db).list_plan_summaries_page(
        limit=limit,
        offset=offset,
    )
    return PlanPolicyPlanPageRead(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < total,
    )


@router.get(
    "/entitlement-catalog",
    response_model=list[PlanPolicyEntitlementCatalogItemRead],
    summary="Listar controles checkbox/número dos entitlements",
)
async def get_plan_policy_entitlement_catalog(
    _: User = Depends(_plans_view),
) -> list[PlanPolicyEntitlementCatalogItemRead]:
    return PlanPolicyService.entitlement_catalog()


@router.post(
    "/workspaces/{workspace_id}/resolve",
    response_model=WorkspacePlanPolicyResolutionRead,
    summary="Materializar e resolver a revisão efetiva de um workspace",
)
async def resolve_workspace_plan_policy(
    workspace_id: UUID,
    effective_at: datetime | None = Query(default=None, alias="effectiveAt"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_plans_manage),
) -> WorkspacePlanPolicyResolutionRead:
    try:
        return await PlanPolicyService(db).resolve_workspace(
            workspace_id=workspace_id,
            effective_at=effective_at,
            actor_user_id=actor.id,
        )
    except (
        PlanPolicyNotFoundError,
        PlanPolicyConflictError,
        PlanPolicyValidationError,
    ) as error:
        _raise_plan_policy_error(error)


@router.get(
    "/{plan_id}/editor",
    response_model=PlanPolicyEditorRead,
    summary="Carregar snapshot efetivo para o editor de plano",
)
async def get_plan_policy_editor(
    plan_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_plans_view),
) -> PlanPolicyEditorRead:
    try:
        return await PlanPolicyService(db).get_editor(plan_id)
    except (PlanPolicyNotFoundError, PlanPolicyValidationError) as error:
        _raise_plan_policy_error(error)


@router.put(
    "/{plan_id}/draft",
    response_model=PlanPolicyDraftRead,
    summary="Salvar checkboxes e números no rascunho do plano",
)
async def save_plan_policy_draft(
    plan_id: UUID,
    payload: PlanPolicyDraftWrite,
    if_match: str | None = Header(default=None, alias="If-Match"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_plans_draft),
) -> PlanPolicyDraftRead:
    try:
        draft = await PlanPolicyService(db).save_draft(
            plan_id=plan_id,
            payload=payload,
            actor_user_id=actor.id,
            expected_etag=if_match,
        )
        return PlanPolicyDraftRead.model_validate(draft)
    except (
        PlanPolicyNotFoundError,
        PlanPolicyConflictError,
        PlanPolicyValidationError,
    ) as error:
        _raise_plan_policy_error(error)


@router.post(
    "/{plan_id}/preview",
    response_model=PlanPolicyPreviewRead,
    summary="Pré-visualizar contas afetadas pelo rollout",
)
async def preview_plan_policy_rollout(
    plan_id: UUID,
    payload: PlanPolicyPreviewRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_plans_preview),
) -> PlanPolicyPreviewRead:
    try:
        return await PlanPolicyService(db).preview(plan_id=plan_id, rollout=payload)
    except (PlanPolicyNotFoundError, PlanPolicyValidationError) as error:
        _raise_plan_policy_error(error)


@router.post(
    "/{plan_id}/publish",
    response_model=PlanPolicyPublicationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Publicar uma versão imutável da política do plano",
)
async def publish_plan_policy(
    plan_id: UUID,
    payload: PlanPolicyPublishRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_root),
) -> PlanPolicyPublicationRead:
    try:
        result = await PlanPolicyService(db).publish(
            plan_id=plan_id,
            payload=payload,
            actor_user_id=actor.id,
            idempotency_key=idempotency_key,
        )
        return PlanPolicyPublicationRead(
            policy=PlanPolicyVersionRead.model_validate(result.policy),
            affected_count=result.affected_count,
            assignments_created=result.assignments_created,
            idempotent_replay=result.idempotent_replay,
        )
    except (
        PlanPolicyNotFoundError,
        PlanPolicyConflictError,
        PlanPolicyValidationError,
    ) as error:
        _raise_plan_policy_error(error)


@router.get(
    "/{plan_id}/versions",
    response_model=list[PlanPolicyVersionRead],
    summary="Listar histórico imutável de publicações",
)
async def list_plan_policy_versions(
    plan_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_plans_view),
) -> list[PlanPolicyVersionRead]:
    try:
        versions = await PlanPolicyService(db).list_versions(plan_id)
        return [PlanPolicyVersionRead.model_validate(version) for version in versions]
    except PlanPolicyNotFoundError as error:
        _raise_plan_policy_error(error)


@router.get(
    "/{plan_id}/versions/page",
    response_model=PlanPolicyVersionPageRead,
    summary="Listar histórico imutável de publicações com paginação real",
)
async def list_plan_policy_versions_page(
    plan_id: UUID,
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_plans_view),
) -> PlanPolicyVersionPageRead:
    try:
        items, total = await PlanPolicyService(db).list_versions_page(
            plan_id,
            limit=limit,
            offset=offset,
        )
        return PlanPolicyVersionPageRead(
            items=[PlanPolicyVersionRead.model_validate(item) for item in items],
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + len(items) < total,
        )
    except PlanPolicyNotFoundError as error:
        _raise_plan_policy_error(error)


@router.get(
    "/{plan_id}/versions/{version}",
    response_model=PlanPolicyVersionRead,
    summary="Ler uma publicação histórica sem mutá-la",
)
async def get_plan_policy_version(
    plan_id: UUID,
    version: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_plans_view),
) -> PlanPolicyVersionRead:
    try:
        policy = await PlanPolicyService(db).get_version(plan_id, version)
        return PlanPolicyVersionRead.model_validate(policy)
    except PlanPolicyNotFoundError as error:
        _raise_plan_policy_error(error)


@router.get(
    "/{plan_id}/audit",
    response_model=list[PlanPolicyAuditEventRead],
    summary="Listar trilha de auditoria do plano",
)
async def list_plan_policy_audit(
    plan_id: UUID,
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_plans_view),
) -> list[PlanPolicyAuditEventRead]:
    try:
        return await PlanPolicyService(db).list_audit(plan_id=plan_id, limit=limit)
    except PlanPolicyNotFoundError as error:
        _raise_plan_policy_error(error)


@router.post(
    "/{plan_id}/materialize-due",
    response_model=PlanPolicyMaterializationRead,
    summary="Materializar assignments de rollouts já vigentes",
)
async def materialize_due_plan_policy_assignments(
    plan_id: UUID,
    effective_at: datetime | None = Query(default=None, alias="effectiveAt"),
    limit: int = Query(default=1_000, ge=1, le=10_000),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_plans_manage),
) -> PlanPolicyMaterializationRead:
    try:
        return await PlanPolicyService(db).materialize_due_assignments(
            plan_id=plan_id,
            actor_user_id=actor.id,
            effective_at=effective_at,
            limit=limit,
        )
    except (
        PlanPolicyNotFoundError,
        PlanPolicyConflictError,
        PlanPolicyValidationError,
    ) as error:
        _raise_plan_policy_error(error)


__all__ = ["router"]
