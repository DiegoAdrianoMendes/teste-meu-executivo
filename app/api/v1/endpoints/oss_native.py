"""Public KLM surfaces for native capability state and controlled commands."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    AuthContext,
    get_current_permissions,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.core.rbac import Permission
from app.models.oss_native import OSSNativeSecretError, assert_secret_free_json
from app.schemas.native_public import (
    KLMCapabilityActionRead,
    KLMCapabilityCatalogRead,
    KLMCapabilityCommandRead,
    KLMCapabilityCommandWrite,
    KLMCapabilityConfigWrite,
    KLMCapabilityDocumentActionWrite,
    KLMCapabilityDocumentRead,
    KLMCapabilitySnapshotRead,
    KLMNativeDomain,
)
from app.schemas.oss_native import (
    CapabilityKey,
    OSSNativeCommandCreate,
    OSSNativeConfigPatch,
    OSSNativeDocumentActionWrite,
    ProviderKey,
)
from app.services.native_public_boundary import (
    public_capability_action,
    public_capability_catalog,
    public_capability_command,
    public_capability_document,
    public_capability_snapshot,
    public_text,
    validate_public_query,
)
from app.services.oss_native_document_service import (
    OSSNativeCapabilityNotFoundError,
    OSSNativeDocumentService,
    OSSNativeExecutionError,
    OSSNativeExternalGateError,
    native_capability,
    required_capability_manage_permissions,
    required_capability_read_permissions,
)
from app.services.oss_native_local_adapter import OSSNativeLocalRecordConflictError
from app.services.oss_native_state_service import (
    OSSNativeCommandConflictError,
    OSSNativeStateService,
    OSSNativeVersionConflictError,
)

router = APIRouter(tags=["KLM Native Capabilities"])

_INTERNAL_SOURCE_BY_DOMAIN: dict[KLMNativeDomain, ProviderKey] = {
    "communications": "chatwoot",
    "collaboration": "rocketchat",
    "automation": "chattermate",
    "crm": "bottlecrm",
}


def _internal_source_for_domain(domain: KLMNativeDomain) -> ProviderKey:
    return cast(ProviderKey, _INTERNAL_SOURCE_BY_DOMAIN[domain])


def _permission_keys(
    auth: AuthContext,
    permissions: list[Permission],
) -> frozenset[tuple[str, str]] | None:
    return frozenset((permission.module, permission.action) for permission in permissions)


def _assert_required_permissions(
    auth: AuthContext,
    permissions: list[Permission],
    required: frozenset[tuple[str, str]],
) -> None:
    permission_keys = _permission_keys(auth, permissions)
    if permission_keys is not None and not required.issubset(permission_keys):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "AUTH-012",
                "message": "Permissão insuficiente para este recurso nativo.",
            },
        )


def _query_value(value: str) -> str | int | bool:
    normalized = value.strip()
    if normalized.lower() in {"true", "false"}:
        return normalized.lower() == "true"
    try:
        return int(normalized)
    except ValueError:
        return normalized


def _version_conflict(error: OSSNativeVersionConflictError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "KLM-NATIVE-001",
            "message": "A configuração foi alterada. Recarregue o snapshot.",
            "currentVersion": error.current_version,
        },
    )


@router.get(
    "/capabilities",
    response_model=KLMCapabilityCatalogRead,
    summary="Listar o catálogo de capacidades nativas KLM",
    operation_id="listKLMNativeCapabilities",
)
async def get_native_capability_registry(
    _: AuthContext = Depends(require_permission("integrations", "view")),
) -> KLMCapabilityCatalogRead:
    return KLMCapabilityCatalogRead.model_validate(public_capability_catalog())


@router.get(
    "/capabilities/domains/{domain}/{capability}",
    response_model=KLMCapabilitySnapshotRead,
    summary="Ler o estado de uma capacidade KLM",
    operation_id="getKLMCapabilitySnapshot",
)
async def get_klm_capability_snapshot(
    domain: KLMNativeDomain,
    capability: CapabilityKey,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_permission("integrations", "view")),
    permissions: list[Permission] = Depends(get_current_permissions),
) -> KLMCapabilitySnapshotRead:
    try:
        provider = _internal_source_for_domain(domain)
        native_capability(provider, capability)
        _assert_required_permissions(
            auth,
            permissions,
            required_capability_read_permissions(provider, capability),
        )
        snapshot = await OSSNativeStateService(db).get_snapshot(
            workspace_id,
            provider,
            capability,
        )
        return KLMCapabilitySnapshotRead.model_validate(
            public_capability_snapshot(domain, capability, snapshot)
        )
    except OSSNativeCapabilityNotFoundError as error:
        raise HTTPException(status_code=404, detail=public_text(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=public_text(error)) from error


@router.get(
    "/capabilities/domains/{domain}/{capability}/document",
    response_model=KLMCapabilityDocumentRead,
    summary="Documento da experiência nativa KLM",
    operation_id="getKLMCapabilityDocument",
)
async def get_klm_capability_document(
    domain: KLMNativeDomain,
    capability: CapabilityKey,
    request: Request,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_permission("integrations", "view")),
    permissions: list[Permission] = Depends(get_current_permissions),
) -> KLMCapabilityDocumentRead:
    if len(request.query_params) > 25:
        raise HTTPException(status_code=422, detail="Muitos filtros na consulta nativa.")
    try:
        provider = _internal_source_for_domain(domain)
        _assert_required_permissions(
            auth,
            permissions,
            required_capability_read_permissions(provider, capability),
        )
        query = assert_secret_free_json(
            validate_public_query(
                {key: _query_value(value) for key, value in request.query_params.items()}
            ),
            field_name="native query",
            max_bytes=32 * 1024,
        )
        document = await OSSNativeDocumentService(db).document(
            workspace_id,
            provider,
            capability,
            query,
            permission_keys=_permission_keys(auth, permissions),
        )
        return KLMCapabilityDocumentRead.model_validate(
            public_capability_document(domain, capability, document)
        )
    except OSSNativeCapabilityNotFoundError as error:
        raise HTTPException(status_code=404, detail=public_text(error)) from error
    except (OSSNativeSecretError, TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=public_text(error)) from error


@router.post(
    "/capabilities/domains/{domain}/{capability}/actions",
    response_model=KLMCapabilityActionRead,
    summary="Executar ação da experiência nativa KLM",
    operation_id="executeKLMCapabilityAction",
)
async def execute_klm_capability_action(
    domain: KLMNativeDomain,
    capability: CapabilityKey,
    payload: KLMCapabilityDocumentActionWrite,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_permission("integrations", "view")),
    permissions: list[Permission] = Depends(get_current_permissions),
) -> KLMCapabilityActionRead:
    try:
        provider = _internal_source_for_domain(domain)
        internal_payload = OSSNativeDocumentActionWrite.model_validate(payload.model_dump())
        _assert_required_permissions(
            auth,
            permissions,
            required_capability_read_permissions(provider, capability),
        )
        result = await OSSNativeDocumentService(db).execute(
            workspace_id=workspace_id,
            actor_user_id=auth.user_id,
            provider=provider,
            capability_key=capability,
            request=internal_payload,
            permission_keys=_permission_keys(auth, permissions),
        )
        return KLMCapabilityActionRead.model_validate(
            public_capability_action(domain, capability, result)
        )
    except OSSNativeCapabilityNotFoundError as error:
        raise HTTPException(status_code=404, detail=public_text(error)) from error
    except OSSNativeExternalGateError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "KLM-NATIVE-003", "message": public_text(error)},
        ) from error
    except OSSNativeExecutionError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "KLM-NATIVE-004",
                "message": public_text(error),
                "commandId": str(error.command_id),
            },
        ) from error
    except OSSNativeCommandConflictError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "KLM-NATIVE-002", "message": public_text(error)},
        ) from error
    except OSSNativeLocalRecordConflictError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "KLM-NATIVE-005", "message": public_text(error)},
        ) from error
    except OSSNativeVersionConflictError as error:
        raise _version_conflict(error) from error
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=public_text(error)) from error
    except (OSSNativeSecretError, TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=public_text(error)) from error


@router.patch(
    "/capabilities/domains/{domain}/{capability}/config",
    response_model=KLMCapabilitySnapshotRead,
    summary="Atualizar configuração nativa com versão otimista",
    operation_id="updateKLMCapabilityConfiguration",
)
async def update_klm_capability_configuration(
    domain: KLMNativeDomain,
    capability: CapabilityKey,
    payload: KLMCapabilityConfigWrite,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_permission("integrations", "manage")),
    permissions: list[Permission] = Depends(get_current_permissions),
) -> KLMCapabilitySnapshotRead:
    try:
        provider = _internal_source_for_domain(domain)
        internal_payload = OSSNativeConfigPatch.model_validate(payload.model_dump())
        native_capability(provider, capability)
        _assert_required_permissions(
            auth,
            permissions,
            required_capability_manage_permissions(provider, capability),
        )
        snapshot = await OSSNativeDocumentService(db).update_configuration(
            workspace_id=workspace_id,
            actor_user_id=auth.user_id,
            provider=provider,
            capability_key=capability,
            data=internal_payload,
        )
        return KLMCapabilitySnapshotRead.model_validate(
            public_capability_snapshot(domain, capability, snapshot)
        )
    except OSSNativeCapabilityNotFoundError as error:
        raise HTTPException(status_code=404, detail=public_text(error)) from error
    except OSSNativeVersionConflictError as error:
        raise _version_conflict(error) from error
    except (OSSNativeSecretError, TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=public_text(error)) from error


@router.post(
    "/capabilities/domains/{domain}/{capability}/commands",
    response_model=KLMCapabilityCommandRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar e processar um comando KLM idempotente",
    operation_id="createKLMCapabilityCommand",
)
async def create_klm_capability_command(
    domain: KLMNativeDomain,
    capability: CapabilityKey,
    payload: KLMCapabilityCommandWrite,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_permission("integrations", "manage")),
    permissions: list[Permission] = Depends(get_current_permissions),
) -> KLMCapabilityCommandRead:
    try:
        provider = _internal_source_for_domain(domain)
        internal_payload = OSSNativeCommandCreate.model_validate(payload.model_dump())
        native_capability(provider, capability)
        _assert_required_permissions(
            auth,
            permissions,
            required_capability_manage_permissions(provider, capability),
        )
        command = await OSSNativeStateService(db).execute_command(
            workspace_id=workspace_id,
            actor_user_id=auth.user_id,
            provider=provider,
            capability=capability,
            data=internal_payload,
        )
        return KLMCapabilityCommandRead.model_validate(
            public_capability_command(domain, capability, command)
        )
    except OSSNativeCapabilityNotFoundError as error:
        raise HTTPException(status_code=404, detail=public_text(error)) from error
    except OSSNativeVersionConflictError as error:
        raise _version_conflict(error) from error
    except OSSNativeCommandConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "KLM-NATIVE-002", "message": public_text(error)},
        ) from error
    except (OSSNativeSecretError, TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=public_text(error)) from error
