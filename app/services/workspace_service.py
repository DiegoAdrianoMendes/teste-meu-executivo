"""
WorkspaceService — tenancy, memberships e setup inicial do SaaS.
"""
from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, List, Optional
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.cache import cache_delete, key_workspace_membership
from app.core.identity_surface import assert_workspace_identity
from app.core.rbac import (
    ROLE_DEFINITIONS,
    get_effective_permissions,
    get_permissions_for_role,
    normalize_permissions,
)
from app.core.security import hash_password_async
from app.core.workspace_access_packages import WORKSPACE_ACCESS_PACKAGES, WorkspaceAccessPackage
from app.models.auth_security import AuthSecurityEvent
from app.models.billing import PlatformTokenUsage, Subscription
from app.models.commercial_catalog import (
    CommercialOfferVersion,
    WorkspaceCommercialContract,
)
from app.models.configuration import ConfigDocument
from app.models.entitlement import EntitlementKey, PlanEntitlement, WorkspaceMonthlyUsage
from app.models.flow import Flow
from app.models.klm_operations import AreaBlueprint
from app.models.omnichannel_v6 import OmnichannelChannelConnection
from app.models.playbook import PlaybookLifecycleState, PlaybookVersionGovernance
from app.models.process_monitoring import MonitoredProcess, MonitoredProcessStatus
from app.models.storage_object import StorageObject
from app.models.user import AgentType, User, UserRole
from app.models.whatsapp import WaConnectionStatus, WhatsAppConnection
from app.models.whatsapp_campaign import WhatsAppCampaignRecipient
from app.models.workspace import Workspace, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.schemas.workspace import (
    IntegrationSetupStatus,
    MembershipCreate,
    MembershipUpdate,
    OnboardingStepStatusRead,
    PermissionRead,
    RoleDefinitionRead,
    WorkspaceAccessPackageRead,
    WorkspaceCreate,
    WorkspaceIntegrationSetupStatusRead,
    WorkspaceMembershipRead,
    WorkspaceNotificationRecipientRead,
    WorkspaceOnboardingStateRead,
    WorkspaceOnboardingUpdate,
    WorkspaceOwnerChannelRead,
    WorkspaceOwnerChannelUpdate,
    WorkspacePlanSummaryRead,
    WorkspaceRead,
    WorkspaceSetupRequest,
    WorkspaceSetupResponse,
    WorkspaceSummary,
    WorkspaceUpdate,
)
from app.services.commercial_catalog_service import CommercialCatalogService
from app.services.entitlement_service import EntitlementEnforcementService
from app.services.plan_policy_service import PlanPolicyService
from app.services.workspace_policy_service import (
    _normalize_notification_settings_shape,
    build_default_workspace_settings,
)


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return slug or "workspace"


GUIDED_ONBOARDING_STEPS: list[dict[str, Any]] = [
    {
        "id": "welcome",
        "title": "Boas-vindas e contexto",
        "description": "Entender rapidamente como o workspace vai operar e confirmar o setup inicial.",
        "required": True,
    },
    {
        "id": "whatsapp",
        "title": "Conectar WhatsApp",
        "description": "Obrigatório para ativar o fluxo operacional principal do MVP.",
        "required": True,
    },
    {
        "id": "areas",
        "title": "Escolher áreas da biblioteca",
        "description": "Ative as áreas jurídicas que o workspace vai operar primeiro.",
        "required": True,
    },
    {
        "id": "template",
        "title": "Escolher template inicial",
        "description": "Selecione pelo menos um flow/template permitido para as áreas ativas.",
        "required": True,
    },
    {
        "id": "follow_up",
        "title": "Configurar follow-up e intervenção humana",
        "description": "Defina a cadência inicial e onde a equipe entra no loop.",
        "required": True,
    },
    {
        "id": "playbook",
        "title": "Definir política comercial e honorários",
        "description": "Preencha o Playbook inicial que governará propostas, honorários e atuação dos agentes.",
        "required": True,
    },
    {
        "id": "integrations",
        "title": "Conectar integrações opcionais",
        "description": "Email, calendário, assinatura, financeiro e webhooks podem ficar para depois.",
        "required": False,
    },
    {
        "id": "review",
        "title": "Revisão final e ativação",
        "description": "Conferir pendências, revisar integrações e concluir o setup assistido.",
        "required": True,
    },
]

GUIDED_INTEGRATIONS: list[dict[str, Any]] = [
    {
        "key": "whatsapp",
        "label": "WhatsApp",
        "description": "Canal principal de atendimento e follow-up do MVP.",
        "required": True,
    },
    {
        "key": "email",
        "label": "Email",
        "description": "Confirmações, resumos e mensagens operacionais por email.",
        "required": False,
    },
    {
        "key": "calendar",
        "label": "Calendário",
        "description": "Agenda de reuniões, prazos e checkpoints do onboarding.",
        "required": False,
    },
    {
        "key": "signature",
        "label": "Assinatura",
        "description": "Contratos e aceite digital durante a ativação comercial.",
        "required": False,
    },
    {
        "key": "finance",
        "label": "Financeiro",
        "description": "Cobranças, lembretes e conciliação operacional.",
        "required": False,
    },
    {
        "key": "webhooks",
        "label": "Webhooks / API aberta",
        "description": "Integrações avançadas e callbacks para sistemas externos.",
        "required": False,
    },
]


