"""
Service do blog público, CMS editorial e indexação.
"""
from __future__ import annotations

import base64
import html
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4
from xml.sax.saxutils import escape

from fastapi import HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.platform_audit import snapshot_platform_blog_post
from app.models.blog_post import BlogPost, BlogPostStatus
from app.models.google_business import (
    GoogleBusinessConnectionStatus,
    GoogleBusinessProfileConnection,
    GoogleBusinessPublishJob,
    GoogleBusinessPublishStatus,
)
from app.models.public_site_document import PublicSiteDocument
from app.models.site_media_asset import SiteMediaAsset, SiteMediaAssetStatus
from app.schemas.blog import (
    BlogCategoryPayloadRead,
    BlogIndexingSettingsRead,
    BlogIndexRead,
    BlogPostCreate,
    BlogPostMetricsRead,
    BlogPostRead,
    BlogPostSummaryRead,
    BlogPostUpdate,
    BlogSeoRead,
    BlogSettingsRead,
    BlogTaxonomyRead,
    EditorialAssistRequest,
    EditorialAssistResponse,
    GeneratedImageResponse,
    GoogleBusinessProfileConnectionRead,
    GoogleBusinessProfileConnectionUpdate,
    GoogleBusinessPublishJobRead,
    GoogleBusinessPublishRequest,
    PlatformDocumentEnvelopeRead,
    SeoAuditRead,
    SeoAutofillResponse,
    SiteMediaAssetCreate,
    SiteMediaAssetRead,
    SiteMediaConfirmRead,
    SiteMediaPresignRead,
)
from app.schemas.platform_audit_event import PlatformAuditEventCreate
from app.services.platform_audit_event_service import PlatformAuditEventService
from app.services.s3_service import S3Service, StorageUnavailableError
from app.services.workspace_service import slugify

BLOG_DOCUMENT_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "blog_settings": {
        "pageTitle": "Blog Meu Executivo",
        "intro": "Conteúdos sobre atendimento jurídico, intake, CRM, automação, IA humanizada e fechamento organizado para escritórios.",
        "ctaLabel": "Agendar demonstração",
        "ctaUrl": "https://wa.me/5561993328108",
        "aiProviderText": "heuristic",
        "aiProviderImage": "fallback",
        "seo": {
            "title": "Blog Meu Executivo",
            "description": "Conteúdos sobre atendimento jurídico, CRM, IA humanizada, intake e fechamento para advocacia.",
            "canonicalUrl": "https://klmadvogados.com/blog",
            "ogImageUrl": None,
            "keywords": ["blog jurídico", "crm jurídico", "ia para advocacia"],
            "socialCopy": "Conteúdos do Meu Executivo sobre atendimento jurídico e advocacia estratégica.",
        },
    },
    "blog_taxonomy": {
        "categories": ["Atendimento jurídico", "CRM jurídico", "IA humanizada", "WhatsApp", "Intake"],
        "tags": ["advocacia", "atendimento", "whatsapp", "intake", "crm", "ia"],
    },
    "blog_indexing": {
        "siteUrl": "https://klmadvogados.com",
        "sitemapEnabled": True,
        "rssEnabled": True,
        "llmsEnabled": True,
        "robotsPolicy": "User-agent: *\nAllow: /",
        "llmsSummary": "Meu Executivo é um escritório de advocacia com atuação estratégica e atendimento remoto em todo o Brasil. Os conteúdos abaixo tratam de direitos, prevenção e caminhos jurídicos.",
    },
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_slug(value: str) -> str:
    return slugify(value).strip("-")[:170] or "post"


def _extract_text_from_blocks(blocks: List[dict]) -> str:
    parts: List[str] = []
    for block in blocks:
        if block.get("content"):
            parts.append(str(block["content"]))
        items = block.get("items") or []
        if items:
            parts.extend(str(item) for item in items)
        if block.get("title"):
            parts.append(str(block["title"]))
    return " ".join(parts).strip()


def _compute_reading_time(text: str) -> int:
    words = len(text.split())
    return max(1, round(words / 220)) if words else 1


def _normalize_keywords(text: str, fallback: List[str]) -> List[str]:
    tokens = re.findall(r"[a-zA-ZÀ-ÿ0-9]{4,}", text.lower())
    ranked = [token for token in tokens if token not in {"para", "com", "mais", "como", "lead", "time", "fluxo"}]
    deduped: List[str] = []
    for token in ranked + fallback:
        if token not in deduped:
            deduped.append(token)
        if len(deduped) >= 8:
            break
    return deduped


def _make_excerpt(title: str, subtitle: Optional[str], text: str) -> str:
    if subtitle:
        return subtitle[:220]
    if text:
        trimmed = re.sub(r"\s+", " ", text).strip()
        return trimmed[:220].rstrip() + ("..." if len(trimmed) > 220 else "")
    return f"Leitura sobre {title}"


def _default_indexing() -> dict:
    return {
        "indexable": True,
        "includeInSitemap": True,
        "includeInRss": True,
        "includeInLlms": True,
    }


def _build_seo(title: str, excerpt: str, slug: str, keywords: Optional[List[str]] = None) -> dict:
    return {
        "title": title[:65],
        "description": excerpt[:155],
        "canonicalUrl": f"https://klmadvogados.com/blog/{slug}",
        "ogImageUrl": None,
        "keywords": keywords or [],
        "socialCopy": excerpt[:180],
    }


