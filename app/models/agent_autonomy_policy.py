"""
Modelo: AgentAutonomyPolicy (Plano Mestre §14)

Política de autonomia POR EVENTO — distinta do modo HIL simples já
existente (hil_total/hil_marcos/autonomo, ver `agent_governance_service` e
`KlmAgentService.set_agent_autonomy`, guardado como `config_documents`
namespace `agent.autonomy_settings`). O modo HIL é o default do agente; a
`AgentAutonomyPolicy` é a regra fina que decide, para UM evento concreto
(com área/cliente/carteira/ação), qual nível A0-A4 vale e o que é
permitido — inclusive níveis (A0/A1) que nenhum modo HIL isoladamente
expressa.

Nunca é o gate soberano de ação externa: `AgentAutonomyPolicyService.evaluate`
sempre consulta `app.core.klm_runtime.external_delivery_allowed()` para
qualquer ação marcada como externa, e o HARD_HUMAN_GATES de
`agent_governance_service` continua valendo por cima disto — esta política
só governa a banda de decisão *interna* (preparar vs. executar).

Campos mapeiam 1:1 os itens do Plano Mestre §14: evento (`event_type`),
condições (`conditions`), área (`area_key`), cliente/carteira (`client_id`/
`portfolio_key`), agente (`agent_key`), ação (`action`), nível A
(`autonomy_level`), capability (`capability_key`), risco (`risk_level`),
confiança mínima (`min_confidence`), orçamento (`budget_limit`/
`budget_currency`), prazo (`sla_minutes`), idempotency key
(`idempotency_key_template`), rollback (`rollback_strategy`), aprovação
(`approval_required`), escalonamento (`escalation_target_agent_key`).
"""
from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.client import Client
    from app.models.user import User
    from app.models.workspace import Workspace


class AgentAutonomyPolicyLevel(str, enum.Enum):
    """Espelha `app.services.agent_governance_service.AutonomyLevel` — um
    enum próprio (em vez de importar o de governance) porque este é um
    valor de coluna persistido: a governança pode evoluir sua leitura
    interna sem forçar uma migração de banco."""

    A0 = "A0"
    A1 = "A1"
    A2 = "A2"
    A3 = "A3"
    A4 = "A4"


class AgentAutonomyPolicyRisk(str, enum.Enum):
    baixo = "baixo"
    medio = "medio"
    alto = "alto"


class AgentAutonomyPolicy(UUIDMixin, TimestampMixin, Base):
    """Regra de autonomia por evento (Plano Mestre §14) — workspace-scoped,
    RLS direta por `workspace_id` (mesmo padrão de `contract_billing_proposals`
    e `klm_knowledge_article_feedback`)."""

    __tablename__ = "agent_autonomy_policies"
    __table_args__ = (
        CheckConstraint(
            "min_confidence IS NULL OR (min_confidence >= 0 AND min_confidence <= 1)",
            name="ck_agent_autonomy_policy_min_confidence_range",
        ),
        CheckConstraint(
            "budget_limit IS NULL OR budget_limit >= 0",
            name="ck_agent_autonomy_policy_budget_nonnegative",
        ),
        CheckConstraint(
            "sla_minutes IS NULL OR sla_minutes > 0",
            name="ck_agent_autonomy_policy_sla_positive",
        ),
        Index(
            "ix_agent_autonomy_policies_workspace_event",
            "workspace_id",
            "event_type",
            "is_active",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── evento + condições ──
    event_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    conditions: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)

    # ── escopo: área / cliente-carteira / agente / ação ──
    area_key: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    client_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("clients.id", ondelete="SET NULL"), nullable=True, index=True
    )
    portfolio_key: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    agent_key: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False, index=True)

    # ── nível A0-A4 + capability + risco ──
    autonomy_level: Mapped[AgentAutonomyPolicyLevel] = mapped_column(
        Enum(AgentAutonomyPolicyLevel, name="agent_autonomy_policy_level_enum"),
        nullable=False,
        index=True,
    )
    capability_key: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    risk_level: Mapped[AgentAutonomyPolicyRisk] = mapped_column(
        Enum(AgentAutonomyPolicyRisk, name="agent_autonomy_policy_risk_enum"),
        nullable=False,
        default=AgentAutonomyPolicyRisk.medio,
    )

    # ── confiança mínima / orçamento / prazo ──
    min_confidence: Mapped[Optional[float]] = mapped_column(Numeric(4, 3), nullable=True)
    budget_limit: Mapped[Optional[float]] = mapped_column(Numeric(14, 2), nullable=True)
    budget_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    sla_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ── idempotency / rollback / aprovação / escalonamento ──
    idempotency_key_template: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    rollback_strategy: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Fail-closed: mesmo uma política em A3 pode manter approval_required=True
    # se o humano que a configurou quiser um freio extra por evento.
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    escalation_target_agent_key: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)

    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    created_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    workspace: Mapped["Workspace"] = relationship("Workspace")
    client: Mapped[Optional["Client"]] = relationship("Client")
    created_by: Mapped[Optional["User"]] = relationship("User")

    def __repr__(self) -> str:
        return (
            f"<AgentAutonomyPolicy id={self.id} event={self.event_type} "
            f"action={self.action} level={self.autonomy_level}>"
        )
