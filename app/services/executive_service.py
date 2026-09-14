"""Deterministic catalogue and fail-closed policy for Meus Executivos.

The service is deliberately provider- and persistence-free. It is safe to use
from routers, orchestration and tests without ever fabricating an AI answer.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.models.workspace_membership import MembershipStatus, WorkspaceRole
from app.schemas.executive import (
    ExecutiveKey,
    ExecutiveProfileRead,
    ExecutiveThreadCreate,
    ExecutiveThreadMode,
)

EXECUTIVE_POLICY_VERSION = "executive-council-v1.0.0"
MAX_EXECUTIVES_PER_ROUND = 8
EXECUTIVE_CAPABILITIES = frozenset(
    {"executives.view", "executives.chat", "executives.share", "executives.reports"}
)
ALLOWED_ROLES = frozenset(
    {WorkspaceRole.workspace_owner.value, WorkspaceRole.workspace_admin.value}
)
FORBIDDEN_MUTATIONS = frozenset(
    {
        "alter_budget",
        "alter_campaign",
        "alter_configuration",
        "alter_lead",
        "alter_matter",
        "alter_permissions",
        "call_external_integration",
        "create_lead",
        "dismiss_person",
        "execute_automation",
        "file_petition",
        "hire_person",
        "issue_charge",
        "move_money",
        "protocol_petition",
        "publish_content",
        "send_message",
        "sign_contract",
    }
)
READ_CAPABILITIES = (
    "workspace_configuration.read",
    "crm.read",
    "clients.read",
    "matters.read",
    "conversations.read",
    "documents.read",
    "finance.read",
    "contracts.read",
    "agenda.read",
    "marketing.read",
    "integrations_health.read",
    "team.read",
    "product_knowledge.read",
    "playbook_published.read",
)


class ExecutivePolicyViolation(ValueError):  # noqa: N818 - compatibility policy name
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ExecutiveSelection:
    participants: tuple[ExecutiveKey, ...]
    mentioned: tuple[ExecutiveKey, ...]
    all_requested: bool


def _profile(
    key: ExecutiveKey,
    display_name: str,
    role: str,
    description: str,
    domains: tuple[str, ...],
) -> ExecutiveProfileRead:
    return ExecutiveProfileRead(
        key=key,
        display_name=display_name,
        mention=f"@{key.value.upper()}",
        role=role,
        description=description,
        domain_manifest={"domains": list(domains)},
        allowed_read_capabilities=READ_CAPABILITIES,
        forbidden_capabilities=tuple(sorted(FORBIDDEN_MUTATIONS)),
        prompt_version=EXECUTIVE_POLICY_VERSION,
    )


EXECUTIVE_CATALOG: dict[ExecutiveKey, ExecutiveProfileRead] = {
    ExecutiveKey.ceo: _profile(
        ExecutiveKey.ceo,
        "CEO",
        "Diretor Executivo",
        "Coordena o conselho, preserva divergências e sintetiza prioridades com evidências.",
        ("strategy", "prioritization", "council_orchestration", "human_decisions"),
    ),
    ExecutiveKey.coo: _profile(
        ExecutiveKey.coo,
        "COO",
        "Diretor de Operações",
        "Analisa capacidade, carteira, SLA, filas, gargalos e qualidade operacional.",
        ("operations", "capacity", "sla", "workflow", "service_quality"),
    ),
    ExecutiveKey.cfo: _profile(
        ExecutiveKey.cfo,
        "CFO",
        "Diretor Financeiro",
        "Distingue receita contratada, faturada, recebida e vencida e avalia riscos de caixa.",
        ("finance", "cashflow", "receivables", "contracts", "margin_evidence"),
    ),
    ExecutiveKey.cro: _profile(
        ExecutiveKey.cro,
        "CRO",
        "Diretor de Receita",
        "Analisa marketing, vendas, atribuição, funil, conversão, retenção e expansão.",
        ("revenue", "marketing", "sales", "attribution", "retention"),
    ),
    ExecutiveKey.cpo: _profile(
        ExecutiveKey.cpo,
        "CPO",
        "Diretor de Produto e Experiência",
        "Analisa serviços jurídicos, jornada, onboarding, oferta e experiência do cliente.",
        ("product", "services", "customer_journey", "onboarding", "experience"),
    ),
    ExecutiveKey.cto: _profile(
        ExecutiveKey.cto,
        "CTO",
        "Diretor de Tecnologia e Inovação",
        "Avalia inovação, automação, engenharia, IA, escalabilidade e riscos técnicos.",
        ("technology", "innovation", "automation", "engineering", "ai"),
    ),
    ExecutiveKey.cio: _profile(
        ExecutiveKey.cio,
        "CIO",
        "Diretor de Informação e Sistemas Internos",
        "Avalia dados, acessos, integrações, continuidade e estados local, homologado e produção.",
        ("information", "data_quality", "security", "integrations", "continuity"),
    ),
    ExecutiveKey.chro: _profile(
        ExecutiveKey.chro,
        "CHRO",
        "Diretor de Pessoas",
        "Analisa capacidade e competências sem inferir emoções ou automatizar decisões de emprego.",
        ("people", "capacity", "skills", "training", "workload"),
    ),
}

_MENTION_PATTERN = re.compile(
    r"(?<![\w@])@(?P<mention>CEO|COO|CFO|CRO|CPO|CTO|CIO|CHRO|TODOS)\b", re.IGNORECASE
)


class ExecutiveService:
    @staticmethod
    def list_profiles() -> tuple[ExecutiveProfileRead, ...]:
        return tuple(EXECUTIVE_CATALOG[key].model_copy(deep=True) for key in ExecutiveKey)

    @staticmethod
    def get_profile(key: ExecutiveKey | str) -> ExecutiveProfileRead:
        try:
            normalized = key if isinstance(key, ExecutiveKey) else ExecutiveKey(key.casefold())
        except (ValueError, AttributeError) as exc:
            raise ExecutivePolicyViolation(
                "EXECUTIVE_UNKNOWN", "Executivo não registrado."
            ) from exc
        return EXECUTIVE_CATALOG[normalized].model_copy(deep=True)

    @staticmethod
    def require_owner_or_admin(role: WorkspaceRole | str | None) -> None:
        value = role.value if isinstance(role, WorkspaceRole) else str(role or "")
        if value not in ALLOWED_ROLES:
            raise ExecutivePolicyViolation(
                "EXECUTIVE_ACCESS_DENIED",
                "Meus Executivos é restrito a owners e admins ativos do workspace.",
            )

    @staticmethod
    def require_capability(capability: str) -> None:
        if capability not in EXECUTIVE_CAPABILITIES:
            raise ExecutivePolicyViolation(
                "EXECUTIVE_CAPABILITY_DENIED", "Capability executiva desconhecida ou negada."
            )

    @staticmethod
    def require_share_target(
        *,
        actor_workspace_id: str,
        target_workspace_id: str,
        target_membership_status: MembershipStatus | str,
        target_role: WorkspaceRole | str,
    ) -> None:
        """Allow shares only to active owners/admins in the same workspace."""
        status_value = (
            target_membership_status.value
            if isinstance(target_membership_status, MembershipStatus)
            else str(target_membership_status)
        )
        role_value = (
            target_role.value if isinstance(target_role, WorkspaceRole) else str(target_role)
        )
        if not actor_workspace_id or actor_workspace_id != target_workspace_id:
            raise ExecutivePolicyViolation(
                "EXECUTIVE_CROSS_WORKSPACE_SHARE_DENIED",
                "A conversa não pode ser compartilhada fora do workspace.",
            )
        if status_value != MembershipStatus.active.value or role_value not in ALLOWED_ROLES:
            raise ExecutivePolicyViolation(
                "EXECUTIVE_SHARE_TARGET_DENIED",
                "Compartilhamento exige owner ou admin ativo do mesmo workspace.",
            )

    @staticmethod
    def require_consultative_action(action: str) -> None:
        normalized = action.strip().casefold().replace("-", "_").replace(" ", "_")
        if normalized in FORBIDDEN_MUTATIONS:
            raise ExecutivePolicyViolation(
                "EXECUTIVE_ACTION_REQUIRES_HUMAN",
                "O conselho pode analisar e propor, mas não executar esta ação.",
            )

    @staticmethod
    def extract_mentions(body: str) -> tuple[tuple[ExecutiveKey, ...], bool]:
        seen: set[ExecutiveKey] = set()
        all_requested = False
        for match in _MENTION_PATTERN.finditer(body):
            mention = match.group("mention").casefold()
            if mention == "todos":
                all_requested = True
                continue
            seen.add(ExecutiveKey(mention))
        ordered = tuple(key for key in ExecutiveKey if key in seen)
        return ordered, all_requested

    @classmethod
    def normalize_participants(
        cls,
        selected: Iterable[ExecutiveKey | str],
        *,
        message_body: str = "",
    ) -> ExecutiveSelection:
        normalized: set[ExecutiveKey] = {ExecutiveKey.ceo}
        for value in selected:
            try:
                normalized.add(
                    value if isinstance(value, ExecutiveKey) else ExecutiveKey(value.casefold())
                )
            except (ValueError, AttributeError) as exc:
                raise ExecutivePolicyViolation(
                    "EXECUTIVE_UNKNOWN", "Participante executivo inválido."
                ) from exc
        mentioned, all_requested = cls.extract_mentions(message_body)
        normalized.update(mentioned)
        if all_requested:
            normalized.update(ExecutiveKey)
        if len(normalized) > MAX_EXECUTIVES_PER_ROUND:
            raise ExecutivePolicyViolation(
                "EXECUTIVE_LIMIT_EXCEEDED", "A rodada excede oito executivos."
            )
        return ExecutiveSelection(
            participants=tuple(key for key in ExecutiveKey if key in normalized),
            mentioned=mentioned,
            all_requested=all_requested,
        )

    @classmethod
    def validate_thread_create(cls, payload: ExecutiveThreadCreate) -> ExecutiveThreadCreate:
        selection = cls.normalize_participants(payload.selected_executives)
        if payload.mode == ExecutiveThreadMode.direct and len(selection.participants) > 2:
            raise ExecutivePolicyViolation(
                "DIRECT_THREAD_PARTICIPANTS_INVALID",
                "Conversa direta aceita CEO e no máximo um executivo especialista.",
            )
        return payload.model_copy(update={"selected_executives": list(selection.participants)})

    @staticmethod
    def policy_snapshot() -> dict[str, object]:
        return {
            "version": EXECUTIVE_POLICY_VERSION,
            "executives": [key.value for key in ExecutiveKey],
            "ceoAlwaysPresent": True,
            "consultativeOnly": True,
            "externalActionsAllowed": False,
            "allowedRoles": sorted(ALLOWED_ROLES),
            "capabilities": sorted(EXECUTIVE_CAPABILITIES),
            "maxExecutivesPerRound": MAX_EXECUTIVES_PER_ROUND,
            "providerFallbackAllowed": False,
            "simulatedResponsesAllowed": False,
        }
