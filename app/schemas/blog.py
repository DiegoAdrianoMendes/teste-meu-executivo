"""
Schemas do blog público, SEO técnico e Google Business Profile.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import Field

from app.schemas.base import CamelCaseSchema, WriteSchema


class BlogBlockRead(CamelCaseSchema):
    type: str
    content: Optional[str] = None
    level: Optional[int] = None
    items: List[str] = Field(default_factory=list)
    url: Optional[str] = None
    title: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BlogBlockUpdate(WriteSchema):
    type: str
    content: Optional[str] = None
    level: Optional[int] = None
    items: List[str] = Field(default_factory=list)
    url: Optional[str] = None
    title: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BlogMediaRefRead(CamelCaseSchema):
    asset_id: Optional[UUID] = None
    url: Optional[str] = None
    alt: Optional[str] = None
    caption: Optional[str] = None
    mime_type: Optional[str] = None
    layout_variant: Optional[str] = None


class BlogMediaRefUpdate(WriteSchema):
    asset_id: Optional[UUID] = None
    url: Optional[str] = None
    alt: Optional[str] = None
    caption: Optional[str] = None
    mime_type: Optional[str] = None
    layout_variant: Optional[str] = None


class BlogSeoRead(CamelCaseSchema):
    title: str
    description: str
    canonical_url: Optional[str] = None
    og_image_url: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)
    social_copy: Optional[str] = None


class BlogSeoUpdate(WriteSchema):
    title: str
    description: str
    canonical_url: Optional[str] = None
    og_image_url: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)
    social_copy: Optional[str] = None


class IndexingPolicyRead(CamelCaseSchema):
    indexable: bool = True
    include_in_sitemap: bool = True
    include_in_rss: bool = True
    include_in_llms: bool = True


class IndexingPolicyUpdate(WriteSchema):
    indexable: bool = True
    include_in_sitemap: bool = True
    include_in_rss: bool = True
    include_in_llms: bool = True


class SeoAuditRead(CamelCaseSchema):
    score: int = 0
    reading_time_minutes: int = 1
    suggestions: List[str] = Field(default_factory=list)
    detected_keywords: List[str] = Field(default_factory=list)


class BlogPostSummaryRead(CamelCaseSchema):
    id: UUID
    slug: str
    title: str
    subtitle: Optional[str] = None
    excerpt: Optional[str] = None
    author_name: str
    author_role: Optional[str] = None
    status: str
    featured: bool = False
    categories: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    cover_media: BlogMediaRefRead = Field(default_factory=BlogMediaRefRead)
    seo: BlogSeoRead
    indexing: IndexingPolicyRead
    seo_audit: SeoAuditRead
    reading_time_minutes: int = 1
    revision: int = 1
    published_at: Optional[datetime] = None
    scheduled_for: Optional[datetime] = None
    updated_at: datetime


class BlogPostMetricsRead(CamelCaseSchema):
    total: int
    indexable: int
    included_in_sitemap: int
    included_in_llms: int
    scored: int
    average_seo_score: Optional[int] = None
    data_state: Literal["available", "insufficient_data"]


class BlogPostRead(BlogPostSummaryRead):
    content_blocks: List[BlogBlockRead] = Field(default_factory=list)
    og_media: BlogMediaRefRead = Field(default_factory=BlogMediaRefRead)
    ai_config: Dict[str, Any] = Field(default_factory=dict)


class BlogPostCreate(WriteSchema):
    slug: Optional[str] = None
    title: str
    subtitle: Optional[str] = None
    excerpt: Optional[str] = None
    author_name: Optional[str] = None
    author_role: Optional[str] = None
    featured: bool = False
    categories: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    content_blocks: List[BlogBlockUpdate] = Field(default_factory=list)
    cover_media: Optional[BlogMediaRefUpdate] = None
    og_media: Optional[BlogMediaRefUpdate] = None
    seo: Optional[BlogSeoUpdate] = None
    indexing: Optional[IndexingPolicyUpdate] = None
    ai_config: Dict[str, Any] = Field(default_factory=dict)


class BlogPostUpdate(WriteSchema):
    slug: Optional[str] = None
    title: Optional[str] = None
    subtitle: Optional[str] = None
    excerpt: Optional[str] = None
    author_name: Optional[str] = None
    author_role: Optional[str] = None
    featured: Optional[bool] = None
    categories: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    content_blocks: Optional[List[BlogBlockUpdate]] = None
    cover_media: Optional[BlogMediaRefUpdate] = None
    og_media: Optional[BlogMediaRefUpdate] = None
    seo: Optional[BlogSeoUpdate] = None
    indexing: Optional[IndexingPolicyUpdate] = None
    ai_config: Optional[Dict[str, Any]] = None
    status: Optional[str] = None
    scheduled_for: Optional[datetime] = None


class BlogIndexRead(CamelCaseSchema):
    page_title: str
    intro: str
    seo: BlogSeoRead
    featured_post: Optional[BlogPostSummaryRead] = None
    posts: List[BlogPostSummaryRead] = Field(default_factory=list)
    categories: List[str] = Field(default_factory=list)


class BlogCategoryPayloadRead(CamelCaseSchema):
    category: str
    posts: List[BlogPostSummaryRead] = Field(default_factory=list)
    seo: BlogSeoRead


class BlogTaxonomyRead(CamelCaseSchema):
    categories: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)


class BlogTaxonomyUpdate(WriteSchema):
    categories: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)


class BlogSettingsRead(CamelCaseSchema):
    page_title: str
    intro: str
    cta_label: str
    cta_url: str
    seo: BlogSeoRead
    ai_provider_text: str = "heuristic"
    ai_provider_image: str = "fallback"


class BlogSettingsUpdate(WriteSchema):
    page_title: str
    intro: str
    cta_label: str
    cta_url: str
    seo: BlogSeoUpdate
    ai_provider_text: str = "heuristic"
    ai_provider_image: str = "fallback"


class BlogIndexingSettingsRead(CamelCaseSchema):
    site_url: str
    sitemap_enabled: bool = True
    rss_enabled: bool = True
    llms_enabled: bool = True
    robots_policy: str = "User-agent: *\nAllow: /"
    llms_summary: str = ""


class BlogIndexingSettingsUpdate(WriteSchema):
    site_url: str
    sitemap_enabled: bool = True
    rss_enabled: bool = True
    llms_enabled: bool = True
    robots_policy: str = "User-agent: *\nAllow: /"
    llms_summary: str = ""


class SiteMediaAssetRead(CamelCaseSchema):
    id: UUID
    key: str
    label: str
    mime_type: str
    file_kind: str
    extension: Optional[str] = None
    status: str
    source: str
    public_url: Optional[str] = None
    alt_text: Optional[str] = None
    caption: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    size_bytes: Optional[int] = None
    usage_metadata: Dict[str, Any] = Field(default_factory=dict)
    generation_prompt: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class SiteMediaAssetCreate(WriteSchema):
    label: str
    filename: str
    mime_type: str
    alt_text: Optional[str] = None
    caption: Optional[str] = None
    source: str = "upload"


class SiteMediaPresignRead(CamelCaseSchema):
    asset: SiteMediaAssetRead
    upload_url: str


class SiteMediaConfirmRead(CamelCaseSchema):
    asset: SiteMediaAssetRead


class EditorialAssistRequest(WriteSchema):
    action: str
    title: Optional[str] = None
    category: Optional[str] = None
    selection: Optional[str] = None
    prompt: Optional[str] = None


class EditorialAssistResponse(CamelCaseSchema):
    title: str
    content_blocks: List[BlogBlockRead] = Field(default_factory=list)
    excerpt: Optional[str] = None
    suggested_keywords: List[str] = Field(default_factory=list)
    suggested_faq: List[BlogBlockRead] = Field(default_factory=list)


class SeoAutofillResponse(CamelCaseSchema):
    seo: BlogSeoRead
    excerpt: str
    slug: str
    seo_audit: SeoAuditRead


class GeneratedImageResponse(CamelCaseSchema):
    asset: SiteMediaAssetRead
    provider_used: str


class PlatformDocumentEnvelopeRead(CamelCaseSchema):
    key: str
    version: int
    updated_at: datetime
    published_at: Optional[datetime] = None
    draft: Dict[str, Any]
    published: Dict[str, Any]


class GoogleBusinessProfileConnectionRead(CamelCaseSchema):
    id: Optional[UUID] = None
    business_name: str
    account_name: Optional[str] = None
    location_id: Optional[str] = None
    location_name: Optional[str] = None
    status: str
    profile_url: Optional[str] = None
    settings: Dict[str, Any] = Field(default_factory=dict)
    last_sync_at: Optional[datetime] = None


class GoogleBusinessProfileConnectionUpdate(WriteSchema):
    business_name: str
    account_name: Optional[str] = None
    location_id: Optional[str] = None
    location_name: Optional[str] = None
    profile_url: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    refresh_token: Optional[str] = None
    access_token: Optional[str] = None
    settings: Dict[str, Any] = Field(default_factory=dict)


class GoogleBusinessPublishJobRead(CamelCaseSchema):
    id: UUID
    connection_id: UUID
    target_type: str
    target_id: Optional[UUID] = None
    title: str
    status: str
    scheduled_for: Optional[datetime] = None
    published_at: Optional[datetime] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    provider_response: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None
    created_at: datetime


class GoogleBusinessPublishRequest(WriteSchema):
    target_type: str
    target_id: Optional[UUID] = None
    title: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    scheduled_for: Optional[datetime] = None
