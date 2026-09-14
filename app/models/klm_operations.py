"""Modelos operacionais do KLM Hub que conectam as superfícies da SPA."""

import enum
import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.append_only import register_append_only_model
from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin

LEGAL_ARTIFACT_EVIDENCE_SCHEMA_VERSION = 1


def canonical_legal_artifact_evidence(
    *,
    evidence_schema_version: int,
    workspace_id: UUID,
    matter_id: UUID,
    artifact_key: UUID,
    version: int,
    kind: str,
    title: str,
    content: dict[str, Any],
    source_snapshot: dict[str, Any],
    created_by_user_id: UUID,
) -> bytes:
    """Serialize immutable legal evidence with one stable, versioned contract."""
    payload = {
        "artifactKey": str(artifact_key),
        "content": content,
        "createdByUserId": str(created_by_user_id),
        "evidenceSchemaVersion": evidence_schema_version,
        "kind": kind,
        "matterId": str(matter_id),
        "sourceSnapshot": source_snapshot,
        "title": title,
        "version": version,
        "workspaceId": str(workspace_id),
    }
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def legal_artifact_evidence_sha256(**values: Any) -> str:
    """Return the SHA-256 digest for the canonical immutable evidence payload."""
    return hashlib.sha256(canonical_legal_artifact_evidence(**values)).hexdigest()


class AreaBlueprintStatus(str, enum.Enum):
    active = "active"
    blueprint = "blueprint"
    draft = "draft"
    archived = "archived"


class OmnichannelChannel(str, enum.Enum):
    whatsapp = "whatsapp"
    email = "email"
    form = "form"
    instagram = "instagram"
    facebook = "facebook"
    messenger = "messenger"
    tiktok = "tiktok"
    webchat = "webchat"
    audio = "audio"
    internal = "internal"


class ThreadStatus(str, enum.Enum):
    open = "open"
    waiting_documents = "waiting_documents"
    waiting_human = "waiting_human"
    human_active = "human_active"
    closed = "closed"
    blocked = "blocked"


class MessageDirection(str, enum.Enum):
    inbound = "inbound"
    outbound = "outbound"
    internal = "internal"


class MessageAuthorType(str, enum.Enum):
    client = "client"
    agent = "agent"
    human = "human"
    system = "system"


class MessageDeliveryStatus(str, enum.Enum):
    local_draft = "local_draft"
    received = "received"
    queued = "queued"
    sent = "sent"
    delivered = "delivered"
    read = "read"
    failed = "failed"
    blocked = "blocked"


class FollowUpStatus(str, enum.Enum):
    scheduled = "scheduled"
    due = "due"
    completed = "completed"
    suppressed = "suppressed"
    cancelled = "cancelled"
    failed = "failed"


