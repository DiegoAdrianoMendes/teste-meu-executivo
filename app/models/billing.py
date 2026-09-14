"""
Modelos: Billing — Planos, Assinaturas e Uso de Tokens.

Estrutura inspirada nas melhores práticas Stripe 2026:
  - Plan: catálogo de planos (Starter, Pro, Business, Enterprise)
  - Subscription: assinatura ativa por workspace (sincronizada com Stripe)
  - TokenBudget: orçamento mensal de tokens por workspace (alertas e rate limiting)
  - PlatformTokenUsage: log diário de uso de tokens por workspace e modelo
  - PlatformTokenUsageByTask: distribuição diária por classe de tarefa
  - PlatformModelUsageLedger: recibo imutável e idempotente por chamada de modelo

Design decisions:
  - stripe_customer_id e stripe_subscription_id são a fonte de verdade para billing
  - status local sincronizado via webhook Stripe
  - TokenBudget permite alertas ANTES de ultrapassar o limite
  - PlatformTokenUsage permite análise de custo por workspace, modelo e dia
  - PlatformTokenUsageByTask separa o consumo por complexidade sem expor prompts
  - PlatformModelUsageLedger impede dupla contagem e preserva a prova de consumo
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.append_only import register_append_only_model
from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.workspace import Workspace


# ── Enums ──────────────────────────────────────────────────────────────────

class PlanTier(str, enum.Enum):
    free        = "free"        # Trial 14 dias, 1 usuário, 50 matters, 100K tokens/mês
    starter     = "starter"     # R$ 297/mês, 3 usuários, 200 matters, 500K tokens/mês
    pro         = "pro"         # R$ 697/mês, 10 usuários, unlimited matters, 2M tokens/mês
    business    = "business"    # R$ 1.497/mês, 25 usuários, unlimited, 10M tokens/mês
    enterprise  = "enterprise"  # Negociado, unlimited tudo, SLA, suporte dedicado


class BillingInterval(str, enum.Enum):
    monthly  = "monthly"
    annually = "annually"   # 20% desconto


class SubscriptionStatus(str, enum.Enum):
    trialing    = "trialing"    # Período de trial
    active      = "active"      # Assinatura ativa e paga
    past_due    = "past_due"    # Pagamento atrasado (acesso mantido por 7 dias)
    cancelled   = "cancelled"   # Cancelada (acesso até fim do período)
    unpaid      = "unpaid"      # Bloqueada por falta de pagamento
    paused      = "paused"      # Pausada pelo cliente


class TokenAlertLevel(str, enum.Enum):
    none     = "none"     # Sem alertas
    warning  = "warning"  # 80% do limite
    critical = "critical" # 95% do limite
    exceeded = "exceeded" # 100%+ — rate limiting ativo


class ModelProviderCallState(str, enum.Enum):
    """Durable ownership and terminal state for one provider execution."""

    claimed = "claimed"
    provider_started = "provider_started"
    succeeded = "succeeded"
    uncertain = "uncertain"
    failed_pre_provider = "failed_pre_provider"


def _model_provider_call_state_enum() -> Enum:
    return Enum(
        ModelProviderCallState,
        name="model_provider_call_state_enum",
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


# ── Modelos ────────────────────────────────────────────────────────────────

class Plan(UUIDMixin, TimestampMixin, Base):
    """
    Catálogo de planos disponíveis na plataforma.
    Gerenciado pelo super-admin — não editável por clientes.
    """
    __tablename__ = "plans"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    tier: Mapped[PlanTier] = mapped_column(
        Enum(PlanTier, name="plan_tier_enum"), nullable=False, unique=True
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Preços em centavos BRL (evita float)
    price_monthly_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price_annually_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Limites do plano
    max_users: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)        # None = ilimitado
    max_matters: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)      # None = ilimitado
    # None exige configuração tipada antes de qualquer API de modelo externa.
    max_tokens_per_month: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_ai_agents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Features habilitadas no plano (flags)
    features: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)

    # IDs Stripe (sincronizados com o Stripe Dashboard)
    stripe_product_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    stripe_price_monthly_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    stripe_price_annually_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    subscriptions: Mapped[list["Subscription"]] = relationship("Subscription", back_populates="plan")


class Subscription(UUIDMixin, TimestampMixin, Base):
    """
    Assinatura ativa de um workspace. Um workspace tem no máximo uma assinatura ativa.

    Stripe é a fonte de verdade — este modelo é sincronizado via webhook.
    NUNCA atualizar status aqui sem receber evento do Stripe.
    """
    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_subscription_workspace"),
        Index(
            "ux_subscriptions_stripe_subscription_id_not_null",
            "stripe_subscription_id",
            unique=True,
            postgresql_where=text("stripe_subscription_id IS NOT NULL"),
            sqlite_where=text("stripe_subscription_id IS NOT NULL"),
        ),
        Index(
            "ux_subscriptions_stripe_customer_id_not_null",
            "stripe_customer_id",
            unique=True,
            postgresql_where=text("stripe_customer_id IS NOT NULL"),
            sqlite_where=text("stripe_customer_id IS NOT NULL"),
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False
    )

    # IDs Stripe
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    stripe_payment_method_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscription_status_enum"),
        nullable=False,
        default=SubscriptionStatus.trialing,
        index=True,
    )
    interval: Mapped[BillingInterval] = mapped_column(
        Enum(BillingInterval, name="billing_interval_enum"),
        nullable=False,
        default=BillingInterval.monthly,
    )

    # Período atual
    current_period_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    current_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    trial_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )

    # Valor atual (pode diferir do plano por cupom/desconto)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="BRL")

    # Metadata do último evento Stripe recebido
    last_stripe_event: Mapped[Optional[dict]] = mapped_column(FlexibleJSON(), nullable=True)
    last_stripe_event_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_stripe_event_created: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="subscription")
    plan: Mapped["Plan"] = relationship("Plan", back_populates="subscriptions")

    @property
    def is_active(self) -> bool:
        return self.status in (SubscriptionStatus.active, SubscriptionStatus.trialing)

    @property
    def mrr_cents(self) -> int:
        """MRR em centavos para este workspace."""
        if not self.is_active:
            return 0
        if self.interval == BillingInterval.annually:
            return self.amount_cents // 12
        return self.amount_cents


class StripeWebhookEventReceipt(UUIDMixin, Base):
    """Receipt global que torna o processamento de cada evento Stripe único."""

    __tablename__ = "stripe_webhook_event_receipts"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_stripe_webhook_receipts_event_id"),
        Index("ix_stripe_webhook_receipts_state_received", "state", "received_at"),
    )

    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    event_created: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="processing")
    subscription_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    subscription: Mapped[Optional["Subscription"]] = relationship("Subscription")


class TokenBudget(UUIDMixin, TimestampMixin, Base):
    """
    Orçamento mensal de tokens por workspace.
    Controla alertas e rate limiting de IA.
    """
    __tablename__ = "token_budgets"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_token_budget_workspace"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )

    # None remove apenas o override local; nunca significa franquia externa ilimitada.
    monthly_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    alert_at_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=80)  # % para alertar
    hard_limit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # Bloquear ou só alertar

    # Acumulador do mês corrente (resetado todo mês via Celery beat)
    current_month_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_month_cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, default=Decimal("0"))
    current_month_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    alert_level: Mapped[TokenAlertLevel] = mapped_column(
        Enum(TokenAlertLevel, name="token_alert_level_enum"),
        nullable=False,
        default=TokenAlertLevel.none,
    )
    last_alert_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="token_budget")


class PlatformTokenUsage(UUIDMixin, Base):
    """
    Log diário de uso de tokens por workspace, modelo e provider.
    Alimentado pelo AgentRun ao completar. Usado pelo super-admin para análise de custo.
    Granularidade: 1 registro por (workspace_id, model, provider, date).
    """
    __tablename__ = "platform_token_usage"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "model_used", "provider", "usage_date",
            name="uq_token_usage_workspace_model_date"
        ),
        Index("ix_token_usage_date", "usage_date"),
        Index("ix_token_usage_workspace", "workspace_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    model_used: Mapped[str] = mapped_column(String(120), nullable=False)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    usage_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    total_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=Decimal("0"))

    workspace: Mapped["Workspace"] = relationship("Workspace")


class PlatformTokenUsageByTask(UUIDMixin, Base):
    """Agregado diário por classe de tarefa para otimização de rotas e franquias."""

    __tablename__ = "platform_token_usage_by_task"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "task_class",
            "model_used",
            "provider",
            "usage_date",
            name="uq_token_usage_task_workspace_model_date",
        ),
        Index("ix_token_usage_task_date", "usage_date"),
        Index("ix_token_usage_task_workspace", "workspace_id"),
        Index("ix_token_usage_task_class", "task_class"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    task_class: Mapped[str] = mapped_column(String(30), nullable=False)
    model_used: Mapped[str] = mapped_column(String(120), nullable=False)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    usage_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    total_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 6), nullable=False, default=Decimal("0")
    )

    workspace: Mapped["Workspace"] = relationship("Workspace")


class PlatformModelUsageLedger(UUIDMixin, Base):
    """Append-only receipt for one measured gateway call, without prompt content."""

    __tablename__ = "platform_model_usage_ledger"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "request_id",
            name="uq_model_usage_ledger_workspace_request",
        ),
        Index("ix_model_usage_ledger_recorded", "recorded_at"),
        Index("ix_model_usage_ledger_workspace", "workspace_id"),
        Index("ix_model_usage_ledger_task_class", "task_class"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    request_id: Mapped[str] = mapped_column(String(180), nullable=False)
    task_class: Mapped[str] = mapped_column(String(30), nullable=False)
    operation: Mapped[str] = mapped_column(String(180), nullable=False, default="unknown")
    model_used: Mapped[str] = mapped_column(String(120), nullable=False)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    provider_request_id: Mapped[str] = mapped_column(String(180), nullable=False, default="")
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 8), nullable=False, default=Decimal("0")
    )
    price_catalog_version: Mapped[str] = mapped_column(
        String(120), nullable=False, default=""
    )
    route_catalog_version: Mapped[str] = mapped_column(
        String(120), nullable=False, default=""
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped["Workspace"] = relationship("Workspace")


class ModelProviderCallJournal(UUIDMixin, TimestampMixin, Base):
    """Mutable execution journal that owns a request before provider network I/O."""

    __tablename__ = "model_provider_call_journal"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "request_id",
            name="uq_model_provider_call_workspace_request",
        ),
        CheckConstraint(
            "estimated_input_tokens >= 0 AND estimated_output_tokens >= 0 "
            "AND reserved_tokens >= 0 AND actual_input_tokens >= 0 "
            "AND actual_output_tokens >= 0 AND cached_input_tokens >= 0 "
            "AND attempt_count >= 0",
            name="ck_model_provider_call_nonnegative",
        ),
        Index("ix_model_provider_call_workspace", "workspace_id"),
        Index("ix_model_provider_call_state", "state"),
        Index("ix_model_provider_call_period", "period_start"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    request_id: Mapped[str] = mapped_column(String(180), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[ModelProviderCallState] = mapped_column(
        _model_provider_call_state_enum(),
        nullable=False,
        default=ModelProviderCallState.claimed,
    )
    task_class: Mapped[str] = mapped_column(String(30), nullable=False)
    operation: Mapped[str] = mapped_column(String(180), nullable=False, default="unknown")
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    model_used: Mapped[str] = mapped_column(String(120), nullable=False)
    route_key: Mapped[str] = mapped_column(String(190), nullable=False)
    estimated_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 8), nullable=False, default=Decimal("0")
    )
    reserved_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    entitlement_reservation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("entitlement_usage_reservations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    provider_request_id: Mapped[str] = mapped_column(String(180), nullable=False, default="")
    actual_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actual_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actual_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 8), nullable=True)
    price_catalog_version: Mapped[str] = mapped_column(
        String(120), nullable=False, default=""
    )
    route_catalog_version: Mapped[str] = mapped_column(
        String(120), nullable=False, default=""
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    provider_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    workspace: Mapped["Workspace"] = relationship("Workspace")


register_append_only_model(PlatformModelUsageLedger)
