from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import Field

from app.schemas.base import CamelCaseSchema, WriteSchema


class MetaOAuthAuthorizeUrlRead(CamelCaseSchema):
    authorization_url: str
    state: str
    expires_at: datetime
    scopes: list[str] = Field(default_factory=list)


class MetaOAuthCallbackRequest(WriteSchema):
    code: str
    state: str


class MetaOAuthCallbackResult(CamelCaseSchema):
    status: str
    connection_id: Optional[UUID] = None
    message: str


class MetaOAuthTokenStatusRead(CamelCaseSchema):
    connection_id: UUID
    state: str
    expires_in_days: Optional[int] = None
    oauth_status: Optional[str] = None
    scopes: list[str] = Field(default_factory=list)