class TechnicalRequestSeverity(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class TechnicalRequestStatus(str, enum.Enum):
    received = "received"
    analyzing = "analyzing"
    patch_ready = "patch_ready"
    validating = "validating"
    resolved = "resolved"
    rejected = "rejected"


class ExternalActionIntentStatus(str, enum.Enum):
    draft = "draft"
    awaiting_approval = "awaiting_approval"
    approved_local = "approved_local"
    blocked_external = "blocked_external"
    cancelled = "cancelled"


class AreaBlueprint(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "klm_area_blueprints"
    __table_args__ = (
        UniqueConstraint("workspace_id", "area_key", name="uq_klm_area_blueprint_key"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    area_key: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    status: Mapped[AreaBlueprintStatus] = mapped_column(
        Enum(AreaBlueprintStatus, name="klm_area_blueprint_status_enum"),
        nullable=False,
        default=AreaBlueprintStatus.draft,
        index=True,
    )
    version: Mapped[str] = mapped_column(String(40), nullable=False, default="v0.1.0-local")
    readiness: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    blueprint: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    validated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    validated_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class OmnichannelThread(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "klm_omnichannel_threads"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_klm_omnichannel_thread_workspace_id",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_klm_omnichannel_thread_workspace_matter",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_klm_omnichannel_thread_workspace_client",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "identity_id"],
            ["klm_omnichannel_identities.workspace_id", "klm_omnichannel_identities.id"],
            name="fk_klm_omnichannel_thread_workspace_identity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "assigned_team_id"],
            ["klm_workspace_teams.workspace_id", "klm_workspace_teams.id"],
            name="fk_klm_omnichannel_thread_workspace_team",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_klm_thread_workspace_queue_activity",
            "workspace_id",
            "queue",
            "status",
            "last_message_at",
        ),
        Index(
            "uq_klm_thread_workspace_identity_matter",
            "workspace_id",
            "identity_id",
            "matter_id",
            unique=True,
            postgresql_where=text("identity_id IS NOT NULL AND matter_id IS NOT NULL"),
            sqlite_where=text("identity_id IS NOT NULL AND matter_id IS NOT NULL"),
        ),
        Index(
            "uq_klm_thread_workspace_identity_unscoped",
            "workspace_id",
            "identity_id",
            unique=True,
            postgresql_where=text("identity_id IS NOT NULL AND matter_id IS NULL"),
            sqlite_where=text("identity_id IS NOT NULL AND matter_id IS NULL"),
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    matter_id: Mapped[Optional[UUID]] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    client_id: Mapped[Optional[UUID]] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    identity_id: Mapped[Optional[UUID]] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    assigned_human_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assigned_team_id: Mapped[Optional[UUID]] = mapped_column(
        FlexibleUUID(), nullable=True, index=True
    )
    primary_channel: Mapped[OmnichannelChannel] = mapped_column(
        Enum(OmnichannelChannel, name="klm_omnichannel_channel_enum"),
        nullable=False,
        default=OmnichannelChannel.whatsapp,
    )
    status: Mapped[ThreadStatus] = mapped_column(
        Enum(ThreadStatus, name="klm_thread_status_enum"),
        nullable=False,
        default=ThreadStatus.open,
        index=True,
    )
    subject: Mapped[Optional[str]] = mapped_column(String(220), nullable=True)
    object_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    owner_agent_key: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Harmonização multi-área (posse forte, CAS): distinta de `owner_agent_key`
    # acima, que é apenas o rótulo informativo/roteamento auto-sincronizado por
    # `MatterService._sync_legal_routing`. Estas quatro colunas são a fonte de
    # verdade de QUEM pode responder por esta conversa agora — geridas
    # exclusivamente por `app.services.conversation_ownership_service`
    # (claim/transfer/release/human_takeover), nunca escritas diretamente.
    owning_agent_key: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    owning_area_key: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    owner_since: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    owner_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    queue: Mapped[str] = mapped_column(
        String(60), nullable=False, default="new", server_default="new", index=True
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_favorite: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    preferred_channel: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unread_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_message_preview: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_inbound_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_outbound_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sla_due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    first_response_due_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_due_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_reason: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)
    policy_state: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    cost_state: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    thread_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)


class OmnichannelMessage(UUIDMixin, Base):
    __tablename__ = "klm_omnichannel_messages"
    __table_args__ = (
        Index("uq_klm_message_workspace_id", "workspace_id", "id", unique=True),
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_klm_message_idempotency"),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_omnichannel_message_workspace_thread",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_klm_omnichannel_message_workspace_matter",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "endpoint_id"],
            ["klm_omnichannel_endpoints.workspace_id", "klm_omnichannel_endpoints.id"],
            name="fk_klm_omnichannel_message_workspace_endpoint",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "binding_id"],
            ["klm_omnichannel_thread_bindings.workspace_id", "klm_omnichannel_thread_bindings.id"],
            name="fk_klm_omnichannel_message_workspace_binding",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_omnichannel_message_workspace_connection",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "content_lifecycle_state IN ('original', 'edited', 'deleted', 'provider_unsupported')",
            name="ck_klm_message_content_lifecycle_state",
        ),
        CheckConstraint(
            "evidence_event_count >= 0 AND evidence_revision_count >= 0",
            name="ck_klm_message_evidence_counts",
        ),
        CheckConstraint(
            "evidence_chain_sha256 IS NULL OR length(evidence_chain_sha256) = 64",
            name="ck_klm_message_evidence_chain_sha256",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    thread_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        nullable=False,
        index=True,
    )
    matter_id: Mapped[Optional[UUID]] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    endpoint_id: Mapped[Optional[UUID]] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    binding_id: Mapped[Optional[UUID]] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    connection_id: Mapped[Optional[UUID]] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    author_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[OmnichannelChannel] = mapped_column(
        Enum(OmnichannelChannel, name="klm_message_channel_enum"), nullable=False
    )
    direction: Mapped[MessageDirection] = mapped_column(
        Enum(MessageDirection, name="klm_message_direction_enum"), nullable=False
    )
    author_type: Mapped[MessageAuthorType] = mapped_column(
        Enum(MessageAuthorType, name="klm_message_author_type_enum"), nullable=False
    )
    author_label: Mapped[str] = mapped_column(String(180), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    audience: Mapped[str] = mapped_column(
        String(40), nullable=False, default="client_private", server_default="client_private"
    )
    content_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default="text", server_default="text"
    )
    language: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    provider_status: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    transport: Mapped[str] = mapped_column(
        String(60), nullable=False, default="local", server_default="local"
    )
    cost_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6), nullable=True)
    cost_currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    window_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    template_id: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)
    external_message_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    delivery_status: Mapped[MessageDeliveryStatus] = mapped_column(
        Enum(MessageDeliveryStatus, name="klm_message_delivery_status_enum"),
        nullable=False,
        default=MessageDeliveryStatus.local_draft,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    content_lifecycle_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="original", server_default="original", index=True
    )
    evidence_event_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    evidence_revision_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    evidence_chain_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    content_edited_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    content_deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    message_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)


