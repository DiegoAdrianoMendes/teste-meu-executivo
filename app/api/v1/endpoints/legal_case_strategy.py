"""
Endpoints: Matriz Estratégia Fato–Prova–Pedido (Onda 5 — Caso 360).
Ver app/models/case_strategy.py e app/services/case_strategy_service.py.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_workspace_id, get_db, require_permission
from app.schemas.case_strategy import (
    CaseFactCreate,
    CaseFactRead,
    CasePleadingCreate,
    CasePleadingRead,
    CaseProofLinkCreate,
    CaseProofLinkRead,
    CaseStrategyGap,
    CaseStrategyMatrixRead,
)
from app.services.case_strategy_service import CaseStrategyService

router = APIRouter(prefix="/legal/demands", tags=["Jurídico — Estratégia do Caso (Onda 5)"])


@router.get(
    "/{demand_id}/strategy",
    response_model=CaseStrategyMatrixRead,
    summary="Matriz estratégia fato-prova-pedido — com lacunas evidenciadas",
)
async def get_case_strategy_matrix(
    demand_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("legal", "view")),
    db: AsyncSession = Depends(get_db),
):
    matrix = await CaseStrategyService(db).get_matrix(workspace_id, demand_id)
    return CaseStrategyMatrixRead(
        legal_demand_id=matrix.legal_demand_id,
        facts=[CaseFactRead.model_validate(fact) for fact in matrix.facts],
        proof_links=[CaseProofLinkRead.model_validate(link) for link in matrix.proof_links],
        pleadings=[CasePleadingRead.model_validate(p) for p in matrix.pleadings],
        gaps=[
            CaseStrategyGap(
                kind=gap.kind,
                fact_id=gap.fact_id,
                pleading_id=gap.pleading_id,
                description=gap.description,
                exception_id=gap.exception_id,
            )
            for gap in matrix.gaps
        ],
    )


@router.post(
    "/{demand_id}/strategy/facts",
    response_model=CaseFactRead,
    status_code=status.HTTP_201_CREATED,
    summary="Adicionar fato alegado — idempotente por descrição",
)
async def add_case_fact(
    demand_id: UUID,
    payload: CaseFactCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("legal", "manage")),
    db: AsyncSession = Depends(get_db),
):
    fact = await CaseStrategyService(db).add_fact(
        workspace_id, demand_id, payload, actor_user_id=auth.user_id
    )
    await db.commit()
    await db.refresh(fact)
    return CaseFactRead.model_validate(fact)


@router.post(
    "/{demand_id}/strategy/facts/{fact_id}/proofs",
    response_model=CaseProofLinkRead,
    status_code=status.HTTP_201_CREATED,
    summary="Vincular prova a um fato — idempotente por (fato, documento)",
)
async def add_case_proof_link(
    demand_id: UUID,
    fact_id: UUID,
    payload: CaseProofLinkCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("legal", "manage")),
    db: AsyncSession = Depends(get_db),
):
    proof = await CaseStrategyService(db).add_proof(
        workspace_id, demand_id, fact_id, payload, actor_user_id=auth.user_id
    )
    await db.commit()
    await db.refresh(proof)
    return CaseProofLinkRead.model_validate(proof)


@router.post(
    "/{demand_id}/strategy/pleadings",
    response_model=CasePleadingRead,
    status_code=status.HTTP_201_CREATED,
    summary="Adicionar pedido — sinaliza lacuna quando sem fato de sustentação",
)
async def add_case_pleading(
    demand_id: UUID,
    payload: CasePleadingCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("legal", "manage")),
    db: AsyncSession = Depends(get_db),
):
    pleading = await CaseStrategyService(db).add_pleading(
        workspace_id, demand_id, payload, actor_user_id=auth.user_id
    )
    await db.commit()
    await db.refresh(pleading)
    return CasePleadingRead.model_validate(pleading)
