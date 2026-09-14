"""Catálogo tipado e autorizado da Central de Comandos do Blackbox.

O catálogo descreve ações conhecidas. Ele não executa endpoints arbitrários e
não substitui a autorização final de cada rota de domínio.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.platform_rbac import has_platform_permission, is_platform_root
from app.models.user import User
from app.schemas.platform_search import (
    PlatformCommandCatalogResponse,
    PlatformCommandDefinition,
    PlatformCommandKind,
    PlatformCommandRisk,
    PlatformSearchEntityType,
)


@dataclass(frozen=True)
class _Command:
    id: str
    label: str
    description: str
    kind: PlatformCommandKind
    scope: str
    module: str
    action: str
    href: str | None = None
    entity_types: tuple[PlatformSearchEntityType, ...] = ()
    risk: PlatformCommandRisk = PlatformCommandRisk.none
    reason_required: bool = False
    confirmation_required: bool = False
    root_only: bool = False


_COMMANDS: tuple[_Command, ...] = (
    _Command(
        "tasks.open",
        "Abrir tarefas e aprovações",
        "Prioridades autorizadas da operação.",
        PlatformCommandKind.navigation,
        "global",
        "dashboard",
        "view",
        "/blackbox/tasks",
    ),
    _Command(
        "accounts.open",
        "Abrir contas",
        "Contas e Account 360.",
        PlatformCommandKind.navigation,
        "global",
        "customers",
        "view",
        "/blackbox/workspaces",
    ),
    _Command(
        "tickets.open",
        "Abrir tickets",
        "Fila e SLA do suporte.",
        PlatformCommandKind.navigation,
        "global",
        "support",
        "view",
        "/blackbox/support-tickets",
    ),
    _Command(
        "pipeline.open",
        "Abrir pipeline",
        "Leads e oportunidades comerciais.",
        PlatformCommandKind.navigation,
        "global",
        "sales",
        "view",
        "/blackbox/growth-crm",
    ),
    _Command(
        "staff.open",
        "Abrir equipe",
        "Funcionários, funções e sessões.",
        PlatformCommandKind.navigation,
        "global",
        "staff",
        "view",
        "/blackbox/staff",
    ),
    _Command(
        "incidents.open",
        "Abrir incidentes",
        "Incidentes e controles de segurança.",
        PlatformCommandKind.navigation,
        "global",
        "security",
        "view",
        "/blackbox/governance/security",
    ),
    _Command(
        "account.create",
        "Criar conta",
        "Iniciar cadastro administrativo de uma conta.",
        PlatformCommandKind.create,
        "global",
        "customers",
        "manage",
        "/blackbox/workspaces?create=1",
        risk=PlatformCommandRisk.low,
    ),
    _Command(
        "lead.create",
        "Criar lead",
        "Registrar um novo prospect no CRM.",
        PlatformCommandKind.create,
        "global",
        "sales",
        "manage",
        "/blackbox/growth-crm?create=1",
        risk=PlatformCommandRisk.low,
    ),
    _Command(
        "ticket.create",
        "Criar ticket",
        "Registrar um chamado interno de suporte.",
        PlatformCommandKind.create,
        "global",
        "support",
        "triage",
        "/blackbox/support-tickets?create=1",
        risk=PlatformCommandRisk.low,
    ),
    _Command(
        "staff.invite",
        "Convidar funcionário",
        "Abrir o convite de equipe.",
        PlatformCommandKind.create,
        "global",
        "staff",
        "manage",
        "/blackbox/staff?invite=1",
        risk=PlatformCommandRisk.high,
        confirmation_required=True,
    ),
    _Command(
        "incident.create",
        "Criar incidente",
        "Registrar e iniciar resposta a incidente.",
        PlatformCommandKind.create,
        "global",
        "security",
        "manage",
        "/blackbox/governance/security?create=1",
        risk=PlatformCommandRisk.high,
        reason_required=True,
        confirmation_required=True,
    ),
    _Command(
        "announcement.create",
        "Criar comunicado",
        "Abrir um novo comunicado oficial.",
        PlatformCommandKind.create,
        "global",
        "announcements",
        "manage",
        "/blackbox/announcements?create=1",
        risk=PlatformCommandRisk.low,
    ),
    _Command(
        "account.record.open",
        "Abrir Conta 360",
        "Consultar a conta selecionada.",
        PlatformCommandKind.navigation,
        "entity",
        "customers",
        "view",
        entity_types=(PlatformSearchEntityType.accounts,),
    ),
    _Command(
        "account.root_direct",
        "Entrar como Super Admin",
        "Abrir o acesso direto root com autoria preservada.",
        PlatformCommandKind.mutation,
        "entity",
        "customers",
        "manage",
        entity_types=(PlatformSearchEntityType.accounts,),
        risk=PlatformCommandRisk.critical,
        reason_required=True,
        confirmation_required=True,
        root_only=True,
    ),
    _Command(
        "user.record.open",
        "Abrir usuário",
        "Consultar o vínculo do usuário.",
        PlatformCommandKind.navigation,
        "entity",
        "customers",
        "view",
        entity_types=(PlatformSearchEntityType.users,),
    ),
    _Command(
        "staff.record.open",
        "Abrir funcionário",
        "Consultar autoridade e histórico.",
        PlatformCommandKind.navigation,
        "entity",
        "staff",
        "view",
        entity_types=(PlatformSearchEntityType.staff,),
    ),
    _Command(
        "ticket.record.open",
        "Abrir ticket",
        "Consultar diagnóstico e histórico.",
        PlatformCommandKind.navigation,
        "entity",
        "support",
        "view",
        entity_types=(PlatformSearchEntityType.tickets,),
    ),
    _Command(
        "ticket.assume",
        "Assumir ticket",
        "Atribuir o ticket a você.",
        PlatformCommandKind.mutation,
        "entity",
        "support",
        "assign",
        entity_types=(PlatformSearchEntityType.tickets,),
        risk=PlatformCommandRisk.low,
        confirmation_required=True,
    ),
    _Command(
        "lead.record.open",
        "Abrir lead",
        "Consultar o lead no pipeline.",
        PlatformCommandKind.navigation,
        "entity",
        "sales",
        "view",
        entity_types=(PlatformSearchEntityType.leads,),
    ),
    _Command(
        "lead.assume",
        "Assumir lead",
        "Atribuir o lead a você.",
        PlatformCommandKind.mutation,
        "entity",
        "sales",
        "manage",
        entity_types=(PlatformSearchEntityType.leads,),
        risk=PlatformCommandRisk.low,
        confirmation_required=True,
    ),
    _Command(
        "conversation.record.open",
        "Abrir conversa",
        "Consultar a conversa na Inbox.",
        PlatformCommandKind.navigation,
        "entity",
        "support",
        "view",
        entity_types=(PlatformSearchEntityType.conversations,),
    ),
    _Command(
        "conversation.assume",
        "Assumir conversa",
        "Atribuir a conversa a você.",
        PlatformCommandKind.mutation,
        "entity",
        "support",
        "respond",
        entity_types=(PlatformSearchEntityType.conversations,),
        risk=PlatformCommandRisk.low,
        confirmation_required=True,
    ),
    _Command(
        "incident.record.open",
        "Abrir incidente",
        "Consultar a resposta ao incidente.",
        PlatformCommandKind.navigation,
        "entity",
        "security",
        "view",
        entity_types=(PlatformSearchEntityType.incidents,),
    ),
    _Command(
        "incident.assume",
        "Assumir incidente",
        "Tornar-se responsável pela resposta.",
        PlatformCommandKind.mutation,
        "entity",
        "security",
        "manage",
        entity_types=(PlatformSearchEntityType.incidents,),
        risk=PlatformCommandRisk.high,
        reason_required=True,
        confirmation_required=True,
    ),
)


class PlatformCommandCatalogService:
    @staticmethod
    def _allowed(actor: User, command: _Command) -> bool:
        if command.root_only:
            return is_platform_root(actor)
        if command.id == "conversation.record.open":
            return has_platform_permission(actor, "support", "view") or has_platform_permission(
                actor, "sales", "view"
            )
        if command.id == "conversation.assume":
            return has_platform_permission(actor, "support", "respond") or has_platform_permission(
                actor, "sales", "manage"
            )
        return has_platform_permission(actor, command.module, command.action)

    @classmethod
    def catalog(cls, actor: User) -> PlatformCommandCatalogResponse:
        return PlatformCommandCatalogResponse(
            commands=[
                PlatformCommandDefinition(
                    id=command.id,
                    label=command.label,
                    description=command.description,
                    kind=command.kind,
                    scope=command.scope,
                    module=command.module,
                    action=command.action,
                    href=command.href,
                    entity_types=list(command.entity_types),
                    risk=command.risk,
                    reason_required=command.reason_required,
                    confirmation_required=command.confirmation_required,
                )
                for command in _COMMANDS
                if cls._allowed(actor, command)
            ]
        )

    @classmethod
    def allowed_ids(
        cls,
        actor: User,
        entity_type: PlatformSearchEntityType,
        *,
        queue: str | None = None,
    ) -> list[str]:
        allowed: list[str] = []
        for command in _COMMANDS:
            if entity_type not in command.entity_types or not cls._allowed(actor, command):
                continue
            if command.id.startswith("conversation."):
                if queue == "sales" and not has_platform_permission(actor, "sales", command.action):
                    if command.action == "view" and has_platform_permission(actor, "sales", "view"):
                        pass
                    elif command.action == "respond" and has_platform_permission(
                        actor, "sales", "manage"
                    ):
                        pass
                    else:
                        continue
                if queue == "support" and not has_platform_permission(
                    actor, "support", command.action
                ):
                    continue
            allowed.append(command.id)
        return allowed
