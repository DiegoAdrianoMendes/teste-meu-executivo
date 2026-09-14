"""
Endpoints administrativos tenant-aware.
"""
import colorsys
from copy import deepcopy
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1.entitlement_errors import entitlement_http_exception
from app.core.deps import (
    AuthContext,
    get_active_membership,
    get_current_workspace_id,
    get_db,
    require_permission,
    require_workspace_admin,
)
from app.models.auth_security import AuthRateLimitScope
from app.models.configuration import ConfigScope
from app.models.user import AgentType, User
from app.models.workspace_membership import WorkspaceMembership, WorkspaceRole
from app.schemas.agent_policy import (
    WorkspaceAutomationSettingsRead,
    WorkspaceAutomationSettingsUpdate,
)
from app.schemas.configuration import configuration_contract_defaults
from app.schemas.control_plane import AdminControlPlaneRead, AdminControlPlaneUpdate
from app.schemas.identity import AdministrativeInvitationLinkRead
from app.schemas.user import UserRead
from app.schemas.whatsapp import MessageTemplateSettingsRead, MessageTemplateSettingsUpdate
from app.schemas.workspace import (
    MembershipCreate,
    MembershipUpdate,
    RoleDefinitionRead,
    TeamMemberCreate,
    TeamMemberRead,
    TeamMemberUpdate,
    WorkspaceAccessPackageRead,
    WorkspaceMembershipRead,
    WorkspaceRead,
    WorkspaceUpdate,
)
from app.services.auth_rate_limit_service import PersistentAuthRateLimiter, request_network_key
from app.services.configuration_service import ConfigurationService
from app.services.entitlement_service import EntitlementError
from app.services.identity_outbox_service import LocalIdentityOutboxService
from app.services.team_member_service import TeamMemberService
from app.services.whatsapp_service import WhatsAppService
from app.services.workspace_policy_service import WorkspacePolicyService
from app.services.workspace_service import WorkspaceService

router = APIRouter(prefix="/admin", tags=["Administração"])


def _hex_to_hsl_channels(value: str, fallback: str) -> str:
    normalized = value.strip().lstrip("#")
    if len(normalized) != 6:
        return fallback
    try:
        red, green, blue = (
            int(normalized[index : index + 2], 16) / 255
            for index in (0, 2, 4)
        )
    except ValueError:
        return fallback
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    return f"{round(hue * 360)} {round(saturation * 100)}% {round(lightness * 100)}%"


@router.get("/workspaces", response_model=List[WorkspaceRead], summary="Listar workspaces")
async def list_workspaces(
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    service = WorkspaceService(db)
    workspace = await service.get_workspace(workspace_id)
    workspaces = [workspace] if workspace else []
    return [await service.to_workspace_read(workspace) for workspace in workspaces if workspace]


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceRead, summary="Atualizar workspace")
async def update_workspace(
    workspace_id: UUID,
    payload: WorkspaceUpdate,
    context: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
):
    if workspace_id != context.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "AUTH-013",
                "message": "Administradores só podem alterar o workspace ativo.",
            },
        )
    service = WorkspaceService(db)
    workspace = await service.update_workspace(workspace_id, payload)
    return await service.to_workspace_read(workspace)


@router.get("/users", response_model=List[UserRead], summary="Listar usuários do workspace")
async def list_workspace_users(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("users", "manage")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User)
        .join(WorkspaceMembership, WorkspaceMembership.user_id == User.id)
        .where(
            WorkspaceMembership.workspace_id == workspace_id,
            User.deleted_at.is_(None),
        )
        .options(selectinload(User.memberships))
        .order_by(User.name.asc())
    )
    return [UserRead.model_validate(user) for user in result.scalars().all()]


@router.get("/memberships", response_model=List[WorkspaceMembershipRead], summary="Listar memberships do workspace")
async def list_workspace_memberships(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("memberships", "manage")),
    db: AsyncSession = Depends(get_db),
):
    service = WorkspaceService(db)
    memberships = await service.list_memberships(workspace_id)
    return [service.to_membership_read(membership) for membership in memberships]