class EmailIngestReceipt(UUIDMixin, TimestampMixin, Base):
    """Owns one provider e-mail event and its fully materialized local result."""

    __tablename__ = "klm_email_ingest_receipts"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider_message_id",
            name="uq_klm_email_receipt_workspace_message",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_message_id: Mapped[str] = mapped_column(String(180), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default="processing", server_default="processing"
    )
    result: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class FollowUpTask(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "klm_follow_up_tasks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_klm_follow_up_workspace_matter",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_follow_up_workspace_thread",
            ondelete="CASCADE",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    matter_id: Mapped[Optional[UUID]] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    thread_id: Mapped[Optional[UUID]] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    channel: Mapped[OmnichannelChannel] = mapped_column(
        Enum(OmnichannelChannel, name="klm_follow_up_channel_enum"), nullable=False
    )
    status: Mapped[FollowUpStatus] = mapped_column(
        Enum(FollowUpStatus, name="klm_follow_up_status_enum"),
        nullable=False,
        default=FollowUpStatus.scheduled,
        index=True,
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    policy_key: Mapped[str] = mapped_column(
        String(100), nullable=False, default="lead_never_unanswered"
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    last_notified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class AcquisitionEvent(UUIDMixin, Base):
    __tablename__ = "klm_acquisition_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "dedupe_key", name="uq_klm_acquisition_dedupe"),
        Index(
            "ix_klm_acquisition_workspace_matter_happened",
            "workspace_id",
            "matter_id",
            "happened_at",
            postgresql_where=text("matter_id IS NOT NULL"),
        ),
        Index(
            "ix_klm_acquisition_workspace_client_unbound_happened",
            "workspace_id",
            "client_id",
            "happened_at",
            postgresql_where=text("matter_id IS NULL AND client_id IS NOT NULL"),
        ),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_klm_acquisition_workspace_matter",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_klm_acquisition_workspace_client",
            ondelete="RESTRICT",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    matter_id: Mapped[Optional[UUID]] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    client_id: Mapped[Optional[UUID]] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    event_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    medium: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    campaign: Mapped[Optional[str]] = mapped_column(String(180), nullable=True, index=True)
    event_id: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(180), nullable=False)
    happened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    value: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    click_ids: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    event_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)


class TechnicalRequest(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "klm_technical_requests"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    assigned_to_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    route: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    severity: Mapped[TechnicalRequestSeverity] = mapped_column(
        Enum(TechnicalRequestSeverity, name="klm_technical_severity_enum"),
        nullable=False,
        default=TechnicalRequestSeverity.medium,
        index=True,
    )
    status: Mapped[TechnicalRequestStatus] = mapped_column(
        Enum(TechnicalRequestStatus, name="klm_technical_status_enum"),
        nullable=False,
        default=TechnicalRequestStatus.received,
        index=True,
    )
    evidence: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    resolution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class LegalArtifactVersion(UUIDMixin, Base):
    """Immutable content version for a proposal, contract or legal draft."""

    __tablename__ = "klm_legal_artifact_versions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "artifact_key",
            "version",
            name="uq_klm_legal_artifact_version",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_klm_legal_artifact_version_workspace_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            "matter_id",
            name="uq_klm_legal_artifact_version_state_parent",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_klm_legal_artifact_workspace_matter",
            ondelete="CASCADE",
        ),
        # Mesmo padrão do matter acima — legal_demand_id só pode apontar
        # para uma LegalDemand do MESMO workspace (integridade tenant no
        # schema, não só em runtime; ver
        # LegalDemand.uq_legal_demands_workspace_id).
        ForeignKeyConstraint(
            ["workspace_id", "legal_demand_id"],
            ["legal_demands.workspace_id", "legal_demands.id"],
            name="fk_klm_legal_artifact_workspace_legal_demand",
            ondelete="SET NULL",
        ),
        CheckConstraint(
            "evidence_schema_version = 1",
            name="ck_klm_legal_artifact_evidence_schema_v1",
        ),
        CheckConstraint(
            "length(evidence_sha256) = 64",
            name="ck_klm_legal_artifact_evidence_sha256_length",
        ),
        # ── Editor jurídico (Onda 6, §9 item 1) ──
        CheckConstraint(
            "author_type IN ('human','agent')",
            name="ck_klm_legal_artifact_author_type_values",
        ),
        CheckConstraint(
            "author_type <> 'agent' OR author_agent_key IS NOT NULL",
            name="ck_klm_legal_artifact_author_agent_key_pair",
        ),
        CheckConstraint(
            "(office_template_object_id IS NULL) = (office_template_object_version IS NULL)",
            name="ck_klm_legal_artifact_office_template_pin_pair",
        ),
        CheckConstraint(
            "protocolability_state IN ('bloqueado','pendente','protocolavel')",
            name="ck_klm_legal_artifact_protocolability_state_values",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    matter_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False)
    artifact_key: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    source_snapshot: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # ── Editor jurídico (Onda 6, §9 item 1) — metadado de proveniência/
    # consulta. NÃO entra no evidence_sha256 (schema v1 é fixo, ver
    # canonical_legal_artifact_evidence acima); o conteúdo estruturado por
    # seções, variáveis do caso, fontes, citações e comentários moram dentro
    # de `content` (JSON), que já é coberto pelo hash.
    # FK composta [workspace_id, legal_demand_id] declarada em __table_args__
    # (fk_klm_legal_artifact_workspace_legal_demand) — não simples aqui, para
    # nunca permitir artefato de um workspace referenciar demanda de outro.
    legal_demand_id: Mapped[Optional[UUID]] = mapped_column(
        FlexibleUUID(),
        nullable=True,
        index=True,
    )
    author_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="human", server_default="human"
    )
    author_agent_key: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    office_template_key: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    office_template_object_id: Mapped[Optional[UUID]] = mapped_column(
        FlexibleUUID(), nullable=True
    )
    office_template_object_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Estado de protocolabilidade resumido (derivado do checklist em
    # `content["checklist"]`, ver legal_editor_service.py) — nunca
    # "protocolavel" enquanto houver placeholder/pendência: a geração NUNCA
    # protocola por conta própria (fail-closed, regra global do escritório).
    protocolability_state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="bloqueado", server_default="bloqueado"
    )
    evidence_schema_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=LEGAL_ARTIFACT_EVIDENCE_SCHEMA_VERSION,
    )
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    state: Mapped["LegalArtifactState"] = relationship(
        "LegalArtifactState",
        back_populates="artifact_version",
        uselist=False,
        lazy="joined",
        cascade="all, delete-orphan",
        single_parent=True,
    )

    def evidence_values(self) -> dict[str, Any]:
        return {
            "evidence_schema_version": self.evidence_schema_version,
            "workspace_id": self.workspace_id,
            "matter_id": self.matter_id,
            "artifact_key": self.artifact_key,
            "version": self.version,
            "kind": self.kind,
            "title": self.title,
            "content": self.content,
            "source_snapshot": self.source_snapshot,
            "created_by_user_id": self.created_by_user_id,
        }

    @property
    def status(self) -> str:
        return self.state.status if self.state is not None else "draft"

    @property
    def approval_item_id(self) -> Optional[UUID]:
        return self.state.approval_item_id if self.state is not None else None

    @property
    def updated_at(self) -> datetime:
        if self.state is not None and self.state.updated_at is not None:
            return self.state.updated_at
        return self.created_at


