"""Endpoints: BI jurídico + capacidade (Onda 8, Plano Mestre §15).

Sem frontend novo nesta onda — mesma disciplina de
app/api/v1/endpoints/legal_controladoria.py: só dado agregado, exposto
para o painel que entra depois. Gate por `require_permission("relatorios",
"view")`, o mesmo módulo de app/api/v1/endpoints/reports.py — BI jurídico é
relatório, não operação de escrita sobre o caso.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_workspace_id, get_db, require_permission
from app.schemas.legal_bi import (
    AiBiRead,
    CapacityReportRead,
    LegalBiRead,
    OperationBiRead,
)
from app.services.legal_bi_service import LegalBiService
from app.services.legal_capacity_service import LegalCapacityService

router = APIRouter(prefix="/legal", tags=["Jurídico — BI e Capacidade (Onda 8)"])

require_relatorios_view = require_permission("relatorios", "view")


@router.get(
    "/bi/operacao",
    response_model=OperationBiRead,
    summary="Indicadores de OPERAÇÃO do BI jurídico (Plano Mestre §15)",
)
async def get_operation_bi(
    period_from: datetime | None = Query(default=None),
    period_to: datetime | None = Query(default=None),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_relatorios_view),
    db: AsyncSession = Depends(get_db),
):
    service = LegalBiService(db, workspace_id)
    result = await service.get_operation_bi(period_from=period_from, period_to=period_to)
    return OperationBiRead.model_validate(result)


@router.get(
    "/bi/juridico",
    response_model=LegalBiRead,
    summary="Indicadores JURÍDICOS do BI jurídico (Plano Mestre §15)",
)
async def get_legal_bi(
    period_from: datetime | None = Query(default=None),
    period_to: datetime | None = Query(default=None),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_relatorios_view),
    db: AsyncSession = Depends(get_db),
):
    service = LegalBiService(db, workspace_id)
    result = await service.get_legal_bi(period_from=period_from, period_to=period_to)
    return LegalBiRead.model_validate(result)


@router.get(
    "/bi/ia",
    response_model=AiBiRead,
    summary="Indicadores de IA do BI jurídico (Plano Mestre §15)",
)
async def get_ai_bi(
    period_from: datetime | None = Query(default=None),
    period_to: datetime | None = Query(default=None),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_relatorios_view),
    db: AsyncSession = Depends(get_db),
):
    service = LegalBiService(db, workspace_id)
    result = await service.get_ai_bi(period_from=period_from, period_to=period_to)
    return AiBiRead.model_validate(result)


@router.get(
    "/capacity",
    response_model=CapacityReportRead,
    summary="Capacidade por responsável/equipe/unidade — carga vs disponibilidade",
)
async def get_capacity(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_relatorios_view),
    db: AsyncSession = Depends(get_db),
):
    service = LegalCapacityService(db, workspace_id)
    result = await service.get_capacity()
    return CapacityReportRead.model_validate(result)