@router.post("/memberships", response_model=WorkspaceMembershipRead, status_code=status.HTTP_201_CREATED, summary="Adicionar usuário ao workspace")
async def create_workspace_membership(
    payload: MembershipCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_permission("memberships", "manage")),
    db: AsyncSession = Depends(get_db),
):
    target_user = await db.get(User, payload.user_id)
    if target_user is not None and target_user.agent_type == AgentType.human:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "TEAM-INVITATION-REQUIRED",
                "message": "Identidades humanas ingressam no workspace somente por convite.",
            },
        )
    if payload.role == WorkspaceRole.workspace_owner:
        actor_membership = await get_active_membership(context, db)
        if actor_membership.role != WorkspaceRole.workspace_owner:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "TEAM-005", "message": "Somente o titular pode atribuir o papel de titular."},
            )
    service = WorkspaceService(db)
    membership = await service.create_membership(workspace_id, payload)
    result = await db.execute(
        select(WorkspaceMembership)
        .options(selectinload(WorkspaceMembership.workspace), selectinload(WorkspaceMembership.user))
        .where(WorkspaceMembership.id == membership.id)
    )
    membership_full = result.scalar_one()
    return service.to_membership_read(membership_full)


@router.patch("/memberships/{membership_id}", response_model=WorkspaceMembershipRead, summary="Atualizar membership")
async def update_workspace_membership(
    membership_id: UUID,
    payload: MembershipUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_permission("memberships", "manage")),
    db: AsyncSession = Depends(get_db),
):
    target = await db.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.id == membership_id,
            WorkspaceMembership.workspace_id == workspace_id,
        )
    )
    if target and target.role == WorkspaceRole.workspace_owner:
        actor_membership = await get_active_membership(context, db)
        if actor_membership.role != WorkspaceRole.workspace_owner:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "TEAM-006", "message": "Administradores não podem alterar o titular do workspace."},
            )
    if payload.role == WorkspaceRole.workspace_owner:
        actor_membership = await get_active_membership(context, db)
        if actor_membership.role != WorkspaceRole.workspace_owner:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "TEAM-005", "message": "Somente o titular pode atribuir o papel de titular."},
            )
    team_update: dict = {}
    if payload.role is not None:
        team_update["workspace_role"] = payload.role
    if payload.status is not None:
        team_update["status"] = payload.status
    if team_update:
        await TeamMemberService(db).update(
            workspace_id,
            membership_id,
            context,
            TeamMemberUpdate(**team_update),
        )

    service = WorkspaceService(db)
    membership: WorkspaceMembership | None
    if payload.is_default is not None:
        membership = await service.update_membership(
            workspace_id,
            membership_id,
            MembershipUpdate(is_default=payload.is_default),
        )
    else:
        membership = await db.scalar(
            select(WorkspaceMembership)
            .options(
                selectinload(WorkspaceMembership.workspace),
                selectinload(WorkspaceMembership.user),
            )
            .where(
                WorkspaceMembership.id == membership_id,
                WorkspaceMembership.workspace_id == workspace_id,
            )
        )
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership não encontrada.")
    return service.to_membership_read(membership)


@router.get(
    "/team-members",
    response_model=List[TeamMemberRead],
    summary="Listar equipe com permissões efetivas por usuário",
)
async def list_team_members(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("users", "manage")),
    db: AsyncSession = Depends(get_db),
):
    return await TeamMemberService(db).list(workspace_id)


@router.post(
    "/team-members",
    response_model=TeamMemberRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Convidar identidade humana para o workspace",
)
async def create_team_member(
    payload: TeamMemberCreate,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_permission("users", "manage")),
    db: AsyncSession = Depends(get_db),
):
    decision = await PersistentAuthRateLimiter(db).check(
        AuthRateLimitScope.invitation,
        identifier=f"{workspace_id}:{str(payload.email).strip().casefold()}",
        network=request_network_key(request),
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "AUTH-RATE-LIMITED", "message": "Convites temporariamente limitados."},
            headers={"Retry-After": str(decision.retry_after)},
        )
    try:
        return await TeamMemberService(db).create(
            workspace_id,
            context,
            payload,
            idempotency_key=idempotency_key,
        )
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error


@router.patch(
    "/team-members/{membership_id}",
    response_model=TeamMemberRead,
    summary="Atualizar papel, status e exceções da membership",
)
async def update_team_member(
    membership_id: UUID,
    payload: TeamMemberUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_permission("users", "manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await TeamMemberService(db).update(workspace_id, membership_id, context, payload)
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error


@router.post(
    "/team-members/invitations/{invitation_id}/resend",
    response_model=TeamMemberRead,
    summary="Rotacionar e reenviar convite local",
)
async def resend_team_invitation(
    invitation_id: UUID,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_permission("users", "manage")),
    db: AsyncSession = Depends(get_db),
):
    decision = await PersistentAuthRateLimiter(db).check(
        AuthRateLimitScope.invitation,
        identifier=f"{workspace_id}:{invitation_id}",
        network=request_network_key(request),
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "AUTH-RATE-LIMITED", "message": "Convites temporariamente limitados."},
            headers={"Retry-After": str(decision.retry_after)},
        )
    return await TeamMemberService(db).resend_invitation(
        workspace_id,
        invitation_id,
        context,
        idempotency_key=idempotency_key,
    )


