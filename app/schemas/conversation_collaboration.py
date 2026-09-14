"""API contracts for native collaboration inside canonical conversations."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.models.conversation_collaboration import (
    ConversationMentionType,
    ConversationParticipantRole,
    ConversationParticipantType,
    ConversationReferenceType,
)
from app.schemas.base import CamelCaseSchema, WriteSchema

CapabilityStatus = Literal["supported", "unavailable", "pending_enablement"]


class ConversationMentionCreate(WriteSchema):
    mention_type: ConversationMentionType
    target_id: UUID


def _validate_unique_mentions(mentions: list[ConversationMentionCreate]) -> None:
    targets = [(item.mention_type.value, item.target_id) for item in mentions]
    if len(targets) != len(set(targets)):
        raise ValueError("A mention target may appear only once per operation.")


class ConversationReferenceCreate(WriteSchema):
    source_message_id: UUID
    target_message_id: UUID
    reference_type: ConversationReferenceType = ConversationReferenceType.reply
    mentions: list[ConversationMentionCreate] = Field(default_factory=list, max_length=50)
    idempotency_key: str = Field(
        min_length=3,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$",
    )

    @model_validator(mode="after")
    def validate_reference(self) -> ConversationReferenceCreate:
        if self.source_message_id == self.target_message_id:
            raise ValueError("Source and target messages must be different.")
        _validate_unique_mentions(self.mentions)
        return self


class ConversationMessageMentionsCreate(WriteSchema):
    source_message_id: UUID
    mentions: list[ConversationMentionCreate] = Field(min_length=1, max_length=50)
    idempotency_key: str = Field(
        min_length=3,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$",
    )

    @model_validator(mode="after")
    def validate_mentions(self) -> ConversationMessageMentionsCreate:
        _validate_unique_mentions(self.mentions)
        return self


class ConversationMentionRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    thread_id: UUID
    operation_id: UUID
    reference_id: UUID | None = None
    source_message_id: UUID
    mention_type: ConversationMentionType
    target_id: UUID
    resolved_label: str
    resolution_source: str
    resolved_at: datetime
    created_by_user_id: UUID | None = None
    created_at: datetime


class ConversationReferenceRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    thread_id: UUID
    source_message_id: UUID
    target_message_id: UUID
    reference_type: ConversationReferenceType
    idempotency_key: str
    created_by_user_id: UUID | None = None
    created_at: datetime
    mentions: list[ConversationMentionRead] = Field(default_factory=list)


class ConversationParticipantCreate(WriteSchema):
    participant_type: ConversationParticipantType
    participant_id: UUID
    role: ConversationParticipantRole = ConversationParticipantRole.member
    idempotency_key: str = Field(
        min_length=3,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$",
    )


class ConversationGroupRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    thread_id: UUID
    name: str
    created_by_user_id: UUID | None = None
    created_at: datetime


class ConversationParticipantRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    thread_id: UUID
    group_id: UUID
    participant_type: ConversationParticipantType
    participant_id: UUID
    role: ConversationParticipantRole
    resolved_label: str
    resolution_source: str
    resolved_at: datetime
    idempotency_key: str
    created_by_user_id: UUID | None = None
    created_at: datetime


class ConversationParticipantCollectionRead(CamelCaseSchema):
    group: ConversationGroupRead | None = None
    participants: list[ConversationParticipantRead] = Field(default_factory=list)


class ConversationCapabilityRead(CamelCaseSchema):
    capability: str
    scope: Literal["native", "provider"]
    status: CapabilityStatus
    provider: str
    official_capability_id: str | None = None
    provider_linked: bool
    gate_open: bool
    external_effect_verified: bool
    reason: str


class ConversationCapabilitiesRead(CamelCaseSchema):
    workspace_id: UUID
    thread_id: UUID
    group_id: UUID | None = None
    participant_count: int = Field(ge=0)
    local_bridge_gate_open: bool
    external_delivery_gate_open: bool
    capabilities: list[ConversationCapabilityRead]
    observed_at: datetime


__all__ = [
    "CapabilityStatus",
    "ConversationCapabilitiesRead",
    "ConversationCapabilityRead",
    "ConversationGroupRead",
    "ConversationMessageMentionsCreate",
    "ConversationMentionCreate",
    "ConversationMentionRead",
    "ConversationParticipantCollectionRead",
    "ConversationParticipantCreate",
    "ConversationParticipantRead",
    "ConversationReferenceCreate",
    "ConversationReferenceRead",
]
