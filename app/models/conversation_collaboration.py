"""Tenant-owned conversation collaboration facts.

These tables are intentionally append-only. Provider projections are not stored as
successful facts here; the service exposes their current gated capability status.
"""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.append_only import register_append_only_model
from app.db.base import Base, FlexibleUUID, UUIDMixin


class ConversationReferenceType(enum.StrEnum):
    reply = "reply"
    quote = "quote"
    context = "context"


class ConversationMentionType(enum.StrEnum):
    user = "user"
    team = "team"


class ConversationParticipantType(enum.StrEnum):
    user = "user"
    team = "team"


class ConversationParticipantRole(enum.StrEnum):
    owner = "owner"
    admin = "admin"
    member = "member"
    observer = "observer"


def _string_enum(enum_type: type[enum.StrEnum], name: str, *, length: int) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        length=length,
    )


class ConversationCollaborationGroup(UUIDMixin, Base):
    """The single native collaboration group owned by one canonical thread."""

    __tablename__ = "klm_conversation_groups"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_conv_group_workspace_id"),
        UniqueConstraint("workspace_id", "thread_id", name="uq_conv_group_thread"),
        UniqueConstraint(
            "workspace_id",
            "thread_id",
            "id",
            name="uq_conv_group_thread_id",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_conv_group_workspace_thread",
            ondelete="RESTRICT",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    thread_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(220), nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConversationMessageReference(UUIDMixin, Base):
    """A typed link between two messages in exactly one canonical thread."""

    __tablename__ = "klm_conversation_message_references"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_conv_ref_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "thread_id",
            "id",
            name="uq_conv_ref_thread_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "thread_id",
            "source_message_id",
            "id",
            name="uq_conv_ref_thread_source_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_conv_ref_idempotency",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_conv_ref_workspace_thread",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "source_message_id"],
            ["klm_omnichannel_messages.workspace_id", "klm_omnichannel_messages.id"],
            name="fk_conv_ref_workspace_source_message",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "target_message_id"],
            ["klm_omnichannel_messages.workspace_id", "klm_omnichannel_messages.id"],
            name="fk_conv_ref_workspace_target_message",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "source_message_id <> target_message_id",
            name="ck_conv_ref_distinct_messages",
        ),
        CheckConstraint("length(payload_sha256) = 64", name="ck_conv_ref_payload_hash"),
        Index(
            "ix_conv_ref_thread_created",
            "workspace_id",
            "thread_id",
            "created_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    thread_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    source_message_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    target_message_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    reference_type: Mapped[ConversationReferenceType] = mapped_column(
        _string_enum(
            ConversationReferenceType,
            "conversation_reference_type_enum",
            length=16,
        ),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConversationMentionOperation(UUIDMixin, Base):
    """Idempotent receipt for one standalone or reference-bound mention batch."""

    __tablename__ = "klm_conversation_mention_operations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_conv_mention_op_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "thread_id",
            "source_message_id",
            "id",
            name="uq_conv_mention_op_message_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_conv_mention_op_idempotency",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_conv_mention_op_workspace_thread",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "source_message_id"],
            ["klm_omnichannel_messages.workspace_id", "klm_omnichannel_messages.id"],
            name="fk_conv_mention_op_workspace_message",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id", "source_message_id", "reference_id"],
            [
                "klm_conversation_message_references.workspace_id",
                "klm_conversation_message_references.thread_id",
                "klm_conversation_message_references.source_message_id",
                "klm_conversation_message_references.id",
            ],
            name="fk_conv_mention_op_workspace_reference",
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(payload_sha256) = 64", name="ck_conv_mention_op_payload_hash"),
        Index(
            "ix_conv_mention_op_message_created",
            "workspace_id",
            "thread_id",
            "source_message_id",
            "created_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    thread_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    source_message_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    reference_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConversationMention(UUIDMixin, Base):
    """A resolved user or team mention attached directly to a source message."""

    __tablename__ = "klm_conversation_mentions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_conv_mention_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_conv_mention_workspace_thread",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id", "source_message_id", "operation_id"],
            [
                "klm_conversation_mention_operations.workspace_id",
                "klm_conversation_mention_operations.thread_id",
                "klm_conversation_mention_operations.source_message_id",
                "klm_conversation_mention_operations.id",
            ],
            name="fk_conv_mention_workspace_operation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id", "source_message_id", "reference_id"],
            [
                "klm_conversation_message_references.workspace_id",
                "klm_conversation_message_references.thread_id",
                "klm_conversation_message_references.source_message_id",
                "klm_conversation_message_references.id",
            ],
            name="fk_conv_mention_workspace_reference",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "source_message_id"],
            ["klm_omnichannel_messages.workspace_id", "klm_omnichannel_messages.id"],
            name="fk_conv_mention_workspace_message",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "mentioned_user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_conv_mention_workspace_user",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "mentioned_team_id"],
            ["klm_workspace_teams.workspace_id", "klm_workspace_teams.id"],
            name="fk_conv_mention_workspace_team",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(mention_type = 'user' AND mentioned_user_id IS NOT NULL "
            "AND mentioned_team_id IS NULL) OR "
            "(mention_type = 'team' AND mentioned_team_id IS NOT NULL "
            "AND mentioned_user_id IS NULL)",
            name="ck_conv_mention_resolved_target",
        ),
        Index(
            "uq_conv_mention_user",
            "workspace_id",
            "operation_id",
            "mentioned_user_id",
            unique=True,
            postgresql_where=text("mentioned_user_id IS NOT NULL"),
            sqlite_where=text("mentioned_user_id IS NOT NULL"),
        ),
        Index(
            "uq_conv_mention_team",
            "workspace_id",
            "operation_id",
            "mentioned_team_id",
            unique=True,
            postgresql_where=text("mentioned_team_id IS NOT NULL"),
            sqlite_where=text("mentioned_team_id IS NOT NULL"),
        ),
        Index(
            "ix_conv_mention_message_created",
            "workspace_id",
            "thread_id",
            "source_message_id",
            "created_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    thread_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    operation_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    reference_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    source_message_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    mention_type: Mapped[ConversationMentionType] = mapped_column(
        _string_enum(
            ConversationMentionType,
            "conversation_mention_type_enum",
            length=16,
        ),
        nullable=False,
    )
    mentioned_user_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    mentioned_team_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    resolved_label: Mapped[str] = mapped_column(String(200), nullable=False)
    resolution_source: Mapped[str] = mapped_column(String(60), nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConversationGroupParticipant(UUIDMixin, Base):
    """A resolved workspace user or team participating in a thread group."""

    __tablename__ = "klm_conversation_group_participants"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_conv_participant_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_conv_participant_idempotency",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id", "group_id"],
            [
                "klm_conversation_groups.workspace_id",
                "klm_conversation_groups.thread_id",
                "klm_conversation_groups.id",
            ],
            name="fk_conv_participant_workspace_group",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_conv_participant_workspace_user",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "team_id"],
            ["klm_workspace_teams.workspace_id", "klm_workspace_teams.id"],
            name="fk_conv_participant_workspace_team",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(participant_type = 'user' AND user_id IS NOT NULL AND team_id IS NULL) OR "
            "(participant_type = 'team' AND team_id IS NOT NULL AND user_id IS NULL)",
            name="ck_conv_participant_resolved_target",
        ),
        CheckConstraint(
            "length(payload_sha256) = 64",
            name="ck_conv_participant_payload_hash",
        ),
        Index(
            "uq_conv_participant_user",
            "workspace_id",
            "group_id",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
            sqlite_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "uq_conv_participant_team",
            "workspace_id",
            "group_id",
            "team_id",
            unique=True,
            postgresql_where=text("team_id IS NOT NULL"),
            sqlite_where=text("team_id IS NOT NULL"),
        ),
        Index(
            "ix_conv_participant_thread_created",
            "workspace_id",
            "thread_id",
            "created_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    thread_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    group_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    participant_type: Mapped[ConversationParticipantType] = mapped_column(
        _string_enum(
            ConversationParticipantType,
            "conversation_participant_type_enum",
            length=16,
        ),
        nullable=False,
    )
    user_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    team_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    role: Mapped[ConversationParticipantRole] = mapped_column(
        _string_enum(
            ConversationParticipantRole,
            "conversation_participant_role_enum",
            length=16,
        ),
        nullable=False,
    )
    resolved_label: Mapped[str] = mapped_column(String(200), nullable=False)
    resolution_source: Mapped[str] = mapped_column(String(60), nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


for _model in (
    ConversationCollaborationGroup,
    ConversationMessageReference,
    ConversationMentionOperation,
    ConversationMention,
    ConversationGroupParticipant,
):
    register_append_only_model(_model)


__all__ = [
    "ConversationCollaborationGroup",
    "ConversationGroupParticipant",
    "ConversationMention",
    "ConversationMentionOperation",
    "ConversationMentionType",
    "ConversationMessageReference",
    "ConversationParticipantRole",
    "ConversationParticipantType",
    "ConversationReferenceType",
]
