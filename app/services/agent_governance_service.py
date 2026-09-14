"""Versioned governance contract for the native agent hierarchy.

This module is intentionally deterministic. Model output cannot change authority,
scope, delegation paths, human gates or the precedence of manual corrections.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping

from app.data.legal_area_catalog import SPECIALIZED_AGENT_BY_KEY, canonical_area_key

AGENT_GOVERNANCE_VERSION = "agent-governance-v1.5.0"
MAX_DELEGATION_DEPTH = 4
COORDINATION_AGENT_KEY = "orquestrador"
OWNER_BRIDGE_AGENT_KEY = "hermes"
OWNER_BRIDGE_DELEGATOR_KEY = "secretaria"
HUMAN_QUEUE_AGENT_KEY = COORDINATION_AGENT_KEY


CORE_PARENT_BY_KEY: dict[str, str | None] = {
    "orquestrador": None,
    "hermes": "secretaria",
    "sdr": "orquestrador",
    "sdr_saude_vendas": "sdr",
    "juridico": "orquestrador",
    "crm": "orquestrador",
    "closer": "orquestrador",
    "secretaria": "orquestrador",
    "paralegal": "juridico",
    "peticao": "juridico",
    "revisor_juridico": "juridico",
    "andamento": "orquestrador",
    "pos_venda_cliente": "andamento",
    "financeiro": "orquestrador",
    "cobranca": "financeiro",
    "marketing": "orquestrador",
    "trafego_google": "marketing",
    "disparos_meta": "marketing",
    "track": "orquestrador",
    "tracking_engineer": "track",
    "atendimento_meta": "sdr",
    "infoproduto": "orquestrador",
    "audio": "orquestrador",
    "auditor": None,
    "ti": None,
    "qualidade": None,
}

INDEPENDENT_CONTROL_AGENTS = frozenset({"auditor", "ti", "qualidade"})
FROZEN_AREA_SAFE_AGENTS = frozenset(
    {COORDINATION_AGENT_KEY, OWNER_BRIDGE_AGENT_KEY, *INDEPENDENT_CONTROL_AGENTS}
)
INITIATOR_TYPES = frozenset({"human", "system", "agent"})
HARD_HUMAN_GATES = frozenset(
    {
        "send_external",
        "sign_contract",
        "change_fees",
        "grant_discount",
        "file_petition",
        "protocol_petition",
        "settle_case",
        "delete_evidence",
        "unlock_area",
        "change_permissions",
    }
)

# Human-in-the-loop autonomy modes configurable per workspace+agent (owner/admin
# panel only; never model output). "hil_total" approves every external
# response, "hil_marcos" approves only milestones (proposal, contract,
# discount), "autonomo" lets the agent run the negotiation up to contract
# acceptance on its own. The pre-approved negotiation band (floor + band,
# both set by a human) is what makes an autonomous grant_discount legitimate:
# the human already approved that corridor, so staying inside it does not
# need a fresh human touch — it still gets recorded in audit either way.
AGENT_AUTONOMY_MODES = frozenset({"hil_total", "hil_marcos", "autonomo"})
DEFAULT_AUTONOMY_MODE = "hil_total"

# The only hard gate that can be satisfied autonomously, and only inside a
# pre-approved negotiation band. Every other hard gate — sign_contract,
# file_petition, protocol_petition, etc. — stays a hard human gate in every
# autonomy mode, with no exception path.
AUTONOMOUS_GATE_EXCEPTIONS = frozenset({"grant_discount"})


class AutonomyLevel(str, Enum):
    """Plano Mestre §14 — a expressão canônica dos três modos HIL já
    existentes (hil_total/hil_marcos/autonomo) num espectro A0-A4:

    A0 (observar): o agente só lê e relata; nunca muta estado.
    A1 (recomendar): o agente sugere uma ação; nunca muta estado.
    A2 (preparar): o agente monta a ação (rascunho/proposta) mas nunca a
        executa — a execução em si sempre fica com o humano.
    A3 (executar ação interna reversível): o agente executa sozinho, mas
        só ação interna e reversível; ação externa continua gated por
        `external_delivery_allowed()` e pelos HARD_HUMAN_GATES, sem exceção.
    A4 (decisão humana obrigatória): equivalente a hil_total — toda ação
        exige um humano, sempre.

    Este enum não substitui hil_total/hil_marcos/autonomo — é a leitura
    A0-A4 sobre eles (ver `autonomy_level_for_mode`), mais granular que os
    três modos porque uma AutonomyPolicy por evento pode declarar A0 ou A1
    (que nenhum modo HIL isoladamente expressa) para eventos puramente de
    observação/recomendação.
    """

    A0_OBSERVE = "A0"
    A1_RECOMMEND = "A1"
    A2_PREPARE = "A2"
    A3_EXECUTE_INTERNAL_REVERSIBLE = "A3"
    A4_HUMAN_REQUIRED = "A4"


AUTONOMY_LEVELS = tuple(level.value for level in AutonomyLevel)

# Mapeamento canônico modo HIL existente -> nível A (Plano Mestre §14):
# hil_total é sempre A4 (o painel já promete "aprova cada resposta externa");
# autonomo é sempre A3 (o painel já promete "conduz sozinho até o aceite" —
# mas só ação interna reversível, nunca externa, ver AutonomyLevel.A3);
# hil_marcos vira A2: o agente prepara a proposta/marco, mas a decisão do
# marco em si (proposta, contrato, desconto) sempre volta para o humano —
# exatamente o que "aprova só marcos" já descrevia antes deste mapeamento.
AUTONOMY_LEVEL_BY_MODE: dict[str, AutonomyLevel] = {
    "hil_total": AutonomyLevel.A4_HUMAN_REQUIRED,
    "hil_marcos": AutonomyLevel.A2_PREPARE,
    "autonomo": AutonomyLevel.A3_EXECUTE_INTERNAL_REVERSIBLE,
}


def autonomy_level_for_mode(autonomy_mode: str) -> AutonomyLevel:
    """Resolve o modo HIL configurado (workspace+agente) para o nível A0-A4
    canônico. Fail-closed: um modo desconhecido nunca vira um nível
    permissivo — levanta violação, igual a `authorize()`."""
    if autonomy_mode not in AGENT_AUTONOMY_MODES:
        raise AgentGovernanceViolation(
            "AUTONOMY_MODE_INVALID",
            "O modo de autonomia do workspace para este agente não é reconhecido.",
        )
    return AUTONOMY_LEVEL_BY_MODE[autonomy_mode]


class AgentGovernanceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# Compatibility name used by the runtime and existing audit/test integrations.
AgentGovernanceViolation = AgentGovernanceError


def _parent_map() -> dict[str, str | None]:
    parents = dict(CORE_PARENT_BY_KEY)
    for key, agent in SPECIALIZED_AGENT_BY_KEY.items():
        parents[key] = str(agent.get("parentAgentKey") or COORDINATION_AGENT_KEY)
    return parents


PARENT_BY_KEY = _parent_map()


def validate_agent_hierarchy() -> None:
    for key, parent in PARENT_BY_KEY.items():
        if parent is not None and parent not in PARENT_BY_KEY:
            raise RuntimeError(f"Agente {key} possui pai inexistente: {parent}.")
        visited: set[str] = set()
        cursor: str | None = key
        while cursor is not None:
            if cursor in visited:
                raise RuntimeError(f"Ciclo detectado na hierarquia de agentes: {key}.")
            visited.add(cursor)
            cursor = PARENT_BY_KEY.get(cursor)
        if len(visited) - 1 > MAX_DELEGATION_DEPTH:
            raise RuntimeError(
                f"Agente {key} excede a profundidade máxima da hierarquia."
            )


validate_agent_hierarchy()


def _effective_parent_map(
    extra_parent_by_key: dict[str, str | None] | None = None,
) -> dict[str, str | None]:
    parents = dict(PARENT_BY_KEY)
    for key, parent in (extra_parent_by_key or {}).items():
        if key in parents:
            raise AgentGovernanceViolation(
                "AGENT_KEY_RESERVED",
                "Um agente personalizado não pode substituir a governança nativa.",
            )
        if parent not in parents:
            raise AgentGovernanceViolation(
                "AGENT_PARENT_UNKNOWN",
                "O pai do agente personalizado não pertence à governança nativa.",
            )
        parents[key] = parent
    for key in tuple(parents):
        visited: set[str] = set()
        cursor: str | None = key
        while cursor is not None:
            if cursor in visited:
                raise AgentGovernanceViolation(
                    "AGENT_HIERARCHY_CYCLE",
                    "A hierarquia personalizada contém um ciclo.",
                )
            visited.add(cursor)
            cursor = parents.get(cursor)
        if len(visited) - 1 > MAX_DELEGATION_DEPTH:
            raise AgentGovernanceViolation(
                "DELEGATION_DEPTH_EXCEEDED",
                "A hierarquia personalizada excede a profundidade segura.",
            )
    return parents


@dataclass(frozen=True)
class AgentGovernanceDecision:
    policy_version: str
    policy_digest_algorithm: str
    policy_digest: str
    target_agent_key: str
    initiator_type: str
    requested_by_agent_key: str | None
    delegation_depth: int
    hierarchy_path: tuple[str, ...]
    area_key: str | None
    product_key: str | None
    workspace_id: str
    client_id: str
    manual_context_revision: int
    manual_override_wins: bool
    human_handoff_required: bool
    human_request_agent_key: str
    external_actions_allowed: bool
    requested_action: str
    allowed_tool_keys: tuple[str, ...]
    requested_tool_keys: tuple[str, ...]
    allowed_delegate_keys: tuple[str, ...]
    autonomy_mode: str
    autonomous_grant_applied: bool
    # Plano Mestre §14 — leitura A0-A4 do autonomy_mode acima (ver
    # `autonomy_level_for_mode`), mais o resultado do enforcement por nível
    # quando o chamador pede execução autônoma explicitamente.
    autonomy_level: str
    autonomous_execution_requested: bool
    action_is_external: bool
    autonomous_execution_applied: bool

    def as_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["hierarchy_path"] = list(self.hierarchy_path)
        payload["allowed_tool_keys"] = list(self.allowed_tool_keys)
        payload["requested_tool_keys"] = list(self.requested_tool_keys)
        payload["allowed_delegate_keys"] = list(self.allowed_delegate_keys)
        return payload


class AgentGovernanceService:
    @staticmethod
    def hierarchy_path(
        agent_key: str,
        parent_by_key: dict[str, str | None] | None = None,
    ) -> tuple[str, ...]:
        parents = parent_by_key or PARENT_BY_KEY
        if agent_key not in parents:
            raise AgentGovernanceViolation("AGENT_UNKNOWN", "Agente não registrado na governança.")
        path: list[str] = []
        cursor: str | None = agent_key
        while cursor is not None:
            path.append(cursor)
            cursor = parents[cursor]
        return tuple(reversed(path))

    @staticmethod
    def children(
        agent_key: str,
        parent_by_key: dict[str, str | None] | None = None,
    ) -> tuple[str, ...]:
        parents = parent_by_key or PARENT_BY_KEY
        return tuple(sorted(key for key, parent in parents.items() if parent == agent_key))

    @classmethod
    def authorize(
        cls,
        *,
        target_agent_key: str,
        initiator_type: str,
        requested_by_agent_key: str | None,
        delegation_depth: int,
        matter_area_key: str | None,
        matter_product_key: str | None,
        run_workspace_id: str,
        matter_workspace_id: str,
        context_client_id: str,
        matter_client_id: str,
        manual_context_revision: int,
        area_enabled: bool,
        request_human: bool,
        requested_action: str = "analyze",
        available_tool_keys: tuple[str, ...] = (),
        requested_tool_keys: tuple[str, ...] = (),
        extra_parent_by_key: dict[str, str | None] | None = None,
        autonomy_mode: str = DEFAULT_AUTONOMY_MODE,
        negotiation_floor_pct: float | None = None,
        negotiation_band_pct: float | None = None,
        requested_discount_pct: float | None = None,
        # Plano Mestre §14 — só quando o chamador realmente pede para
        # executar autonomamente (não apenas observar/preparar) o
        # enforcement por nível A0-A4 entra em jogo; default False não
        # muda nenhum comportamento existente (compatibilidade total com
        # todo chamador anterior a este enforcement).
        autonomous_execution_requested: bool = False,
        action_is_external: bool = False,
    ) -> AgentGovernanceDecision:
        parents = _effective_parent_map(extra_parent_by_key)
        if target_agent_key not in parents:
            raise AgentGovernanceViolation("AGENT_UNKNOWN", "Agente não registrado na governança.")
        if not run_workspace_id or not matter_workspace_id:
            raise AgentGovernanceViolation(
                "WORKSPACE_SCOPE_REQUIRED",
                "Toda execução precisa estar vinculada a um workspace e a um caso.",
            )
        if run_workspace_id != matter_workspace_id:
            raise AgentGovernanceViolation(
                "CROSS_WORKSPACE_ACCESS_DENIED",
                "A execução não pode acessar um caso de outro workspace.",
            )
        if not context_client_id or not matter_client_id:
            raise AgentGovernanceViolation(
                "CLIENT_SCOPE_REQUIRED",
                "Toda execução precisa estar vinculada ao cliente exato do caso.",
            )
        if context_client_id != matter_client_id:
            raise AgentGovernanceViolation(
                "CROSS_CLIENT_ACCESS_DENIED",
                "A execução não pode carregar contexto de outro cliente.",
            )
        if initiator_type not in INITIATOR_TYPES:
            raise AgentGovernanceViolation(
                "INITIATOR_TYPE_INVALID", "O tipo de iniciador não pertence ao contrato."
            )
        if initiator_type == "agent" and not requested_by_agent_key:
            raise AgentGovernanceViolation(
                "DELEGATOR_REQUIRED",
                "Toda execução iniciada por agente precisa identificar o delegante.",
            )
        if initiator_type != "agent" and requested_by_agent_key:
            raise AgentGovernanceViolation(
                "DELEGATOR_NOT_ALLOWED",
                "Somente uma execução iniciada por agente pode declarar delegante.",
            )
        if target_agent_key == OWNER_BRIDGE_AGENT_KEY and initiator_type != "agent":
            raise AgentGovernanceViolation(
                "OWNER_BRIDGE_DELEGATOR_REQUIRED",
                "Hermes só pode ser acionado pela Secretaria na cadeia agentica.",
            )
        if initiator_type == "agent" and delegation_depth < 1:
            raise AgentGovernanceViolation(
                "DELEGATION_DEPTH_INVALID",
                "Uma delegação entre agentes deve começar na profundidade um.",
            )
        if initiator_type != "agent" and delegation_depth != 0:
            raise AgentGovernanceViolation(
                "DELEGATION_DEPTH_INVALID",
                "Execuções humanas ou de sistema começam na profundidade zero.",
            )
        if delegation_depth < 0 or delegation_depth > MAX_DELEGATION_DEPTH:
            raise AgentGovernanceViolation(
                "DELEGATION_DEPTH_EXCEEDED",
                "A cadeia de delegação excedeu o limite seguro.",
            )
        if autonomy_mode not in AGENT_AUTONOMY_MODES:
            raise AgentGovernanceViolation(
                "AUTONOMY_MODE_INVALID",
                "O modo de autonomia do workspace para este agente não é reconhecido.",
            )
        autonomy_level = autonomy_level_for_mode(autonomy_mode)

        # Plano Mestre §14 — enforcement por nível A0-A4. Só entra em jogo
        # quando o chamador pede execução autônoma explicitamente
        # (`autonomous_execution_requested=True`); observar/recomendar/
        # preparar sem pedir execução nunca cai aqui, então nenhum
        # chamador anterior a este enforcement muda de comportamento.
        autonomous_execution_applied = False
        if autonomous_execution_requested:
            if autonomy_level in (
                AutonomyLevel.A0_OBSERVE,
                AutonomyLevel.A1_RECOMMEND,
            ):
                raise AgentGovernanceViolation(
                    "AUTONOMY_LEVEL_A0_A1_NEVER_MUTATE",
                    "Nos níveis A0 (observar) e A1 (recomendar) o agente nunca "
                    "muta estado nem executa ação — só relata ou sugere.",
                )
            if autonomy_level == AutonomyLevel.A2_PREPARE:
                raise AgentGovernanceViolation(
                    "AUTONOMY_LEVEL_A2_PREPARES_ONLY",
                    "No nível A2 o agente prepara a ação (rascunho/proposta) "
                    "mas nunca a executa — a execução do marco fica com o humano.",
                )
            if autonomy_level == AutonomyLevel.A4_HUMAN_REQUIRED:
                raise AgentGovernanceViolation(
                    "AUTONOMY_LEVEL_A4_HUMAN_REQUIRED",
                    "No nível A4 toda ação exige decisão humana, sem exceção.",
                )
            # A3: executa sozinho, mas só ação interna reversível — ação
            # externa continua gated por `external_delivery_allowed()` e
            # pelos HARD_HUMAN_GATES, sem exceção nesta camada.
            if action_is_external:
                raise AgentGovernanceViolation(
                    "AUTONOMY_LEVEL_A3_EXTERNAL_ACTION_GATED",
                    "No nível A3 o agente só executa ação interna e reversível; "
                    "ação externa continua exigindo o gate de entrega externa "
                    "e, quando aplicável, aprovação humana.",
                )
            autonomous_execution_applied = True

        for label, value in (
            ("negotiation_floor_pct", negotiation_floor_pct),
            ("negotiation_band_pct", negotiation_band_pct),
            ("requested_discount_pct", requested_discount_pct),
        ):
            if value is not None and not (0 <= value <= 1):
                raise AgentGovernanceViolation(
                    "NEGOTIATION_VALUE_INVALID",
                    f"O valor de {label} precisa estar entre 0 e 1.",
                )

        autonomous_grant_applied = False
        if requested_action in HARD_HUMAN_GATES:
            # grant_discount is the one hard gate a workspace can pre-approve for
            # autonomous handling — and only fully inside the band: sign_contract,
            # file_petition, protocol_petition and everything else in
            # HARD_HUMAN_GATES stay hard-gated in every autonomy mode, no
            # exceptions. The human who set the floor/band already gave the
            # approval that matters; staying inside it just needs an audit trail.
            eligible_for_autonomy = (
                requested_action in AUTONOMOUS_GATE_EXCEPTIONS
                and autonomy_mode == "autonomo"
            )
            if eligible_for_autonomy:
                within_band = (
                    negotiation_floor_pct is not None
                    and negotiation_band_pct is not None
                    and requested_discount_pct is not None
                    and requested_discount_pct <= negotiation_band_pct
                    and (1 - requested_discount_pct) >= negotiation_floor_pct
                )
                if within_band:
                    autonomous_grant_applied = True
                else:
                    raise AgentGovernanceViolation(
                        "HUMAN_GATE_REQUIRED",
                        "O desconto solicitado está fora da banda pré-aprovada ou "
                        "nenhuma banda foi configurada pelo humano responsável; a "
                        "concessão precisa escalar para revisão humana.",
                    )
            else:
                raise AgentGovernanceViolation(
                    "HUMAN_GATE_REQUIRED",
                    "Esta ação não pode ser executada autonomamente por agentes.",
                )

        allowed_tools = tuple(sorted(set(available_tool_keys)))
        requested_tools = tuple(sorted(set(requested_tool_keys)))
        unauthorized_tools = sorted(set(requested_tools) - set(allowed_tools))
        if unauthorized_tools:
            raise AgentGovernanceViolation(
                "TOOL_SCOPE_DENIED",
                "A execução solicitou ferramenta fora da alçada do agente: "
                + ", ".join(unauthorized_tools),
            )

        if requested_by_agent_key:
            if requested_by_agent_key not in parents:
                raise AgentGovernanceViolation(
                    "DELEGATOR_UNKNOWN", "O agente delegante não pertence à hierarquia."
                )
            if requested_by_agent_key == target_agent_key:
                raise AgentGovernanceViolation(
                    "SELF_DELEGATION_BLOCKED", "Um agente não pode delegar trabalho a si mesmo."
                )
            if requested_by_agent_key == OWNER_BRIDGE_AGENT_KEY:
                raise AgentGovernanceViolation(
                    "OWNER_BRIDGE_CANNOT_ORCHESTRATE",
                    "Hermes é a ponte com o humano e não pode orquestrar outros agentes.",
                )
            if target_agent_key in INDEPENDENT_CONTROL_AGENTS:
                raise AgentGovernanceViolation(
                    "INDEPENDENT_CONTROL_REQUIRED",
                    "Agentes de controle independente só podem ser iniciados "
                    "por humano ou sistema.",
                )
            if target_agent_key == OWNER_BRIDGE_AGENT_KEY:
                if requested_by_agent_key != OWNER_BRIDGE_DELEGATOR_KEY:
                    raise AgentGovernanceViolation(
                        "OWNER_BRIDGE_DELEGATOR_REQUIRED",
                        "Somente a Secretaria pode encaminhar uma exceção ao Hermes executivo.",
                    )
                if not request_human:
                    raise AgentGovernanceViolation(
                        "OWNER_BRIDGE_REQUEST_REQUIRED",
                        "Uma delegação ao Hermes precisa representar uma solicitação humana.",
                    )
                hierarchy_depth = len(cls.hierarchy_path(target_agent_key, parents)) - 1
                if delegation_depth != hierarchy_depth:
                    raise AgentGovernanceViolation(
                        "DELEGATION_DEPTH_INCONSISTENT",
                        "A profundidade declarada deve corresponder exatamente "
                        "à cadeia de autoridade.",
                    )
            else:
                expected_parent = parents[target_agent_key]
                if requested_by_agent_key != expected_parent:
                    raise AgentGovernanceViolation(
                        "INVALID_DELEGATION_PATH",
                        "A delegação não respeita a hierarquia aprovada.",
                    )
                hierarchy_depth = len(cls.hierarchy_path(target_agent_key, parents)) - 1
                if delegation_depth != hierarchy_depth:
                    raise AgentGovernanceViolation(
                        "DELEGATION_DEPTH_INCONSISTENT",
                        "A profundidade declarada deve corresponder exatamente "
                        "à cadeia de autoridade.",
                    )

        specialized = SPECIALIZED_AGENT_BY_KEY.get(target_agent_key)
        normalized_area = canonical_area_key(matter_area_key)
        frozen_handoff_step = target_agent_key == HUMAN_QUEUE_AGENT_KEY and request_human
        if (
            normalized_area
            and not area_enabled
            and target_agent_key not in FROZEN_AREA_SAFE_AGENTS
            and not frozen_handoff_step
        ):
            raise AgentGovernanceViolation(
                "AREA_FROZEN",
                "A área deste agente está congelada e precisa ser desbloqueada "
                "pela equipe autorizada do SaaS.",
            )
        if specialized:
            agent_area = str(specialized["areaKey"])
            if normalized_area != agent_area:
                raise AgentGovernanceViolation(
                    "AGENT_SCOPE_MISMATCH",
                    "O agente especializado não pode acessar um caso de outra área.",
                )
            agent_product = specialized.get("productKey")
            if agent_product and matter_product_key != agent_product:
                raise AgentGovernanceViolation(
                    "AGENT_PRODUCT_SCOPE_MISMATCH",
                    "O subagente não pode acessar outro produto jurídico da mesma área.",
                )

        allowed_delegates = list(cls.children(target_agent_key, parents))
        if normalized_area and not area_enabled:
            allowed_delegates = []
        if target_agent_key == OWNER_BRIDGE_DELEGATOR_KEY:
            allowed_delegates.append(OWNER_BRIDGE_AGENT_KEY)

        policy = cls.policy_snapshot(extra_parent_by_key=extra_parent_by_key)

        return AgentGovernanceDecision(
            policy_version=str(policy["version"]),
            policy_digest_algorithm=str(policy["policyDigestAlgorithm"]),
            policy_digest=str(policy["policyDigest"]),
            target_agent_key=target_agent_key,
            initiator_type=initiator_type,
            requested_by_agent_key=requested_by_agent_key,
            delegation_depth=delegation_depth,
            hierarchy_path=cls.hierarchy_path(target_agent_key, parents),
            area_key=normalized_area,
            product_key=matter_product_key,
            workspace_id=run_workspace_id,
            client_id=matter_client_id,
            manual_context_revision=max(0, int(manual_context_revision or 0)),
            manual_override_wins=True,
            human_handoff_required=bool(request_human),
            human_request_agent_key=HUMAN_QUEUE_AGENT_KEY,
            external_actions_allowed=False,
            requested_action=requested_action,
            allowed_tool_keys=allowed_tools,
            requested_tool_keys=requested_tools,
            allowed_delegate_keys=tuple(sorted(set(allowed_delegates))),
            autonomy_mode=autonomy_mode,
            autonomous_grant_applied=autonomous_grant_applied,
            autonomy_level=autonomy_level.value,
            autonomous_execution_requested=bool(autonomous_execution_requested),
            action_is_external=bool(action_is_external),
            autonomous_execution_applied=autonomous_execution_applied,
        )

    @classmethod
    def policy_snapshot(
        cls,
        *,
        extra_parent_by_key: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        parents = _effective_parent_map(extra_parent_by_key)
        policy = {
            "version": AGENT_GOVERNANCE_VERSION,
            "coordinationAgentKey": COORDINATION_AGENT_KEY,
            "ownerBridgeAgentKey": OWNER_BRIDGE_AGENT_KEY,
            "ownerBridgeDelegatorKey": OWNER_BRIDGE_DELEGATOR_KEY,
            "maxDelegationDepth": MAX_DELEGATION_DEPTH,
            "manualOverrideWins": True,
            "crossAreaAccessDisabled": True,
            "crossProductAccessDisabled": True,
            "crossWorkspaceAccessDenied": True,
            "crossClientAccessDenied": True,
            "leastPrivilegeTools": True,
            "delegationRules": {
                "directParentOnly": True,
                "ownerBridgeOnlyFrom": OWNER_BRIDGE_DELEGATOR_KEY,
                "directOwnerBridgeInvocationDisabled": True,
                "independentControlSystemOnly": True,
                "depthValidated": True,
            },
            "hermesOnlyHumanBridge": False,
            "humanQueueCoordinator": HUMAN_QUEUE_AGENT_KEY,
            "hermesExecutiveBridgeOnly": True,
            "frozenAreaSafeAgents": sorted(FROZEN_AREA_SAFE_AGENTS),
            "externalActionsAllowed": False,
            "hardHumanGates": sorted(HARD_HUMAN_GATES),
            "independentControlAgents": sorted(INDEPENDENT_CONTROL_AGENTS),
            "initiatorTypes": sorted(INITIATOR_TYPES),
            "parentByAgentKey": parents,
            # Plano Mestre §14 — leitura A0-A4 canônica dos modos HIL.
            "autonomyLevels": list(AUTONOMY_LEVELS),
            "autonomyLevelByMode": {
                mode: level.value for mode, level in AUTONOMY_LEVEL_BY_MODE.items()
            },
        }
        canonical = json.dumps(
            policy,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return {
            **policy,
            "policyDigestAlgorithm": "sha256",
            "policyDigest": hashlib.sha256(canonical).hexdigest(),
        }


HANDOFF_SUMMARY_SCHEMA_VERSION = "agent-handoff-summary-v1"


@dataclass(frozen=True)
class HandoffSummary:
    """Structured resumo attached to every agent-to-agent delegation.

    Carries forward exactly what the next agent (or the human who picks up
    the thread) needs so the lead is never asked to repeat their story:
    the working thesis, a compact dossier summary, the objections already
    contornadas and the agreed next step.
    """

    schema_version: str
    thesis: str
    dossier_summary: str
    objections_handled: tuple[str, ...]
    next_step: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "thesis": self.thesis,
            "dossierSummary": self.dossier_summary,
            "objectionsHandled": list(self.objections_handled),
            "nextStep": self.next_step,
        }


def _handoff_text(value: Any, fallback: str) -> str:
    text = str(value).strip() if value is not None else ""
    return text if text else fallback


def _summarize_handoff_dossier(dossier: Mapping[str, Any] | None) -> str:
    if not dossier:
        return "Nenhum dossiê levantado até aqui."
    parts: list[str] = []
    area = dossier.get("legalAreaKey") or dossier.get("legal_area_key")
    if area:
        parts.append(f"área {area}")
    product = dossier.get("legalProductKey") or dossier.get("legal_product_key")
    if product:
        parts.append(f"produto {product}")
    client = dossier.get("clientName") or dossier.get("client_name")
    if client:
        parts.append(f"cliente {client}")
    evidence = dossier.get("evidenceRefs") or dossier.get("evidence_refs") or ()
    if evidence:
        parts.append(f"{len(evidence)} evidência(s) já reunida(s)")
    if not parts:
        return "Dossiê iniciado, ainda sem itens estruturados."
    return "Dossiê: " + "; ".join(parts) + "."


def build_handoff_summary(context: Mapping[str, Any]) -> HandoffSummary:
    """Pure, deterministic structured handoff summary.

    `context` is a plain dict already assembled by the caller (matter facts,
    dossier, objections resolved so far, agreed next step) — this function
    performs no I/O and calls no model. It only folds that data into the
    fixed schema every delegated agent run attaches, so the lead's story
    never needs to be told twice.
    """
    thesis = _handoff_text(
        context.get("thesis") if "thesis" in context else context.get("tese"),
        "Tese ainda não identificada nesta etapa.",
    )
    dossier = context.get("dossier") if "dossier" in context else context.get("dossie")
    dossier_summary = _summarize_handoff_dossier(
        dossier if isinstance(dossier, Mapping) else None
    )
    objections_raw = (
        context.get("objectionsHandled")
        or context.get("objections_handled")
        or context.get("objecoesContornadas")
        or ()
    )
    objections_handled = tuple(
        cleaned
        for cleaned in (_handoff_text(item, "") for item in objections_raw)
        if cleaned
    )
    next_step = _handoff_text(
        context.get("nextStep") or context.get("next_step") or context.get("proximoPasso"),
        "Próximo passo ainda não definido; aguardar orientação do agente/coordenador.",
    )
    return HandoffSummary(
        schema_version=HANDOFF_SUMMARY_SCHEMA_VERSION,
        thesis=thesis,
        dossier_summary=dossier_summary,
        objections_handled=objections_handled,
        next_step=next_step,
    )
