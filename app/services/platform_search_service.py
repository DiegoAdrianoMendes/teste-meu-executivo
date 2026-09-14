"""Busca global e lookups seguros sobre as tabelas autoritativas do Blackbox."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Awaitable, Callable, Iterable
from typing import Any
from uuid import UUID

from sqlalchemy import String, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.platform_rbac import has_platform_permission, is_platform_root
from app.models.error_log import ErrorLog
from app.models.growth_crm import GrowthProspect
from app.models.identity import WorkspaceInvitation
from app.models.platform_inbox import PlatformInboxConversation, PlatformInboxQueue
from app.models.platform_incident import PlatformIncident
from app.models.platform_organization import PlatformStaffInvitation
from app.models.platform_release import PlatformRelease
from app.models.subscription_commercial import WorkspaceSubscription
from app.models.support_access import SupportAccessGrant
from app.models.support_ticket import SupportTicket
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_membership import WorkspaceMembership
from app.schemas.platform_search import (
    PlatformLookupResponse,
    PlatformSearchEntityType,
    PlatformSearchGroup,
    PlatformSearchResponse,
    PlatformSearchResult,
)
from app.services.platform_command_catalog_service import PlatformCommandCatalogService


class PlatformLookupPermissionError(PermissionError):
    """O ator não pode sequer consultar o tipo pedido pelo seletor."""


class PlatformLookupCursorError(ValueError):
    """Cursor opaco inválido ou produzido por outra versão do contrato."""


SearchAdapter = Callable[..., Awaitable[list[PlatformSearchResult]]]


class PlatformSearchService:
    """Executa cada grupo isoladamente e nunca persiste a consulta digitada."""

    _DEFAULT_TYPES = (
        PlatformSearchEntityType.accounts,
        PlatformSearchEntityType.users,
        PlatformSearchEntityType.staff,
        PlatformSearchEntityType.tickets,
        PlatformSearchEntityType.leads,
        PlatformSearchEntityType.conversations,
        PlatformSearchEntityType.incidents,
    )

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _enum_value(value: Any) -> str | None:
        if value is None:
            return None
        return str(getattr(value, "value", value))

    @staticmethod
    def mask_email(value: str | None) -> str | None:
        if not value or "@" not in value:
            return None
        _local, domain = value.rsplit("@", 1)
        return f"***@{domain.lower()}"

    @staticmethod
    def _escaped_pattern(query: str) -> str:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    @classmethod
    def _match(cls, query: str, *columns: Any) -> Any:
        if not query:
            return None
        pattern = cls._escaped_pattern(query)
        return or_(*(cast(column, String).ilike(pattern, escape="\\") for column in columns))

    @staticmethod
    def _encode_cursor(offset: int) -> str:
        raw = f"platform-lookup:v1:{offset}".encode("ascii")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int:
        if cursor is None:
            return 0
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            raw = base64.urlsafe_b64decode(padded).decode("ascii")
            prefix, version, offset = raw.split(":", 2)
            if prefix != "platform-lookup" or version != "v1":
                raise ValueError
            parsed = int(offset)
            if parsed < 0:
                raise ValueError
            return parsed
        except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
            raise PlatformLookupCursorError("Cursor de lookup inválido.") from exc

    @staticmethod
    def _permission_allowed(
        actor: User,
        entity_type: PlatformSearchEntityType,
        *,
        purpose: str | None = None,
    ) -> bool:
        if entity_type in {
            PlatformSearchEntityType.accounts,
            PlatformSearchEntityType.users,
        }:
            if purpose == "announcement_audience":
                return has_platform_permission(actor, "announcements", "view")
            if entity_type == PlatformSearchEntityType.accounts and purpose in {
                "plan_policy_include",
                "plan_policy_exclude",
            }:
                return has_platform_permission(actor, "plans", "view")
            if entity_type == PlatformSearchEntityType.accounts and purpose == "audit_filter":
                return has_platform_permission(actor, "audit", "view")
            if entity_type == PlatformSearchEntityType.accounts and purpose == "benefit_grant":
                return has_platform_permission(actor, "benefits", "view")
            if (
                entity_type == PlatformSearchEntityType.accounts
                and purpose == "support_access_workspace"
            ):
                return has_platform_permission(
                    actor, "support_access", "view"
                ) or has_platform_permission(
                    actor, "support_access", "request"
                )
            if (
                entity_type == PlatformSearchEntityType.accounts
                and purpose == "ai_token_package_support"
            ):
                return has_platform_permission(actor, "support", "view")
            if entity_type == PlatformSearchEntityType.accounts and purpose in {
                "agent_billing_reconciliation",
                "ai_cost_policy",
            }:
                return is_platform_root(actor)
            return has_platform_permission(actor, "customers", "view")
        if entity_type == PlatformSearchEntityType.staff:
            if purpose == "platform_task_participant":
                return has_platform_permission(actor, "platform_tasks", "assign")
            if purpose == "platform_task_reference":
                # Resolve somente IDs que o operador já recebeu em uma tarefa
                # autorizada. A finalidade não permite pesquisar/distribuir
                # trabalho e, portanto, não substitui ``staff:view`` ou
                # ``platform_tasks:assign``.
                return has_platform_permission(actor, "platform_tasks", "view")
            if purpose in {"staff_reassignment_source", "staff_reassignment_target"}:
                return has_platform_permission(actor, "staff_reassignment", "manage")
            if purpose == "support_ticket_filter":
                return has_platform_permission(actor, "support", "view")
            if purpose == "growth_assignee":
                return has_platform_permission(actor, "sales", "manage")
            if purpose == "inbox_assignee":
                return has_platform_permission(
                    actor, "support", "respond"
                ) or has_platform_permission(
                    actor, "sales", "manage"
                )
            if purpose in {
                "bulk_assignee",
                "operational_owner",
                "reviewer",
                "support_assignee",
            }:
                return any(
                    has_platform_permission(actor, module, "manage")
                    for module in (
                        "sales",
                        "support",
                        "customers",
                        "onboarding",
                        "billing",
                        "announcements",
                        "workspace_configuration",
                    )
                )
            return has_platform_permission(actor, "staff", "view")
        if entity_type == PlatformSearchEntityType.tickets:
            if purpose in {"benefit_grant", "benefit_decision"}:
                return has_platform_permission(actor, "benefits", "view")
            if purpose in {"coupon_write", "coupon_status"}:
                return has_platform_permission(actor, "coupons", "view")
            return has_platform_permission(actor, "support", "view")
        if entity_type == PlatformSearchEntityType.leads:
            return has_platform_permission(actor, "sales", "view")
        if entity_type == PlatformSearchEntityType.conversations:
            return has_platform_permission(actor, "sales", "view") or has_platform_permission(
                actor, "support", "view"
            )
        if entity_type == PlatformSearchEntityType.incidents:
            return has_platform_permission(actor, "security", "view") or has_platform_permission(
                actor, "support", "view"
            )
        if entity_type in {
            PlatformSearchEntityType.errors,
            PlatformSearchEntityType.support_access,
        }:
            return has_platform_permission(actor, "support", "view")
        if entity_type == PlatformSearchEntityType.releases:
            if purpose == "support_evidence":
                return has_platform_permission(actor, "support", "view")
            return has_platform_permission(actor, "releases", "view")
        if entity_type == PlatformSearchEntityType.subscriptions:
            return has_platform_permission(actor, "billing", "view")
        if entity_type == PlatformSearchEntityType.workspace_invitations:
            return has_platform_permission(actor, "customers", "view")
        if entity_type == PlatformSearchEntityType.staff_invitations:
            return has_platform_permission(actor, "staff", "view")
        return False

    def _adapter(self, entity_type: PlatformSearchEntityType) -> SearchAdapter:
        return {
            PlatformSearchEntityType.accounts: self._search_accounts,
            PlatformSearchEntityType.users: self._search_users,
            PlatformSearchEntityType.staff: self._search_staff,
            PlatformSearchEntityType.tickets: self._search_tickets,
            PlatformSearchEntityType.leads: self._search_leads,
            PlatformSearchEntityType.conversations: self._search_conversations,
            PlatformSearchEntityType.incidents: self._search_incidents,
            PlatformSearchEntityType.errors: self._search_errors,
            PlatformSearchEntityType.support_access: self._search_support_access,
            PlatformSearchEntityType.releases: self._search_releases,
            PlatformSearchEntityType.subscriptions: self._search_subscriptions,
            PlatformSearchEntityType.workspace_invitations: self._search_workspace_invitations,
            PlatformSearchEntityType.staff_invitations: self._search_staff_invitations,
        }[entity_type]

    async def search(
        self,
        *,
        actor: User,
        query: str,
        types: Iterable[PlatformSearchEntityType] | None,
        limit_per_type: int,
        include_inactive: bool,
    ) -> PlatformSearchResponse:
        requested = list(types) if types is not None else list(self._DEFAULT_TYPES)
        groups: list[PlatformSearchGroup] = []
        for entity_type in requested:
            # Autorização acontece antes da chamada do adapter e, portanto, antes da query.
            if not self._permission_allowed(actor, entity_type):
                continue
            try:
                async with self.db.begin_nested():
                    items = await self._adapter(entity_type)(
                        actor=actor,
                        query=query,
                        limit=limit_per_type,
                        offset=0,
                        include_inactive=include_inactive,
                        workspace_id=None,
                        status_filter=None,
                        ids=None,
                        purpose=None,
                    )
            except Exception:  # grupo indisponível não derruba grupos saudáveis
                groups.append(
                    PlatformSearchGroup(
                        entity_type=entity_type,
                        state="unavailable",
                        error_code="PLATFORM-SEARCH-GROUP-UNAVAILABLE",
                    )
                )
                continue
            groups.append(PlatformSearchGroup(entity_type=entity_type, items=items))
        return PlatformSearchResponse(groups=groups)

    async def lookup(
        self,
        *,
        actor: User,
        entity_type: PlatformSearchEntityType,
        purpose: str,
        query: str,
        workspace_id: UUID | None,
        status_filter: str | None,
        cursor: str | None,
        limit: int,
    ) -> PlatformLookupResponse:
        # A finalidade pode reduzir o conjunto de campos e habilitar um lookup
        # mínimo entre domínios; ela nunca amplia a ação final do operador.
        if not self._permission_allowed(actor, entity_type, purpose=purpose):
            raise PlatformLookupPermissionError("Tipo de lookup não autorizado.")
        offset = self._decode_cursor(cursor)
        try:
            async with self.db.begin_nested():
                rows = await self._adapter(entity_type)(
                    actor=actor,
                    query=query,
                    limit=limit + 1,
                    offset=offset,
                    include_inactive=True,
                    workspace_id=workspace_id,
                    status_filter=status_filter,
                    ids=None,
                    purpose=purpose,
                )
        except Exception:
            return PlatformLookupResponse(
                entity_type=entity_type,
                state="unavailable",
                error_code="PLATFORM-LOOKUP-UNAVAILABLE",
            )
        has_more = len(rows) > limit
        return PlatformLookupResponse(
            entity_type=entity_type,
            items=rows[:limit],
            next_cursor=self._encode_cursor(offset + limit) if has_more else None,
        )

    async def resolve(
        self,
        *,
        actor: User,
        entity_type: PlatformSearchEntityType,
        purpose: str,
        ids: list[UUID],
        workspace_id: UUID | None,
    ) -> PlatformLookupResponse:
        if not self._permission_allowed(actor, entity_type, purpose=purpose):
            raise PlatformLookupPermissionError("Tipo de lookup não autorizado.")
        try:
            async with self.db.begin_nested():
                rows = await self._adapter(entity_type)(
                    actor=actor,
                    query="",
                    limit=len(ids),
                    offset=0,
                    include_inactive=True,
                    workspace_id=workspace_id,
                    status_filter=None,
                    ids=set(ids),
                    purpose=purpose,
                )
        except Exception:
            return PlatformLookupResponse(
                entity_type=entity_type,
                state="unavailable",
                error_code="PLATFORM-LOOKUP-UNAVAILABLE",
            )
        by_id = {item.id: item for item in rows}
        return PlatformLookupResponse(
            entity_type=entity_type,
            items=[by_id[item_id] for item_id in ids if item_id in by_id],
        )

    @staticmethod
    def _apply_common_filters(
        stmt: Any,
        *,
        model: Any,
        query: str,
        columns: tuple[Any, ...],
        ids: set[UUID] | None,
        status_column: Any | None,
        status_filter: str | None,
    ) -> Any:
        if ids is not None:
            stmt = stmt.where(model.id.in_(ids))
        elif query:
            match = PlatformSearchService._match(query, model.id, *columns)
            stmt = stmt.where(match)
        if status_filter and status_column is not None:
            stmt = stmt.where(status_column == status_filter)
        return stmt

    async def _search_accounts(self, **kwargs: Any) -> list[PlatformSearchResult]:
        query = kwargs["query"]
        stmt = select(Workspace)
        stmt = self._apply_common_filters(
            stmt,
            model=Workspace,
            query=query,
            columns=(Workspace.name, Workspace.slug, Workspace.legal_name),
            ids=kwargs["ids"],
            status_column=Workspace.status,
            status_filter=kwargs["status_filter"],
        )
        if not kwargs["include_inactive"]:
            stmt = stmt.where(Workspace.deleted_at.is_(None), Workspace.is_active.is_(True))
        if kwargs["workspace_id"] is not None:
            stmt = stmt.where(Workspace.id == kwargs["workspace_id"])
        rows = (
            await self.db.scalars(
                stmt.order_by(Workspace.updated_at.desc(), Workspace.id.asc())
                .offset(kwargs["offset"])
                .limit(kwargs["limit"])
            )
        ).all()
        return [
            PlatformSearchResult(
                id=row.id,
                entity_type=PlatformSearchEntityType.accounts,
                label=row.name,
                secondary_label=row.legal_name,
                public_identifier=row.slug,
                workspace_id=row.id,
                workspace_label=row.name,
                status=self._enum_value(row.status),
                badges=[self._enum_value(row.setup_mode) or "self_service"],
                metadata={"onboardingCompleted": bool(row.onboarding_completed)},
                allowed_commands=PlatformCommandCatalogService.allowed_ids(
                    kwargs["actor"], PlatformSearchEntityType.accounts
                ),
                href=f"/blackbox/accounts?workspaceId={row.id}",
            )
            for row in rows
        ]

    async def _search_users(self, **kwargs: Any) -> list[PlatformSearchResult]:
        stmt = select(User).where(User.is_internal_staff.is_(False))
        if kwargs["workspace_id"] is not None:
            stmt = stmt.join(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == kwargs["workspace_id"]
            )
        stmt = self._apply_common_filters(
            stmt,
            model=User,
            query=kwargs["query"],
            columns=(User.name, User.email),
            ids=kwargs["ids"],
            status_column=None,
            status_filter=kwargs["status_filter"],
        )
        if not kwargs["include_inactive"]:
            stmt = stmt.where(User.deleted_at.is_(None), User.is_active.is_(True))
        rows = (
            await self.db.scalars(
                stmt.distinct()
                .order_by(User.updated_at.desc(), User.id.asc())
                .offset(kwargs["offset"])
                .limit(kwargs["limit"])
            )
        ).all()
        workspace_id = kwargs["workspace_id"]
        return [
            PlatformSearchResult(
                id=row.id,
                entity_type=PlatformSearchEntityType.users,
                label=row.name,
                secondary_label=self.mask_email(row.email),
                workspace_id=workspace_id,
                status="active" if row.is_active and row.deleted_at is None else "inactive",
                masked_email=self.mask_email(row.email),
                badges=[self._enum_value(row.role) or "user"],
                allowed_commands=PlatformCommandCatalogService.allowed_ids(
                    kwargs["actor"], PlatformSearchEntityType.users
                ),
                href=f"/blackbox/accounts?userId={row.id}",
            )
            for row in rows
        ]

    async def _search_staff(self, **kwargs: Any) -> list[PlatformSearchResult]:
        del kwargs["workspace_id"]
        stmt = select(User).where(User.is_internal_staff.is_(True))
        stmt = self._apply_common_filters(
            stmt,
            model=User,
            query=kwargs["query"],
            columns=(User.name, User.email, User.platform_role_package),
            ids=kwargs["ids"],
            status_column=None,
            status_filter=kwargs["status_filter"],
        )
        active_staff_purposes = {
            "bulk_assignee",
            "operational_owner",
            "reviewer",
            "team_manager",
            "team_member",
            "growth_assignee",
            "inbox_assignee",
            "support_assignee",
            "support_ticket_filter",
            "staff_reassignment_target",
            "platform_task_participant",
            "platform_task_reference",
        }
        if not kwargs["include_inactive"] or kwargs.get("purpose") in active_staff_purposes:
            stmt = stmt.where(
                User.deleted_at.is_(None),
                User.is_active.is_(True),
                User.platform_access_active.is_(True),
            )
        if kwargs.get("purpose") in {
            "team_manager",
            "team_member",
            "staff_reassignment_source",
            "staff_reassignment_target",
        }:
            stmt = stmt.where(
                ~(
                    User.is_platform_admin.is_(True)
                    & (User.platform_role_package == "platform_owner")
                )
            )
        ordered_stmt = stmt.order_by(User.updated_at.desc(), User.id.asc())
        eligibility_purpose = kwargs.get("purpose")

        def is_eligible_assignee(user: User) -> bool:
            if eligibility_purpose == "growth_assignee":
                return has_platform_permission(user, "sales", "manage")
            if eligibility_purpose == "inbox_assignee":
                return has_platform_permission(
                    user, "support", "respond"
                ) or has_platform_permission(user, "sales", "manage")
            return True

        if eligibility_purpose not in {"growth_assignee", "inbox_assignee"}:
            rows = (
                await self.db.scalars(
                    ordered_stmt.offset(kwargs["offset"]).limit(kwargs["limit"])
                )
            ).all()
        else:
            rows = []
            eligible_offset = 0
            stream = await self.db.stream_scalars(ordered_stmt)
            try:
                async for row in stream:
                    if not is_eligible_assignee(row):
                        continue
                    if eligible_offset < kwargs["offset"]:
                        eligible_offset += 1
                        continue
                    rows.append(row)
                    if len(rows) >= kwargs["limit"]:
                        break
            finally:
                await stream.close()
        return [
            PlatformSearchResult(
                id=row.id,
                entity_type=PlatformSearchEntityType.staff,
                label=row.name,
                secondary_label=self.mask_email(row.email),
                status="active" if row.is_active and row.platform_access_active else "inactive",
                masked_email=self.mask_email(row.email),
                badges=[
                    row.platform_role_package
                    or ("platform_owner" if is_platform_root(row) else "staff")
                ],
                metadata={"rolePackage": row.platform_role_package},
                allowed_commands=PlatformCommandCatalogService.allowed_ids(
                    kwargs["actor"], PlatformSearchEntityType.staff
                ),
                href=f"/blackbox/staff?staffId={row.id}",
            )
            for row in rows
        ]

    async def _search_tickets(self, **kwargs: Any) -> list[PlatformSearchResult]:
        stmt = select(SupportTicket, Workspace).join(
            Workspace,
            Workspace.id == SupportTicket.workspace_id,
        )
        stmt = self._apply_common_filters(
            stmt,
            model=SupportTicket,
            query=kwargs["query"],
            columns=(SupportTicket.public_number, SupportTicket.subject, Workspace.name),
            ids=kwargs["ids"],
            status_column=SupportTicket.status,
            status_filter=kwargs["status_filter"],
        )
        if kwargs["workspace_id"] is not None:
            stmt = stmt.where(SupportTicket.workspace_id == kwargs["workspace_id"])
        rows = (
            await self.db.execute(
                stmt.order_by(SupportTicket.updated_at.desc(), SupportTicket.id.asc())
                .offset(kwargs["offset"])
                .limit(kwargs["limit"])
            )
        ).all()
        return [
            PlatformSearchResult(
                id=ticket.id,
                entity_type=PlatformSearchEntityType.tickets,
                label=ticket.subject,
                secondary_label=workspace.name,
                public_identifier=ticket.public_number,
                workspace_id=workspace.id,
                workspace_label=workspace.name,
                status=self._enum_value(ticket.status),
                badges=[
                    self._enum_value(ticket.queue) or "support",
                    self._enum_value(ticket.severity) or "medium",
                ],
                metadata={
                    "priority": self._enum_value(ticket.priority),
                    "version": ticket.version,
                    "assignedToUserId": str(ticket.assigned_to_user_id)
                    if ticket.assigned_to_user_id
                    else None,
                },
                allowed_commands=PlatformCommandCatalogService.allowed_ids(
                    kwargs["actor"], PlatformSearchEntityType.tickets
                ),
                href=f"/blackbox/support?ticketId={ticket.id}",
            )
            for ticket, workspace in rows
        ]

    async def _search_leads(self, **kwargs: Any) -> list[PlatformSearchResult]:
        stmt = select(GrowthProspect, Workspace).outerjoin(
            Workspace,
            Workspace.id == GrowthProspect.workspace_id,
        )
        stmt = self._apply_common_filters(
            stmt,
            model=GrowthProspect,
            query=kwargs["query"],
            columns=(
                GrowthProspect.nome_escritorio,
                GrowthProspect.contato_nome,
                GrowthProspect.contato_email,
                GrowthProspect.cidade,
            ),
            ids=kwargs["ids"],
            status_column=GrowthProspect.estagio,
            status_filter=kwargs["status_filter"],
        )
        if kwargs["workspace_id"] is not None:
            stmt = stmt.where(GrowthProspect.workspace_id == kwargs["workspace_id"])
        rows = (
            await self.db.execute(
                stmt.order_by(GrowthProspect.updated_at.desc(), GrowthProspect.id.asc())
                .offset(kwargs["offset"])
                .limit(kwargs["limit"])
            )
        ).all()
        return [
            PlatformSearchResult(
                id=lead.id,
                entity_type=PlatformSearchEntityType.leads,
                label=lead.nome_escritorio,
                secondary_label=lead.contato_nome or lead.cidade,
                workspace_id=workspace.id if workspace else None,
                workspace_label=workspace.name if workspace else None,
                status=self._enum_value(lead.estagio),
                masked_email=self.mask_email(lead.contato_email),
                badges=[self._enum_value(lead.origem) or "outro"],
                metadata={
                    "ownerAssigned": lead.owner_user_id is not None,
                    "ownerUserId": str(lead.owner_user_id) if lead.owner_user_id else None,
                },
                allowed_commands=PlatformCommandCatalogService.allowed_ids(
                    kwargs["actor"], PlatformSearchEntityType.leads
                ),
                href=f"/blackbox/growth?leadId={lead.id}",
            )
            for lead, workspace in rows
        ]

    async def _search_conversations(self, **kwargs: Any) -> list[PlatformSearchResult]:
        actor: User = kwargs["actor"]
        stmt = select(PlatformInboxConversation, Workspace).outerjoin(
            Workspace, Workspace.id == PlatformInboxConversation.workspace_id
        )
        can_sales = has_platform_permission(actor, "sales", "view")
        can_support = has_platform_permission(actor, "support", "view")
        if can_sales and not can_support:
            stmt = stmt.where(PlatformInboxConversation.queue == PlatformInboxQueue.sales)
        elif can_support and not can_sales:
            stmt = stmt.where(PlatformInboxConversation.queue == PlatformInboxQueue.support)
        stmt = self._apply_common_filters(
            stmt,
            model=PlatformInboxConversation,
            query=kwargs["query"],
            columns=(
                PlatformInboxConversation.contact_name,
                PlatformInboxConversation.subject,
                Workspace.name,
            ),
            ids=kwargs["ids"],
            status_column=PlatformInboxConversation.status,
            status_filter=kwargs["status_filter"],
        )
        if kwargs["workspace_id"] is not None:
            stmt = stmt.where(PlatformInboxConversation.workspace_id == kwargs["workspace_id"])
        rows = (
            await self.db.execute(
                stmt.order_by(
                    PlatformInboxConversation.last_message_at.desc(),
                    PlatformInboxConversation.id.asc(),
                )
                .offset(kwargs["offset"])
                .limit(kwargs["limit"])
            )
        ).all()
        return [
            PlatformSearchResult(
                id=conversation.id,
                entity_type=PlatformSearchEntityType.conversations,
                label=conversation.contact_name,
                secondary_label=conversation.subject,
                workspace_id=workspace.id if workspace else None,
                workspace_label=workspace.name if workspace else None,
                status=self._enum_value(conversation.status),
                badges=[
                    self._enum_value(conversation.queue) or "support",
                    self._enum_value(conversation.channel) or "webchat",
                ],
                metadata={
                    "unreadCount": conversation.unread_count,
                    "assignedToUserId": str(conversation.assigned_to_user_id)
                    if conversation.assigned_to_user_id
                    else None,
                },
                allowed_commands=PlatformCommandCatalogService.allowed_ids(
                    kwargs["actor"],
                    PlatformSearchEntityType.conversations,
                    queue=self._enum_value(conversation.queue),
                ),
                href=f"/blackbox/inbox?conversationId={conversation.id}",
            )
            for conversation, workspace in rows
        ]

    async def _search_incidents(self, **kwargs: Any) -> list[PlatformSearchResult]:
        del kwargs["workspace_id"]
        stmt = select(PlatformIncident)
        stmt = self._apply_common_filters(
            stmt,
            model=PlatformIncident,
            query=kwargs["query"],
            columns=(PlatformIncident.title,),
            ids=kwargs["ids"],
            status_column=PlatformIncident.status,
            status_filter=kwargs["status_filter"],
        )
        rows = (
            await self.db.scalars(
                stmt.order_by(PlatformIncident.updated_at.desc(), PlatformIncident.id.asc())
                .offset(kwargs["offset"])
                .limit(kwargs["limit"])
            )
        ).all()
        return [
            PlatformSearchResult(
                id=row.id,
                entity_type=PlatformSearchEntityType.incidents,
                label=row.title,
                status=row.status,
                badges=[row.severity, *[str(item) for item in (row.components or [])[:3]]],
                metadata={"startedAt": row.started_at.isoformat() if row.started_at else None},
                allowed_commands=PlatformCommandCatalogService.allowed_ids(
                    kwargs["actor"], PlatformSearchEntityType.incidents
                ),
                href=f"/blackbox/governance/security?incidentId={row.id}",
            )
            for row in rows
        ]

    async def _search_errors(self, **kwargs: Any) -> list[PlatformSearchResult]:
        stmt = select(ErrorLog)
        stmt = self._apply_common_filters(
            stmt,
            model=ErrorLog,
            query=kwargs["query"],
            columns=(ErrorLog.error_code, ErrorLog.exception_type, ErrorLog.message),
            ids=kwargs["ids"],
            status_column=ErrorLog.status,
            status_filter=kwargs["status_filter"],
        )
        if kwargs["workspace_id"] is not None:
            stmt = stmt.where(ErrorLog.workspace_id == kwargs["workspace_id"])
        rows = (
            await self.db.scalars(
                stmt.order_by(ErrorLog.last_seen_at.desc(), ErrorLog.id.asc())
                .offset(kwargs["offset"])
                .limit(kwargs["limit"])
            )
        ).all()
        return [
            PlatformSearchResult(
                id=row.id,
                entity_type=PlatformSearchEntityType.errors,
                label=row.error_code,
                secondary_label=row.exception_type,
                public_identifier=row.error_code,
                workspace_id=row.workspace_id,
                status=self._enum_value(row.status),
                badges=[self._enum_value(row.severity) or "medium"],
                metadata={"occurrences": row.occurrences},
                href=f"/blackbox/errors?errorId={row.id}",
            )
            for row in rows
        ]

    async def _search_support_access(self, **kwargs: Any) -> list[PlatformSearchResult]:
        stmt = select(SupportAccessGrant, Workspace).join(
            Workspace, Workspace.id == SupportAccessGrant.workspace_id
        )
        stmt = self._apply_common_filters(
            stmt,
            model=SupportAccessGrant,
            query=kwargs["query"],
            columns=(SupportAccessGrant.ticket_reference, Workspace.name),
            ids=kwargs["ids"],
            status_column=SupportAccessGrant.state,
            status_filter=kwargs["status_filter"],
        )
        if kwargs["workspace_id"] is not None:
            stmt = stmt.where(SupportAccessGrant.workspace_id == kwargs["workspace_id"])
        rows = (
            await self.db.execute(
                stmt.order_by(SupportAccessGrant.requested_at.desc(), SupportAccessGrant.id.asc())
                .offset(kwargs["offset"])
                .limit(kwargs["limit"])
            )
        ).all()
        return [
            PlatformSearchResult(
                id=grant.id,
                entity_type=PlatformSearchEntityType.support_access,
                label=f"Acesso {grant.ticket_reference}",
                secondary_label=workspace.name,
                public_identifier=grant.ticket_reference,
                workspace_id=workspace.id,
                workspace_label=workspace.name,
                status=self._enum_value(grant.state),
                badges=[f"{grant.requested_duration_minutes} min"],
                href=f"/blackbox/support-access?requestId={grant.id}",
            )
            for grant, workspace in rows
        ]

    async def _search_releases(self, **kwargs: Any) -> list[PlatformSearchResult]:
        del kwargs["workspace_id"]
        stmt = select(PlatformRelease)
        stmt = self._apply_common_filters(
            stmt,
            model=PlatformRelease,
            query=kwargs["query"],
            columns=(
                PlatformRelease.version,
                PlatformRelease.environment,
                PlatformRelease.commit_sha,
            ),
            ids=kwargs["ids"],
            status_column=PlatformRelease.status,
            status_filter=kwargs["status_filter"],
        )
        rows = (
            await self.db.scalars(
                stmt.order_by(
                    PlatformRelease.deployed_at.desc().nullslast(),
                    PlatformRelease.created_at.desc(),
                    PlatformRelease.id.asc(),
                )
                .offset(kwargs["offset"])
                .limit(kwargs["limit"])
            )
        ).all()
        return [
            PlatformSearchResult(
                id=row.id,
                entity_type=PlatformSearchEntityType.releases,
                label=row.version,
                secondary_label=row.environment,
                public_identifier=row.commit_sha[:12] if row.commit_sha else row.version,
                status=row.status,
                badges=[row.environment, row.source],
                metadata={"revision": row.revision},
                href=f"/blackbox/governance/releases?releaseId={row.id}",
            )
            for row in rows
        ]

    async def _search_subscriptions(self, **kwargs: Any) -> list[PlatformSearchResult]:
        stmt = select(WorkspaceSubscription, Workspace).join(
            Workspace, Workspace.id == WorkspaceSubscription.workspace_id
        )
        stmt = self._apply_common_filters(
            stmt,
            model=WorkspaceSubscription,
            query=kwargs["query"],
            columns=(Workspace.name, Workspace.slug, WorkspaceSubscription.plano),
            ids=kwargs["ids"],
            status_column=WorkspaceSubscription.status,
            status_filter=kwargs["status_filter"],
        )
        if kwargs["workspace_id"] is not None:
            stmt = stmt.where(WorkspaceSubscription.workspace_id == kwargs["workspace_id"])
        rows = (
            await self.db.execute(
                stmt.order_by(
                    WorkspaceSubscription.updated_at.desc(), WorkspaceSubscription.id.asc()
                )
                .offset(kwargs["offset"])
                .limit(kwargs["limit"])
            )
        ).all()
        return [
            PlatformSearchResult(
                id=subscription.id,
                entity_type=PlatformSearchEntityType.subscriptions,
                label=workspace.name,
                secondary_label=f"Plano {subscription.plano.value}",
                public_identifier=workspace.slug,
                workspace_id=workspace.id,
                workspace_label=workspace.name,
                status=subscription.status.value,
                badges=[subscription.plano.value],
                metadata={"trialEndsAt": subscription.trial_ends_at},
                href=f"/blackbox/billing?workspaceId={workspace.id}",
            )
            for subscription, workspace in rows
        ]

    async def _search_workspace_invitations(self, **kwargs: Any) -> list[PlatformSearchResult]:
        stmt = select(WorkspaceInvitation, Workspace).join(
            Workspace, Workspace.id == WorkspaceInvitation.workspace_id
        )
        stmt = self._apply_common_filters(
            stmt,
            model=WorkspaceInvitation,
            query=kwargs["query"],
            columns=(WorkspaceInvitation.invited_email, Workspace.name),
            ids=kwargs["ids"],
            status_column=WorkspaceInvitation.status,
            status_filter=kwargs["status_filter"],
        )
        if kwargs["workspace_id"] is not None:
            stmt = stmt.where(WorkspaceInvitation.workspace_id == kwargs["workspace_id"])
        rows = (
            await self.db.execute(
                stmt.order_by(WorkspaceInvitation.created_at.desc(), WorkspaceInvitation.id.asc())
                .offset(kwargs["offset"])
                .limit(kwargs["limit"])
            )
        ).all()
        return [
            PlatformSearchResult(
                id=invitation.id,
                entity_type=PlatformSearchEntityType.workspace_invitations,
                label=f"Convite para {workspace.name}",
                secondary_label=self.mask_email(invitation.invited_email),
                workspace_id=workspace.id,
                workspace_label=workspace.name,
                status=invitation.status.value,
                masked_email=self.mask_email(invitation.invited_email),
                badges=["convite de conta"],
                metadata={"expiresAt": invitation.expires_at},
                href=f"/blackbox/people?workspaceId={workspace.id}",
            )
            for invitation, workspace in rows
        ]

    async def _search_staff_invitations(self, **kwargs: Any) -> list[PlatformSearchResult]:
        del kwargs["workspace_id"]
        stmt = select(PlatformStaffInvitation)
        stmt = self._apply_common_filters(
            stmt,
            model=PlatformStaffInvitation,
            query=kwargs["query"],
            columns=(
                PlatformStaffInvitation.name,
                PlatformStaffInvitation.email,
                PlatformStaffInvitation.role_package,
            ),
            ids=kwargs["ids"],
            status_column=PlatformStaffInvitation.status,
            status_filter=kwargs["status_filter"],
        )
        rows = (
            await self.db.scalars(
                stmt.order_by(
                    PlatformStaffInvitation.created_at.desc(), PlatformStaffInvitation.id.asc()
                )
                .offset(kwargs["offset"])
                .limit(kwargs["limit"])
            )
        ).all()
        return [
            PlatformSearchResult(
                id=invitation.id,
                entity_type=PlatformSearchEntityType.staff_invitations,
                label=invitation.name,
                secondary_label=self.mask_email(invitation.email),
                status=invitation.status,
                masked_email=self.mask_email(invitation.email),
                badges=[invitation.role_package],
                metadata={"expiresAt": invitation.expires_at},
                href="/blackbox/team",
            )
            for invitation in rows
        ]
