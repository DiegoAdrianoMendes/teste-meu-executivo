"""Rotas isoladas dos Studios analíticos do Blackbox."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.platform_audit import (
    record_canonical_platform_mutation,
    snapshot_platform_config_document,
)
from app.core.platform_access import require_platform_permission, require_platform_root
from app.core.platform_rbac import has_platform_permission, is_platform_root
from app.models.user import User
from app.schemas.platform_blackbox_studios import (
    AdvancedReportRead,
    CreativeLibraryArchiveRequest,
    CreativeLibraryItemInput,
    CreativeLibraryPageRead,
    CreativeLibraryRead,
    PerformanceBoardRead,
)
from app.services.configuration_service import ConfigConflictError, ConfigValidationError
from app.services.platform_blackbox_studios_service import (
    CREATIVE_LIBRARY_NAMESPACE,
    PlatformBlackboxStudiosService,
)

router = APIRouter(
    prefix="/platform/blackbox-studios",
    tags=["Blackbox Studios"],
)

_performance_view = require_platform_permission("performance", "view")
_reports_view = require_platform_permission("reports", "view")


def _raise_write_error(error: Exception) -> None:
    if isinstance(error, LookupError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "BLACKBOX-STUDIO-NOT-FOUND", "message": str(error)},
        ) from error
    if isinstance(error, ConfigConflictError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "BLACKBOX-STUDIO-CONFLICT", "message": str(error)},
        ) from error
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": "BLACKBOX-STUDIO-INVALID", "message": str(error)},
    ) from error


@router.get("/performance/workspace-team", response_model=PerformanceBoardRead)
async def workspace_team_performance(
    period_days: int = Query(default=30, ge=1, le=365, alias="periodDays"),
    workspace_id: UUID | None = Query(default=None, alias="workspaceId"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_root),
) -> PerformanceBoardRead:
    return await PlatformBlackboxStudiosService(db).workspace_team_performance(
        period_days=period_days,
        workspace_id=workspace_id,
        limit=limit,
        offset=offset,
    )


@router.get("/performance/saas-team", response_model=PerformanceBoardRead)
async def saas_team_performance(
    period_days: int = Query(default=30, ge=1, le=365, alias="periodDays"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_performance_view),
) -> PerformanceBoardRead:
    return await PlatformBlackboxStudiosService(db).saas_team_performance(
        period_days=period_days,
        limit=limit,
        offset=offset,
    )


@router.get("/performance/workspace-benchmark", response_model=PerformanceBoardRead)
async def workspace_benchmark(
    period_days: int = Query(default=30, ge=1, le=365, alias="periodDays"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_root),
) -> PerformanceBoardRead:
    return await PlatformBlackboxStudiosService(db).workspace_benchmark(
        period_days=period_days,
        limit=limit,
        offset=offset,
    )


@router.get("/creative-library/page", response_model=CreativeLibraryPageRead)
async def get_creative_library_page(
    status_filter: Literal["active", "approved", "draft", "archived", "all"] = Query(
        default="active", alias="status"
    ),
    query: str | None = Query(default=None, max_length=160),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_root),
) -> CreativeLibraryPageRead:
    return await PlatformBlackboxStudiosService(db).creative_library_page(
        status_filter=status_filter,
        query=query,
        limit=limit,
        offset=offset,
    )


@router.get("/creative-library", response_model=CreativeLibraryRead, deprecated=True)
async def get_creative_library(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_root),
) -> CreativeLibraryRead:
    return await PlatformBlackboxStudiosService(db).creative_library()


@router.put("/creative-library/items", response_model=CreativeLibraryRead)
async def upsert_creative_library_item(
    data: CreativeLibraryItemInput,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_root),
    context: AuthContext = Depends(get_current_auth_context),
) -> CreativeLibraryRead:
    try:
        before = await snapshot_platform_config_document(
            db, CREATIVE_LIBRARY_NAMESPACE, for_update=True
        )
        result = await PlatformBlackboxStudiosService(db).upsert_creative(
            data=data,
            actor_user_id=actor.id,
        )
        after = await snapshot_platform_config_document(db, CREATIVE_LIBRARY_NAMESPACE)
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=after,
            module="marketing",
            action="upsert_creative_library_item",
            reason=f"Atualização do item criativo {data.name.strip()}.",
            reason_source="system",
        )
        return result
    except (ConfigConflictError, ConfigValidationError, ValueError) as error:
        await db.rollback()
        _raise_write_error(error)


@router.post(
    "/creative-library/items/{item_id}/archive",
    response_model=CreativeLibraryRead,
)
async def archive_creative_library_item(
    item_id: str,
    data: CreativeLibraryArchiveRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_root),
    context: AuthContext = Depends(get_current_auth_context),
) -> CreativeLibraryRead:
    try:
        before = await snapshot_platform_config_document(
            db, CREATIVE_LIBRARY_NAMESPACE, for_update=True
        )
        result = await PlatformBlackboxStudiosService(db).archive_creative(
            item_id=item_id,
            actor_user_id=actor.id,
            expected_etag=data.expected_etag,
        )
        after = await snapshot_platform_config_document(db, CREATIVE_LIBRARY_NAMESPACE)
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=after,
            module="marketing",
            action="archive_creative_library_item",
            reason=f"Arquivamento do item criativo {item_id}.",
            reason_source="system",
        )
        return result
    except (LookupError, ConfigConflictError, ConfigValidationError, ValueError) as error:
        await db.rollback()
        _raise_write_error(error)


@router.get("/reports", response_model=AdvancedReportRead)
async def advanced_reports(
    period_days: int = Query(default=30, ge=1, le=365, alias="periodDays"),
    category: str | None = Query(default=None),
    query: str | None = Query(default=None, max_length=160),
    only_with_data: bool = Query(default=False, alias="onlyWithData"),
    sort_by: Literal["label", "category", "value"] = Query(default="label", alias="sortBy"),
    sort_direction: Literal["asc", "desc"] = Query(default="asc", alias="sortDirection"),
    limit: int = Query(default=150, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_reports_view),
) -> AdvancedReportRead:
    return await PlatformBlackboxStudiosService(db).advanced_report(
        period_days=period_days,
        category=category,
        query=query,
        only_with_data=only_with_data,
        sort_by=sort_by,
        sort_direction=sort_direction,
        limit=limit,
        offset=offset,
        include_financial=bool(
            is_platform_root(actor)
            or has_platform_permission(actor, "finance", "view")
        ),
        include_sensitive=is_platform_root(actor),
    )