class LegalArtifactState(UUIDMixin, TimestampMixin, Base):
    """Mutable workflow projection for one immutable legal artifact version."""

    __tablename__ = "klm_legal_artifact_states"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "artifact_version_id",
            name="uq_klm_legal_artifact_state_version",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "artifact_version_id", "matter_id"],
            [
                "klm_legal_artifact_versions.workspace_id",
                "klm_legal_artifact_versions.id",
                "klm_legal_artifact_versions.matter_id",
            ],
            name="fk_klm_legal_artifact_state_workspace_version",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["approval_item_id", "approval_matter_id"],
            ["approval_queue_items.id", "approval_queue_items.matter_id"],
            name="fk_klm_legal_artifact_state_approval_matter",
            ondelete="SET NULL",
        ),
        CheckConstraint(
            "(approval_item_id IS NULL AND approval_matter_id IS NULL) OR "
            "(approval_item_id IS NOT NULL AND approval_matter_id IS NOT NULL)",
            name="ck_klm_legal_artifact_state_approval_pair",
        ),
        CheckConstraint(
            "approval_matter_id IS NULL OR approval_matter_id = matter_id",
            name="ck_klm_legal_artifact_state_approval_matter",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    artifact_version_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    matter_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False)
    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="draft", server_default="draft", index=True
    )
    approval_item_id: Mapped[Optional[UUID]] = mapped_column(
        FlexibleUUID(), nullable=True, index=True
    )
    approval_matter_id: Mapped[Optional[UUID]] = mapped_column(FlexibleUUID(), nullable=True)
    artifact_version: Mapped[LegalArtifactVersion] = relationship(
        LegalArtifactVersion,
        back_populates="state",
        foreign_keys=[workspace_id, artifact_version_id, matter_id],
    )


