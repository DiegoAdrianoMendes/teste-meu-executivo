"""
Endpoint: Users (Governança de Usuários Humanos)
CRUD completo para gerenciamento de usuários humanos do sistema.

Códigos de erro padronizados:
  USR-001  Usuário não encontrado
  USR-002  Email já cadastrado
  USR-003  Permissão insuficiente (apenas admin pode criar/deletar)
  USR-004  Não é possível desativar o próprio usuário
  USR-005  Usuário inativo — contate o administrador
  AUTH-001 Token ausente ou inválido
  AUTH-002 Token expirado
  AUTH-003 Credenciais incorretas
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.entitlement_errors import entitlement_http_exception
from app.core.deps import (
    AuthContext,
    get_current_auth_context,
    get_current_user_id,
    get_current_workspace_id,
    get_db,
    require_workspace_admin,
)
from app.models.configuration import ConfigScope
from app.models.user import UserRole
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.schemas.configuration import (
    ConfigLayerRead,
    ConfigVersionRead,
    UserPreferences,
    UserPreferencesRead,
    UserPreferencesUpdate,
)
from app.schemas.user import UserCreate, UserRead, UserUpdate
from app.schemas.workspace import TeamMemberUpdate
from app.services.configuration_service import (
    ConfigConflictError,
    ConfigurationService,
    ConfigValidationError,
)
from app.services.entitlement_service import EntitlementError
from app.services.team_member_service import TeamMemberService
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["Usuários"])
USER_PREFERENCES_NAMESPACE = "user.preferences"
DEFAULT_USER_PREFERENCES = UserPreferences().model_dump()


async def _read_preferences(
    *,
    service: ConfigurationService,
    context: AuthContext,
    workspace_id: UUID,
) -> UserPreferencesRead:
    resolved = await service.resolve(
        namespace=USER_PREFERENCES_NAMESPACE,
        workspace_id=workspace_id,
        role_key=context.role.value if context.role else None,
        user_id=context.user_id,
        contract_defaults=DEFAULT_USER_PREFERENCES,
    )
    user_document = await service.get_tenant_document(
        workspace_id=workspace_id,
        scope=ConfigScope.user,
        namespace=USER_PREFERENCES_NAMESPACE,
        user_id=context.user_id,
    )
    return UserPreferencesRead(
        preferences=UserPreferences.model_validate(resolved.effective),
        etag=resolved.etag,
        user_etag=user_document.etag if user_document else None,
        user_version=user_document.current_version if user_document else None,
        layers=[
            ConfigLayerRead(
                scope=layer.scope,
                scope_key=layer.scope_key,
                version=layer.version,
                etag=layer.etag,
            )
            for layer in resolved.layers
        ],
    )


@router.get("/me", response_model=UserRead, summary="Perfil do usuário autenticado")
async def get_me(
    current_user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
):
    """
    Retorna os dados do usuário autenticado.
    Usado pelo frontend após o login para popular o estado `currentUser`.
    """
    service = UserService(db)
    user = await service.get_by_id(current_user_id, workspace_id=workspace_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USR-001", "message": "Usuário não encontrado."},
        )
    return user


@router.get(
    "/me/preferences",
    response_model=UserPreferencesRead,
    summary="Preferências efetivas do usuário",
)
async def get_my_preferences(
    response: Response,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
):
    result = await _read_preferences(
        service=ConfigurationService(db),
        context=context,
        workspace_id=workspace_id,
    )
    response.headers["ETag"] = f'"{result.etag}"'
    return result


@router.put(
    "/me/preferences",
    response_model=UserPreferencesRead,
    summary="Atualizar preferências do usuário",
)
async def update_my_preferences(
    data: UserPreferencesUpdate,
    response: Response,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    if_match: str | None = Header(default=None, alias="If-Match"),
):
    service = ConfigurationService(db)
    current = await service.get_tenant_document(
        workspace_id=workspace_id,
        scope=ConfigScope.user,
        namespace=USER_PREFERENCES_NAMESPACE,
        user_id=context.user_id,
    )
    override = dict(current.payload) if current else {}
    override.update(data.model_dump(exclude_none=True, exclude_unset=True))
    try:
        await service.write_tenant(
            workspace_id=workspace_id,
            scope=ConfigScope.user,
            namespace=USER_PREFERENCES_NAMESPACE,
            payload=override,
            actor_user_id=context.user_id,
            user_id=context.user_id,
            expected_etag=if_match,
            reason="User preferences updated",
        )
    except ConfigConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"code": "CFG-001", "message": str(exc)},
        ) from exc
    except ConfigValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "CFG-002", "message": str(exc)},
        ) from exc
    result = await _read_preferences(
        service=service,
        context=context,
        workspace_id=workspace_id,
    )
    response.headers["ETag"] = f'"{result.etag}"'
    return result


@router.get(
    "/me/preferences/versions",
    response_model=list[ConfigVersionRead],
    summary="Histórico das preferências do usuário",
)
async def list_my_preference_versions(
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
):
    service = ConfigurationService(db)
    document = await service.get_tenant_document(
        workspace_id=workspace_id,
        scope=ConfigScope.user,
        namespace=USER_PREFERENCES_NAMESPACE,
        user_id=context.user_id,
    )
    return [] if document is None else await service.history(document.id)


@router.post(
    "/me/preferences/rollback/{version}",
    response_model=UserPreferencesRead,
    summary="Restaurar uma versão das preferências",
)
async def rollback_my_preferences(
    version: int,
    response: Response,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    if_match: str | None = Header(default=None, alias="If-Match"),
):
    service = ConfigurationService(db)
    document = await service.get_tenant_document(
        workspace_id=workspace_id,
        scope=ConfigScope.user,
        namespace=USER_PREFERENCES_NAMESPACE,
        user_id=context.user_id,
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CFG-003", "message": "Preferências ainda não possuem histórico."},
        )
    try:
        await service.rollback_tenant(
            document=document,
            target_version=version,
            actor_user_id=context.user_id,
            expected_etag=if_match,
        )
    except ConfigConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"code": "CFG-001", "message": str(exc)},
        ) from exc
    except ConfigValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CFG-003", "message": str(exc)},
        ) from exc
    result = await _read_preferences(
        service=service,
        context=context,
        workspace_id=workspace_id,
    )
    response.headers["ETag"] = f'"{result.etag}"'
    return result


@router.get("", response_model=list[UserRead], summary="Listar usuários humanos")
async def list_users(
    role: UserRole | None = Query(
        None,
        description="Filtrar por papel: senior_lawyer | lawyer | intern | admin",
    ),
    is_active: bool | None = Query(None, description="Filtrar por status ativo/inativo"),
    search: str | None = Query(None, description="Busca por nome ou email"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
):
    """
    Lista todos os usuários humanos do sistema.
    Suporta filtros por role, status e busca textual.
    """
    service = UserService(db)
    return await service.get_all_humans(
        role=role,
        workspace_id=workspace_id,
        is_active=is_active,
        search=search,
        skip=skip,
        limit=limit,
    )


@router.get("/{user_id}", response_model=UserRead, summary="Detalhe do usuário")
async def get_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
):
    """Retorna os dados de um usuário específico."""
    service = UserService(db)
    user = await service.get_by_id(user_id, workspace_id=workspace_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USR-001", "message": "Usuário não encontrado."},
        )
    return user


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar usuário humano",
)
async def create_user(
    data: UserCreate,
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_workspace_admin),
    workspace_id: UUID = Depends(get_current_workspace_id),
):
    del data, db, workspace_id
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "TEAM-INVITATION-REQUIRED",
            "message": "Use o fluxo de convite da equipe para adicionar uma identidade humana.",
        },
    )


@router.patch("/{user_id}", response_model=UserRead, summary="Atualizar usuário")
async def update_user(
    user_id: UUID,
    data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    context: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
):
    """
    Atualização parcial de dados do usuário.
    O próprio usuário pode atualizar nome, initials e avatar.
    Apenas perfis administrativos podem alterar role e is_active.
    """
    service = UserService(db)

    user = await service.get_by_id(user_id, workspace_id=workspace_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USR-001", "message": "Usuário não encontrado."},
        )

    is_self = context.user_id == user_id
    if not is_self:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "USR-003",
                "message": "Administradores de workspace não podem alterar a identidade global.",
            },
        )

    update_data = data.model_dump(exclude_unset=True)
    forbidden_fields = {"email", "password", "role", "is_active"} & update_data.keys()
    if forbidden_fields:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "IDENTITY-SELF-SERVICE-REQUIRED",
                "message": "Email, senha, papel global e estado global não são alterados por este endpoint.",
            },
        )

    try:
        updated = await service.update(user_id, data, workspace_id=workspace_id)
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    return updated


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Desativar usuário")
async def deactivate_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    context: AuthContext = Depends(require_workspace_admin),
    workspace_id: UUID = Depends(get_current_workspace_id),
):
    """
    Desativa (soft delete) um usuário humano.
    Requer perfil administrativo do workspace. Não é possível desativar o próprio usuário.
    """
    if context.user_id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "USR-004", "message": "Não é possível desativar o próprio usuário."},
        )

    membership = await db.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
        )
    )
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USR-001", "message": "Usuário não encontrado."},
        )

    await TeamMemberService(db).update(
        workspace_id,
        membership.id,
        context,
        TeamMemberUpdate(status=MembershipStatus.suspended),
    )
    return None
