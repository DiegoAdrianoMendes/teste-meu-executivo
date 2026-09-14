"""Versioned workspace configuration API."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    AuthContext,
    get_current_auth_context,
    get_current_workspace_id,
    get_db,
    require_workspace_admin,
)
from app.models.configuration import ConfigDocument, ConfigScope
from app.models.workspace_membership import WorkspaceMembership
from app.schemas.configuration import (
    ConfigLayerRead,
    ConfigurationDocumentRead,
    ConfigurationDocumentWrite,
    ConfigVersionRead,
    ResolvedConfigurationRead,
    configuration_contract_defaults,
)
from app.services.configuration_service import (
    ConfigConflictError,
    ConfigurationService,
    ConfigValidationError,
)

router = APIRouter(prefix="/configurations", tags=["Configurações versionadas"])


def _raise_configuration_error(exc: Exception) -> None:
    if isinstance(exc, ConfigConflictError):
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"code": "CFG-001", "message": str(exc)},
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": "CFG-002", "message": str(exc)},
    ) from exc


def _target_kwargs(
    scope: ConfigScope,
    *,
    role_key: str | None,
    user_id: UUID | None,
) -> dict:
    if scope is ConfigScope.platform:
        raise ConfigValidationError("Platform scope is managed by the platform control plane.")
    return {"role_key": role_key, "user_id": user_id}


async def _require_workspace_user(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
) -> None:
    if user_id is None:
        return
    membership_id = await db.scalar(
        select(WorkspaceMembership.id).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
        )
    )
    if membership_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "CFG-004", "message": "O usuário não pertence ao workspace."},
        )


@router.get(
    "/{namespace}/effective",
    response_model=ResolvedConfigurationRead,
    summary="Resolver configuração efetiva para a sessão",
)
async def get_effective_configuration(
    namespace: str,
    response: Response,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
):
    try:
        resolved = await ConfigurationService(db).resolve(
            namespace=namespace,
            workspace_id=workspace_id,
            role_key=context.role.value if context.role else None,
            user_id=context.user_id,
            contract_defaults=configuration_contract_defaults(namespace),
        )
    except ConfigValidationError as exc:
        _raise_configuration_error(exc)
    response.headers["ETag"] = f'"{resolved.etag}"'
    return ResolvedConfigurationRead(
        namespace=resolved.namespace,
        effective=resolved.effective,
        etag=resolved.etag,
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


@router.get(
    "/{namespace}/me",
    response_model=ConfigurationDocumentRead | None,
    summary="Ler preferências versionadas do usuário atual",
)
async def get_my_configuration_document(
    namespace: str,
    response: Response,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
):
    try:
        document = await ConfigurationService(db).get_tenant_document(
            workspace_id=workspace_id,
            scope=ConfigScope.user,
            namespace=namespace,
            user_id=context.user_id,
        )
    except ConfigValidationError as exc:
        _raise_configuration_error(exc)
    if document is not None:
        response.headers["ETag"] = f'"{document.etag}"'
    return document


@router.put(
    "/{namespace}/me",
    response_model=ConfigurationDocumentRead,
    summary="Salvar preferências versionadas do usuário atual",
)
async def put_my_configuration_document(
    namespace: str,
    data: ConfigurationDocumentWrite,
    response: Response,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    if_match: str | None = Header(default=None, alias="If-Match"),
):
    try:
        document = await ConfigurationService(db).write_tenant(
            workspace_id=workspace_id,
            scope=ConfigScope.user,
            namespace=namespace,
            payload=data.payload,
            actor_user_id=context.user_id,
            user_id=context.user_id,
            schema_version=data.schema_version,
            expected_etag=if_match,
            reason=data.reason or "Preferências pessoais atualizadas",
        )
        await db.commit()
        await db.refresh(document)
    except (ConfigConflictError, ConfigValidationError) as exc:
        await db.rollback()
        _raise_configuration_error(exc)
    response.headers["ETag"] = f'"{document.etag}"'
    return document


@router.get(
    "/{namespace}/documents/{scope}",
    response_model=ConfigurationDocumentRead,
    summary="Ler camada administrativa de configuração",
)
async def get_configuration_document(
    namespace: str,
    scope: ConfigScope,
    response: Response,
    role_key: str | None = Query(default=None, max_length=80),
    user_id: UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_workspace_admin),
):
    service = ConfigurationService(db)
    try:
        targets = _target_kwargs(scope, role_key=role_key, user_id=user_id)
        document = await service.get_tenant_document(
            workspace_id=workspace_id,
            scope=scope,
            namespace=namespace,
            **targets,
        )
    except ConfigValidationError as exc:
        _raise_configuration_error(exc)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CFG-003", "message": "Camada de configuração não encontrada."},
        )
    response.headers["ETag"] = f'"{document.etag}"'
    return document


@router.put(
    "/{namespace}/documents/{scope}",
    response_model=ConfigurationDocumentRead,
    summary="Criar ou atualizar camada administrativa",
)
async def put_configuration_document(
    namespace: str,
    scope: ConfigScope,
    data: ConfigurationDocumentWrite,
    response: Response,
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
    if_match: str | None = Header(default=None, alias="If-Match"),
):
    await _require_workspace_user(
        db,
        workspace_id=workspace_id,
        user_id=data.user_id,
    )
    service = ConfigurationService(db)
    try:
        targets = _target_kwargs(
            scope,
            role_key=data.role_key,
            user_id=data.user_id,
        )
        document = await service.write_tenant(
            workspace_id=workspace_id,
            scope=scope,
            namespace=namespace,
            payload=data.payload,
            actor_user_id=context.user_id,
            schema_version=data.schema_version,
            expected_etag=if_match,
            reason=data.reason,
            **targets,
        )
    except (ConfigConflictError, ConfigValidationError) as exc:
        _raise_configuration_error(exc)
    response.headers["ETag"] = f'"{document.etag}"'
    return document


async def _load_document(
    service: ConfigurationService,
    *,
    workspace_id: UUID,
    scope: ConfigScope,
    namespace: str,
    role_key: str | None,
    user_id: UUID | None,
) -> ConfigDocument:
    try:
        targets = _target_kwargs(scope, role_key=role_key, user_id=user_id)
        document = await service.get_tenant_document(
            workspace_id=workspace_id,
            scope=scope,
            namespace=namespace,
            **targets,
        )
    except ConfigValidationError as exc:
        _raise_configuration_error(exc)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CFG-003", "message": "Camada de configuração não encontrada."},
        )
    return document


@router.get(
    "/{namespace}/documents/{scope}/versions",
    response_model=list[ConfigVersionRead],
    summary="Listar histórico de uma camada",
)
async def list_configuration_versions(
    namespace: str,
    scope: ConfigScope,
    role_key: str | None = Query(default=None, max_length=80),
    user_id: UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_workspace_admin),
):
    service = ConfigurationService(db)
    document = await _load_document(
        service,
        workspace_id=workspace_id,
        scope=scope,
        namespace=namespace,
        role_key=role_key,
        user_id=user_id,
    )
    return await service.history(document.id)


@router.post(
    "/{namespace}/documents/{scope}/rollback/{version}",
    response_model=ConfigurationDocumentRead,
    summary="Restaurar uma versão como nova revisão",
)
async def rollback_configuration_document(
    namespace: str,
    scope: ConfigScope,
    version: int,
    response: Response,
    role_key: str | None = Query(default=None, max_length=80),
    user_id: UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
    if_match: str | None = Header(default=None, alias="If-Match"),
):
    service = ConfigurationService(db)
    document = await _load_document(
        service,
        workspace_id=workspace_id,
        scope=scope,
        namespace=namespace,
        role_key=role_key,
        user_id=user_id,
    )
    try:
        rolled_back = await service.rollback_tenant(
            document=document,
            target_version=version,
            actor_user_id=context.user_id,
            expected_etag=if_match,
        )
    except (ConfigConflictError, ConfigValidationError) as exc:
        _raise_configuration_error(exc)
    response.headers["ETag"] = f'"{rolled_back.etag}"'
    return rolled_back
