"""
Endpoints administrativos do site público.
"""
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.platform_access import require_platform_module
from app.core.platform_audit import (
    record_canonical_platform_mutation,
    snapshot_platform_site_document,
)
from app.models.user import User
from app.schemas.public_site import (
    PlatformSiteDocumentEnvelopeRead,
    PublicFaqDocumentUpdate,
    PublicHomeDocumentUpdate,
    PublicLegalDocumentUpdate,
    PublicMediaLibraryUpdate,
    PublicSiteSettingsUpdate,
)
from app.services.public_site_service import PublicSiteService

router = APIRouter(prefix="/platform/site", tags=["Platform Site"])
require_platform_content_access = require_platform_module("content")


def require_platform_site_reason(
    reason: str = Header(
        ..., alias="X-Operation-Reason", max_length=3000
    ),
) -> str:
    normalized = unquote(reason).strip()
    if not 8 <= len(normalized) <= 500:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="O motivo deve conter de 8 a 500 caracteres não vazios.",
        )
    return normalized


@router.get(
    "/settings",
    response_model=PlatformSiteDocumentEnvelopeRead,
    summary="Configurações do site público",
)
async def get_platform_site_settings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> PlatformSiteDocumentEnvelopeRead:
    return await PublicSiteService(db).get_admin_document("settings")


@router.put(
    "/settings",
    response_model=PlatformSiteDocumentEnvelopeRead,
    summary="Salvar rascunho de configurações do site",
)
async def update_platform_site_settings(
    payload: PublicSiteSettingsUpdate,
    request: Request,
    reason: str = Depends(require_platform_site_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> PlatformSiteDocumentEnvelopeRead:
    service = PublicSiteService(db)
    await service.ensure_defaults()
    before = await snapshot_platform_site_document(db, "settings", for_update=True)
    result = await service.update_settings(payload, context.user_id, commit=False)
    after = await snapshot_platform_site_document(db, "settings")
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=after,
        module="content",
        action="update_platform_site_settings",
        reason=reason,
    )
    return result


@router.get("/home", response_model=PlatformSiteDocumentEnvelopeRead, summary="Conteúdo da home")
async def get_platform_site_home(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> PlatformSiteDocumentEnvelopeRead:
    return await PublicSiteService(db).get_admin_document("home")


@router.put(
    "/home",
    response_model=PlatformSiteDocumentEnvelopeRead,
    summary="Salvar rascunho da home",
)
async def update_platform_site_home(
    payload: PublicHomeDocumentUpdate,
    request: Request,
    reason: str = Depends(require_platform_site_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> PlatformSiteDocumentEnvelopeRead:
    service = PublicSiteService(db)
    await service.ensure_defaults()
    before = await snapshot_platform_site_document(db, "home", for_update=True)
    result = await service.update_home(payload, context.user_id, commit=False)
    after = await snapshot_platform_site_document(db, "home")
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=after,
        module="content",
        action="update_platform_site_home",
        reason=reason,
    )
    return result


@router.get("/faq", response_model=PlatformSiteDocumentEnvelopeRead, summary="FAQ do site")
async def get_platform_site_faq(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> PlatformSiteDocumentEnvelopeRead:
    return await PublicSiteService(db).get_admin_document("faq")


@router.put(
    "/faq",
    response_model=PlatformSiteDocumentEnvelopeRead,
    summary="Salvar rascunho do FAQ",
)
async def update_platform_site_faq(
    payload: PublicFaqDocumentUpdate,
    request: Request,
    reason: str = Depends(require_platform_site_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> PlatformSiteDocumentEnvelopeRead:
    service = PublicSiteService(db)
    await service.ensure_defaults()
    before = await snapshot_platform_site_document(db, "faq", for_update=True)
    result = await service.update_faq(payload, context.user_id, commit=False)
    after = await snapshot_platform_site_document(db, "faq")
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=after,
        module="content",
        action="update_platform_site_faq",
        reason=reason,
    )
    return result


@router.get(
    "/media",
    response_model=PlatformSiteDocumentEnvelopeRead,
    summary="Biblioteca de mídias do site",
)
async def get_platform_site_media(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> PlatformSiteDocumentEnvelopeRead:
    return await PublicSiteService(db).get_admin_document("media")


@router.put(
    "/media",
    response_model=PlatformSiteDocumentEnvelopeRead,
    summary="Salvar rascunho da biblioteca de mídias",
)
async def update_platform_site_media(
    payload: PublicMediaLibraryUpdate,
    request: Request,
    reason: str = Depends(require_platform_site_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> PlatformSiteDocumentEnvelopeRead:
    service = PublicSiteService(db)
    await service.ensure_defaults()
    before = await snapshot_platform_site_document(db, "media", for_update=True)
    result = await service.update_media(payload, context.user_id, commit=False)
    after = await snapshot_platform_site_document(db, "media")
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=after,
        module="content",
        action="update_platform_site_media",
        reason=reason,
    )
    return result


@router.get(
    "/legal/{kind}",
    response_model=PlatformSiteDocumentEnvelopeRead,
    summary="Documento legal do site",
)
async def get_platform_site_legal(
    kind: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> PlatformSiteDocumentEnvelopeRead:
    return await PublicSiteService(db).get_admin_document(kind)


@router.put(
    "/legal/{kind}",
    response_model=PlatformSiteDocumentEnvelopeRead,
    summary="Salvar rascunho do documento legal",
)
async def update_platform_site_legal(
    kind: str,
    payload: PublicLegalDocumentUpdate,
    request: Request,
    reason: str = Depends(require_platform_site_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> PlatformSiteDocumentEnvelopeRead:
    service = PublicSiteService(db)
    key = service.validate_legal_key(kind)
    await service.ensure_defaults()
    before = await snapshot_platform_site_document(db, key, for_update=True)
    result = await service.update_legal(key, payload, context.user_id, commit=False)
    after = await snapshot_platform_site_document(db, key)
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=after,
        module="content",
        action="update_platform_site_legal",
        reason=reason,
    )
    return result


@router.post(
    "/publish/{resource}",
    response_model=PlatformSiteDocumentEnvelopeRead,
    summary="Publicar recurso do site",
)
async def publish_platform_site_resource(
    resource: str,
    request: Request,
    reason: str = Depends(require_platform_site_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> PlatformSiteDocumentEnvelopeRead:
    service = PublicSiteService(db)
    key = service.validate_key(resource)
    await service.ensure_defaults()
    before = await snapshot_platform_site_document(db, key, for_update=True)
    result = await service.publish_document(key, context.user_id, commit=False)
    after = await snapshot_platform_site_document(db, key)
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=after,
        module="content",
        action="publish_platform_site_resource",
        reason=reason,
    )
    return result
