"""
Endpoints do Growth CRM da plataforma (funil de aquisição B2B dos
escritórios-clientes). Acesso exclusivo da Blackbox, governado pelo módulo
``sales``: consultas exigem ``view`` e mutações exigem ``manage``.

"Casa de ferreiro, espeto de pau": o Super Admin ganha aqui o mesmo tipo de
funil que o KLM Hub vende para os escritórios, aplicado à própria captação
de escritórios como clientes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.platform_audit import (
    record_canonical_platform_mutation,
    snapshot_platform_growth_activity,
    snapshot_platform_growth_prospect,
)
from app.core.platform_access import require_platform_permission
from app.models.user import User
from app.models.workspace import WorkspaceSetupMode
from app.schemas.email import CanonicalEmail
from app.services.platform_growth_service import (
    GrowthInvalidStageTransitionError,
    GrowthProspectNotFoundError,
    PlatformGrowthService,
)

router = APIRouter(prefix="/platform/growth", tags=["Platform Growth CRM"])
_sales_view = require_platform_permission("sales", "view")
_sales_manage = require_platform_permission("sales", "manage")


def _raise_growth_error(error: Exception) -> None:
    if isinstance(error, GrowthProspectNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "GROWTH-PROSPECT-NOT-FOUND", "message": str(error)},
        ) from error
    if isinstance(error, GrowthInvalidStageTransitionError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "GROWTH-INVALID-TRANSITION", "message": str(error)},
        ) from error
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": "GROWTH-INVALID", "message": str(error)},
    ) from error


# ── Payloads ─────────────────────────────────────────────────────────────


class GrowthProspectCreate(BaseModel):
    nome_escritorio: str = Field(..., alias="nomeEscritorio", min_length=1, max_length=200)
    contato_nome: Optional[str] = Field(default=None, alias="contatoNome")
    contato_email: Optional[str] = Field(default=None, alias="contatoEmail")
    contato_telefone: Optional[str] = Field(default=None, alias="contatoTelefone")
    cidade: Optional[str] = None
    uf: Optional[str] = Field(default=None, max_length=2)
    tamanho_estimado: Optional[str] = Field(default=None, alias="tamanhoEstimado")
    origem: str = "outro"
    valor_estimado_cents: int = Field(default=0, alias="valorEstimadoCents", ge=0)
    owner_user_id: Optional[UUID] = Field(default=None, alias="ownerUserId")
    notas: Optional[str] = None
    score: Optional[int] = Field(default=None, ge=0, le=100)
    next_action: Optional[str] = Field(default=None, alias="nextAction", max_length=300)
    next_action_at: Optional[datetime] = Field(default=None, alias="nextActionAt")
    campaign: Optional[str] = Field(default=None, max_length=200)
    utm_source: Optional[str] = Field(default=None, alias="utmSource", max_length=200)
    utm_medium: Optional[str] = Field(default=None, alias="utmMedium", max_length=200)
    utm_campaign: Optional[str] = Field(default=None, alias="utmCampaign", max_length=200)
    proposal_status: Optional[Literal["draft", "sent", "accepted", "rejected", "expired"]] = Field(
        default=None, alias="proposalStatus"
    )
    proposal_reference: Optional[str] = Field(default=None, alias="proposalReference", max_length=300)
    proposal_value_cents: Optional[int] = Field(default=None, alias="proposalValueCents", ge=0)

    class Config:
        populate_by_name = True


class GrowthProspectUpdate(BaseModel):
    nome_escritorio: Optional[str] = Field(default=None, alias="nomeEscritorio")
    contato_nome: Optional[str] = Field(default=None, alias="contatoNome")
    contato_email: Optional[str] = Field(default=None, alias="contatoEmail")
    contato_telefone: Optional[str] = Field(default=None, alias="contatoTelefone")
    cidade: Optional[str] = None
    uf: Optional[str] = Field(default=None, max_length=2)
    tamanho_estimado: Optional[str] = Field(default=None, alias="tamanhoEstimado")
    origem: Optional[str] = None
    valor_estimado_cents: Optional[int] = Field(default=None, alias="valorEstimadoCents", ge=0)
    owner_user_id: Optional[UUID] = Field(default=None, alias="ownerUserId")
    notas: Optional[str] = None
    workspace_id: Optional[UUID] = Field(default=None, alias="workspaceId")
    score: Optional[int] = Field(default=None, ge=0, le=100)
    next_action: Optional[str] = Field(default=None, alias="nextAction", max_length=300)
    next_action_at: Optional[datetime] = Field(default=None, alias="nextActionAt")
    campaign: Optional[str] = Field(default=None, max_length=200)
    utm_source: Optional[str] = Field(default=None, alias="utmSource", max_length=200)
    utm_medium: Optional[str] = Field(default=None, alias="utmMedium", max_length=200)
    utm_campaign: Optional[str] = Field(default=None, alias="utmCampaign", max_length=200)
    proposal_status: Optional[Literal["draft", "sent", "accepted", "rejected", "expired"]] = Field(
        default=None, alias="proposalStatus"
    )
    proposal_reference: Optional[str] = Field(default=None, alias="proposalReference", max_length=300)
    proposal_value_cents: Optional[int] = Field(default=None, alias="proposalValueCents", ge=0)

    class Config:
        populate_by_name = True


class GrowthStageMoveRequest(BaseModel):
    to_estagio: str = Field(..., alias="toEstagio")
    motivo: str = ""

    class Config:
        populate_by_name = True


class GrowthActivityCreate(BaseModel):
    tipo: str
    descricao: str = ""

    class Config:
        populate_by_name = True


class GrowthProspectConvertRequest(BaseModel):
    workspace_name: str = Field(..., alias="workspaceName", min_length=2, max_length=200)
    slug: Optional[str] = Field(default=None, max_length=120)
    owner_name: str = Field(..., alias="ownerName", min_length=2, max_length=200)
    owner_email: CanonicalEmail = Field(..., alias="ownerEmail")
    plan_key: str = Field(default="pro", alias="planKey")
    setup_mode: WorkspaceSetupMode = Field(default=WorkspaceSetupMode.assisted, alias="setupMode")

    class Config:
        populate_by_name = True


# ── Endpoints ────────────────────────────────────────────────────────────


@router.get(
    "/assignees",
    summary="Responsáveis disponíveis para o pipeline comercial",
    deprecated=True,
)
async def list_sales_assignees(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_sales_view),
) -> List[Dict[str, str]]:
    return await PlatformGrowthService(db).list_sales_assignees()


@router.get("/pipeline", summary="Kanban do funil: prospects agrupados por estágio")
async def get_pipeline(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_sales_view),
) -> Dict[str, List[Dict[str, Any]]]:
    service = PlatformGrowthService(db)
    board = await service.get_pipeline()
    return {
        stage: [service.serialize_prospect(prospect) for prospect in prospects]
        for stage, prospects in board.items()
    }


@router.get("/funnel-metrics", summary="Métricas do funil: conversão, win rate, pipeline")
async def get_funnel_metrics(
    period_days: int = Query(default=90, ge=1, le=730, alias="periodDays"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_sales_view),
) -> Dict[str, Any]:
    return await PlatformGrowthService(db).get_funnel_metrics(period_days=period_days)


@router.get("/prospects", summary="Listar prospects com filtros e busca")
async def list_prospects(
    estagio: Optional[str] = Query(default=None),
    origem: Optional[str] = Query(default=None),
    owner_user_id: Optional[UUID] = Query(default=None, alias="ownerUserId"),
    search: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_sales_view),
) -> Dict[str, Any]:
    service = PlatformGrowthService(db)
    result = await service.list_prospects(
        estagio=estagio,
        origem=origem,
        owner_user_id=owner_user_id,
        search=search,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [service.serialize_prospect(item) for item in result["items"]],
        "count": result["count"],
        "total": result["count"],
        "limit": result["limit"],
        "offset": result["offset"],
        "hasMore": result["offset"] + len(result["items"]) < result["count"],
    }


@router.get("/prospects/{prospect_id}", summary="Detalhe do prospect + histórico")
async def get_prospect(
    prospect_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_sales_view),
) -> Dict[str, Any]:
    service = PlatformGrowthService(db)
    try:
        prospect = await service.get_prospect(prospect_id)
    except GrowthProspectNotFoundError as error:
        _raise_growth_error(error)
    return service.serialize_prospect(prospect, include_history=True)


@router.post(
    "/prospects",
    status_code=status.HTTP_201_CREATED,
    summary="Criar prospect (escritório-alvo)",
)
async def create_prospect(
    payload: GrowthProspectCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_sales_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> Dict[str, Any]:
    service = PlatformGrowthService(db)
    try:
        prospect = await service.create_prospect(
            nome_escritorio=payload.nome_escritorio,
            contato_nome=payload.contato_nome,
            contato_email=payload.contato_email,
            contato_telefone=payload.contato_telefone,
            cidade=payload.cidade,
            uf=payload.uf,
            tamanho_estimado=payload.tamanho_estimado,
            origem=payload.origem,
            valor_estimado_cents=payload.valor_estimado_cents,
            owner_user_id=payload.owner_user_id,
            notas=payload.notas,
            commercial_metadata={
                "score": payload.score,
                "next_action": payload.next_action,
                "next_action_at": (
                    payload.next_action_at.replace(tzinfo=timezone.utc).isoformat()
                    if payload.next_action_at and payload.next_action_at.tzinfo is None
                    else payload.next_action_at.isoformat()
                    if payload.next_action_at
                    else None
                ),
                "campaign": payload.campaign,
                "utm_source": payload.utm_source,
                "utm_medium": payload.utm_medium,
                "utm_campaign": payload.utm_campaign,
                "proposal_status": payload.proposal_status,
                "proposal_reference": payload.proposal_reference,
                "proposal_value_cents": payload.proposal_value_cents,
            },
            actor=actor,
        )
    except ValueError as error:
        _raise_growth_error(error)
    after = await snapshot_platform_growth_prospect(db, prospect.id)
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=None,
        after_snapshot=after,
        module="sales",
        action="create_growth_prospect",
        reason="Criação de lead no CRM comercial da plataforma.",
        reason_source="system",
    )
    return service.serialize_prospect(prospect)


@router.patch("/prospects/{prospect_id}", summary="Atualizar campos do prospect")
async def update_prospect(
    prospect_id: UUID,
    payload: GrowthProspectUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_sales_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> Dict[str, Any]:
    service = PlatformGrowthService(db)
    try:
        before = await snapshot_platform_growth_prospect(
            db, prospect_id, for_update=True
        )
        prospect = await service.update_prospect(
            prospect_id,
            fields=payload.model_dump(by_alias=False, exclude_unset=True),
        )
    except (GrowthProspectNotFoundError, ValueError) as error:
        _raise_growth_error(error)
    after = await snapshot_platform_growth_prospect(db, prospect_id)
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=after,
        module="sales",
        action="update_growth_prospect",
        reason="Atualização administrativa do lead comercial.",
        reason_source="system",
    )
    return service.serialize_prospect(prospect)


@router.patch("/prospects/{prospect_id}/stage", summary="Mover estágio do funil (auditado)")
async def move_prospect_stage(
    prospect_id: UUID,
    payload: GrowthStageMoveRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_sales_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> Dict[str, Any]:
    service = PlatformGrowthService(db)
    try:
        before = await snapshot_platform_growth_prospect(
            db, prospect_id, for_update=True
        )
        prospect = await service.move_stage(
            prospect_id, to_estagio=payload.to_estagio, motivo=payload.motivo, actor=actor
        )
    except (GrowthProspectNotFoundError, GrowthInvalidStageTransitionError) as error:
        _raise_growth_error(error)
    after = await snapshot_platform_growth_prospect(db, prospect_id)
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=after,
        module="sales",
        action="move_growth_prospect_stage",
        reason=payload.motivo or "Movimentação operacional do estágio comercial.",
        reason_source="user" if payload.motivo else "system",
    )
    return service.serialize_prospect(prospect)


@router.post(
    "/prospects/{prospect_id}/activities",
    status_code=status.HTTP_201_CREATED,
    summary="Registrar um toque/atividade com o prospect",
)
async def create_activity(
    prospect_id: UUID,
    payload: GrowthActivityCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_sales_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> Dict[str, Any]:
    service = PlatformGrowthService(db)
    try:
        activity = await service.add_activity(
            prospect_id, tipo=payload.tipo, descricao=payload.descricao, actor=actor
        )
    except (GrowthProspectNotFoundError, GrowthInvalidStageTransitionError) as error:
        _raise_growth_error(error)
    after = await snapshot_platform_growth_activity(db, activity.id)
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=None,
        after_snapshot=after,
        module="sales",
        action="create_growth_activity",
        reason=(
            payload.descricao
            if len(payload.descricao.strip()) >= 8
            else "Registro de atividade no CRM comercial da plataforma."
        ),
        reason_source="user" if len(payload.descricao.strip()) >= 8 else "system",
    )
    return service.serialize_activity(activity)


@router.post(
    "/prospects/{prospect_id}/convert",
    status_code=status.HTTP_201_CREATED,
    summary="Converter prospect em conta de forma transacional",
)
async def convert_prospect_to_workspace(
    prospect_id: UUID,
    payload: GrowthProspectConvertRequest,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=200),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_sales_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> Dict[str, Any]:
    try:
        before = await snapshot_platform_growth_prospect(
            db, prospect_id, for_update=True
        )
        result = await PlatformGrowthService(db).convert_to_workspace(
            prospect_id,
            workspace_name=payload.workspace_name,
            slug=payload.slug,
            owner_name=payload.owner_name,
            owner_email=str(payload.owner_email),
            plan_key=payload.plan_key,
            setup_mode=payload.setup_mode,
            actor=actor,
            idempotency_key=idempotency_key,
        )
        after = await snapshot_platform_growth_prospect(db, prospect_id)
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=after,
            module="sales",
            action="convert_growth_prospect",
            reason=f"Conversão do lead em conta no plano {payload.plan_key}.",
            reason_source="system",
        )
        return result
    except (GrowthProspectNotFoundError, ValueError) as error:
        await db.rollback()
        _raise_growth_error(error)


__all__ = ["router"]