def _compute_seo_audit(title: str, excerpt: str, blocks: List[dict], seo: dict, indexing: dict) -> dict:
    score = 0
    suggestions: List[str] = []
    detected_keywords = _normalize_keywords(f"{title} {excerpt} {_extract_text_from_blocks(blocks)}", [])

    if 40 <= len(title) <= 65:
        score += 20
    else:
        suggestions.append("Ajuste o título SEO para algo entre 40 e 65 caracteres.")

    description = seo.get("description") or excerpt
    if 120 <= len(description) <= 155:
        score += 20
    else:
        suggestions.append("Use uma meta description entre 120 e 155 caracteres.")

    headings = sum(1 for block in blocks if block.get("type") == "heading")
    if headings >= 2:
        score += 15
    else:
        suggestions.append("Adicione mais intertítulos para facilitar leitura e rastreio semântico.")

    body = _extract_text_from_blocks(blocks)
    if len(body.split()) >= 300:
        score += 15
    else:
        suggestions.append("Amplie o corpo do artigo para ganhar profundidade semântica.")

    if seo.get("keywords"):
        score += 10
    else:
        suggestions.append("Cadastre palavras-chave prioritárias para guiar SEO e snippets.")

    if indexing.get("indexable", True):
        score += 10
    else:
        suggestions.append("Este artigo está como noindex e não será ranqueado.")

    if seo.get("canonicalUrl"):
        score += 10
    else:
        suggestions.append("Defina um canonical para o artigo.")

    return {
        "score": min(score, 100),
        "readingTimeMinutes": _compute_reading_time(body),
        "suggestions": suggestions,
        "detectedKeywords": detected_keywords,
    }


def _render_blocks_to_html(blocks: List[dict]) -> str:
    parts: List[str] = []
    for block in blocks:
        block_type = block.get("type")
        content = html.escape(block.get("content") or "")
        title = html.escape(block.get("title") or "")
        items = block.get("items") or []
        if block_type == "heading":
            level = max(2, min(int(block.get("level") or 2), 4))
            parts.append(f"<h{level}>{content}</h{level}>")
        elif block_type == "quote":
            parts.append(f"<blockquote><p>{content}</p></blockquote>")
        elif block_type == "list":
            li = "".join(f"<li>{html.escape(str(item))}</li>" for item in items)
            parts.append(f"<ul>{li}</ul>")
        elif block_type == "faq":
            faq_items = "".join(f"<li>{html.escape(str(item))}</li>" for item in items)
            parts.append(f"<section><h3>{title or 'FAQ'}</h3><ul>{faq_items}</ul></section>")
        elif block_type == "callout":
            parts.append(f"<aside><strong>{title}</strong><p>{content}</p></aside>")
        else:
            parts.append(f"<p>{content}</p>")
    return "\n".join(parts)


def _render_blocks_to_markdown(blocks: List[dict]) -> str:
    parts: List[str] = []
    for block in blocks:
        block_type = block.get("type")
        content = (block.get("content") or "").strip()
        title = (block.get("title") or "").strip()
        items = block.get("items") or []
        if block_type == "heading":
            level = max(2, min(int(block.get("level") or 2), 4))
            parts.append(f"{'#' * level} {content}")
        elif block_type == "quote":
            parts.append(f"> {content}")
        elif block_type == "list":
            parts.extend(f"- {item}" for item in items)
        elif block_type == "faq":
            parts.append(f"## {title or 'FAQ'}")
            parts.extend(f"- {item}" for item in items)
        elif block_type == "callout":
            parts.append(f"**{title}**\n\n{content}")
        else:
            parts.append(content)
    return "\n\n".join(part for part in parts if part)


def _svg_data_url(title: str, subtitle: Optional[str]) -> str:
    safe_title = html.escape(title)
    safe_subtitle = html.escape((subtitle or "")[:120])
    svg = f"""
    <svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900" viewBox="0 0 1600 900">
      <defs>
        <linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="#0b4f84" />
          <stop offset="100%" stop-color="#11a7d8" />
        </linearGradient>
      </defs>
      <rect width="1600" height="900" fill="#04131f" />
      <circle cx="1320" cy="140" r="220" fill="url(#g)" opacity="0.20" />
      <circle cx="280" cy="720" r="260" fill="url(#g)" opacity="0.14" />
      <rect x="86" y="86" width="1428" height="728" rx="40" fill="#081d2b" stroke="#0f5e95" stroke-opacity="0.35" />
      <text x="120" y="210" fill="#d4af37" font-size="28" font-family="Arial, sans-serif" letter-spacing="4">KLM ADVOGADOS</text>
      <text x="120" y="360" fill="#ffffff" font-size="72" font-weight="700" font-family="Arial, sans-serif">{safe_title}</text>
      <text x="120" y="455" fill="#bcd7e4" font-size="30" font-family="Arial, sans-serif">{safe_subtitle}</text>
    </svg>
    """.strip()
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


