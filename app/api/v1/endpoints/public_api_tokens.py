from __future__ import annotations

from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user_id, get_current_workspace_id, get_db, require_permission
from app.schemas.public_api import (
    PublicApiRequestLogRead,
    WorkspacePublicApiTokenCreate,
    WorkspacePublicApiTokenCreateResponse,
    WorkspacePublicApiTokenRead,
)
from app.services.public_api_service import PublicApiService

router = APIRouter(prefix="/integrations/public-api-tokens", tags=["Public API Tokens"])


def _get_service(db: AsyncSession = Depends(get_db)) -> PublicApiService:
    return PublicApiService(db)


@router.get("", response_model=List[WorkspacePublicApiTokenRead], summary="Listar tokens da API externa do workspace")
async def list_public_api_tokens(
    svc: PublicApiService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("integrations", "view")),
):
    return await svc.list_tokens(workspace_id)


@router.get("/logs", response_model=List[PublicApiRequestLogRead], summary="Listar auditoria recente da API externa")
async def list_public_api_logs(
    svc: PublicApiService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("integrations", "view")),
):
    return await svc.list_logs(workspace_id)


@router.post("", response_model=WorkspacePublicApiTokenCreateResponse, status_code=status.HTTP_201_CREATED, summary="Criar token da API externa")
async def create_public_api_token(
    payload: WorkspacePublicApiTokenCreate,
    svc: PublicApiService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    user_id: UUID = Depends(get_current_user_id),
    _: object = Depends(require_permission("integrations", "manage")),
):
    return await svc.create_token(workspace_id, user_id, payload)


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Revogar token da API externa")
async def revoke_public_api_token(
    token_id: UUID,
    svc: PublicApiService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("integrations", "manage")),
):
    await svc.revoke_token(workspace_id, token_id)
