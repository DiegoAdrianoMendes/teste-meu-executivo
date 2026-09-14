"""
Endpoint: Notifications
Gerenciamento de notificações do usuário, isoladas por workspace.
  - GET /notifications → lista notificações do workspace atual
  - PATCH /notifications/{id}/read → marca uma como lida
  - POST /notifications/mark-all-read → marca todas como lidas
  - GET /notifications/unread-count → badge de contagem no sino
  - POST /notifications/request-access → pede ao(s) administrador(es) do workspace acesso a um módulo
"""
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import can_view_sensitive_lead_data, get_db, get_current_user_id, get_current_workspace_id
from app.core.lead_privacy import mask_notification
from app.models.notification import NotificationType
from app.models.user import User
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.schemas.base import CamelCaseSchema
from app.schemas.notification import NotificationRead
from app.services.notification_service import NotificationService

router = APIRouter(prefix="/notifications", tags=["Notificações"])

# Rótulos legíveis dos módulos, usados apenas para compor o texto da notificação
# enviada ao administrador — a fonte de verdade das permissões continua em
# app.core.rbac / app.core.workspace_access_packages.
_MODULE_LABELS = {
    "dashboard": "Início",
    "kanban": "Funil",
    "lista": "Leads",
    "relatorios": "KPIs / relatórios",
    "aprovacoes": "Aprovações",
    "configuracoes": "Configurações",
    "mensagens": "Conversas",
    "users": "Usuários",
    "memberships": "Membros",
    "roles": "Papéis",
    "permissions": "Permissões",
    "finance": "Financeiro",
    "integrations": "Integrações / conexões",
    "automations": "Automações",
    "agents": "Agentes de IA",
    "notifications": "Notificações",
    "branding": "Identidade visual",
    "security": "Segurança",
    "audit": "Auditoria",
    "lead_data": "Dados sensíveis de clientes",
    "admin": "Administração",
}


class AccessRequestPayload(CamelCaseSchema):
    module: str = Field(..., description="Chave técnica do módulo/permissão negada.")
    module_label: str | None = Field(None, description="Rótulo legível do módulo, se já resolvido no front.")
    note: str | None = Field(None, max_length=500, description="Mensagem opcional do solicitante.")


@router.get("", response_model=List[NotificationRead], summary="Listar notificações do usuário")
async def list_notifications(
    skip: int = 0,
    limit: int = 50,
    unread_only: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    service = NotificationService(db)
    notifications = await service.get_for_user(
        current_user_id,
        workspace_id=workspace_id,
        skip=skip,
        limit=limit,
        unread_only=unread_only,
    )
    return notifications if can_view_sensitive else [mask_notification(item) for item in notifications]


@router.get("/unread-count", summary="Contagem de não lidas (badge)")
async def unread_count(
    db: AsyncSession = Depends(get_db),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
):
    service = NotificationService(db)
    count = await service.get_unread_count(current_user_id, workspace_id=workspace_id)
    return {"unreadCount": count}


@router.patch("/{notification_id}/read", status_code=status.HTTP_200_OK, summary="Marcar como lida")
async def mark_as_read(
    notification_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
):
    service = NotificationService(db)
    success = await service.mark_as_read(
        notification_id,
        current_user_id,
        workspace_id=workspace_id,
    )
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notificação não encontrada.")
    return {"message": "Notificação marcada como lida."}


@router.post("/mark-all-read", status_code=status.HTTP_200_OK, summary="Marcar todas como lidas")
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
):
    service = NotificationService(db)
    count = await service.mark_all_as_read(current_user_id, workspace_id=workspace_id)
    return {"message": f"{count} notificações marcadas como lidas.", "updatedCount": count}


@router.post(
    "/request-access",
    status_code=status.HTTP_201_CREATED,
    summary="Solicitar acesso a um módulo (avisa os administradores do workspace)",
)
async def request_module_access(
    payload: AccessRequestPayload,
    db: AsyncSession = Depends(get_db),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
):
    """Usado pelo empty-state `ModuleAccessDenied` do front: qualquer usuário
    autenticado com um workspace ativo pode disparar este pedido — não requer
    nenhuma permissão de módulo, pois o próprio ponto é "eu não tenho acesso".
    Cria uma notificação individual para cada owner/admin ativo do workspace.
    """
    requester = await db.get(User, current_user_id)
    requester_name = (requester.name if requester and getattr(requester, "name", None) else None) or "Um usuário"
    module_label = payload.module_label or _MODULE_LABELS.get(payload.module, payload.module)

    admins_result = await db.execute(
        select(WorkspaceMembership.user_id).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.status == MembershipStatus.active,
            WorkspaceMembership.role.in_(
                {WorkspaceRole.workspace_owner, WorkspaceRole.workspace_admin}
            ),
            WorkspaceMembership.user_id != current_user_id,
        )
    )
    admin_user_ids = list({row for row in admins_result.scalars().all()})

    if not admin_user_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOTIF-ACCESS-001",
                "message": "Nenhum administrador ativo foi encontrado neste workspace para receber o pedido.",
            },
        )

    message = f"{requester_name} solicitou acesso ao módulo \"{module_label}\"."
    if payload.note:
        message = f"{message} Mensagem: {payload.note.strip()}"

    service = NotificationService(db)
    for admin_user_id in admin_user_ids:
        await service.create(
            user_id=admin_user_id,
            title="Pedido de acesso a módulo",
            message=message,
            notification_type=NotificationType.info,
            workspace_id=workspace_id,
        )

    return {
        "message": "Pedido enviado ao(s) administrador(es) do workspace.",
        "notifiedAdmins": len(admin_user_ids),
    }
