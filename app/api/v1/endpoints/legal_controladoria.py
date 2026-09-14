"""
Endpoints: Controladoria jurídica — distribuição + SLA + painel diário
(Onda 4, Plano Mestre §8).

Sem frontend novo nesta onda: o painel só devolve dado agregado para a
tela existente (a UI de controladoria entra na Onda 8, quando o menu novo
entrar). Reusa `require_permission("legal", ...)` — mesmo módulo de
permissão de app/api/v1/endpoints/legal_demands.py, já concedido aos
papéis que operam o jurídico (ver app/core/rbac.py).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_workspace_id, get_db, require_permission
from app.models.legal_controladoria import DistributionTrigger
from app.schemas.legal_controladoria import (
    ControladoriaPainelRead,
    DeadlineWindowCountsRead,
    DistributionAssignRequest,
    DistributionDecisionListRead,
    DistributionDecisionRead,
    DistributionRuleCreate,
    DistributionRuleListRead,
    DistributionRuleRead,
    DistributionRuleUpdate,
    LegalDemandSlaRead,
    LegalDemandSlaUpdate,
    SlaEscalationLogListRead,
    SlaEscalationLogRead,
)
from app.services.legal_controladoria_panel_service import LegalControladoriaPanelService
from app.services.legal_distribution_service import (
    DistributionDemandNotFoundError,
    DistributionError,
    DistributionNoAvailableCandidateError,
    DistributionNoRuleMatchedError,
    DistributionReasonRequiredError,
    DistributionRuleNameConflictError,
    DistributionRuleNotFoundError,
    LegalDistributionService,
)
from app.services.legal_sla_service import (
    LegalSlaDemandNotFoundError,
    LegalSlaLogNotFoundError,
    LegalSlaService,
)

router = APIRouter(prefix="/legal/controladoria", tags=["Jurídico — Controladoria (Onda 4)"])

require_legal_view = require_permission("legal", "view")
require_legal_manage = require_permission("legal", "manage")


def _domain_error_to_http(error: DistributionError) -> HTTPException:
    if isinstance(error, DistributionRuleNotFoundError | DistributionDemandNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    if isinstance(error, DistributionRuleNameConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if isinstance(error, DistributionNoRuleMatchedError | DistributionNoAvailableCandidateError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if isinstance(error, DistributionReasonRequiredError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))


# ── Regras de distribuição ──────────────────────────────────────────────


@router.post(
    "/distribution-rules",
    response_model=DistributionRuleRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar regra de distribuição de trabalho jurídico",
)
async def create_distribution_rule(
    payload: DistributionRuleCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_legal_manage),
    db: AsyncSession = Depends(get_db),
):
    service = LegalDistributionService(db, workspace_id)
    try:
        rule = await service.create_rule(
            name=payload.name,
            description=payload.description,
            active=payload.active,
            priority=payload.priority,
            match_natures=payload.match_natures,
            match_areas=payload.match_areas,
            match_client_ids=[str(item) for item in payload.match_client_ids],
            match_risks=payload.match_risks,
            match_units=payload.match_units,
            match_teams=payload.match_teams,
            strategy=payload.strategy,
            candidate_user_ids=[str(item) for item in payload.candidate_user_ids],
            max_active_per_candidate=payload.max_active_per_candidate,
            respect_availability=payload.respect_availability,
            created_by_user_id=auth.user_id,
        )
    except DistributionError as error:
        raise _domain_error_to_http(error) from error
    await db.commit()
    await db.refresh(rule)
    return DistributionRuleRead.model_validate(rule)


@router.get(
    "/distribution-rules",
    response_model=DistributionRuleListRead,
    summary="Listar regras de distribuição do workspace",
)
async def list_distribution_rules(
    active_only: bool = Query(default=False),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_legal_view),
    db: AsyncSession = Depends(get_db),
):
    service = LegalDistributionService(db, workspace_id)
    rules = await service.list_rules(active_only=active_only)
    items = [DistributionRuleRead.model_validate(rule) for rule in rules]
    return DistributionRuleListRead(items=items, total=len(items))


@router.patch(
    "/distribution-rules/{rule_id}",
    response_model=DistributionRuleRead,
    summary="Atualizar regra de distribuição",
)
async def update_distribution_rule(
    rule_id: UUID,
    payload: DistributionRuleUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_legal_manage),
    db: AsyncSession = Depends(get_db),
):
    service = LegalDistributionService(db, workspace_id)
    fields = payload.model_dump(exclude_unset=True)
    if "match_client_ids" in fields and fields["match_client_ids"] is not None:
        fields["match_client_ids"] = [str(item) for item in fields["match_client_ids"]]
    if "candidate_user_ids" in fields and fields["candidate_user_ids"] is not None:
        fields["candidate_user_ids"] = [str(item) for item in fields["candidate_user_ids"]]
    try:
        rule = await service.update_rule(rule_id, **fields)
    except DistributionError as error:
        raise _domain_error_to_http(error) from error
    await db.commit()
    await db.refresh(rule)
    return DistributionRuleRead.model_validate(rule)


@router.delete(
    "/distribution-rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remover regra de distribuição",
)
async def delete_distribution_rule(
    rule_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_legal_manage),
    db: AsyncSession = Depends(get_db),
):
    service = LegalDistributionService(db, workspace_id)
    try:
        await service.delete_rule(rule_id)
    except DistributionError as error:
        raise _domain_error_to_http(error) from error
    await db.commit()


# ── Atribuição de trabalho jurídico ─────────────────────────────────────


@router.post(
    "/demands/{legal_demand_id}/distribution/assign",
    response_model=DistributionDecisionRead,
    summary="(Re)distribuir uma demanda — automático por regra ou manual com motivo",
)
async def assign_legal_demand(
    legal_demand_id: UUID,
    payload: DistributionAssignRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_legal_manage),
    db: AsyncSession = Depends(get_db),
):
    service = LegalDistributionService(db, workspace_id)
    trigger = (
        DistributionTrigger.redistribuicao_manual
        if payload.forced_user_id is not None
        else DistributionTrigger.regra_automatica
    )
    try:
        decision = await service.assign(
            legal_demand_id,
            trigger=trigger,
            forced_user_id=payload.forced_user_id,
            reason=payload.reason,
            decided_by_user_id=auth.user_id,
        )
    except DistributionError as error:
        raise _domain_error_to_http(error) from error
    await db.commit()
    await db.refresh(decision)
    return DistributionDecisionRead.model_validate(decision)


@router.get(
    "/demands/{legal_demand_id}/distribution/history",
    response_model=DistributionDecisionListRead,
    summary="Histórico de (re)distribuição de uma demanda",
)
async def get_distribution_history(
    legal_demand_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_legal_view),
    db: AsyncSession = Depends(get_db),
):
    service = LegalDistributionService(db, workspace_id)
    decisions = await service.list_history(legal_demand_id)
    return DistributionDecisionListRead(
        items=[DistributionDecisionRead.model_validate(item) for item in decisions]
    )


# ── SLA em camadas ───────────────────────────────────────────────────────


@router.patch(
    "/demands/{legal_demand_id}/sla",
    response_model=LegalDemandSlaRead,
    summary="Definir/atualizar as camadas de SLA de uma demanda e recalcular o prazo efetivo",
)
async def update_legal_demand_sla(
    legal_demand_id: UUID,
    payload: LegalDemandSlaUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_legal_manage),
    db: AsyncSession = Depends(get_db),
):
    sla_service = LegalSlaService(db, workspace_id)
    try:
        demand = await sla_service.get_demand(legal_demand_id)
    except LegalSlaDemandNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(demand, field, value)
    await db.flush()
    demand = await sla_service.recompute(legal_demand_id)
    await db.commit()
    await db.refresh(demand)
    return _sla_read(demand)


@router.get(
    "/demands/{legal_demand_id}/sla",
    response_model=LegalDemandSlaRead,
    summary="Camadas de SLA e prazo efetivo calculado de uma demanda",
)
async def get_legal_demand_sla(
    legal_demand_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_legal_view),
    db: AsyncSession = Depends(get_db),
):
    sla_service = LegalSlaService(db, workspace_id)
    try:
        demand = await sla_service.get_demand(legal_demand_id)
    except LegalSlaDemandNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return _sla_read(demand)


@router.get(
    "/demands/{legal_demand_id}/sla/escalations",
    response_model=SlaEscalationLogListRead,
    summary="Histórico de escalonamento de SLA de uma demanda",
)
async def list_sla_escalations(
    legal_demand_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_legal_view),
    db: AsyncSession = Depends(get_db),
):
    sla_service = LegalSlaService(db, workspace_id)
    logs = await sla_service.list_escalations(legal_demand_id)
    return SlaEscalationLogListRead(
        items=[SlaEscalationLogRead.model_validate(item) for item in logs]
    )


@router.post(
    "/sla-escalations/{log_id}/acknowledge",
    response_model=SlaEscalationLogRead,
    summary="Confirmar leitura de um escalonamento de SLA",
)
async def acknowledge_sla_escalation(
    log_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_legal_view),
    db: AsyncSession = Depends(get_db),
):
    sla_service = LegalSlaService(db, workspace_id)
    try:
        log = await sla_service.acknowledge(log_id, acknowledged_by_user_id=auth.user_id)
    except LegalSlaLogNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await db.commit()
    await db.refresh(log)
    return SlaEscalationLogRead.model_validate(log)


@router.post(
    "/sla/recompute",
    summary="Varredura manual — recalcula SLA e escalona onde necessário (todo o workspace)",
)
async def recompute_workspace_sla(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_legal_manage),
    db: AsyncSession = Depends(get_db),
):
    sla_service = LegalSlaService(db, workspace_id)
    touched = await sla_service.recompute_due()
    await db.commit()
    return {"touched": touched}


def _sla_read(demand) -> LegalDemandSlaRead:
    return LegalDemandSlaRead(
        legal_demand_id=demand.id,
        prazo_legal_at=demand.prazo_legal_at,
        prazo_fatal_at=demand.prazo_fatal_at,
        prazo_interno_at=demand.prazo_interno_at,
        sla_cliente_at=demand.sla_cliente_at,
        prazo_playbook_at=demand.prazo_playbook_at,
        margem_seguranca_dias=demand.margem_seguranca_dias,
        sla_effective_at=demand.sla_effective_at,
        sla_effective_layer=demand.sla_effective_layer,
        sla_escalation_level=demand.sla_escalation_level,
    )


# ── Painel diário ────────────────────────────────────────────────────────


@router.get(
    "/painel",
    response_model=ControladoriaPainelRead,
    summary="Painel diário agregado da controladoria jurídica",
)
async def get_controladoria_painel(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_legal_view),
    db: AsyncSession = Depends(get_db),
):
    service = LegalControladoriaPanelService(db, workspace_id)
    painel = await service.get_panel()
    return ControladoriaPainelRead(
        generated_at=painel.generated_at,
        publicacoes_nao_tratadas=painel.publicacoes_nao_tratadas,
        prazos_sugeridos=painel.prazos_sugeridos,
        prazos_fatais=painel.prazos_fatais,
        prazos_por_janela=DeadlineWindowCountsRead(
            d7=painel.prazos_por_janela.d7,
            d3=painel.prazos_por_janela.d3,
            d1=painel.prazos_por_janela.d1,
            d0=painel.prazos_por_janela.d0,
            atrasados=painel.prazos_por_janela.atrasados,
        ),
        tarefas_sem_responsavel=painel.tarefas_sem_responsavel,
        processos_sem_movimentacao=painel.processos_sem_movimentacao,
        audiencias_proximas=painel.audiencias_proximas,
        documentos_pendentes=painel.documentos_pendentes,
        minutas_bloqueadas=painel.minutas_bloqueadas,
        excecoes_de_agentes=painel.excecoes_de_agentes,
        clientes_sem_atualizacao=painel.clientes_sem_atualizacao,
    )