class BlogService:
    DOC_KEYS = {"blog_settings", "blog_taxonomy", "blog_indexing"}

    def __init__(self, db: AsyncSession):
        self.db = db
        self.s3 = S3Service()

    async def ensure_defaults(self) -> None:
        dialect_name = self.db.get_bind().dialect.name
        if dialect_name == "postgresql":
            # Documents and the singleton-like Google connection belong to
            # one bootstrap. Serialize its first execution across API workers.
            await self.db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext('blog_service_defaults'))")
            )

        rows = [
            {
                "key": key,
                "title": key.replace("_", " ").title(),
                "description": f"Documento global {key}.",
                "draft_content": content,
                "published_content": content,
                "version": 1,
                "published_at": _utcnow(),
            }
            for key, content in BLOG_DOCUMENT_DEFAULTS.items()
        ]
        if dialect_name == "postgresql":
            statement = postgresql_insert(PublicSiteDocument).values(rows)
            statement = statement.on_conflict_do_nothing(index_elements=[PublicSiteDocument.key])
        elif dialect_name == "sqlite":
            statement = sqlite_insert(PublicSiteDocument).values(rows)
            statement = statement.on_conflict_do_nothing(index_elements=[PublicSiteDocument.key])
        else:  # pragma: no cover - production and tests use PostgreSQL/SQLite.
            raise RuntimeError(f"Unsupported database dialect for blog defaults: {dialect_name}")
        insert_result = await self.db.execute(statement)
        inserted = bool(insert_result.rowcount)

        existing_connection = await self.db.scalar(select(GoogleBusinessProfileConnection))
        if not existing_connection:
            self.db.add(
                GoogleBusinessProfileConnection(
                    business_name="Meu Executivo",
                    status=GoogleBusinessConnectionStatus.disconnected,
                    settings={"publishToGoogleByDefault": False, "multiLocationMode": "single_expandable"},
                )
            )
            inserted = True

        if inserted:
            await self.db.commit()

    async def publish_due_posts(
        self,
        *,
        limit: int = 100,
        now: datetime | None = None,
        commit: bool = True,
    ) -> int:
        reference_now = now or _utcnow()
        operation_id = str(uuid4())
        posts = list(
            (
                await self.db.scalars(
                    select(BlogPost)
                    .where(
                        BlogPost.status == BlogPostStatus.scheduled,
                        BlogPost.scheduled_for.is_not(None),
                        BlogPost.scheduled_for <= reference_now,
                    )
                    .order_by(BlogPost.scheduled_for, BlogPost.id)
                    .with_for_update(skip_locked=True)
                    .limit(max(1, min(limit, 500)))
                )
            ).all()
        )
        for post in posts:
            before_snapshot = await snapshot_platform_blog_post(self.db, post.id)
            before = before_snapshot[2] if before_snapshot is not None else None
            previous_revision = post.revision
            post.status = BlogPostStatus.published
            post.published_at = post.scheduled_for or reference_now
            post.revision += 1
            await self.db.flush()
            after_snapshot = await snapshot_platform_blog_post(self.db, post.id)
            after = after_snapshot[2] if after_snapshot is not None else None
            await PlatformAuditEventService(self.db).record(
                PlatformAuditEventCreate(
                    actor_id=None,
                    actor_role_snapshot="system",
                    is_platform_root=False,
                    session_type="system",
                    module="content",
                    action="publish_due_blog_post",
                    resource_type="blog_post",
                    resource_id=str(post.id),
                    resource_label_snapshot=post.title,
                    result="success",
                    before=before,
                    after=after,
                    reason="Publicação automática de artigo agendado vencido.",
                    reason_source="system",
                    correlation_id=operation_id,
                    operation_id=operation_id,
                    resource_version_before=previous_revision,
                    resource_version_after=post.revision,
                    route="blog_publication_tasks.publish_due",
                    method="WORKER",
                )
            )
        if posts:
            await (self.db.commit() if commit else self.db.flush())
        return len(posts)

    async def list_public_posts(self, limit: int = 20) -> List[BlogPostSummaryRead]:
        result = await self.db.execute(
            select(BlogPost)
            .where(BlogPost.status == BlogPostStatus.published)
            .order_by(BlogPost.featured.desc(), BlogPost.published_at.desc(), BlogPost.updated_at.desc())
            .limit(limit)
        )
        return [self._to_summary(post) for post in result.scalars().all()]

    async def get_public_blog_index(self) -> BlogIndexRead:
        settings = await self.get_blog_settings()
        posts = await self.list_public_posts(limit=50)
        featured_post = next((post for post in posts if post.featured), posts[0] if posts else None)
        categories = await self.get_taxonomy()
        return BlogIndexRead(
            page_title=settings.page_title,
            intro=settings.intro,
            seo=settings.seo,
            featured_post=featured_post,
            posts=posts,
            categories=categories.categories,
        )

    async def get_public_post(self, slug: str) -> BlogPostRead:
        result = await self.db.execute(select(BlogPost).where(BlogPost.slug == slug))
        post = result.scalar_one_or_none()
        if not post or post.status != BlogPostStatus.published:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post não encontrado.")
        return self._to_detail(post)

    async def get_public_posts_by_category(self, category_slug: str) -> BlogCategoryPayloadRead:
        taxonomy = await self.get_taxonomy()
        match = next((item for item in taxonomy.categories if _normalize_slug(item) == category_slug), None)
        if not match:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Categoria não encontrada.")
        posts = [post for post in await self.list_public_posts(limit=100) if match in post.categories]
        return BlogCategoryPayloadRead(
            category=match,
            posts=posts,
            seo=BlogSeoRead(
                title=f"{match} | Blog Meu Executivo",
                description=f"Conteúdos sobre {match.lower()} para advocacia e operação comercial jurídica.",
                canonical_url=f"https://klmadvogados.com/blog/categoria/{category_slug}",
                og_image_url=None,
                keywords=_normalize_keywords(match, ["blog", "meu executivo"]),
                social_copy=f"Leituras sobre {match.lower()} no Meu Executivo.",
            ),
        )

    async def list_admin_posts(self) -> List[BlogPostSummaryRead]:
        result = await self.db.execute(select(BlogPost).order_by(BlogPost.updated_at.desc()))
        return [self._to_summary(post) for post in result.scalars().all()]

    async def page_admin_posts(self, *, limit: int, offset: int) -> tuple[List[BlogPostSummaryRead], int]:
        total = int(await self.db.scalar(select(func.count(BlogPost.id))) or 0)
        rows = list(await self.db.scalars(
            select(BlogPost).order_by(BlogPost.updated_at.desc(), BlogPost.id.desc()).offset(offset).limit(limit)
        ))
        return [self._to_summary(post) for post in rows], total

    async def get_admin_post_metrics(self) -> BlogPostMetricsRead:
        row = (
            await self.db.execute(
                select(
                    func.count(BlogPost.id).label("total"),
                    func.count(BlogPost.id)
                    .filter(BlogPost.indexing["indexable"].as_boolean().is_(True))
                    .label("indexable"),
                    func.count(BlogPost.id)
                    .filter(
                        BlogPost.indexing["includeInSitemap"]
                        .as_boolean()
                        .is_(True)
                    )
                    .label("included_in_sitemap"),
                    func.count(BlogPost.id)
                    .filter(
                        BlogPost.indexing["includeInLlms"]
                        .as_boolean()
                        .is_(True)
                    )
                    .label("included_in_llms"),
                    func.count(BlogPost.id)
                    .filter(BlogPost.seo_audit["score"].as_integer().is_not(None))
                    .label("scored"),
                    func.avg(BlogPost.seo_audit["score"].as_integer()).label(
                        "average_seo_score"
                    ),
                )
            )
        ).one()
        total = int(row.total or 0)
        scored = int(row.scored or 0)
        average_score = (
            round(float(row.average_seo_score))
            if row.average_seo_score is not None
            else None
        )
        return BlogPostMetricsRead(
            total=total,
            indexable=int(row.indexable or 0),
            included_in_sitemap=int(row.included_in_sitemap or 0),
            included_in_llms=int(row.included_in_llms or 0),
            scored=scored,
            average_seo_score=average_score,
            data_state="available" if total > 0 else "insufficient_data",
        )

    async def get_admin_post(self, post_id: UUID) -> BlogPostRead:
        post = await self.db.get(BlogPost, post_id)
        if not post:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post não encontrado.")
        return self._to_detail(post)

    async def create_post(
        self,
        payload: BlogPostCreate,
        user_id: UUID,
        *,
        commit: bool = True,
    ) -> BlogPostRead:
        await self.ensure_defaults()
        blocks = [block.model_dump(by_alias=False) for block in payload.content_blocks]
        title = payload.title.strip()
        slug = _normalize_slug(payload.slug or title)
        excerpt = payload.excerpt or _make_excerpt(title, payload.subtitle, _extract_text_from_blocks(blocks))
        seo = payload.seo.model_dump(by_alias=False) if payload.seo else _build_seo(title, excerpt, slug)
        indexing = payload.indexing.model_dump(by_alias=False) if payload.indexing else _default_indexing()
        audit = _compute_seo_audit(title, excerpt, blocks, seo, indexing)
        post = BlogPost(
            slug=slug,
            title=title,
            subtitle=payload.subtitle,
            excerpt=excerpt,
            author_name=payload.author_name or "Equipe Meu Executivo",
            author_role=payload.author_role,
            featured=payload.featured,
            categories=payload.categories,
            tags=payload.tags,
            content_blocks=blocks,
            cover_media=(payload.cover_media.model_dump(by_alias=False) if payload.cover_media else {"url": _svg_data_url(title, payload.subtitle), "alt": title}),
            og_media=(payload.og_media.model_dump(by_alias=False) if payload.og_media else {"url": _svg_data_url(title, payload.subtitle), "alt": title}),
            seo=seo,
            indexing=indexing,
            seo_audit=audit,
            reading_time_minutes=audit["readingTimeMinutes"],
            ai_config=payload.ai_config,
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
        self.db.add(post)
        await (self.db.commit() if commit else self.db.flush())
        await self.db.refresh(post)
        return self._to_detail(post)

    async def update_post(
        self,
        post_id: UUID,
        payload: BlogPostUpdate,
        user_id: UUID,
        *,
        commit: bool = True,
    ) -> BlogPostRead:
        post = await self.db.get(BlogPost, post_id)
        if not post:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post não encontrado.")

        update = payload.model_dump(exclude_unset=True, by_alias=False)
        for key, value in update.items():
            if key == "content_blocks" and value is not None:
                setattr(post, key, value)
            elif key in {"cover_media", "og_media", "seo", "indexing"} and value is not None:
                setattr(post, key, value)
            elif value is not None:
                setattr(post, key, value)

        if update.get("title") and not update.get("slug"):
            post.slug = _normalize_slug(post.slug or post.title)

        body = _extract_text_from_blocks(post.content_blocks or [])
        post.excerpt = post.excerpt or _make_excerpt(post.title, post.subtitle, body)
        post.seo = post.seo or _build_seo(post.title, post.excerpt, post.slug)
        post.indexing = post.indexing or _default_indexing()
        post.seo_audit = _compute_seo_audit(post.title, post.excerpt or "", post.content_blocks or [], post.seo or {}, post.indexing or {})
        post.reading_time_minutes = post.seo_audit.get("readingTimeMinutes", 1)
        post.updated_by_user_id = user_id
        post.revision += 1

        await (self.db.commit() if commit else self.db.flush())
        await self.db.refresh(post)
        return self._to_detail(post)

    async def publish_post(
        self, post_id: UUID, user_id: UUID, *, commit: bool = True
    ) -> BlogPostRead:
        post = await self.db.get(BlogPost, post_id)
        if not post:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post não encontrado.")
        post.status = BlogPostStatus.published
        post.published_at = _utcnow()
        post.updated_by_user_id = user_id
        post.revision += 1
        await (self.db.commit() if commit else self.db.flush())
        await self.db.refresh(post)
        return self._to_detail(post)

    async def schedule_post(
        self,
        post_id: UUID,
        publish_at: datetime,
        user_id: UUID,
        *,
        commit: bool = True,
    ) -> BlogPostRead:
        post = await self.db.get(BlogPost, post_id)
        if not post:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post não encontrado.")
        post.status = BlogPostStatus.scheduled
        post.scheduled_for = publish_at
        post.updated_by_user_id = user_id
        post.revision += 1
        await (self.db.commit() if commit else self.db.flush())
        await self.db.refresh(post)
        return self._to_detail(post)

    async def archive_post(
        self, post_id: UUID, user_id: UUID, *, commit: bool = True
    ) -> BlogPostRead:
        post = await self.db.get(BlogPost, post_id)
        if not post:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post não encontrado.")
        post.status = BlogPostStatus.archived
        post.updated_by_user_id = user_id
        post.revision += 1
        await (self.db.commit() if commit else self.db.flush())
        await self.db.refresh(post)
        return self._to_detail(post)

    async def duplicate_post(
        self, post_id: UUID, user_id: UUID, *, commit: bool = True
    ) -> BlogPostRead:
        post = await self.db.get(BlogPost, post_id)
        if not post:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post não encontrado.")
        duplicate = BlogPost(
            slug=_normalize_slug(f"{post.slug}-copy"),
            title=f"{post.title} (cópia)",
            subtitle=post.subtitle,
            excerpt=post.excerpt,
            author_name=post.author_name,
            author_role=post.author_role,
            status=BlogPostStatus.draft,
            featured=False,
            categories=list(post.categories or []),
            tags=list(post.tags or []),
            content_blocks=list(post.content_blocks or []),
            cover_media=dict(post.cover_media or {}),
            og_media=dict(post.og_media or {}),
            seo=dict(post.seo or {}),
            indexing=dict(post.indexing or _default_indexing()),
            seo_audit=dict(post.seo_audit or {}),
            ai_config=dict(post.ai_config or {}),
            reading_time_minutes=post.reading_time_minutes,
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
        self.db.add(duplicate)
        await (self.db.commit() if commit else self.db.flush())
        await self.db.refresh(duplicate)
        return self._to_detail(duplicate)

    async def get_blog_settings(self) -> BlogSettingsRead:
        doc = await self._get_doc("blog_settings")
        return BlogSettingsRead.model_validate(doc.published_content)

    async def get_taxonomy(self) -> BlogTaxonomyRead:
        doc = await self._get_doc("blog_taxonomy")
        return BlogTaxonomyRead.model_validate(doc.published_content)

    async def get_indexing_settings(self) -> BlogIndexingSettingsRead:
        doc = await self._get_doc("blog_indexing")
        return BlogIndexingSettingsRead.model_validate(doc.published_content)

    async def get_admin_doc(self, key: str) -> PlatformDocumentEnvelopeRead:
        doc = await self._get_doc(key)
        return PlatformDocumentEnvelopeRead(
            key=doc.key,
            version=doc.version,
            updated_at=doc.updated_at,
            published_at=doc.published_at,
            draft=self._serialize_doc_content(key, doc.draft_content),
            published=self._serialize_doc_content(key, doc.published_content),
        )

    async def update_doc(
        self,
        key: str,
        payload: dict,
        user_id: UUID,
        *,
        commit: bool = True,
    ) -> PlatformDocumentEnvelopeRead:
        doc = await self._get_doc(key)
        doc.draft_content = payload
        doc.updated_by_user_id = user_id
        await (self.db.commit() if commit else self.db.flush())
        await self.db.refresh(doc)
        return await self.get_admin_doc(key)

    async def publish_doc(
        self, key: str, user_id: UUID, *, commit: bool = True
    ) -> PlatformDocumentEnvelopeRead:
        doc = await self._get_doc(key)
        doc.published_content = doc.draft_content
        doc.published_at = _utcnow()
        doc.published_by_user_id = user_id
        doc.version += 1
        await (self.db.commit() if commit else self.db.flush())
        await self.db.refresh(doc)
        return await self.get_admin_doc(key)

    async def generate_seo_autofill(
        self, post_id: UUID, *, commit: bool = True
    ) -> SeoAutofillResponse:
        post = await self.db.get(BlogPost, post_id)
        if not post:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post não encontrado.")
        body = _extract_text_from_blocks(post.content_blocks or [])
        excerpt = _make_excerpt(post.title, post.subtitle, body)
        slug = _normalize_slug(post.slug or post.title)
        seo_payload = _build_seo(post.title, excerpt, slug, _normalize_keywords(f"{post.title} {body}", []))
        audit = _compute_seo_audit(post.title, excerpt, post.content_blocks or [], seo_payload, post.indexing or _default_indexing())
        post.slug = slug
        post.excerpt = excerpt
        post.seo = seo_payload
        post.seo_audit = audit
        post.reading_time_minutes = audit["readingTimeMinutes"]
        post.revision += 1
        await (self.db.commit() if commit else self.db.flush())
        await self.db.refresh(post)
        return SeoAutofillResponse(
            seo=BlogSeoRead.model_validate(seo_payload),
            excerpt=excerpt,
            slug=slug,
            seo_audit=SeoAuditRead.model_validate(audit),
        )

    async def run_editorial_assist(self, post_id: UUID, payload: EditorialAssistRequest) -> EditorialAssistResponse:
        post = await self.db.get(BlogPost, post_id)
        if not post:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post não encontrado.")

        title = payload.title or post.title
        category = payload.category or (post.categories[0] if post.categories else "Atendimento jurídico")
        selection = payload.selection or ""
        if payload.action == "outline":
            blocks = [
                {"type": "heading", "level": 2, "content": f"Por que {category.lower()} exige fluxo organizado"},
                {"type": "heading", "level": 2, "content": "Erros mais comuns no atendimento"},
                {"type": "heading", "level": 2, "content": "Como estruturar um processo mais previsível"},
            ]
        elif payload.action == "faq":
            blocks = [
                {"type": "faq", "title": "Perguntas frequentes", "items": [
                    f"Como aplicar {category.lower()} com mais consistência?",
                    "Qual o melhor momento para envolver o humano?",
                    "Como reduzir retrabalho no CRM e no follow-up?",
                ]}
            ]
        elif payload.action == "rewrite":
            rewritten = selection or _extract_text_from_blocks(post.content_blocks or [])
            blocks = [{"type": "paragraph", "content": f"{rewritten.strip()} Em termos operacionais, isso pede um fluxo mais claro, menos retrabalho e mais contexto compartilhado entre IA e time humano."}]
        else:
            blocks = [
                {"type": "paragraph", "content": f"{title} é um tema central para escritórios que querem crescer sem depender de improviso operacional."},
                {"type": "paragraph", "content": f"No contexto de {category.lower()}, o Meu Executivo atua com análise técnica, comunicação clara e acompanhamento organizado."},
            ]

        combined_text = _extract_text_from_blocks(blocks)
        return EditorialAssistResponse(
            title=title,
            content_blocks=blocks,
            excerpt=_make_excerpt(title, post.subtitle, combined_text),
            suggested_keywords=_normalize_keywords(f"{title} {category} {combined_text}", [category.lower(), "klm advogados"]),
            suggested_faq=[block for block in blocks if block.get("type") == "faq"],
        )

    async def generate_cover_asset(
        self, post_id: UUID, user_id: UUID, *, commit: bool = True
    ) -> GeneratedImageResponse:
        post = await self.db.get(BlogPost, post_id)
        if not post:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post não encontrado.")

        public_url = _svg_data_url(post.title, post.subtitle)
        asset = SiteMediaAsset(
            key=f"blog-cover-{post.slug}",
            label=f"Capa — {post.title}",
            mime_type="image/svg+xml",
            file_kind="image",
            extension="svg",
            status=SiteMediaAssetStatus.ready,
            source="generated",
            public_url=public_url,
            alt_text=post.title,
            caption="Capa editorial gerada automaticamente para o blog.",
            generation_prompt=f"Capa editorial do Meu Executivo para o artigo '{post.title}'.",
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
        self.db.add(asset)
        await self.db.flush()
        await self.db.refresh(asset)

        post.cover_media = {"assetId": asset.id, "url": asset.public_url, "alt": asset.alt_text, "mimeType": asset.mime_type}
        post.og_media = {"assetId": asset.id, "url": asset.public_url, "alt": asset.alt_text, "mimeType": asset.mime_type}
        post.seo = {**(post.seo or {}), "ogImageUrl": asset.public_url}
        post.updated_by_user_id = user_id
        post.revision += 1
        await (self.db.commit() if commit else self.db.flush())
        await self.db.refresh(post)

        return GeneratedImageResponse(asset=self._to_asset(asset), provider_used="fallback_svg")

    async def list_media_assets(self) -> List[SiteMediaAssetRead]:
        result = await self.db.execute(select(SiteMediaAsset).order_by(SiteMediaAsset.updated_at.desc()))
        return [self._to_asset(asset) for asset in result.scalars().all()]

    async def page_media_assets(self, *, limit: int, offset: int) -> tuple[List[SiteMediaAssetRead], int]:
        total = int(await self.db.scalar(select(func.count(SiteMediaAsset.id))) or 0)
        rows = list(await self.db.scalars(
            select(SiteMediaAsset).order_by(SiteMediaAsset.updated_at.desc(), SiteMediaAsset.id.desc()).offset(offset).limit(limit)
        ))
        return [self._to_asset(asset) for asset in rows], total

    async def create_media_upload(
        self,
        payload: SiteMediaAssetCreate,
        user_id: UUID,
        *,
        commit: bool = True,
    ) -> SiteMediaPresignRead:
        extension = payload.filename.rsplit(".", 1)[-1].lower() if "." in payload.filename else None
        key_slug = _normalize_slug(payload.label or payload.filename)
        asset = SiteMediaAsset(
            key=f"site-media-{key_slug}-{UUID(int=0)}".replace("00000000-0000-0000-0000-000000000000", "")[:170],
            label=payload.label,
            mime_type=payload.mime_type,
            file_kind=self._detect_file_kind(payload.mime_type),
            extension=extension,
            status=SiteMediaAssetStatus.draft,
            source=payload.source,
            alt_text=payload.alt_text,
            caption=payload.caption,
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
        self.db.add(asset)
        await self.db.flush()
        asset.key = f"site-media-{asset.id}"
        asset.s3_key = f"public-site/media/{asset.id}/{payload.filename}"
        try:
            upload_url = self.s3.generate_presigned_upload_url(
                asset.s3_key,
                mime_type=payload.mime_type,
                bucket_name=settings.S3_PUBLIC_ASSET_BUCKET_NAME,
            )
        except StorageUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "MEDIA-001",
                    "message": "Armazenamento de mídia indisponível neste ambiente.",
                },
            ) from exc
        await (self.db.commit() if commit else self.db.flush())
        await self.db.refresh(asset)
        return SiteMediaPresignRead(asset=self._to_asset(asset), upload_url=upload_url)

    async def confirm_media_upload(
        self, asset_id: UUID, *, commit: bool = True
    ) -> SiteMediaConfirmRead:
        asset = await self.db.get(SiteMediaAsset, asset_id)
        if not asset:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset não encontrado.")
        if asset.s3_key:
            asset.public_url = self.s3.get_public_asset_url(asset.s3_key)
        asset.status = SiteMediaAssetStatus.ready
        await (self.db.commit() if commit else self.db.flush())
        await self.db.refresh(asset)
        return SiteMediaConfirmRead(asset=self._to_asset(asset))

    async def get_google_business_connection(self) -> GoogleBusinessProfileConnectionRead:
        await self.ensure_defaults()
        connection = await self.db.scalar(select(GoogleBusinessProfileConnection))
        if not connection:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conexão Google Business não encontrada.")
        return self._to_google_connection(connection)

    async def update_google_business_connection(
        self,
        payload: GoogleBusinessProfileConnectionUpdate,
        user_id: UUID,
        *,
        commit: bool = True,
    ) -> GoogleBusinessProfileConnectionRead:
        connection = await self.db.scalar(select(GoogleBusinessProfileConnection))
        if not connection:
            connection = GoogleBusinessProfileConnection()
            self.db.add(connection)
        data = payload.model_dump(by_alias=False)
        for key, value in data.items():
            if value is not None:
                setattr(connection, key, value)
        connection.status = (
            GoogleBusinessConnectionStatus.connected
            if connection.location_id and (connection.refresh_token or connection.access_token)
            else GoogleBusinessConnectionStatus.pending
        )
        connection.updated_by_user_id = user_id
        await (self.db.commit() if commit else self.db.flush())
        await self.db.refresh(connection)
        return self._to_google_connection(connection)

    async def list_google_business_jobs(self) -> List[GoogleBusinessPublishJobRead]:
        result = await self.db.execute(select(GoogleBusinessPublishJob).order_by(GoogleBusinessPublishJob.created_at.desc()))
        return [self._to_google_job(job) for job in result.scalars().all()]

    async def page_google_business_jobs(self, *, limit: int, offset: int) -> tuple[List[GoogleBusinessPublishJobRead], int]:
        total = int(await self.db.scalar(select(func.count(GoogleBusinessPublishJob.id))) or 0)
        rows = list(await self.db.scalars(
            select(GoogleBusinessPublishJob).order_by(
                GoogleBusinessPublishJob.created_at.desc(), GoogleBusinessPublishJob.id.desc()
            ).offset(offset).limit(limit)
        ))
        return [self._to_google_job(job) for job in rows], total

    async def queue_google_publish(
        self,
        payload: GoogleBusinessPublishRequest,
        user_id: UUID,
        *,
        commit: bool = True,
    ) -> GoogleBusinessPublishJobRead:
        connection = await self.db.scalar(select(GoogleBusinessProfileConnection))
        if not connection:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Conecte o Google Business Profile antes de publicar.")

        status_value = (
            GoogleBusinessPublishStatus.scheduled
            if payload.scheduled_for
            else GoogleBusinessPublishStatus.queued
        )
        error_message = None
        if connection.status != GoogleBusinessConnectionStatus.connected:
            status_value = GoogleBusinessPublishStatus.requires_review
            error_message = "Credenciais ou localização principal ainda não foram concluídas."

        job = GoogleBusinessPublishJob(
            connection_id=connection.id,
            target_type=payload.target_type,
            target_id=payload.target_id,
            title=payload.title,
            status=status_value,
            scheduled_for=payload.scheduled_for,
            payload=payload.payload,
            error_message=error_message,
            created_by_user_id=user_id,
        )
        self.db.add(job)
        await (self.db.commit() if commit else self.db.flush())
        await self.db.refresh(job)
        return self._to_google_job(job)

    async def render_sitemap_xml(self) -> str:
        indexing = await self.get_indexing_settings()
        if not indexing.sitemap_enabled:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sitemap desabilitado.")
        posts = [post for post in await self.list_public_posts(limit=500) if post.indexing.include_in_sitemap]
        categories = sorted({category for post in posts for category in post.categories})
        url_lines = [
            self._sitemap_url(indexing.site_url, "/"),
            self._sitemap_url(indexing.site_url, "/blog"),
            self._sitemap_url(indexing.site_url, "/termos"),
            self._sitemap_url(indexing.site_url, "/privacidade"),
        ]
        for category in categories:
            url_lines.append(self._sitemap_url(indexing.site_url, f"/blog/categoria/{_normalize_slug(category)}"))
        for post in posts:
            lastmod = (post.updated_at or post.published_at or _utcnow()).date().isoformat()
            url_lines.append(self._sitemap_url(indexing.site_url, f"/blog/{post.slug}", lastmod=lastmod))
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(url_lines)
            + "\n</urlset>"
        )

    async def render_robots_txt(self) -> str:
        indexing = await self.get_indexing_settings()
        lines = [indexing.robots_policy.strip(), "", f"Sitemap: {indexing.site_url.rstrip('/')}/sitemap.xml"]
        return "\n".join(line for line in lines if line)

    async def render_rss_xml(self) -> str:
        indexing = await self.get_indexing_settings()
        if not indexing.rss_enabled:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RSS desabilitado.")
        settings = await self.get_blog_settings()
        posts = [post for post in await self.list_public_posts(limit=20) if post.indexing.include_in_rss]
        items = []
        for post in posts:
            pub_date = (post.published_at or _utcnow()).strftime("%a, %d %b %Y %H:%M:%S +0000")
            items.append(
                "<item>"
                f"<title>{escape(post.title)}</title>"
                f"<link>{escape(indexing.site_url.rstrip('/') + '/blog/' + post.slug)}</link>"
                f"<description>{escape(post.excerpt or '')}</description>"
                f"<pubDate>{pub_date}</pubDate>"
                "</item>"
            )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<rss version=\"2.0\"><channel>"
            f"<title>{escape(settings.page_title)}</title>"
            f"<link>{escape(indexing.site_url.rstrip('/') + '/blog')}</link>"
            f"<description>{escape(settings.intro)}</description>"
            + "".join(items)
            + "</channel></rss>"
        )

    async def render_llms_txt(self) -> str:
        indexing = await self.get_indexing_settings()
        if not indexing.llms_enabled:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="llms.txt desabilitado.")
        posts = [post for post in await self.list_public_posts(limit=40) if post.indexing.include_in_llms]
        lines = [
            "# Meu Executivo",
            "",
            indexing.llms_summary,
            "",
            "## Conteúdos publicados",
        ]
        for post in posts:
            lines.append(f"- {post.title}: {indexing.site_url.rstrip('/')}/blog/{post.slug}")
        return "\n".join(lines)

    async def render_post_markdown(self, slug: str) -> str:
        post = await self.get_public_post(slug)
        return f"# {post.title}\n\n{post.subtitle or ''}\n\n{_render_blocks_to_markdown([block.model_dump(by_alias=False) for block in post.content_blocks])}".strip()

    async def get_post_json_payload(self, slug: str) -> dict:
        post = await self.get_public_post(slug)
        return post.model_dump(by_alias=True, mode="json")

    async def _get_doc(self, key: str) -> PublicSiteDocument:
        await self.ensure_defaults()
        result = await self.db.execute(select(PublicSiteDocument).where(PublicSiteDocument.key == key))
        doc = result.scalar_one_or_none()
        if not doc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Documento editorial não encontrado.")
        return doc

    def _sitemap_url(self, site_url: str, path: str, lastmod: Optional[str] = None) -> str:
        line = f"<url><loc>{escape(site_url.rstrip('/') + path)}</loc>"
        if lastmod:
            line += f"<lastmod>{lastmod}</lastmod>"
        line += "</url>"
        return line

    def _serialize_doc_content(self, key: str, content: dict) -> dict:
        if key == "blog_settings":
            return BlogSettingsRead.model_validate(content).model_dump(by_alias=True)
        if key == "blog_taxonomy":
            return BlogTaxonomyRead.model_validate(content).model_dump(by_alias=True)
        if key == "blog_indexing":
            return BlogIndexingSettingsRead.model_validate(content).model_dump(by_alias=True)
        return content

    def _detect_file_kind(self, mime_type: str) -> str:
        if mime_type.startswith("image/"):
            return "image"
        if mime_type.startswith("video/"):
            return "video"
        if mime_type == "application/pdf":
            return "document"
        return "asset"

    def _to_summary(self, post: BlogPost) -> BlogPostSummaryRead:
        return BlogPostSummaryRead(
            id=post.id,
            slug=post.slug,
            title=post.title,
            subtitle=post.subtitle,
            excerpt=post.excerpt,
            author_name=post.author_name,
            author_role=post.author_role,
            status=post.status.value if hasattr(post.status, "value") else str(post.status),
            featured=post.featured,
            categories=list(post.categories or []),
            tags=list(post.tags or []),
            cover_media=post.cover_media or {},
            seo=post.seo or _build_seo(post.title, post.excerpt or "", post.slug),
            indexing=post.indexing or _default_indexing(),
            seo_audit=post.seo_audit or {"score": 0, "readingTimeMinutes": 1, "suggestions": [], "detectedKeywords": []},
            reading_time_minutes=post.reading_time_minutes,
            revision=post.revision,
            published_at=post.published_at,
            scheduled_for=post.scheduled_for,
            updated_at=post.updated_at,
        )

    def _to_detail(self, post: BlogPost) -> BlogPostRead:
        summary = self._to_summary(post)
        return BlogPostRead(
            **summary.model_dump(),
            content_blocks=post.content_blocks or [],
            og_media=post.og_media or {},
            ai_config=post.ai_config or {},
        )

    def _to_asset(self, asset: SiteMediaAsset) -> SiteMediaAssetRead:
        return SiteMediaAssetRead.model_validate(asset)

    def _to_google_connection(self, connection: GoogleBusinessProfileConnection) -> GoogleBusinessProfileConnectionRead:
        return GoogleBusinessProfileConnectionRead.model_validate(connection)

    def _to_google_job(self, job: GoogleBusinessPublishJob) -> GoogleBusinessPublishJobRead:
        return GoogleBusinessPublishJobRead.model_validate(job)
