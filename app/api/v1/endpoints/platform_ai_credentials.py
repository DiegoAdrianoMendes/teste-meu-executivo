"""
Chaves de provedores de IA da plataforma, geridas pelo Blackbox (write-only).

PUT grava ou rotaciona a chave cifrada; GET e DELETE nunca expõem o valor,
somente os últimos 4 caracteres. Toda mutação vai para a trilha canônica
``platform_audit_events`` dentro da mesma transação e invalida o cache do
gateway neste processo (os demais convergem em até 15 s).

Precedência no gateway: BYOK do workspace → chave da plataforma (aqui) →
variável de ambiente.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.workspace_credentials import invalidate_platform_credential_cache
from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.platform_access import require_platform_permission
from app.core.platform_audit import record_canonical_platform_mutation
from app.models.platform_model_credential import PlatformModelCredential
from app.models.user import User
from app.schemas.platform_ai_credentials import (
    PlatformAiCredentialsRead,
    PlatformAiCredentialStatusRead,
    PlatformAiCredentialWrite,
)
from app.services.platform_model_credential_service import (
    InvalidModelCredentialError,
    ModelCredentialAlreadyInactiveError,
    ModelCredentialNotFoundError,
    PlatformModelCredentialService,
    UnknownModelCredentialProviderError,
    UnmanagedModelCredentialProviderError,
    cipher_configured,
    credential_status_dict,
)

router = APIRouter(prefix="/platform/ai-credentials", tags=["Platform"])

_ai_operations_view = require_platform_permission("ai_operations", "view")
_ai_operations_manage = require_platform_permission("ai_operations", "manage")

_RESOURCE_TYPE = "platform_model_credential"


def _http_error_for(error: Exception) -> HTTPException:
    if isinstance(error, UnknownModelCredentialProviderError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PLATFORM-AI-CRED-001", "message": str(error)},
        )
    if isinstance(error, ModelCredentialNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PLATFORM-AI-CRED-002", "message": str(error)},
        )
    if isinstance(error, InvalidModelCredentialError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "PLATFORM-AI-CRED-003", "message": str(error)},
        )
    if isinstance(error, UnmanagedModelCredentialProviderError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "PLATFORM-AI-CRED-004", "message": str(error)},
        )
    if isinstance(error, ModelCredentialAlreadyInactiveError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "PLATFORM-AI-CRED-006", "message": str(error)},
        )
    raise error


def _require_cipher() -> None:
    """Sem cifra forte não se grava nada: 503 explícito em vez de 500 genérico."""
    if not cipher_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PLATFORM-AI-CRED-005",
                "message": (
                    "A cifra de segredos (INTEGRATION_SECRET_ENCRYPTION_KEY) não está "
                    "configurada ou é fraca; a chave não foi gravada."
                ),
            },
        )


def _snapshot(credential: PlatformModelCredential) -> tuple[str, str, dict]:
    """Snapshot de auditoria: nunca contém a chave, só os 4 últimos caracteres."""
    return (
        _RESOURCE_TYPE,
        credential.provider,
        {
            "provider": credential.provider,
            "configured": True,
            "isActive": bool(credential.is_active),
            "keyLast4": credential.api_key_hint,
            "updatedAt": credential.updated_at.isoformat() if credential.updated_at else None,
            "updatedByUserId": (
                str(credential.updated_by_user_id) if credential.updated_by_user_id else None
            ),
        },
    )


@router.get(
    "",
    response_model=PlatformAiCredentialsRead,
    summary="Chaves dos provedores de IA da plataforma (status redigido) e gate",
)
async def list_platform_ai_credentials(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_ai_operations_view),
):
    """Uma entrada por provider; nunca contém a chave em claro."""
    service = PlatformModelCredentialService(db)
    return {
        "gate": service.gate_status(),
        "providers": await service.status_entries(),
    }


@router.put(
    "/{provider}",
    response_model=PlatformAiCredentialStatusRead,
    summary="Grava ou rotaciona a chave de API do provider na plataforma (write-only)",
)
async def put_platform_ai_credential(
    provider: str,
    payload: PlatformAiCredentialWrite,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    _: User = Depends(_ai_operations_manage),
    db: AsyncSession = Depends(get_db),
):
    service = PlatformModelCredentialService(db)
    try:
        existing = await service.get_credential(provider)
    except UnknownModelCredentialProviderError as error:
        raise _http_error_for(error) from error
    _require_cipher()
    before = _snapshot(existing) if existing is not None else None

    try:
        credential = await service.set_credential(
            provider=provider,
            api_key=payload.api_key,
            actor_user_id=context.user_id,
        )
    except (
        UnknownModelCredentialProviderError,
        InvalidModelCredentialError,
        UnmanagedModelCredentialProviderError,
    ) as error:
        raise _http_error_for(error) from error

    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=_snapshot(credential),
        module="ai_operations",
        action="ai_credentials.rotate" if existing is not None else "ai_credentials.create",
        reason=f"Chave de API do provider {credential.provider} gravada pelo Blackbox.",
        reason_source="system",
    )
    response = credential_status_dict(credential.provider, credential)
    await db.commit()
    # Depois do commit: uma chamada concorrente entre a invalidação do serviço e
    # o commit poderia ter recacheado a linha antiga.
    invalidate_platform_credential_cache(credential.provider)
    return response


@router.delete(
    "/{provider}",
    response_model=PlatformAiCredentialStatusRead,
    summary="Desativa a chave de API do provider na plataforma",
)
async def delete_platform_ai_credential(
    provider: str,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    _: User = Depends(_ai_operations_manage),
    db: AsyncSession = Depends(get_db),
):
    service = PlatformModelCredentialService(db)
    try:
        existing = await service.get_credential(provider)
        if existing is None:
            raise ModelCredentialNotFoundError(
                f"Nenhuma chave da plataforma armazenada para o provider {provider}."
            )
        before = _snapshot(existing)
        credential = await service.deactivate_credential(
            provider=provider,
            actor_user_id=context.user_id,
        )
    except (
        UnknownModelCredentialProviderError,
        ModelCredentialNotFoundError,
        ModelCredentialAlreadyInactiveError,
    ) as error:
        raise _http_error_for(error) from error

    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=_snapshot(credential),
        module="ai_operations",
        action="ai_credentials.deactivate",
        reason=f"Chave de API do provider {credential.provider} desativada pelo Blackbox.",
        reason_source="system",
    )
    response = credential_status_dict(credential.provider, credential)
    await db.commit()
    invalidate_platform_credential_cache(credential.provider)
    return response
