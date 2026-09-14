"""Canonical omnichannel entities shared by inbox, social, ads and automation."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.append_only import register_append_only_model
from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin


class OmnichannelChannelConnection(UUIDMixin, TimestampMixin, Base):
    """Workspace-owned connection projection without storing provider secrets."""

    __tablename__ = "klm_omnichannel_channel_connections"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_klm_channel_connection_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "provider",
            "connection_key",
            name="uq_klm_channel_connection_provider_key",
        ),
        Index(
            "ix_klm_channel_connection_workspace_channel_status",
            "workspace_id",
            "channel",
            "status",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    connection_key: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str] = mapped_column(String(180), nullable=False)
    external_account_id: Mapped[str | None] = mapped_column(String(180), nullable=True)
    transport_mode: Mapped[str] = mapped_column(
        String(40), nullable=False, default="official_api", server_default="official_api"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="configured_local", server_default="configured_local"
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    capabilities: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    limits: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    routing_policy: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    cost_policy: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    health: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    configuration: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    credential_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    webhook_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_configured", server_default="not_configured"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OmnichannelWebhookCredential(UUIDMixin, TimestampMixin, Base):
    """Encrypted, versioned signing material for one public channel ingress."""

    __tablename__ = "klm_omnichannel_webhook_credentials"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "connection_id", name="uq_klm_webhook_credential_connection"
        ),
        UniqueConstraint("workspace_id", "id", name="uq_klm_webhook_credential_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_webhook_credential_workspace_connection",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "timestamp_tolerance_seconds >= 30 AND timestamp_tolerance_seconds <= 3600",
            name="ck_klm_webhook_credential_timestamp_tolerance",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    algorithm: Mapped[str] = mapped_column(
        String(40), nullable=False, default="hmac_sha256", server_default="hmac_sha256"
    )
    signing_secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    verify_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp_tolerance_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=300, server_default="300"
    )
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default="active", server_default="active", index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class OmnichannelIdentity(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "klm_omnichannel_identities"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_klm_identity_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_klm_identity_workspace_client",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_klm_identity_verified_phone",
            "workspace_id",
            "canonical_phone",
            unique=True,
            postgresql_where=text("canonical_phone IS NOT NULL"),
            sqlite_where=text("canonical_phone IS NOT NULL"),
        ),
        Index(
            "uq_klm_identity_verified_email",
            "workspace_id",
            "canonical_email",
            unique=True,
            postgresql_where=text("canonical_email IS NOT NULL"),
            sqlite_where=text("canonical_email IS NOT NULL"),
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    canonical_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    identity_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="provisional", server_default="provisional", index=True
    )
    source: Mapped[str] = mapped_column(
        String(60), nullable=False, default="local", server_default="local"
    )
    merge_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    identity_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)


class OmnichannelIdentityReviewCase(UUIDMixin, TimestampMixin, Base):
    """Human review queue for ambiguous person, endpoint and matter associations."""

    __tablename__ = "klm_omnichannel_identity_reviews"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_klm_identity_review_idempotency"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "identity_id"],
            ["klm_omnichannel_identities.workspace_id", "klm_omnichannel_identities.id"],
            name="fk_klm_identity_review_workspace_identity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "candidate_identity_id"],
            ["klm_omnichannel_identities.workspace_id", "klm_omnichannel_identities.id"],
            name="fk_klm_identity_review_workspace_candidate",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_identity_review_workspace_thread",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_klm_identity_review_workspace_matter",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_klm_identity_review_workspace_status_type",
            "workspace_id",
            "status",
            "review_type",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    identity_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    candidate_identity_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), nullable=True, index=True
    )
    thread_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    matter_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    resolved_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    review_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="open", server_default="open", index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    evidence: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    resolution: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OmnichannelIdentityMutationEvent(UUIDMixin, Base):
    """Append-only evidence for merge and split operations."""

    __tablename__ = "klm_omnichannel_identity_mutation_events"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_klm_identity_mutation_idempotency"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "source_identity_id"],
            ["klm_omnichannel_identities.workspace_id", "klm_omnichannel_identities.id"],
            name="fk_klm_identity_mutation_workspace_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "target_identity_id"],
            ["klm_omnichannel_identities.workspace_id", "klm_omnichannel_identities.id"],
            name="fk_klm_identity_mutation_workspace_target",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_klm_identity_mutation_workspace_source_time",
            "workspace_id",
            "source_identity_id",
            "occurred_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_identity_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    target_identity_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    operation: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    before_snapshot: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    after_snapshot: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class OmnichannelEndpoint(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "klm_omnichannel_endpoints"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_klm_endpoint_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "provider",
            "connection_key",
            "external_user_id",
            name="uq_klm_endpoint_provider_user",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "identity_id"],
            ["klm_omnichannel_identities.workspace_id", "klm_omnichannel_identities.id"],
            name="fk_klm_endpoint_workspace_identity",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_endpoint_workspace_connection",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_klm_endpoint_workspace_address",
            "workspace_id",
            "channel",
            "normalized_address",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    identity_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    connection_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    connection_key: Mapped[str] = mapped_column(
        String(120), nullable=False, default="local", server_default="local"
    )
    external_account_id: Mapped[str | None] = mapped_column(String(180), nullable=True)
    external_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    normalized_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consent_state: Mapped[str] = mapped_column(
        String(40), nullable=False, default="unknown", server_default="unknown", index=True
    )
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    capabilities: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    endpoint_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)


class OmnichannelThreadBinding(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "klm_omnichannel_thread_bindings"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_klm_binding_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "provider",
            "connection_key",
            "external_thread_id",
            name="uq_klm_binding_provider_thread",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_binding_workspace_thread",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "endpoint_id"],
            ["klm_omnichannel_endpoints.workspace_id", "klm_omnichannel_endpoints.id"],
            name="fk_klm_binding_workspace_endpoint",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_binding_workspace_connection",
            ondelete="RESTRICT",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    thread_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    endpoint_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    connection_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    connection_key: Mapped[str] = mapped_column(String(120), nullable=False)
    external_thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    binding_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default="active"
    )
    last_cursor: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    binding_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)


class WorkspaceTeam(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "klm_workspace_teams"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_klm_team_workspace_id"),
        UniqueConstraint("workspace_id", "slug", name="uq_klm_team_workspace_slug"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    team_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default="service", server_default="service"
    )
    color: Mapped[str] = mapped_column(
        String(16), nullable=False, default="#1a9948", server_default="#1a9948"
    )
    capacity: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    routing_strategy: Mapped[str] = mapped_column(
        String(40), nullable=False, default="manual", server_default="manual"
    )
    schedule: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"), index=True
    )


class WorkspaceTeamMember(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "klm_workspace_team_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "team_id", "user_id", name="uq_klm_team_member_user"),
        ForeignKeyConstraint(
            ["workspace_id", "team_id"],
            ["klm_workspace_teams.workspace_id", "klm_workspace_teams.id"],
            name="fk_klm_team_member_workspace_team",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["membership_id", "workspace_id", "user_id"],
            [
                "workspace_memberships.id",
                "workspace_memberships.workspace_id",
                "workspace_memberships.user_id",
            ],
            name="fk_klm_team_member_membership_binding",
            ondelete="CASCADE",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    membership_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    member_role: Mapped[str] = mapped_column(
        String(40), nullable=False, default="member", server_default="member"
    )
    routing_weight: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default="100"
    )
    max_concurrent: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    is_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class OmnichannelChannelEvent(UUIDMixin, Base):
    __tablename__ = "klm_omnichannel_channel_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_klm_channel_event_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "provider",
            "connection_key",
            "external_event_id",
            name="uq_klm_channel_event_external",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "identity_id"],
            ["klm_omnichannel_identities.workspace_id", "klm_omnichannel_identities.id"],
            name="fk_klm_channel_event_workspace_identity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_channel_event_workspace_thread",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "binding_id"],
            ["klm_omnichannel_thread_bindings.workspace_id", "klm_omnichannel_thread_bindings.id"],
            name="fk_klm_channel_event_workspace_binding",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "parent_event_id"],
            ["klm_omnichannel_channel_events.workspace_id", "klm_omnichannel_channel_events.id"],
            name="fk_klm_channel_event_workspace_parent",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_channel_event_workspace_connection",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_klm_channel_event_workspace_thread_time",
            "workspace_id",
            "thread_id",
            "occurred_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    identity_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    thread_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    binding_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    parent_event_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    connection_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    connection_key: Mapped[str] = mapped_column(String(120), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    external_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    visibility: Mapped[str] = mapped_column(
        String(40), nullable=False, default="client_private", server_default="client_private"
    )
    public_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    actionability: Mapped[str] = mapped_column(
        String(40), nullable=False, default="informational", server_default="informational"
    )
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    correlation: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    event_payload: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    event_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)


class OmnichannelProviderWebhookEnvelope(UUIDMixin, Base):
    """Exact signed provider delivery kept outside tenant-facing APIs."""

    __tablename__ = "klm_omnichannel_provider_webhook_envelopes"
    __table_args__ = (
        CheckConstraint("byte_size >= 0", name="ck_klm_provider_envelope_byte_size"),
        CheckConstraint(
            "length(payload_sha256) = 64",
            name="ck_klm_provider_envelope_payload_sha256",
        ),
        CheckConstraint(
            "length(signature_sha256) = 64",
            name="ck_klm_provider_envelope_signature_sha256",
        ),
        CheckConstraint(
            "delivery_fingerprint_sha256 IS NULL "
            "OR length(delivery_fingerprint_sha256) = 64",
            name="ck_klm_provider_envelope_delivery_fingerprint",
        ),
        Index(
            "ix_klm_provider_envelope_provider_received",
            "provider",
            "received_at",
        ),
        Index(
            "ix_klm_provider_envelope_payload_sha256",
            "payload_sha256",
        ),
        Index(
            "uq_klm_provider_envelope_delivery_fingerprint",
            "delivery_fingerprint_sha256",
            unique=True,
            postgresql_where=text("delivery_fingerprint_sha256 IS NOT NULL"),
            sqlite_where=text("delivery_fingerprint_sha256 IS NOT NULL"),
        ),
    )

    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # NULL is reserved for envelopes that predate the durable-dedup migration.
    # Every new capture writes the canonical fingerprint before flush.
    delivery_fingerprint_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    raw_payload: Mapped[bytes] = mapped_column(LargeBinary(), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    signature_header: Mapped[str] = mapped_column(Text, nullable=False)
    signature_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_scheme: Mapped[str] = mapped_column(String(40), nullable=False)
    signature_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    request_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class OmnichannelProviderWebhookProcessingState(TimestampMixin, Base):
    """Mutable claim/retry projection for one immutable provider envelope."""

    __tablename__ = "klm_omnichannel_provider_webhook_processing_states"
    __table_args__ = (
        CheckConstraint(
            "status IN "
            "('queued', 'processing', 'processed', 'retryable_failed', 'terminal_failed')",
            name="ck_klm_provider_webhook_processing_status",
        ),
        CheckConstraint(
            "attempts >= 0",
            name="ck_klm_provider_webhook_processing_attempts",
        ),
        CheckConstraint(
            "(status = 'processing' "
            "AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (status <> 'processing' "
            "AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_klm_provider_webhook_processing_lease_shape",
        ),
        CheckConstraint(
            "(status = 'processed' AND processed_at IS NOT NULL) "
            "OR (status <> 'processed' AND processed_at IS NULL)",
            name="ck_klm_provider_webhook_processing_processed_at",
        ),
        CheckConstraint(
            "(status = 'retryable_failed' "
            "AND next_retry_at IS NOT NULL AND last_failed_at IS NOT NULL) "
            "OR (status <> 'retryable_failed' AND next_retry_at IS NULL)",
            name="ck_klm_provider_webhook_processing_retry_shape",
        ),
        CheckConstraint(
            "(status = 'terminal_failed' AND terminal_failed_at IS NOT NULL) "
            "OR (status <> 'terminal_failed' AND terminal_failed_at IS NULL)",
            name="ck_klm_provider_webhook_processing_terminal_at",
        ),
        Index(
            "ix_klm_provider_webhook_processing_claim",
            "status",
            "next_retry_at",
            "lease_expires_at",
        ),
        Index(
            "ix_klm_provider_webhook_processing_terminal",
            "terminal_failed_at",
            postgresql_where=text("status = 'terminal_failed'"),
            sqlite_where=text("status = 'terminal_failed'"),
        ),
    )

    envelope_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey(
            "klm_omnichannel_provider_webhook_envelopes.id",
            ondelete="RESTRICT",
        ),
        primary_key=True,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="queued", server_default="queued"
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    terminal_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)


class OmnichannelMessageEvidenceEvent(UUIDMixin, Base):
    """Append-only content history and custody chain for one provider message."""

    __tablename__ = "klm_omnichannel_message_evidence_events"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "chain_id",
            "revision",
            name="uq_klm_message_evidence_chain_revision",
        ),
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_klm_message_evidence_idempotency",
        ),
        UniqueConstraint(
            "workspace_id", "id", name="uq_klm_message_evidence_workspace_id"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "message_id"],
            ["klm_omnichannel_messages.workspace_id", "klm_omnichannel_messages.id"],
            name="fk_klm_message_evidence_workspace_message",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_message_evidence_workspace_thread",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "channel_event_id"],
            [
                "klm_omnichannel_channel_events.workspace_id",
                "klm_omnichannel_channel_events.id",
            ],
            name="fk_klm_message_evidence_workspace_channel_event",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_message_evidence_workspace_connection",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "previous_event_id"],
            [
                "klm_omnichannel_message_evidence_events.workspace_id",
                "klm_omnichannel_message_evidence_events.id",
            ],
            name="fk_klm_message_evidence_workspace_previous",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "storage_object_id"],
            ["storage_objects.workspace_id", "storage_objects.id"],
            name="fk_klm_message_evidence_workspace_storage_object",
            ondelete="RESTRICT",
        ),
        CheckConstraint("revision > 0", name="ck_klm_message_evidence_revision"),
        CheckConstraint(
            "content_revision IS NULL OR content_revision > 0",
            name="ck_klm_message_evidence_content_revision",
        ),
        CheckConstraint(
            "event_type IN ('captured', 'edited', 'deleted', 'provider_receipt', "
            "'media_captured', 'media_unavailable', 'provider_unsupported')",
            name="ck_klm_message_evidence_event_type",
        ),
        CheckConstraint(
            "observation_state IN ('observed', 'provider_signal', 'inferred', 'unavailable')",
            name="ck_klm_message_evidence_observation_state",
        ),
        CheckConstraint(
            "length(event_payload_sha256) = 64",
            name="ck_klm_message_evidence_payload_sha256",
        ),
        CheckConstraint(
            "length(evidence_sha256) = 64",
            name="ck_klm_message_evidence_sha256",
        ),
        Index(
            "ix_klm_message_evidence_message_time",
            "workspace_id",
            "message_id",
            "received_at",
        ),
        Index(
            "ix_klm_message_evidence_external",
            "workspace_id",
            "provider",
            "connection_key",
            "external_message_id",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    chain_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    content_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    thread_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    channel_event_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), nullable=True, index=True
    )
    connection_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), nullable=True, index=True
    )
    legacy_whatsapp_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("whatsapp_messages.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    envelope_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "klm_omnichannel_provider_webhook_envelopes.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )
    previous_event_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), nullable=True, index=True
    )
    storage_object_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    connection_key: Mapped[str] = mapped_column(String(120), nullable=False)
    external_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    observation_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="observed", server_default="observed"
    )
    content_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    body_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_content: Mapped[dict] = mapped_column(
        FlexibleJSON(), nullable=False, default=dict
    )
    event_payload: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    event_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_envelope_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signature_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    media_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    media_byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    media_mime_type: Mapped[str | None] = mapped_column(String(127), nullable=True)
    media_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_media_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    storage_object_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    previous_evidence_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    provider_occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class OmnichannelRoutingEvent(UUIDMixin, Base):
    __tablename__ = "klm_omnichannel_routing_events"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_klm_routing_event_idempotency"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_routing_event_workspace_thread",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "from_team_id"],
            ["klm_workspace_teams.workspace_id", "klm_workspace_teams.id"],
            name="fk_klm_routing_event_workspace_from_team",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "to_team_id"],
            ["klm_workspace_teams.workspace_id", "klm_workspace_teams.id"],
            name="fk_klm_routing_event_workspace_to_team",
            ondelete="RESTRICT",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    thread_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    from_user_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    to_user_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    from_team_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    to_team_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(
        String(40), nullable=False, default="human", server_default="human"
    )
    expected_thread_version: Mapped[int] = mapped_column(Integer, nullable=False)
    result_thread_version: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(180), nullable=True, index=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class OmnichannelActionIntent(UUIDMixin, TimestampMixin, Base):
    """Provider action prepared locally and blocked at the external-delivery gate."""

    __tablename__ = "klm_omnichannel_action_intents"
    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_klm_action_intent_key"),
        Index(
            "uq_klm_action_intent_workspace_id",
            "workspace_id",
            "id",
            unique=True,
        ),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_action_intent_workspace_thread",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "event_id"],
            ["klm_omnichannel_channel_events.workspace_id", "klm_omnichannel_channel_events.id"],
            name="fk_klm_action_intent_workspace_event",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_action_intent_workspace_connection",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_klm_action_intent_workspace_state_due",
            "workspace_id",
            "state",
            "scheduled_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    thread_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    event_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    connection_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    target_external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="local_preview",
        server_default="local_preview",
        index=True,
    )
    required_capability: Mapped[str] = mapped_column(String(80), nullable=False)
    capability_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unknown", server_default="unknown"
    )
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5"
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_owner: Mapped[str | None] = mapped_column(String(180), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    delivery_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_supports_idempotency: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    provider_action_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    delivery_uncertain_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_payload: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    provider_receipt: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class OmnichannelActionIntentEvent(UUIDMixin, Base):
    """Append-only lifecycle evidence for one provider action."""

    __tablename__ = "klm_omnichannel_action_intent_events"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_klm_action_intent_event_idempotency",
        ),
        UniqueConstraint(
            "workspace_id", "id", name="uq_klm_action_intent_event_workspace_id"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "action_intent_id"],
            ["klm_omnichannel_action_intents.workspace_id", "klm_omnichannel_action_intents.id"],
            name="fk_klm_action_event_workspace_intent",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_action_event_workspace_thread",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_action_event_workspace_connection",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_klm_action_intent_event_intent_time",
            "workspace_id",
            "action_intent_id",
            "occurred_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action_intent_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), nullable=False, index=True
    )
    thread_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    connection_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    previous_state: Mapped[str] = mapped_column(String(32), nullable=False)
    next_state: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(180), nullable=True)
    lease_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    external_action_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    event_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)


class OmnichannelAutomationRule(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "klm_omnichannel_automation_rules"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_klm_automation_rule_slug"),
        UniqueConstraint("workspace_id", "id", name="uq_klm_automation_rule_workspace_id"),
        Index(
            "ix_klm_automation_rule_workspace_trigger_status",
            "workspace_id",
            "trigger_type",
            "status",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    trigger_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="draft", server_default="draft", index=True
    )
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default="100"
    )
    coexist_with_ai: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    stop_on_match: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    trigger_config: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    conditions: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    actions: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    quiet_hours: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    rate_limits: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class OmnichannelAutomationRun(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "klm_omnichannel_automation_runs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_klm_automation_run_key"),
        UniqueConstraint("workspace_id", "id", name="uq_klm_automation_run_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "rule_id"],
            [
                "klm_omnichannel_automation_rules.workspace_id",
                "klm_omnichannel_automation_rules.id",
            ],
            name="fk_klm_automation_run_workspace_rule",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "event_id"],
            ["klm_omnichannel_channel_events.workspace_id", "klm_omnichannel_channel_events.id"],
            name="fk_klm_automation_run_workspace_event",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_automation_run_workspace_thread",
            ondelete="CASCADE",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    event_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    thread_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="simulated", server_default="simulated", index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    current_action: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    context: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    result: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    error: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OmnichannelAutomationRunEvent(UUIDMixin, Base):
    __tablename__ = "klm_omnichannel_automation_run_events"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "run_id", "sequence", name="uq_klm_automation_run_event_sequence"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["klm_omnichannel_automation_runs.workspace_id", "klm_omnichannel_automation_runs.id"],
            name="fk_klm_automation_run_event_workspace_run",
            ondelete="CASCADE",
        ),
        Index("ix_klm_automation_run_event_run_time", "run_id", "occurred_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    action_key: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    input_snapshot: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    output_snapshot: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class LeadCaptureReceipt(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "klm_lead_capture_receipts"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider",
            "connection_key",
            "external_lead_id",
            name="uq_klm_lead_capture_external",
        ),
        UniqueConstraint(
            "workspace_id", "id", name="uq_klm_lead_capture_workspace_id"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "identity_id"],
            ["klm_omnichannel_identities.workspace_id", "klm_omnichannel_identities.id"],
            name="fk_klm_lead_capture_workspace_identity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_lead_capture_workspace_thread",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_klm_lead_capture_workspace_matter",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_lead_capture_workspace_connection",
            ondelete="RESTRICT",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    connection_key: Mapped[str] = mapped_column(String(120), nullable=False)
    external_lead_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_form_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="processing", server_default="processing", index=True
    )
    mapping_version: Mapped[str] = mapped_column(
        String(40), nullable=False, default="v1", server_default="v1"
    )
    identity_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    thread_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    matter_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    result: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OmnichannelPublicIngressReceipt(UUIDMixin, Base):
    """Append-only proof that a signed public event crossed the native boundary."""

    __tablename__ = "klm_omnichannel_public_ingress_receipts"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider",
            "connection_key",
            "external_event_id",
            name="uq_klm_public_ingress_provider_event",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_klm_public_ingress_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_public_ingress_workspace_connection",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_klm_public_ingress_connection_received",
            "workspace_id",
            "connection_id",
            "received_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    connection_key: Mapped[str] = mapped_column(String(120), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_lead_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    signature_scheme: Mapped[str] = mapped_column(String(40), nullable=False)
    request_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class LeadHydrationJob(UUIDMixin, TimestampMixin, Base):
    """Durable retrieval of a provider lead referenced by a locator-only webhook."""

    __tablename__ = "klm_lead_hydration_jobs"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider",
            "connection_key",
            "external_lead_id",
            name="uq_klm_lead_hydration_external",
        ),
        UniqueConstraint(
            "workspace_id", "id", name="uq_klm_lead_hydration_workspace_id"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_lead_hydration_workspace_connection",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "public_ingress_receipt_id"],
            [
                "klm_omnichannel_public_ingress_receipts.workspace_id",
                "klm_omnichannel_public_ingress_receipts.id",
            ],
            name="fk_klm_lead_hydration_workspace_ingress",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "lead_capture_receipt_id"],
            ["klm_lead_capture_receipts.workspace_id", "klm_lead_capture_receipts.id"],
            name="fk_klm_lead_hydration_workspace_capture",
            ondelete="RESTRICT",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_klm_lead_hydration_attempts"),
        CheckConstraint("max_attempts >= 1", name="ck_klm_lead_hydration_max_attempts"),
        Index(
            "ix_klm_lead_hydration_workspace_due",
            "workspace_id",
            "state",
            "next_attempt_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    public_ingress_receipt_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), nullable=False, index=True
    )
    lead_capture_receipt_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    connection_key: Mapped[str] = mapped_column(String(120), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    external_lead_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    external_form_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    locator_payload: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="queued", server_default="queued", index=True
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=7, server_default="7"
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_owner: Mapped[str | None] = mapped_column(String(180), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    fetch_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    hydrated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_receipt: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)


class LeadHydrationEvent(UUIDMixin, Base):
    """Append-only lifecycle evidence for provider lead hydration."""

    __tablename__ = "klm_lead_hydration_events"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_klm_lead_hydration_event_idempotency",
        ),
        UniqueConstraint(
            "workspace_id", "id", name="uq_klm_lead_hydration_event_workspace_id"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "hydration_job_id"],
            ["klm_lead_hydration_jobs.workspace_id", "klm_lead_hydration_jobs.id"],
            name="fk_klm_lead_hydration_event_workspace_job",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_lead_hydration_event_workspace_connection",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_klm_lead_hydration_event_job_time",
            "workspace_id",
            "hydration_job_id",
            "occurred_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    hydration_job_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), nullable=False, index=True
    )
    connection_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    external_lead_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    previous_state: Mapped[str] = mapped_column(String(32), nullable=False)
    next_state: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(180), nullable=True)
    lease_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    event_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)


class AutomationTriggerReceipt(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "klm_automation_trigger_receipts"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "channel_event_id",
            "flow_revision_id",
            "action_key",
            name="uq_klm_automation_trigger_once",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "channel_event_id"],
            ["klm_omnichannel_channel_events.workspace_id", "klm_omnichannel_channel_events.id"],
            name="fk_klm_automation_trigger_workspace_event",
            ondelete="CASCADE",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel_event_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    flow_revision_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    action_key: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="claimed", server_default="claimed", index=True
    )
    result: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)


class OmnichannelProviderMediaReceipt(UUIDMixin, Base):
    """Immutable proof that media was uploaded through one tenant-owned connection."""

    __tablename__ = "klm_omnichannel_provider_media_receipts"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "id", name="uq_klm_provider_media_receipt_workspace_id"
        ),
        UniqueConstraint(
            "workspace_id",
            "connection_id",
            "provider",
            "provider_media_id",
            name="uq_klm_provider_media_receipt_provider_id",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_provider_media_receipt_workspace_connection",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "media_type IN ('image', 'audio', 'video', 'document')",
            name="ck_klm_provider_media_receipt_media_type",
        ),
        CheckConstraint(
            "length(source_sha256) = 64 AND length(provider_payload_sha256) = 64",
            name="ck_klm_provider_media_receipt_sha256_length",
        ),
        CheckConstraint(
            "source_size_bytes > 0",
            name="ck_klm_provider_media_receipt_source_size_positive",
        ),
        Index(
            "ix_klm_provider_media_receipt_connection_recorded",
            "workspace_id",
            "connection_id",
            "recorded_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    provider_media_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    media_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_content_type: Mapped[str] = mapped_column(String(160), nullable=False)
    provider_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_receipt: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class OmnichannelProviderMediaUploadIntent(UUIDMixin, TimestampMixin, Base):
    """Durable, idempotent checkpoint around one provider media upload."""

    __tablename__ = "klm_omnichannel_provider_media_upload_intents"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "id", name="uq_klm_provider_media_intent_workspace_id"
        ),
        UniqueConstraint(
            "workspace_id",
            "idempotency_key_hash",
            name="uq_klm_provider_media_intent_idempotency",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_provider_media_intent_workspace_connection",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "receipt_id"],
            [
                "klm_omnichannel_provider_media_receipts.workspace_id",
                "klm_omnichannel_provider_media_receipts.id",
            ],
            name="fk_klm_provider_media_intent_workspace_receipt",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "state IN ('prepared', 'dispatching', 'provider_accepted', 'completed', "
            "'failed', 'delivery_uncertain')",
            name="ck_klm_provider_media_intent_state",
        ),
        CheckConstraint(
            "media_type IN ('image', 'audio', 'video', 'document')",
            name="ck_klm_provider_media_intent_media_type",
        ),
        CheckConstraint(
            "length(idempotency_key_hash) = 64 AND length(payload_sha256) = 64 "
            "AND length(source_sha256) = 64 AND length(source_filename_sha256) = 64",
            name="ck_klm_provider_media_intent_hash_lengths",
        ),
        CheckConstraint(
            "source_size_bytes > 0 AND attempt_count >= 0",
            name="ck_klm_provider_media_intent_positive_values",
        ),
        Index(
            "ix_klm_provider_media_intent_connection_state",
            "workspace_id",
            "connection_id",
            "state",
        ),
        Index("ix_klm_provider_media_intent_workspace", "workspace_id"),
        Index("ix_klm_provider_media_intent_connection", "connection_id"),
        Index("ix_klm_provider_media_intent_provider", "provider"),
        Index("ix_klm_provider_media_intent_idem_key", "idempotency_key_hash"),
        Index("ix_klm_provider_media_intent_state", "state"),
        Index("ix_klm_provider_media_intent_media_id", "provider_media_id"),
        Index("ix_klm_provider_media_intent_receipt", "receipt_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    media_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_content_type: Mapped[str] = mapped_column(String(160), nullable=False)
    source_extension: Mapped[str] = mapped_column(String(16), nullable=False)
    source_filename_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="prepared", server_default="prepared"
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    provider_media_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_receipt: Mapped[dict] = mapped_column(
        FlexibleJSON(), nullable=False, default=dict
    )
    receipt_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    request_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivery_uncertain_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class OmnichannelProviderMediaUploadReconciliation(UUIDMixin, Base):
    """Immutable human decision that resolves an uncertain provider upload."""

    __tablename__ = "klm_omnichannel_provider_media_upload_reconciliations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_klm_provider_media_recon_workspace_id",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_provider_media_recon_workspace_connection",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "upload_intent_id"],
            [
                "klm_omnichannel_provider_media_upload_intents.workspace_id",
                "klm_omnichannel_provider_media_upload_intents.id",
            ],
            name="fk_klm_provider_media_recon_workspace_intent",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "decision IN ('provider_accepted', 'retry_authorized')",
            name="ck_klm_provider_media_recon_decision",
        ),
        CheckConstraint(
            "length(reason) BETWEEN 3 AND 1000",
            name="ck_klm_provider_media_recon_reason_length",
        ),
        CheckConstraint(
            "(decision = 'provider_accepted' AND provider_media_id IS NOT NULL) OR "
            "(decision = 'retry_authorized' AND provider_media_id IS NULL)",
            name="ck_klm_provider_media_recon_evidence",
        ),
        Index(
            "ix_klm_provider_media_recon_intent_time",
            "workspace_id",
            "upload_intent_id",
            "resolved_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    upload_intent_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), nullable=False, index=True
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    decision: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    provider_media_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_receipt: Mapped[dict] = mapped_column(
        FlexibleJSON(), nullable=False, default=dict
    )
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class OmnichannelMessageDeliveryEvent(UUIDMixin, Base):
    """Immutable provider evidence used to project outbound delivery state."""

    __tablename__ = "klm_omnichannel_message_delivery_events"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider",
            "connection_key",
            "external_event_id",
            name="uq_klm_delivery_event_provider_event",
        ),
        UniqueConstraint(
            "workspace_id", "id", name="uq_klm_delivery_event_workspace_id"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "message_id"],
            ["klm_omnichannel_messages.workspace_id", "klm_omnichannel_messages.id"],
            name="fk_klm_delivery_event_workspace_message",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_delivery_event_workspace_thread",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_delivery_event_workspace_connection",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_klm_delivery_event_message_occurred",
            "workspace_id",
            "message_id",
            "occurred_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    thread_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    connection_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    connection_key: Mapped[str] = mapped_column(String(120), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_message_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    provider_status: Mapped[str] = mapped_column(String(80), nullable=False)
    projected_status_before: Mapped[str] = mapped_column(String(32), nullable=False)
    projected_status_after: Mapped[str] = mapped_column(String(32), nullable=False)
    applied: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    ignored_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_payload: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    event_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)


class OmnichannelMessageCostLedgerEntry(UUIDMixin, Base):
    """Append-only provider cost evidence; projections are never the source of truth."""

    __tablename__ = "klm_omnichannel_message_cost_ledger"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_klm_message_cost_idempotency"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "message_id"],
            ["klm_omnichannel_messages.workspace_id", "klm_omnichannel_messages.id"],
            name="fk_klm_message_cost_workspace_message",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_message_cost_workspace_thread",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "delivery_event_id"],
            [
                "klm_omnichannel_message_delivery_events.workspace_id",
                "klm_omnichannel_message_delivery_events.id",
            ],
            name="fk_klm_message_cost_workspace_delivery_event",
            ondelete="RESTRICT",
        ),
        CheckConstraint("amount >= 0", name="ck_klm_message_cost_amount_nonnegative"),
        CheckConstraint("quantity > 0", name="ck_klm_message_cost_quantity_positive"),
        Index(
            "ix_klm_message_cost_thread_occurred",
            "workspace_id",
            "thread_id",
            "occurred_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    thread_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    delivery_event_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    connection_key: Mapped[str] = mapped_column(String(120), nullable=False)
    entry_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default="provider_charge", server_default="provider_charge"
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(15, 6), nullable=False, default=Decimal("1"), server_default="1"
    )
    unit: Mapped[str] = mapped_column(
        String(40), nullable=False, default="message", server_default="message"
    )
    pricing_category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_invoice_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    entry_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)


class OmnichannelScheduledMessage(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "klm_omnichannel_scheduled_messages"
    __table_args__ = (
        Index("uq_klm_schedule_workspace_id", "workspace_id", "id", unique=True),
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_klm_schedule_idempotency"),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_schedule_workspace_thread",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "endpoint_id"],
            ["klm_omnichannel_endpoints.workspace_id", "klm_omnichannel_endpoints.id"],
            name="fk_klm_schedule_workspace_endpoint",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_schedule_workspace_connection",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "canonical_message_id"],
            ["klm_omnichannel_messages.workspace_id", "klm_omnichannel_messages.id"],
            name="fk_klm_schedule_workspace_canonical_message",
            ondelete="RESTRICT",
        ),
        Index("ix_klm_schedule_due", "state", "scheduled_at", "next_attempt_at"),
        Index(
            "ix_klm_schedule_workspace_due",
            "workspace_id",
            "state",
            "scheduled_at",
            "next_attempt_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    thread_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    endpoint_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    connection_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    canonical_message_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), nullable=True, index=True
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    author_type: Mapped[str] = mapped_column(
        String(24), nullable=False, default="system", server_default="system"
    )
    author_label: Mapped[str] = mapped_column(
        String(180),
        nullable=False,
        default="Agendamento Meu Executivo",
        server_default="Agendamento Meu Executivo",
    )
    connection_key: Mapped[str] = mapped_column(String(120), nullable=False)
    channel: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    template_id: Mapped[str | None] = mapped_column(String(180), nullable=True)
    attachments: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    message_payload: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    timezone: Mapped[str] = mapped_column(
        String(80), nullable=False, default="America/Sao_Paulo", server_default="America/Sao_Paulo"
    )
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="scheduled", server_default="scheduled", index=True
    )
    lease_owner: Mapped[str | None] = mapped_column(String(180), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5"
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_supports_idempotency: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    delivery_uncertain_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_receipt: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)


class OmnichannelScheduledMessageEvent(UUIDMixin, Base):
    """Append-only evidence for creation, control and delivery transitions."""

    __tablename__ = "klm_omnichannel_scheduled_message_events"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "id", name="uq_klm_schedule_event_workspace_id"
        ),
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_klm_schedule_event_idempotency",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "scheduled_message_id"],
            [
                "klm_omnichannel_scheduled_messages.workspace_id",
                "klm_omnichannel_scheduled_messages.id",
            ],
            name="fk_klm_schedule_event_workspace_schedule",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_schedule_event_workspace_thread",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_schedule_event_workspace_connection",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "canonical_message_id"],
            ["klm_omnichannel_messages.workspace_id", "klm_omnichannel_messages.id"],
            name="fk_klm_schedule_event_workspace_canonical_message",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_klm_schedule_event_schedule_occurred",
            "workspace_id",
            "scheduled_message_id",
            "occurred_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scheduled_message_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), nullable=False, index=True
    )
    thread_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    connection_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    canonical_message_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    previous_state: Mapped[str] = mapped_column(String(32), nullable=False)
    next_state: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(180), nullable=True)
    lease_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    external_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    event_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)


class OmnichannelAuditFinding(UUIDMixin, TimestampMixin, Base):
    """Mutable auditor projection backed by canonical delivery and conversation evidence."""

    __tablename__ = "klm_omnichannel_audit_findings"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "dedupe_key",
            name="uq_klm_omnichannel_audit_finding_dedupe",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_audit_finding_workspace_thread",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "message_id"],
            ["klm_omnichannel_messages.workspace_id", "klm_omnichannel_messages.id"],
            name="fk_klm_audit_finding_workspace_message",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_klm_audit_finding_workspace_matter",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "severity IN ('info', 'warning', 'critical')",
            name="ck_klm_audit_finding_severity",
        ),
        CheckConstraint(
            "state IN ('open', 'retry_scheduled', 'waiting_human', 'resolved', 'dismissed')",
            name="ck_klm_audit_finding_state",
        ),
        CheckConstraint(
            "retry_count >= 0 AND max_retries >= 0",
            name="ck_klm_audit_finding_retry_counts",
        ),
        Index(
            "ix_klm_audit_finding_workspace_state_seen",
            "workspace_id",
            "state",
            "last_seen_at",
        ),
        Index(
            "ix_klm_audit_finding_thread_state",
            "workspace_id",
            "thread_id",
            "state",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    thread_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    matter_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    message_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    finding_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(
        String(16), nullable=False, default="warning", server_default="warning", index=True
    )
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default="open", server_default="open", index=True
    )
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(180), nullable=False)
    evidence: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    remediation: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    max_retries: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    internal_note_message_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), nullable=True, index=True
    )
    retry_schedule_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), nullable=True, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class OmnichannelCallSession(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "klm_omnichannel_call_sessions"
    __table_args__ = (
        Index("uq_klm_call_workspace_id", "workspace_id", "id", unique=True),
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_klm_call_idempotency"),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_call_workspace_thread",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "endpoint_id"],
            ["klm_omnichannel_endpoints.workspace_id", "klm_omnichannel_endpoints.id"],
            name="fk_klm_call_workspace_endpoint",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_call_workspace_connection",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "transcript_object_id"],
            ["storage_objects.workspace_id", "storage_objects.id"],
            name="fk_klm_call_workspace_transcript_object",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "recording_object_id"],
            ["storage_objects.workspace_id", "storage_objects.id"],
            name="fk_klm_call_workspace_recording_object",
            ondelete="RESTRICT",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    thread_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    endpoint_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    connection_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    connection_key: Mapped[str] = mapped_column(String(120), nullable=False)
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="requested", server_default="requested", index=True
    )
    permission_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unknown", server_default="unknown"
    )
    provider_call_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_object_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    recording_object_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 6), nullable=True)
    cost_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    call_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)


class OmnichannelCallEvent(UUIDMixin, Base):
    """Append-only state transition evidence for one native call session."""

    __tablename__ = "klm_omnichannel_call_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_klm_call_event_workspace_id"),
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_klm_call_event_idempotency"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "call_id"],
            ["klm_omnichannel_call_sessions.workspace_id", "klm_omnichannel_call_sessions.id"],
            name="fk_klm_call_event_workspace_call",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_call_event_workspace_thread",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_call_event_workspace_connection",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_klm_call_event_call_occurred",
            "workspace_id",
            "call_id",
            "occurred_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    call_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    thread_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    connection_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    external_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    previous_state: Mapped[str] = mapped_column(String(32), nullable=False)
    next_state: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_call_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    event_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)


class OmnichannelCallCostLedgerEntry(UUIDMixin, Base):
    """Append-only provider charge evidence for calls and audio sessions."""

    __tablename__ = "klm_omnichannel_call_cost_ledger"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_klm_call_cost_idempotency"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "call_id"],
            ["klm_omnichannel_call_sessions.workspace_id", "klm_omnichannel_call_sessions.id"],
            name="fk_klm_call_cost_workspace_call",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_call_cost_workspace_thread",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "call_event_id"],
            ["klm_omnichannel_call_events.workspace_id", "klm_omnichannel_call_events.id"],
            name="fk_klm_call_cost_workspace_event",
            ondelete="RESTRICT",
        ),
        CheckConstraint("amount >= 0", name="ck_klm_call_cost_amount_nonnegative"),
        CheckConstraint("quantity > 0", name="ck_klm_call_cost_quantity_positive"),
        Index(
            "ix_klm_call_cost_call_occurred",
            "workspace_id",
            "call_id",
            "occurred_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    call_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    thread_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    call_event_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(15, 6), nullable=False, default=Decimal("1"), server_default="1"
    )
    unit: Mapped[str] = mapped_column(
        String(40), nullable=False, default="call", server_default="call"
    )
    pricing_category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_invoice_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    entry_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)


class MetaGroupCapability(UUIDMixin, TimestampMixin, Base):
    """Evidence-backed Groups API readiness for one official Meta connection."""

    __tablename__ = "klm_meta_group_capabilities"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "id", name="uq_klm_meta_group_capability_workspace_id"
        ),
        UniqueConstraint(
            "workspace_id",
            "connection_id",
            name="uq_klm_meta_group_capability_connection",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_meta_group_capability_connection",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "developer_app_state IN ('not_ready', 'ready')",
            name="ck_klm_meta_group_capability_app_state",
        ),
        CheckConstraint(
            "oba_eligibility_state IN ('unknown', 'eligible', 'ineligible')",
            name="ck_klm_meta_group_capability_oba_state",
        ),
        CheckConstraint(
            "oba_evidence_source IN "
            "('unverified', 'meta_api', 'meta_business_manager')",
            name="ck_klm_meta_group_capability_oba_source",
        ),
        CheckConstraint(
            "product_mode IN "
            "('official_api', 'cloud_api', 'business_app', 'coexistence', "
            "'multi_solution', 'unsupported')",
            name="ck_klm_meta_group_capability_product_mode",
        ),
        CheckConstraint(
            "max_participants = 8 AND max_groups_per_business_number = 10000 "
            "AND invite_only = true",
            name="ck_klm_meta_group_capability_official_limits",
        ),
        CheckConstraint(
            "oba_evidence_source = 'unverified' OR "
            "(oba_evidence_reference IS NOT NULL AND oba_evidence_recorded_at IS NOT NULL)",
            name="ck_klm_meta_group_capability_external_evidence",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), nullable=False, index=True
    )
    phone_number_id: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    waba_id: Mapped[str | None] = mapped_column(String(180), nullable=True)
    developer_app_configured: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    webhook_callback_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    developer_app_state: Mapped[str] = mapped_column(
        String(24), nullable=False, default="not_ready", server_default="not_ready"
    )
    required_webhook_fields: Mapped[list] = mapped_column(
        FlexibleJSON(), nullable=False, default=list
    )
    subscribed_webhook_fields: Mapped[list] = mapped_column(
        FlexibleJSON(), nullable=False, default=list
    )
    oba_eligibility_state: Mapped[str] = mapped_column(
        String(24), nullable=False, default="unknown", server_default="unknown"
    )
    oba_evidence_source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unverified", server_default="unverified"
    )
    oba_evidence_reference: Mapped[str | None] = mapped_column(String(500), nullable=True)
    oba_evidence_recorded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    product_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unsupported", server_default="unsupported"
    )
    max_participants: Mapped[int] = mapped_column(
        Integer, nullable=False, default=8, server_default="8"
    )
    max_groups_per_business_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10000, server_default="10000"
    )
    invite_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    evidence_metadata: Mapped[dict] = mapped_column(
        FlexibleJSON(), nullable=False, default=dict
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class MetaGroup(UUIDMixin, TimestampMixin, Base):
    """Tenant projection of a Meta group or an explicitly local internal draft."""

    __tablename__ = "klm_meta_groups"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_klm_meta_group_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "create_idempotency_key",
            name="uq_klm_meta_group_create_idempotency",
        ),
        UniqueConstraint(
            "workspace_id",
            "connection_id",
            "provider_group_id",
            name="uq_klm_meta_group_provider_id",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_meta_group_connection",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "capability_id"],
            [
                "klm_meta_group_capabilities.workspace_id",
                "klm_meta_group_capabilities.id",
            ],
            name="fk_klm_meta_group_capability",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "group_kind IN ('local_internal', 'meta_cloud_group')",
            name="ck_klm_meta_group_kind",
        ),
        CheckConstraint(
            "state IN ('local_ready', 'create_pending', 'active', 'delete_pending', "
            "'deleted', 'failed')",
            name="ck_klm_meta_group_state",
        ),
        CheckConstraint(
            "join_approval_mode IN ('auto_approve', 'approval_required')",
            name="ck_klm_meta_group_join_approval",
        ),
        CheckConstraint(
            "participant_count >= 1 AND participant_count <= 8",
            name="ck_klm_meta_group_participant_count",
        ),
        CheckConstraint(
            "length(request_payload_sha256) = 64",
            name="ck_klm_meta_group_payload_sha256",
        ),
        CheckConstraint(
            "(group_kind = 'local_internal' AND provider_group_id IS NULL) OR "
            "(group_kind = 'meta_cloud_group' AND provider_group_id IS NOT NULL)",
            name="ck_klm_meta_group_official_provider_id",
        ),
        Index(
            "ix_klm_meta_group_workspace_connection_state",
            "workspace_id",
            "connection_id",
            "state",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), nullable=False, index=True
    )
    capability_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), nullable=True, index=True
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    group_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="local_internal", server_default="local_internal"
    )
    provider_group_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    join_approval_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="auto_approve", server_default="auto_approve"
    )
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="local_ready", server_default="local_ready", index=True
    )
    invite_link: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    participant_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    create_idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    request_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    last_lifecycle_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    group_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)


class MetaGroupParticipant(UUIDMixin, TimestampMixin, Base):
    """Participant projection fed only by invite and provider lifecycle facts."""

    __tablename__ = "klm_meta_group_participants"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "id", name="uq_klm_meta_group_participant_workspace_id"
        ),
        UniqueConstraint(
            "workspace_id",
            "group_id",
            "provider_user_id",
            name="uq_klm_meta_group_participant_provider_user",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "group_id"],
            ["klm_meta_groups.workspace_id", "klm_meta_groups.id"],
            name="fk_klm_meta_group_participant_group",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "state IN ('invited', 'join_requested', 'joined', 'left', 'removed', 'rejected')",
            name="ck_klm_meta_group_participant_state",
        ),
        CheckConstraint(
            "source IN ('invite_link', 'join_request', 'provider_webhook')",
            name="ck_klm_meta_group_participant_source",
        ),
        CheckConstraint(
            "role IN ('member', 'admin')",
            name="ck_klm_meta_group_participant_role",
        ),
        Index(
            "ix_klm_meta_group_participant_group_state",
            "workspace_id",
            "group_id",
            "state",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    group_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    provider_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    wa_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    business_scoped_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    join_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="invited", server_default="invited", index=True
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    role: Mapped[str] = mapped_column(
        String(24), nullable=False, default="member", server_default="member"
    )
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    participant_metadata: Mapped[dict] = mapped_column(
        FlexibleJSON(), nullable=False, default=dict
    )


class MetaGroupOperation(UUIDMixin, TimestampMixin, Base):
    """Idempotent local intent; this domain never performs provider dispatch."""

    __tablename__ = "klm_meta_group_operations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "id", name="uq_klm_meta_group_operation_workspace_id"
        ),
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_klm_meta_group_operation_idempotency",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "group_id"],
            ["klm_meta_groups.workspace_id", "klm_meta_groups.id"],
            name="fk_klm_meta_group_operation_group",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_meta_group_operation_connection",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "operation_type IN ('create_local_group', 'prepare_official_group_creation', "
            "'prepare_group_message')",
            name="ck_klm_meta_group_operation_type",
        ),
        CheckConstraint(
            "state IN ('local_ready', 'blocked_by_capability')",
            name="ck_klm_meta_group_operation_state",
        ),
        CheckConstraint(
            "length(request_payload_sha256) = 64",
            name="ck_klm_meta_group_operation_payload_sha256",
        ),
        CheckConstraint(
            "external_dispatch_executed = false",
            name="ck_klm_meta_group_operation_no_dispatch",
        ),
        Index(
            "ix_klm_meta_group_operation_workspace_state",
            "workspace_id",
            "state",
            "created_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    group_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), nullable=True, index=True
    )
    connection_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), nullable=False, index=True
    )
    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    operation_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="local_ready", server_default="local_ready", index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    request_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_payload: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    capability_snapshot: Mapped[dict] = mapped_column(
        FlexibleJSON(), nullable=False, default=dict
    )
    external_dispatch_executed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class MetaGroupLifecycleEvent(UUIDMixin, Base):
    """Append-only local and provider lifecycle evidence for Meta groups."""

    __tablename__ = "klm_meta_group_lifecycle_events"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "id", name="uq_klm_meta_group_event_workspace_id"
        ),
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_klm_meta_group_event_idempotency",
        ),
        UniqueConstraint(
            "workspace_id",
            "connection_id",
            "external_event_id",
            name="uq_klm_meta_group_event_external",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "group_id"],
            ["klm_meta_groups.workspace_id", "klm_meta_groups.id"],
            name="fk_klm_meta_group_event_group",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "operation_id"],
            ["klm_meta_group_operations.workspace_id", "klm_meta_group_operations.id"],
            name="fk_klm_meta_group_event_operation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_meta_group_event_connection",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "source IN ('local_control', 'provider_webhook')",
            name="ck_klm_meta_group_event_source",
        ),
        CheckConstraint(
            "(source = 'local_control' AND webhook_field IS NULL) OR "
            "(source = 'provider_webhook' AND webhook_field IN "
            "('group_lifecycle_update', 'group_participants_update', "
            "'group_settings_update', 'group_status_update'))",
            name="ck_klm_meta_group_event_webhook_field",
        ),
        CheckConstraint(
            "length(payload_sha256) = 64",
            name="ck_klm_meta_group_event_payload_sha256",
        ),
        Index(
            "ix_klm_meta_group_event_group_time",
            "workspace_id",
            "group_id",
            "occurred_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    group_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    operation_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), nullable=True, index=True
    )
    connection_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), nullable=False, index=True
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    webhook_field: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    external_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    event_payload: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    state_snapshot: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


register_append_only_model(OmnichannelChannelEvent)
register_append_only_model(OmnichannelProviderWebhookEnvelope)
register_append_only_model(OmnichannelMessageEvidenceEvent)
register_append_only_model(OmnichannelRoutingEvent)
register_append_only_model(OmnichannelActionIntentEvent)
register_append_only_model(OmnichannelAutomationRunEvent)
register_append_only_model(OmnichannelIdentityMutationEvent)
register_append_only_model(OmnichannelProviderMediaReceipt)
register_append_only_model(OmnichannelProviderMediaUploadReconciliation)
register_append_only_model(OmnichannelMessageDeliveryEvent)
register_append_only_model(OmnichannelMessageCostLedgerEntry)
register_append_only_model(OmnichannelPublicIngressReceipt)
register_append_only_model(LeadHydrationEvent)
register_append_only_model(OmnichannelScheduledMessageEvent)
register_append_only_model(OmnichannelCallEvent)
register_append_only_model(OmnichannelCallCostLedgerEntry)
register_append_only_model(MetaGroupLifecycleEvent)
