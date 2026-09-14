"""Native tenant-scoped conversation response metrics API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_workspace_id, get_db, require_permission
from app.schemas.conversation_response_metrics import (
    ConversationResponseFilterPreset,
    ConversationResponseMetricsRead,
    ConversationResponsePeriod,
    ConversationResponseSlaStatus,
    ConversationResponseSort,
    ConversationResponseSortDirection,
    ConversationResponseState,
)
from app.services.conversation_response_metrics_service import (
    ConversationResponseMetricsService,
)

router = APIRouter(prefix="/klm/omnichannel", tags=["KLM Omnichannel Metrics"])


@router.get(
    "/response-metrics",
    response_model=ConversationResponseMetricsRead,
    summary="Tempos de resposta e KPIs omnichannel por conta, equipe e atendente",
)
async def conversation_response_metrics(
    period: ConversationResponsePeriod = Query(
        default=ConversationResponsePeriod.all_time
    ),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    preset: ConversationResponseFilterPreset = Query(
        default=ConversationResponseFilterPreset.all
    ),
    attendant_ids: Annotated[
        list[UUID] | None,
        Query(alias="attendant_id"),
    ] = None,
    team_ids: Annotated[
        list[UUID] | None,
        Query(alias="team_id"),
    ] = None,
    response_statuses: Annotated[
        list[ConversationResponseState] | None,
        Query(alias="status"),
    ] = None,
    sla_statuses: Annotated[
        list[ConversationResponseSlaStatus] | None,
        Query(alias="sla"),
    ] = None,
    sort_by: ConversationResponseSort = Query(
        default=ConversationResponseSort.duration
    ),
    sort_direction: ConversationResponseSortDirection = Query(
        default=ConversationResponseSortDirection.desc
    ),
    offset: int = Query(default=0, ge=0, le=10_000),
    limit: int = Query(default=100, ge=1, le=500),
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> ConversationResponseMetricsRead:
    try:
        return await ConversationResponseMetricsService(db).calculate(
            workspace_id,
            period=period,
            date_from=date_from,
            date_to=date_to,
            preset=preset,
            attendant_ids=attendant_ids or (),
            team_ids=team_ids or (),
            statuses=response_statuses or (),
            sla_statuses=sla_statuses or (),
            sort_by=sort_by,
            sort_direction=sort_direction,
            offset=offset,
            limit=limit,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_RESPONSE_METRICS_FILTER", "message": str(error)},
        ) from error
