"""
Service: Platform Metrics — motor de KPIs do Super Admin (Sprint "Painel Admin
com bateria completa de KPIs").

Consolida ~150 indicadores organizados por categoria (Receita, Assinantes,
Uso/Engajamento, Aquisição, Operação/IA, Saúde) a partir de dados
JÁ PERSISTIDOS: contratos comerciais canônicos mais assinaturas legadas ainda
não reconciliadas, PlatformTokenUsage/PlatformModelUsageLedger
(custo real de IA, app.models.billing), AcquisitionEvent (app.models.klm_operations),
Matter, WhatsAppMessage/WhatsAppConnection, WorkspaceMembership, AuthSecurityEvent/
MfaCredential (app.models.auth_security).

Política no-fake: nenhum KPI é inventado. Quando a fonte de dado não existe no
sistema (ex.: DAU/WAU/MAU exigem log de sessão/login de usuário de tenant, que
não existe hoje — só há AdministrativeSession para staff da plataforma em MFA),
o KPI é retornado com `sem_dado=True` e uma nota honesta explicando o motivo,
nunca com um número fabricado.

Fórmulas documentadas (ver docstrings de cada método):
  - MRR/ARR: soma do preço efetivo canônico; usa WorkspaceSubscription apenas
    para workspaces ainda sem contrato canônico.
  - MRR movement (novo/expansão/contração/churn): eventos canônicos e eventos
    legados excluindo workspaces já reconciliados.
  - Net profit = MRR − custo de infra rateado − custo real de IA (PlatformTokenUsage,
    convertido USD→BRL por câmbio de referência) − custo estimado de WhatsApp
    (PLAN_WHATSAPP_COST_CENTS, mesma tabela usada em platform_billing_service).
  - EBITDA aproximado = net profit (proxy: não há depreciação/imposto modelados
    separadamente nesta base local-first — documentado como aproximação).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth_security import AdministrativeSession, AuthSecurityEvent, MfaCredential
from app.models.billing import PlatformModelUsageLedger, PlatformTokenUsage
from app.models.commercial_catalog import (
    CommercialCatalogEvent,
    WorkspaceCommercialContract,
)
from app.models.klm_operations import AcquisitionEvent
from app.models.matter import Matter
from app.models.platform_inbox import (
    PlatformInboxConversation,
    PlatformInboxMessage,
    PlatformInboxStatus,
)
from app.models.subscription_commercial import (
    SubscriptionEvent,
    SubscriptionEventType,
)
from app.models.support_ticket import SupportTicket, SupportTicketStatus
from app.models.user import User
from app.models.whatsapp import (
    HumanControlState,
    WaConnectionStatus,
    WaMessageDirection,
    WaMessageStatus,
    WaQueueStatus,
    WhatsAppConnection,
    WhatsAppContact,
    WhatsAppMessage,
)
from app.models.workspace import Workspace, WorkspaceStatus
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.services.commercial_legacy_bridge_service import (
    CanonicalRevenueReadModel,
    CommercialRevenueRecord,
)
from app.services.platform_billing_service import (
    PLAN_WHATSAPP_COST_CENTS,
)

# Câmbio de referência USD->BRL (mesmo usado em platform_billing_service.get_workspace_pnl).
USD_BRL_REF = Decimal("5.50")

# Custo mensal de infra estimado por workspace ativo (rateio simples de referência;
# documentado — não há telemetria de custo de infra por tenant nesta base).
INFRA_COST_CENTS_PER_ACTIVE_WORKSPACE = 1500

ACTIVE_SUB_STATUSES = {"trial", "ativa", "inadimplente"}
PAYING_SUB_STATUSES = {"ativa", "inadimplente"}
COMMERCIAL_STATUSES = ("trial", "ativa", "inadimplente", "pausada", "cancelada")
COMMERCIAL_PLAN_SKUS = (
    "crm_start",
    "crm_advanced",
    "crm_evolution",
    "legal_360",
    "legacy_custom",
)

CATEGORIES = (
    "receita",
    "assinaturas",
    "cobranca",
    "retencao",
    "churn",
    "aquisicao",
    "vendas",
    "onboarding",
    "sucesso_cliente",
    "suporte",
    "conversas",
    "canais",
    "produto",
    "ia",
    "infraestrutura",
    "equipe",
    "seguranca",
)

CATEGORY_LABELS = {
    "receita": "Receita",
    "assinaturas": "Assinaturas",
    "cobranca": "Cobrança",
    "retencao": "Retenção",
    "churn": "Churn",
    "aquisicao": "Aquisição",
    "vendas": "Vendas",
    "onboarding": "Onboarding",
    "sucesso_cliente": "Sucesso do cliente",
    "suporte": "Suporte",
    "conversas": "Conversas",
    "canais": "Canais",
    "produto": "Produto",
    "ia": "IA",
    "infraestrutura": "Infraestrutura",
    "equipe": "Equipe",
    "seguranca": "Segurança",
}

LEGACY_CATEGORY_ALIASES = {
    "assinantes": "assinaturas",
    "uso_engajamento": "produto",
    "operacao_ia": "ia",
    "saude": "sucesso_cliente",
}


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@dataclass
class Kpi:
    key: str
    label: str
    category: str
    value: Optional[float] = None
    unit: str = "number"  # number | brl | pct | count
    delta_pct: Optional[float] = None
    series: Optional[list[dict[str, Any]]] = None
    sem_dado: bool = False
    nota: Optional[str] = None
    definition: Optional[str] = None
    formula: Optional[str] = None
    source: Optional[str] = None
    period: Optional[str] = None
    filters: list[str] = field(default_factory=list)
    updated_at: Optional[str] = None
    quality: Optional[str] = None
    coverage: Optional[str] = None
    owner: Optional[str] = None
    drill_down: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "category": self.category,
            "value": self.value,
            "unit": self.unit,
            "delta_pct": self.delta_pct,
            "series": self.series,
            "sem_dado": self.sem_dado,
            "nota": self.nota,
            "definition": self.definition or self.label,
            "formula": self.formula or "Agregação dos registros persistidos na fonte indicada.",
            "source": self.source or _source_for_category(self.category),
            "period": self.period,
            "filters": self.filters,
            "updated_at": self.updated_at,
            "quality": self.quality or ("indisponivel" if self.sem_dado else "atualizado"),
            "coverage": self.coverage or ("sem_cobertura" if self.sem_dado else "integral"),
            "owner": self.owner or _owner_for_category(self.category),
            "drill_down": self.drill_down,
        }


def _source_for_category(category: str) -> str:
    return {
        "receita": "commercial_contracts + legacy_unreconciled + platform_usage_ledger",
        "assinaturas": "commercial_contracts + legacy_unreconciled",
        "cobranca": "commercial_contracts + commercial_catalog_events",
        "retencao": "commercial_contracts + commercial_catalog_events",
        "churn": "commercial_contracts + commercial_catalog_events",
        "aquisicao": "acquisition_events",
        "vendas": "growth_prospects + acquisition_events",
        "onboarding": "workspaces",
        "sucesso_cliente": "workspaces + workspace_memberships",
        "suporte": "support_tickets",
        "conversas": "platform_inbox_conversations + platform_inbox_messages",
        "canais": "platform_inbox_conversations + platform_inbox_messages",
        "produto": "matters + workspace_memberships + whatsapp_messages",
        "ia": "platform_token_usage + platform_model_usage_ledger",
        "infraestrutura": "status runtime; sem série histórica persistida",
        "equipe": "users",
        "seguranca": "administrative_sessions + auth_security_events + mfa_credentials",
    }.get(category, "fonte não catalogada")


def _owner_for_category(category: str) -> str:
    return {
        "receita": "Financeiro",
        "assinaturas": "Financeiro",
        "cobranca": "Cobrança",
        "retencao": "Customer Success",
        "churn": "Customer Success",
        "aquisicao": "Marketing",
        "vendas": "Comercial",
        "onboarding": "Onboarding",
        "sucesso_cliente": "Customer Success",
        "suporte": "Suporte",
        "conversas": "Operações",
        "canais": "Operações",
        "produto": "Produto",
        "ia": "Produto e IA",
        "infraestrutura": "Engenharia",
        "equipe": "Operações",
        "seguranca": "Segurança",
    }.get(category, "Super Admin")


class PlatformMetricsService:
    """Motor de KPIs do Super Admin. Todas as leituras; nenhuma escrita."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── API pública ──────────────────────────────────────────────────────

    async def get_all_metrics(self, *, period_days: int = 30) -> dict[str, Any]:
        kpis = await self._build_all(period_days=period_days)
        by_category: dict[str, list[dict[str, Any]]] = {c: [] for c in CATEGORIES}
        for kpi in kpis:
            by_category[kpi.category].append(kpi.to_dict())
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "period_days": period_days,
            "total_kpis": len(kpis),
            "categories": [
                {
                    "key": cat,
                    "label": CATEGORY_LABELS[cat],
                    "count": len(by_category[cat]),
                    "kpis": by_category[cat],
                }
                for cat in CATEGORIES
            ],
        }

    async def get_category_metrics(self, category: str, *, period_days: int = 30) -> dict[str, Any]:
        category = LEGACY_CATEGORY_ALIASES.get(category, category)
        if category not in CATEGORIES:
            raise ValueError(f"Categoria desconhecida: {category}")
        kpis = [k for k in await self._build_all(period_days=period_days) if k.category == category]
        return {
            "key": category,
            "label": CATEGORY_LABELS[category],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "period_days": period_days,
            "count": len(kpis),
            "kpis": [k.to_dict() for k in kpis],
        }

    # ── Orquestração ─────────────────────────────────────────────────────

    async def _build_all(self, *, period_days: int) -> list[Kpi]:
        subs = await CanonicalRevenueReadModel(self.db).list_records()
        kpis: list[Kpi] = []
        kpis += await self._receita_kpis(subs, period_days=period_days)
        kpis += await self._assinantes_kpis(subs, period_days=period_days)
        kpis += await self._uso_engajamento_kpis(period_days=period_days)
        kpis += await self._aquisicao_kpis(period_days=period_days)
        kpis += await self._operacao_ia_kpis(period_days=period_days)
        kpis += await self._saude_kpis(subs, period_days=period_days)
        self._normalize_categories(kpis)
        kpis += await self._operational_kpis(period_days=period_days)
        for category in CATEGORIES:
            if not any(item.category == category for item in kpis):
                kpis.append(Kpi(
                    f"{category}.cobertura",
                    f"Cobertura de {CATEGORY_LABELS[category]}",
                    category,
                    None,
                    "count",
                    sem_dado=True,
                    nota="Fonte operacional ainda não consolidada para este recorte.",
                    quality="indisponivel",
                    coverage="sem_cobertura",
                ))
        generated_at = datetime.now(timezone.utc).isoformat()
        for kpi in kpis:
            kpi.period = kpi.period or f"últimos {period_days} dias"
            kpi.updated_at = kpi.updated_at or generated_at
            kpi.filters = kpi.filters or ["period_days"]
        return kpis

    @staticmethod
    def _normalize_categories(kpis: list[Kpi]) -> None:
        """Migra a bateria histórica para a taxonomia operacional do Blackbox."""
        for kpi in kpis:
            key = kpi.key.lower()
            if kpi.category == "assinantes":
                kpi.category = "assinaturas"
            elif kpi.category == "uso_engajamento":
                kpi.category = "produto"
            elif kpi.category == "operacao_ia":
                kpi.category = "ia"
            elif kpi.category == "saude":
                kpi.category = "sucesso_cliente"

            if "churn" in key:
                kpi.category = "churn"
            elif any(token in key for token in ("nrr", "grr", "retenc", "quick_ratio")):
                kpi.category = "retencao"
            elif any(token in key for token in ("inadimpl", "desconto", "trial")):
                kpi.category = "cobranca"
            elif any(token in key for token in ("mfa", "segur", "auth_")):
                kpi.category = "seguranca"

    async def _operational_kpis(self, *, period_days: int) -> list[Kpi]:
        """Indicadores transversais dos módulos operacionais já persistidos."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=period_days)
        open_ticket_statuses = tuple(
            status for status in SupportTicketStatus
            if status.value not in {"resolved", "closed", "cancelled"}
        )
        tickets_open = await self.db.scalar(
            select(func.count(SupportTicket.id)).where(SupportTicket.status.in_(open_ticket_statuses))
        ) or 0
        tickets_created = await self.db.scalar(
            select(func.count(SupportTicket.id)).where(SupportTicket.created_at >= cutoff)
        ) or 0
        tickets_sla_overdue = await self.db.scalar(
            select(func.count(SupportTicket.id)).where(
                SupportTicket.status.in_(open_ticket_statuses),
                SupportTicket.first_responded_at.is_(None),
                SupportTicket.first_response_due_at < now,
            )
        ) or 0

        conversations_open = await self.db.scalar(
            select(func.count(PlatformInboxConversation.id)).where(
                PlatformInboxConversation.status != PlatformInboxStatus.resolved
            )
        ) or 0
        inbound_messages = await self.db.scalar(
            select(func.count(PlatformInboxMessage.id)).where(
                PlatformInboxMessage.occurred_at >= cutoff,
                PlatformInboxMessage.direction == "inbound",
            )
        ) or 0
        active_channels = await self.db.scalar(
            select(func.count(func.distinct(PlatformInboxConversation.channel))).where(
                PlatformInboxConversation.last_message_at >= cutoff
            )
        ) or 0

        workspaces_total = await self.db.scalar(select(func.count(Workspace.id))) or 0
        workspaces_onboarded = await self.db.scalar(
            select(func.count(Workspace.id)).where(Workspace.onboarding_completed.is_(True))
        ) or 0
        onboarding_rate = (
            float(workspaces_onboarded) / float(workspaces_total) * 100
            if workspaces_total else None
        )
        staff_active = await self.db.scalar(
            select(func.count(User.id)).where(
                User.is_internal_staff.is_(True),
                User.platform_access_active.is_(True),
                User.is_active.is_(True),
            )
        ) or 0
        active_admin_sessions = await self.db.scalar(
            select(func.count(AdministrativeSession.id)).where(
                AdministrativeSession.revoked_at.is_(None),
                AdministrativeSession.expires_at > now,
            )
        ) or 0
        denied_security_events = await self.db.scalar(
            select(func.count(AuthSecurityEvent.id)).where(
                AuthSecurityEvent.occurred_at >= cutoff,
                AuthSecurityEvent.outcome.in_(("denied", "failed")),
            )
        ) or 0

        kpis = [
            Kpi("suporte.abertos", "Tickets em aberto", "suporte", float(tickets_open), "count", drill_down="/blackbox/support"),
            Kpi("suporte.criados", "Tickets criados no período", "suporte", float(tickets_created), "count", drill_down="/blackbox/support"),
            Kpi("suporte.sla_primeira_resposta_vencido", "Tickets com SLA de primeira resposta vencido", "suporte", float(tickets_sla_overdue), "count", drill_down="/blackbox/support"),
            Kpi("conversas.abertas", "Conversas abertas", "conversas", float(conversations_open), "count", drill_down="/blackbox/inbox"),
            Kpi("conversas.entrantes", "Mensagens recebidas no período", "conversas", float(inbound_messages), "count", drill_down="/blackbox/inbox"),
            Kpi("canais.ativos", "Canais com atividade no período", "canais", float(active_channels), "count", drill_down="/blackbox/inbox"),
            Kpi("onboarding.contas_concluidas", "Contas com onboarding concluído", "onboarding", float(workspaces_onboarded), "count", drill_down="/blackbox/customers"),
            Kpi("onboarding.taxa_conclusao", "Taxa de conclusão do onboarding", "onboarding", onboarding_rate, "pct", sem_dado=onboarding_rate is None, nota=None if onboarding_rate is not None else "Nenhuma conta cadastrada.", formula="contas com onboarding concluído / total de contas × 100", drill_down="/blackbox/customers"),
            Kpi("equipe.funcionarios_ativos", "Funcionários ativos", "equipe", float(staff_active), "count", drill_down="/blackbox/staff"),
            Kpi("seguranca.sessoes_administrativas_ativas", "Sessões administrativas ativas", "seguranca", float(active_admin_sessions), "count", drill_down="/blackbox/governance"),
            Kpi("seguranca.eventos_negados_falhos", "Eventos de segurança negados ou falhos", "seguranca", float(denied_security_events), "count", drill_down="/blackbox/governance"),
            Kpi("infraestrutura.disponibilidade_historica", "Disponibilidade histórica", "infraestrutura", None, "pct", sem_dado=True, nota="A saúde atual existe, mas ainda não há série histórica de uptime persistida.", source="status runtime", coverage="indisponivel", drill_down="/blackbox/infrastructure"),
        ]
        return kpis

    # ── Receita ──────────────────────────────────────────────────────────

    async def _receita_kpis(
        self,
        subs: list[CommercialRevenueRecord],
        *,
        period_days: int,
    ) -> list[Kpi]:
        kpis: list[Kpi] = []

        mrr_cents = sum(s.mrr_cents for s in subs)
        arr_cents = mrr_cents * 12
        kpis.append(Kpi("receita.mrr", "MRR", "receita", mrr_cents / 100, "brl",
                         series=self._mrr_series(subs, months=6)))
        kpis.append(Kpi("receita.arr", "ARR", "receita", arr_cents / 100, "brl"))

        paying = [s for s in subs if s.status in PAYING_SUB_STATUSES]
        active = [s for s in subs if s.status in ACTIVE_SUB_STATUSES]
        arpa = (mrr_cents / len(paying) / 100) if paying else None
        kpis.append(Kpi("receita.arpa", "ARPA (receita média por conta paga)", "receita", arpa, "brl",
                         sem_dado=arpa is None, nota=None if arpa is not None else "Sem contas pagantes no período."))
        # ARPU: aproxima usuário = membership ativo por workspace pagante.
        total_users_paying = await self._count_active_memberships_for_workspaces(
            [s.workspace_id for s in paying]
        )
        arpu = (mrr_cents / total_users_paying / 100) if total_users_paying else None
        kpis.append(Kpi("receita.arpu", "ARPU (receita média por usuário)", "receita", arpu, "brl",
                         sem_dado=arpu is None,
                         nota=None if arpu is not None else "Sem membros ativos em contas pagantes."))

        # Receita e assinantes por plano — quebra real (não fabricada) pelas 4 categorias
        # publicadas em docs/PRICING_BILLING_POLITICA_2026-07.md.
        for plano in COMMERCIAL_PLAN_SKUS:
            plano_subs = [s for s in active if s.sku == plano]
            plano_mrr = sum(s.mrr_cents for s in plano_subs)
            kpis.append(Kpi(
                f"receita.por_plano.{plano}",
                f"Receita mensal — plano {plano}",
                "receita", plano_mrr / 100, "brl",
            ))

        # Custos e lucro
        custo_ia_usd, custo_ia_series = await self._custo_ia_total(period_days=period_days)
        custo_ia_cents = int(custo_ia_usd * float(USD_BRL_REF) * 100)
        custo_whatsapp_cents = sum(
            PLAN_WHATSAPP_COST_CENTS.get(s.sku, 0)
            for s in active
        )
        custo_infra_cents = len(active) * INFRA_COST_CENTS_PER_ACTIVE_WORKSPACE
        custo_total_cents = custo_ia_cents + custo_whatsapp_cents + custo_infra_cents

        kpis.append(Kpi("receita.custo_ia", "Custo de IA (real, medido)", "receita",
                         custo_ia_cents / 100, "brl", series=custo_ia_series))
        kpis.append(Kpi("receita.custo_whatsapp_estimado", "Custo WhatsApp (estimado por plano)", "receita",
                         custo_whatsapp_cents / 100, "brl",
                         nota="Estimativa por plano (docs/PRICING_BILLING_POLITICA_2026-07.md §3.2); não há medição real de custo Meta por conversa nesta base."))
        kpis.append(Kpi("receita.custo_infra_estimado", "Custo de infra (rateio estimado)", "receita",
                         custo_infra_cents / 100, "brl",
                         nota=f"Rateio fixo de R$ {INFRA_COST_CENTS_PER_ACTIVE_WORKSPACE/100:.2f}/workspace ativo — sem telemetria de custo de infra por tenant."))

        kpis.append(Kpi("receita.custo_total", "Custo total (IA + WhatsApp + infra)", "receita",
                         custo_total_cents / 100, "brl"))
        net_profit_cents = mrr_cents - custo_total_cents
        kpis.append(Kpi("receita.lucro_liquido", "Lucro líquido (net profit)", "receita",
                         net_profit_cents / 100, "brl",
                         nota="MRR − (custo IA real + custo WhatsApp estimado + custo infra rateado)."))
        kpis.append(Kpi("receita.ebitda_aproximado", "EBITDA aproximado", "receita",
                         net_profit_cents / 100, "brl",
                         nota="Proxy = lucro líquido; base local-first não modela depreciação/impostos separadamente."))
        margem_bruta_pct = round((mrr_cents - custo_ia_cents - custo_whatsapp_cents) / mrr_cents * 100, 2) if mrr_cents else None
        margem_liquida_pct = round(net_profit_cents / mrr_cents * 100, 2) if mrr_cents else None
        kpis.append(Kpi("receita.margem_bruta_pct", "Margem bruta", "receita", margem_bruta_pct, "pct",
                         sem_dado=margem_bruta_pct is None, nota=None if margem_bruta_pct is not None else "Sem MRR no período."))
        kpis.append(Kpi("receita.margem_liquida_pct", "Margem líquida", "receita", margem_liquida_pct, "pct",
                         sem_dado=margem_liquida_pct is None, nota=None if margem_liquida_pct is not None else "Sem MRR no período."))

        # MRR movement
        movement = await self._mrr_movement(period_days=period_days)
        for k, label in (
            ("novo", "MRR novo"), ("expansao", "MRR expansão"),
            ("contracao", "MRR contração"), ("churned", "MRR churned"),
        ):
            kpis.append(Kpi(f"receita.mrr_movement.{k}", label, "receita", movement[k] / 100, "brl"))
        kpis.append(Kpi("receita.mrr_movement.net_new", "MRR líquido novo (net new MRR)", "receita",
                         (movement["novo"] + movement["expansao"] - movement["contracao"] - movement["churned"]) / 100,
                         "brl", nota="novo + expansão − contração − churned, no período."))

        # NRR / GRR / Quick ratio
        nrr, grr = await self._nrr_grr(subs, period_days=period_days)
        kpis.append(Kpi("receita.nrr", "Net Revenue Retention (NRR)", "receita", nrr, "pct",
                         sem_dado=nrr is None, nota=None if nrr is not None else "Sem MRR inicial no início do período de referência."))
        kpis.append(Kpi("receita.grr", "Gross Revenue Retention (GRR)", "receita", grr, "pct",
                         sem_dado=grr is None, nota=None if grr is not None else "Sem MRR inicial no início do período de referência."))
        quick_ratio = None
        if (movement["contracao"] + movement["churned"]) > 0:
            quick_ratio = round((movement["novo"] + movement["expansao"]) / (movement["contracao"] + movement["churned"]), 2)
        kpis.append(Kpi("receita.quick_ratio", "Quick Ratio (crescimento/perda de MRR)", "receita",
                         quick_ratio, "number", sem_dado=quick_ratio is None,
                         nota=None if quick_ratio is not None else "Sem contração/churn no período — divisão indefinida."))

        # LTV / CAC / Payback
        churn_rate_pct = self._logo_churn_rate(subs, period_days=period_days)
        ltv = None
        if paying:
            arpa_cents = mrr_cents / len(paying)
            monthly_churn = max(churn_rate_pct / 100, 0.01)
            ltv = round(arpa_cents / monthly_churn / 100, 2)
        kpis.append(Kpi("receita.ltv", "LTV estimado", "receita", ltv, "brl", sem_dado=ltv is None,
                         nota=None if ltv is not None else "Sem contas pagantes."))

        cac = await self._cac_estimate(period_days=period_days)
        kpis.append(Kpi("receita.cac", "CAC (custo de aquisição de cliente)", "receita",
                         cac, "brl", sem_dado=cac is None,
                         nota="Sem dado: AcquisitionEvent não registra custo/investimento de mídia (campo `value` é valor do evento de conversão, não spend); nenhum modelo de custo de marketing está integrado."))
        kpis.append(Kpi("receita.payback_meses", "Payback (meses)", "receita", None, "number",
                         sem_dado=True, nota="Depende de CAC, que está sem dado."))

        # Adicionais vendidos (add-ons)
        addons = await self._addons_kpis()
        kpis += addons

        return kpis

    def _mrr_series(
        self,
        subs: list[CommercialRevenueRecord],
        *,
        months: int,
    ) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        points: list[dict[str, Any]] = []
        for i in range(months - 1, -1, -1):
            marker = now - timedelta(days=30 * i)
            mrr_at = sum(
                s.mrr_cents for s in subs
                if s.created_at and _aware(s.created_at) <= marker
                and (s.cancelled_at is None or _aware(s.cancelled_at) > marker)
            )
            points.append({"period": marker.strftime("%Y-%m"), "value": round(mrr_at / 100, 2)})
        return points

    async def _custo_ia_total(self, *, period_days: int) -> tuple[float, list[dict[str, Any]]]:
        since = datetime.now(timezone.utc) - timedelta(days=period_days)
        rows = (
            await self.db.execute(
                select(PlatformTokenUsage.usage_date, PlatformTokenUsage.total_cost_usd)
                .where(PlatformTokenUsage.usage_date >= since)
            )
        ).all()
        total = sum(float(cost or 0) for _date, cost in rows)
        by_day: dict[str, float] = {}
        for usage_date, cost in rows:
            key = _aware(usage_date).strftime("%Y-%m-%d") if usage_date else "desconhecido"
            by_day[key] = by_day.get(key, 0.0) + float(cost or 0)
        series = [{"period": k, "value": round(v * float(USD_BRL_REF), 2)} for k, v in sorted(by_day.items())]
        return total, series

    async def _mrr_movement(self, *, period_days: int) -> dict[str, int]:
        """Derive movement once per workspace from canonical or legacy events."""
        since = datetime.now(timezone.utc) - timedelta(days=period_days)
        movement = {"novo": 0, "expansao": 0, "contracao": 0, "churned": 0}
        canonical_workspace_ids = set(
            (
                await self.db.scalars(
                    select(WorkspaceCommercialContract.workspace_id)
                )
            ).all()
        )
        canonical_events = list(
            (
                await self.db.scalars(
                    select(CommercialCatalogEvent).where(
                        CommercialCatalogEvent.occurred_at >= since,
                        CommercialCatalogEvent.resource_type
                        == "workspace_commercial_contract",
                    )
                )
            ).all()
        )
        for event in canonical_events:
            before_mrr = self._canonical_snapshot_mrr(event.before)
            after_mrr = self._canonical_snapshot_mrr(event.after)
            self._accumulate_mrr_movement(movement, before_mrr, after_mrr)

        legacy_query = select(SubscriptionEvent).where(
            SubscriptionEvent.occurred_at >= since
        )
        if canonical_workspace_ids:
            legacy_query = legacy_query.where(
                SubscriptionEvent.workspace_id.not_in(canonical_workspace_ids)
            )
        legacy_events = list((await self.db.scalars(legacy_query)).all())
        for ev in legacy_events:
            before_mrr = int((ev.before_json or {}).get("mrr_cents") or 0)
            after_mrr = int((ev.after_json or {}).get("mrr_cents") or 0)
            self._accumulate_mrr_movement(movement, before_mrr, after_mrr)
        return movement

    @staticmethod
    def _canonical_snapshot_mrr(snapshot: dict[str, Any] | None) -> int:
        if not snapshot:
            return 0
        status = str(snapshot.get("status") or "")
        price = snapshot.get("price") or {}
        recurring = int(price.get("recurring_price_cents") or 0)
        return recurring if status in {"active", "past_due"} else 0

    @staticmethod
    def _accumulate_mrr_movement(
        movement: dict[str, int],
        before_mrr: int,
        after_mrr: int,
    ) -> None:
        if before_mrr == 0 and after_mrr > 0:
            movement["novo"] += after_mrr
        elif after_mrr == 0 and before_mrr > 0:
            movement["churned"] += before_mrr
        elif after_mrr > before_mrr:
            movement["expansao"] += after_mrr - before_mrr
        elif after_mrr < before_mrr:
            movement["contracao"] += before_mrr - after_mrr

    async def _nrr_grr(
        self, subs: list[CommercialRevenueRecord], *, period_days: int
    ) -> tuple[Optional[float], Optional[float]]:
        since = datetime.now(timezone.utc) - timedelta(days=period_days)
        cohort = [s for s in subs if s.created_at and _aware(s.created_at) < since]
        if not cohort:
            return None, None
        start_mrr = sum(s.mrr_cents for s in cohort)
        if start_mrr == 0:
            return None, None
        end_mrr_with_expansion = sum(
            s.mrr_cents if s.status in ACTIVE_SUB_STATUSES else 0 for s in cohort
        )
        end_mrr_capped = sum(
            min(s.mrr_cents, s.mrr_cents) if s.status in ACTIVE_SUB_STATUSES else 0
            for s in cohort
        )
        nrr = round(end_mrr_with_expansion / start_mrr * 100, 2)
        grr = round(min(end_mrr_capped, start_mrr) / start_mrr * 100, 2)
        return nrr, grr

    def _logo_churn_rate(
        self,
        subs: list[CommercialRevenueRecord],
        *,
        period_days: int,
    ) -> float:
        since = datetime.now(timezone.utc) - timedelta(days=period_days)
        cancelled = sum(
            1
            for sub in subs
            if sub.status == "cancelada"
            and sub.cancelled_at is not None
            and _aware(sub.cancelled_at) >= since
        )
        active_now = sum(1 for sub in subs if sub.status in ACTIVE_SUB_STATUSES)
        denom = active_now + cancelled
        return round(cancelled / denom * 100, 2) if denom else 0.0

    async def _cac_estimate(self, *, period_days: int) -> Optional[float]:
        # Sem fonte de investimento de marketing (spend) no modelo — nunca inventar.
        return None

    async def _addons_kpis(self) -> list[Kpi]:
        try:
            from app.models.ai_token_package import (
                AiTokenPackageRequestStatus,
                WorkspaceAiTokenPackageRequest,
            )
        except ImportError:
            return [Kpi("receita.addons.pacotes_tokens_aprovados", "Pacotes de tokens aprovados (30d)",
                         "receita", None, "count", sem_dado=True, nota="Modelo de pacotes de tokens indisponível.")]

        since = datetime.now(timezone.utc) - timedelta(days=30)
        rows = (
            await self.db.execute(
                select(WorkspaceAiTokenPackageRequest).where(
                    WorkspaceAiTokenPackageRequest.created_at >= since
                )
            )
        ).scalars().all()
        approved = [r for r in rows if r.status == AiTokenPackageRequestStatus.approved]
        receita_addons_cents = sum(int(r.commercial_price_cents_snapshot or 0) for r in approved)
        return [
            Kpi("receita.addons.pacotes_tokens_aprovados", "Pacotes de tokens aprovados (30d)", "receita",
                float(len(approved)), "count"),
            Kpi("receita.addons.receita_pacotes_tokens", "Receita de pacotes de tokens (30d)", "receita",
                receita_addons_cents / 100, "brl"),
            Kpi("assinantes.addons.usuarios_extras", "Usuários extras vendidos (add-on)", "assinantes",
                None, "count", sem_dado=True,
                nota="Não há SKU de 'usuário extra' modelado separadamente — `EntitlementKey.users` controla limite do plano, não venda avulsa."),
        ]

    # ── Assinantes ───────────────────────────────────────────────────────

    async def _assinantes_kpis(
        self,
        subs: list[CommercialRevenueRecord],
        *,
        period_days: int,
    ) -> list[Kpi]:
        kpis: list[Kpi] = []
        total = len(subs)
        paying = [s for s in subs if s.status in PAYING_SUB_STATUSES]
        trial = [s for s in subs if s.status == "trial"]
        cancelled = [s for s in subs if s.status == "cancelada"]
        cortesia = [s for s in subs if s.courtesy]

        kpis.append(Kpi("assinantes.total", "Assinantes totais (contas)", "assinantes", float(total), "count"))
        kpis.append(Kpi("assinantes.pagantes", "Assinantes pagantes", "assinantes", float(len(paying)), "count"))
        kpis.append(Kpi("assinantes.trial_ativos", "Trials ativos", "assinantes", float(len(trial)), "count"))
        kpis.append(Kpi("assinantes.cortesias", "Contas em cortesia", "assinantes", float(len(cortesia)), "count"))
        kpis.append(Kpi("assinantes.cancelados_total", "Cancelamentos acumulados", "assinantes", float(len(cancelled)), "count"))

        for status_ in COMMERCIAL_STATUSES:
            count = sum(1 for s in subs if s.status == status_)
            kpis.append(Kpi(f"assinantes.por_status.{status_}", f"Assinantes — status {status_}",
                             "assinantes", float(count), "count"))

        for plano in COMMERCIAL_PLAN_SKUS:
            count = sum(1 for s in subs if s.sku == plano and s.status in ACTIVE_SUB_STATUSES)
            kpis.append(Kpi(f"assinantes.por_plano.{plano}", f"Assinantes ativos — plano {plano}",
                             "assinantes", float(count), "count"))

        # Conversão de trial: eventos plano_alterado/reativada saindo de trial no período.
        conversion = await self._trial_conversion_rate(period_days=period_days)
        kpis.append(Kpi("assinantes.conversao_trial_pct", "Conversão de trial → pago", "assinantes",
                         conversion, "pct", sem_dado=conversion is None,
                         nota=None if conversion is not None else "Nenhum trial concluído (convertido ou expirado) no período."))

        # Churn de receita (já coberto em receita.mrr_movement.churned) — aqui, logo churn.
        logo_churn = self._logo_churn_rate(subs, period_days=period_days)
        kpis.append(Kpi("assinantes.churn_logo_pct", "Churn de logo (contas)", "assinantes", logo_churn, "pct"))
        movement = await self._mrr_movement(period_days=period_days)
        start_mrr = sum(s.mrr_cents for s in subs) + movement["churned"] - movement["novo"] - movement["expansao"] + movement["contracao"]
        churn_receita_pct = round(movement["churned"] / start_mrr * 100, 2) if start_mrr > 0 else None
        kpis.append(Kpi("assinantes.churn_receita_pct", "Churn de receita", "assinantes", churn_receita_pct, "pct",
                         sem_dado=churn_receita_pct is None, nota=None if churn_receita_pct is not None else "Sem MRR inicial de referência no período."))

        # Novos workspaces no período (proxy de "novos assinantes")
        since = datetime.now(timezone.utc) - timedelta(days=period_days)
        novos = sum(1 for s in subs if s.created_at and _aware(s.created_at) >= since)
        kpis.append(Kpi("assinantes.novas_assinaturas_periodo", f"Novas assinaturas ({period_days}d)",
                         "assinantes", float(novos), "count"))

        # Retenção por cohort simples: % de assinaturas criadas no mês M-1..M-6 ainda ativas hoje.
        cohort_series = self._cohort_retention_series(subs, months=6)
        kpis.append(Kpi("assinantes.retencao_cohort_mensal", "Retenção por cohort mensal (assinaturas)",
                         "assinantes", None, "pct", series=cohort_series,
                         nota="% de assinaturas abertas em cada mês que seguem ativas/trial/inadimplentes hoje."))

        return kpis

    async def _trial_conversion_rate(self, *, period_days: int) -> Optional[float]:
        since = datetime.now(timezone.utc) - timedelta(days=period_days)
        canonical_workspace_ids = set(
            (
                await self.db.scalars(
                    select(WorkspaceCommercialContract.workspace_id)
                )
            ).all()
        )
        canonical_events = list(
            (
                await self.db.scalars(
                    select(CommercialCatalogEvent).where(
                        CommercialCatalogEvent.occurred_at >= since,
                        CommercialCatalogEvent.event_type == "contract_status_changed",
                    )
                )
            ).all()
        )
        canonical_concluded = [
            event
            for event in canonical_events
            if str((event.before or {}).get("status")) == "trialing"
        ]
        canonical_converted = sum(
            1
            for event in canonical_concluded
            if str((event.after or {}).get("status")) in {"active", "past_due"}
        )

        legacy_query = select(SubscriptionEvent).where(
            SubscriptionEvent.occurred_at >= since,
            SubscriptionEvent.event_type == SubscriptionEventType.plano_alterado,
        )
        if canonical_workspace_ids:
            legacy_query = legacy_query.where(
                SubscriptionEvent.workspace_id.not_in(canonical_workspace_ids)
            )
        legacy_events = list((await self.db.scalars(legacy_query)).all())
        legacy_concluded = [
            event
            for event in legacy_events
            if (event.before_json or {}).get("status") == "trial"
        ]
        legacy_converted = sum(
            1
            for event in legacy_concluded
            if (event.after_json or {}).get("status") in {"ativa", "inadimplente"}
        )
        concluded_count = len(canonical_concluded) + len(legacy_concluded)
        if not concluded_count:
            return None
        return round(
            (canonical_converted + legacy_converted) / concluded_count * 100,
            2,
        )

    def _cohort_retention_series(
        self,
        subs: list[CommercialRevenueRecord],
        *,
        months: int,
    ) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        points: list[dict[str, Any]] = []
        for i in range(months, 0, -1):
            month_start = (now.replace(day=1) - timedelta(days=30 * i)).replace(day=1)
            month_end = (month_start + timedelta(days=32)).replace(day=1)
            cohort = [
                s for s in subs
                if s.created_at and month_start <= _aware(s.created_at) < month_end
            ]
            if not cohort:
                points.append({"period": month_start.strftime("%Y-%m"), "value": None})
                continue
            still_active = sum(1 for s in cohort if s.status in ACTIVE_SUB_STATUSES)
            points.append({"period": month_start.strftime("%Y-%m"), "value": round(still_active / len(cohort) * 100, 2)})
        return points

    async def _count_active_memberships_for_workspaces(self, workspace_ids: list) -> int:
        if not workspace_ids:
            return 0
        return await self.db.scalar(
            select(func.count(WorkspaceMembership.id)).where(
                WorkspaceMembership.workspace_id.in_(workspace_ids),
                WorkspaceMembership.status == MembershipStatus.active,
            )
        ) or 0

    # ── Uso / Engajamento ────────────────────────────────────────────────

    async def _uso_engajamento_kpis(self, *, period_days: int) -> list[Kpi]:
        kpis: list[Kpi] = []
        since = datetime.now(timezone.utc) - timedelta(days=period_days)

        # DAU/WAU/MAU — sem fonte de sessão/login de usuário de tenant no modelo atual
        # (AdministrativeSession existe só para MFA de staff da plataforma).
        for key, label in (("dau", "DAU (usuários ativos/dia)"), ("wau", "WAU (usuários ativos/semana)"),
                            ("mau", "MAU (usuários ativos/mês)")):
            kpis.append(Kpi(f"uso.{key}", label, "uso_engajamento", None, "count", sem_dado=True,
                             nota="Sem dado: não há log de sessão/login por usuário de tenant persistido (AdministrativeSession cobre só MFA de staff)."))

        # Usuários simultâneos: sem sinal de sessão concorrente real; proxy honesta =
        # pico de workspaces distintos com chamada de modelo de IA na mesma janela de 5 min.
        peak_concurrent, peak_at = await self._peak_concurrent_workspaces(period_days=period_days)
        kpis.append(Kpi("uso.pico_workspaces_concorrentes_ia", "Pico de workspaces simultâneos (proxy via uso de IA)",
                         "uso_engajamento", float(peak_concurrent) if peak_concurrent is not None else None,
                         "count", sem_dado=peak_concurrent is None,
                         nota="Proxy: maior nº de workspaces distintos com chamada de IA na mesma janela de 5 min (PlatformModelUsageLedger). Não é sessão de usuário real — usar como indicador de carga, não de concorrência de login."))

        # Matters
        matters_criados = await self.db.scalar(
            select(func.count(Matter.id)).where(Matter.created_at >= since)
        ) or 0
        matters_total = await self.db.scalar(select(func.count(Matter.id))) or 0
        kpis.append(Kpi(f"uso.matters_criados_{period_days}d", f"Matters criados ({period_days}d)",
                         "uso_engajamento", float(matters_criados), "count"))
        kpis.append(Kpi("uso.matters_total", "Matters totais (todos os workspaces)", "uso_engajamento",
                         float(matters_total), "count"))

        # Membros por role e workspaces por status
        for role in WorkspaceRole:
            count = await self.db.scalar(
                select(func.count(WorkspaceMembership.id)).where(
                    WorkspaceMembership.role == role, WorkspaceMembership.status == MembershipStatus.active
                )
            ) or 0
            kpis.append(Kpi(f"uso.membros_por_role.{role.value}", f"Membros ativos — papel {role.value}",
                             "uso_engajamento", float(count), "count"))

        total_membros_ativos = await self.db.scalar(
            select(func.count(WorkspaceMembership.id)).where(WorkspaceMembership.status == MembershipStatus.active)
        ) or 0
        kpis.append(Kpi("uso.membros_ativos_total", "Membros ativos totais", "uso_engajamento",
                         float(total_membros_ativos), "count"))

        for status_ in WorkspaceStatus:
            count = await self.db.scalar(
                select(func.count(Workspace.id)).where(Workspace.status == status_)
            ) or 0
            kpis.append(Kpi(f"uso.workspaces_por_status.{status_.value}", f"Workspaces — status {status_.value}",
                             "uso_engajamento", float(count), "count"))

        # WhatsApp: mensagens enviadas/recebidas e taxa de entrega
        for direction in WaMessageDirection:
            count = await self.db.scalar(
                select(func.count(WhatsAppMessage.id)).where(
                    WhatsAppMessage.direction == direction, WhatsAppMessage.timestamp >= since
                )
            ) or 0
            label = "enviadas" if direction == WaMessageDirection.outbound else "recebidas"
            kpis.append(Kpi(f"uso.whatsapp_mensagens_{label}", f"Mensagens WhatsApp {label} ({period_days}d)",
                             "uso_engajamento", float(count), "count"))

        for msg_status in WaMessageStatus:
            count = await self.db.scalar(
                select(func.count(WhatsAppMessage.id)).where(
                    WhatsAppMessage.status == msg_status,
                    WhatsAppMessage.direction == WaMessageDirection.outbound,
                    WhatsAppMessage.timestamp >= since,
                )
            ) or 0
            kpis.append(Kpi(f"uso.whatsapp_status.{msg_status.value}", f"Mensagens outbound — status {msg_status.value}",
                             "uso_engajamento", float(count), "count"))

        sent = await self.db.scalar(
            select(func.count(WhatsAppMessage.id)).where(
                WhatsAppMessage.direction == WaMessageDirection.outbound, WhatsAppMessage.timestamp >= since
            )
        ) or 0
        delivered_or_read = await self.db.scalar(
            select(func.count(WhatsAppMessage.id)).where(
                WhatsAppMessage.direction == WaMessageDirection.outbound,
                WhatsAppMessage.timestamp >= since,
                WhatsAppMessage.status.in_([WaMessageStatus.delivered, WaMessageStatus.read]),
            )
        ) or 0
        taxa_entrega = round(delivered_or_read / sent * 100, 2) if sent else None
        kpis.append(Kpi("uso.whatsapp_taxa_entrega_pct", "Taxa de entrega WhatsApp", "uso_engajamento",
                         taxa_entrega, "pct", sem_dado=taxa_entrega is None,
                         nota=None if taxa_entrega is not None else "Sem mensagens outbound no período."))

        for conn_status in WaConnectionStatus:
            count = await self.db.scalar(
                select(func.count(WhatsAppConnection.id)).where(WhatsAppConnection.status == conn_status)
            ) or 0
            kpis.append(Kpi(f"uso.whatsapp_conexoes.{conn_status.value}", f"Conexões WhatsApp — {conn_status.value}",
                             "uso_engajamento", float(count), "count"))

        # Contatos WhatsApp por fila de atendimento e por controle humano/IA
        for queue_status in WaQueueStatus:
            count = await self.db.scalar(
                select(func.count(WhatsAppContact.id)).where(WhatsAppContact.queue_status == queue_status)
            ) or 0
            kpis.append(Kpi(f"uso.whatsapp_fila.{queue_status.value}", f"Contatos WhatsApp — fila {queue_status.value}",
                             "uso_engajamento", float(count), "count"))

        for control_state in HumanControlState:
            count = await self.db.scalar(
                select(func.count(WhatsAppContact.id)).where(WhatsAppContact.human_control_state == control_state)
            ) or 0
            kpis.append(Kpi(f"uso.whatsapp_controle.{control_state.value}", f"Contatos WhatsApp — controle {control_state.value}",
                             "uso_engajamento", float(count), "count"))

        contatos_total = await self.db.scalar(select(func.count(WhatsAppContact.id))) or 0
        contatos_bloqueados = await self.db.scalar(
            select(func.count(WhatsAppContact.id)).where(WhatsAppContact.is_blocked.is_(True))
        ) or 0
        kpis.append(Kpi("uso.whatsapp_contatos_total", "Contatos WhatsApp totais", "uso_engajamento",
                         float(contatos_total), "count"))
        kpis.append(Kpi("uso.whatsapp_contatos_bloqueados", "Contatos WhatsApp bloqueados", "uso_engajamento",
                         float(contatos_bloqueados), "count"))

        return kpis

    async def _peak_concurrent_workspaces(self, *, period_days: int) -> tuple[Optional[int], Optional[str]]:
        since = datetime.now(timezone.utc) - timedelta(days=min(period_days, 7))  # janela curta, custo de query
        rows = (
            await self.db.execute(
                select(PlatformModelUsageLedger.workspace_id, PlatformModelUsageLedger.recorded_at)
                .where(PlatformModelUsageLedger.recorded_at >= since)
            )
        ).all()
        if not rows:
            return None, None
        buckets: dict[datetime, set] = {}
        for ws_id, recorded_at in rows:
            recorded_at = _aware(recorded_at)
            bucket_key = recorded_at.replace(
                minute=(recorded_at.minute // 5) * 5, second=0, microsecond=0
            )
            buckets.setdefault(bucket_key, set()).add(ws_id)
        peak_bucket = max(buckets, key=lambda k: len(buckets[k]))
        return len(buckets[peak_bucket]), peak_bucket.isoformat()

    # ── Aquisição ────────────────────────────────────────────────────────

    async def _aquisicao_kpis(self, *, period_days: int) -> list[Kpi]:
        kpis: list[Kpi] = []
        since = datetime.now(timezone.utc) - timedelta(days=period_days)

        total = await self.db.scalar(
            select(func.count(AcquisitionEvent.id)).where(AcquisitionEvent.happened_at >= since)
        ) or 0
        kpis.append(Kpi(f"aquisicao.eventos_total_{period_days}d", f"Eventos de aquisição ({period_days}d)",
                         "aquisicao", float(total), "count"))

        rows = (
            await self.db.execute(
                select(AcquisitionEvent.channel, func.count(AcquisitionEvent.id))
                .where(AcquisitionEvent.happened_at >= since)
                .group_by(AcquisitionEvent.channel)
                .order_by(func.count(AcquisitionEvent.id).desc())
                .limit(10)
            )
        ).all()
        if not rows:
            kpis.append(Kpi("aquisicao.por_canal", "Eventos de aquisição por canal", "aquisicao", None, "count",
                             sem_dado=True, nota="Nenhum AcquisitionEvent registrado no período."))
        else:
            for channel, count in rows:
                kpis.append(Kpi(f"aquisicao.por_canal.{channel}", f"Eventos de aquisição — canal {channel}",
                                 "aquisicao", float(count), "count"))

        conv_rows = (
            await self.db.execute(
                select(AcquisitionEvent.event_name, func.count(AcquisitionEvent.id))
                .where(AcquisitionEvent.happened_at >= since, AcquisitionEvent.matter_id.is_not(None))
                .group_by(AcquisitionEvent.event_name)
                .order_by(func.count(AcquisitionEvent.id).desc())
                .limit(10)
            )
        ).all()
        for event_name, count in conv_rows:
            kpis.append(Kpi(f"aquisicao.conversoes_atribuidas.{event_name}",
                             f"Conversões atribuídas a matter — {event_name}", "aquisicao", float(count), "count"))
        if not conv_rows:
            kpis.append(Kpi("aquisicao.conversoes_atribuidas", "Conversões atribuídas a matter", "aquisicao",
                             None, "count", sem_dado=True, nota="Nenhum evento de aquisição vinculado a matter no período."))

        campaign_count = await self.db.scalar(
            select(func.count(func.distinct(AcquisitionEvent.campaign))).where(
                AcquisitionEvent.happened_at >= since, AcquisitionEvent.campaign.is_not(None)
            )
        ) or 0
        kpis.append(Kpi("aquisicao.campanhas_ativas", f"Campanhas distintas com evento ({period_days}d)",
                         "aquisicao", float(campaign_count), "count"))

        return kpis

    # ── Operação / IA ────────────────────────────────────────────────────

    async def _operacao_ia_kpis(self, *, period_days: int) -> list[Kpi]:
        kpis: list[Kpi] = []
        since = datetime.now(timezone.utc) - timedelta(days=period_days)

        totals = (
            await self.db.execute(
                select(
                    func.coalesce(func.sum(PlatformTokenUsage.total_input_tokens), 0),
                    func.coalesce(func.sum(PlatformTokenUsage.total_output_tokens), 0),
                    func.coalesce(func.sum(PlatformTokenUsage.total_runs), 0),
                    func.coalesce(func.sum(PlatformTokenUsage.total_cost_usd), 0),
                ).where(PlatformTokenUsage.usage_date >= since)
            )
        ).one()
        input_tokens, output_tokens, total_runs, cost_usd = totals
        kpis.append(Kpi(f"ia.tokens_input_{period_days}d", f"Tokens de entrada ({period_days}d)",
                         "operacao_ia", float(input_tokens), "count"))
        kpis.append(Kpi(f"ia.tokens_output_{period_days}d", f"Tokens de saída ({period_days}d)",
                         "operacao_ia", float(output_tokens), "count"))
        kpis.append(Kpi(f"ia.execucoes_{period_days}d", f"Execuções de IA / runs ({period_days}d)",
                         "operacao_ia", float(total_runs), "count"))
        kpis.append(Kpi(f"ia.custo_usd_{period_days}d", f"Custo de IA em USD ({period_days}d)",
                         "operacao_ia", round(float(cost_usd), 2), "number"))
        kpis.append(Kpi(f"ia.custo_brl_{period_days}d", f"Custo de IA em BRL ({period_days}d)",
                         "operacao_ia", round(float(cost_usd) * float(USD_BRL_REF), 2), "brl"))

        # Custo de IA por modelo
        model_rows = (
            await self.db.execute(
                select(PlatformTokenUsage.model_used, func.coalesce(func.sum(PlatformTokenUsage.total_cost_usd), 0))
                .where(PlatformTokenUsage.usage_date >= since)
                .group_by(PlatformTokenUsage.model_used)
                .order_by(func.coalesce(func.sum(PlatformTokenUsage.total_cost_usd), 0).desc())
                .limit(10)
            )
        ).all()
        if not model_rows:
            kpis.append(Kpi("ia.custo_por_modelo", "Custo de IA por modelo", "operacao_ia", None, "brl",
                             sem_dado=True, nota="Sem uso de IA registrado no período."))
        for model, cost in model_rows:
            kpis.append(Kpi(f"ia.custo_por_modelo.{model}", f"Custo de IA — modelo {model}",
                             "operacao_ia", round(float(cost) * float(USD_BRL_REF), 2), "brl"))

        # Custo de IA por workspace (top 10 — consumo mais alto no período)
        ws_rows = (
            await self.db.execute(
                select(
                    PlatformTokenUsage.workspace_id,
                    Workspace.name,
                    func.coalesce(func.sum(PlatformTokenUsage.total_cost_usd), 0),
                )
                .join(Workspace, Workspace.id == PlatformTokenUsage.workspace_id)
                .where(PlatformTokenUsage.usage_date >= since)
                .group_by(PlatformTokenUsage.workspace_id, Workspace.name)
                .order_by(func.coalesce(func.sum(PlatformTokenUsage.total_cost_usd), 0).desc())
                .limit(10)
            )
        ).all()
        if not ws_rows:
            kpis.append(Kpi("ia.custo_por_workspace_top10", "Custo de IA por workspace (top 10)", "operacao_ia",
                             None, "brl", sem_dado=True, nota="Sem uso de IA registrado no período."))
        for ws_id, ws_name, cost in ws_rows:
            kpis.append(Kpi(f"ia.custo_por_workspace.{ws_id}", f"Custo de IA — {ws_name}",
                             "operacao_ia", round(float(cost) * float(USD_BRL_REF), 2), "brl"))

        # Consumo por classe de tarefa (PlatformTokenUsageByTask) — granularidade
        # usada para otimizar roteamento de modelo e franquias de plano.
        from app.models.billing import PlatformTokenUsageByTask
        task_rows = (
            await self.db.execute(
                select(
                    PlatformTokenUsageByTask.task_class,
                    func.coalesce(func.sum(PlatformTokenUsageByTask.total_cost_usd), 0),
                    func.coalesce(func.sum(PlatformTokenUsageByTask.total_runs), 0),
                )
                .where(PlatformTokenUsageByTask.usage_date >= since)
                .group_by(PlatformTokenUsageByTask.task_class)
                .order_by(func.coalesce(func.sum(PlatformTokenUsageByTask.total_cost_usd), 0).desc())
                .limit(12)
            )
        ).all()
        if not task_rows:
            kpis.append(Kpi("ia.consumo_por_classe_tarefa", "Consumo de IA por classe de tarefa", "operacao_ia",
                             None, "count", sem_dado=True, nota="Sem uso de IA classificado por tarefa no período."))
        for task_class, cost, runs in task_rows:
            kpis.append(Kpi(f"ia.execucoes_por_tarefa.{task_class}", f"Execuções de IA — tarefa {task_class}",
                             "operacao_ia", float(runs), "count"))
            kpis.append(Kpi(f"ia.custo_por_tarefa.{task_class}", f"Custo de IA — tarefa {task_class}",
                             "operacao_ia", round(float(cost) * float(USD_BRL_REF), 2), "brl"))

        # Ledger: estados de chamadas de provider (saúde operacional do gateway de IA)
        from app.models.billing import ModelProviderCallJournal, ModelProviderCallState
        for state in ModelProviderCallState:
            count = await self.db.scalar(
                select(func.count(ModelProviderCallJournal.id)).where(
                    ModelProviderCallJournal.state == state,
                    ModelProviderCallJournal.created_at >= since,
                )
            ) or 0
            kpis.append(Kpi(f"ia.ledger_estado.{state.value}", f"Chamadas de IA — estado {state.value} ({period_days}d)",
                             "operacao_ia", float(count), "count"))

        return kpis

    # ── Saúde ────────────────────────────────────────────────────────────

    async def _saude_kpis(
        self,
        subs: list[CommercialRevenueRecord],
        *,
        period_days: int,
    ) -> list[Kpi]:
        kpis: list[Kpi] = []
        since = datetime.now(timezone.utc) - timedelta(days=period_days)

        inadimplentes = sum(1 for s in subs if s.status == "inadimplente")
        kpis.append(Kpi("saude.contas_inadimplentes", "Contas inadimplentes (em risco)", "saude",
                         float(inadimplentes), "count"))

        rate_limited = await self.db.scalar(
            select(func.count(AuthSecurityEvent.id)).where(
                AuthSecurityEvent.occurred_at >= since, AuthSecurityEvent.outcome != "success",
            )
        ) or 0
        kpis.append(Kpi(f"saude.eventos_seguranca_nao_sucesso_{period_days}d",
                         f"Eventos de segurança não-sucesso ({period_days}d)", "saude", float(rate_limited), "count"))

        total_staff = await self.db.scalar(
            select(func.count(User.id)).where(User.is_platform_admin.is_(True), User.is_active.is_(True))
        ) or 0
        mfa_enabled = await self.db.scalar(
            select(func.count(MfaCredential.id)).where(MfaCredential.is_enabled.is_(True))
        ) or 0
        mfa_adoption = round(mfa_enabled / total_staff * 100, 2) if total_staff else None
        kpis.append(Kpi("saude.adocao_mfa_staff_pct", "Adoção de MFA — equipe da plataforma", "saude",
                         mfa_adoption, "pct", sem_dado=mfa_adoption is None,
                         nota=None if mfa_adoption is not None else "Sem staff da plataforma ativo cadastrado."))

        # Reaproveita NRR/GRR/quick_ratio como indicadores de saúde consolidados
        nrr, grr = await self._nrr_grr(subs, period_days=period_days)
        kpis.append(Kpi("saude.nrr", "NRR (indicador de saúde de receita)", "saude", nrr, "pct",
                         sem_dado=nrr is None, nota=None if nrr is not None else "Sem cohort de referência no período."))
        kpis.append(Kpi("saude.grr", "GRR (indicador de saúde de receita)", "saude", grr, "pct",
                         sem_dado=grr is None, nota=None if grr is not None else "Sem cohort de referência no período."))

        logo_churn = self._logo_churn_rate(subs, period_days=period_days)
        kpis.append(Kpi("saude.churn_logo_pct", "Churn de logo (saúde)", "saude", logo_churn, "pct"))

        return kpis
