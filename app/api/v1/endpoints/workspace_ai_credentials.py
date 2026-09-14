"""
Credenciais BYOK de LLM por workspace (write-only).

PUT armazena/rotaciona a chave cifrada; GET e DELETE nunca expõem o valor —
somente a máscara com os últimos 4 caracteres. Toda mudança gera KlmAuditEvent
e invalida o cache de resolução do gateway.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    AuthContext,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.models.workspace_model_credential import (
    MODEL_CREDENTIAL_PROVIDERS,
    WorkspaceModelCredential,
)
from app.schemas.workspace_ai_credentials import (
    WorkspaceAiCredentialRead,
    WorkspaceAiCredentialStatusRead,
    WorkspaceAiCredentialWrite,
)
from app.services.workspace_model_credential_service import (
    InvalidModelCredentialError,
    ModelCredentialNotFoundError,
    UnknownModelCredentialProviderError,
    WorkspaceModelCredentialService,
    credential_to_redacted_dict,
)

router = APIRouter(prefix="/workspaces/ai-credentials", tags=["Workspaces"])


def _http_error_for(error: Exception) -> HTTPException:
    if isinstance(error, UnknownModelCredentialProviderError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "AI-CRED-001", "message": str(error)},
        )
    if isinstance(error, ModelCredentialNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "AI-CRED-002", "message": str(error)},
        )
    if isinstance(error, InvalidModelCredentialError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "AI-CRED-003", "message": str(error)},
        )
    raise error


@router.get(
    "",
    response_model=list[WorkspaceAiCredentialStatusRead],
    summary="Status redigido das credenciais BYOK de todos os providers",
)
async def list_ai_credentials(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("integrations", "view")),
    db: AsyncSession = Depends(get_db),
):
    """Uma entrada por provider do catálogo; nunca contém a chave em claro."""
    from sqlalchemy import select

    credentials = (
        (
            await db.execute(
                select(WorkspaceModelCredential).where(
                    WorkspaceModelCredential.workspace_id == workspace_id
                )
            )
        )
        .scalars()
        .all()
    )
    by_provider = {credential.provider: credential for credential in credentials}
    entries: list[dict] = []
    for provider in MODEL_CREDENTIAL_PROVIDERS:
        credential = by_provider.get(provider)
        if credential is None:
            entries.append(
                {
                    "provider": provider,
                    "configured": False,
                    "isActive": False,
                    "apiKeyMasked": None,
                    "updatedAt": None,
                }
            )
            continue
        entries.append(credential_to_redacted_dict(credential))
    return entries


@router.get(
    "/{provider}",
    response_model=WorkspaceAiCredentialRead,
    summary="Status redigido da credencial BYOK do provider",
)
async def get_ai_credential(
    provider: str,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("integrations", "view")),
    db: AsyncSession = Depends(get_db),
):
    service = WorkspaceModelCredentialService(db)
    try:
        credential = await service.get_credential(workspace_id, provider)
    except UnknownModelCredentialProviderError as error:
        raise _http_error_for(error) from error
    if credential is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "AI-CRED-002",
                "message": f"Nenhuma credencial armazenada para o provider {provider}.",
            },
        )
    return credential_to_redacted_dict(credential)


@router.put(
    "/{provider}",
    response_model=WorkspaceAiCredentialRead,
    summary="Armazena ou rotaciona a chave de API do provider (write-only)",
)
async def put_ai_credential(
    provider: str,
    payload: WorkspaceAiCredentialWrite,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_permission("integrations", "manage")),
    db: AsyncSession = Depends(get_db),
):
    service = WorkspaceModelCredentialService(db)
    try:
        credential = await service.set_credential(
            workspace_id=workspace_id,
            provider=provider,
            api_key=payload.api_key,
            actor_user_id=context.user_id,
        )
    except (UnknownModelCredentialProviderError, InvalidModelCredentialError) as error:
        raise _http_error_for(error) from error
    await db.commit()
    return credential_to_redacted_dict(credential)


@router.delete(
    "/{provider}",
    response_model=WorkspaceAiCredentialRead,
    summary="Desativa a credencial BYOK do provider",
)
async def delete_ai_credential(
    provider: str,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_permission("integrations", "manage")),
    db: AsyncSession = Depends(get_db),
):
    service = WorkspaceModelCredentialService(db)
    try:
        credential = await service.deactivate_credential(
            workspace_id=workspace_id,
            provider=provider,
            actor_user_id=context.user_id,
        )
    except (UnknownModelCredentialProviderError, ModelCredentialNotFoundError) as error:
        raise _http_error_for(error) from error
    await db.commit()
    return credential_to_redacted_dict(credential)
