"""
Catálogo simples de RBAC por workspace.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

from app.models.workspace_membership import WorkspaceRole

UNRESTRICTED_PERMISSION_SCOPES = frozenset({"workspace", "all"})

TASK_PERMISSION_ACTIONS = frozenset(
    {"view", "create", "edit", "assign", "review", "complete", "comment"}
)
_TASK_ACTIONS_FROM_KANBAN: dict[str, tuple[str, ...]] = {
    "view": ("view",),
    "create": ("create",),
    "edit": ("edit", "assign", "review", "complete", "comment"),
}


@dataclass(frozen=True)
class Permission:
    module: str
    action: str
    scope: str = "workspace"


@dataclass(frozen=True)
class RoleDefinition:
    id: WorkspaceRole
    label: str
    description: str
    permissions: List[Permission]


ROLE_DEFINITIONS: Dict[WorkspaceRole, RoleDefinition] = {
    WorkspaceRole.workspace_owner: RoleDefinition(
        id=WorkspaceRole.workspace_owner,
        label="Workspace Owner",
        description="Controle total do workspace, membros e configurações.",
        permissions=[
            Permission("dashboard", "view"),
            Permission("dashboard", "export"),
            Permission("kanban", "view"),
            Permission("kanban", "create"),
            Permission("kanban", "edit"),
            Permission("kanban", "delete"),
            Permission("lista", "view"),
            Permission("lista", "export"),
            Permission("mensagens", "view"),
            Permission("mensagens", "send_message"),
            Permission("relatorios", "view"),
            Permission("relatorios", "manage"),
            Permission("relatorios", "export"),
            Permission("aprovacoes", "view"),
            Permission("aprovacoes", "approve"),
            Permission("configuracoes", "view"),
            Permission("configuracoes", "edit"),
            Permission("admin", "view"),
            Permission("admin", "manage"),
            Permission("workspace", "manage"),
            Permission("users", "manage"),
            Permission("memberships", "manage"),
            Permission("roles", "view"),
            Permission("permissions", "view"),
            Permission("finance", "view"),
            Permission("finance", "manage"),
            Permission("finance", "export"),
            Permission("integrations", "view"),
            Permission("integrations", "manage"),
            Permission("automations", "view"),
            Permission("automations", "manage"),
            Permission("agents", "view"),
            Permission("agents", "manage"),
            Permission("executives", "view"),
            Permission("executives", "chat"),
            Permission("executives", "share"),
            Permission("executives", "reports"),
            Permission("notifications", "view"),
            Permission("notifications", "manage"),
            Permission("branding", "view"),
            Permission("branding", "manage"),
            Permission("security", "view"),
            Permission("audit", "view"),
            Permission("lead_data", "view"),
            Permission("lead_data", "edit"),
            # Módulo processual contratável à parte (add-on, gate real é o
            # entitlement `monitored_processes`; a permissão só decide quem
            # dentro do workspace pode operar a superfície).
            Permission("processos", "view"),
            Permission("processos", "manage"),
            # Demanda jurídica canônica (Onda 2 — /legal/demands).
            Permission("legal", "view"),
            Permission("legal", "manage"),
        ],
    ),
    WorkspaceRole.workspace_admin: RoleDefinition(
        id=WorkspaceRole.workspace_admin,
        label="Workspace Admin",
        description="Administra operação, usuários e preferências do workspace.",
        permissions=[
            Permission("dashboard", "view"),
            Permission("dashboard", "export"),
            Permission("kanban", "view"),
            Permission("kanban", "create"),
            Permission("kanban", "edit"),
            Permission("kanban", "delete"),
            Permission("lista", "view"),
            Permission("lista", "export"),
            Permission("mensagens", "view"),
            Permission("mensagens", "send_message"),
            Permission("relatorios", "view"),
            Permission("relatorios", "manage"),
            Permission("relatorios", "export"),
            Permission("aprovacoes", "view"),
            Permission("aprovacoes", "approve"),
            Permission("configuracoes", "view"),
            Permission("configuracoes", "edit"),
            Permission("admin", "view"),
            Permission("users", "manage"),
            Permission("memberships", "manage"),
            Permission("roles", "view"),
            Permission("permissions", "view"),
            Permission("finance", "view"),
            Permission("finance", "manage"),
            Permission("finance", "export"),
            Permission("integrations", "view"),
            Permission("integrations", "manage"),
            Permission("automations", "view"),
            Permission("automations", "manage"),
            Permission("agents", "view"),
            Permission("agents", "manage"),
            Permission("executives", "view"),
            Permission("executives", "chat"),
            Permission("executives", "share"),
            Permission("executives", "reports"),
            Permission("notifications", "view"),
            Permission("notifications", "manage"),
            Permission("branding", "view"),
            Permission("branding", "manage"),
            Permission("security", "view"),
            Permission("audit", "view"),
            Permission("lead_data", "view"),
            Permission("lead_data", "edit"),
            Permission("processos", "view"),
            Permission("processos", "manage"),
            Permission("legal", "view"),
            Permission("legal", "manage"),
        ],
    ),
    WorkspaceRole.legal_manager: RoleDefinition(
        id=WorkspaceRole.legal_manager,
        label="Legal Manager",
        description="Opera o jurídico e acompanha cards, aprovações e relatórios.",
        permissions=[
            Permission("dashboard", "view"),
            Permission("kanban", "view"),
            Permission("kanban", "edit"),
            Permission("lista", "view"),
            Permission("lista", "export"),
            Permission("mensagens", "view"),
            Permission("mensagens", "send_message"),
            Permission("relatorios", "view"),
            Permission("relatorios", "export"),
            Permission("aprovacoes", "view"),
            Permission("aprovacoes", "approve"),
            Permission("configuracoes", "view"),
            Permission("lead_data", "view"),
            Permission("lead_data", "edit"),
            Permission("processos", "view"),
            Permission("processos", "manage"),
            Permission("legal", "view"),
            Permission("legal", "manage"),
        ],
    ),
    WorkspaceRole.finance_manager: RoleDefinition(
        id=WorkspaceRole.finance_manager,
        label="Finance Manager",
        description=(
            "Controla cobrança, integrações financeiras, lembretes e visão de receita do "
            "workspace."
        ),
        permissions=[
            Permission("dashboard", "view"),
            Permission("kanban", "view"),
            Permission("lista", "view"),
            Permission("mensagens", "view"),
            Permission("relatorios", "view"),
            Permission("relatorios", "export"),
            Permission("configuracoes", "view"),
            Permission("finance", "view"),
            Permission("finance", "manage"),
            Permission("finance", "export"),
            Permission("integrations", "view"),
            Permission("integrations", "manage"),
            Permission("admin", "view"),
        ],
    ),
    WorkspaceRole.operator: RoleDefinition(
        id=WorkspaceRole.operator,
        label="Operator",
        description="Opera cards, tarefas e comunicações do workspace.",
        permissions=[
            Permission("dashboard", "view"),
            Permission("kanban", "view"),
            Permission("kanban", "create"),
            Permission("kanban", "edit"),
            Permission("lista", "view"),
            Permission("mensagens", "view"),
            Permission("mensagens", "send_message"),
            Permission("aprovacoes", "view"),
            Permission("processos", "view"),
            Permission("legal", "view"),
        ],
    ),
    WorkspaceRole.viewer: RoleDefinition(
        id=WorkspaceRole.viewer,
        label="Viewer",
        description="Acesso somente leitura para acompanhamento.",
        permissions=[
            Permission("dashboard", "view"),
            Permission("kanban", "view"),
            Permission("lista", "view"),
            Permission("mensagens", "view"),
            Permission("processos", "view"),
            Permission("legal", "view"),
        ],
    ),
}


def get_permissions_for_role(role: WorkspaceRole | None) -> List[Permission]:
    if role is None:
        return []
    return ROLE_DEFINITIONS[role].permissions


def permission_key(permission: Permission | dict[str, Any] | Any) -> tuple[str, str]:
    if isinstance(permission, Permission):
        return permission.module, permission.action
    if not isinstance(permission, dict):
        return str(getattr(permission, "module", "")), str(getattr(permission, "action", ""))
    return str(permission.get("module", "")), str(permission.get("action", ""))


def known_permission_keys() -> set[tuple[str, str]]:
    keys = {
        permission_key(permission)
        for definition in ROLE_DEFINITIONS.values()
        for permission in definition.permissions
    }
    keys.update(("tasks", action) for action in TASK_PERMISSION_ACTIONS)
    return keys


def normalize_permissions(
    rows: Iterable[Permission | dict[str, Any] | Any] | None,
) -> List[Permission]:
    normalized: dict[tuple[str, str], Permission] = {}
    for row in rows or []:
        if isinstance(row, Permission):
            permission = row
        elif isinstance(row, dict):
            module = str(row.get("module", "")).strip()
            action = str(row.get("action", "")).strip()
            scope = str(row.get("scope", "workspace")).strip() or "workspace"
            if not module or not action:
                continue
            permission = Permission(module=module, action=action, scope=scope)
        else:
            module = str(getattr(row, "module", "")).strip()
            action = str(getattr(row, "action", "")).strip()
            scope = str(getattr(row, "scope", "workspace")).strip() or "workspace"
            if not module or not action:
                continue
            permission = Permission(module=module, action=action, scope=scope)
        normalized[permission_key(permission)] = permission
    return list(normalized.values())


def get_effective_permissions(
    role: WorkspaceRole | None,
    grants: Iterable[Permission | dict[str, Any]] | None = None,
    denials: Iterable[Permission | dict[str, Any]] | None = None,
) -> List[Permission]:
    normalized_denials = normalize_permissions(denials)
    base = {
        permission_key(permission): permission
        for permission in get_permissions_for_role(role)
    }
    if role == WorkspaceRole.workspace_owner:
        # Owner mantém o pacote completo, mas bloqueios individuais persistidos
        # na própria membership valem também para ele (autolimitação auditável).
        for denied in normalized_denials:
            base.pop(permission_key(denied), None)
        return _with_derived_task_permissions(base, normalized_denials)

    for denied in normalized_denials:
        base.pop(permission_key(denied), None)
    for granted in normalize_permissions(grants):
        key = permission_key(granted)
        if granted.scope in UNRESTRICTED_PERMISSION_SCOPES:
            base[key] = granted
        else:
            # Legacy own/team grants never had row-level enforcement. Treat them
            # as a restriction instead of silently widening access workspace-wide.
            base.pop(key, None)
    if ("lead_data", "view") not in base:
        base.pop(("lead_data", "edit"), None)
    return _with_derived_task_permissions(base, normalized_denials)


def _with_derived_task_permissions(
    permissions: dict[tuple[str, str], Permission],
    denials: Iterable[Permission],
) -> List[Permission]:
    """Expõe o RBAC granular de tarefas sem ignorar restrições legadas do Kanban.

    A derivação mantém memberships existentes operacionais durante a migração.
    Grants/denials explícitos em ``tasks`` continuam tendo a palavra final.
    """

    for kanban_action, task_actions in _TASK_ACTIONS_FROM_KANBAN.items():
        source = permissions.get(("kanban", kanban_action))
        if source is None:
            continue
        for task_action in task_actions:
            permissions.setdefault(
                ("tasks", task_action),
                Permission(module="tasks", action=task_action, scope=source.scope),
            )
    for denied in denials:
        if denied.module == "tasks":
            permissions.pop(permission_key(denied), None)
    return list(permissions.values())


def has_effective_permission(
    role: WorkspaceRole | None,
    module: str,
    action: str,
    grants: Iterable[Permission | dict[str, Any]] | None = None,
    denials: Iterable[Permission | dict[str, Any]] | None = None,
) -> bool:
    return any(
        permission.module == module
        and permission.action == action
        and permission.scope in UNRESTRICTED_PERMISSION_SCOPES
        for permission in get_effective_permissions(role, grants, denials)
    )


def has_permission(role: WorkspaceRole | None, module: str, action: str) -> bool:
    return has_effective_permission(role, module, action)