@router.get(
    "/team-members/invitations/{invitation_id}/link",
    response_model=AdministrativeInvitationLinkRead,
    summary="Obter link temporário de um convite pendente",
)
async def read_team_invitation_link(
    invitation_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_permission("users", "manage")),
    db: AsyncSession = Depends(get_db),
) -> AdministrativeInvitationLinkRead:
    delivery = await LocalIdentityOutboxService(db).read_workspace_invitation_admin(
        invitation_id,
        workspace_id=workspace_id,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return AdministrativeInvitationLinkRead(
        link=delivery.link,
        recipient_hint=delivery.recipient_hint,
        expires_at=delivery.expires_at,
    )


@router.delete(
    "/team-members/invitations/{invitation_id}",
    response_model=TeamMemberRead,
    summary="Revogar convite pendente",
)
async def revoke_team_invitation(
    invitation_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_permission("users", "manage")),
    db: AsyncSession = Depends(get_db),
):
    return await TeamMemberService(db).revoke_invitation(
        workspace_id,
        invitation_id,
        context,
    )


@router.get("/roles", response_model=List[RoleDefinitionRead], summary="Catálogo de papéis do workspace")
async def list_workspace_roles(
    _: AuthContext = Depends(require_permission("roles", "view")),
    db: AsyncSession = Depends(get_db),
):
    return WorkspaceService(db).list_role_catalog()


@router.get("/permissions", response_model=List[RoleDefinitionRead], summary="Papéis e permissões disponíveis")
async def list_workspace_permissions(
    _: AuthContext = Depends(require_permission("permissions", "view")),
    db: AsyncSession = Depends(get_db),
):
    return WorkspaceService(db).list_role_catalog()


@router.get(
    "/access-packages",
    response_model=List[WorkspaceAccessPackageRead],
    summary="Pacotes de acesso prontos para equipes de escritórios",
)
async def list_workspace_access_packages(
    _: AuthContext = Depends(require_permission("roles", "view")),
    db: AsyncSession = Depends(get_db),
):
    return WorkspaceService(db).list_access_package_catalog()


@router.get("/settings", response_model=WorkspaceRead, summary="Configurações do workspace ativo")
async def get_workspace_settings(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("admin", "view")),
    db: AsyncSession = Depends(get_db),
):
    service = WorkspaceService(db)
    workspace = await service.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "WKS-001", "message": "Workspace não encontrado."},
        )
    return await service.to_workspace_read(workspace)


@router.patch("/settings", response_model=WorkspaceRead, summary="Atualizar configurações do workspace ativo")
async def update_workspace_settings(
    payload: WorkspaceUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("configuracoes", "edit")),
    db: AsyncSession = Depends(get_db),
):
    service = WorkspaceService(db)
    workspace = await service.update_workspace(workspace_id, payload)
    return await service.to_workspace_read(workspace)


@router.get(
    "/settings/automation",
    response_model=WorkspaceAutomationSettingsRead,
    summary="Configurações de automação do workspace ativo",
)
async def get_workspace_automation_settings(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("admin", "view")),
    db: AsyncSession = Depends(get_db),
):
    return await WorkspacePolicyService(db).get_workspace_automation_settings(workspace_id)


@router.patch(
    "/settings/automation",
    response_model=WorkspaceAutomationSettingsRead,
    summary="Atualizar automação, modelos e contexto do workspace ativo",
)
async def update_workspace_automation_settings(
    payload: WorkspaceAutomationSettingsUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("configuracoes", "edit")),
    db: AsyncSession = Depends(get_db),
):
    return await WorkspacePolicyService(db).update_workspace_automation_settings(workspace_id, payload)


@router.get(
    "/settings/control-plane",
    response_model=AdminControlPlaneRead,
    summary="Biblioteca, prompts, modelos e governança administrativa do workspace",
)
async def get_admin_control_plane(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
):
    return await WorkspacePolicyService(db).get_admin_control_plane(workspace_id)


