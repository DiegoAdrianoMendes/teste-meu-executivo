from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import Field

from app.models.platform_official_whatsapp import PlatformOfficialWhatsAppRole
from app.models.whatsapp import ConnectionType, WaConnectionStatus
from app.schemas.base import CamelCaseSchema, WriteSchema


class PlatformOfficialWhatsAppConnectionRead(CamelCaseSchema):
    id: Optional[UUID] = None
    role: PlatformOfficialWhatsAppRole
    connectionType: Optional[ConnectionType] = None
    isActive: bool = False
    status: WaConnectionStatus
    phoneNumberId: Optional[str] = None
    phoneNumber: Optional[str] = None
    displayPhoneNumber: Optional[str] = None
    displayName: Optional[str] = None
    businessAccountId: Optional[str] = None
    wabaId: Optional[str] = None
    webhookRegistered: bool = False
    connectedAt: Optional[datetime] = None
    disconnectedAt: Optional[datetime] = None
    updatedAt: Optional[datetime] = None
    lastError: Optional[str] = None
    transportMode: str = "official_api"  # noqa: N815 - stable frontend contract
    coexistenceSyncStatus: str = "not_applicable"  # noqa: N815 - stable frontend contract
    coexistenceContactsSyncRequestId: str | None = None  # noqa: N815 - stable frontend contract
    coexistenceHistorySyncRequestId: str | None = None  # noqa: N815 - stable frontend contract
    coexistenceSyncRequestedAt: datetime | None = None  # noqa: N815 - stable frontend contract
    tokenExpiresAt: datetime | None = None  # noqa: N815 - stable frontend contract
    tokenLifecycleState: str = "not_applicable"  # noqa: N815 - stable frontend contract
    tokenExpiresInDays: int | None = None  # noqa: N815 - stable frontend contract


class PlatformOfficialWhatsAppConnectManual(WriteSchema):
    access_token: str = Field(..., description="Token permanente da System User da Meta")
    phone_number_id: str = Field(..., description="Phone Number ID do número oficial")
    business_account_id: str = Field(..., description="WABA ID do número oficial")
    registration_pin: Optional[str] = Field(default=None, min_length=6, max_length=6)
    display_name: Optional[str] = None
    is_active: bool = True


class PlatformOfficialWhatsAppEmbeddedCallback(WriteSchema):
    code: str
    waba_id: Optional[str] = None
    phone_number_id: Optional[str] = None
    registration_pin: Optional[str] = Field(default=None, min_length=6, max_length=6)
    is_active: bool = True
    transport_mode: str = Field(default="coexistence", pattern=r"^(official_api|coexistence)$")


class PlatformOfficialWhatsAppActiveUpdate(WriteSchema):
    is_active: bool


class PlatformOfficialWhatsAppTestSend(WriteSchema):
    to: str
    body: str = Field(..., min_length=1, max_length=4096)


class PlatformOfficialWhatsAppTestResult(CamelCaseSchema):
    success: bool
    message: str
