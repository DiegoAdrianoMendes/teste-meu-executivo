"""Contratos HTTP do lifecycle local de identidade."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.core.password_policy import PasswordValue
from app.models.identity import IdentityOutboxPurpose, InvitationStatus
from app.models.workspace_membership import WorkspaceRole
from app.schemas.base import CamelCaseSchema, WriteSchema
from app.schemas.email import CanonicalEmail
from app.schemas.user import TokenResponse


class IdentityRequestAcceptedRead(CamelCaseSchema):
    request_id: UUID
    status: Literal["accepted"] = "accepted"
    message: str
    delivery_mode: Literal["transactional_email", "privileged_local_simulation"]
    delivery_id: UUID
    expires_at: datetime


class PublicIdentityRequestAcceptedRead(CamelCaseSchema):
    """Uniform public acknowledgement without a delivery capability."""

    request_id: UUID
    status: Literal["accepted"] = "accepted"
    message: str
    delivery_mode: Literal["transactional_email", "privileged_local_simulation"]
    expires_at: datetime


class LocalOutboxMessageRead(CamelCaseSchema):
    id: UUID
    purpose: IdentityOutboxPurpose
    recipient_hint: str
    delivery_mode: Literal["local_simulation"] = "local_simulation"
    code: str | None = None
    link: str
    expires_at: datetime


class AdministrativeInvitationLinkRead(CamelCaseSchema):
    link: str
    recipient_hint: str
    expires_at: datetime


class SecretVerificationRequest(WriteSchema):
    delivery_id: UUID | None = None
    token: str | None = Field(default=None, min_length=16, max_length=512)
    code: str | None = Field(default=None, min_length=6, max_length=12)

    @model_validator(mode="after")
    def require_one_secret(self) -> SecretVerificationRequest:
        if bool(self.token) == bool(self.code):
            raise ValueError("Informe token ou code, exclusivamente.")
        return self


class SignupVerificationRequest(SecretVerificationRequest):
    @model_validator(mode="after")
    def require_delivery_for_code(self) -> SignupVerificationRequest:
        if self.code and self.delivery_id is None:
            raise ValueError("deliveryId e obrigatorio ao usar um codigo.")
        return self


class SignupResendRequest(WriteSchema):
    email: CanonicalEmail


class SignupVerificationRead(TokenResponse):
    verification_status: Literal["verified"] = "verified"


class InvitationCreateRequest(WriteSchema):
    name: str = Field(min_length=2, max_length=200)
    email: CanonicalEmail
    workspace_role: WorkspaceRole = WorkspaceRole.operator
    permission_grants: list[dict[str, str]] = Field(default_factory=list)
    permission_denials: list[dict[str, str]] = Field(default_factory=list)


class InvitationAcceptRequest(SecretVerificationRequest):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    password: PasswordValue | None = None

    @model_validator(mode="after")
    def require_delivery_for_code(self) -> InvitationAcceptRequest:
        if self.code and self.delivery_id is None:
            raise ValueError("deliveryId e obrigatorio ao usar um codigo.")
        return self


class InvitationRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    membership_id: UUID
    invited_email: str
    invited_name: str
    status: InvitationStatus
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    send_count: int
    last_delivery_id: UUID | None


class InvitationAcceptedRead(CamelCaseSchema):
    invitation_id: UUID
    membership_id: UUID
    status: Literal["accepted"] = "accepted"
    requires_login: Literal[True] = True
    message: str


class InvitationPreviewRead(CamelCaseSchema):
    invitation_id: UUID
    workspace_name: str
    recipient_hint: str
    status: InvitationStatus
    expires_at: datetime
    requires_password: bool


class ForgotPasswordRequest(WriteSchema):
    email: CanonicalEmail


class ResetPasswordRequest(SecretVerificationRequest):
    new_password: PasswordValue

    @model_validator(mode="after")
    def require_delivery_for_code(self) -> ResetPasswordRequest:
        if self.code and self.delivery_id is None:
            raise ValueError("deliveryId e obrigatorio ao usar um codigo.")
        return self


class PasswordChangeRequest(WriteSchema):
    current_password: str = Field(min_length=1, max_length=512)
    new_password: PasswordValue


class MessageRead(CamelCaseSchema):
    message: str


class UserSessionRead(CamelCaseSchema):
    id: UUID
    device_label: str
    device_type: str
    browser: str
    operating_system: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    revocation_reason: str | None
    current: bool


class SessionRevocationRead(CamelCaseSchema):
    revoked_count: int
