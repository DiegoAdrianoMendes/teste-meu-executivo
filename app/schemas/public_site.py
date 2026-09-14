"""
Schemas do site público e CMS institucional.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import Field

from app.schemas.base import CamelCaseSchema, WriteSchema


class SeoMetadataRead(CamelCaseSchema):
    title: str
    description: str
    canonical_url: Optional[str] = None
    og_image_url: Optional[str] = None


class SeoMetadataUpdate(WriteSchema):
    title: str
    description: str
    canonical_url: Optional[str] = None
    og_image_url: Optional[str] = None


class PublicSectionMediaRead(CamelCaseSchema):
    poster_url: Optional[str] = None
    mp4_url: Optional[str] = None
    webm_url: Optional[str] = None
    gif_url: Optional[str] = None
    alt: Optional[str] = None
    caption: Optional[str] = None
    layout_variant: str = "default"


class PublicSectionMediaUpdate(WriteSchema):
    poster_url: Optional[str] = None
    mp4_url: Optional[str] = None
    webm_url: Optional[str] = None
    gif_url: Optional[str] = None
    alt: Optional[str] = None
    caption: Optional[str] = None
    layout_variant: str = "default"


class PublicCtaRead(CamelCaseSchema):
    primary_label: Optional[str] = None
    secondary_label: Optional[str] = None
    secondary_target: Optional[str] = None


class PublicCtaUpdate(WriteSchema):
    primary_label: Optional[str] = None
    secondary_label: Optional[str] = None
    secondary_target: Optional[str] = None


class PublicHomeItemRead(CamelCaseSchema):
    title: str
    description: Optional[str] = None
    badge: Optional[str] = None


class PublicHomeItemUpdate(WriteSchema):
    title: str
    description: Optional[str] = None
    badge: Optional[str] = None


class PublicHomeSectionRead(CamelCaseSchema):
    key: str
    nav_label: str
    title: str
    subtitle: Optional[str] = None
    body: Optional[str] = None
    bullets: List[str] = Field(default_factory=list)
    items: List[PublicHomeItemRead] = Field(default_factory=list)
    cta: Optional[PublicCtaRead] = None
    is_visible: bool = True
    order: int
    media: Optional[PublicSectionMediaRead] = None


class PublicHomeSectionUpdate(WriteSchema):
    key: str
    nav_label: str
    title: str
    subtitle: Optional[str] = None
    body: Optional[str] = None
    bullets: List[str] = Field(default_factory=list)
    items: List[PublicHomeItemUpdate] = Field(default_factory=list)
    cta: Optional[PublicCtaUpdate] = None
    is_visible: bool = True
    order: int
    media: Optional[PublicSectionMediaUpdate] = None


class PublicFaqItemRead(CamelCaseSchema):
    question: str
    answer: str


class PublicFaqItemUpdate(WriteSchema):
    question: str
    answer: str


class PublicLegalSectionRead(CamelCaseSchema):
    heading: str
    body: str


class PublicLegalSectionUpdate(WriteSchema):
    heading: str
    body: str


class PublicSiteSettingsRead(CamelCaseSchema):
    brand_name: str
    tagline: str
    support_email: str
    contact_email: str
    whatsapp_url: str
    whatsapp_message: str
    primary_cta_label: str
    secondary_cta_label: str
    secondary_cta_section_id: str
    floating_whatsapp_label: str
    seo: SeoMetadataRead


class PublicSiteSettingsUpdate(WriteSchema):
    brand_name: str
    tagline: str
    support_email: str
    contact_email: str
    whatsapp_url: str
    whatsapp_message: str
    primary_cta_label: str
    secondary_cta_label: str
    secondary_cta_section_id: str
    floating_whatsapp_label: str
    seo: SeoMetadataUpdate


class PublicHomeDocumentRead(CamelCaseSchema):
    sections: List[PublicHomeSectionRead] = Field(default_factory=list)
    seo: SeoMetadataRead


class PublicHomeDocumentUpdate(WriteSchema):
    sections: List[PublicHomeSectionUpdate] = Field(default_factory=list)
    seo: SeoMetadataUpdate


class PublicFaqDocumentRead(CamelCaseSchema):
    items: List[PublicFaqItemRead] = Field(default_factory=list)


class PublicFaqDocumentUpdate(WriteSchema):
    items: List[PublicFaqItemUpdate] = Field(default_factory=list)


class PublicLegalDocumentRead(CamelCaseSchema):
    title: str
    updated_label: str
    seo: SeoMetadataRead
    sections: List[PublicLegalSectionRead] = Field(default_factory=list)


class PublicLegalDocumentUpdate(WriteSchema):
    title: str
    updated_label: str
    seo: SeoMetadataUpdate
    sections: List[PublicLegalSectionUpdate] = Field(default_factory=list)


class PublicMediaAssetRead(CamelCaseSchema):
    key: str
    label: str
    poster_url: Optional[str] = None
    mp4_url: Optional[str] = None
    webm_url: Optional[str] = None
    gif_url: Optional[str] = None
    alt: Optional[str] = None
    caption: Optional[str] = None
    layout_variant: str = "default"


class PublicMediaAssetUpdate(WriteSchema):
    key: str
    label: str
    poster_url: Optional[str] = None
    mp4_url: Optional[str] = None
    webm_url: Optional[str] = None
    gif_url: Optional[str] = None
    alt: Optional[str] = None
    caption: Optional[str] = None
    layout_variant: str = "default"


class PublicMediaLibraryRead(CamelCaseSchema):
    items: List[PublicMediaAssetRead] = Field(default_factory=list)


class PublicMediaLibraryUpdate(WriteSchema):
    items: List[PublicMediaAssetUpdate] = Field(default_factory=list)


class PublicHomePayloadRead(CamelCaseSchema):
    settings: PublicSiteSettingsRead
    home: PublicHomeDocumentRead
    faq: PublicFaqDocumentRead


class PlatformSiteDocumentEnvelopeRead(CamelCaseSchema):
    key: str
    title: str
    description: Optional[str] = None
    version: int
    updated_at: datetime
    published_at: Optional[datetime] = None
    draft: dict
    published: dict
