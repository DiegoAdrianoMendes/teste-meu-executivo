"""
Endpoints administrativos do blog, mídia e Google Business Profile.
"""
from datetime import datetime
from urllib.parse import unquote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.platform_access import require_platform_module
from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.platform_audit import (
    record_canonical_platform_mutation,
    snapshot_platform_blog_post,
    snapshot_platform_google_business_connection,
    snapshot_platform_google_business_job,
    snapshot_platform_site_document,
    snapshot_platform_site_media_asset,
)
from app.models.user import User
from app.schemas.blog import (
    BlogIndexingSettingsUpdate,
    BlogPostCreate,
    BlogPostMetricsRead,
    BlogPostRead,
    BlogPostSummaryRead,
    BlogPostUpdate,
    BlogSettingsUpdate,
    BlogTaxonomyUpdate,
    EditorialAssistRequest,
    EditorialAssistResponse,
    GeneratedImageResponse,
    GoogleBusinessProfileConnectionRead,
    GoogleBusinessProfileConnectionUpdate,
    GoogleBusinessPublishJobRead,
    GoogleBusinessPublishRequest,
    PlatformDocumentEnvelopeRead,
    SeoAutofillResponse,
    SiteMediaAssetCreate,
    SiteMediaAssetRead,
    SiteMediaConfirmRead,
    SiteMediaPresignRead,
)
from app.services.blog_service import BlogService

router = APIRouter(prefix="/platform/blog", tags=["Platform Blog"])
require_platform_content_access = require_platform_module("content")


