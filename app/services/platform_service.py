"""
Service: Platform — KPIs do SaaS para o super-admin (dono da plataforma).

Métricas disponíveis:
  - MRR (Monthly Recurring Revenue) e ARR
  - Churn mensal e anual
  - CAC (por plataforma de anúncio ou estimativa)
  - LTV estimado
  - Workspaces: total, ativas, trial, churn, novas no mês
  - Token usage: custo total, custo por workspace, custo por modelo
  - AWS cost: via Cost Explorer (real) ou estimativa (fallback)
  - Saúde por workspace: score 0–100

Este service roda apenas para usuários com is_platform_admin=True.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.identity_surface import assert_workspace_identity
from app.core.klm_runtime import aws_external_access_allowed
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.core.security import hash_password_async
from app.core.workspace_access_packages import get_workspace_access_package
from app.models.agent_run import AgentRun
from app.models.auth_security import AuthSecurityEvent
from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    PlatformModelUsageLedger,
    PlatformTokenUsage,
    PlatformTokenUsageByTask,
    Subscription,
    SubscriptionStatus,
)
from app.models.commercial_catalog import CommercialOffer, WorkspaceCommercialContract
from app.models.conversion_feedback import AdsConversionFeedbackRecord
from app.models.entitlement import EntitlementKey, PlanEntitlement
from app.models.identity import UserSession, WorkspaceInvitation
from app.models.klm_operations import AcquisitionEvent
from app.models.matter import Matter
from app.models.user import AgentType, User, UserRole
from app.models.whatsapp_campaign import (
    CampaignRecipientStatus,
    CampaignStatus,
    MetaTemplateStatus,
    WhatsAppCampaign,
    WhatsAppCampaignDeliveryEvent,
    WhatsAppCampaignRecipient,
    WhatsAppMetaTemplate,
    WhatsAppSuppressionEntry,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.schemas.workspace import TeamMemberCreate, WorkspaceCreate, WorkspaceUpdate
from app.services.commercial_catalog_service import canonical_sku_for_legacy_plan_key
from app.services.commercial_legacy_bridge_service import (
    CanonicalContractCommand,
    canonical_sku,
)
from app.services.identity_lifecycle_service import IdentityLifecycleService
from app.services.local_billing_service import LocalBillingService
from app.services.workspace_service import WorkspaceService

_META_ERROR_CODE_RE = re.compile(r"^\[(\d+)\]\s*(.*)$")


class InvalidPlatformPlanKeyError(ValueError):
    """Raised when a legacy plan route does not identify a known plan."""


class LegacyPlanEditorDisabledError(ValueError):
    """Raised when a legacy write would bypass the canonical plan catalog."""


class PlatformService:
    """Service para KPIs e gestão da plataforma (super-admin)."""

    PLAN_KEY_TO_TIER = {
        "basic": PlanTier.starter,
        "start": PlanTier.starter,
        "crm_start": PlanTier.starter,
        "pro": PlanTier.pro,
        "advanced": PlanTier.pro,
        "crm_advanced": PlanTier.pro,
        "scale": PlanTier.business,
        "evolution": PlanTier.business,
        "crm_evolution": PlanTier.business,
    }
    PLAN_KEY_TO_LEGACY_DEFAULT = {
        "start": "basic",
        "crm_start": "basic",
        "advanced": "pro",
        "crm_advanced": "pro",
        "evolution": "scale",
        "crm_evolution": "scale",
    }

    LIMIT_ENTITLEMENTS = {
        "maxTokensPerMonth": EntitlementKey.tokens,
        "maxAiExecutionsPerMonth": EntitlementKey.ai_executions,
        "maxAiConversationsPerMonth": EntitlementKey.ai_conversations,
        "maxAudioMinutesPerMonth": EntitlementKey.audio_minutes,
        "maxStorageBytes": EntitlementKey.storage_bytes,
        "maxFiles": EntitlementKey.files,
        "maxUsers": EntitlementKey.users,
        "maxConnectedWhatsapps": EntitlementKey.whatsapp_numbers,
        "maxAiAgents": EntitlementKey.agents,
        "maxLegalAreas": EntitlementKey.legal_areas,
        "automationLimit": EntitlementKey.automations,
        # Módulo processual contratável à parte (add-on, não core) — 0 nos
        # planos base; vendido/ligado por override de conta
        # (WorkspaceEntitlementOverride) ou plano dedicado.
        "maxMonitoredProcesses": EntitlementKey.monitored_processes,
    }
    PLATFORM_FEATURE_ENTITLEMENTS = {
        "digitalSignature": EntitlementKey.digital_signature,
        "calendar": EntitlementKey.calendar,
        "email": EntitlementKey.email,
        "webhooks": EntitlementKey.webhooks,
        "advancedAudit": EntitlementKey.advanced_audit,
        "successManager": EntitlementKey.success_manager,
    }

    PLAN_DEFAULTS: Dict[str, Dict[str, Any]] = {
        "basic": {
            "name": "Basic",
            "description": "Entrada guiada para escritórios enxutos com automação essencial, CRM e um canal principal de atendimento.",
            "recommended": False,
            "limits": {
                "maxTokensPerMonth": 500_000,
                "maxAiExecutionsPerMonth": 500,
                "maxUsers": 3,
                "maxWorkspaces": 1,
                "maxConnectedWhatsapps": 1,
                "maxAiAgents": 6,
                "maxLegalAreas": 1,
                "maxAiConversationsPerMonth": 125,
                "maxAudioMinutesPerMonth": 20,
                "maxStorageBytes": 5_368_709_120,
                "maxFiles": 1_000,
                "maxFilesPerConversation": 10,
                "automationLimit": 6,
                "maxOperationalRecipients": 2,
                "maxCampaignRecipientsPerMonth": None,
                "maxMonitoredProcesses": 0,
            },
            "features": {
                "digitalSignature": False,
                "calendar": False,
                "email": False,
                "webhooks": False,
                "advancedAudit": False,
                "successManager": False,
                "supportTier": "standard",
                "integrations": ["whatsapp", "transcricao", "mensagens_agendadas"],
                "highlights": [
                    "125 conversas com IA por mês",
                    "1 canal WhatsApp conectado",
                    "Biblioteca inicial de áreas",
                    "Mensagens agendadas e transcrição multimodal",
                ],
            },
        },
        "pro": {
            "name": "Pro",
            "description": "Plano principal para escritórios em crescimento que precisam de mais equipe, IA e automações plug and play.",
            "recommended": True,
            "limits": {
                "maxTokensPerMonth": 2_000_000,
                "maxAiExecutionsPerMonth": 2_000,
                "maxUsers": 6,
                "maxWorkspaces": 3,
                "maxConnectedWhatsapps": 3,
                "maxAiAgents": 12,
                "maxLegalAreas": 4,
                "maxAiConversationsPerMonth": 250,
                "maxAudioMinutesPerMonth": 40,
                "maxStorageBytes": 26_843_545_600,
                "maxFiles": 5_000,
                "maxFilesPerConversation": None,
                "automationLimit": None,
                "maxOperationalRecipients": 6,
                "maxCampaignRecipientsPerMonth": None,
                "maxMonitoredProcesses": 0,
            },
            "features": {
                "digitalSignature": True,
                "calendar": True,
                "email": True,
                "webhooks": False,
                "advancedAudit": True,
                "successManager": False,
                "supportTier": "priority",
                "integrations": ["whatsapp", "zapsign", "email", "calendar", "advbox"],
                "highlights": [
                    "250 conversas com IA por mês",
                    "Assinatura digital e integrações operacionais",
                    "Automações ilimitadas do workspace",
                    "Biblioteca completa de áreas e follow-ups",
                ],
            },
        },
        "scale": {
            "name": "Scale",
            "description": "Governança avançada para operação multi-time, alta volumetria e gestão próxima da plataforma.",
            "recommended": False,
            "limits": {
                "maxTokensPerMonth": 10_000_000,
                "maxAiExecutionsPerMonth": 10_000,
                "maxUsers": None,
                "maxWorkspaces": 4,
                "maxConnectedWhatsapps": 6,
                "maxAiAgents": None,
                "maxLegalAreas": None,
                "maxAiConversationsPerMonth": 500,
                "maxAudioMinutesPerMonth": 90,
                "maxStorageBytes": 107_374_182_400,
                "maxFiles": 20_000,
                "maxFilesPerConversation": None,
                "automationLimit": None,
                "maxOperationalRecipients": None,
                "maxCampaignRecipientsPerMonth": None,
                "maxMonitoredProcesses": 0,
            },
            "features": {
                "digitalSignature": True,
                "calendar": True,
                "email": True,
                "webhooks": True,
                "advancedAudit": True,
                "successManager": True,
                "supportTier": "exclusive",
                "integrations": ["whatsapp", "zapsign", "email", "calendar", "advbox", "webhooks"],
                "highlights": [
                    "500 conversas com IA por mês",
                    "Suporte exclusivo e gerente de sucesso",
                    "Webhooks e auditoria avançada",
                    "Múltiplos workspaces com governança central",
                ],
            },
        },
    }

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── MRR / ARR / Billing ───────────────────────────────────────────────

    async def get_mrr_breakdown(self) -> Dict[str, Any]:
        """
        Calcula MRR total e detalhado por plano.
        Retorna: total_mrr_cents, total_arr_cents, breakdown por plano.
        """
        stmt = (
            select(
                Plan.tier,
                Plan.name,
                Subscription.interval,
                func.count(Subscription.id).label("count"),
                func.sum(Subscription.amount_cents).label("total_cents"),
            )
            .join(Subscription, Subscription.plan_id == Plan.id)
            .where(
                Subscription.status.in_(
                    [
                        SubscriptionStatus.active,
                        SubscriptionStatus.trialing,
                    ]
                )
            )
            .group_by(Plan.tier, Plan.name, Subscription.interval)
        )
        rows = (await self.db.execute(stmt)).all()

        total_mrr_cents = 0
        breakdown_by_tier: dict[str, dict[str, Any]] = {}
        for row in rows:
            amount_cents = int(row.total_cents or 0)
            mrr = amount_cents // 12 if row.interval == BillingInterval.annually else amount_cents
            tier_key = row.tier.value if hasattr(row.tier, "value") else str(row.tier)
            item = breakdown_by_tier.setdefault(
                tier_key,
                {
                    "tier": row.tier,
                    "name": row.name,
                    "workspace_count": 0,
                    "mrr_cents": 0,
                },
            )
            item["workspace_count"] += int(row.count or 0)
            item["mrr_cents"] += mrr
            total_mrr_cents += mrr

        breakdown = []
        for item in breakdown_by_tier.values():
            breakdown.append(
                {
                    **item,
                    "mrr_brl": round(item["mrr_cents"] / 100, 2),
                }
            )

        return {
            "total_mrr_cents": total_mrr_cents,
            "total_arr_cents": total_mrr_cents * 12,
            "total_mrr_brl": round(total_mrr_cents / 100, 2),
            "total_arr_brl": round(total_mrr_cents * 12 / 100, 2),
            "breakdown": breakdown,
        }

    async def get_workspace_summary(self) -> Dict[str, Any]:
        """
        Resumo de workspaces: total, ativas, trial, suspensas, novas (30d), churnadas (30d).
        """
        now = datetime.now(timezone.utc)
        thirty_days_ago = now - timedelta(days=30)

        total = await self.db.scalar(select(func.count(Workspace.id)))
        active = await self.db.scalar(
            select(func.count(Workspace.id)).where(
                Workspace.is_active.is_(True),
                Workspace.deleted_at.is_(None),
            )
        )
        trial = await self.db.scalar(
            select(func.count(Subscription.id)).where(
                Subscription.status == SubscriptionStatus.trialing
            )
        )
        new_30d = await self.db.scalar(
            select(func.count(Workspace.id)).where(
                Workspace.created_at >= thirty_days_ago,
                Workspace.deleted_at.is_(None),
            )
        )
        churned_30d = await self.db.scalar(
            select(func.count(Subscription.id)).where(
                Subscription.status == SubscriptionStatus.cancelled,
                Subscription.cancelled_at >= thirty_days_ago,
            )
        )

        return {
            "total_workspaces": total or 0,
            "active_workspaces": active or 0,
            "trial_workspaces": trial or 0,
            "new_last_30d": new_30d or 0,
            "churned_last_30d": churned_30d or 0,
            "churn_rate_30d_pct": round(
                (churned_30d or 0) / max((active or 0) + (churned_30d or 0), 1) * 100,
                2,
            ),
            "churn_denominator_30d": (active or 0) + (churned_30d or 0),
        }

    # ── Token Usage / AI Costs ────────────────────────────────────────────

    async def get_token_usage_summary(
        self,
        days: int = 30,
        workspace_id: Optional[UUID] = None,
    ) -> Dict[str, Any]:
        """
        Uso de tokens consolidado: total de tokens, custo por modelo, custo por workspace.
        """
        since = datetime.now(timezone.utc) - timedelta(days=days)

        base_filter = [PlatformTokenUsage.usage_date >= since]
        if workspace_id:
            base_filter.append(PlatformTokenUsage.workspace_id == workspace_id)

        # Totais gerais
        totals_stmt = select(
            func.sum(PlatformTokenUsage.total_runs).label("total_runs"),
            func.sum(PlatformTokenUsage.total_input_tokens).label("total_input"),
            func.sum(PlatformTokenUsage.total_output_tokens).label("total_output"),
            func.sum(PlatformTokenUsage.total_cost_usd).label("total_cost_usd"),
        ).where(*base_filter)
        totals = (await self.db.execute(totals_stmt)).one()

        # Por modelo
        by_model_stmt = (
            select(
                PlatformTokenUsage.model_used,
                PlatformTokenUsage.provider,
                func.sum(PlatformTokenUsage.total_runs).label("runs"),
                func.sum(PlatformTokenUsage.total_input_tokens).label("input_tokens"),
                func.sum(PlatformTokenUsage.total_output_tokens).label("output_tokens"),
                func.sum(PlatformTokenUsage.total_cost_usd).label("cost_usd"),
            )
            .where(*base_filter)
            .group_by(PlatformTokenUsage.model_used, PlatformTokenUsage.provider)
            .order_by(func.sum(PlatformTokenUsage.total_cost_usd).desc())
        )
        by_model = (await self.db.execute(by_model_stmt)).all()

        # Top workspaces por custo
        by_workspace_stmt = (
            select(
                PlatformTokenUsage.workspace_id,
                func.sum(PlatformTokenUsage.total_runs).label("runs"),
                func.sum(PlatformTokenUsage.total_cost_usd).label("cost_usd"),
            )
            .where(*base_filter)
            .group_by(PlatformTokenUsage.workspace_id)
            .order_by(func.sum(PlatformTokenUsage.total_cost_usd).desc())
            .limit(20)
        )
        by_workspace = (await self.db.execute(by_workspace_stmt)).all()

        return {
            "period_days": days,
            "total_runs": int(totals.total_runs or 0),
            "total_input_tokens": int(totals.total_input or 0),
            "total_output_tokens": int(totals.total_output or 0),
            "total_tokens": int((totals.total_input or 0) + (totals.total_output or 0)),
            "total_cost_usd": float(totals.total_cost_usd or 0),
            "by_model": [
                {
                    "model": r.model_used,
                    "provider": r.provider,
                    "runs": int(r.runs or 0),
                    "input_tokens": int(r.input_tokens or 0),
                    "output_tokens": int(r.output_tokens or 0),
                    "cost_usd": float(r.cost_usd or 0),
                }
                for r in by_model
            ],
            "top_workspaces_by_cost": [
                {
                    "workspace_id": str(r.workspace_id),
                    "runs": int(r.runs or 0),
                    "cost_usd": float(r.cost_usd or 0),
                }
                for r in by_workspace
            ],
        }

    async def get_aws_cost_estimate(self) -> Dict[str, Any]:
        """
        Busca custo AWS via Cost Explorer API (real) com fallback para estimativa.
        Retorna custo por serviço nos últimos 30 dias.
        """
        if not aws_external_access_allowed():
            return self._aws_cost_estimate_fallback()
        try:
            return await asyncio.to_thread(self._fetch_aws_cost_real)
        except Exception as exc:
            logger.warning(
                "platform_service.aws_cost_real_failed",
                error_type=safe_error_type(exc),
            )
            return self._aws_cost_estimate_fallback()

    def _fetch_aws_cost_real(self) -> Dict[str, Any]:
        """Busca custo real via AWS Cost Explorer API (síncrono, roda em thread)."""
        if not aws_external_access_allowed():
            raise RuntimeError("AWS Cost Explorer bloqueado pelo gate externo do runtime.")

        import boto3

        end = date.today()
        start = end - timedelta(days=30)

        client = boto3.client("ce", region_name="us-east-1")
        response = client.get_cost_and_usage(
            TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        )

        results = response.get("ResultsByTime", [])
        cost_by_service: Dict[str, float] = {}
        total_usd = 0.0

        for period in results:
            for group in period.get("Groups", []):
                service = group["Keys"][0]
                amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                cost_by_service[service] = cost_by_service.get(service, 0) + amount
                total_usd += amount

        return {
            "source": "aws_cost_explorer",
            "period_days": 30,
            "total_usd": round(total_usd, 4),
            "by_service": [
                {"service": k, "cost_usd": round(v, 4)}
                for k, v in sorted(cost_by_service.items(), key=lambda x: -x[1])
            ],
        }

    def _aws_cost_estimate_fallback(self) -> Dict[str, Any]:
        """
        Estimativa de custo AWS baseada em uso típico da stack.
        Usado quando Cost Explorer não está disponível.
        """
        return {
            "source": "estimate",
            "period_days": 30,
            "note": "Estimativa baseada em uso típico. Configure IAM ce:GetCostAndUsage para dados reais.",
            "total_usd": None,
            "by_service": [
                {
                    "service": "Amazon ECS/Fargate",
                    "cost_usd": None,
                    "estimate_range": "$50–$200/mês",
                },
                {
                    "service": "Amazon RDS (PostgreSQL)",
                    "cost_usd": None,
                    "estimate_range": "$30–$100/mês",
                },
                {
                    "service": "Amazon ElastiCache (Redis)",
                    "cost_usd": None,
                    "estimate_range": "$15–$50/mês",
                },
                {"service": "Amazon S3", "cost_usd": None, "estimate_range": "$5–$20/mês"},
                {"service": "Amazon CloudFront", "cost_usd": None, "estimate_range": "$2–$15/mês"},
                {"service": "AWS ALB", "cost_usd": None, "estimate_range": "$20–$40/mês"},
            ],
        }

    # ── Workspace Health Score ────────────────────────────────────────────

    async def get_workspace_health_scores(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Calcula score de saúde (0–100) para cada workspace.
        Usado pelo super-admin para identificar clientes em risco de churn.

        Score é baseado em:
          - Setup completo (+20): integração, usuários, automação configurados
          - Uso ativo (+30): matters criados nos últimos 30 dias
          - Integração conectada (+20): WhatsApp ou fintech conectado
          - IA em uso (+20): AgentRuns nos últimos 30 dias
          - Assinatura ativa (+10): não está em trial nem overdue
        """
        now = datetime.now(timezone.utc)
        thirty_days_ago = now - timedelta(days=30)

        stmt = (
            select(Workspace)
            .where(Workspace.deleted_at.is_(None), Workspace.is_active.is_(True))
            .limit(limit)
        )
        workspaces = (await self.db.execute(stmt)).scalars().all()

        scores = []
        for ws in workspaces:
            score = 0
            reasons = []

            # Matters recentes
            recent_matters = await self.db.scalar(
                select(func.count(Matter.id)).where(
                    Matter.workspace_id == ws.id,
                    Matter.created_at >= thirty_days_ago,
                )
            )
            if (recent_matters or 0) >= 5:
                score += 30
                reasons.append(f"{recent_matters} matters nos últimos 30 dias")
            elif (recent_matters or 0) > 0:
                score += 15
                reasons.append(f"{recent_matters} matter(s) nos últimos 30 dias")
            else:
                reasons.append("⚠️ Sem matters recentes")

            # Usuários membros
            member_count = await self.db.scalar(
                select(func.count(WorkspaceMembership.id)).where(
                    WorkspaceMembership.workspace_id == ws.id,
                )
            )
            if (member_count or 0) >= 2:
                score += 20
                reasons.append(f"{member_count} usuários ativos")

            # AgentRuns recentes (IA em uso)
            recent_runs = await self.db.scalar(
                select(func.count(AgentRun.id)).where(
                    AgentRun.workspace_id == ws.id,
                    AgentRun.created_at >= thirty_days_ago,
                )
            )
            if (recent_runs or 0) > 0:
                score += 20
                reasons.append(f"{recent_runs} execuções de IA")
            else:
                reasons.append("⚠️ IA não utilizada")

            # Assinatura ativa
            sub_status = None
            if hasattr(ws, "subscription") and ws.subscription:
                sub_status = ws.subscription.status
                if ws.subscription.is_active:
                    score += 10
                    reasons.append(f"Assinatura {sub_status}")

            scores.append(
                {
                    "workspace_id": str(ws.id),
                    "workspace_name": ws.name,
                    "workspace_slug": ws.slug,
                    "health_score": min(score, 100),
                    "status": (
                        "healthy" if score >= 70 else "at_risk" if score >= 40 else "critical"
                    ),
                    "subscription_status": sub_status,
                    "reasons": reasons,
                }
            )

        return sorted(scores, key=lambda x: x["health_score"])

    # ── Saúde Meta & Funil (Super Admin enxerga tudo) ───────────────────────

    async def get_meta_health(
        self,
        days: int = 7,
        workspace_id: Optional[UUID] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """
        Agrega, por workspace, a saúde operacional Meta: campanhas pausadas por
        política/kill switch, top erros Meta por código, funil de aquisição
        (24h/7d) e supressões (opt-out) por motivo.

        Observação de dados: o motor grava `pause_reason`/`paused_by_policy`
        nas pausas por política; a leitura prefere as colunas e usa o evento
        de auditoria `campaign_auto_paused` como fallback para pausas
        anteriores à gravação das colunas.
        """
        now = datetime.now(timezone.utc)
        since_24h = now - timedelta(hours=24)
        since_7d = now - timedelta(days=7)
        lookback = now - timedelta(days=max(days, 1))

        # ── Campanhas pausadas (status atual) ───────────────────────────
        campaign_filters = [WhatsAppCampaign.status == CampaignStatus.paused]
        if workspace_id:
            campaign_filters.append(WhatsAppCampaign.workspace_id == workspace_id)

        paused_stmt = (
            select(WhatsAppCampaign)
            .where(*campaign_filters)
            .order_by(WhatsAppCampaign.paused_at.desc().nullslast())
            .limit(limit)
        )
        paused_campaigns = (await self.db.execute(paused_stmt)).scalars().all()

        pause_events_by_campaign: Dict[UUID, Dict[str, Any]] = {}
        if paused_campaigns:
            campaign_ids = [c.id for c in paused_campaigns]
            events_stmt = (
                select(WhatsAppCampaignDeliveryEvent)
                .where(WhatsAppCampaignDeliveryEvent.campaign_id.in_(campaign_ids))
                .order_by(WhatsAppCampaignDeliveryEvent.occurred_at.desc())
            )
            events = (await self.db.execute(events_stmt)).scalars().all()
            for event in events:
                if event.campaign_id in pause_events_by_campaign:
                    continue
                if (event.payload or {}).get("event") != "campaign_auto_paused":
                    continue
                pause_events_by_campaign[event.campaign_id] = {
                    "pause_reason": event.payload.get("pauseReason"),
                    "provider_code": event.payload.get("providerCode"),
                    "occurred_at": event.occurred_at,
                }

        paused_campaigns_payload = []
        for campaign in paused_campaigns:
            event_info = pause_events_by_campaign.get(campaign.id, {})
            reason = campaign.pause_reason or event_info.get("pause_reason")
            paused_campaigns_payload.append(
                {
                    "campaign_id": str(campaign.id),
                    "workspace_id": str(campaign.workspace_id),
                    "title": campaign.title,
                    "paused_at": campaign.paused_at.isoformat() if campaign.paused_at else None,
                    "pause_reason": reason,
                    "provider_code": event_info.get("provider_code"),
                    "paused_by_policy": campaign.paused_by_policy or bool(event_info),
                    "reason_source": (
                        "campaign.pause_reason"
                        if campaign.pause_reason
                        else "evento campaign_auto_paused"
                        if event_info.get("pause_reason")
                        else None
                    ),
                }
            )

        # ── Top erros Meta por código (recipients falhos) ───────────────
        recipient_filters = [
            WhatsAppCampaignRecipient.status == CampaignRecipientStatus.failed,
            WhatsAppCampaignRecipient.last_error.is_not(None),
            WhatsAppCampaignRecipient.updated_at >= lookback,
        ]
        if workspace_id:
            recipient_filters.append(WhatsAppCampaignRecipient.workspace_id == workspace_id)

        errors_stmt = (
            select(WhatsAppCampaignRecipient.last_error)
            .where(*recipient_filters)
            .order_by(WhatsAppCampaignRecipient.updated_at.desc())
            .limit(2000)
        )
        raw_errors = (await self.db.execute(errors_stmt)).scalars().all()

        code_counter: Counter[str] = Counter()
        sample_messages: Dict[str, str] = {}
        unclassified = 0
        for raw in raw_errors:
            match = _META_ERROR_CODE_RE.match(raw or "")
            if match:
                code, message = match.group(1), match.group(2)
                code_counter[code] += 1
                sample_messages.setdefault(code, message or raw)
            else:
                unclassified += 1
        top_errors = [
            {
                "provider_code": code,
                "count": count,
                "sample_message": sample_messages.get(code),
            }
            for code, count in code_counter.most_common(5)
        ]

        # ── Funil de aquisição (24h / 7d) ────────────────────────────────
        async def _funnel(since: datetime) -> List[Dict[str, Any]]:
            funnel_filters = [AcquisitionEvent.happened_at >= since]
            if workspace_id:
                funnel_filters.append(AcquisitionEvent.workspace_id == workspace_id)
            stmt = (
                select(
                    AcquisitionEvent.event_name,
                    func.count(AcquisitionEvent.id).label("count"),
                )
                .where(*funnel_filters)
                .group_by(AcquisitionEvent.event_name)
                .order_by(func.count(AcquisitionEvent.id).desc())
            )
            rows = (await self.db.execute(stmt)).all()
            return [{"event_name": r.event_name, "count": int(r.count or 0)} for r in rows]

        # AsyncSession não suporta operações concorrentes na mesma sessão;
        # sequencial evita InvalidRequestError intermitente sob asyncpg.
        funnel_24h = await _funnel(since_24h)
        funnel_7d = await _funnel(since_7d)

        # ── Supressões (opt-out) por motivo ──────────────────────────────
        suppression_filters = [WhatsAppSuppressionEntry.is_active.is_(True)]
        if workspace_id:
            suppression_filters.append(WhatsAppSuppressionEntry.workspace_id == workspace_id)
        suppression_stmt = (
            select(
                WhatsAppSuppressionEntry.reason,
                func.count(WhatsAppSuppressionEntry.id).label("count"),
            )
            .where(*suppression_filters)
            .group_by(WhatsAppSuppressionEntry.reason)
            .order_by(func.count(WhatsAppSuppressionEntry.id).desc())
        )
        suppression_rows = (await self.db.execute(suppression_stmt)).all()

        # ── Templates Meta: rejeitados/pausados/recategorizados por workspace
        # (diretriz 7 — Super Admin enxerga tudo; W2.2) ──────────────────
        template_status_filters = [
            WhatsAppMetaTemplate.status.in_(
                (
                    MetaTemplateStatus.rejected,
                    MetaTemplateStatus.paused,
                    MetaTemplateStatus.disabled,
                )
            )
        ]
        if workspace_id:
            template_status_filters.append(WhatsAppMetaTemplate.workspace_id == workspace_id)
        template_status_stmt = (
            select(
                WhatsAppMetaTemplate.workspace_id,
                WhatsAppMetaTemplate.status,
                func.count(WhatsAppMetaTemplate.id).label("count"),
            )
            .where(*template_status_filters)
            .group_by(WhatsAppMetaTemplate.workspace_id, WhatsAppMetaTemplate.status)
        )
        template_status_rows = (await self.db.execute(template_status_stmt)).all()

        recategorized_filters = [WhatsAppMetaTemplate.raw_payload.is_not(None)]
        if workspace_id:
            recategorized_filters.append(WhatsAppMetaTemplate.workspace_id == workspace_id)
        recategorized_stmt = select(
            WhatsAppMetaTemplate.workspace_id, WhatsAppMetaTemplate.raw_payload
        ).where(*recategorized_filters)
        recategorized_rows = (await self.db.execute(recategorized_stmt)).all()
        recategorized_by_workspace: Counter[str] = Counter()
        for row in recategorized_rows:
            raw = row.raw_payload or {}
            if raw.get("previousCategory"):
                recategorized_by_workspace[str(row.workspace_id)] += 1

        template_health_by_workspace: Dict[str, Dict[str, int]] = {}
        for row in template_status_rows:
            bucket = template_health_by_workspace.setdefault(
                str(row.workspace_id),
                {"rejected": 0, "paused": 0, "disabled": 0, "recategorized": 0},
            )
            status_key = getattr(row.status, "value", str(row.status))
            if status_key in bucket:
                bucket[status_key] = int(row.count or 0)
        for ws_key, count in recategorized_by_workspace.items():
            bucket = template_health_by_workspace.setdefault(
                ws_key, {"rejected": 0, "paused": 0, "disabled": 0, "recategorized": 0}
            )
            bucket["recategorized"] = count

        templates_by_workspace = [
            {"workspace_id": ws_key, **counts}
            for ws_key, counts in sorted(template_health_by_workspace.items())
        ]
        templates_totals = {
            "rejected": sum(item["rejected"] for item in templates_by_workspace),
            "paused": sum(item["paused"] for item in templates_by_workspace),
            "disabled": sum(item["disabled"] for item in templates_by_workspace),
            "recategorized": sum(item["recategorized"] for item in templates_by_workspace),
        }

        # ── Recibos de feedback CAPI (dispatcher T3) ─────────────────────
        capi_filters = [AdsConversionFeedbackRecord.record_type == "provider_receipt"]
        if workspace_id:
            capi_filters.append(AdsConversionFeedbackRecord.workspace_id == workspace_id)
        capi_filters.append(AdsConversionFeedbackRecord.recorded_at >= lookback)
        capi_stmt = (
            select(
                AdsConversionFeedbackRecord.provider,
                AdsConversionFeedbackRecord.state,
                func.count(AdsConversionFeedbackRecord.id).label("count"),
            )
            .where(*capi_filters)
            .group_by(AdsConversionFeedbackRecord.provider, AdsConversionFeedbackRecord.state)
        )
        capi_rows = (await self.db.execute(capi_stmt)).all()
        capi_by_provider: Dict[str, Dict[str, int]] = {}
        capi_totals: Counter[str] = Counter()
        for row in capi_rows:
            provider_counts = capi_by_provider.setdefault(row.provider, {})
            provider_counts[row.state] = int(row.count or 0)
            capi_totals[row.state] += int(row.count or 0)
        capi_feedback = {
            "total_receipts": sum(capi_totals.values()),
            "simulated_blocked": capi_totals.get("blocked", 0),
            "accepted": capi_totals.get("accepted", 0),
            "rejected": capi_totals.get("rejected", 0),
            "retryable": capi_totals.get("retryable", 0),
            "by_provider": capi_by_provider,
        }

        return {
            "period_days": days,
            "workspace_id": str(workspace_id) if workspace_id else None,
            "paused_campaigns": paused_campaigns_payload,
            "top_meta_errors": top_errors,
            "unclassified_errors_count": unclassified,
            "funnel_last_24h": funnel_24h,
            "funnel_last_7d": funnel_7d,
            "suppressions_by_reason": [
                {"reason": r.reason, "count": int(r.count or 0)} for r in suppression_rows
            ],
            "capi_feedback": capi_feedback,
            "templates_health": {
                "totals": templates_totals,
                "by_workspace": templates_by_workspace,
            },
            "data_notes": [
                "Motivo de pausa lido de pause_reason/paused_by_policy da "
                "campanha; evento campaign_auto_paused serve de fallback "
                "para pausas anteriores à gravação das colunas.",
                "capi_feedback.simulated_blocked conta recibos marcados "
                "simulated=true (gate externo fechado ou não aprovado); "
                "nunca representa envio real à Meta.",
                "templates_health.recategorized conta templates cujo "
                "raw_payload.previousCategory foi gravado pelo webhook "
                "template_category_update (W2.2); não é histórico completo, "
                "só a última recategorização observada.",
            ],
        }

    async def list_plan_catalog(self) -> List[Dict[str, Any]]:
        result = await self.db.execute(select(Plan))
        plans = {getattr(plan.tier, "value", ""): plan for plan in result.scalars().all()}
        plan_ids = [plan.id for plan in plans.values()]
        entitlement_rows = (
            list(
                (
                    await self.db.scalars(
                        select(PlanEntitlement).where(PlanEntitlement.plan_id.in_(plan_ids))
                    )
                ).all()
            )
            if plan_ids
            else []
        )
        entitlements_by_plan: Dict[UUID, Dict[EntitlementKey, PlanEntitlement]] = {}
        for row in entitlement_rows:
            entitlements_by_plan.setdefault(row.plan_id, {})[row.entitlement] = row

        items: List[Dict[str, Any]] = []
        for key, default in self.PLAN_DEFAULTS.items():
            tier = self.PLAN_KEY_TO_TIER[key]
            plan = plans.get(tier.value)
            typed = entitlements_by_plan.get(plan.id, {}) if plan else {}
            merged_features = self._build_plan_features(default["features"], plan, typed)
            limits = self._build_plan_limits(default["limits"], plan, typed)
            items.append(
                {
                    "key": key,
                    "name": plan.name if plan and plan.name else default["name"],
                    "description": plan.description
                    if plan and plan.description
                    else default["description"],
                    "recommended": default["recommended"],
                    "limits": limits,
                    "features": merged_features,
                    "is_active": plan.is_active if plan else True,
                }
            )
        return items

    async def upsert_plan_catalog_item(
        self, plan_key: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        tier = self.PLAN_KEY_TO_TIER.get(plan_key)
        if not tier:
            raise InvalidPlatformPlanKeyError("Plano inválido.")

        canonical_sku = canonical_sku_for_legacy_plan_key(plan_key)
        if canonical_sku is not None:
            canonical_offer_id = await self.db.scalar(
                select(CommercialOffer.id).where(CommercialOffer.sku == canonical_sku)
            )
            if canonical_offer_id is not None:
                raise LegacyPlanEditorDisabledError(
                    "O catálogo canônico já está ativo. Publique uma nova revisão "
                    f"da oferta {canonical_sku} no Estúdio de Planos."
                )

        legacy_gaps = self._legacy_plan_editor_unmapped_entitlements()
        if legacy_gaps:
            raise LegacyPlanEditorDisabledError(
                "O editor legado está desativado porque não representa o contrato "
                "comercial completo. Use Ofertas e contratos no Estúdio de Planos. "
                f"Entitlements não representados: {', '.join(legacy_gaps)}."
            )

        result = await self.db.execute(select(Plan).where(Plan.tier == tier))
        plan = result.scalar_one_or_none()
        default_key = self.PLAN_KEY_TO_LEGACY_DEFAULT.get(plan_key, plan_key)
        default = self.PLAN_DEFAULTS[default_key]
        incoming_limits = self._normalize_limit_keys(payload.get("limits") or {})
        incoming_features = self._normalize_feature_keys(payload.get("features") or {})
        billing = LocalBillingService(self.db)
        if plan is None:
            await billing.ensure_catalog()
            plan = await self.db.scalar(select(Plan).where(Plan.tier == tier))
        if plan is None:
            raise ValueError("Catálogo tipado do plano não foi provisionado.")

        typed_rows = list(
            (
                await self.db.scalars(
                    select(PlanEntitlement).where(PlanEntitlement.plan_id == plan.id)
                )
            ).all()
        )
        typed = {row.entitlement: row for row in typed_rows}
        current_limits = self._build_plan_limits(default["limits"], plan, typed)
        current_features = self._build_plan_features(default["features"], plan, typed)
        merged_limits = {**current_limits, **incoming_limits}
        merged_features = self._merge_plan_features(current_features, incoming_features)

        plan.name = payload.get("name") or plan.name or default["name"]
        plan.description = payload.get("description") or plan.description or default["description"]
        plan.is_active = payload.get("isActive", plan.is_active)

        entitlement_updates = {
            entitlement: (True, merged_limits.get(limit_key))
            for limit_key, entitlement in self.LIMIT_ENTITLEMENTS.items()
        }
        entitlement_updates.update(
            {
                entitlement: (bool(merged_features.get(feature_key, False)), None)
                for feature_key, entitlement in self.PLATFORM_FEATURE_ENTITLEMENTS.items()
            }
        )
        for entitlement, (enabled, limit_quantity) in entitlement_updates.items():
            row = typed.get(entitlement)
            if row is None:
                row = PlanEntitlement(plan_id=plan.id, entitlement=entitlement)
                self.db.add(row)
            row.enabled = enabled
            row.limit_quantity = limit_quantity
            row.hard_limit = True
            LocalBillingService._sync_legacy_entitlement(
                plan,
                entitlement,
                enabled,
                limit_quantity,
            )
        compatibility_features = dict(plan.features or {})
        compatibility_features.update(merged_features)
        for key in (
            "maxWorkspaces",
            "maxFilesPerConversation",
            "maxOperationalRecipients",
            "maxCampaignRecipientsPerMonth",
        ):
            compatibility_features[key] = merged_limits.get(key)
        plan.features = compatibility_features

        await self.db.flush()
        catalog = await self.list_plan_catalog()
        return next(item for item in catalog if item["key"] == default_key)

    @classmethod
    def _legacy_plan_editor_unmapped_entitlements(cls) -> tuple[str, ...]:
        """Return canonical keys the deprecated editor cannot represent.

        This deliberately compares against ``EntitlementKey`` instead of a
        second hand-maintained completeness list. The legacy surface remains
        readable for migration evidence, but mutations fail before any write
        whenever its projection is incomplete.
        """

        represented = set(cls.LIMIT_ENTITLEMENTS.values()) | set(
            cls.PLATFORM_FEATURE_ENTITLEMENTS.values()
        )
        return tuple(sorted(item.value for item in set(EntitlementKey) - represented))

    async def list_platform_workspaces(
        self,
        limit: int = 100,
        *,
        workspace_id: UUID | None = None,
        offset: int = 0,
        search: str | None = None,
        status_filter: WorkspaceStatus | None = None,
    ) -> List[Dict[str, Any]]:
        now = datetime.now(timezone.utc)
        thirty_days_ago = now - timedelta(days=30)

        query = (
            select(Workspace)
            .options(
                selectinload(Workspace.subscription).selectinload(Subscription.plan),
                selectinload(Workspace.memberships).selectinload(WorkspaceMembership.user),
            )
            .where(Workspace.deleted_at.is_(None))
        )
        if workspace_id is not None:
            query = query.where(Workspace.id == workspace_id)
        if search:
            term = f"%{search.strip()}%"
            query = query.where(or_(Workspace.name.ilike(term), Workspace.slug.ilike(term)))
        if status_filter is not None:
            query = query.where(Workspace.status == status_filter)
        # The public page contract advertises createdAt/id ordering. Keep the
        # SQL order identical so offsets remain deterministic when several
        # accounts are created in the same timestamp bucket.
        query = query.order_by(Workspace.created_at.desc(), Workspace.id.desc())
        result = await self.db.execute(query.offset(offset).limit(limit))
        workspaces = result.scalars().all()
        workspace_service = WorkspaceService(self.db)

        workspace_ids = [workspace.id for workspace in workspaces]
        matter_counts = (
            {
                row.workspace_id: int(row.count)
                for row in (
                    await self.db.execute(
                        select(Matter.workspace_id, func.count(Matter.id).label("count"))
                        .where(
                            Matter.workspace_id.in_(workspace_ids),
                            Matter.created_at >= thirty_days_ago,
                        )
                        .group_by(Matter.workspace_id)
                    )
                ).all()
            }
            if workspace_ids
            else {}
        )
        ai_run_counts = (
            {
                row.workspace_id: int(row.count)
                for row in (
                    await self.db.execute(
                        select(AgentRun.workspace_id, func.count(AgentRun.id).label("count"))
                        .where(
                            AgentRun.workspace_id.in_(workspace_ids),
                            AgentRun.created_at >= thirty_days_ago,
                        )
                        .group_by(AgentRun.workspace_id)
                    )
                ).all()
            }
            if workspace_ids
            else {}
        )

        items: List[Dict[str, Any]] = []
        for workspace in workspaces:
            workspace_summary = workspace_service.to_workspace_summary(workspace)
            owner_membership = next(
                (
                    membership
                    for membership in workspace.memberships
                    if membership.role == WorkspaceRole.workspace_owner and membership.user
                ),
                None,
            )
            member_count = sum(
                1
                for membership in workspace.memberships
                if membership.status in {MembershipStatus.active, MembershipStatus.invited}
            )
            matter_count_30d = matter_counts.get(workspace.id, 0)
            ai_runs_30d = ai_run_counts.get(workspace.id, 0)
            subscription = workspace.__dict__.get("subscription")
            plan = subscription.__dict__.get("plan") if subscription else None
            plan_summary = (
                {
                    "tier": WorkspaceService._normalize_plan_tier(
                        getattr(getattr(plan, "tier", None), "value", None)
                    ),
                    "name": WorkspaceService._normalize_plan_name(
                        plan.name,
                        getattr(getattr(plan, "tier", None), "value", None),
                    ),
                    "subscription_status": getattr(
                        getattr(subscription, "status", None), "value", None
                    ),
                }
                if plan is not None
                else None
            )
            health_score = self._calculate_workspace_health_score(
                workspace=workspace,
                member_count=member_count,
                matter_count_30d=matter_count_30d,
                ai_runs_30d=ai_runs_30d,
            )
            items.append(
                {
                    "id": workspace.id,
                    "name": workspace.name,
                    "slug": workspace.slug,
                    "legal_name": workspace.legal_name,
                    "description": workspace.description,
                    "status": workspace.status,
                    "setup_mode": workspace.setup_mode,
                    "is_active": workspace.is_active,
                    "onboarding_completed": workspace.onboarding_completed,
                    "onboarding_progress": workspace_summary.onboarding_progress,
                    "next_onboarding_step": workspace_summary.next_onboarding_step,
                    "owner": {
                        "name": owner_membership.user.name
                        if owner_membership and owner_membership.user
                        else None,
                        "email": owner_membership.user.email
                        if owner_membership and owner_membership.user
                        else None,
                    },
                    "plan_summary": plan_summary,
                    "member_count": member_count,
                    "matter_count_30d": matter_count_30d,
                    "ai_runs_30d": ai_runs_30d,
                    "health_score": health_score,
                    "health_status": self._health_status(health_score),
                    "launch_notes": (workspace.settings or {}).get("platformLaunchNotes"),
                }
            )
        return items

    async def count_platform_workspaces(
        self,
        *,
        search: str | None = None,
        status_filter: WorkspaceStatus | None = None,
    ) -> int:
        query = select(func.count(Workspace.id)).where(Workspace.deleted_at.is_(None))
        if search:
            term = f"%{search.strip()}%"
            query = query.where(or_(Workspace.name.ilike(term), Workspace.slug.ilike(term)))
        if status_filter is not None:
            query = query.where(Workspace.status == status_filter)
        return int((await self.db.scalar(query)) or 0)

    async def create_platform_workspace(
        self,
        payload: Dict[str, Any],
        *,
        commit: bool = True,
        actor_user_id: UUID | None = None,
    ) -> Dict[str, Any]:
        workspace_service = WorkspaceService(self.db)
        workspace = await workspace_service.create_workspace(
            WorkspaceCreate(
                name=payload["name"],
                slug=payload.get("slug"),
                legal_name=payload.get("legal_name"),
                description=payload.get("description"),
                setup_mode=payload.get("setup_mode", WorkspaceSetupMode.assisted),
                settings={"platformLaunchNotes": payload.get("launch_notes")},
            )
        )
        workspace.status = WorkspaceStatus.provisioning
        workspace.is_active = False

        owner_email = payload.get("owner_email")
        owner_password = payload.get("owner_password")
        owner_name = payload.get("owner_name")
        if owner_email and owner_password and owner_name:
            normalized_owner_email = str(owner_email).strip().casefold()
            existing_user = await self.db.execute(
                select(User).where(
                    func.lower(User.email) == normalized_owner_email,
                    User.deleted_at.is_(None),
                )
            )
            user = existing_user.scalar_one_or_none()
            if not user:
                user = User(
                    name=owner_name,
                    email=normalized_owner_email,
                    hashed_password=await hash_password_async(owner_password),
                    agent_type=AgentType.human,
                    role=UserRole.admin,
                    initials="".join(part[0] for part in owner_name.split()[:2]).upper() or "OW",
                    color="#1a6fa5",
                    is_active=True,
                    email_verified_at=datetime.now(timezone.utc),
                )
                self.db.add(user)
                await self.db.flush()
                self.db.add(
                    AuthSecurityEvent(
                        event_type="trusted_setup_email_verified",
                        outcome="success",
                        actor_user_id=user.id,
                        subject_user_id=user.id,
                        details={
                            "source": "platform_admin_workspace_creation",
                            "workspace_id": str(workspace.id),
                        },
                    )
                )
            assert_workspace_identity(user)
            membership = WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role=WorkspaceRole.workspace_owner,
                status=MembershipStatus.active,
                is_default=True,
                access_package="office_owner",
            )
            self.db.add(membership)

        await self._ensure_workspace_plan(
            workspace.id,
            payload.get("plan_key", "pro"),
            actor_user_id=actor_user_id,
            idempotency_key=f"platform-workspace-create-plan:{workspace.id}",
            reason="Plano inicial definido na criação administrativa da conta.",
        )
        if commit:
            await self.db.commit()
        else:
            await self.db.flush()

        workspaces = await self.list_platform_workspaces(limit=1, workspace_id=workspace.id)
        return next(item for item in workspaces if str(item["id"]) == str(workspace.id))

    async def update_platform_workspace(
        self,
        workspace_id: UUID,
        payload: Dict[str, Any],
        *,
        commit: bool = True,
        actor_user_id: UUID | None = None,
    ) -> Dict[str, Any]:
        workspace_service = WorkspaceService(self.db)
        next_status = payload.get("status")
        next_is_active = payload.get("is_active")
        if next_status and next_is_active is None:
            next_is_active = next_status == WorkspaceStatus.active
        update_payload = {
            key: value
            for key, value in {
                "name": payload.get("name"),
                "slug": payload.get("slug"),
                "legal_name": payload.get("legal_name"),
                "description": payload.get("description"),
                "status": next_status,
                "is_active": next_is_active,
            }.items()
            if value is not None
        }
        workspace = await workspace_service.update_workspace(
            workspace_id,
            WorkspaceUpdate(**update_payload),
        )

        if payload.get("onboarding_completed") is not None:
            workspace.onboarding_completed = payload["onboarding_completed"]

        if payload.get("launch_notes") is not None:
            workspace.settings = {
                **(workspace.settings or {}),
                "platformLaunchNotes": payload["launch_notes"],
            }

        if payload.get("plan_key"):
            await self._ensure_workspace_plan(
                workspace.id,
                payload["plan_key"],
                actor_user_id=actor_user_id,
                reason="Plano alterado pela administração da plataforma.",
            )

        if commit:
            await self.db.commit()
        else:
            await self.db.flush()
        # Não expire a sessão inteira: o Blackbox executa lotes por recurso e
        # outros objetos já carregados podem pertencer ao chamador. Expirar
        # globalmente fazia acessos posteriores dispararem I/O implícito fora
        # do contexto async (MissingGreenlet) e tornava o resultado do lote
        # dependente do estado acidental da sessão.
        await self.db.refresh(workspace)
        refreshed = await workspace_service.get_workspace(workspace_id)
        if not refreshed:
            raise ValueError("Workspace não encontrado após atualização.")
        # Recarregue exatamente o recurso alterado. A implementação anterior
        # procurava a conta dentro das 200 mais recentes; contas antigas eram
        # persistidas com sucesso e, em seguida, reportadas como falha porque
        # não apareciam naquele recorte.
        workspaces = await self.list_platform_workspaces(limit=1, workspace_id=refreshed.id)
        return next(item for item in workspaces if str(item["id"]) == str(refreshed.id))

    async def _ensure_workspace_plan(
        self,
        workspace_id: UUID,
        plan_key: str,
        *,
        actor_user_id: UUID | None = None,
        idempotency_key: str | None = None,
        reason: str = "Plano alterado pela administração da plataforma.",
    ) -> None:
        if plan_key not in self.PLAN_KEY_TO_TIER:
            raise InvalidPlatformPlanKeyError("Plano inicial inválido.")
        target_sku = canonical_sku(plan_key)
        contract_version = await self.db.scalar(
            select(WorkspaceCommercialContract.version).where(
                WorkspaceCommercialContract.workspace_id == workspace_id
            )
        )
        await CanonicalContractCommand(
            self.db,
            control_plane=True,
        ).ensure_core_plan(
            workspace_id=workspace_id,
            plan=target_sku,
            actor_user_id=actor_user_id,
            idempotency_key=(
                idempotency_key
                or f"platform-plan:{workspace_id}:{target_sku}:v{contract_version or 0}"
            ),
            reason=reason,
            project_legacy=True,
        )

    @staticmethod
    def _merge_plan_features(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        merged = {**base}
        merged.update({key: value for key, value in override.items() if value is not None})
        return merged

    @classmethod
    def _build_plan_limits(
        cls,
        default_limits: Dict[str, Any],
        plan: Optional[Plan],
        entitlements: Dict[EntitlementKey, PlanEntitlement],
    ) -> Dict[str, Any]:
        features = {**(plan.features or {})} if plan and plan.features else {}
        legacy = {
            "maxTokensPerMonth": plan.max_tokens_per_month
            if plan
            else default_limits.get("maxTokensPerMonth"),
            "maxAiExecutionsPerMonth": features.get(
                "maxAiExecutionsPerMonth", default_limits.get("maxAiExecutionsPerMonth")
            ),
            "maxUsers": plan.max_users
            if plan and plan.max_users is not None
            else default_limits.get("maxUsers"),
            "maxWorkspaces": features.get("maxWorkspaces", default_limits.get("maxWorkspaces")),
            "maxConnectedWhatsapps": features.get(
                "maxConnectedWhatsapps", default_limits.get("maxConnectedWhatsapps")
            ),
            "maxAiAgents": plan.max_ai_agents
            if plan and plan.max_ai_agents is not None
            else default_limits.get("maxAiAgents"),
            "maxLegalAreas": features.get("maxLegalAreas", default_limits.get("maxLegalAreas")),
            "maxAiConversationsPerMonth": features.get(
                "maxAiConversationsPerMonth", default_limits.get("maxAiConversationsPerMonth")
            ),
            "maxAudioMinutesPerMonth": features.get(
                "maxAudioMinutesPerMonth", default_limits.get("maxAudioMinutesPerMonth")
            ),
            "maxStorageBytes": features.get(
                "maxStorageBytes", default_limits.get("maxStorageBytes")
            ),
            "maxFiles": features.get("maxFilesPerMonth", default_limits.get("maxFiles")),
            "maxFilesPerConversation": features.get(
                "maxFilesPerConversation", default_limits.get("maxFilesPerConversation")
            ),
            "automationLimit": features.get(
                "automationLimit", default_limits.get("automationLimit")
            ),
            "maxOperationalRecipients": features.get(
                "maxOperationalRecipients", default_limits.get("maxOperationalRecipients")
            ),
            "maxCampaignRecipientsPerMonth": features.get(
                "maxCampaignRecipientsPerMonth",
                default_limits.get("maxCampaignRecipientsPerMonth"),
            ),
            "maxMonitoredProcesses": features.get(
                "maxMonitoredProcesses", default_limits.get("maxMonitoredProcesses")
            ),
        }
        for limit_key, entitlement_key in cls.LIMIT_ENTITLEMENTS.items():
            row = entitlements.get(entitlement_key)
            if row is not None:
                legacy[limit_key] = row.limit_quantity if row.enabled else 0
        return legacy

    @classmethod
    def _build_plan_features(
        cls,
        default_features: Dict[str, Any],
        plan: Optional[Plan],
        entitlements: Dict[EntitlementKey, PlanEntitlement],
    ) -> Dict[str, Any]:
        merged = cls._merge_plan_features(
            default_features,
            plan.features if plan and plan.features else {},
        )
        for feature_key, entitlement_key in cls.PLATFORM_FEATURE_ENTITLEMENTS.items():
            row = entitlements.get(entitlement_key)
            if row is not None:
                merged[feature_key] = row.enabled
        return merged

    @staticmethod
    def _normalize_limit_keys(payload: Dict[str, Any]) -> Dict[str, Any]:
        aliases = {
            "maxTokensPerMonth": ("maxTokensPerMonth", "max_tokens_per_month"),
            "maxAiExecutionsPerMonth": ("maxAiExecutionsPerMonth", "max_ai_executions_per_month"),
            "maxUsers": ("maxUsers", "max_users"),
            "maxWorkspaces": ("maxWorkspaces", "max_workspaces"),
            "maxConnectedWhatsapps": ("maxConnectedWhatsapps", "max_connected_whatsapps"),
            "maxAiAgents": ("maxAiAgents", "max_ai_agents"),
            "maxLegalAreas": ("maxLegalAreas", "max_legal_areas"),
            "maxAiConversationsPerMonth": (
                "maxAiConversationsPerMonth",
                "max_ai_conversations_per_month",
            ),
            "maxAudioMinutesPerMonth": ("maxAudioMinutesPerMonth", "max_audio_minutes_per_month"),
            "maxStorageBytes": ("maxStorageBytes", "max_storage_bytes"),
            "maxFiles": ("maxFiles", "max_files"),
            "maxFilesPerConversation": ("maxFilesPerConversation", "max_files_per_conversation"),
            "automationLimit": ("automationLimit", "automation_limit"),
            "maxOperationalRecipients": ("maxOperationalRecipients", "max_operational_recipients"),
            "maxCampaignRecipientsPerMonth": (
                "maxCampaignRecipientsPerMonth",
                "max_campaign_recipients_per_month",
            ),
            "maxMonitoredProcesses": (
                "maxMonitoredProcesses",
                "max_monitored_processes",
            ),
        }
        values: Dict[str, Any] = {}
        for key, source_aliases in aliases.items():
            for alias in source_aliases:
                if alias in payload:
                    values[key] = payload[alias]
                    break
        return values

    @staticmethod
    def _normalize_feature_keys(payload: Dict[str, Any]) -> Dict[str, Any]:
        values = {
            "digitalSignature": payload.get("digitalSignature", payload.get("digital_signature")),
            "calendar": payload.get("calendar"),
            "email": payload.get("email"),
            "webhooks": payload.get("webhooks"),
            "advancedAudit": payload.get("advancedAudit", payload.get("advanced_audit")),
            "successManager": payload.get("successManager", payload.get("success_manager")),
            "supportTier": payload.get("supportTier", payload.get("support_tier")),
            "integrations": payload.get("integrations", []),
            "highlights": payload.get("highlights", []),
        }
        return {key: value for key, value in values.items() if value is not None}

    @staticmethod
    def _calculate_workspace_health_score(
        workspace: Workspace,
        member_count: int,
        matter_count_30d: int,
        ai_runs_30d: int,
    ) -> int:
        score = 0
        if workspace.onboarding_completed:
            score += 20
        if matter_count_30d >= 5:
            score += 30
        elif matter_count_30d > 0:
            score += 15
        if member_count >= 2:
            score += 20
        if ai_runs_30d > 0:
            score += 20
        if workspace.subscription and workspace.subscription.is_active:
            score += 10
        return min(score, 100)

    @staticmethod
    def _health_status(score: int) -> str:
        if score >= 70:
            return "healthy"
        if score >= 40:
            return "at_risk"
        return "critical"

    # ── Token Usage Logger ────────────────────────────────────────────────

    @staticmethod
    async def log_agent_run_tokens(
        db: AsyncSession,
        run_or_workspace_id: AgentRun | UUID,
        model_used: Optional[str] = None,
        provider: Optional[str] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        cost_usd: Decimal | float | None = None,
        task_class: str = "unknown",
        request_id: str | None = None,
        operation: str = "agent.run",
        cached_input_tokens: int = 0,
        provider_request_id: str = "",
        price_catalog_version: str = "",
        route_catalog_version: str = "",
    ) -> None:
        """
        Agrega uso sem encerrar a transação do chamador. Aceita o AgentRun
        completo ou os campos explícitos para compatibilidade.
        """
        if isinstance(run_or_workspace_id, AgentRun):
            run = run_or_workspace_id
            workspace_id = run.workspace_id
            model_used = run.model_used
            provider = run.ai_provider
            input_tokens = run.input_tokens
            output_tokens = run.output_tokens
            cost_usd = run.cost_usd
            request_id = request_id or f"agent-run:{run.id}"
        else:
            workspace_id = run_or_workspace_id
        if not model_used or not provider:
            raise ValueError("model_used e provider são obrigatórios para registrar tokens.")
        await PlatformService.log_model_usage(
            db,
            workspace_id=workspace_id,
            model_used=model_used,
            provider=provider,
            input_tokens=int(input_tokens or 0),
            output_tokens=int(output_tokens or 0),
            cost_usd=Decimal(str(cost_usd or 0)),
            task_class=task_class,
            request_id=request_id or f"legacy:{uuid4()}",
            operation=operation,
            cached_input_tokens=cached_input_tokens,
            provider_request_id=provider_request_id,
            price_catalog_version=price_catalog_version,
            route_catalog_version=route_catalog_version,
        )

    @staticmethod
    async def log_model_usage(
        db: AsyncSession,
        *,
        workspace_id: UUID,
        model_used: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: Decimal | float,
        task_class: str,
        request_id: str,
        operation: str = "unknown",
        cached_input_tokens: int = 0,
        provider_request_id: str = "",
        price_catalog_version: str = "",
        route_catalog_version: str = "",
    ) -> None:
        """Persist one measured call and increment aggregates exactly once."""

        from sqlalchemy.dialects.postgresql import insert as pg_insert

        normalized_input = max(0, int(input_tokens))
        normalized_output = max(0, int(output_tokens))
        normalized_cached = max(0, int(cached_input_tokens))
        normalized_cost = Decimal(str(cost_usd or 0))
        normalized_task_class = str(task_class or "").strip().lower()
        normalized_request_id = str(request_id or "").strip()
        normalized_operation = str(operation or "unknown").strip()[:180] or "unknown"
        if not model_used or not provider:
            raise ValueError("model_used e provider são obrigatórios para registrar tokens.")
        if normalized_task_class not in {"simple", "medium", "complex", "premium", "unknown"}:
            raise ValueError("task_class inválida para registrar tokens.")
        if normalized_cost < 0:
            raise ValueError("cost_usd não pode ser negativo.")
        if not normalized_request_id or len(normalized_request_id) > 180:
            raise ValueError("request_id é obrigatório e deve ter até 180 caracteres.")
        ledger_values = {
            "id": uuid4(),
            "workspace_id": workspace_id,
            "request_id": normalized_request_id,
            "task_class": normalized_task_class,
            "operation": normalized_operation,
            "model_used": model_used,
            "provider": provider,
            "provider_request_id": str(provider_request_id or "").strip()[:180],
            "input_tokens": normalized_input,
            "output_tokens": normalized_output,
            "cached_input_tokens": normalized_cached,
            "cost_usd": normalized_cost,
            "price_catalog_version": str(price_catalog_version or "").strip()[:120],
            "route_catalog_version": str(route_catalog_version or "").strip()[:120],
        }
        inserted = await PlatformService._insert_model_usage_receipt(
            db,
            values=ledger_values,
        )
        if not inserted:
            await db.flush()
            return
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        dialect = db.bind.dialect.name if db.bind is not None else ""
        if dialect == "postgresql":
            stmt = (
                pg_insert(PlatformTokenUsage)
                .values(
                    workspace_id=workspace_id,
                    model_used=model_used,
                    provider=provider,
                    usage_date=today,
                    total_runs=1,
                    total_input_tokens=normalized_input,
                    total_output_tokens=normalized_output,
                    total_cost_usd=normalized_cost,
                )
                .on_conflict_do_update(
                    constraint="uq_token_usage_workspace_model_date",
                    set_={
                        "total_runs": PlatformTokenUsage.total_runs + 1,
                        "total_input_tokens": PlatformTokenUsage.total_input_tokens
                        + normalized_input,
                        "total_output_tokens": PlatformTokenUsage.total_output_tokens
                        + normalized_output,
                        "total_cost_usd": PlatformTokenUsage.total_cost_usd + normalized_cost,
                    },
                )
            )
            await db.execute(stmt)
            task_stmt = (
                pg_insert(PlatformTokenUsageByTask)
                .values(
                    workspace_id=workspace_id,
                    task_class=normalized_task_class,
                    model_used=model_used,
                    provider=provider,
                    usage_date=today,
                    total_runs=1,
                    total_input_tokens=normalized_input,
                    total_output_tokens=normalized_output,
                    total_cost_usd=normalized_cost,
                )
                .on_conflict_do_update(
                    constraint="uq_token_usage_task_workspace_model_date",
                    set_={
                        "total_runs": PlatformTokenUsageByTask.total_runs + 1,
                        "total_input_tokens": (
                            PlatformTokenUsageByTask.total_input_tokens + normalized_input
                        ),
                        "total_output_tokens": (
                            PlatformTokenUsageByTask.total_output_tokens + normalized_output
                        ),
                        "total_cost_usd": (
                            PlatformTokenUsageByTask.total_cost_usd + normalized_cost
                        ),
                    },
                )
            )
            await db.execute(task_stmt)
        else:
            existing = await db.scalar(
                select(PlatformTokenUsage).where(
                    PlatformTokenUsage.workspace_id == workspace_id,
                    PlatformTokenUsage.model_used == model_used,
                    PlatformTokenUsage.provider == provider,
                    PlatformTokenUsage.usage_date == today,
                )
            )
            if existing:
                existing.total_runs += 1
                existing.total_input_tokens += normalized_input
                existing.total_output_tokens += normalized_output
                existing.total_cost_usd += normalized_cost
            else:
                db.add(
                    PlatformTokenUsage(
                        workspace_id=workspace_id,
                        model_used=model_used,
                        provider=provider,
                        usage_date=today,
                        total_runs=1,
                        total_input_tokens=normalized_input,
                        total_output_tokens=normalized_output,
                        total_cost_usd=normalized_cost,
                    )
                )
            task_usage = await db.scalar(
                select(PlatformTokenUsageByTask).where(
                    PlatformTokenUsageByTask.workspace_id == workspace_id,
                    PlatformTokenUsageByTask.task_class == normalized_task_class,
                    PlatformTokenUsageByTask.model_used == model_used,
                    PlatformTokenUsageByTask.provider == provider,
                    PlatformTokenUsageByTask.usage_date == today,
                )
            )
            if task_usage:
                task_usage.total_runs += 1
                task_usage.total_input_tokens += normalized_input
                task_usage.total_output_tokens += normalized_output
                task_usage.total_cost_usd += normalized_cost
            else:
                db.add(
                    PlatformTokenUsageByTask(
                        workspace_id=workspace_id,
                        task_class=normalized_task_class,
                        model_used=model_used,
                        provider=provider,
                        usage_date=today,
                        total_runs=1,
                        total_input_tokens=normalized_input,
                        total_output_tokens=normalized_output,
                        total_cost_usd=normalized_cost,
                    )
                )
        await db.flush()

    @staticmethod
    async def _insert_model_usage_receipt(
        db: AsyncSession,
        *,
        values: dict[str, Any],
    ) -> bool:
        """Insert an immutable receipt or prove that an exact replay already exists."""

        dialect = db.bind.dialect.name if db.bind is not None else ""
        statement = None
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as dialect_insert

            statement = (
                dialect_insert(PlatformModelUsageLedger)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["workspace_id", "request_id"])
                .returning(PlatformModelUsageLedger.id)
            )
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as dialect_insert

            statement = (
                dialect_insert(PlatformModelUsageLedger)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["workspace_id", "request_id"])
                .returning(PlatformModelUsageLedger.id)
            )

        if statement is not None:
            inserted_id = await db.scalar(statement)
            if inserted_id is not None:
                return True
        else:
            existing = await db.scalar(
                select(PlatformModelUsageLedger).where(
                    PlatformModelUsageLedger.workspace_id == values["workspace_id"],
                    PlatformModelUsageLedger.request_id == values["request_id"],
                )
            )
            if existing is None:
                db.add(PlatformModelUsageLedger(**values))
                await db.flush()
                return True

        existing = await db.scalar(
            select(PlatformModelUsageLedger).where(
                PlatformModelUsageLedger.workspace_id == values["workspace_id"],
                PlatformModelUsageLedger.request_id == values["request_id"],
            )
        )
        if existing is None:
            raise RuntimeError("Recibo de uso não pôde ser inserido nem relido.")
        comparable = (
            "task_class",
            "operation",
            "model_used",
            "provider",
            "provider_request_id",
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "price_catalog_version",
            "route_catalog_version",
        )
        if any(getattr(existing, field) != values[field] for field in comparable) or (
            Decimal(existing.cost_usd) != Decimal(values["cost_usd"])
        ):
            raise ValueError("request_id de uso de IA foi repetido com medição divergente.")
        return False

    # ── Pessoas (gestão macro de usuários, Sprint B1) ────────────────────

    async def list_people(
        self,
        *,
        search: Optional[str] = None,
        role: Optional[str] = None,
        workspace_id: Optional[UUID] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Todos os usuários humanos de todos os workspaces, com busca global."""
        stmt = (
            select(User, WorkspaceMembership, Workspace.name)
            .join(WorkspaceMembership, WorkspaceMembership.user_id == User.id)
            .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
            .where(User.agent_type == AgentType.human, User.deleted_at.is_(None))
        )
        if search:
            like = f"%{search}%"
            stmt = stmt.where((User.name.ilike(like)) | (User.email.ilike(like)))
        if role:
            stmt = stmt.where(WorkspaceMembership.role == role)
        if workspace_id:
            stmt = stmt.where(WorkspaceMembership.workspace_id == workspace_id)
        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = int((await self.db.scalar(count_stmt)) or 0)
        stmt = stmt.order_by(User.name.asc(), User.id.asc()).limit(limit).offset(offset)

        rows = (await self.db.execute(stmt)).all()
        items = []
        now = datetime.now(timezone.utc)
        for user, membership, workspace_name in rows:
            invitation = await self.db.scalar(
                select(WorkspaceInvitation)
                .where(WorkspaceInvitation.membership_id == membership.id)
                .order_by(WorkspaceInvitation.created_at.desc())
                .limit(1)
            )
            last_login_at = await self.db.scalar(
                select(func.max(UserSession.last_seen_at)).where(UserSession.user_id == user.id)
            )
            active_session_count = int(
                (
                    await self.db.scalar(
                        select(func.count(UserSession.id)).where(
                            UserSession.user_id == user.id,
                            UserSession.revoked_at.is_(None),
                            UserSession.expires_at > now,
                        )
                    )
                )
                or 0
            )
            items.append(
                {
                    "user_id": str(user.id),
                    "name": user.name,
                    "email": user.email,
                    "is_active": user.is_active,
                    "workspace_id": str(membership.workspace_id),
                    "workspace_name": workspace_name,
                    "role": membership.role.value
                    if hasattr(membership.role, "value")
                    else membership.role,
                    "access_package": membership.access_package,
                    "membership_status": (
                        membership.status.value
                        if hasattr(membership.status, "value")
                        else membership.status
                    ),
                    "last_seen_at": last_login_at.isoformat() if last_login_at else None,
                    "active_session_count": active_session_count,
                    "invitation_id": str(invitation.id) if invitation else None,
                    "invitation_status": invitation.status.value if invitation else None,
                    "invitation_expires_at": (
                        invitation.expires_at.isoformat() if invitation else None
                    ),
                }
            )
        return {
            "items": items,
            "limit": limit,
            "offset": offset,
            "count": total,
            "total": total,
            "hasMore": offset + len(items) < total,
        }

    async def invite_person(
        self,
        *,
        workspace_id: UUID,
        name: str,
        email: str,
        access_package: str,
        actor: User,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        package = get_workspace_access_package(access_package)
        if package is None:
            raise ValueError("Pacote de acesso não encontrado.")
        invitation = await IdentityLifecycleService(self.db).create_invitation(
            workspace_id=workspace_id,
            actor_user_id=actor.id,
            payload=TeamMemberCreate(
                name=name,
                email=email,
                workspace_role=package.workspace_role,
                access_package=package.key,
            ),
            idempotency_key=idempotency_key,
            permission_grants=[
                {"module": item.module, "action": item.action, "scope": item.scope}
                for item in package.permission_grants
            ],
            permission_denials=[
                {"module": item.module, "action": item.action, "scope": item.scope}
                for item in package.permission_denials
            ],
            access_package=package.key,
        )
        self.db.add(
            AuthSecurityEvent(
                event_type="platform_workspace_user_invited",
                outcome="success",
                actor_user_id=actor.id,
                subject_user_id=invitation.user_id,
                details={
                    "workspace_id": str(workspace_id),
                    "invitation_id": str(invitation.id),
                    "access_package": package.key,
                },
            )
        )
        await self.db.flush()
        return self._serialize_platform_invitation(invitation)

    async def resend_person_invitation(
        self,
        *,
        workspace_id: UUID,
        invitation_id: UUID,
        actor: User,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        invitation = await IdentityLifecycleService(self.db).resend_invitation(
            workspace_id=workspace_id,
            invitation_id=invitation_id,
            idempotency_key=idempotency_key,
            commit_expiration=False,
        )
        self.db.add(
            AuthSecurityEvent(
                event_type="platform_workspace_invitation_resent",
                outcome="success",
                actor_user_id=actor.id,
                subject_user_id=invitation.user_id,
                details={"workspace_id": str(workspace_id), "invitation_id": str(invitation.id)},
            )
        )
        await self.db.flush()
        return self._serialize_platform_invitation(invitation)

    async def revoke_person_invitation(
        self,
        *,
        workspace_id: UUID,
        invitation_id: UUID,
        actor: User,
    ) -> Dict[str, Any]:
        invitation = await IdentityLifecycleService(self.db).revoke_invitation(
            workspace_id=workspace_id,
            invitation_id=invitation_id,
        )
        self.db.add(
            AuthSecurityEvent(
                event_type="platform_workspace_invitation_revoked",
                outcome="success",
                actor_user_id=actor.id,
                subject_user_id=invitation.user_id,
                details={"workspace_id": str(workspace_id), "invitation_id": str(invitation.id)},
            )
        )
        await self.db.flush()
        return self._serialize_platform_invitation(invitation)

    @staticmethod
    def _serialize_platform_invitation(invitation: WorkspaceInvitation) -> Dict[str, Any]:
        return {
            "id": str(invitation.id),
            "workspace_id": str(invitation.workspace_id),
            "membership_id": str(invitation.membership_id),
            "user_id": str(invitation.user_id),
            "email": invitation.invited_email,
            "name": invitation.invited_name,
            "status": invitation.status.value,
            "expires_at": invitation.expires_at.isoformat(),
            "send_count": invitation.send_count,
        }

    async def set_person_blocked(
        self, *, user_id: UUID, workspace_id: UUID, blocked: bool, actor: User
    ) -> Dict[str, Any]:
        """Bloqueia/desbloqueia o acesso de um usuário a um workspace (suspende a membership)."""
        membership = await self.db.scalar(
            select(WorkspaceMembership)
            .where(
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if membership is None:
            raise ValueError("Membership não encontrada.")
        if blocked and membership.role == WorkspaceRole.workspace_owner:
            another_owner_count = int(
                (
                    await self.db.scalar(
                        select(func.count(WorkspaceMembership.id)).where(
                            WorkspaceMembership.workspace_id == workspace_id,
                            WorkspaceMembership.user_id != user_id,
                            WorkspaceMembership.role == WorkspaceRole.workspace_owner,
                            WorkspaceMembership.status == MembershipStatus.active,
                        )
                    )
                )
                or 0
            )
            if another_owner_count == 0:
                raise ValueError(
                    "Transfira a titularidade antes de suspender o único titular ativo."
                )
        before = membership.status.value
        membership.status = MembershipStatus.suspended if blocked else MembershipStatus.active
        if blocked:
            sessions = list(
                (
                    await self.db.scalars(
                        select(UserSession)
                        .where(
                            UserSession.user_id == user_id,
                            UserSession.last_workspace_id == workspace_id,
                            UserSession.revoked_at.is_(None),
                        )
                        .with_for_update()
                    )
                ).all()
            )
            now = datetime.now(timezone.utc)
            for session in sessions:
                session.revoked_at = now
                session.revocation_reason = "platform_membership_suspended"
        else:
            sessions = []
        self.db.add(
            AuthSecurityEvent(
                event_type="platform_workspace_user_access_changed",
                outcome="success",
                actor_user_id=actor.id,
                subject_user_id=user_id,
                details={
                    "workspace_id": str(workspace_id),
                    "before": before,
                    "after": membership.status.value,
                    "revoked_sessions": len(sessions),
                },
            )
        )
        await self.db.flush()
        return {
            "user_id": str(user_id),
            "workspace_id": str(workspace_id),
            "membership_status": membership.status.value,
        }

    async def revoke_person_sessions(
        self, *, user_id: UUID, workspace_id: UUID, actor: User
    ) -> Dict[str, Any]:
        membership = await self.db.scalar(
            select(WorkspaceMembership)
            .where(
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if membership is None:
            raise ValueError("Membership não encontrada.")
        sessions = list(
            (
                await self.db.scalars(
                    select(UserSession)
                    .where(
                        UserSession.user_id == user_id,
                        UserSession.revoked_at.is_(None),
                    )
                    .with_for_update()
                )
            ).all()
        )
        now = datetime.now(timezone.utc)
        for session in sessions:
            session.revoked_at = now
            session.revocation_reason = "platform_workspace_user_sessions_revoked"
        self.db.add(
            AuthSecurityEvent(
                event_type="platform_workspace_user_sessions_revoked",
                outcome="success",
                actor_user_id=actor.id,
                subject_user_id=user_id,
                details={"workspace_id": str(workspace_id), "revoked_count": len(sessions)},
            )
        )
        await self.db.flush()
        return {
            "user_id": str(user_id),
            "workspace_id": str(workspace_id),
            "revoked_count": len(sessions),
        }

    async def transfer_workspace_ownership(
        self,
        *,
        user_id: UUID,
        workspace_id: UUID,
        actor: User,
        reason: str,
    ) -> Dict[str, Any]:
        memberships = list(
            (
                await self.db.scalars(
                    select(WorkspaceMembership)
                    .where(
                        WorkspaceMembership.workspace_id == workspace_id,
                        WorkspaceMembership.status == MembershipStatus.active,
                    )
                    .with_for_update()
                )
            ).all()
        )
        target = next((item for item in memberships if item.user_id == user_id), None)
        if target is None:
            raise ValueError("O novo titular precisa possuir um vínculo ativo com a conta.")
        previous_owners = [
            item
            for item in memberships
            if item.role == WorkspaceRole.workspace_owner and item.user_id != user_id
        ]
        before = {
            "owners": [
                str(item.user_id)
                for item in memberships
                if item.role == WorkspaceRole.workspace_owner
            ],
            "target_role": target.role.value,
            "target_access_package": target.access_package,
        }
        for membership in previous_owners:
            membership.role = WorkspaceRole.workspace_admin
            membership.access_package = "office_admin"
            membership.permission_grants = []
            membership.permission_denials = []
        target.role = WorkspaceRole.workspace_owner
        target.access_package = "office_owner"
        target.permission_grants = []
        target.permission_denials = []

        affected_user_ids = {target.user_id, *(item.user_id for item in previous_owners)}
        sessions = list(
            (
                await self.db.scalars(
                    select(UserSession)
                    .where(
                        UserSession.user_id.in_(affected_user_ids),
                        UserSession.revoked_at.is_(None),
                    )
                    .with_for_update()
                )
            ).all()
        )
        now = datetime.now(timezone.utc)
        for session in sessions:
            session.revoked_at = now
            session.revocation_reason = "platform_workspace_ownership_transferred"
        self.db.add(
            AuthSecurityEvent(
                event_type="platform_workspace_ownership_transferred",
                outcome="success",
                actor_user_id=actor.id,
                subject_user_id=target.user_id,
                details={
                    "workspace_id": str(workspace_id),
                    "reason": reason,
                    "before": before,
                    "after": {"owner": str(target.user_id), "access_package": "office_owner"},
                    "demoted_user_ids": [str(item.user_id) for item in previous_owners],
                    "revoked_sessions": len(sessions),
                },
            )
        )
        await self.db.flush()
        return {
            "workspace_id": str(workspace_id),
            "owner_user_id": str(target.user_id),
            "demoted_user_ids": [str(item.user_id) for item in previous_owners],
            "revoked_sessions": len(sessions),
        }

    async def set_person_access_package(
        self, *, user_id: UUID, workspace_id: UUID, access_package: str, actor: User
    ) -> Dict[str, Any]:
        """Troca o pacote de acesso (papel/permissões) de um usuário num workspace."""
        membership = await self.db.scalar(
            select(WorkspaceMembership)
            .where(
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if membership is None:
            raise ValueError("Membership não encontrada.")
        package = get_workspace_access_package(access_package)
        if package is None:
            raise ValueError("Pacote de acesso não encontrado.")
        before = {
            "access_package": membership.access_package,
            "role": membership.role.value,
            "permission_grants": membership.permission_grants or [],
            "permission_denials": membership.permission_denials or [],
        }
        membership.access_package = access_package
        membership.role = package.workspace_role
        membership.permission_grants = [
            {"module": item.module, "action": item.action, "scope": item.scope}
            for item in package.permission_grants
        ]
        membership.permission_denials = [
            {"module": item.module, "action": item.action, "scope": item.scope}
            for item in package.permission_denials
        ]
        self.db.add(
            AuthSecurityEvent(
                event_type="platform_workspace_user_package_changed",
                outcome="success",
                actor_user_id=actor.id,
                subject_user_id=user_id,
                details={
                    "workspace_id": str(workspace_id),
                    "before": before,
                    "after": {
                        "access_package": membership.access_package,
                        "role": membership.role.value,
                        "permission_grants": membership.permission_grants,
                        "permission_denials": membership.permission_denials,
                    },
                },
            )
        )
        await self.db.flush()
        return {
            "user_id": str(user_id),
            "workspace_id": str(workspace_id),
            "access_package": membership.access_package,
            "role": membership.role.value,
        }

    async def get_workspace_360(self, workspace_id: UUID) -> Dict[str, Any]:
        """Drawer consolidado: assinatura + consumo + saúde Meta + erros + usuários."""
        from app.services.platform_billing_service import PlatformBillingService

        workspace = await self.db.get(Workspace, workspace_id)
        if workspace is None:
            raise ValueError(f"Workspace {workspace_id} não encontrado.")

        billing_svc = PlatformBillingService(self.db)

        async def load_or_none(awaitable: Any) -> Any:
            try:
                return await awaitable
            except Exception:
                return None

        # AsyncSession não suporta operações concorrentes na mesma transação.
        # Cada fonte continua isolada, porém é lida sequencialmente para evitar
        # estados concorrentes difíceis de reproduzir em PostgreSQL.
        subscription = await load_or_none(billing_svc.get_subscription(workspace_id))
        pnl = await load_or_none(billing_svc.get_workspace_pnl(workspace_id))
        events = await load_or_none(billing_svc.list_events(workspace_id, limit=10))
        token_usage = await load_or_none(
            self.get_token_usage_summary(days=30, workspace_id=workspace_id)
        )
        meta_health = await load_or_none(
            self.get_meta_health(days=7, workspace_id=workspace_id, limit=10)
        )
        people = await load_or_none(self.list_people(workspace_id=workspace_id, limit=100))

        return {
            "workspace_id": str(workspace_id),
            "workspace_name": workspace.name,
            "subscription": subscription,
            "pnl": pnl,
            "recent_events": events,
            "token_usage_30d": token_usage,
            "meta_health_7d": meta_health,
            "people": people,
        }
