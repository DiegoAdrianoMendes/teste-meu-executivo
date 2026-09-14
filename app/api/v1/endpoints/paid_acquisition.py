"""Endpoints: Aquisição paga integrada (Sprint F5).

Nenhuma rota aqui executa uma chamada real à Marketing API. Criação de
campanha sempre nasce como `ExternalActionIntent(status=awaiting_approval)`;
aprovar/rejeitar usa o fluxo genérico já existente em
`/api/v1/klm/external-intents/{id}/approve-local` e `/reject-local`.
"""
from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth_context, get_current_workspace_id, get_db
from app.core.deps import require_permission
from app.models.klm_operations import ExternalActionIntent
from app.schemas.paid_acquisition import (
    CampaignDraftRead,
    CHANNEL_LABELS,
    FunnelSummaryRead,
    PaidAcquisitionSyncStatus,
    PrepareCampaignDraftRequest,
)
from app.services.paid_acquisition_service import (
    PaidAcquisitionNotFoundError,
    PaidAcquisitionService,
    PaidAcquisitionValidationError,
)

router = APIRouter(prefix="/paid-acquisition", tags=["Aquisição Paga"])

# Não existe módulo RBAC "marketing" dedicado no catálogo do workspace
# (app.core.rbac); aquisição paga é uma extensão do módulo de integrações
# já usado por ExternalActionIntent/ads-manager, então reaproveitamos
# "integrations" (view/manage) para manter a checagem coerente com o padrão
# existente em vez de introduzir uma permissão nova sem consumidor.
require_marketing_view = require_permission("integrations", "view")
require_marketing_manage = require_permission("integrations", "manage")


def _to_draft_read(intent: ExternalActionIntent) -> CampaignDraftRead:
    channel = intent.payload.get("channel", "ctwa")
    return CampaignDraftRead(
        id=intent.id,
        workspace_id=intent.workspace_id,
        channel=channel,
        channel_label=intent.payload.get("channelLabel") or CHANNEL_LABELS.get(channel, channel),
        status=intent.status.value if hasattr(intent.status, "value") else str(intent.status),
        provider=intent.provider,
        marketing_api_payload=intent.payload.get("marketingApiPayload", {}),
        reason=intent.reason,
        requested_by_user_id=intent.requested_by_user_id,
        created_at=intent.created_at,
        updated_at=intent.updated_at,
    )


@router.get("/sync-status", response_model=PaidAcquisitionSyncStatus)
async def get_sync_status(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_marketing_view),
    db: AsyncSession = Depends(get_db),
) -> PaidAcquisitionSyncStatus:
    status_payload = await PaidAcquisitionService(db).sync_status(workspace_id)
    return PaidAcquisitionSyncStatus(**status_payload)


@router.post(
    "/campaigns",
    response_model=CampaignDraftRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_campaign_draft(
    payload: PrepareCampaignDraftRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_marketing_manage),
    db: AsyncSession = Depends(get_db),
) -> CampaignDraftRead:
    try:
        intent, _replayed = await PaidAcquisitionService(db).prepare_campaign_draft(
            workspace_id, auth.user_id, payload
        )
        await db.commit()
    except PaidAcquisitionValidationError as error:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "PAID-ACQ-422", "message": str(error)},
        ) from error
    return _to_draft_read(intent)


@router.get("/campaigns", response_model=list[CampaignDraftRead])
async def list_campaign_drafts(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_marketing_view),
    db: AsyncSession = Depends(get_db),
    status_filter: str | None = Query(default=None, alias="status"),
) -> list[CampaignDraftRead]:
    intents = await PaidAcquisitionService(db).list_campaign_drafts(
        workspace_id, status=status_filter
    )
    return [_to_draft_read(intent) for intent in intents]


@router.get("/campaigns/{draft_id}", response_model=CampaignDraftRead)
async def get_campaign_draft(
    draft_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_marketing_view),
    db: AsyncSession = Depends(get_db),
) -> CampaignDraftRead:
    try:
        intent = await PaidAcquisitionService(db).get_campaign_draft(workspace_id, draft_id)
    except PaidAcquisitionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PAID-ACQ-404", "message": str(error)},
        ) from error
    return _to_draft_read(intent)


@router.get("/funnel-summary", response_model=FunnelSummaryRead)
async def get_funnel_summary(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_marketing_view),
    db: AsyncSession = Depends(get_db),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
) -> FunnelSummaryRead:
    resolved_to = date_to or date.today()
    resolved_from = date_from or (resolved_to - timedelta(days=30))
    try:
        summary = await PaidAcquisitionService(db).campaign_funnel_summary(
            workspace_id, date_from=resolved_from, date_to=resolved_to
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "PAID-ACQ-422", "message": str(error)},
        ) from error
    return FunnelSummaryRead(**summary)
