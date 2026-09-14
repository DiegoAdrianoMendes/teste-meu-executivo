"""
Endpoints: Demanda jurídica canônica (Onda 2 — Fundação jurídica).
Ver app/models/legal_demand.py e app/services/legal_demand_service.py.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_workspace_id, get_db, require_permission
from app.models.legal_demand import LegalDemand, LegalDemandNature, LegalDemandPhase
from app.schemas.case_360 import (
    Case360Read,
    ComunicacoesRead,
    FinanceiroSubledgerRead,
    FinanceTotalsRead,
    OportunidadeRead,
    ProcessoRead,
    PublicacaoResumoRead,
    ResponsavelRead,
    TrabalhoDistribuidoRead,
)
from app.schemas.case_party import CasePartyCreate, CasePartyListRead, CasePartyRead
from app.schemas.case_strategy import CaseStrategyMatrixRead
from app.schemas.client import ClientRead
from app.schemas.conversation import ConversationRead
from app.schemas.document import DocumentRead
from app.schemas.exception_center import ExceptionCenterRead
from app.schemas.fee_arrangement import FeeArrangementRead
from app.schemas.finance import FinanceEntryRead
from app.schemas.legal_controladoria import (
    DistributionDecisionRead,
    LegalDemandSlaRead,
    SlaEscalationLogRead,
)
from app.schemas.legal_demand import (
    LegalDemandCreate,
    LegalDemandListRead,
    LegalDemandPhaseTransitionRequest,
    LegalDemandRead,
    LegalDemandUpdate,
)
from app.schemas.legal_review import (
    CitationVerificationRead,
    CitationVerificationRunRead,
    LegalReviewFindingRead,
    LegalReviewRunRead,
)
from app.schemas.timeline_event import TimelineEventRead
from app.services.case_360_service import Case360Result, Case360Service
from app.services.citation_verification_service import CitationVerificationService
from app.services.legal_demand_service import LegalDemandService
from app.services.legal_review_service import LegalReviewService

router = APIRouter(prefix="/legal/demands", tags=["Jurídico — Demandas (Onda 2)"])


def _demand_read(demand: LegalDemand) -> LegalDemandRead:
    return LegalDemandRead.model_validate(demand)


@router.post(
    "",
    response_model=LegalDemandRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar demanda jurídica — idempotente por external_ref",
)
async def create_legal_demand(
    payload: LegalDemandCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("legal", "manage")),
    db: AsyncSession = Depends(get_db),
):
    demand = await LegalDemandService(db).create(
        workspace_id, payload, actor_user_id=auth.user_id
    )
    await db.commit()
    await db.refresh(demand)
    return _demand_read(demand)


@router.get(
    "",
    response_model=LegalDemandListRead,
    summary="Listar demandas jurídicas do workspace",
)
async def list_legal_demands(
    nature: Optional[LegalDemandNature] = Query(default=None),
    phase: Optional[LegalDemandPhase] = Query(default=None),
    matter_id: Optional[UUID] = Query(default=None),
    client_id: Optional[UUID] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("legal", "view")),
    db: AsyncSession = Depends(get_db),
):
    demands, total = await LegalDemandService(db).list_demands(
        workspace_id,
        nature=nature,
        phase=phase,
        matter_id=matter_id,
        client_id=client_id,
        limit=limit,
        offset=offset,
    )
    return LegalDemandListRead(
        items=[_demand_read(demand) for demand in demands], total=total
    )


@router.get(
    "/{demand_id}",
    response_model=LegalDemandRead,
    summary="Detalhe de uma demanda jurídica",
)
async def get_legal_demand(
    demand_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("legal", "view")),
    db: AsyncSession = Depends(get_db),
):
    demand = await LegalDemandService(db).get_demand(workspace_id, demand_id)
    return _demand_read(demand)


@router.patch(
    "/{demand_id}",
    response_model=LegalDemandRead,
    summary="Atualizar campos mutáveis de uma demanda jurídica",
)
async def update_legal_demand(
    demand_id: UUID,
    payload: LegalDemandUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("legal", "manage")),
    db: AsyncSession = Depends(get_db),
):
    demand = await LegalDemandService(db).update(
        workspace_id, demand_id, payload, actor_user_id=auth.user_id
    )
    await db.commit()
    await db.refresh(demand)
    return _demand_read(demand)


@router.post(
    "/{demand_id}/transition",
    response_model=LegalDemandRead,
    summary="Transicionar a fase de uma demanda jurídica — idempotente por valor",
)
async def transition_legal_demand_phase(
    demand_id: UUID,
    payload: LegalDemandPhaseTransitionRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("legal", "manage")),
    db: AsyncSession = Depends(get_db),
):
    demand = await LegalDemandService(db).transition_phase(
        workspace_id,
        demand_id,
        payload.phase,
        reason=payload.reason,
        actor_user_id=auth.user_id,
    )
    await db.commit()
    await db.refresh(demand)
    return _demand_read(demand)


# ── Partes (Onda 5 — Caso 360) ───────────────────────────────────────────


@router.post(
    "/{demand_id}/parties",
    response_model=CasePartyRead,
    status_code=status.HTTP_201_CREATED,
    summary="Adicionar parte ao caso — polo, papel, qualificação",
)
async def add_case_party(
    demand_id: UUID,
    payload: CasePartyCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("legal", "manage")),
    db: AsyncSession = Depends(get_db),
):
    party = await Case360Service(db).add_party(
        workspace_id,
        demand_id,
        name=payload.name,
        pole=payload.pole,
        role=payload.role,
        qualification=payload.qualification,
        notes=payload.notes,
    )
    await db.commit()
    await db.refresh(party)
    return CasePartyRead.model_validate(party)


@router.get(
    "/{demand_id}/parties",
    response_model=CasePartyListRead,
    summary="Listar partes do caso (polo/papel/qualificação)",
)
async def list_case_parties(
    demand_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("legal", "view")),
    db: AsyncSession = Depends(get_db),
):
    parties = await Case360Service(db).list_parties(workspace_id, demand_id)
    return CasePartyListRead(items=[CasePartyRead.model_validate(party) for party in parties])


# ── Caso 360 (Onda 5 — visão agregada, Plano Mestre §5) ─────────────────


def _sla_read(demand: LegalDemand) -> LegalDemandSlaRead:
    # Mesmo mapeamento de app/api/v1/endpoints/legal_controladoria.py::_sla_read
    # (Onda 4) — duplicado aqui de propósito: o Caso 360 lê o mesmo dado já
    # persistido em LegalDemand, sem depender do módulo de controladoria.
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


def _case_360_read(result: Case360Result) -> Case360Read:
    demand = result.demand
    matter = result.matter

    oportunidade: Optional[OportunidadeRead] = None
    if matter is not None:
        oportunidade = OportunidadeRead(
            matter_id=matter.id,
            case_number=matter.case_number,
            column_id=matter.column_id,
            micro_status=matter.micro_status,
            priority=matter.priority,
            urgency=matter.urgency,
            classification=matter.classification,
            lead_source=matter.lead_source,
            claim_value=matter.claim_value,
            stars_rating=matter.stars_rating,
            card_order=matter.card_order,
        )

    responsavel = (
        ResponsavelRead.model_validate(result.responsible_user)
        if result.responsible_user is not None
        else None
    )

    processos = [
        ProcessoRead(
            id=process.id,
            numero_cnj=process.numero_cnj,
            tribunal=process.tribunal,
            status=process.status,
            last_movement_at=process.last_movement_at,
            publicacoes=[
                PublicacaoResumoRead.model_validate(publication)
                for publication in result.publications_by_process.get(process.id, [])
            ],
        )
        for process in result.processes
    ]

    trabalho_distribuido = TrabalhoDistribuidoRead(
        responsible_user=responsavel,
        last_decision=(
            DistributionDecisionRead.model_validate(result.distribution_last_decision)
            if result.distribution_last_decision is not None
            else None
        ),
        open_escalations=[
            SlaEscalationLogRead.model_validate(log) for log in result.open_sla_escalations
        ],
    )

    financeiro = FinanceiroSubledgerRead(
        entries=[FinanceEntryRead.model_validate(entry) for entry in result.finance_entries],
        totals=FinanceTotalsRead(
            total_receivable=result.finance_totals.total_receivable,
            total_paid=result.finance_totals.total_paid,
            total_pending=result.finance_totals.total_pending,
            total_overdue=result.finance_totals.total_overdue,
        ),
        fee_arrangements=[
            FeeArrangementRead.model_validate(arrangement)
            for arrangement in result.fee_arrangements
        ],
    )

    comunicacoes = ComunicacoesRead(
        total_count=result.conversations_total,
        recent=[ConversationRead.model_validate(item) for item in result.conversations_recent],
    )

    return Case360Read(
        generated_at=result.generated_at,
        demand_id=demand.id,
        workspace_id=demand.workspace_id,
        demanda_contratada=LegalDemandRead.model_validate(demand),
        oportunidade=oportunidade,
        cliente=ClientRead.model_validate(result.client),
        responsavel=responsavel,
        partes=[CasePartyRead.model_validate(party) for party in result.case_parties],
        processos=processos,
        prazos=_sla_read(demand),
        trabalho_distribuido=trabalho_distribuido,
        documentos=[DocumentRead.model_validate(document) for document in result.documents],
        financeiro=financeiro,
        comunicacoes=comunicacoes,
        estrategia=demand.strategy,
        provas=CaseStrategyMatrixRead.model_validate(result.strategy_matrix),
        excecoes_abertas=[
            ExceptionCenterRead.model_validate(exception) for exception in result.open_exceptions
        ],
        timeline=[TimelineEventRead.model_validate(event) for event in result.timeline],
    )


@router.get(
    "/{demand_id}/case-360",
    response_model=Case360Read,
    summary="Caso 360 — visão agregada da demanda (Onda 5, Plano Mestre §5)",
)
async def get_case_360(
    demand_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("legal", "view")),
    db: AsyncSession = Depends(get_db),
):
    result = await Case360Service(db).get_case_360(workspace_id, demand_id)
    return _case_360_read(result)


# ── Verificação de citações + revisão adversarial (Onda 6, §9/§3 item 20) ──


@router.post(
    "/{demand_id}/artifacts/{artifact_id}/verify-citations",
    response_model=CitationVerificationRunRead,
    summary="Conferir citações (súmula/tema/REsp/lei) de um artefato jurídico",
)
async def verify_artifact_citations(
    demand_id: UUID,
    artifact_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("legal", "manage")),
    db: AsyncSession = Depends(get_db),
):
    result = await CitationVerificationService(db).verify_citations(
        workspace_id, demand_id, artifact_id, actor_user_id=auth.user_id
    )
    await db.commit()
    return CitationVerificationRunRead(
        artifact_version_id=result.artifact_version_id,
        all_verified=result.all_verified,
        gate_open=result.gate_open,
        verifications=[
            CitationVerificationRead.model_validate(item) for item in result.verifications
        ],
    )


@router.post(
    "/{demand_id}/artifacts/{artifact_id}/review",
    response_model=LegalReviewRunRead,
    summary="Revisão adversarial de um artefato jurídico — achados por severidade",
)
async def review_artifact(
    demand_id: UUID,
    artifact_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("legal", "manage")),
    db: AsyncSession = Depends(get_db),
):
    result = await LegalReviewService(db).review(
        workspace_id, demand_id, artifact_id, actor_user_id=auth.user_id
    )
    await db.commit()
    return LegalReviewRunRead(
        artifact_version_id=result.artifact_version_id,
        is_protocolable=result.is_protocolable,
        journey_advanced=result.journey_advanced,
        findings=[LegalReviewFindingRead.model_validate(item) for item in result.findings],
        parecer=result.parecer,
        review_status=result.review_status,
        methodology_version=result.methodology_version,
    )
