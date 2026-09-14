"""Catálogo CMED local, ciclos prescritos e política interna de honorários."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_workspace_id, get_db, require_permission
from app.models.user import User
from app.schemas.medication import (
    LegalPricingPolicyRead,
    LegalPricingPolicyUpdate,
    MatterTreatmentEstimateRead,
    MedicationCatalogStatusRead,
    MedicationReferenceRead,
    TreatmentEstimateUpsert,
)
from app.services.medication_pricing_service import MedicationPricingService

router = APIRouter(prefix="/medications", tags=["Medicamentos e Honorários"])


async def _actor_label(db: AsyncSession, auth_context: AuthContext) -> str:
    actor = await db.get(User, auth_context.user_id)
    return actor.name if actor else "Usuário KLM"


@router.get("/catalog/status", response_model=MedicationCatalogStatusRead)
async def catalog_status(
    _: AuthContext = Depends(require_permission("lista", "view")),
    db: AsyncSession = Depends(get_db),
) -> MedicationCatalogStatusRead:
    return await MedicationPricingService(db).catalog_status()


@router.get("/catalog", response_model=list[MedicationReferenceRead])
async def search_catalog(
    q: str | None = Query(default=None, max_length=200),
    oncology: bool | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
    _: AuthContext = Depends(require_permission("lista", "view")),
    db: AsyncSession = Depends(get_db),
) -> list[MedicationReferenceRead]:
    records = await MedicationPricingService(db).search_catalog(
        query=q,
        oncology=oncology,
        limit=limit,
    )
    return [MedicationReferenceRead.model_validate(record) for record in records]


@router.get(
    "/matters/{matter_id}/treatment-estimates",
    response_model=list[MatterTreatmentEstimateRead],
)
async def list_treatment_estimates(
    matter_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("lista", "view")),
    db: AsyncSession = Depends(get_db),
) -> list[MatterTreatmentEstimateRead]:
    try:
        estimates = await MedicationPricingService(db).list_treatment_estimates(
            workspace_id, matter_id
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return [MatterTreatmentEstimateRead.model_validate(estimate) for estimate in estimates]


@router.put(
    "/matters/{matter_id}/treatment-estimates",
    response_model=MatterTreatmentEstimateRead,
)
async def upsert_treatment_estimate(
    matter_id: UUID,
    payload: TreatmentEstimateUpsert,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth_context: AuthContext = Depends(require_permission("kanban", "edit")),
    db: AsyncSession = Depends(get_db),
) -> MatterTreatmentEstimateRead:
    try:
        estimate = await MedicationPricingService(db).upsert_treatment_estimate(
            workspace_id,
            matter_id,
            payload,
            actor_user_id=auth_context.user_id,
            actor_label=await _actor_label(db, auth_context),
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return MatterTreatmentEstimateRead.model_validate(estimate)


@router.delete(
    "/matters/{matter_id}/treatment-estimates/{estimate_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_treatment_estimate(
    matter_id: UUID,
    estimate_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth_context: AuthContext = Depends(require_permission("kanban", "edit")),
    db: AsyncSession = Depends(get_db),
) -> Response:
    try:
        await MedicationPricingService(db).delete_treatment_estimate(
            workspace_id,
            matter_id,
            estimate_id,
            actor_user_id=auth_context.user_id,
            actor_label=await _actor_label(db, auth_context),
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/pricing-policy", response_model=LegalPricingPolicyRead)
async def get_pricing_policy(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("configuracoes", "view")),
    db: AsyncSession = Depends(get_db),
) -> LegalPricingPolicyRead:
    try:
        return await MedicationPricingService(db).get_pricing_policy(workspace_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.put("/pricing-policy", response_model=LegalPricingPolicyRead)
async def update_pricing_policy(
    payload: LegalPricingPolicyUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth_context: AuthContext = Depends(require_permission("configuracoes", "edit")),
    db: AsyncSession = Depends(get_db),
) -> LegalPricingPolicyRead:
    try:
        return await MedicationPricingService(db).update_pricing_policy(
            workspace_id,
            payload,
            actor_user_id=auth_context.user_id,
            actor_label=await _actor_label(db, auth_context),
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


__all__ = ["router"]
