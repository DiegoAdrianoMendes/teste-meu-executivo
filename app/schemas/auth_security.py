"""Contratos HTTP para MFA local e sessões administrativas."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.models.auth_security import MfaChallengePurpose, MfaVerificationMethod
from app.schemas.base import CamelCaseSchema, WriteSchema


class MfaVerificationRequest(WriteSchema):
    code: str | None = Field(default=None, min_length=6, max_length=12)
    recovery_code: str | None = Field(default=None, min_length=12, max_length=32)

    @model_validator(mode="after")
    def require_one_verifier(self) -> MfaVerificationRequest:
        if bool(self.code) == bool(self.recovery_code):
            raise ValueError("Informe code ou recoveryCode, exclusivamente.")
        return self


class MfaChallengeRead(CamelCaseSchema):
    challenge_id: UUID
    purpose: MfaChallengePurpose
    expires_in: int
    attempts_remaining: int
    recovery_available: bool
    simulated_code: str | None = None
    provisioning_uri: str | None = None
    manual_entry_key: str | None = None


class MfaLoginChallengeRead(MfaChallengeRead):
    requires_mfa: Literal[True] = True


class MfaStatusRead(CamelCaseSchema):
    enabled: bool
    simulated: bool = True
    recovery_codes_remaining: int


class MfaEnrollmentCompleteRead(MfaStatusRead):
    recovery_codes: list[str]


class AdministrativeSessionRead(CamelCaseSchema):
    id: UUID
    mfa_method: MfaVerificationMethod
    expires_at: datetime
    last_verified_at: datetime | None
    revoked_at: datetime | None
    revocation_reason: str | None
    created_at: datetime


class AdministrativeSessionTokenRead(CamelCaseSchema):
    administrative_session_token: str
    token_type: Literal["admin_session"] = "admin_session"
    expires_in: int
    session: AdministrativeSessionRead


class AdministrativeSessionStatusRead(CamelCaseSchema):
    active: bool
    session: AdministrativeSessionRead


class AuthSecurityEventRead(CamelCaseSchema):
    id: UUID
    event_type: str
    outcome: str
    challenge_id: UUID | None
    administrative_session_id: UUID | None
    details: dict
    occurred_at: datetime