@router.patch(
    "/settings/control-plane",
    response_model=AdminControlPlaneRead,
    summary="Atualizar biblioteca, prompts, modelos e governança administrativa",
)
async def update_admin_control_plane(
    payload: AdminControlPlaneUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await WorkspacePolicyService(db).update_admin_control_plane(workspace_id, payload)
    if payload.branding is not None:
        configuration = ConfigurationService(db)
        resolved = await configuration.resolve(
            namespace="brand.identity",
            workspace_id=workspace_id,
            role_key=context.role.value if context.role else None,
            user_id=context.user_id,
            contract_defaults=configuration_contract_defaults("brand.identity"),
        )
        brand_payload = deepcopy(resolved.effective)
        brand_payload["name"] = payload.branding.brand_name
        brand_payload["shortName"] = payload.branding.short_name
        brand_payload["tagline"] = payload.branding.tagline
        brand_payload["legalName"] = payload.branding.legal_name
        brand_payload["domain"] = payload.branding.domain
        brand_payload["defaultLocale"] = payload.branding.default_locale
        brand_payload["defaultDensity"] = payload.branding.layout_density
        supplied_brand_fields = payload.branding.model_fields_set
        logo_dark_url = (
            payload.branding.logo_url
            if "logo_url" in supplied_brand_fields
            and "logo_dark_url" not in supplied_brand_fields
            else payload.branding.logo_dark_url
        )
        favicon_url = (
            payload.branding.icon_url
            if "icon_url" in supplied_brand_fields
            and "favicon_url" not in supplied_brand_fields
            else payload.branding.favicon_url
        )
        apple_touch_icon_url = (
            payload.branding.icon_url
            if "icon_url" in supplied_brand_fields
            and "apple_touch_icon_url" not in supplied_brand_fields
            else payload.branding.apple_touch_icon_url
        )
        assets = brand_payload.setdefault("assets", {})
        assets.update(
            {
                "logoLightUrl": payload.branding.logo_url,
                "logoDarkUrl": logo_dark_url,
                "markUrl": payload.branding.icon_url,
                "faviconUrl": favicon_url,
                "appleTouchIconUrl": apple_touch_icon_url,
                "letterheadUrl": payload.branding.letterhead_url,
                "signatureUrl": payload.branding.signature_url,
            }
        )
        colors = brand_payload.setdefault("colors", {})
        colors.update(
            {
                "primary": payload.branding.primary_color,
                "accent": payload.branding.accent_color,
                "foreground": payload.branding.foreground_color,
                "background": payload.branding.background_color,
                "sidebar": payload.branding.sidebar_color,
                "success": payload.branding.success_color,
                "warning": payload.branding.warning_color,
                "danger": payload.branding.danger_color,
            }
        )
        variables = brand_payload.setdefault("cssVariables", {})
        variables.update(
            {
                "--primary": _hex_to_hsl_channels(
                    payload.branding.primary_color, "39 67% 58%"
                ),
                "--accent": _hex_to_hsl_channels(
                    payload.branding.accent_color, "37 56% 50%"
                ),
                "--foreground": _hex_to_hsl_channels(
                    payload.branding.foreground_color, "222 24% 10%"
                ),
                "--background": _hex_to_hsl_channels(
                    payload.branding.background_color, "60 9% 98%"
                ),
                "--sidebar-background": _hex_to_hsl_channels(
                    payload.branding.sidebar_color, "0 0% 2%"
                ),
                "--sidebar-primary": _hex_to_hsl_channels(
                    payload.branding.primary_color, "39 67% 58%"
                ),
                "--success": _hex_to_hsl_channels(
                    payload.branding.success_color, "136 55% 31%"
                ),
                "--warning": _hex_to_hsl_channels(
                    payload.branding.warning_color, "39 96% 43%"
                ),
                "--destructive": _hex_to_hsl_channels(
                    payload.branding.danger_color, "1 45% 50%"
                ),
                "--brand-font-family": payload.branding.sans_family,
            }
        )
        variables["--ring"] = variables["--primary"]
        brand_payload["typography"] = {
            "sansFamily": payload.branding.sans_family,
            "legalFamily": payload.branding.legal_family,
        }
        brand_payload["metadata"] = {
            "defaultTitle": payload.branding.default_title,
            "titleTemplate": payload.branding.title_template,
            "description": payload.branding.description,
            "applicationName": payload.branding.application_name,
            "author": payload.branding.author,
            "themeColor": payload.branding.theme_color,
        }
        brand_payload["contact"] = {
            "email": payload.branding.email,
            "supportEmail": payload.branding.support_email,
            "phone": payload.branding.phone,
            "websiteUrl": payload.branding.website_url,
        }
        brand_payload["legal"] = {
            "registeredName": payload.branding.registered_name,
            "registrationNumber": payload.branding.registration_number,
            "barRegistration": payload.branding.bar_registration,
            "address": payload.branding.address,
        }
        brand_payload["socialProof"] = [
            item.model_dump(mode="json", by_alias=True)
            for item in payload.branding.social_proof
        ]
        await configuration.write_tenant(
            workspace_id=workspace_id,
            scope=ConfigScope.workspace,
            namespace="brand.identity",
            payload=brand_payload,
            actor_user_id=context.user_id,
            reason="Branding updated from workspace settings",
        )
    return result


@router.get(
    "/message-templates",
    response_model=MessageTemplateSettingsRead,
    summary="Templates de mensagens, propostas e contrato do workspace",
)
async def get_message_templates(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("configuracoes", "view")),
    db: AsyncSession = Depends(get_db),
):
    return await WhatsAppService(db).get_template_settings(workspace_id)


