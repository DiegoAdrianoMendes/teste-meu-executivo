"""Plano Mestre §14 — políticas de autonomia por evento (AutonomyPolicy).

Distinto do modo HIL simples do agente (hil_total/hil_marcos/autonomo,
guardado em `config_documents` namespace `agent.autonomy_settings` via
`KlmAgentService.set_agent_autonomy`), este serviço resolve, "dado este
evento + contexto, qual nível A0-A4 vale e o que é permitido" — cruzando a
política mais específica cadastrada com o gate real de ação externa
(`app.core.klm_runtime.external_delivery_allowed`) e com os
HARD_HUMAN_GATES/AutonomyLevel de `agent_governance_service`. Não
substitui nenhum dos dois — soma-se a eles.

Fail-closed em toda borda: sem política casando o evento, o nível
resolvido é sempre A4 (decisão humana obrigatória); ação marcada como
externa nunca executa sozinha nesta camada, mesmo em A3.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.klm_runtime import external_delivery_allowed
from app.models.agent_autonomy_policy import (
    AgentAutonomyPolicy,
    AgentAutonomyPolicyLevel,
    AgentAutonomyPolicyRisk,
)
from app.models.klm_operations import KlmAuditEvent
from app.services.agent_governance_service import (
    HARD_HUMAN_GATES,
    HUMAN_QUEUE_AGENT_KEY,
    AutonomyLevel,
)
from app.services.audit_actor_service import resolve_human_actor_label

_MUTABLE_FIELDS = (
    "name",
    "description",
    "event_type",
    "conditions",
    "area_key",
    "client_id",
    "portfolio_key",
    "agent_key",
    "action",
    "autonomy_level",
    "capability_key",
    "risk_level",
    "min_confidence",
    "budget_limit",
    "budget_currency",
    "sla_minutes",
    "idempotency_key_template",
    "rollback_strategy",
    "approval_required",
    "escalation_target_agent_key",
    "priority",
    "is_active",
)


def _snapshot(policy: AgentAutonomyPolicy) -> dict[str, Any]:
    return {
        "id": str(policy.id),
        "name": policy.name,
        "eventType": policy.event_type,
        "areaKey": policy.area_key,
        "clientId": str(policy.client_id) if policy.client_id else None,
        "portfolioKey": policy.portfolio_key,
        "agentKey": policy.agent_key,
        "action": policy.action,
        "autonomyLevel": (
            policy.autonomy_level.value
            if isinstance(policy.autonomy_level, AgentAutonomyPolicyLevel)
            else policy.autonomy_level
        ),
        "riskLevel": (
            policy.risk_level.value
            if isinstance(policy.risk_level, AgentAutonomyPolicyRisk)
            else policy.risk_level
        ),
        "approvalRequired": policy.approval_required,
        "priority": policy.priority,
        "isActive": policy.is_active,
    }


@dataclass(frozen=True)
class AutonomyPolicyDecision:
    """Resultado de `AutonomyPolicyService.evaluate` — o que é permitido
    para este evento+contexto agora, nunca uma promessa futura."""

    workspace_id: str
    event_type: str
    matched_policy_id: str | None
    autonomy_level: str
    is_external_action: bool
    external_action_gate_open: bool
    approval_required: bool
    can_prepare: bool
    can_execute_internal: bool
    capability_key: str | None
    risk_level: str
    min_confidence: float | None
    budget_limit: float | None
    budget_currency: str | None
    sla_minutes: int | None
    idempotency_key: str
    rollback_strategy: str | None
    escalation_target_agent_key: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "workspaceId": self.workspace_id,
            "eventType": self.event_type,
            "matchedPolicyId": self.matched_policy_id,
            "autonomyLevel": self.autonomy_level,
            "isExternalAction": self.is_external_action,
            "externalActionGateOpen": self.external_action_gate_open,
            "approvalRequired": self.approval_required,
            "canPrepare": self.can_prepare,
            "canExecuteInternal": self.can_execute_internal,
            "capabilityKey": self.capability_key,
            "riskLevel": self.risk_level,
            "minConfidence": self.min_confidence,
            "budgetLimit": self.budget_limit,
            "budgetCurrency": self.budget_currency,
            "slaMinutes": self.sla_minutes,
            "idempotencyKey": self.idempotency_key,
            "rollbackStrategy": self.rollback_strategy,
            "escalationTargetAgentKey": self.escalation_target_agent_key,
        }


class AutonomyPolicyService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── CRUD admin ──

    async def list_policies(self, workspace_id: UUID) -> list[AgentAutonomyPolicy]:
        rows = await self.db.scalars(
            select(AgentAutonomyPolicy)
            .where(AgentAutonomyPolicy.workspace_id == workspace_id)
            .order_by(AgentAutonomyPolicy.priority, AgentAutonomyPolicy.created_at)
        )
        return list(rows.all())

    async def get_policy(self, workspace_id: UUID, policy_id: UUID) -> AgentAutonomyPolicy:
        policy = await self.db.scalar(
            select(AgentAutonomyPolicy).where(
                AgentAutonomyPolicy.id == policy_id,
                AgentAutonomyPolicy.workspace_id == workspace_id,
            )
        )
        if policy is None:
            raise LookupError("Política de autonomia não encontrada.")
        return policy

    async def create_policy(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        payload: dict[str, Any],
    ) -> AgentAutonomyPolicy:
        policy = AgentAutonomyPolicy(
            workspace_id=workspace_id,
            created_by_user_id=user_id,
            **{key: value for key, value in payload.items() if key in _MUTABLE_FIELDS},
        )
        self.db.add(policy)
        await self.db.flush()
        actor_label = await resolve_human_actor_label(self.db, user_id)
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=user_id,
                actor_type="human",
                actor_label=actor_label,
                event_type="agent_autonomy_policy_created",
                entity_type="agent_autonomy_policy",
                entity_id=policy.id,
                correlation_id=f"agent-autonomy-policy:{policy.id}",
                summary=f"Política de autonomia '{policy.name}' criada para o evento {policy.event_type}.",
                before_data=None,
                after_data=_snapshot(policy),
                occurred_at=datetime.now(timezone.utc),
            )
        )
        await self.db.flush()
        return policy

    async def update_policy(
        self,
        *,
        workspace_id: UUID,
        policy_id: UUID,
        user_id: UUID,
        payload: dict[str, Any],
    ) -> AgentAutonomyPolicy:
        policy = await self.get_policy(workspace_id, policy_id)
        before = _snapshot(policy)
        for key, value in payload.items():
            if key in _MUTABLE_FIELDS and value is not None:
                setattr(policy, key, value)
        await self.db.flush()
        actor_label = await resolve_human_actor_label(self.db, user_id)
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=user_id,
                actor_type="human",
                actor_label=actor_label,
                event_type="agent_autonomy_policy_updated",
                entity_type="agent_autonomy_policy",
                entity_id=policy.id,
                correlation_id=f"agent-autonomy-policy:{policy.id}:{datetime.now(timezone.utc).isoformat()}",
                summary=f"Política de autonomia '{policy.name}' atualizada.",
                before_data=before,
                after_data=_snapshot(policy),
                occurred_at=datetime.now(timezone.utc),
            )
        )
        await self.db.flush()
        # `updated_at` usa onupdate=func.now() no servidor — sem refresh o
        # atributo em memória fica stale e a serialização do Read falha
        # (MissingGreenlet ao tentar um lazy load fora do await).
        await self.db.refresh(policy)
        return policy

    async def delete_policy(
        self,
        *,
        workspace_id: UUID,
        policy_id: UUID,
        user_id: UUID,
    ) -> None:
        policy = await self.get_policy(workspace_id, policy_id)
        before = _snapshot(policy)
        actor_label = await resolve_human_actor_label(self.db, user_id)
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=user_id,
                actor_type="human",
                actor_label=actor_label,
                event_type="agent_autonomy_policy_deleted",
                entity_type="agent_autonomy_policy",
                entity_id=policy.id,
                correlation_id=f"agent-autonomy-policy:{policy.id}:deleted",
                summary=f"Política de autonomia '{policy.name}' removida.",
                before_data=before,
                after_data=None,
                occurred_at=datetime.now(timezone.utc),
            )
        )
        await self.db.delete(policy)
        await self.db.flush()

    # ── avaliação (evento + contexto -> nível A + o que é permitido) ──

    async def evaluate(
        self,
        *,
        workspace_id: UUID,
        event_type: str,
        agent_key: Optional[str] = None,
        area_key: Optional[str] = None,
        client_id: Optional[UUID] = None,
        portfolio_key: Optional[str] = None,
        action: Optional[str] = None,
        is_external_action: bool = False,
    ) -> AutonomyPolicyDecision:
        candidates = await self.db.scalars(
            select(AgentAutonomyPolicy).where(
                AgentAutonomyPolicy.workspace_id == workspace_id,
                AgentAutonomyPolicy.event_type == event_type,
                AgentAutonomyPolicy.is_active.is_(True),
            )
        )
        matched: AgentAutonomyPolicy | None = None
        best_specificity = -1
        best_priority = None
        for candidate in candidates.all():
            if not self._scope_matches(
                candidate,
                agent_key=agent_key,
                area_key=area_key,
                client_id=client_id,
                portfolio_key=portfolio_key,
                action=action,
            ):
                continue
            specificity = self._specificity(candidate)
            if (
                matched is None
                or specificity > best_specificity
                or (specificity == best_specificity and candidate.priority < best_priority)
            ):
                matched = candidate
                best_specificity = specificity
                best_priority = candidate.priority

        # A ação é externa se o chamador já sabe (is_external_action) ou se
        # ela está no catálogo canônico de HARD_HUMAN_GATES de
        # agent_governance_service — reaproveitado aqui em vez de duplicado.
        resolved_action = action or ""
        is_external = bool(is_external_action or resolved_action in HARD_HUMAN_GATES)
        external_gate_open = external_delivery_allowed()

        if matched is None:
            # Fail-closed: nenhuma política casou o evento -> A4, aprovação
            # humana obrigatória, nada de preparo/execução autônoma.
            return AutonomyPolicyDecision(
                workspace_id=str(workspace_id),
                event_type=event_type,
                matched_policy_id=None,
                autonomy_level=AutonomyLevel.A4_HUMAN_REQUIRED.value,
                is_external_action=is_external,
                external_action_gate_open=external_gate_open,
                approval_required=True,
                can_prepare=False,
                can_execute_internal=False,
                capability_key=None,
                risk_level=AgentAutonomyPolicyRisk.alto.value,
                min_confidence=None,
                budget_limit=None,
                budget_currency=None,
                sla_minutes=None,
                idempotency_key=self._default_idempotency_key(
                    workspace_id, event_type, agent_key, action
                ),
                rollback_strategy=None,
                escalation_target_agent_key=HUMAN_QUEUE_AGENT_KEY,
            )

        matched_level_value = (
            matched.autonomy_level.value
            if isinstance(matched.autonomy_level, AgentAutonomyPolicyLevel)
            else str(matched.autonomy_level)
        )
        level = AutonomyLevel(matched_level_value)
        # A0/A1/A4 nunca dispensam aprovação humana, mesmo que a política
        # tenha sido salva com approval_required=False por engano — o
        # enforcement do nível vence a configuração da política.
        approval_required = (
            True
            if level
            in (
                AutonomyLevel.A0_OBSERVE,
                AutonomyLevel.A1_RECOMMEND,
                AutonomyLevel.A4_HUMAN_REQUIRED,
            )
            else bool(matched.approval_required)
        )
        can_prepare = level in (
            AutonomyLevel.A2_PREPARE,
            AutonomyLevel.A3_EXECUTE_INTERNAL_REVERSIBLE,
        )
        # A3 só executa sozinho ação interna e reversível — ação externa
        # continua gated aqui, e mesmo interna respeita approval_required.
        can_execute_internal = (
            level == AutonomyLevel.A3_EXECUTE_INTERNAL_REVERSIBLE
            and not is_external
            and not approval_required
        )

        idempotency_key = self._render_idempotency_key(
            matched,
            workspace_id=workspace_id,
            event_type=event_type,
            agent_key=agent_key,
            area_key=area_key,
            client_id=client_id,
            portfolio_key=portfolio_key,
            action=action,
        )

        return AutonomyPolicyDecision(
            workspace_id=str(workspace_id),
            event_type=event_type,
            matched_policy_id=str(matched.id),
            autonomy_level=level.value,
            is_external_action=is_external,
            external_action_gate_open=external_gate_open,
            approval_required=approval_required,
            can_prepare=can_prepare,
            can_execute_internal=can_execute_internal,
            capability_key=matched.capability_key,
            risk_level=(
                matched.risk_level.value
                if isinstance(matched.risk_level, AgentAutonomyPolicyRisk)
                else str(matched.risk_level)
            ),
            min_confidence=(
                float(matched.min_confidence) if matched.min_confidence is not None else None
            ),
            budget_limit=(
                float(matched.budget_limit) if matched.budget_limit is not None else None
            ),
            budget_currency=matched.budget_currency,
            sla_minutes=matched.sla_minutes,
            idempotency_key=idempotency_key,
            rollback_strategy=matched.rollback_strategy,
            escalation_target_agent_key=(
                matched.escalation_target_agent_key or HUMAN_QUEUE_AGENT_KEY
            ),
        )

    @staticmethod
    def _scope_matches(
        policy: AgentAutonomyPolicy,
        *,
        agent_key: Optional[str],
        area_key: Optional[str],
        client_id: Optional[UUID],
        portfolio_key: Optional[str],
        action: Optional[str],
    ) -> bool:
        if action is not None and policy.action != action:
            return False
        if policy.agent_key is not None and policy.agent_key != agent_key:
            return False
        if policy.area_key is not None and policy.area_key != area_key:
            return False
        if policy.client_id is not None and str(policy.client_id) != (
            str(client_id) if client_id else None
        ):
            return False
        if policy.portfolio_key is not None and policy.portfolio_key != portfolio_key:
            return False
        return True

    @staticmethod
    def _specificity(policy: AgentAutonomyPolicy) -> int:
        return sum(
            1
            for field in (
                policy.agent_key,
                policy.area_key,
                policy.client_id,
                policy.portfolio_key,
            )
            if field is not None
        )

    @staticmethod
    def _default_idempotency_key(
        workspace_id: UUID,
        event_type: str,
        agent_key: Optional[str],
        action: Optional[str],
    ) -> str:
        return (
            f"autonomy:{workspace_id}:{event_type}:"
            f"{agent_key or 'any'}:{action or 'any'}"
        )

    def _render_idempotency_key(
        self,
        policy: AgentAutonomyPolicy,
        *,
        workspace_id: UUID,
        event_type: str,
        agent_key: Optional[str],
        area_key: Optional[str],
        client_id: Optional[UUID],
        portfolio_key: Optional[str],
        action: Optional[str],
    ) -> str:
        context = {
            "workspaceId": str(workspace_id),
            "eventType": event_type,
            "agentKey": agent_key or "any",
            "areaKey": area_key or "any",
            "clientId": str(client_id) if client_id else "any",
            "portfolioKey": portfolio_key or "any",
            "action": action or policy.action,
            "policyId": str(policy.id),
        }
        template = policy.idempotency_key_template
        if not template:
            return self._default_idempotency_key(workspace_id, event_type, agent_key, action)
        try:
            return template.format(**context)
        except (KeyError, IndexError):
            # Template malformado nunca derruba a avaliação — cai para o
            # default determinístico (fail-closed em segurança, não em
            # disponibilidade).
            return self._default_idempotency_key(workspace_id, event_type, agent_key, action)