@event.listens_for(LegalArtifactVersion, "before_insert")
def _set_legal_artifact_evidence(_mapper, _connection, artifact: LegalArtifactVersion) -> None:
    if artifact.evidence_schema_version is None:
        artifact.evidence_schema_version = LEGAL_ARTIFACT_EVIDENCE_SCHEMA_VERSION
    if artifact.evidence_schema_version != LEGAL_ARTIFACT_EVIDENCE_SCHEMA_VERSION:
        raise ValueError("Unsupported legal artifact evidence schema version.")
    expected = legal_artifact_evidence_sha256(**artifact.evidence_values())
    if artifact.evidence_sha256 and artifact.evidence_sha256 != expected:
        raise ValueError("Legal artifact evidence SHA-256 does not match canonical content.")
    artifact.evidence_sha256 = expected


register_append_only_model(LegalArtifactVersion)


class ProcessMonitoringRecord(UUIDMixin, TimestampMixin, Base):
    """Workspace-owned process record with local events and deadlines."""

    __tablename__ = "klm_process_monitoring_records"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "case_number_normalized",
            name="uq_klm_process_workspace_number",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_klm_process_workspace_matter",
            ondelete="CASCADE",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    matter_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    case_number: Mapped[str] = mapped_column(String(80), nullable=False)
    case_number_normalized: Mapped[str] = mapped_column(String(80), nullable=False)
    court: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)
    status: Mapped[str] = mapped_column(String(80), nullable=False, default="cadastrado")
    next_deadline_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_event_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_state: Mapped[str] = mapped_column(String(40), nullable=False, default="manual_local")
    events: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)


class KlmAuditEvent(UUIDMixin, Base):
    __tablename__ = "klm_audit_events"
    __table_args__ = (
        Index(
            "uq_klm_audit_native_approval_consumed_once",
            "workspace_id",
            "entity_type",
            "entity_id",
            unique=True,
            postgresql_where=text("event_type = 'native_capability_human_approval_consumed'"),
            sqlite_where=text("event_type = 'native_capability_human_approval_consumed'"),
        ),
        CheckConstraint(
            "event_type <> 'native_capability_human_approval_consumed' OR entity_id IS NOT NULL",
            name="ck_klm_audit_consumed_approval_entity",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_label: Mapped[str] = mapped_column(String(180), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_id: Mapped[Optional[UUID]] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    before_data: Mapped[Optional[dict]] = mapped_column(FlexibleJSON(), nullable=True)
    after_data: Mapped[Optional[dict]] = mapped_column(FlexibleJSON(), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


register_append_only_model(KlmAuditEvent)


class ExternalActionIntent(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "klm_external_action_intents"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_klm_external_intent_workspace_id",
        ),
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_klm_intent_idempotency"),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_klm_external_intent_workspace_matter",
            ondelete="RESTRICT",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requested_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    matter_id: Mapped[Optional[UUID]] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    action_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    provider: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    status: Mapped[ExternalActionIntentStatus] = mapped_column(
        Enum(ExternalActionIntentStatus, name="klm_external_intent_status_enum"),
        nullable=False,
        default=ExternalActionIntentStatus.blocked_external,
        index=True,
    )
    payload: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="Ações externas desativadas até aprovação do usuário.",
    )