@router.put(
    "/message-templates",
    response_model=MessageTemplateSettingsRead,
    summary="Atualizar templates de mensagens, propostas e contrato",
)
async def update_message_templates(
    payload: MessageTemplateSettingsUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("configuracoes", "edit")),
    db: AsyncSession = Depends(get_db),
):
    return await WhatsAppService(db).update_template_settings(workspace_id, payload)


@router.get("/roles/catalog", summary="Catálogo de roles com labels e permissões")
async def list_roles_catalog():
    """Retorna catálogo de roles com labels e permissões."""
    return [
        {
            "id": "senior_lawyer",
            "label": "Advogado Sênior",
            "description": "Acesso total: casos, finanças, configurações, aprovações",
            "permissions": ["matters:*", "finance:*", "settings:*", "approvals:*", "users:read", "agents:*"],
            "color": "#0e3d5c",
        },
        {
            "id": "lawyer",
            "label": "Advogado",
            "description": "Gerencia casos, tarefas e aprova documentos",
            "permissions": ["matters:*", "tasks:*", "approvals:review", "documents:*"],
            "color": "#1a6fa5",
        },
        {
            "id": "intern",
            "label": "Estagiário",
            "description": "Visualiza casos, cria tarefas, sem acesso financeiro",
            "permissions": ["matters:read", "tasks:*", "documents:upload"],
            "color": "#3a9fcc",
        },
        {
            "id": "admin",
            "label": "Administrador",
            "description": "Gerencia usuários, integrações e configurações do escritório",
            "permissions": ["users:*", "settings:*", "integrations:*", "reports:*"],
            "color": "#1a6fa5",
        },
        {
            "id": "ai_analyst",
            "label": "Agente IA — Analyst",
            "description": "Agente automatizado de análise jurídica",
            "permissions": ["matters:read", "matters:update"],
            "color": "#6366f1",
        },
        {
            "id": "ai_drafter",
            "label": "Agente IA — Drafter",
            "description": "Agente automatizado de redação de minutas",
            "permissions": ["matters:read", "documents:upload", "approvals:create"],
            "color": "#8b5cf6",
        },
    ]


@router.get("/openapi-surface", summary="Superfície simplificada da API para uso interno")
async def get_openapi_surface():
    """Retorna superfície simplificada da API para uso interno."""
    return {
        "version": "v1",
        "resources": [
            {"path": "/auth", "methods": ["POST"], "description": "Autenticação e registro"},
            {"path": "/matters", "methods": ["GET", "POST", "PUT", "DELETE"], "description": "Casos jurídicos (aggregate root)"},
            {"path": "/matters/{id}/documents", "methods": ["GET", "POST", "DELETE"], "description": "Documentos do caso"},
            {"path": "/whatsapp", "methods": ["GET", "POST"], "description": "WhatsApp Business API"},
            {"path": "/flows", "methods": ["GET", "POST", "PUT", "DELETE"], "description": "Flow Builder e automações"},
            {"path": "/flows/templates", "methods": ["GET"], "description": "Biblioteca de templates"},
            {"path": "/flows/scheduled-messages", "methods": ["GET", "POST", "DELETE"], "description": "Mensagens agendadas"},
            {"path": "/finance", "methods": ["GET", "POST", "PATCH"], "description": "Módulo financeiro"},
            {"path": "/approvals", "methods": ["GET", "POST"], "description": "Fila de aprovações"},
            {"path": "/reports", "methods": ["GET"], "description": "Relatórios e métricas"},
            {"path": "/admin", "methods": ["GET", "POST", "PATCH"], "description": "Administração do workspace"},
            {"path": "/webhooks/stripe", "methods": ["POST"], "description": "Webhook Stripe"},
            {"path": "/webhooks/zapsign", "methods": ["POST"], "description": "Webhook ZapSign"},
        ],
    }
