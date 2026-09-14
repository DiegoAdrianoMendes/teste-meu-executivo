"""State machine and request-time validation for local support break-glass access."""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException, Request, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.platform_rbac import has_platform_permission, is_platform_root
from app.core.security import create_support_access_token
from app.models.support_access import (
    SUPPORT_ACCESS_MAX_DURATION_MINUTES,
    SupportAccessEvent,
    SupportAccessGrant,
    SupportAccessScope,
    SupportAccessState,
)
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.schemas.support_access import (
    SupportAccessApproval,
    SupportAccessDecision,
    SupportAccessEventPageRead,
    SupportAccessRequestCreate,
    SupportAccessTokenRead,
)
from app.services.mfa_service import ADMIN_SESSION_TTL_SECONDS, LocalMfaService

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_SUPPORT_ROUTE_RESOURCES = (
    ("/api/v1/admin/workspaces", "workspace"),
    ("/api/v1/admin/users", "members"),
    ("/api/v1/admin/memberships", "members"),
    ("/api/v1/admin/team-members", "members"),
    ("/api/v1/admin/roles", "members"),
    ("/api/v1/workspaces", "workspace"),
    ("/api/v1/users", "members"),
    ("/api/v1/clients", "clients"),
    # Rotas de tarefa aninhadas em caso precisam vencer o prefixo genérico de
    # matters; o FastAPI expõe aqui o template, não o UUID resolvido.
    ("/api/v1/matters/{matter_id}/tasks", "tasks"),
    ("/api/v1/matters", "matters"),
    ("/api/v1/tasks", "tasks"),
    # Configuração de provider/calendário continua subordinada ao escopo de
    # configurações; compromissos e a agenda interna usam escopo próprio.
    ("/api/v1/agenda/booking-links", "configurations"),
    ("/api/v1/agenda/settings", "configurations"),
    ("/api/v1/meetings", "meetings"),
    ("/api/v1/agenda", "meetings"),
    ("/api/v1/documents", "documents"),
    ("/api/v1/finance", "finance"),
    ("/api/v1/reports", "reports"),
    # Agent Platform V3 is configuration of a tenant deployment.  The route
    # dependency separately requires the operator's platform AI RBAC, so an
    # approved configuration grant alone never creates agent authority.
    ("/api/v1/agent-platform", "configurations"),
    ("/api/v1/klm/areas", "configurations"),
    ("/api/v1/configurations", "configurations"),
)
_SUPPORT_BLOCKED_ROUTE_PARTS = (
    "/support-access",
    "/closure",
    "/download",
    "/export",
    "/setup",
    "/upload",
    "/optimize",
    "/api/v1/agenda/public/",
)
ROOT_DIRECT_STEP_UP_MAX_AGE_SECONDS = ADMIN_SESSION_TTL_SECONDS
AdministrativeSupportSessionType = Literal["root_direct", "support_approved"]