class WorkspaceService:
    PLAN_OPERATIONAL_RECIPIENT_LIMITS: dict[str, int | None] = {
        "basic": 2,
        "pro": 6,
        "scale": None,
    }
    PLAN_CAMPAIGN_RECIPIENT_LIMITS: dict[str, int | None] = {
        "basic": None,
        "pro": None,
        "scale": None,
    }

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_workspace(self, workspace_id: UUID) -> Optional[Workspace]:
        result = await self.db.execute(
            select(Workspace)
            .options(selectinload(Workspace.subscription).selectinload(Subscription.plan))
            .where(Workspace.id == workspace_id, Workspace.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def list_workspaces(self) -> List[Workspace]:
        result = await self.db.execute(
            select(Workspace)
            .options(selectinload(Workspace.subscription).selectinload(Subscription.plan))
            .where(Workspace.deleted_at.is_(None))
            .order_by(Workspace.created_at.desc())
        )
        return list(result.scalars().all())

    async def create_workspace(self, data: WorkspaceCreate) -> Workspace:
        slug = slugify(data.slug or data.name)
        existing = await self.db.execute(select(Workspace).where(Workspace.slug == slug))
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "WKS-002", "message": "Já existe um workspace com este slug."},
            )

        workspace = Workspace(
            name=data.name,
            slug=slug,
            legal_name=data.legal_name,
            description=data.description,
            setup_mode=data.setup_mode,
            status=WorkspaceStatus.active,
            is_active=True,
            settings=self._merge_settings_with_defaults(data.settings, onboarding_mode=data.setup_mode.value),
        )
        self.db.add(workspace)
        await self.db.flush()
        return workspace

    async def update_workspace(self, workspace_id: UUID, data: WorkspaceUpdate) -> Workspace:
        workspace = await self.get_workspace(workspace_id)
        if not workspace:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WKS-001", "message": "Workspace não encontrado."},
            )
        update_data = data.model_dump(exclude_unset=True)
        if "slug" in update_data and update_data["slug"]:
            slug = slugify(update_data["slug"])
            existing = await self.db.execute(
                select(Workspace).where(Workspace.slug == slug, Workspace.id != workspace_id)
            )
            if existing.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "WKS-002", "message": "Já existe um workspace com este slug."},
                )
            update_data["slug"] = slug

        if "settings" in update_data and update_data["settings"] is not None:
            workspace.settings = self._merge_settings_with_defaults(
                {**(workspace.settings or {}), **update_data["settings"]},
                onboarding_mode=workspace.setup_mode.value,
            )
            update_data.pop("settings")

        for field, value in update_data.items():
            setattr(workspace, field, value)
        await self.db.flush()
        return workspace

    async def get_owner_channel(self, workspace_id: UUID) -> WorkspaceOwnerChannelRead:
        """Lê o canal do dono (WhatsApp) usado nas notificações de rotina do Hermes."""
        workspace = await self.get_workspace(workspace_id)
        if not workspace:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WKS-001", "message": "Workspace não encontrado."},
            )
        return WorkspaceOwnerChannelRead(
            owner_whatsapp_phone=workspace.owner_whatsapp_phone,
            configured=bool(workspace.owner_whatsapp_phone),
        )

    async def update_owner_channel(
        self, workspace_id: UUID, data: WorkspaceOwnerChannelUpdate
    ) -> WorkspaceOwnerChannelRead:
        """Configura (ou remove) o WhatsApp do dono. Não abre nenhum gate externo."""
        workspace = await self.get_workspace(workspace_id)
        if not workspace:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WKS-001", "message": "Workspace não encontrado."},
            )
        workspace.owner_whatsapp_phone = data.owner_whatsapp_phone
        await self.db.flush()
        return WorkspaceOwnerChannelRead(
            owner_whatsapp_phone=workspace.owner_whatsapp_phone,
            configured=bool(workspace.owner_whatsapp_phone),
        )

    async def list_memberships(self, workspace_id: UUID) -> List[WorkspaceMembership]:
        result = await self.db.execute(
            select(WorkspaceMembership)
            .options(
                selectinload(WorkspaceMembership.workspace),
                selectinload(WorkspaceMembership.user),
            )
            .where(WorkspaceMembership.workspace_id == workspace_id)
            .order_by(WorkspaceMembership.created_at.asc())
        )
        return list(result.scalars().all())

    async def create_membership(self, workspace_id: UUID, data: MembershipCreate) -> WorkspaceMembership:
        existing = await self.db.execute(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == data.user_id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "WKS-003", "message": "Usuário já pertence a este workspace."},
            )

        user = await self.db.scalar(
            select(User).where(User.id == data.user_id, User.deleted_at.is_(None))
        )
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WKS-006", "message": "Usuário não encontrado."},
            )
        assert_workspace_identity(user)
        if data.status in {MembershipStatus.active, MembershipStatus.invited}:
            await self._assert_membership_limit(
                workspace_id,
                agent_type=user.agent_type,
            )

        if data.is_default:
            await self._clear_default_memberships(data.user_id)

        membership = WorkspaceMembership(
            workspace_id=workspace_id,
            user_id=data.user_id,
            role=data.role,
            status=data.status,
            is_default=data.is_default,
            permission_grants=[],
            permission_denials=[],
        )
        self.db.add(membership)
        await self.db.flush()
        await self.db.refresh(membership)
        return membership

    async def update_membership(
        self,
        workspace_id: UUID,
        membership_id: UUID,
        data: MembershipUpdate,
    ) -> WorkspaceMembership:
        result = await self.db.execute(
            select(WorkspaceMembership)
            .options(selectinload(WorkspaceMembership.workspace), selectinload(WorkspaceMembership.user))
            .where(
                WorkspaceMembership.id == membership_id,
                WorkspaceMembership.workspace_id == workspace_id,
            )
        )
        membership = result.scalar_one_or_none()
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WKS-004", "message": "Membership não encontrada."},
            )
        update_data = data.model_dump(exclude_unset=True)
        next_role = update_data.get("role", membership.role)
        next_status = update_data.get("status", membership.status)
        if membership.role == WorkspaceRole.workspace_owner and (
            next_role != WorkspaceRole.workspace_owner or next_status != MembershipStatus.active
        ):
            other_owners = await self.db.scalar(
                select(func.count(WorkspaceMembership.id)).where(
                    WorkspaceMembership.workspace_id == membership.workspace_id,
                    WorkspaceMembership.id != membership.id,
                    WorkspaceMembership.role == WorkspaceRole.workspace_owner,
                    WorkspaceMembership.status == MembershipStatus.active,
                )
            )
            if not other_owners:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "WKS-009", "message": "O workspace precisa manter ao menos um titular ativo."},
                )
        if update_data.get("is_default"):
            await self._clear_default_memberships(membership.user_id, exclude_membership_id=membership.id)
        if (
            update_data.get("status") in {MembershipStatus.active, MembershipStatus.invited}
            and membership.status == MembershipStatus.suspended
        ):
            await self._assert_membership_limit(
                membership.workspace_id,
                exclude_membership_id=membership.id,
                agent_type=membership.user.agent_type,
            )

        for field, value in update_data.items():
            setattr(membership, field, value)
        await self.db.flush()
        await cache_delete(key_workspace_membership(membership.workspace_id, membership.user_id))
        return membership

    async def setup_workspace(self, data: WorkspaceSetupRequest) -> WorkspaceSetupResponse:
        normalized_admin_email = str(data.admin_email).strip().casefold()
        existing_user = await self.db.execute(
            select(User).where(
                func.lower(User.email) == normalized_admin_email,
                User.deleted_at.is_(None),
            )
        )
        if existing_user.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "AUTH-006", "message": "Já existe um usuário com este e-mail."},
            )

        workspace = await self.create_workspace(
            WorkspaceCreate(
                name=data.workspace_name,
                slug=data.workspace_slug,
                legal_name=data.legal_name,
                setup_mode=data.mode,
                settings=build_default_workspace_settings(
                    onboarding_mode=data.mode.value,
                    automation_preset=data.automation_preset,
                ),
            )
        )

        admin_user = User(
            name=data.admin_name,
            email=normalized_admin_email,
            hashed_password=await hash_password_async(data.admin_password),
            agent_type=AgentType.human,
            role=UserRole.admin,
            initials=self._make_initials(data.admin_name),
            is_active=True,
            email_verified_at=datetime.now(UTC),
        )
        self.db.add(admin_user)
        await self.db.flush()

        membership = WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=admin_user.id,
            role=WorkspaceRole.workspace_owner,
            status=MembershipStatus.active,
            is_default=True,
            access_package="office_owner",
        )
        self.db.add(membership)
        await self.db.flush()
        self.db.add(
            AuthSecurityEvent(
                event_type="trusted_setup_email_verified",
                outcome="success",
                actor_user_id=admin_user.id,
                subject_user_id=admin_user.id,
                details={
                    "source": "workspace_setup_token",
                    "workspace_id": str(workspace.id),
                    "setup_mode": data.mode.value,
                },
            )
        )
        await self.db.flush()
        await self.db.refresh(workspace)
        await self.db.refresh(membership)

        return WorkspaceSetupResponse(
            workspace=await self.to_workspace_read(workspace),
            membership=self.to_membership_read(membership),
        )

    def list_role_catalog(self) -> List[RoleDefinitionRead]:
        return [
            RoleDefinitionRead(
                id=definition.id,
                label=definition.label,
                description=definition.description,
                permissions=[
                    PermissionRead(module=p.module, action=p.action, scope=p.scope)
                    for p in definition.permissions
                ],
            )
            for definition in ROLE_DEFINITIONS.values()
        ]

    @staticmethod
    def _to_access_package_read(package: "WorkspaceAccessPackage") -> WorkspaceAccessPackageRead:
        permissions = get_effective_permissions(
            package.workspace_role,
            package.permission_grants,
            package.permission_denials,
        )
        return WorkspaceAccessPackageRead(
            key=package.key,
            label=package.label,
            description=package.description,
            workspace_role=package.workspace_role,
            permission_grants=[
                PermissionRead(module=item.module, action=item.action, scope=item.scope)
                for item in package.permission_grants
            ],
            permission_denials=[
                PermissionRead(module=item.module, action=item.action, scope=item.scope)
                for item in package.permission_denials
            ],
            permissions=[
                PermissionRead(module=item.module, action=item.action, scope=item.scope)
                for item in permissions
            ],
            sensitive_data_policy=package.sensitive_data_policy,
        )

    def list_access_package_catalog(self) -> List[WorkspaceAccessPackageRead]:
        return [self._to_access_package_read(package) for package in WORKSPACE_ACCESS_PACKAGES.values()]

    async def get_membership_access_package(self, membership_id: UUID) -> Optional[WorkspaceAccessPackageRead]:
        """Retorna o pacote de acesso legível do próprio usuário (self-service).

        Não exige permissão de administração: qualquer usuário autenticado pode
        consultar o que o próprio acesso inclui, para a seção "O que meu acesso
        inclui" da página de preferências.
        """
        membership = await self.db.get(WorkspaceMembership, membership_id)
        if membership is None:
            return None
        package = WORKSPACE_ACCESS_PACKAGES.get(membership.access_package or "")
        if package is not None:
            return self._to_access_package_read(package)
        # Sem pacote pronto associado (acesso customizado): sintetiza um pacote
        # "virtual" a partir do papel + grants/denials efetivos da própria membership,
        # para que a UI sempre tenha algo legível para mostrar.
        grants = normalize_permissions(membership.permission_grants)
        denials = normalize_permissions(membership.permission_denials)
        permissions = get_effective_permissions(membership.role, grants, denials)
        return WorkspaceAccessPackageRead(
            key="custom",
            label=f"Acesso personalizado ({membership.role.value})",
            description="Este acesso foi configurado individualmente pelo administrador do workspace, fora dos pacotes prontos.",
            workspace_role=membership.role,
            permission_grants=[PermissionRead(module=p.module, action=p.action, scope=p.scope) for p in grants],
            permission_denials=[PermissionRead(module=p.module, action=p.action, scope=p.scope) for p in denials],
            permissions=[PermissionRead(module=p.module, action=p.action, scope=p.scope) for p in permissions],
            sensitive_data_policy="hidden",
        )

    def to_workspace_summary(self, workspace: Workspace) -> WorkspaceSummary:
        onboarding_state = self._get_guided_onboarding_state(workspace.settings or {}, workspace.setup_mode.value)
        return WorkspaceSummary(
            id=workspace.id,
            name=workspace.name,
            slug=workspace.slug,
            status=workspace.status,
            is_active=workspace.is_active,
            onboarding_completed=bool(onboarding_state.get("isCompleted", workspace.onboarding_completed)),
            onboarding_progress=int(onboarding_state.get("progressPercent", 0)),
            next_onboarding_step=onboarding_state.get("nextStep"),
        )

    async def to_workspace_read(self, workspace: Workspace) -> WorkspaceRead:
        plan_summary = await self._build_plan_summary(workspace)
        return WorkspaceRead(
            id=workspace.id,
            name=workspace.name,
            slug=workspace.slug,
            legal_name=workspace.legal_name,
            description=workspace.description,
            status=workspace.status,
            setup_mode=workspace.setup_mode,
            is_active=workspace.is_active,
            settings=workspace.settings or {},
            plan_summary=plan_summary,
        )

    async def get_workspace_onboarding(self, workspace_id: UUID) -> WorkspaceOnboardingStateRead:
        workspace = await self.get_workspace(workspace_id)
        if not workspace:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WKS-001", "message": "Workspace não encontrado."},
            )

        from app.services.workspace_policy_service import WorkspacePolicyService

        operations = await WorkspacePolicyService(self.db).get_workspace_operations(workspace_id)
        return await self._build_onboarding_state(workspace, operations.area_activations)

    async def update_workspace_onboarding(
        self,
        workspace_id: UUID,
        payload: WorkspaceOnboardingUpdate,
    ) -> WorkspaceOnboardingStateRead:
        workspace = await self.get_workspace(workspace_id)
        if not workspace:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WKS-001", "message": "Workspace não encontrado."},
            )

        settings_data = self._merge_settings_with_defaults(workspace.settings or {}, workspace.setup_mode.value)
        onboarding = self._get_guided_onboarding_state(settings_data, workspace.setup_mode.value)

        if payload.current_step:
            onboarding["currentStep"] = payload.current_step

        if payload.mark_step_completed == "playbook":
            playbook_id = payload.playbook_id or onboarding.get("playbookId")
            try:
                normalized_playbook_id = UUID(str(playbook_id))
            except (TypeError, ValueError, AttributeError):
                normalized_playbook_id = None
            if normalized_playbook_id is None or not await self._has_onboarding_playbook(
                workspace_id, normalized_playbook_id
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "WKS-009",
                        "message": "Publique um Playbook deste workspace antes de concluir a política comercial.",
                    },
                )
            onboarding["playbookId"] = str(normalized_playbook_id)

        if payload.mark_step_completed and payload.mark_step_completed not in onboarding["completedSteps"]:
            onboarding["completedSteps"].append(payload.mark_step_completed)
            onboarding["skippedSteps"] = [
                step_id for step_id in onboarding["skippedSteps"]
                if step_id != payload.mark_step_completed
            ]

        if payload.skip_step:
            step_meta = next((step for step in GUIDED_ONBOARDING_STEPS if step["id"] == payload.skip_step), None)
            if step_meta and step_meta["required"]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"code": "WKS-007", "message": "Este passo é obrigatório e não pode ser ignorado."},
                )
            if payload.skip_step not in onboarding["skippedSteps"]:
                onboarding["skippedSteps"].append(payload.skip_step)

        if payload.integrations is not None:
            whatsapp_marked_connected = any(
                integration.key == "whatsapp" and integration.status == "connected"
                for integration in payload.integrations
            )
            if whatsapp_marked_connected and not await self._has_ready_native_whatsapp_connection(
                workspace_id
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "WKS-008",
                        "message": (
                            "Valide uma conexão WhatsApp oficial no registro nativo "
                            "antes de concluir esta etapa."
                        ),
                    },
                )
            current_integrations = onboarding.get("integrations", {})
            for integration in payload.integrations:
                current_integrations[integration.key] = integration.status
            onboarding["integrations"] = current_integrations

        if payload.review_completed is not None:
            onboarding["reviewCompleted"] = bool(payload.review_completed)
            if payload.review_completed and "review" not in onboarding["completedSteps"]:
                onboarding["completedSteps"].append("review")

        if payload.playbook_id is not None:
            if not await self._has_onboarding_playbook(workspace_id, payload.playbook_id):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "WKS-009",
                        "message": "Playbook publicado não encontrado neste workspace.",
                    },
                )
            onboarding["playbookId"] = str(payload.playbook_id)

        if payload.area_activations is not None:
            from app.schemas.control_plane import (
                WorkspaceAreaActivationUpdate,
                WorkspaceOperationsUpdate,
            )
            from app.services.workspace_policy_service import WorkspacePolicyService

            area_payload = WorkspaceOperationsUpdate(
                area_activations=[WorkspaceAreaActivationUpdate.model_validate(item) for item in payload.area_activations]
            )
            await WorkspacePolicyService(self.db).update_workspace_operations(workspace_id, area_payload)
            settings_data = self._merge_settings_with_defaults(workspace.settings or {}, workspace.setup_mode.value)

        if payload.notification_recipients is not None:
            await self._assert_operational_recipient_limit(workspace, payload.notification_recipients)
            notifications = _normalize_notification_settings_shape(settings_data.get("notifications", {}))
            notifications["recipients"] = [
                {
                    "id": item.id or str(uuid4()),
                    "name": item.name,
                    "email": item.email,
                    "phone": item.phone,
                    "role": item.role,
                    "user_id": str(item.user_id) if item.user_id else None,
                }
                for item in payload.notification_recipients
                if item.role != "workspace_owner"
            ]
            settings_data["notifications"] = notifications

        settings_data["guidedOnboarding"] = onboarding
        workspace.settings = settings_data

        state = await self.get_workspace_onboarding(workspace_id)
        workspace.onboarding_completed = state.onboarding_completed
        settings_data["guidedOnboarding"] = self._persisted_onboarding_payload(state)
        workspace.settings = settings_data
        await self.db.flush()
        return state

    def to_membership_read(self, membership: WorkspaceMembership) -> WorkspaceMembershipRead:
        role_permissions = get_permissions_for_role(membership.role)
        grants = normalize_permissions(membership.permission_grants)
        denials = normalize_permissions(membership.permission_denials)
        permissions = get_effective_permissions(membership.role, grants, denials)
        return WorkspaceMembershipRead(
            id=membership.id,
            workspace_id=membership.workspace_id,
            user_id=membership.user_id,
            role=membership.role,
            status=membership.status,
            is_default=membership.is_default,
            access_package=membership.access_package,
            workspace=self.to_workspace_summary(membership.workspace),
            role_permissions=[
                PermissionRead(module=p.module, action=p.action, scope=p.scope)
                for p in role_permissions
            ],
            permission_grants=[
                PermissionRead(module=p.module, action=p.action, scope=p.scope)
                for p in grants
            ],
            permission_denials=[
                PermissionRead(module=p.module, action=p.action, scope=p.scope)
                for p in denials
            ],
            permissions=[
                PermissionRead(module=p.module, action=p.action, scope=p.scope)
                for p in permissions
            ],
            can_view_sensitive_lead_data=any(
                p.module == "lead_data" and p.action == "view" for p in permissions
            ),
            can_edit_sensitive_lead_data=any(
                p.module == "lead_data" and p.action == "edit" for p in permissions
            ),
        )

    @staticmethod
    def _make_initials(name: str) -> str:
        parts = name.strip().split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[-1][0]).upper()
        return name[:2].upper()

    async def _clear_default_memberships(
        self,
        user_id: UUID,
        exclude_membership_id: Optional[UUID] = None,
    ) -> None:
        result = await self.db.execute(
            select(WorkspaceMembership).where(WorkspaceMembership.user_id == user_id)
        )
        for membership in result.scalars().all():
            if exclude_membership_id and membership.id == exclude_membership_id:
                continue
            membership.is_default = False

    async def _assert_membership_limit(
        self,
        workspace_id: UUID,
        exclude_membership_id: Optional[UUID] = None,
        *,
        agent_type: AgentType = AgentType.human,
    ) -> None:
        async def load_current_quantity() -> int:
            conditions = [
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.status.in_(
                    [MembershipStatus.active, MembershipStatus.invited]
                ),
                User.agent_type == agent_type,
                User.deleted_at.is_(None),
            ]
            if exclude_membership_id:
                conditions.append(WorkspaceMembership.id != exclude_membership_id)
            value = await self.db.scalar(
                select(func.count(WorkspaceMembership.id))
                .join(User, User.id == WorkspaceMembership.user_id)
                .where(*conditions)
            )
            return int(value or 0)

        entitlement = (
            EntitlementKey.users
            if agent_type == AgentType.human
            else EntitlementKey.agents
        )
        await EntitlementEnforcementService(self.db).enforce_stock(
            workspace_id=workspace_id,
            entitlement=entitlement,
            load_current_quantity=load_current_quantity,
        )

    async def _build_plan_summary(self, workspace: Workspace) -> Optional[WorkspacePlanSummaryRead]:
        canonical_contract_id = await self.db.scalar(
            select(WorkspaceCommercialContract.id).where(
                WorkspaceCommercialContract.workspace_id == workspace.id
            )
        )
        canonical_contract = None
        canonical_manifest = None
        hydrated_workspace = workspace
        subscription = None
        plan = None
        features: dict[str, Any] = {}
        entitlements: dict[EntitlementKey, PlanEntitlement] = {}
        versioned_entitlements: dict[EntitlementKey, Any] = {}
        versioned_parameters = None
        summary_tier: str | None = None
        summary_name: str | None = None
        summary_status: str | None = None

        if canonical_contract_id is not None:
            # Once a canonical contract exists it is the only commercial
            # authority for this surface. Legacy Plan/PlanPolicy rows remain a
            # one-way projection and must never overwrite a pinned contract,
            # Legal-only account, bonus or workspace override.
            catalog = CommercialCatalogService(self.db)
            canonical_contract = await catalog.get_workspace_contract(workspace.id)
            canonical_manifest = await catalog.resolve_effective_manifest(workspace.id)
            active_lines = [
                line
                for line in canonical_contract.lines
                if line.status.value == "active" and line.sku in canonical_manifest.active_products
            ]
            policy_names = dict(
                (
                    await self.db.execute(
                        select(CommercialOfferVersion.id, CommercialOfferVersion.name).where(
                            CommercialOfferVersion.id.in_(
                                [line.policy_version_id for line in active_lines]
                            )
                        )
                    )
                ).all()
            ) if active_lines else {}
            fallback_names = {
                "crm_start": "Start",
                "crm_advanced": "Advanced",
                "crm_evolution": "Evolution",
                "legal_360": "Jurídico 360",
            }
            summary_name = " + ".join(
                policy_names.get(line.policy_version_id, fallback_names.get(line.sku, line.sku))
                for line in active_lines
            ) or "Contrato sem linha ativa"
            core_line = next(
                (line for line in active_lines if line.offer_family.value == "crm"),
                None,
            )
            has_legal = any(line.offer_family.value == "legal" for line in active_lines)
            summary_tier = (
                core_line.sku.removeprefix("crm_")
                if core_line is not None
                else ("legal_360" if has_legal else None)
            )
            summary_status = canonical_contract.status.value
        else:
            if "subscription" not in hydrated_workspace.__dict__:
                result = await self.db.execute(
                    select(Workspace)
                    .options(selectinload(Workspace.subscription).selectinload(Subscription.plan))
                    .where(Workspace.id == workspace.id, Workspace.deleted_at.is_(None))
                )
                hydrated_workspace = result.scalar_one_or_none() or workspace

            subscription = hydrated_workspace.__dict__.get("subscription")
            plan = subscription.plan if subscription else None
            if not subscription and not plan:
                return None

            features = {**(plan.features or {})} if plan and plan.features else {}
            entitlement_rows = (
                list(
                    (
                        await self.db.scalars(
                            select(PlanEntitlement).where(PlanEntitlement.plan_id == plan.id)
                        )
                    ).all()
                )
                if plan is not None
                else []
            )
            entitlements = {row.entitlement: row for row in entitlement_rows}
            # Legacy-only accounts continue to honor their immutable published
            # PlanPolicy assignment until they receive a canonical contract.
            versioned_resolution = await PlanPolicyService(self.db).resolve_versioned_workspace(
                workspace_id=workspace.id,
            )
            versioned_entitlements = (
                versioned_resolution.entitlements if versioned_resolution is not None else {}
            )
            versioned_parameters = (
                versioned_resolution.parameters if versioned_resolution is not None else None
            )
            summary_tier = (
                self._normalize_plan_tier(getattr(plan.tier, "value", None)) if plan else None
            )
            summary_name = (
                self._normalize_plan_name(plan.name, getattr(plan.tier, "value", None))
                if plan
                else None
            )
            summary_status = getattr(subscription.status, "value", None) if subscription else None

        max_users = plan.max_users if plan else None

        def entitlement_limit(key: EntitlementKey, fallback: Any) -> Any:
            if canonical_manifest is not None:
                capability = canonical_manifest.capabilities[key]
                return capability.limit if capability.enabled else 0
            versioned = versioned_entitlements.get(key)
            if versioned is not None:
                return versioned.limit_quantity if versioned.enabled else 0
            row = entitlements.get(key)
            if row is None:
                return fallback
            return row.limit_quantity if row.enabled else 0

        def entitlement_enabled(key: EntitlementKey, fallback: bool) -> bool:
            if canonical_manifest is not None:
                return bool(canonical_manifest.capabilities[key].permitted)
            versioned = versioned_entitlements.get(key)
            if versioned is not None:
                return bool(versioned.enabled)
            row = entitlements.get(key)
            return bool(row.enabled) if row is not None else fallback

        def parameter_limit(field_name: str, fallback: Any) -> Any:
            if canonical_manifest is not None:
                return None
            if versioned_parameters is not None:
                return getattr(versioned_parameters, field_name)
            return fallback

        max_users = entitlement_limit(EntitlementKey.users, max_users)
        max_connected_whatsapps = entitlement_limit(
            EntitlementKey.whatsapp_numbers,
            features.get("maxConnectedWhatsapps"),
        )
        max_workspaces = (
            entitlement_limit(EntitlementKey.workspaces, None)
            if canonical_manifest is not None
            else parameter_limit("max_workspaces", features.get("maxWorkspaces"))
        )
        max_ai_conversations_per_month = entitlement_limit(
            EntitlementKey.ai_conversations,
            features.get("maxAiConversationsPerMonth"),
        )
        max_audio_minutes_per_month = entitlement_limit(
            EntitlementKey.audio_minutes,
            features.get("maxAudioMinutesPerMonth"),
        )
        max_storage_bytes = entitlement_limit(
            EntitlementKey.storage_bytes,
            features.get("maxStorageBytes"),
        )
        max_files = entitlement_limit(
            EntitlementKey.files,
            features.get("maxFilesPerMonth"),
        )
        max_files_per_conversation = parameter_limit(
            "max_files_per_conversation",
            features.get("maxFilesPerConversation"),
        )
        automation_limit = entitlement_limit(
            EntitlementKey.automations,
            features.get("automationLimit"),
        )
        max_ai_agents = entitlement_limit(
            EntitlementKey.agents,
            plan.max_ai_agents if plan else None,
        )
        max_legal_areas = entitlement_limit(
            EntitlementKey.legal_areas,
            features.get("maxLegalAreas"),
        )
        max_operational_recipients = parameter_limit(
            "max_operational_recipients",
            self._resolve_operational_recipient_limit(subscription, features),
        )
        max_campaign_recipients_per_month = (
            (
                canonical_manifest.capabilities[
                    EntitlementKey.campaign_recipients
                ].limit
                if canonical_manifest.capabilities[
                    EntitlementKey.campaign_recipients
                ].permitted
                else None
            )
            if canonical_manifest is not None
            else parameter_limit(
                "max_campaign_recipients_per_month",
                self._resolve_campaign_recipient_limit(subscription, features),
            )
        )
        support_tier = features.get("supportTier")
        result = await self.db.execute(
            select(func.count())
            .select_from(WorkspaceMembership)
            .where(
                WorkspaceMembership.workspace_id == workspace.id,
                WorkspaceMembership.status.in_([MembershipStatus.active, MembershipStatus.invited]),
            )
        )
        seats_used = int(result.scalar() or 0)
        seats_remaining = None if max_users is None else max(max_users - seats_used, 0)

        whatsapps_result = await self.db.execute(
            select(func.count())
            .select_from(WhatsAppConnection)
            .where(
                WhatsAppConnection.workspace_id == workspace.id,
                WhatsAppConnection.status == WaConnectionStatus.connected,
            )
        )
        connected_whatsapps_used = int(whatsapps_result.scalar() or 0)
        connected_whatsapps_remaining = (
            None
            if max_connected_whatsapps is None
            else max(int(max_connected_whatsapps) - connected_whatsapps_used, 0)
        )

        automations_result = await self.db.execute(
            select(func.count())
            .select_from(Flow)
            .where(
                Flow.workspace_id == workspace.id,
                Flow.deleted_at.is_(None),
                Flow.is_active.is_(True),
            )
        )
        automations_used = int(automations_result.scalar() or 0)
        automations_remaining = (
            None if automation_limit is None else max(int(automation_limit) - automations_used, 0)
        )

        workspace_settings = hydrated_workspace.settings or {}
        notifications = _normalize_notification_settings_shape(workspace_settings.get("notifications", {}))
        operational_recipients_used = len(notifications.get("recipients", []))
        operational_recipients_remaining = (
            None
            if max_operational_recipients is None
            else max(int(max_operational_recipients) - operational_recipients_used, 0)
        )

        now = datetime.now(UTC)
        month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
        campaign_recipients_used_this_month = int(
            await self.db.scalar(
                select(func.count(WhatsAppCampaignRecipient.id)).where(
                    WhatsAppCampaignRecipient.workspace_id == workspace.id,
                    WhatsAppCampaignRecipient.sent_at.is_not(None),
                    WhatsAppCampaignRecipient.sent_at >= month_start,
                    WhatsAppCampaignRecipient.sent_at <= now,
                )
            )
            or 0
        )
        campaign_recipients_remaining_this_month = (
            None
            if max_campaign_recipients_per_month is None
            else max(
                int(max_campaign_recipients_per_month)
                - campaign_recipients_used_this_month,
                0,
            )
        )
        monthly_usage_rows = list(
            (
                await self.db.scalars(
                    select(WorkspaceMonthlyUsage).where(
                        WorkspaceMonthlyUsage.workspace_id == workspace.id,
                        WorkspaceMonthlyUsage.period_start == month_start.date(),
                        WorkspaceMonthlyUsage.entitlement.in_(
                            [
                                EntitlementKey.ai_conversations,
                                EntitlementKey.audio_minutes,
                            ]
                        ),
                    )
                )
            ).all()
        )
        monthly_usage = {row.entitlement: row for row in monthly_usage_rows}
        ai_usage = monthly_usage.get(EntitlementKey.ai_conversations)
        audio_usage = monthly_usage.get(EntitlementKey.audio_minutes)
        ai_usage_result = await self.db.execute(
            select(func.sum(PlatformTokenUsage.total_runs)).where(
                PlatformTokenUsage.workspace_id == workspace.id,
                PlatformTokenUsage.usage_date >= month_start,
            )
        )
        ai_conversations_used_this_month = (
            ai_usage.consumed_quantity + ai_usage.reserved_quantity
            if ai_usage is not None
            else int(ai_usage_result.scalar() or 0)
        )
        audio_minutes_used_this_month = (
            audio_usage.consumed_quantity + audio_usage.reserved_quantity
            if audio_usage is not None
            else 0
        )
        ai_conversations_remaining_this_month = (
            None
            if max_ai_conversations_per_month is None
            else max(int(max_ai_conversations_per_month) - ai_conversations_used_this_month, 0)
        )
        audio_minutes_remaining_this_month = (
            None
            if max_audio_minutes_per_month is None
            else max(int(max_audio_minutes_per_month) - audio_minutes_used_this_month, 0)
        )

        storage_result = (
            await self.db.execute(
                select(
                    func.count(StorageObject.id),
                    func.coalesce(func.sum(StorageObject.byte_size), 0),
                ).where(
                    StorageObject.workspace_id == workspace.id,
                    StorageObject.deleted_at.is_(None),
                    StorageObject.byte_size.is_not(None),
                )
            )
        ).one()
        files_used = int(storage_result[0] or 0)
        storage_bytes_used = int(storage_result[1] or 0)
        files_remaining = (
            None if max_files is None else max(int(max_files) - files_used, 0)
        )
        storage_bytes_remaining = (
            None
            if max_storage_bytes is None
            else max(int(max_storage_bytes) - storage_bytes_used, 0)
        )

        ai_agents_used = 0
        stage_policies = (workspace_settings.get("automation") or {}).get("stagePolicies") or {}
        agent_keys = {
            policy.get("agentKey")
            for policy in stage_policies.values()
            if policy.get("agentKey")
        }
        ai_agents_used += len(agent_keys)
        ai_agents_remaining = (
            None
            if max_ai_agents is None
            else max(int(max_ai_agents) - ai_agents_used, 0)
        )

        legal_areas_used = int(
            await self.db.scalar(
                select(func.count(AreaBlueprint.id)).where(
                    AreaBlueprint.workspace_id == workspace.id,
                    AreaBlueprint.is_active.is_(True),
                )
            )
            or 0
        )
        legal_areas_remaining = (
            None
            if max_legal_areas is None
            else max(int(max_legal_areas) - legal_areas_used, 0)
        )

        # Módulo processual (add-on) — mesma contagem usada no gate real
        # (`ProcessMonitoringService._enforce_quota`): só processos `ativo`
        # consomem a quota de estoque.
        max_monitored_processes = entitlement_limit(
            EntitlementKey.monitored_processes,
            features.get("maxMonitoredProcesses", 0),
        )
        monitored_processes_used = int(
            await self.db.scalar(
                select(func.count(MonitoredProcess.id)).where(
                    MonitoredProcess.workspace_id == workspace.id,
                    MonitoredProcess.status == MonitoredProcessStatus.ativo,
                )
            )
            or 0
        )
        monitored_processes_remaining = (
            None
            if max_monitored_processes is None
            else max(int(max_monitored_processes) - monitored_processes_used, 0)
        )

        return WorkspacePlanSummaryRead(
            tier=summary_tier,
            name=summary_name,
            subscription_status=summary_status,
            max_users=max_users,
            max_workspaces=max_workspaces,
            max_connected_whatsapps=max_connected_whatsapps,
            max_ai_agents=max_ai_agents,
            max_legal_areas=max_legal_areas,
            max_ai_conversations_per_month=max_ai_conversations_per_month,
            max_audio_minutes_per_month=max_audio_minutes_per_month,
            max_storage_bytes=max_storage_bytes,
            max_files=max_files,
            max_files_per_conversation=max_files_per_conversation,
            automation_limit=automation_limit,
            max_operational_recipients=max_operational_recipients,
            max_campaign_recipients_per_month=max_campaign_recipients_per_month,
            seats_used=seats_used,
            seats_remaining=seats_remaining,
            connected_whatsapps_used=connected_whatsapps_used,
            connected_whatsapps_remaining=connected_whatsapps_remaining,
            ai_agents_used=ai_agents_used,
            ai_agents_remaining=ai_agents_remaining,
            legal_areas_used=legal_areas_used,
            legal_areas_remaining=legal_areas_remaining,
            ai_conversations_used_this_month=ai_conversations_used_this_month,
            ai_conversations_remaining_this_month=ai_conversations_remaining_this_month,
            audio_minutes_used_this_month=audio_minutes_used_this_month,
            audio_minutes_remaining_this_month=audio_minutes_remaining_this_month,
            storage_bytes_used=storage_bytes_used,
            storage_bytes_remaining=storage_bytes_remaining,
            files_used=files_used,
            files_remaining=files_remaining,
            automations_used=automations_used,
            automations_remaining=automations_remaining,
            operational_recipients_used=operational_recipients_used,
            operational_recipients_remaining=operational_recipients_remaining,
            campaign_recipients_used_this_month=campaign_recipients_used_this_month,
            campaign_recipients_remaining_this_month=(
                campaign_recipients_remaining_this_month
            ),
            max_monitored_processes=max_monitored_processes,
            monitored_processes_used=monitored_processes_used,
            monitored_processes_remaining=monitored_processes_remaining,
            support_tier=support_tier,
            has_digital_signature=entitlement_enabled(
                EntitlementKey.digital_signature,
                bool(features.get("digitalSignature", False)),
            ),
            has_calendar=entitlement_enabled(
                EntitlementKey.calendar,
                bool(features.get("calendar", False)),
            ),
            has_email=entitlement_enabled(
                EntitlementKey.email,
                bool(features.get("email", False)),
            ),
            has_webhooks=entitlement_enabled(
                EntitlementKey.webhooks,
                bool(features.get("webhooks", False)),
            ),
            has_advanced_audit=entitlement_enabled(
                EntitlementKey.advanced_audit,
                bool(features.get("advancedAudit", False)),
            ),
            has_success_manager=entitlement_enabled(
                EntitlementKey.success_manager,
                bool(features.get("successManager", False)),
            ),
            is_at_limit=any(
                value == 0
                for value in (
                    seats_remaining,
                    connected_whatsapps_remaining,
                    ai_agents_remaining,
                    legal_areas_remaining,
                    ai_conversations_remaining_this_month,
                    audio_minutes_remaining_this_month,
                    storage_bytes_remaining,
                    files_remaining,
                    automations_remaining,
                    operational_recipients_remaining,
                    campaign_recipients_remaining_this_month,
                )
                if value is not None
            ),
        )

    @staticmethod
    def _normalize_plan_tier(tier: Optional[str]) -> Optional[str]:
        mapping = {
            "starter": "basic",
            "pro": "pro",
            "business": "scale",
            "enterprise": "scale",
            "free": "basic",
        }
        return mapping.get(tier or "", tier)

    @staticmethod
    def _normalize_plan_name(name: Optional[str], tier: Optional[str]) -> Optional[str]:
        normalized_tier = WorkspaceService._normalize_plan_tier(tier)
        fallback = {
            "basic": "Basic",
            "pro": "Pro",
            "scale": "Scale",
        }
        if not name:
            return fallback.get(normalized_tier or "", None)
        if name.lower() in {"starter", "basic"}:
            return "Basic"
        if name.lower() == "business":
            return "Scale"
        return name

    @staticmethod
    def _merge_settings_with_defaults(settings_data: Optional[dict], onboarding_mode: str) -> dict:
        base = build_default_workspace_settings(onboarding_mode=onboarding_mode)
        if not settings_data:
            base["guidedOnboarding"] = WorkspaceService._build_default_guided_onboarding_state()
            return base

        base.update({key: value for key, value in settings_data.items() if key != "automation"})
        base.setdefault("automation", {})
        base["automation"].setdefault("stagePolicies", {})
        base["guidedOnboarding"] = WorkspaceService._get_guided_onboarding_state(
            settings_data,
            onboarding_mode,
        )

        for stage_key, stage_value in settings_data.get("automation", {}).get("stagePolicies", {}).items():
            current = base["automation"]["stagePolicies"].get(stage_key, {})
            current.update(stage_value)
            base["automation"]["stagePolicies"][stage_key] = current

        return base

    @staticmethod
    def _build_default_guided_onboarding_state() -> dict[str, Any]:
        return {
            "currentStep": "welcome",
            "completedSteps": [],
            "skippedSteps": [],
            "reviewCompleted": False,
            "playbookId": None,
            "notificationRecipients": [],
            "integrations": {
                item["key"]: "not_started"
                for item in GUIDED_INTEGRATIONS
            },
        }

    @staticmethod
    def _get_guided_onboarding_state(settings_data: dict[str, Any], onboarding_mode: str) -> dict[str, Any]:
        state = deepcopy(WorkspaceService._build_default_guided_onboarding_state())
        raw = (settings_data or {}).get("guidedOnboarding") or {}
        state["currentStep"] = raw.get("currentStep") or state["currentStep"]
        state["completedSteps"] = [
            step_id for step_id in raw.get("completedSteps", [])
            if any(step["id"] == step_id for step in GUIDED_ONBOARDING_STEPS)
        ]
        state["skippedSteps"] = [
            step_id for step_id in raw.get("skippedSteps", [])
            if any(step["id"] == step_id for step in GUIDED_ONBOARDING_STEPS)
        ]
        state["reviewCompleted"] = bool(raw.get("reviewCompleted", False))
        state["playbookId"] = raw.get("playbookId") or None
        integrations = state["integrations"]
        integrations.update({
            key: value
            for key, value in (raw.get("integrations") or {}).items()
            if key in integrations and value in {"not_started", "connected", "skipped"}
        })
        state["notificationRecipients"] = raw.get("notificationRecipients", []) or []
        return WorkspaceService._compute_onboarding_progress(
            state,
            WorkspaceService._operations_snapshot(settings_data, onboarding_mode),
        )

    @staticmethod
    def _operations_snapshot(settings_data: dict[str, Any], onboarding_mode: str) -> list[dict[str, Any]]:
        from app.services.workspace_policy_service import build_default_workspace_settings

        merged = build_default_workspace_settings(onboarding_mode=onboarding_mode)
        merged.update({key: value for key, value in (settings_data or {}).items() if key != "automation"})
        operations = merged.get("workspaceOperations") or {}
        selections = operations.get("areaSelections") or {}
        active_keys = set(operations.get("activeAreaKeys") or [])
        rows: list[dict[str, Any]] = []
        for key, selection in selections.items():
            rows.append(
                {
                    "key": key,
                    "enabled": bool(selection.get("enabled", key in active_keys)),
                    "selected_flow_template_id": selection.get("selectedFlowTemplateId"),
                    "follow_up_profile": selection.get("followUpProfile"),
                    "human_intervention_mode": selection.get("humanInterventionMode"),
                }
            )
        return rows

    async def _build_onboarding_state(
        self,
        workspace: Workspace,
        area_activations: list[Any],
    ) -> WorkspaceOnboardingStateRead:
        settings_data = self._merge_settings_with_defaults(
            workspace.settings or {}, workspace.setup_mode.value
        )
        base_state = self._get_guided_onboarding_state(
            settings_data, workspace.setup_mode.value
        )
        whatsapp_state_reconciled = False
        if (
            base_state.get("integrations", {}).get("whatsapp") == "connected"
            and not await self._has_ready_native_whatsapp_connection(workspace.id)
        ):
            base_state["integrations"]["whatsapp"] = "not_started"
            base_state["currentStep"] = "whatsapp"
            base_state["completedSteps"] = [
                step_id
                for step_id in base_state.get("completedSteps", [])
                if step_id != "whatsapp"
            ]
            whatsapp_state_reconciled = True
        computed = self._compute_onboarding_progress(base_state, area_activations)
        notification_recipients = await self._resolve_notification_recipients(workspace)
        step_rows: list[OnboardingStepStatusRead] = []
        completed_steps: list[str] = computed["completedSteps"]
        skipped_steps: list[str] = computed["skippedSteps"]
        pending_required_steps: list[str] = []

        status_map = computed["statusMap"]
        for step in GUIDED_ONBOARDING_STEPS:
            step_status = status_map[step["id"]]
            if step["required"] and step_status != "completed":
                pending_required_steps.append(step["id"])
            step_rows.append(
                OnboardingStepStatusRead(
                    id=step["id"],
                    title=step["title"],
                    description=step["description"],
                    required=bool(step["required"]),
                    completed=step_status == "completed",
                    skipped=step_status == "skipped",
                    blocked=step_status == "blocked",
                    status=step_status,
                )
            )

        state = WorkspaceOnboardingStateRead(
            current_step=computed["currentStep"],
            onboarding_completed=bool(computed["isCompleted"]),
            onboarding_progress=computed["progressPercent"],
            next_onboarding_step=computed["nextStep"],
            pending_required_steps=pending_required_steps,
            completed_steps=completed_steps,
            skipped_steps=skipped_steps,
            review_completed=bool(computed["reviewCompleted"]),
            playbook_id=computed.get("playbookId"),
            integrations=[
                WorkspaceIntegrationSetupStatusRead(
                    key=item["key"],
                    label=item["label"],
                    description=item["description"],
                    required=bool(item["required"]),
                    status=computed["integrations"][item["key"]],
                )
                for item in GUIDED_INTEGRATIONS
            ],
            steps=step_rows,
            area_activations=[
                item.model_dump(by_alias=True) if hasattr(item, "model_dump") else item
                for item in area_activations
            ],
            notification_recipients=notification_recipients,
        )
        if whatsapp_state_reconciled or workspace.onboarding_completed != state.onboarding_completed:
            workspace.onboarding_completed = state.onboarding_completed
            settings_data["guidedOnboarding"] = self._persisted_onboarding_payload(state)
            workspace.settings = settings_data
            await self.db.flush()
        return state

    async def _has_ready_native_whatsapp_connection(self, workspace_id: UUID) -> bool:
        connections = list(
            (
                await self.db.scalars(
                    select(OmnichannelChannelConnection).where(
                        OmnichannelChannelConnection.workspace_id == workspace_id,
                        OmnichannelChannelConnection.channel == "whatsapp",
                        OmnichannelChannelConnection.provider == "meta_cloud",
                        OmnichannelChannelConnection.transport_mode.in_(
                            {"cloud_api", "coexistence"}
                        ),
                        OmnichannelChannelConnection.status.notin_(
                            {"paused", "disconnected_local"}
                        ),
                    )
                )
            ).all()
        )
        return any(
            (connection.health or {}).get("state") == "local_configuration_complete"
            for connection in connections
        )

    async def _has_onboarding_playbook(
        self,
        workspace_id: UUID,
        playbook_id: UUID,
    ) -> bool:
        """Only a published, current workspace playbook can govern live agents."""
        published_event_id = await self.db.scalar(
            select(PlaybookVersionGovernance.id)
            .join(
                ConfigDocument,
                ConfigDocument.id == PlaybookVersionGovernance.document_id,
            )
            .where(
                ConfigDocument.id == playbook_id,
                ConfigDocument.workspace_id == workspace_id,
                ConfigDocument.namespace.startswith("sales.playbook."),
                PlaybookVersionGovernance.version == ConfigDocument.current_version,
                PlaybookVersionGovernance.state == PlaybookLifecycleState.published,
            )
            .limit(1)
        )
        return published_event_id is not None

    @staticmethod
    def _compute_onboarding_progress(
        onboarding_state: dict[str, Any],
        area_activations: list[Any],
    ) -> dict[str, Any]:
        completed_steps = list(dict.fromkeys(onboarding_state.get("completedSteps", [])))
        skipped_steps = list(dict.fromkeys(onboarding_state.get("skippedSteps", [])))
        integrations: dict[str, IntegrationSetupStatus] = {
            item["key"]: onboarding_state.get("integrations", {}).get(item["key"], "not_started")
            for item in GUIDED_INTEGRATIONS
        }

        normalized_areas = [
            item.model_dump(by_alias=False) if hasattr(item, "model_dump") else item
            for item in area_activations
        ]
        enabled_areas = [area for area in normalized_areas if area.get("enabled")]
        has_area = len(enabled_areas) > 0
        has_template = any(area.get("selected_flow_template_id") or area.get("selectedFlowTemplateId") for area in enabled_areas)
        has_follow_up = any(
            (area.get("follow_up_profile") or area.get("followUpProfile"))
            and (area.get("human_intervention_mode") or area.get("humanInterventionMode"))
            for area in enabled_areas
        )
        has_playbook = bool(onboarding_state.get("playbookId"))
        review_completed = bool(onboarding_state.get("reviewCompleted", False))

        status_map: dict[str, str] = {}
        for step in GUIDED_ONBOARDING_STEPS:
            step_id = step["id"]
            if step_id == "welcome":
                status_map[step_id] = "completed" if step_id in completed_steps else "pending"
            elif step_id == "whatsapp":
                status_map[step_id] = "completed" if integrations["whatsapp"] == "connected" else "pending"
            elif step_id == "areas":
                status_map[step_id] = "completed" if has_area and step_id in completed_steps else "blocked" if integrations["whatsapp"] != "connected" else "pending"
            elif step_id == "template":
                status_map[step_id] = "completed" if has_template and step_id in completed_steps else "blocked" if not has_area else "pending"
            elif step_id == "follow_up":
                status_map[step_id] = "completed" if has_follow_up and step_id in completed_steps else "blocked" if not has_template else "pending"
            elif step_id == "playbook":
                status_map[step_id] = (
                    "completed"
                    if has_playbook and step_id in completed_steps
                    else "blocked"
                    if not has_follow_up
                    else "pending"
                )
            elif step_id == "integrations":
                if step_id in skipped_steps:
                    status_map[step_id] = "skipped"
                else:
                    optional_statuses = [integrations[item["key"]] for item in GUIDED_INTEGRATIONS if not item["required"]]
                    status_map[step_id] = "completed" if step_id in completed_steps or (optional_statuses and all(status != "not_started" for status in optional_statuses)) else "pending"
            elif step_id == "review":
                status_map[step_id] = "completed" if review_completed else "blocked" if not has_playbook else "pending"

        required_steps = [step["id"] for step in GUIDED_ONBOARDING_STEPS if step["required"]]
        completed_required = sum(1 for step_id in required_steps if status_map[step_id] == "completed")
        progress_percent = round((completed_required / max(len(required_steps), 1)) * 100)
        next_step = next((step["id"] for step in GUIDED_ONBOARDING_STEPS if status_map[step["id"]] not in {"completed", "skipped"}), None)
        current_step = onboarding_state.get("currentStep") or next_step or "review"

        return {
            "currentStep": current_step,
            "completedSteps": completed_steps,
            "skippedSteps": skipped_steps,
            "integrations": integrations,
            "reviewCompleted": review_completed,
            "playbookId": onboarding_state.get("playbookId"),
            "statusMap": status_map,
            "progressPercent": progress_percent,
            "nextStep": next_step,
            "isCompleted": all(status_map[step_id] == "completed" for step_id in required_steps),
        }

    @staticmethod
    def _persisted_onboarding_payload(
        state: WorkspaceOnboardingStateRead,
    ) -> dict[str, Any]:
        return {
            "currentStep": state.current_step,
            "completedSteps": state.completed_steps,
            "skippedSteps": state.skipped_steps,
            "reviewCompleted": state.review_completed,
            "playbookId": str(state.playbook_id) if state.playbook_id else None,
            "integrations": {item.key: item.status for item in state.integrations},
            "notificationRecipients": [
                item.model_dump(by_alias=True)
                for item in state.notification_recipients
                if item.role != "workspace_owner"
            ],
            "progressPercent": state.onboarding_progress,
            "nextStep": state.next_onboarding_step,
        }

    def _resolve_operational_recipient_limit(
        self,
        subscription: Subscription | None,
        features: dict[str, Any],
    ) -> int | None:
        configured = features.get("maxOperationalRecipients")
        if configured is not None:
            return int(configured)
        if not subscription or not getattr(subscription, "plan", None):
            return self.PLAN_OPERATIONAL_RECIPIENT_LIMITS["basic"]
        normalized_tier = self._normalize_plan_tier(getattr(subscription.plan.tier, "value", None))
        return self.PLAN_OPERATIONAL_RECIPIENT_LIMITS.get(
            normalized_tier or "",
            self.PLAN_OPERATIONAL_RECIPIENT_LIMITS["basic"],
        )

    def _resolve_campaign_recipient_limit(
        self,
        subscription: Subscription | None,
        features: dict[str, Any],
    ) -> int | None:
        if "maxCampaignRecipientsPerMonth" in features:
            configured = features["maxCampaignRecipientsPerMonth"]
            return None if configured is None else int(configured)
        if not subscription or not getattr(subscription, "plan", None):
            return self.PLAN_CAMPAIGN_RECIPIENT_LIMITS["basic"]
        normalized_tier = self._normalize_plan_tier(
            getattr(subscription.plan.tier, "value", None)
        )
        return self.PLAN_CAMPAIGN_RECIPIENT_LIMITS.get(
            normalized_tier or "",
            self.PLAN_CAMPAIGN_RECIPIENT_LIMITS["basic"],
        )

    async def _get_workspace_owner_recipient(self, workspace_id: UUID) -> WorkspaceNotificationRecipientRead | None:
        result = await self.db.execute(
            select(WorkspaceMembership)
            .options(selectinload(WorkspaceMembership.user))
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.role == WorkspaceRole.workspace_owner,
                WorkspaceMembership.status == MembershipStatus.active,
            )
            .limit(1)
        )
        membership = result.scalar_one_or_none()
        if not membership or not membership.user:
            return None
        return WorkspaceNotificationRecipientRead(
            id=f"owner:{membership.user.id}",
            name=membership.user.name,
            email=membership.user.email,
            phone=membership.user.phone,
            role="workspace_owner",
            user_id=membership.user.id,
            locked=True,
        )

    async def _resolve_notification_recipients(self, workspace: Workspace) -> list[WorkspaceNotificationRecipientRead]:
        settings_data = self._merge_settings_with_defaults(workspace.settings or {}, workspace.setup_mode.value)
        notifications = _normalize_notification_settings_shape(settings_data.get("notifications", {}))
        recipients: list[WorkspaceNotificationRecipientRead] = []
        owner = await self._get_workspace_owner_recipient(workspace.id)
        if owner:
            recipients.append(owner)
        for item in notifications.get("recipients", []):
            if item.get("role") == "workspace_owner":
                continue
            recipients.append(
                WorkspaceNotificationRecipientRead(
                    id=str(item.get("id") or uuid4()),
                    name=item.get("name") or "Contato operacional",
                    email=item.get("email"),
                    phone=item.get("phone"),
                    role=item.get("role") or "custom",
                    user_id=item.get("user_id"),
                    locked=bool(item.get("locked", False)),
                )
            )
        return recipients

    async def _assert_operational_recipient_limit(
        self,
        workspace: Workspace,
        recipients: list[Any],
    ) -> None:
        plan_summary = await self._build_plan_summary(workspace)
        limit = (
            plan_summary.max_operational_recipients
            if plan_summary
            else self.PLAN_OPERATIONAL_RECIPIENT_LIMITS["basic"]
        )
        if limit is None:
            return
        if len(recipients) > limit:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WKS-008",
                    "message": f"Seu plano permite até {limit} contatos operacionais extras para alertas.",
                },
            )
