"""RBAC da equipe interna que opera o SaaS KLM Hub."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PlatformPermission:
    module: str
    action: str


@dataclass(frozen=True)
class PlatformRolePackage:
    key: str
    label: str
    description: str
    permissions: tuple[PlatformPermission, ...]
    assignable: bool = True
    requires_mfa: bool = True


def _permission(module: str, *actions: str) -> tuple[PlatformPermission, ...]:
    return tuple(PlatformPermission(module, action) for action in actions)


PLATFORM_PERMISSION_CATALOG: tuple[PlatformPermission, ...] = (
    *_permission("dashboard", "view", "team_view"),
    *_permission("performance", "view"),
    *_permission("reports", "view", "export"),
    *_permission("bulk_operations", "view", "manage", "export"),
    *_permission("finance", "view", "manage", "export"),
    *_permission("customers", "view", "manage", "export"),
    *_permission("plans", "view", "manage", "draft", "preview", "publish", "restore"),
    *_permission("commercial_contracts", "view", "manage"),
    *_permission("commercial_adjustments", "view", "manage"),
    *_permission("entitlement_bonuses", "view", "manage"),
    *_permission(
        "platform_tasks",
        "view",
        "create",
        "edit",
        "assign",
        "review",
        "complete",
        "comment",
    ),
    *_permission("announcements", "view", "manage"),
    *_permission("billing", "view", "manage", "export"),
    *_permission("benefits", "view", "grant", "approve", "reverse", "manage_policy"),
    *_permission("referrals", "manage"),
    *_permission("partners", "manage"),
    *_permission("coupons", "view", "manage", "export"),
    *_permission("sales", "view", "manage", "export"),
    *_permission("onboarding", "view", "manage"),
    *_permission("success", "view", "manage"),
    *_permission(
        "support",
        "view",
        "respond",
        "triage",
        "assign",
        "manage",
        "export",
    ),
    *_permission("support_access", "view", "request"),
    *_permission("staff", "view", "manage"),
    *_permission("staff_reassignment", "view", "manage", "approve"),
    *_permission("workspace_configuration", "view", "manage", "approve"),
    *_permission("agent_templates", "view", "manage", "approve"),
    *_permission("flow_templates", "view", "manage", "approve"),
    *_permission("experience_customization", "view", "manage"),
    *_permission("configuration_deployments", "view", "manage", "rollback"),
    *_permission("content", "view", "manage", "publish"),
    *_permission("marketing", "view", "manage", "export"),
    *_permission("community", "view", "manage"),
    *_permission("whatsapp", "view", "manage"),
    *_permission("integrations", "view", "manage"),
    *_permission("ai_operations", "view", "manage"),
    *_permission("errors", "view", "manage"),
    *_permission("logs", "view", "export"),
    *_permission("audit", "view", "export"),
    *_permission("compliance", "view", "manage", "export"),
    *_permission("security", "view", "manage"),
    *_permission("releases", "view", "manage"),
    *_permission("feature_flags", "view", "manage"),
    *_permission("infrastructure", "view", "manage"),
    *_permission("data_governance", "view", "manage", "export"),
    *_permission("settings", "view", "manage"),
)


def _permissions(*rows: tuple[str, tuple[str, ...]]) -> tuple[PlatformPermission, ...]:
    permissions: list[PlatformPermission] = []
    for module, actions in rows:
        permissions.extend(_permission(module, *actions))
    return tuple(permissions)


PLATFORM_ROLE_PACKAGES: dict[str, PlatformRolePackage] = {
    "platform_owner": PlatformRolePackage(
        key="platform_owner",
        label="Titular da plataforma",
        description="Acesso raiz reservado ao titular, com todos os controles e trilhas.",
        permissions=PLATFORM_PERMISSION_CATALOG,
        assignable=False,
    ),
    "platform_admin": PlatformRolePackage(
        key="platform_admin",
        label="Administrador operacional",
        description="Administra a operação do SaaS sem receber a credencial raiz do titular.",
        permissions=tuple(
            permission
            for permission in PLATFORM_PERMISSION_CATALOG
            if (permission.module, permission.action)
            not in {("plans", "publish"), ("plans", "restore")}
        ),
    ),
    "sales_sdr": PlatformRolePackage(
        key="sales_sdr",
        label="Vendas / SDR",
        description="Prospecção, qualificação e acompanhamento do pipeline comercial do SaaS.",
        permissions=_permissions(
            ("dashboard", ("view",)),
            ("customers", ("view",)),
            ("plans", ("view",)),
            ("sales", ("view", "manage")),
            ("platform_tasks", ("view", "create", "edit", "complete", "comment")),
        ),
    ),
    "commercial_manager": PlatformRolePackage(
        key="commercial_manager",
        label="Gestão comercial",
        description="Metas, forecast, propostas, descontos autorizados e desempenho comercial.",
        permissions=_permissions(
            ("dashboard", ("view", "team_view")),
            ("performance", ("view",)),
            ("reports", ("view", "export")),
            ("finance", ("view",)),
            ("customers", ("view", "export")),
            ("plans", ("view",)),
            ("billing", ("view",)),
            ("commercial_adjustments", ("view", "manage")),
            ("sales", ("view", "manage", "export")),
            ("bulk_operations", ("view", "manage", "export")),
            ("staff_reassignment", ("view", "manage")),
            (
                "platform_tasks",
                ("view", "create", "edit", "assign", "review", "complete", "comment"),
            ),
        ),
    ),
    "onboarding": PlatformRolePackage(
        key="onboarding",
        label="Onboarding",
        description="Implantação assistida, configuração inicial e ativação dos escritórios.",
        permissions=_permissions(
            ("dashboard", ("view",)),
            ("customers", ("view", "manage")),
            ("plans", ("view",)),
            ("onboarding", ("view", "manage")),
            ("benefits", ("view", "grant")),
            ("referrals", ("manage",)),
            ("coupons", ("view",)),
            ("support", ("view",)),
            ("integrations", ("view",)),
            ("workspace_configuration", ("view", "manage")),
            ("agent_templates", ("view", "manage")),
            ("flow_templates", ("view", "manage")),
            ("experience_customization", ("view", "manage")),
            ("configuration_deployments", ("view",)),
            ("bulk_operations", ("view", "manage", "export")),
            (
                "platform_tasks",
                ("view", "create", "edit", "assign", "review", "complete", "comment"),
            ),
        ),
    ),
    "customer_success": PlatformRolePackage(
        key="customer_success",
        label="Customer Success",
        description="Saúde da carteira, adoção, retenção, expansão e prevenção de churn.",
        permissions=_permissions(
            ("dashboard", ("view",)),
            ("reports", ("view",)),
            ("finance", ("view",)),
            ("customers", ("view",)),
            ("plans", ("view",)),
            ("onboarding", ("view",)),
            ("success", ("view", "manage")),
            ("support", ("view",)),
            ("benefits", ("view", "grant")),
            ("referrals", ("manage",)),
            ("partners", ("manage",)),
            ("coupons", ("view",)),
            ("platform_tasks", ("view", "create", "edit", "complete", "comment")),
        ),
    ),
    "support": PlatformRolePackage(
        key="support",
        label="Suporte",
        description="Atendimento, diagnóstico e acesso temporário aprovado aos workspaces.",
        permissions=_permissions(
            ("dashboard", ("view",)),
            ("customers", ("view",)),
            ("support", ("view", "respond", "triage", "assign", "export")),
            ("support_access", ("view", "request")),
            ("errors", ("view",)),
            ("benefits", ("view", "grant")),
            ("coupons", ("view",)),
            ("workspace_configuration", ("view", "manage")),
            ("agent_templates", ("view", "manage")),
            ("flow_templates", ("view", "manage")),
            ("experience_customization", ("view", "manage")),
            ("configuration_deployments", ("view",)),
            ("platform_tasks", ("view", "create", "edit", "complete", "comment")),
        ),
    ),
    "technical_support": PlatformRolePackage(
        key="technical_support",
        label="Suporte técnico",
        description=(
            "Investiga integrações, falhas e infraestrutura com acesso temporário governado."
        ),
        permissions=_permissions(
            ("dashboard", ("view",)),
            ("customers", ("view",)),
            (
                "support",
                ("view", "respond", "triage", "assign", "manage", "export"),
            ),
            ("support_access", ("view", "request")),
            ("integrations", ("view",)),
            ("errors", ("view", "manage")),
            ("logs", ("view",)),
            ("infrastructure", ("view",)),
            ("workspace_configuration", ("view", "manage", "approve")),
            ("agent_templates", ("view", "manage", "approve")),
            ("flow_templates", ("view", "manage", "approve")),
            ("experience_customization", ("view", "manage")),
            ("configuration_deployments", ("view", "manage", "rollback")),
            ("bulk_operations", ("view", "manage", "export")),
            ("staff_reassignment", ("view", "manage")),
            ("benefits", ("view", "grant")),
            ("coupons", ("view",)),
            (
                "platform_tasks",
                ("view", "create", "edit", "assign", "review", "complete", "comment"),
            ),
        ),
    ),
    "finance_billing": PlatformRolePackage(
        key="finance_billing",
        label="Financeiro e cobrança",
        description=(
            "Assinaturas, faturamento, inadimplência, conciliação e indicadores financeiros."
        ),
        permissions=_permissions(
            ("dashboard", ("view",)),
            ("reports", ("view", "export")),
            ("finance", ("view", "manage", "export")),
            ("customers", ("view", "export")),
            ("plans", ("view",)),
            ("billing", ("view", "manage", "export")),
            ("commercial_contracts", ("view", "manage")),
            ("commercial_adjustments", ("view", "manage")),
            ("entitlement_bonuses", ("view", "manage")),
            ("audit", ("view",)),
            ("benefits", ("view", "grant", "approve", "reverse")),
            ("referrals", ("manage",)),
            ("partners", ("manage",)),
            ("coupons", ("view", "manage", "export")),
            ("bulk_operations", ("view", "manage", "export")),
            ("platform_tasks", ("view", "create", "edit", "complete", "comment")),
        ),
    ),
    "marketing_content": PlatformRolePackage(
        key="marketing_content",
        label="Marketing e conteúdo",
        description="Site, blog, mídia, SEO, campanhas e mensuração de aquisição.",
        permissions=_permissions(
            ("dashboard", ("view",)),
            ("reports", ("view", "export")),
            ("content", ("view", "manage", "publish")),
            ("marketing", ("view", "manage", "export")),
            ("community", ("view",)),
            ("whatsapp", ("view",)),
            ("integrations", ("view",)),
            ("platform_tasks", ("view", "create", "edit", "review", "complete", "comment")),
        ),
    ),
    "community_manager": PlatformRolePackage(
        key="community_manager",
        label="Gestão de comunidade",
        description="Comunidade, comunicação recorrente, eventos e relacionamento com usuários.",
        permissions=_permissions(
            ("dashboard", ("view",)),
            ("content", ("view",)),
            ("marketing", ("view",)),
            ("community", ("view", "manage")),
            ("announcements", ("view", "manage")),
            ("whatsapp", ("view",)),
            ("platform_tasks", ("view", "create", "edit", "complete", "comment")),
        ),
    ),
    "compliance_security": PlatformRolePackage(
        key="compliance_security",
        label="Compliance e segurança",
        description=(
            "Controles, evidências, auditoria, privacidade, risco e segurança da plataforma."
        ),
        permissions=_permissions(
            ("dashboard", ("view",)),
            ("customers", ("view", "export")),
            ("support_access", ("view",)),
            ("errors", ("view",)),
            ("logs", ("view", "export")),
            ("audit", ("view", "export")),
            ("compliance", ("view", "manage", "export")),
            ("security", ("view", "manage")),
            ("data_governance", ("view", "manage", "export")),
            ("platform_tasks", ("view", "create", "edit", "review", "complete", "comment")),
        ),
    ),
    "product_operations": PlatformRolePackage(
        key="product_operations",
        label="Produto e operações",
        description="Uso do produto, qualidade operacional, releases e evolução controlada.",
        permissions=_permissions(
            ("dashboard", ("view",)),
            ("finance", ("view",)),
            ("customers", ("view", "export")),
            ("plans", ("view",)),
            ("success", ("view",)),
            ("support", ("view",)),
            ("ai_operations", ("view",)),
            ("errors", ("view",)),
            ("logs", ("view",)),
            ("releases", ("view",)),
            ("feature_flags", ("view", "manage")),
            ("workspace_configuration", ("view", "manage", "approve")),
            ("agent_templates", ("view", "manage", "approve")),
            ("flow_templates", ("view", "manage", "approve")),
            ("experience_customization", ("view", "manage")),
            ("configuration_deployments", ("view", "manage")),
            ("benefits", ("view",)),
            ("coupons", ("view",)),
            (
                "platform_tasks",
                ("view", "create", "edit", "assign", "review", "complete", "comment"),
            ),
        ),
    ),
    "engineering": PlatformRolePackage(
        key="engineering",
        label="Engenharia / TI",
        description="Erros, logs, integrações, releases, infraestrutura e operação técnica.",
        permissions=_permissions(
            ("dashboard", ("view",)),
            ("customers", ("view",)),
            ("support", ("view", "respond", "triage", "assign", "export")),
            ("support_access", ("view", "request")),
            ("integrations", ("view", "manage")),
            ("ai_operations", ("view", "manage")),
            ("errors", ("view", "manage")),
            ("logs", ("view", "export")),
            ("audit", ("view",)),
            ("security", ("view",)),
            ("releases", ("view", "manage")),
            ("feature_flags", ("view", "manage")),
            ("infrastructure", ("view", "manage")),
            ("workspace_configuration", ("view", "manage", "approve")),
            ("agent_templates", ("view", "manage", "approve")),
            ("flow_templates", ("view", "manage", "approve")),
            ("experience_customization", ("view", "manage")),
            ("configuration_deployments", ("view", "manage", "rollback")),
            ("staff_reassignment", ("view", "manage")),
            (
                "platform_tasks",
                ("view", "create", "edit", "assign", "review", "complete", "comment"),
            ),
        ),
    ),
}


def is_platform_root(user: Any) -> bool:
    """Único predicado de autoridade irrestrita da plataforma.

    O booleano legado isolado não concede bypass. A dupla marcação impede que
    um pacote comum, grant customizado ou registro legado malformado reproduza
    a autoridade do titular.
    """
    return bool(
        user
        and getattr(user, "is_active", False)
        and getattr(user, "deleted_at", None) is None
        and getattr(user, "is_internal_staff", False)
        and getattr(user, "platform_access_active", False)
        and getattr(user, "is_platform_admin", False)
        and getattr(user, "platform_role_package", None) == "platform_owner"
    )


def platform_permission_key(
    permission: PlatformPermission | dict[str, Any] | Any,
) -> tuple[str, str]:
    if isinstance(permission, PlatformPermission):
        return permission.module, permission.action
    if isinstance(permission, dict):
        return str(permission.get("module", "")), str(permission.get("action", ""))
    return str(getattr(permission, "module", "")), str(getattr(permission, "action", ""))


def normalize_platform_permissions(
    rows: Iterable[PlatformPermission | dict[str, Any] | Any] | None,
) -> tuple[PlatformPermission, ...]:
    known = {platform_permission_key(item) for item in PLATFORM_PERMISSION_CATALOG}
    normalized: dict[tuple[str, str], PlatformPermission] = {}
    for row in rows or []:
        module, action = platform_permission_key(row)
        key = (module.strip(), action.strip())
        if key in known:
            normalized[key] = PlatformPermission(*key)
    return tuple(normalized.values())


def effective_platform_permissions(user: Any) -> tuple[PlatformPermission, ...]:
    if (
        not user
        or not getattr(user, "is_active", False)
        or getattr(user, "deleted_at", None) is not None
    ):
        return ()
    if is_platform_root(user):
        return PLATFORM_PERMISSION_CATALOG
    if not getattr(user, "is_internal_staff", False) or not getattr(
        user, "platform_access_active", False
    ):
        return ()
    package = PLATFORM_ROLE_PACKAGES.get(str(getattr(user, "platform_role_package", "") or ""))
    if package is None:
        return ()
    # `platform_owner` é uma identidade técnica, não um pacote delegável.
    # Um registro inconsistente não herda seu catálogo.
    if package.key == "platform_owner":
        return ()
    permissions = {platform_permission_key(item): item for item in package.permissions}
    for denied in normalize_platform_permissions(
        getattr(user, "platform_permission_denials", None)
    ):
        permissions.pop(platform_permission_key(denied), None)
    for granted in normalize_platform_permissions(
        getattr(user, "platform_permission_grants", None)
    ):
        permissions[platform_permission_key(granted)] = granted
    return tuple(permissions.values())


def has_platform_permission(user: Any, module: str, action: str) -> bool:
    return (module, action) in {
        platform_permission_key(item) for item in effective_platform_permissions(user)
    }


def platform_permissions_as_dicts(user: Any) -> list[dict[str, str]]:
    return [
        {"module": item.module, "action": item.action}
        for item in effective_platform_permissions(user)
    ]