def require_platform_blog_reason(
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
    "/posts",
    response_model=list[BlogPostSummaryRead],
    summary="Lista administrativa de posts",
    deprecated=True,
)
async def list_blog_posts(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> list[BlogPostSummaryRead]:
    return await BlogService(db).list_admin_posts()


@router.get("/posts/page", summary="Paginar posts administrativos")
async def page_blog_posts(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> dict:
    rows, total = await BlogService(db).page_admin_posts(limit=limit, offset=offset)
    return {"items": rows, "total": total, "limit": limit, "offset": offset, "hasMore": offset + len(rows) < total}


@router.get(
    "/posts/metrics",
    response_model=BlogPostMetricsRead,
    summary="Métricas agregadas de SEO dos posts",
)
async def get_blog_post_metrics(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> BlogPostMetricsRead:
    return await BlogService(db).get_admin_post_metrics()


@router.post("/posts", response_model=BlogPostRead, status_code=status.HTTP_201_CREATED, summary="Criar post")
async def create_blog_post(
    payload: BlogPostCreate,
    request: Request,
    reason: str = Depends(require_platform_blog_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> BlogPostRead:
    service = BlogService(db)
    result = await service.create_post(payload, context.user_id, commit=False)
    after = await snapshot_platform_blog_post(db, result.id)
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=None,
        after_snapshot=after,
        module="content",
        action="create_blog_post",
        reason=reason,
    )
    return result


@router.get("/posts/{post_id}", response_model=BlogPostRead, summary="Detalhe administrativo do post")
async def get_blog_post(
    post_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> BlogPostRead:
    return await BlogService(db).get_admin_post(post_id)


@router.put("/posts/{post_id}", response_model=BlogPostRead, summary="Salvar rascunho do post")
async def update_blog_post(
    post_id: UUID,
    payload: BlogPostUpdate,
    request: Request,
    reason: str = Depends(require_platform_blog_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> BlogPostRead:
    before = await snapshot_platform_blog_post(db, post_id, for_update=True)
    result = await BlogService(db).update_post(
        post_id, payload, context.user_id, commit=False
    )
    after = await snapshot_platform_blog_post(db, post_id)
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=after,
        module="content",
        action="update_blog_post",
        reason=reason,
    )
    return result


@router.post("/posts/{post_id}/publish", response_model=BlogPostRead, summary="Publicar post")
async def publish_blog_post(
    post_id: UUID,
    request: Request,
    reason: str = Depends(require_platform_blog_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> BlogPostRead:
    before = await snapshot_platform_blog_post(db, post_id, for_update=True)
    result = await BlogService(db).publish_post(
        post_id, context.user_id, commit=False
    )
    after = await snapshot_platform_blog_post(db, post_id)
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=before, after_snapshot=after,
        module="content", action="publish_blog_post", reason=reason,
    )
    return result


@router.post("/posts/{post_id}/schedule", response_model=BlogPostRead, summary="Agendar post")
async def schedule_blog_post(
    post_id: UUID,
    request: Request,
    publish_at: datetime = Query(..., alias="publishAt"),
    reason: str = Depends(require_platform_blog_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> BlogPostRead:
    before = await snapshot_platform_blog_post(db, post_id, for_update=True)
    result = await BlogService(db).schedule_post(
        post_id, publish_at, context.user_id, commit=False
    )
    after = await snapshot_platform_blog_post(db, post_id)
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=before, after_snapshot=after,
        module="content", action="schedule_blog_post", reason=reason,
    )
    return result


@router.post("/posts/{post_id}/archive", response_model=BlogPostRead, summary="Arquivar post")
async def archive_blog_post(
    post_id: UUID,
    request: Request,
    reason: str = Depends(require_platform_blog_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> BlogPostRead:
    before = await snapshot_platform_blog_post(db, post_id, for_update=True)
    result = await BlogService(db).archive_post(
        post_id, context.user_id, commit=False
    )
    after = await snapshot_platform_blog_post(db, post_id)
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=before, after_snapshot=after,
        module="content", action="archive_blog_post", reason=reason,
    )
    return result


@router.post("/posts/{post_id}/duplicate", response_model=BlogPostRead, summary="Duplicar post")
async def duplicate_blog_post(
    post_id: UUID,
    request: Request,
    reason: str = Depends(require_platform_blog_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> BlogPostRead:
    result = await BlogService(db).duplicate_post(
        post_id, context.user_id, commit=False
    )
    after = await snapshot_platform_blog_post(db, result.id)
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=None, after_snapshot=after,
        module="content", action="duplicate_blog_post", reason=reason,
    )
    return result


@router.post("/posts/{post_id}/seo-autofill", response_model=SeoAutofillResponse, summary="Preenchimento one-click de SEO")
async def autofill_blog_post_seo(
    post_id: UUID,
    request: Request,
    reason: str = Depends(require_platform_blog_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> SeoAutofillResponse:
    before = await snapshot_platform_blog_post(db, post_id, for_update=True)
    result = await BlogService(db).generate_seo_autofill(post_id, commit=False)
    after = await snapshot_platform_blog_post(db, post_id)
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=before, after_snapshot=after,
        module="content", action="autofill_blog_post_seo", reason=reason,
    )
    return result


@router.post("/posts/{post_id}/assist", response_model=EditorialAssistResponse, summary="Assistente editorial do post")
async def assist_blog_post(
    post_id: UUID,
    payload: EditorialAssistRequest,
    request: Request,
    reason: str = Depends(require_platform_blog_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> EditorialAssistResponse:
    before = await snapshot_platform_blog_post(db, post_id, for_update=True)
    result = await BlogService(db).run_editorial_assist(post_id, payload)
    after = await snapshot_platform_blog_post(db, post_id)
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=before, after_snapshot=after,
        module="content", action="assist_blog_post", reason=reason,
    )
    return result


@router.post("/posts/{post_id}/generate-cover", response_model=GeneratedImageResponse, summary="Gerar capa do post")
async def generate_blog_post_cover(
    post_id: UUID,
    request: Request,
    reason: str = Depends(require_platform_blog_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> GeneratedImageResponse:
    before = await snapshot_platform_blog_post(db, post_id, for_update=True)
    result = await BlogService(db).generate_cover_asset(
        post_id, context.user_id, commit=False
    )
    after = await snapshot_platform_blog_post(db, post_id)
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=before, after_snapshot=after,
        module="content", action="generate_blog_post_cover", reason=reason,
    )
    return result


@router.get("/settings", response_model=PlatformDocumentEnvelopeRead, summary="Configurações do blog")
async def get_blog_settings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> PlatformDocumentEnvelopeRead:
    return await BlogService(db).get_admin_doc("blog_settings")


@router.put("/settings", response_model=PlatformDocumentEnvelopeRead, summary="Salvar rascunho das configurações do blog")
async def update_blog_settings(
    payload: BlogSettingsUpdate,
    request: Request,
    reason: str = Depends(require_platform_blog_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> PlatformDocumentEnvelopeRead:
    service = BlogService(db)
    await service.ensure_defaults()
    before = await snapshot_platform_site_document(db, "blog_settings", for_update=True)
    result = await service.update_doc(
        "blog_settings", payload.model_dump(by_alias=False), context.user_id,
        commit=False,
    )
    after = await snapshot_platform_site_document(db, "blog_settings")
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=before, after_snapshot=after,
        module="content", action="update_blog_settings", reason=reason,
    )
    return result


@router.get("/taxonomy", response_model=PlatformDocumentEnvelopeRead, summary="Taxonomia do blog")
async def get_blog_taxonomy(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> PlatformDocumentEnvelopeRead:
    return await BlogService(db).get_admin_doc("blog_taxonomy")


@router.put("/taxonomy", response_model=PlatformDocumentEnvelopeRead, summary="Salvar taxonomia do blog")
async def update_blog_taxonomy(
    payload: BlogTaxonomyUpdate,
    request: Request,
    reason: str = Depends(require_platform_blog_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> PlatformDocumentEnvelopeRead:
    service = BlogService(db)
    await service.ensure_defaults()
    before = await snapshot_platform_site_document(db, "blog_taxonomy", for_update=True)
    result = await service.update_doc(
        "blog_taxonomy", payload.model_dump(by_alias=False), context.user_id,
        commit=False,
    )
    after = await snapshot_platform_site_document(db, "blog_taxonomy")
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=before, after_snapshot=after,
        module="content", action="update_blog_taxonomy", reason=reason,
    )
    return result


@router.get("/indexing", response_model=PlatformDocumentEnvelopeRead, summary="SEO e indexação globais do blog")
async def get_blog_indexing(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> PlatformDocumentEnvelopeRead:
    return await BlogService(db).get_admin_doc("blog_indexing")


@router.put("/indexing", response_model=PlatformDocumentEnvelopeRead, summary="Salvar indexação global do blog")
async def update_blog_indexing(
    payload: BlogIndexingSettingsUpdate,
    request: Request,
    reason: str = Depends(require_platform_blog_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> PlatformDocumentEnvelopeRead:
    service = BlogService(db)
    await service.ensure_defaults()
    before = await snapshot_platform_site_document(db, "blog_indexing", for_update=True)
    result = await service.update_doc(
        "blog_indexing", payload.model_dump(by_alias=False), context.user_id,
        commit=False,
    )
    after = await snapshot_platform_site_document(db, "blog_indexing")
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=before, after_snapshot=after,
        module="content", action="update_blog_indexing", reason=reason,
    )
    return result


@router.post("/publish/{resource}", response_model=PlatformDocumentEnvelopeRead, summary="Publicar documento global do blog")
async def publish_blog_resource(
    resource: str,
    request: Request,
    reason: str = Depends(require_platform_blog_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> PlatformDocumentEnvelopeRead:
    service = BlogService(db)
    if resource not in service.DOC_KEYS:
        raise HTTPException(status_code=404, detail="Documento do blog não encontrado.")
    await service.ensure_defaults()
    before = await snapshot_platform_site_document(db, resource, for_update=True)
    result = await service.publish_doc(resource, context.user_id, commit=False)
    after = await snapshot_platform_site_document(db, resource)
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=before, after_snapshot=after,
        module="content", action="publish_blog_resource", reason=reason,
    )
    return result


@router.get(
    "/media/assets",
    response_model=list[SiteMediaAssetRead],
    summary="Biblioteca global de mídia",
    deprecated=True,
)
async def list_blog_media_assets(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> list[SiteMediaAssetRead]:
    return await BlogService(db).list_media_assets()


@router.get("/media/assets/page", summary="Paginar biblioteca global de mídia")
async def page_blog_media_assets(
    limit: int = Query(default=24, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> dict:
    rows, total = await BlogService(db).page_media_assets(limit=limit, offset=offset)
    return {"items": rows, "total": total, "limit": limit, "offset": offset, "hasMore": offset + len(rows) < total}


@router.post("/media/assets", response_model=SiteMediaPresignRead, status_code=status.HTTP_201_CREATED, summary="Criar asset e URL de upload")
async def create_blog_media_asset(
    payload: SiteMediaAssetCreate,
    request: Request,
    reason: str = Depends(require_platform_blog_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> SiteMediaPresignRead:
    result = await BlogService(db).create_media_upload(
        payload, context.user_id, commit=False
    )
    after = await snapshot_platform_site_media_asset(db, result.asset.id)
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=None, after_snapshot=after,
        module="content", action="create_blog_media_asset", reason=reason,
    )
    return result


@router.post("/media/assets/{asset_id}/confirm", response_model=SiteMediaConfirmRead, summary="Confirmar upload do asset")
async def confirm_blog_media_asset(
    asset_id: UUID,
    request: Request,
    reason: str = Depends(require_platform_blog_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> SiteMediaConfirmRead:
    before = await snapshot_platform_site_media_asset(db, asset_id, for_update=True)
    result = await BlogService(db).confirm_media_upload(asset_id, commit=False)
    after = await snapshot_platform_site_media_asset(db, asset_id)
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=before, after_snapshot=after,
        module="content", action="confirm_blog_media_asset", reason=reason,
    )
    return result


@router.get("/google-business", response_model=GoogleBusinessProfileConnectionRead, summary="Conexão Google Business Profile")
async def get_google_business_connection(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> GoogleBusinessProfileConnectionRead:
    return await BlogService(db).get_google_business_connection()


@router.put("/google-business", response_model=GoogleBusinessProfileConnectionRead, summary="Salvar conexão Google Business Profile")
async def update_google_business_connection(
    payload: GoogleBusinessProfileConnectionUpdate,
    request: Request,
    reason: str = Depends(require_platform_blog_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> GoogleBusinessProfileConnectionRead:
    service = BlogService(db)
    await service.ensure_defaults()
    before = await snapshot_platform_google_business_connection(db, for_update=True)
    result = await service.update_google_business_connection(
        payload, context.user_id, commit=False
    )
    after = await snapshot_platform_google_business_connection(db, result.id)
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=before, after_snapshot=after,
        module="content", action="update_google_business_connection", reason=reason,
    )
    return result


@router.get(
    "/google-business/jobs",
    response_model=list[GoogleBusinessPublishJobRead],
    summary="Fila de publicação do Google Business Profile",
    deprecated=True,
)
async def list_google_business_jobs(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> list[GoogleBusinessPublishJobRead]:
    return await BlogService(db).list_google_business_jobs()


@router.get("/google-business/jobs/page", summary="Paginar jobs do Google Business Profile")
async def page_google_business_jobs(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> dict:
    rows, total = await BlogService(db).page_google_business_jobs(limit=limit, offset=offset)
    return {"items": rows, "total": total, "limit": limit, "offset": offset, "hasMore": offset + len(rows) < total}


@router.post("/google-business/jobs", response_model=GoogleBusinessPublishJobRead, status_code=status.HTTP_201_CREATED, summary="Criar job de publicação no Google Business Profile")
async def queue_google_business_job(
    payload: GoogleBusinessPublishRequest,
    request: Request,
    reason: str = Depends(require_platform_blog_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_content_access),
) -> GoogleBusinessPublishJobRead:
    result = await BlogService(db).queue_google_publish(
        payload, context.user_id, commit=False
    )
    after = await snapshot_platform_google_business_job(db, result.id)
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=None, after_snapshot=after,
        module="content", action="queue_google_business_job", reason=reason,
    )
    return result
