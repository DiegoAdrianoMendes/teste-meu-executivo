"""Contracts for user preferences and versioned configuration metadata."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.models.configuration import ConfigScope
from app.schemas.base import CamelCaseSchema, WriteSchema


class UserNotificationPreferences(WriteSchema):
    in_app: bool = True
    email: bool = True
    whatsapp: bool = False
    approval_requests: bool = True
    sla_warnings: bool = True


class PlatformFeatureFlagUpdate(WriteSchema):
    enabled: bool
    reason: str = Field(min_length=3, max_length=240)
    expected_etag: str | None = Field(default=None, max_length=128)


class UserDailyDigestPreferences(WriteSchema):
    enabled: bool = True
    hour: int = Field(default=8, ge=0, le=23)


class UserAccessibilityPreferences(WriteSchema):
    reduced_motion: bool = False
    high_contrast: bool = False
    font_scale: float = Field(default=1.0, ge=0.8, le=1.5)


class UserPreferences(WriteSchema):
    theme: Literal["system", "light", "dark"] = "system"
    density: Literal["comfortable", "compact", "spacious"] = "comfortable"
    sidebar_collapsed: bool = False
    # Kept for backwards-compatible configuration documents. New clients use
    # the nested accessibility object below.
    reduced_motion: bool = False
    high_contrast: bool = False
    locale: str = Field(default="pt-BR", min_length=2, max_length=20)
    timezone: str = Field(default="America/Sao_Paulo", min_length=3, max_length=80)
    currency: str = Field(default="BRL", min_length=3, max_length=3)
    date_format: str = Field(default="dd/MM/yyyy", min_length=4, max_length=40)
    time_format: Literal["12h", "24h"] = "24h"
    home_path: str = Field(default="/app", min_length=1, max_length=240)
    default_filters: dict[str, Any] = Field(default_factory=dict)
    notifications: UserNotificationPreferences = Field(
        default_factory=UserNotificationPreferences
    )
    daily_digest: UserDailyDigestPreferences = Field(
        default_factory=UserDailyDigestPreferences
    )
    accessibility: UserAccessibilityPreferences = Field(
        default_factory=UserAccessibilityPreferences
    )

    @field_validator("locale", "timezone", "currency", "date_format", "home_path")
    @classmethod
    def no_control_characters(cls, value: str) -> str:
        cleaned = value.strip()
        if any(ord(character) < 32 for character in cleaned):
            raise ValueError("Control characters are not allowed.")
        return cleaned


class UserPreferencesUpdate(WriteSchema):
    theme: Literal["system", "light", "dark"] | None = None
    density: Literal["comfortable", "compact", "spacious"] | None = None
    sidebar_collapsed: bool | None = None
    reduced_motion: bool | None = None
    high_contrast: bool | None = None
    locale: str | None = Field(default=None, min_length=2, max_length=20)
    timezone: str | None = Field(default=None, min_length=3, max_length=80)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    date_format: str | None = Field(default=None, min_length=4, max_length=40)
    time_format: Literal["12h", "24h"] | None = None
    home_path: str | None = Field(default=None, min_length=1, max_length=240)
    default_filters: dict[str, Any] | None = None
    notifications: UserNotificationPreferences | None = None
    daily_digest: UserDailyDigestPreferences | None = None
    accessibility: UserAccessibilityPreferences | None = None


class BrandAssets(WriteSchema):
    logo_light_url: str = "/logo.svg"
    logo_dark_url: str = "/logo.svg"
    mark_url: str = "/logo-icon.png"
    favicon_url: str = "/favicon.svg"
    apple_touch_icon_url: str | None = "/logo-icon.png"
    letterhead_url: str | None = None
    signature_url: str | None = None


class BrandColors(WriteSchema):
    primary: str = "#DCA74C"
    accent: str = "#C69237"
    foreground: str = "#131720"
    background: str = "#FAFAF9"
    sidebar: str = "#050505"
    success: str = "#237A3B"
    warning: str = "#D98E04"
    danger: str = "#B94A48"


class BrandTypography(WriteSchema):
    sans_family: str = "'Geist Variable', 'Geist', sans-serif"
    legal_family: str = "'Geist Variable', 'Geist', sans-serif"


class BrandMetadata(WriteSchema):
    default_title: str = "Meu Executivo | Operação jurídica inteligente"
    title_template: str = "%s | Meu Executivo"
    description: str = (
        "Sistema Meu Executivo para atendimento, CRM, operação jurídica, agentes e "
        "inteligência comercial."
    )
    application_name: str = "Meu Executivo"
    author: str = "Meu Executivo"
    theme_color: str = "#050505"


class BrandContact(WriteSchema):
    email: str = "contato@klmadvogados.com"
    support_email: str = "contato@klmadvogados.com"
    phone: str | None = None
    website_url: str = "https://klmadvogados.com"


class BrandLegalIdentity(WriteSchema):
    registered_name: str = "Meu Executivo"
    registration_number: str | None = None
    bar_registration: str | None = None
    address: str | None = None


class BrandIdentity(WriteSchema):
    name: str = "Meu Executivo"
    short_name: str = "KLM"
    tagline: str = "Operação jurídica inteligente"
    legal_name: str = "Meu Executivo"
    domain: str = "klmadvogados.com"
    default_locale: str = "pt-BR"
    assets: BrandAssets = Field(default_factory=BrandAssets)
    colors: BrandColors = Field(default_factory=BrandColors)
    css_variables: dict[str, str] = Field(
        default_factory=lambda: {
            "--primary": "39 67% 58%",
            "--accent": "37 56% 50%",
            "--ring": "39 67% 58%",
            "--sidebar-background": "0 0% 2%",
            "--sidebar-primary": "39 67% 58%",
            "--brand-gradient": "hsl(0 0% 2%)",
            "--brand-gradient-subtle": "hsl(39 67% 58% / 0.1)",
            "--ai-gradient": "hsl(39 67% 58%)",
            "--brand-font-family": "'Geist Variable', 'Geist', sans-serif",
        }
    )
    typography: BrandTypography = Field(default_factory=BrandTypography)
    metadata: BrandMetadata = Field(default_factory=BrandMetadata)
    contact: BrandContact = Field(default_factory=BrandContact)
    legal: BrandLegalIdentity = Field(default_factory=BrandLegalIdentity)
    social_proof: list[dict[str, Any]] = Field(default_factory=list)


DEFAULT_BRAND_IDENTITY = BrandIdentity().model_dump(mode="json", by_alias=True)
DEFAULT_WORKSPACE_EXPERIENCE: dict[str, Any] = {
    "name": "Experiência padrão",
    "brief": "",
    "welcomeMessage": "",
    "supportLabel": "Ajuda e suporte",
    "emptyStates": {},
    "navigationLabels": {},
    "featureVisibility": {},
}


def configuration_contract_defaults(namespace: str) -> dict[str, Any]:
    if namespace == "brand.identity":
        return dict(DEFAULT_BRAND_IDENTITY)
    if namespace == "workspace.experience":
        return dict(DEFAULT_WORKSPACE_EXPERIENCE)
    return {}


class ConfigLayerRead(CamelCaseSchema):
    scope: str
    scope_key: str
    version: int
    etag: str


class UserPreferencesRead(CamelCaseSchema):
    preferences: UserPreferences
    etag: str
    user_etag: str | None = None
    user_version: int | None = None
    layers: list[ConfigLayerRead]


class ConfigVersionRead(CamelCaseSchema):
    id: UUID
    version: int
    schema_version: str
    payload: dict
    payload_hash: str
    reason: str | None = None
    created_by_user_id: UUID | None = None
    created_at: datetime


class ConfigVersionPageRead(CamelCaseSchema):
    items: list[ConfigVersionRead]
    total: int
    limit: int
    offset: int
    has_more: bool


class ConfigurationDocumentWrite(WriteSchema):
    payload: dict
    schema_version: str = Field(default="1.0", min_length=1, max_length=40)
    reason: str | None = Field(default=None, max_length=240)
    role_key: str | None = Field(default=None, max_length=80)
    user_id: UUID | None = None


class ConfigurationDocumentRead(CamelCaseSchema):
    id: UUID
    namespace: str
    scope: ConfigScope
    scope_key: str
    role_key: str | None = None
    user_id: UUID | None = None
    schema_version: str
    current_version: int
    etag: str
    payload: dict
    updated_by_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class ResolvedConfigurationRead(CamelCaseSchema):
    namespace: str
    effective: dict
    etag: str
    layers: list[ConfigLayerRead]


class PlatformConfigurationDocumentWrite(WriteSchema):
    payload: dict
    schema_version: str = Field(default="1.0", min_length=1, max_length=40)
    reason: str | None = Field(default=None, max_length=240)


class PlatformConfigurationDocumentRead(CamelCaseSchema):
    id: UUID
    namespace: str
    schema_version: str
    current_version: int
    etag: str
    payload: dict
    updated_by_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