@dataclass(frozen=True)
class AuthorizedSupportAccess:
    grant: SupportAccessGrant
    administrative_session_id: UUID
    administrative_session_type: AdministrativeSupportSessionType


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _error(
    code: str,
    message: str,
    *,
    status_code: int = status.HTTP_403_FORBIDDEN,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def ensure_administrative_access_enabled() -> None:
    """Fail closed unless the isolated local mode or explicit AWS gate is active."""
    isolated_local_mode = (
        settings.is_development
        and settings.KLM_RUNTIME_MODE == "local_simulation"
        and not settings.KLM_EXTERNAL_ACTIONS_ENABLED
        and not settings.KLM_AWS_ENABLED
    )
    if not isolated_local_mode and not settings.KLM_ADMINISTRATIVE_ACCESS_ENABLED:
        raise _error(
            "SUPPORT-ADMINISTRATIVE-ACCESS-DISABLED",
            "As sessões administrativas estão desabilitadas neste ambiente.",
            status_code=status.HTTP_404_NOT_FOUND,
        )


# Compatibilidade interna temporária; novos consumidores devem usar o nome que
# descreve o contrato publicado em vez do antigo gate local-only.
ensure_local_support_access_enabled = ensure_administrative_access_enabled


def support_route_template(request: Request) -> str:
    route = request.scope.get("route")
    return str(getattr(route, "path", request.url.path))


def required_support_scope_for_request(request: Request) -> str | None:
    """Resolve a finite local-data scope without using path parameters or query data."""
    route_path = support_route_template(request)
    if any(part in route_path for part in _SUPPORT_BLOCKED_ROUTE_PARTS):
        return None
    resource = next(
        (
            candidate
            for prefix, candidate in _SUPPORT_ROUTE_RESOURCES
            if route_path == prefix or route_path.startswith(f"{prefix}/")
        ),
        None,
    )
    if resource is None:
        return None
    operation = "read" if request.method.upper() in _READ_METHODS else "write"
    candidate = f"{resource}:{operation}"
    return candidate if candidate in {scope.value for scope in SupportAccessScope} else None


class SupportAccessGrantService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.operation_was_replayed = False

    async def authorize_platform_workspace_operation(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        required_scope: SupportAccessScope,
        request: Request,
        administrative_session_type: AdministrativeSupportSessionType | None = None,
        administrative_session_id: UUID | None = None,
        require_root_direct: bool = False,
    ) -> SupportAccessGrant | None:
        """Exige grant ativo para a equipe do SaaS operar uma conta contratante.

        A emissão e a ativação break-glass continuam restritas ao runtime local.
        Um grant já ativo, porém, pode ser validado em qualquer runtime.
        """
        actor = await self.db.scalar(
            select(User).where(
                User.id == actor_user_id,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
        if actor is None:
            raise _error(
                "SUPPORT-PRINCIPAL-INACTIVE",
                "O funcionário do SaaS não está ativo.",
            )
        if is_platform_root(actor):
            # Root bypasses customer approval, never the explicit administrative
            # session boundary.  A control-plane read/diagnostic may remain in
            # the normal Blackbox session; a mutation inside a tenant requires
            # a fresh, MFA-backed ``root_direct`` context so attribution and
            # revocation remain precise in AWS.
            if require_root_direct and (
                administrative_session_type != "root_direct"
                or administrative_session_id is None
            ):
                raise _error(
                    "SUPPORT-ROOT-DIRECT-REQUIRED",
                    "Entre diretamente na conta com MFA antes de alterar dados privados dela.",
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )
            return None

        await self._require_active_workspace(workspace_id)
        await self._expire_matching(workspace_id=workspace_id)
        now = datetime.now(UTC)
        grants = list(
            (
                await self.db.scalars(
                    select(SupportAccessGrant)
                    .where(
                        SupportAccessGrant.workspace_id == workspace_id,
                        SupportAccessGrant.requester_user_id == actor_user_id,
                        SupportAccessGrant.state == SupportAccessState.active,
                        SupportAccessGrant.expires_at.is_not(None),
                        SupportAccessGrant.expires_at > now,
                    )
                    .order_by(
                        SupportAccessGrant.activated_at.desc(),
                        SupportAccessGrant.requested_at.desc(),
                    )
                )
            ).all()
        )
        if not grants:
            raise _error(
                "SUPPORT-WORKSPACE-GRANT-REQUIRED",
                "A operação exige acesso temporário ativo e aprovado para esta conta.",
            )

        scope_value = required_scope.value
        grant = next(
            (
                candidate
                for candidate in grants
                if scope_value in set(candidate.approved_scopes or [])
            ),
            None,
        )
        if grant is None:
            denied_grant = grants[0]
            self._access_event(
                denied_grant,
                actor_user_id,
                request,
                event_type="support_access_denied",
                required_scope=scope_value,
                reason_code="scope_denied",
            )
            await self.db.commit()
            raise _error(
                "SUPPORT-SCOPE-DENIED",
                "O acesso temporário não autoriza esta operação de configuração.",
            )

        self._access_event(
            grant,
            actor_user_id,
            request,
            event_type="support_access_used",
            required_scope=scope_value,
            reason_code=None,
        )
        await self.db.commit()
        return grant

    async def list_configuration_workspaces(
        self,
        *,
        actor_user_id: UUID,
    ) -> list[dict[str, object]]:
        """Lista somente contas que o ator pode configurar no Estúdio."""
        actor = await self.db.scalar(
            select(User).where(
                User.id == actor_user_id,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
        if actor is None:
            raise _error(
                "SUPPORT-PRINCIPAL-INACTIVE",
                "O funcionário do SaaS não está ativo.",
            )

        if is_platform_root(actor):
            workspaces = list(
                (
                    await self.db.scalars(
                        select(Workspace)
                        .where(
                            Workspace.is_active.is_(True),
                            Workspace.deleted_at.is_(None),
                        )
                        .order_by(Workspace.name.asc(), Workspace.id.asc())
                    )
                ).all()
            )
            return [
                {
                    "id": workspace.id,
                    "name": workspace.name,
                    "slug": workspace.slug,
                    "can_read": True,
                    "can_write": True,
                    "expires_at": None,
                    "read_expires_at": None,
                    "write_expires_at": None,
                }
                for workspace in workspaces
            ]

        await self._expire_matching(workspace_id=None)
        now = datetime.now(UTC)
        rows = (
            await self.db.execute(
                select(SupportAccessGrant, Workspace)
                .join(Workspace, Workspace.id == SupportAccessGrant.workspace_id)
                .where(
                    SupportAccessGrant.requester_user_id == actor_user_id,
                    SupportAccessGrant.state == SupportAccessState.active,
                    SupportAccessGrant.expires_at.is_not(None),
                    SupportAccessGrant.expires_at > now,
                    Workspace.is_active.is_(True),
                    Workspace.deleted_at.is_(None),
                )
                .order_by(
                    Workspace.name.asc(),
                    SupportAccessGrant.expires_at.desc(),
                )
            )
        ).all()

        by_workspace: dict[UUID, dict[str, object]] = {}
        for grant, workspace in rows:
            scopes = set(grant.approved_scopes or [])
            can_read = SupportAccessScope.configurations_read.value in scopes
            can_write = SupportAccessScope.configurations_write.value in scopes
            if not can_read and not can_write:
                continue
            current = by_workspace.setdefault(
                workspace.id,
                {
                    "id": workspace.id,
                    "name": workspace.name,
                    "slug": workspace.slug,
                    "can_read": False,
                    "can_write": False,
                    "expires_at": None,
                    "read_expires_at": None,
                    "write_expires_at": None,
                },
            )
            current["can_read"] = bool(current["can_read"]) or can_read
            current["can_write"] = bool(current["can_write"]) or can_write
            if can_read:
                read_expiry = current["read_expires_at"]
                if read_expiry is None or grant.expires_at > read_expiry:
                    current["read_expires_at"] = grant.expires_at
            if can_write:
                write_expiry = current["write_expires_at"]
                if write_expiry is None or grant.expires_at > write_expiry:
                    current["write_expires_at"] = grant.expires_at

        for current in by_workspace.values():
            expiries = [
                value
                for value in (
                    current["read_expires_at"],
                    current["write_expires_at"],
                )
                if isinstance(value, datetime)
            ]
            current["expires_at"] = min(expiries) if expiries else None
        return list(by_workspace.values())

    async def page_configuration_workspaces(
        self,
        *,
        actor_user_id: UUID,
        search: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, object]], int]:
        """Pagina contas autorizadas sem perder o escopo temporário do Estúdio."""
        actor = await self.db.scalar(
            select(User).where(
                User.id == actor_user_id,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
        if actor is None:
            raise _error(
                "SUPPORT-PRINCIPAL-INACTIVE",
                "O funcionário do SaaS não está ativo.",
            )

        normalized_search = (search or "").strip()
        if is_platform_root(actor):
            filters = [
                Workspace.is_active.is_(True),
                Workspace.deleted_at.is_(None),
            ]
            if normalized_search:
                pattern = f"%{normalized_search}%"
                filters.append(
                    or_(Workspace.name.ilike(pattern), Workspace.slug.ilike(pattern))
                )
            total = int(
                await self.db.scalar(
                    select(func.count(Workspace.id)).where(*filters)
                )
                or 0
            )
            workspaces = list(
                await self.db.scalars(
                    select(Workspace)
                    .where(*filters)
                    .order_by(Workspace.name.asc(), Workspace.id.asc())
                    .offset(offset)
                    .limit(limit)
                )
            )
            return [
                {
                    "id": workspace.id,
                    "name": workspace.name,
                    "slug": workspace.slug,
                    "can_read": True,
                    "can_write": True,
                    "expires_at": None,
                    "read_expires_at": None,
                    "write_expires_at": None,
                }
                for workspace in workspaces
            ], total

        authorized = await self.list_configuration_workspaces(
            actor_user_id=actor_user_id
        )
        if normalized_search:
            needle = normalized_search.casefold()
            authorized = [
                workspace
                for workspace in authorized
                if needle in str(workspace["name"]).casefold()
                or needle in str(workspace["slug"]).casefold()
            ]
        authorized.sort(
            key=lambda workspace: (
                str(workspace["name"]).casefold(),
                str(workspace["id"]),
            )
        )
        return authorized[offset : offset + limit], len(authorized)

    async def get_configuration_workspace(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
    ) -> dict[str, object] | None:
        """Resolve uma conta exata sem reler a coleção autorizada do Estúdio."""
        actor = await self.db.scalar(
            select(User).where(
                User.id == actor_user_id,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
        if actor is None:
            raise _error(
                "SUPPORT-PRINCIPAL-INACTIVE",
                "O funcionário do SaaS não está ativo.",
            )

        workspace = await self.db.scalar(
            select(Workspace).where(
                Workspace.id == workspace_id,
                Workspace.is_active.is_(True),
                Workspace.deleted_at.is_(None),
            )
        )
        if workspace is None:
            return None
        if is_platform_root(actor):
            return {
                "id": workspace.id,
                "name": workspace.name,
                "slug": workspace.slug,
                "can_read": True,
                "can_write": True,
                "expires_at": None,
                "read_expires_at": None,
                "write_expires_at": None,
            }

        now = datetime.now(UTC)
        grants = list(
            await self.db.scalars(
                select(SupportAccessGrant)
                .where(
                    SupportAccessGrant.workspace_id == workspace_id,
                    SupportAccessGrant.requester_user_id == actor_user_id,
                    SupportAccessGrant.state == SupportAccessState.active,
                    SupportAccessGrant.expires_at.is_not(None),
                    SupportAccessGrant.expires_at > now,
                )
                .order_by(SupportAccessGrant.expires_at.desc())
            )
        )
        read_expiry: datetime | None = None
        write_expiry: datetime | None = None
        for grant in grants:
            scopes = set(grant.approved_scopes or [])
            if SupportAccessScope.configurations_read.value in scopes:
                if read_expiry is None or grant.expires_at > read_expiry:
                    read_expiry = grant.expires_at
            if SupportAccessScope.configurations_write.value in scopes:
                if write_expiry is None or grant.expires_at > write_expiry:
                    write_expiry = grant.expires_at
        if read_expiry is None and write_expiry is None:
            return None
        expiries = [value for value in (read_expiry, write_expiry) if value is not None]
        return {
            "id": workspace.id,
            "name": workspace.name,
            "slug": workspace.slug,
            "can_read": read_expiry is not None,
            "can_write": write_expiry is not None,
            "expires_at": min(expiries),
            "read_expires_at": read_expiry,
            "write_expires_at": write_expiry,
        }

    async def request_access(
        self,
        requester_user_id: UUID,
        payload: SupportAccessRequestCreate,
    ) -> SupportAccessGrant:
        ensure_local_support_access_enabled()
        await self._require_platform_support_operator(requester_user_id)
        # Serializa solicitações do mesmo workspace no PostgreSQL para que a
        # idempotência não dependa de um savepoint que possa escapar do
        # rollback externo (especialmente no SQLite dos testes integrados).
        await self._require_active_workspace(payload.workspace_id, lock=True)
        scopes = self._scope_values(payload.requested_scopes)
        existing = await self._find_ticket_request(
            payload.workspace_id,
            requester_user_id,
            payload.ticket_reference,
        )
        if existing is not None:
            self._assert_idempotent_request(existing, payload, scopes)
            self.operation_was_replayed = True
            return existing

        now = datetime.now(UTC)
        grant = SupportAccessGrant(
            workspace_id=payload.workspace_id,
            requester_user_id=requester_user_id,
            reason=payload.reason,
            ticket_reference=payload.ticket_reference,
            requested_scopes=scopes,
            requested_duration_minutes=payload.duration_minutes,
            state=SupportAccessState.requested,
            requested_at=now,
        )
        self.db.add(grant)
        await self.db.flush()
        self._event(
            grant,
            event_type="support_access_requested",
            actor_user_id=requester_user_id,
            from_state=None,
            to_state=SupportAccessState.requested,
            details={
                "scopes": scopes,
                "duration_minutes": payload.duration_minutes,
            },
        )
        await self.db.flush()
        await self.db.refresh(grant)
        return grant

    async def list_for_platform(
        self,
        actor_user_id: UUID,
        *,
        workspace_id: UUID | None = None,
        state: SupportAccessState | None = None,
        limit: int = 100,
    ) -> list[SupportAccessGrant]:
        rows, _ = await self.page_for_platform(
            actor_user_id,
            workspace_id=workspace_id,
            state=state,
            limit=limit,
            offset=0,
        )
        return rows

    async def page_for_platform(
        self,
        actor_user_id: UUID,
        *,
        workspace_id: UUID | None = None,
        state: SupportAccessState | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[SupportAccessGrant], int]:
        ensure_local_support_access_enabled()
        await self._require_platform_support_operator(actor_user_id)
        await self._expire_matching(workspace_id=workspace_id)
        conditions = []
        if workspace_id is not None:
            conditions.append(SupportAccessGrant.workspace_id == workspace_id)
        if state is not None:
            conditions.append(SupportAccessGrant.state == state)
        total = int(await self.db.scalar(
            select(func.count(SupportAccessGrant.id)).where(*conditions)
        ) or 0)
        rows = list(
            (
                await self.db.scalars(
                    select(SupportAccessGrant)
                    .where(*conditions)
                    .order_by(SupportAccessGrant.requested_at.desc(), SupportAccessGrant.id.desc())
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        )
        return rows, total

    async def list_for_workspace(
        self,
        actor_user_id: UUID,
        workspace_id: UUID,
        *,
        state: SupportAccessState | None = None,
        limit: int = 100,
    ) -> list[SupportAccessGrant]:
        ensure_local_support_access_enabled()
        await self._require_workspace_approver(actor_user_id, workspace_id)
        await self._expire_matching(workspace_id=workspace_id)
        conditions = [SupportAccessGrant.workspace_id == workspace_id]
        if state is not None:
            conditions.append(SupportAccessGrant.state == state)
        return list(
            (
                await self.db.scalars(
                    select(SupportAccessGrant)
                    .where(*conditions)
                    .order_by(SupportAccessGrant.requested_at.desc())
                    .limit(limit)
                )
            ).all()
        )

    async def page_for_workspace(
        self,
        actor_user_id: UUID,
        workspace_id: UUID,
        *,
        state: SupportAccessState | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[SupportAccessGrant], int]:
        ensure_local_support_access_enabled()
        await self._require_workspace_approver(actor_user_id, workspace_id)
        await self._expire_matching(workspace_id=workspace_id)
        conditions = [SupportAccessGrant.workspace_id == workspace_id]
        if state is not None:
            conditions.append(SupportAccessGrant.state == state)
        total = int(
            (await self.db.scalar(select(func.count(SupportAccessGrant.id)).where(*conditions)))
            or 0
        )
        rows = list(
            (
                await self.db.scalars(
                    select(SupportAccessGrant)
                    .where(*conditions)
                    .order_by(
                        SupportAccessGrant.requested_at.desc(),
                        SupportAccessGrant.id.desc(),
                    )
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        )
        return rows, total

    async def approve(
        self,
        grant_id: UUID,
        workspace_id: UUID,
        approver_user_id: UUID,
        approval: SupportAccessApproval,
    ) -> SupportAccessGrant:
        ensure_local_support_access_enabled()
        await self._require_workspace_approver(approver_user_id, workspace_id)
        grant = await self._load_workspace_grant(grant_id, workspace_id, lock=True)
        if grant.requester_user_id == approver_user_id:
            raise _error(
                "SUPPORT-SEPARATION-OF-DUTIES",
                "O solicitante não pode aprovar o próprio acesso de suporte.",
            )
        scopes = self._scope_values(approval.approved_scopes)
        if not set(scopes).issubset(set(grant.requested_scopes)):
            raise _error(
                "SUPPORT-SCOPE-ESCALATION",
                "A aprovação só pode reduzir os escopos solicitados.",
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        if approval.duration_minutes > grant.requested_duration_minutes:
            raise _error(
                "SUPPORT-DURATION-ESCALATION",
                "A aprovação só pode reduzir a duração solicitada.",
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        if grant.state == SupportAccessState.approved:
            if (
                grant.approved_scopes == scopes
                and grant.approved_duration_minutes == approval.duration_minutes
            ):
                return grant
            raise self._state_conflict(grant)
        if grant.state != SupportAccessState.requested:
            raise self._state_conflict(grant)

        now = datetime.now(UTC)
        previous = grant.state
        grant.state = SupportAccessState.approved
        grant.approver_user_id = approver_user_id
        grant.approved_scopes = scopes
        grant.approved_duration_minutes = approval.duration_minutes
        grant.approved_at = now
        grant.version += 1
        self._event(
            grant,
            event_type="support_access_approved",
            actor_user_id=approver_user_id,
            from_state=previous,
            to_state=grant.state,
            details={"scopes": scopes, "duration_minutes": approval.duration_minutes},
        )
        await self.db.commit()
        await self.db.refresh(grant)
        return grant

    async def reject(
        self,
        grant_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        decision: SupportAccessDecision,
    ) -> SupportAccessGrant:
        ensure_local_support_access_enabled()
        await self._require_workspace_approver(actor_user_id, workspace_id)
        grant = await self._load_workspace_grant(grant_id, workspace_id, lock=True)
        if grant.state == SupportAccessState.rejected:
            return grant
        if grant.state != SupportAccessState.requested:
            raise self._state_conflict(grant)
        previous = grant.state
        grant.state = SupportAccessState.rejected
        grant.rejected_by_user_id = actor_user_id
        grant.rejected_at = datetime.now(UTC)
        grant.rejection_reason = decision.reason
        grant.version += 1
        self._event(
            grant,
            event_type="support_access_rejected",
            actor_user_id=actor_user_id,
            from_state=previous,
            to_state=grant.state,
            details={"reason_present": True},
        )
        await self.db.commit()
        await self.db.refresh(grant)
        return grant

    async def revoke(
        self,
        grant_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        decision: SupportAccessDecision,
    ) -> SupportAccessGrant:
        ensure_local_support_access_enabled()
        await self._require_workspace_approver(actor_user_id, workspace_id)
        grant = await self._load_workspace_grant(grant_id, workspace_id, lock=True)
        if await self._expire_if_needed(grant):
            await self.db.commit()
            await self.db.refresh(grant)
            return grant
        if grant.state == SupportAccessState.revoked:
            return grant
        if grant.state not in {SupportAccessState.approved, SupportAccessState.active}:
            raise self._state_conflict(grant)
        previous = grant.state
        grant.state = SupportAccessState.revoked
        grant.revoked_by_user_id = actor_user_id
        grant.revoked_at = datetime.now(UTC)
        grant.revocation_reason = decision.reason
        grant.version += 1
        self._event(
            grant,
            event_type="support_access_revoked",
            actor_user_id=actor_user_id,
            from_state=previous,
            to_state=grant.state,
            details={"reason_present": True},
        )
        await self.db.commit()
        await self.db.refresh(grant)
        return grant

    async def activate(
        self,
        grant_id: UUID,
        actor_user_id: UUID,
        administrative_session_token: str,
        *,
        request: Request,
    ) -> SupportAccessTokenRead:
        ensure_local_support_access_enabled()
        await self._require_platform_support_operator(actor_user_id)
        admin_session = await LocalMfaService(self.db).verify_administrative_session(
            administrative_session_token,
            request=request,
            commit=False,
        )
        if admin_session.user_id != actor_user_id:
            raise LocalMfaService._invalid_admin_session()

        grant = await self._load_grant(grant_id)
        if grant.requester_user_id != actor_user_id:
            raise _error(
                "SUPPORT-NOT-REQUESTER",
                "Somente o platform admin solicitante pode ativar este acesso.",
            )
        await LocalMfaService(self.db).require_linked_administrative_session(
            admin_session.id,
            actor_user_id=actor_user_id,
            workspace_id=grant.workspace_id,
        )
        if grant.state != SupportAccessState.approved:
            raise self._state_conflict(grant)
        if not grant.approved_scopes or not grant.approved_duration_minutes:
            raise _error(
                "SUPPORT-INVALID-APPROVAL",
                "A aprovação não contém escopo e duração válidos.",
                status_code=status.HTTP_409_CONFLICT,
            )

        now = datetime.now(UTC)
        expires_at = min(
            now + timedelta(minutes=grant.approved_duration_minutes),
            _as_utc(admin_session.expires_at),
        )
        if expires_at <= now:
            raise LocalMfaService._invalid_admin_session()
        token_jti = uuid4().hex
        expected_version = grant.version
        result = await self.db.execute(
            update(SupportAccessGrant)
            .where(
                SupportAccessGrant.id == grant.id,
                SupportAccessGrant.state == SupportAccessState.approved,
                SupportAccessGrant.version == expected_version,
            )
            .values(
                state=SupportAccessState.active,
                activated_by_user_id=actor_user_id,
                activation_admin_session_id=admin_session.id,
                active_token_jti=token_jti,
                activated_at=now,
                expires_at=expires_at,
                version=expected_version + 1,
            )
        )
        if result.rowcount != 1:
            await self.db.rollback()
            raise _error(
                "SUPPORT-ACTIVATION-CONFLICT",
                "O grant já foi ativado ou alterado por outra operação.",
                status_code=status.HTTP_409_CONFLICT,
            )
        grant = await self._load_grant(grant.id)
        self._event(
            grant,
            event_type="support_access_activated",
            actor_user_id=actor_user_id,
            from_state=SupportAccessState.approved,
            to_state=SupportAccessState.active,
            details={
                "scopes": list(grant.approved_scopes or []),
                "expires_at": expires_at.isoformat(),
                "mfa_verified": True,
            },
        )
        token = create_support_access_token(
            actor_user_id,
            grant_id=grant.id,
            workspace_id=grant.workspace_id,
            scopes=list(grant.approved_scopes or []),
            expires_at=expires_at,
            token_jti=token_jti,
        )
        await self.db.flush()
        await self.db.refresh(grant)
        return SupportAccessTokenRead(
            support_access_token=token,
            expires_in=max(1, int((expires_at - now).total_seconds())),
            grant=grant,
        )

    async def activate_root_direct(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        administrative_session_token: str | None,
        *,
        reason: str,
        request: Request,
    ) -> SupportAccessTokenRead:
        """Create an approval-free root session after a fresh, actor-bound MFA step-up."""
        ensure_local_support_access_enabled()
        actor = await self.db.get(User, actor_user_id)
        if not is_platform_root(actor):
            raise _error(
                "PLATFORM-ROOT-REQUIRED",
                "A operação exige o titular da plataforma.",
            )
        if not administrative_session_token:
            raise _error(
                "SUPPORT-ROOT-STEP-UP-REQUIRED",
                "Confirme o MFA em uma sessão administrativa antes do acesso direto.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        admin_session = await LocalMfaService(self.db).verify_administrative_session(
            administrative_session_token,
            request=request,
            commit=False,
        )
        if admin_session.user_id != actor_user_id:
            raise LocalMfaService._invalid_admin_session()
        await LocalMfaService(self.db).require_linked_administrative_session(
            admin_session.id,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
        )

        await self._require_active_workspace(workspace_id)
        now = datetime.now(UTC)
        if _as_utc(admin_session.created_at) < (
            now - timedelta(seconds=ROOT_DIRECT_STEP_UP_MAX_AGE_SECONDS)
        ):
            raise _error(
                "SUPPORT-ROOT-STEP-UP-STALE",
                "A confirmação MFA não é recente. Faça um novo step-up administrativo.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        expires_at = min(
            now + timedelta(minutes=SUPPORT_ACCESS_MAX_DURATION_MINUTES),
            _as_utc(admin_session.expires_at),
        )
        if expires_at <= now:
            raise LocalMfaService._invalid_admin_session()
        scopes = sorted(scope.value for scope in SupportAccessScope)
        token_jti = uuid4().hex
        grant = SupportAccessGrant(
            workspace_id=workspace_id,
            requester_user_id=actor.id,
            reason=reason,
            ticket_reference=f"ROOT-{uuid4().hex}",
            requested_scopes=scopes,
            requested_duration_minutes=SUPPORT_ACCESS_MAX_DURATION_MINUTES,
            state=SupportAccessState.active,
            requested_at=now,
            approver_user_id=None,
            approved_scopes=scopes,
            approved_duration_minutes=SUPPORT_ACCESS_MAX_DURATION_MINUTES,
            approved_at=now,
            activated_by_user_id=actor.id,
            activation_admin_session_id=admin_session.id,
            active_token_jti=token_jti,
            activated_at=now,
            expires_at=expires_at,
        )
        self.db.add(grant)
        await self.db.flush()
        self._event(
            grant,
            event_type="support_access_root_direct",
            actor_user_id=actor.id,
            from_state=None,
            to_state=SupportAccessState.active,
            details={
                "scopes": scopes,
                "expires_at": expires_at.isoformat(),
                "approval_required": False,
                "mfa_required": True,
                "mfa_verified": True,
                "session_type": "root_direct",
                "reason": reason,
            },
        )
        token = create_support_access_token(
            actor.id,
            grant_id=grant.id,
            workspace_id=workspace_id,
            scopes=scopes,
            expires_at=expires_at,
            token_jti=token_jti,
        )
        await self.db.flush()
        await self.db.refresh(grant)
        return SupportAccessTokenRead(
            support_access_token=token,
            expires_in=max(1, int((expires_at - now).total_seconds())),
            grant=grant,
        )

    async def authorize_token(
        self,
        payload: dict,
        *,
        required_scope: str | None,
        request: Request,
    ) -> AuthorizedSupportAccess:
        """Revalidate database state, expiry and scope for every support request."""
        ensure_local_support_access_enabled()
        try:
            grant_id = UUID(str(payload["grant_id"]))
            user_id = UUID(str(payload["sub"]))
            workspace_id = UUID(str(payload["workspace_id"]))
            token_jti = str(payload["jti"])
            claim_scopes = sorted({str(scope) for scope in payload["scopes"]})
            claim_expiry = datetime.fromtimestamp(float(payload["exp"]), tz=UTC)
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise self._invalid_support_token() from error

        grant = await self.db.scalar(
            select(SupportAccessGrant).where(SupportAccessGrant.id == grant_id).with_for_update()
        )
        if grant is None:
            raise self._invalid_support_token()
        identity_matches = (
            grant.requester_user_id == user_id
            and grant.workspace_id == workspace_id
            and grant.active_token_jti is not None
            and hmac.compare_digest(grant.active_token_jti, token_jti)
        )
        approved_scopes = sorted(set(grant.approved_scopes or []))
        if not identity_matches or claim_scopes != approved_scopes:
            raise self._invalid_support_token()

        if await self._expire_if_needed(grant):
            self._access_event(
                grant,
                user_id,
                request,
                event_type="support_access_denied",
                required_scope=required_scope,
                reason_code="expired",
            )
            await self.db.commit()
            raise self._invalid_support_token()
        expiry_matches = (
            grant.expires_at is not None
            and claim_expiry <= _as_utc(grant.expires_at) + timedelta(seconds=1)
        )
        if not expiry_matches:
            raise self._invalid_support_token()
        if grant.state != SupportAccessState.active:
            self._access_event(
                grant,
                user_id,
                request,
                event_type="support_access_denied",
                required_scope=required_scope,
                reason_code=grant.state.value,
            )
            await self.db.commit()
            raise self._invalid_support_token()

        user = await self.db.scalar(
            select(User).where(
                User.id == user_id,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
        workspace = await self.db.scalar(
            select(Workspace).where(
                Workspace.id == workspace_id,
                Workspace.is_active.is_(True),
                Workspace.deleted_at.is_(None),
            )
        )
        if user is None or workspace is None:
            self._access_event(
                grant,
                user_id,
                request,
                event_type="support_access_denied",
                required_scope=required_scope,
                reason_code="principal_or_workspace_inactive",
            )
            await self.db.commit()
            raise self._invalid_support_token()

        try:
            session_type = await self._administrative_session_type(grant, user)
            if grant.activation_admin_session_id is None:
                raise LocalMfaService._invalid_admin_session()
            administrative_session = (
                await LocalMfaService(self.db).require_linked_administrative_session(
                    grant.activation_admin_session_id,
                    actor_user_id=user_id,
                    workspace_id=workspace_id,
                )
            )
        except HTTPException:
            self._access_event(
                grant,
                user_id,
                request,
                event_type="support_access_denied",
                required_scope=required_scope,
                reason_code="administrative_session_invalid",
            )
            await self.db.commit()
            raise self._invalid_support_token() from None

        if (
            session_type == "support_approved"
            and not has_platform_permission(user, "support_access", "request")
        ):
            self._access_event(
                grant,
                user_id,
                request,
                event_type="support_access_denied",
                required_scope=required_scope,
                reason_code="principal_permission_revoked",
            )
            await self.db.commit()
            raise self._invalid_support_token()
        if required_scope is None or required_scope not in approved_scopes:
            self._access_event(
                grant,
                user_id,
                request,
                event_type="support_access_denied",
                required_scope=required_scope,
                reason_code="scope_denied",
            )
            await self.db.commit()
            raise _error(
                "SUPPORT-SCOPE-DENIED",
                "O grant de suporte não autoriza esta operação.",
            )

        self._access_event(
            grant,
            user_id,
            request,
            event_type="support_access_used",
            required_scope=required_scope,
            reason_code=None,
        )
        await self.db.commit()
        return AuthorizedSupportAccess(
            grant=grant,
            administrative_session_id=administrative_session.id,
            administrative_session_type=session_type,
        )

    async def _administrative_session_type(
        self,
        grant: SupportAccessGrant,
        actor: User,
    ) -> AdministrativeSupportSessionType:
        """Classify the derived session from append-only evidence and invariants."""
        root_event_id = await self.db.scalar(
            select(SupportAccessEvent.id)
            .where(
                SupportAccessEvent.grant_id == grant.id,
                SupportAccessEvent.event_type == "support_access_root_direct",
            )
            .limit(1)
        )
        common_actor_matches = (
            grant.requester_user_id == actor.id
            and grant.activated_by_user_id == actor.id
        )
        if root_event_id is not None:
            if not (
                common_actor_matches
                and grant.approver_user_id is None
                and str(grant.ticket_reference or "").startswith("ROOT-")
                and is_platform_root(actor)
            ):
                raise self._invalid_support_token()
            return "root_direct"

        if not (
            common_actor_matches
            and grant.approver_user_id is not None
            and grant.approved_at is not None
            and grant.activation_admin_session_id is not None
        ):
            raise self._invalid_support_token()
        return "support_approved"

    async def list_events_for_platform(
        self,
        grant_id: UUID,
        actor_user_id: UUID,
    ) -> list[SupportAccessEvent]:
        ensure_local_support_access_enabled()
        await self._require_platform_support_operator(actor_user_id)
        await self._load_grant(grant_id)
        return await self._list_events(grant_id)

    async def page_events_for_platform(
        self,
        grant_id: UUID,
        actor_user_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> SupportAccessEventPageRead:
        ensure_local_support_access_enabled()
        await self._require_platform_support_operator(actor_user_id)
        await self._load_grant(grant_id)
        return await self._page_events(grant_id, limit=limit, offset=offset)

    async def list_events_for_workspace(
        self,
        grant_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
    ) -> list[SupportAccessEvent]:
        ensure_local_support_access_enabled()
        await self._require_workspace_approver(actor_user_id, workspace_id)
        await self._load_workspace_grant(grant_id, workspace_id)
        return await self._list_events(grant_id)

    async def page_events_for_workspace(
        self,
        grant_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> SupportAccessEventPageRead:
        ensure_local_support_access_enabled()
        await self._require_workspace_approver(actor_user_id, workspace_id)
        await self._load_workspace_grant(grant_id, workspace_id)
        return await self._page_events(grant_id, limit=limit, offset=offset)

    async def _list_events(self, grant_id: UUID) -> list[SupportAccessEvent]:
        latest = list(
            (
                await self.db.scalars(
                    select(SupportAccessEvent)
                    .where(SupportAccessEvent.grant_id == grant_id)
                    .order_by(
                        SupportAccessEvent.occurred_at.desc(),
                        SupportAccessEvent.id.desc(),
                    )
                    .limit(500)
                )
            ).all()
        )
        return list(reversed(latest))

    async def _page_events(
        self,
        grant_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> SupportAccessEventPageRead:
        total = int(
            (
                await self.db.scalar(
                    select(func.count(SupportAccessEvent.id)).where(
                        SupportAccessEvent.grant_id == grant_id
                    )
                )
            )
            or 0
        )
        items = list(
            (
                await self.db.scalars(
                    select(SupportAccessEvent)
                    .where(SupportAccessEvent.grant_id == grant_id)
                    .order_by(
                        SupportAccessEvent.occurred_at.desc(),
                        SupportAccessEvent.id.desc(),
                    )
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
        )
        return SupportAccessEventPageRead(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + len(items) < total,
        )

    async def _require_platform_support_operator(self, user_id: UUID) -> User:
        user = await self.db.scalar(
            select(User).where(
                User.id == user_id,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
        if user is None or not has_platform_permission(user, "support_access", "request"):
            raise _error(
                "SUPPORT-PLATFORM-PERMISSION-REQUIRED",
                "A operação exige um pacote ativo de suporte da plataforma.",
            )
        return user

    async def _require_workspace_approver(
        self,
        user_id: UUID,
        workspace_id: UUID,
    ) -> WorkspaceMembership:
        membership = await self.db.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.status == MembershipStatus.active,
                WorkspaceMembership.role.in_(
                    {WorkspaceRole.workspace_owner, WorkspaceRole.workspace_admin}
                ),
            )
        )
        if membership is None:
            raise _error(
                "SUPPORT-WORKSPACE-APPROVER-REQUIRED",
                "A operação exige owner ou admin ativo deste workspace.",
            )
        return membership

    async def _require_active_workspace(
        self,
        workspace_id: UUID,
        *,
        lock: bool = False,
    ) -> Workspace:
        statement = select(Workspace).where(
                Workspace.id == workspace_id,
                Workspace.is_active.is_(True),
                Workspace.deleted_at.is_(None),
            )
        if lock:
            statement = statement.with_for_update()
        workspace = await self.db.scalar(statement)
        if workspace is None:
            raise _error(
                "SUPPORT-WORKSPACE-NOT-FOUND",
                "Workspace ativo não encontrado.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return workspace

    async def _find_ticket_request(
        self,
        workspace_id: UUID,
        requester_user_id: UUID,
        ticket_reference: str,
    ) -> SupportAccessGrant | None:
        return await self.db.scalar(
            select(SupportAccessGrant).where(
                SupportAccessGrant.workspace_id == workspace_id,
                SupportAccessGrant.requester_user_id == requester_user_id,
                SupportAccessGrant.ticket_reference == ticket_reference,
            )
        )

    @staticmethod
    def _assert_idempotent_request(
        grant: SupportAccessGrant,
        payload: SupportAccessRequestCreate,
        scopes: list[str],
    ) -> None:
        if (
            grant.reason != payload.reason
            or grant.requested_scopes != scopes
            or grant.requested_duration_minutes != payload.duration_minutes
        ):
            raise _error(
                "SUPPORT-TICKET-CONFLICT",
                "A referência já foi usada com parâmetros diferentes.",
                status_code=status.HTTP_409_CONFLICT,
            )

    async def _load_grant(self, grant_id: UUID, *, lock: bool = False) -> SupportAccessGrant:
        statement = select(SupportAccessGrant).where(SupportAccessGrant.id == grant_id)
        if lock:
            statement = statement.with_for_update()
        grant = await self.db.scalar(statement)
        if grant is None:
            raise _error(
                "SUPPORT-GRANT-NOT-FOUND",
                "Solicitação de suporte não encontrada.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return grant

    async def _load_workspace_grant(
        self,
        grant_id: UUID,
        workspace_id: UUID,
        *,
        lock: bool = False,
    ) -> SupportAccessGrant:
        statement = select(SupportAccessGrant).where(
            SupportAccessGrant.id == grant_id,
            SupportAccessGrant.workspace_id == workspace_id,
        )
        if lock:
            statement = statement.with_for_update()
        grant = await self.db.scalar(statement)
        if grant is None:
            raise _error(
                "SUPPORT-GRANT-NOT-FOUND",
                "Solicitação de suporte não encontrada neste workspace.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return grant

    async def _expire_matching(self, *, workspace_id: UUID | None) -> None:
        now = datetime.now(UTC)
        conditions = [
            SupportAccessGrant.state == SupportAccessState.active,
            SupportAccessGrant.expires_at.is_not(None),
            SupportAccessGrant.expires_at <= now,
        ]
        if workspace_id is not None:
            conditions.append(SupportAccessGrant.workspace_id == workspace_id)
        grants = list(
            (
                await self.db.scalars(
                    select(SupportAccessGrant).where(*conditions).with_for_update()
                )
            ).all()
        )
        for grant in grants:
            await self._expire_if_needed(grant, now=now)
        if grants:
            await self.db.commit()

    async def _expire_if_needed(
        self,
        grant: SupportAccessGrant,
        *,
        now: datetime | None = None,
    ) -> bool:
        resolved_now = now or datetime.now(UTC)
        if (
            grant.state != SupportAccessState.active
            or grant.expires_at is None
            or _as_utc(grant.expires_at) > resolved_now
        ):
            return False
        previous = grant.state
        grant.state = SupportAccessState.expired
        grant.expired_at = resolved_now
        grant.version += 1
        self._event(
            grant,
            event_type="support_access_expired",
            actor_user_id=None,
            from_state=previous,
            to_state=grant.state,
            details={"expired_by_request_check": True},
        )
        return True

    @staticmethod
    def _scope_values(scopes: list[SupportAccessScope]) -> list[str]:
        return sorted({scope.value for scope in scopes})

    def _event(
        self,
        grant: SupportAccessGrant,
        *,
        event_type: str,
        actor_user_id: UUID | None,
        from_state: SupportAccessState | None,
        to_state: SupportAccessState | None,
        details: dict,
    ) -> None:
        self.db.add(
            SupportAccessEvent(
                grant_id=grant.id,
                workspace_id=grant.workspace_id,
                actor_user_id=actor_user_id,
                event_type=event_type,
                from_state=from_state.value if from_state is not None else None,
                to_state=to_state.value if to_state is not None else None,
                details=details,
            )
        )

    def _access_event(
        self,
        grant: SupportAccessGrant,
        actor_user_id: UUID,
        request: Request,
        *,
        event_type: str,
        required_scope: str | None,
        reason_code: str | None,
    ) -> None:
        details = {
            "method": request.method.upper(),
            "route": support_route_template(request),
            "required_scope": required_scope,
        }
        if reason_code is not None:
            details["reason_code"] = reason_code
        self._event(
            grant,
            event_type=event_type,
            actor_user_id=actor_user_id,
            from_state=grant.state,
            to_state=grant.state,
            details=details,
        )

    @staticmethod
    def _state_conflict(grant: SupportAccessGrant) -> HTTPException:
        return _error(
            "SUPPORT-STATE-CONFLICT",
            f"A operação não é válida no estado {grant.state.value}.",
            status_code=status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _invalid_support_token() -> HTTPException:
        return _error(
            "SUPPORT-TOKEN-INVALID",
            "Token de suporte inválido, expirado ou revogado.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
