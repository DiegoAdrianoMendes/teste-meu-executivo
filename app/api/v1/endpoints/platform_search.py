"""Endpoints POST da busca global e dos seletores seguros do Blackbox."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.identity_surface import has_active_platform_access
from app.models.user import User
from app.schemas.platform_search import (
    PlatformCommandCatalogResponse,
    PlatformLookupQueryRequest,
    PlatformLookupResolveRequest,
    PlatformLookupResponse,
    PlatformSearchRequest,
    PlatformSearchResponse,
)
from app.services.platform_command_catalog_service import PlatformCommandCatalogService
from app.services.platform_search_service import (
    PlatformLookupCursorError,
    PlatformLookupPermissionError,
    PlatformSearchService,
)

router = APIRouter(prefix="/platform", tags=["Platform Search"])


async def require_platform_search_actor(
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> User:
    if context.surface != "platform":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "BLACKBOX-IDENTITY-REQUIRED",
                "message": "Identidade Blackbox obrigatória.",
            },
        )
    actor = await db.get(User, context.user_id)
    if (
        actor is None
        or not actor.is_active
        or actor.deleted_at is not None
        or not has_active_platform_access(actor)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "PLATFORM-ACCESS-DENIED",
                "message": "Acesso interno à plataforma indisponível.",
            },
        )
    return actor


@router.get("/commands/catalog", response_model=PlatformCommandCatalogResponse)
async def get_platform_command_catalog(
    response: Response,
    actor: User = Depends(require_platform_search_actor),
) -> PlatformCommandCatalogResponse:
    response.headers["Cache-Control"] = "no-store"
    return PlatformCommandCatalogService.catalog(actor)


@router.post("/search/query", response_model=PlatformSearchResponse)
async def search_platform(
    payload: PlatformSearchRequest,
    response: Response,
    actor: User = Depends(require_platform_search_actor),
    db: AsyncSession = Depends(get_db),
) -> PlatformSearchResponse:
    response.headers["Cache-Control"] = "no-store"
    return await PlatformSearchService(db).search(
        actor=actor,
        query=payload.query,
        types=payload.types,
        limit_per_type=payload.limit_per_type,
        include_inactive=payload.include_inactive,
    )


@router.post("/lookups/query", response_model=PlatformLookupResponse)
async def query_platform_lookup(
    payload: PlatformLookupQueryRequest,
    response: Response,
    actor: User = Depends(require_platform_search_actor),
    db: AsyncSession = Depends(get_db),
) -> PlatformLookupResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await PlatformSearchService(db).lookup(
            actor=actor,
            entity_type=payload.entity_type,
            purpose=payload.purpose,
            query=payload.query,
            workspace_id=payload.workspace_id,
            status_filter=payload.status,
            cursor=payload.cursor,
            limit=payload.limit,
        )
    except PlatformLookupPermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "PLATFORM-PERMISSION-DENIED",
                "message": "Seu pacote de acesso não autoriza este seletor.",
            },
        ) from exc
    except PlatformLookupCursorError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "PLATFORM-LOOKUP-CURSOR-INVALID", "message": str(exc)},
        ) from exc


@router.post("/lookups/resolve", response_model=PlatformLookupResponse)
async def resolve_platform_lookup(
    payload: PlatformLookupResolveRequest,
    response: Response,
    actor: User = Depends(require_platform_search_actor),
    db: AsyncSession = Depends(get_db),
) -> PlatformLookupResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await PlatformSearchService(db).resolve(
            actor=actor,
            entity_type=payload.entity_type,
            purpose=payload.purpose,
            ids=payload.ids,
            workspace_id=payload.workspace_id,
        )
    except PlatformLookupPermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "PLATFORM-PERMISSION-DENIED",
                "message": "Seu pacote de acesso não autoriza este seletor.",
            },
        ) from exc
