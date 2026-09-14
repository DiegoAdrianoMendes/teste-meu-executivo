"""Public KLM capability-function inventory and controlled runtime."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_permissions, get_db, require_permission
from app.core.rbac import Permission
from app.schemas.native_public import (
    KLMCapabilityFunctionActionRead,
    KLMCapabilityFunctionActionWrite,
    KLMCapabilityFunctionListRead,
    KLMCapabilityFunctionRead,
    KLMCapabilityFunctionRuntimeRead,
)
from app.schemas.oss_function_unit import OSSFunctionUnitActionWrite
from app.services.native_public_boundary import (
    NativePublicNotFoundError,
    get_public_function,
    list_public_functions,
    public_function_action,
    public_function_runtime,
    public_text,
    resolve_internal_function_id,
    validate_public_query,
)
from app.services.oss_function_unit_runtime_service import (
    OSSFunctionUnitAuthorizationError,
    OSSFunctionUnitContractError,
    OSSFunctionUnitExternalGateError,
    OSSFunctionUnitHumanApprovalError,
    OSSFunctionUnitIdempotencyConflictError,
    OSSFunctionUnitMaturityError,
    OSSFunctionUnitRuntimeService,
)
from app.services.oss_function_unit_service import (
    OSSFunctionUnitCatalogError,
    OSSFunctionUnitNotFoundError,
)
from app.services.oss_native_local_adapter import (
    OSSNativeLocalContractError,
    OSSNativeLocalExternalGateError,
    OSSNativeLocalRecordConflictError,
    OSSNativeLocalValidationError,
)

router = APIRouter(tags=["KLM Capability Functions"])


@router.get(
    "/capabilities/functions",
    response_model=KLMCapabilityFunctionListRead,
    summary="Listar as funções nativas KLM",
    operation_id="listKLMCapabilityFunctions",
)
async def list_klm_capability_functions(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, alias="pageSize", ge=1, le=200),
    search: str | None = Query(default=None, max_length=120),
    domain: str | None = Query(default=None, max_length=64),
    category: str | None = Query(default=None, max_length=64),
    disposition: str | None = Query(default=None, max_length=64),
    capability: str | None = Query(default=None, max_length=180),
    _: AuthContext = Depends(require_permission("integrations", "view")),
) -> KLMCapabilityFunctionListRead:
    try:
        validate_public_query(request.query_params)
        payload = list_public_functions(
            page=page,
            page_size=page_size,
            search=search,
            domain=domain,
            category=category,
            disposition=disposition,
            capability=capability,
        )
        return KLMCapabilityFunctionListRead.model_validate(payload)
    except ValueError as error:
        message = public_text(error)
        if "Filtro público" in message:
            message = "Filtro público não suportado."
        raise HTTPException(status_code=422, detail=message) from error
    except OSSFunctionUnitCatalogError as error:
        raise HTTPException(status_code=503, detail=public_text(error)) from error


@router.get(
    "/capabilities/functions/{unit_id}",
    response_model=KLMCapabilityFunctionRead,
    summary="Ler uma função nativa KLM",
    operation_id="getKLMCapabilityFunction",
)
async def get_klm_capability_function(
    unit_id: str,
    _: AuthContext = Depends(require_permission("integrations", "view")),
) -> KLMCapabilityFunctionRead:
    try:
        return KLMCapabilityFunctionRead.model_validate(get_public_function(unit_id))
    except NativePublicNotFoundError as error:
        raise HTTPException(status_code=404, detail="Função KLM não encontrada.") from error
    except OSSFunctionUnitCatalogError as error:
        raise HTTPException(status_code=503, detail=public_text(error)) from error


@router.get(
    "/capabilities/functions/{unit_id}/runtime",
    response_model=KLMCapabilityFunctionRuntimeRead,
    summary="Consultar a disponibilidade de uma função KLM",
    operation_id="getKLMCapabilityFunctionRuntime",
)
async def get_klm_capability_function_runtime(
    unit_id: str,
    auth: AuthContext = Depends(require_permission("integrations", "view")),
    db: AsyncSession = Depends(get_db),
) -> KLMCapabilityFunctionRuntimeRead:
    if auth.workspace_id is None:
        raise HTTPException(status_code=403, detail="Selecione um workspace.")
    try:
        internal_id = resolve_internal_function_id(unit_id)
        payload = await OSSFunctionUnitRuntimeService(db).snapshot(auth.workspace_id, internal_id)
        return KLMCapabilityFunctionRuntimeRead.model_validate(
            public_function_runtime(unit_id, payload)
        )
    except (NativePublicNotFoundError, OSSFunctionUnitNotFoundError) as error:
        raise HTTPException(status_code=404, detail="Função KLM não encontrada.") from error
    except OSSFunctionUnitContractError as error:
        raise HTTPException(status_code=409, detail=public_text(error)) from error
    except OSSFunctionUnitCatalogError as error:
        raise HTTPException(status_code=503, detail=public_text(error)) from error


@router.post(
    "/capabilities/functions/{unit_id}/actions",
    response_model=KLMCapabilityFunctionActionRead,
    summary="Executar uma função KLM controlada",
    operation_id="executeKLMCapabilityFunction",
)
async def execute_klm_capability_function(
    unit_id: str,
    data: KLMCapabilityFunctionActionWrite,
    auth: AuthContext = Depends(require_permission("integrations", "manage")),
    permissions: list[Permission] = Depends(get_current_permissions),
    db: AsyncSession = Depends(get_db),
) -> KLMCapabilityFunctionActionRead:
    if auth.workspace_id is None:
        raise HTTPException(status_code=403, detail="Selecione um workspace.")
    try:
        internal_id = resolve_internal_function_id(unit_id)
        internal_data = OSSFunctionUnitActionWrite.model_validate(data.model_dump())
        payload = await OSSFunctionUnitRuntimeService(db).execute(
            workspace_id=auth.workspace_id,
            actor_user_id=auth.user_id,
            unit_id=internal_id,
            mode=internal_data.mode,
            idempotency_key=internal_data.idempotency_key,
            resource_id=internal_data.resource_id,
            payload=internal_data.payload,
            permission_keys=frozenset(
                (permission.module, permission.action) for permission in permissions
            ),
        )
        return KLMCapabilityFunctionActionRead.model_validate(public_function_action(payload))
    except (NativePublicNotFoundError, OSSFunctionUnitNotFoundError) as error:
        raise HTTPException(status_code=404, detail="Função KLM não encontrada.") from error
    except OSSFunctionUnitExternalGateError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=public_text(error)
        ) from error
    except (OSSFunctionUnitAuthorizationError, OSSFunctionUnitHumanApprovalError) as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=public_text(error)
        ) from error
    except OSSFunctionUnitMaturityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=public_text(error)
        ) from error
    except OSSFunctionUnitIdempotencyConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=public_text(error)
        ) from error
    except OSSFunctionUnitContractError as error:
        raise HTTPException(status_code=422, detail=public_text(error)) from error
    except OSSNativeLocalRecordConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=public_text(error)
        ) from error
    except (OSSNativeLocalValidationError, OSSNativeLocalContractError) as error:
        raise HTTPException(status_code=422, detail=public_text(error)) from error
    except OSSNativeLocalExternalGateError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=public_text(error)
        ) from error
    except OSSFunctionUnitCatalogError as error:
        raise HTTPException(status_code=503, detail=public_text(error)) from error
