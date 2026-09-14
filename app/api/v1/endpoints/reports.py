"""
Endpoint: Reports
Métricas e KPIs para o painel de relatórios.
"""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_workspace_id, get_db, require_permission
from app.schemas.reports import KPIAggregateRead, ReportsSummary
from app.services.reports_service import ReportsService

router = APIRouter(prefix="/reports", tags=["Relatórios"])


@router.get("/metrics", response_model=ReportsSummary, summary="KPIs e métricas do dashboard")
async def get_metrics(
    workspace_id = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("relatorios", "view")),
    db: AsyncSession = Depends(get_db),
):
    """Retorna todos os KPIs e dados de gráficos para o painel de relatórios."""
    service = ReportsService(db)
    return await service.get_summary(workspace_id=workspace_id)


@router.get("/kpi-aggregate", response_model=KPIAggregateRead, summary="KPIs agregados por domínio")
async def get_kpi_aggregate(
    workspace_id=Depends(get_current_workspace_id),
    _: object = Depends(require_permission("relatorios", "view")),
    db: AsyncSession = Depends(get_db),
):
    service = ReportsService(db)
    return await service.get_kpi_aggregate(workspace_id=workspace_id)


@router.get("/metrics/live", summary="KPIs em tempo real do workspace (dashboard)")
async def get_workspace_metrics(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("relatorios", "view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Métricas do workspace para o Dashboard.
    Retorna KPIs em tempo real.
    """
    from sqlalchemy import func, select, and_
    from app.models.matter import Matter
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    # Total de leads na semana
    leads_week_result = await db.execute(
        select(func.count(Matter.id)).where(
            and_(Matter.workspace_id == workspace_id, Matter.created_at >= week_ago, Matter.deleted_at.is_(None))
        )
    )
    leads_this_week = leads_week_result.scalar() or 0

    # Leads por coluna (pipeline)
    columns_result = await db.execute(
        select(Matter.column_id, func.count(Matter.id)).where(
            and_(Matter.workspace_id == workspace_id, Matter.deleted_at.is_(None))
        ).group_by(Matter.column_id)
    )
    column_counts = {row[0]: row[1] for row in columns_result.fetchall()}

    # Taxa de conversão (entrada → proposta_fechamento) no último mês
    total_leads = await db.execute(
        select(func.count(Matter.id)).where(
            and_(Matter.workspace_id == workspace_id, Matter.created_at >= month_ago, Matter.deleted_at.is_(None))
        )
    )
    total_count = total_leads.scalar() or 1

    converted = await db.execute(
        select(func.count(Matter.id)).where(
            and_(
                Matter.workspace_id == workspace_id,
                Matter.created_at >= month_ago,
                Matter.deleted_at.is_(None),
                Matter.column_id.in_(["proposta_fechamento", "aprovacao_pendente", "assinado"]),
            )
        )
    )
    converted_count = converted.scalar() or 0
    conversion_rate = round((converted_count / max(total_count, 1)) * 100, 1)

    # Taxa de propostas aceitas (assinado / proposta_fechamento+aprovacao_pendente+assinado)
    proposal_total = await db.execute(
        select(func.count(Matter.id)).where(
            and_(
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
                Matter.column_id.in_(["proposta_fechamento", "aprovacao_pendente", "assinado"]),
            )
        )
    )
    proposal_total_count = proposal_total.scalar() or 1

    signed = column_counts.get("assinado", 0)
    proposals_accepted_rate = round((signed / max(proposal_total_count, 1)) * 100, 1)

    # Valor total em negociação
    pipeline_value_result = await db.execute(
        select(func.coalesce(func.sum(Matter.claim_value), 0)).where(
            and_(
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
                Matter.column_id.notin_(["assinado", "migrar_sistema"]),
            )
        )
    )
    pipeline_value = float(pipeline_value_result.scalar() or 0)

    return {
        "proposals_accepted_rate": proposals_accepted_rate,
        "conversion_rate": conversion_rate,
        "leads_this_week": leads_this_week,
        "pipeline_value": pipeline_value,
        "top_column_counts": column_counts,
        "period": "last_30_days",
        "updated_at": now.isoformat(),
    }
