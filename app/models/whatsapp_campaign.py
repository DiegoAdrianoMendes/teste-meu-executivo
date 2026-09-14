"""
Modelos comerciais do WhatsApp oficial.

Escopo:
- templates oficiais sincronizados da Meta
- opt-in e supressão por contato/telefone
- campanhas outbound via template oficial
- fila/jobs, destinatários e eventos de entrega
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, FlexibleUUID, UUIDMixin

if TYPE_CHECKING:
    from app.models.flow import Flow, FlowRun
    from app.models.matter import Matter
    from app.models.user import User
    from app.models.whatsapp import WhatsAppConnection, WhatsAppContact
    from app.models.workspace import Workspace


class MetaTemplateStatus(str, enum.Enum):
    approved = "approved"
    pending = "pending"
    rejected = "rejected"
    paused = "paused"
    disabled = "disabled"


class WhatsAppTemplateIntent(enum.StrEnum):
    """Fixed intents used to order the approved-template picker.

    The value is deliberately nullable on the model: existing Meta templates
    must not be classified by a speculative backfill.
    """

    follow_up = "follow_up"
    reativacao = "reativacao"
    documentos = "documentos"
    audiencia = "audiencia"
    cobranca = "cobranca"
    proposta = "proposta"


class OptInStatus(str, enum.Enum):
    opted_in = "opted_in"
    opted_out = "opted_out"


class CampaignStatus(str, enum.Enum):
    draft = "draft"
    scheduled = "scheduled"
    running = "running"
    paused = "paused"
    completed = "completed"
    stopped = "stopped"
    failed = "failed"


class CampaignRecipientStatus(str, enum.Enum):
    queued = "queued"
    scheduled = "scheduled"
    processing = "processing"
    sent = "sent"
    delivered = "delivered"
    read = "read"
    failed = "failed"
    skipped = "skipped"
    delivery_uncertain = "delivery_uncertain"


class CampaignSendJobStatus(str, enum.Enum):
    pending = "pending"
    scheduled = "scheduled"
    processing = "processing"
    sent = "sent"
    failed = "failed"
    cancelled = "cancelled"
    delivery_uncertain = "delivery_uncertain"


class WhatsAppMetaTemplate(UUIDMixin, Base):
    __tablename__ = "whatsapp_meta_templates"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "whatsapp_connection_id",
            "name",
            "language",
            name="uq_wa_meta_templates_workspace_connection_name_language",
        ),
        Index("ix_wa_meta_templates_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    whatsapp_connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("whatsapp_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_template_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    intent: Mapped[WhatsAppTemplateIntent | None] = mapped_column(
        SAEnum(WhatsAppTemplateIntent, name="wa_meta_template_intent_enum"),
        nullable=True,
    )
    language: Mapped[str] = mapped_column(String(20), nullable=False, default="pt_BR")
    status: Mapped[MetaTemplateStatus] = mapped_column(
        SAEnum(MetaTemplateStatus, name="wa_meta_template_status_enum"),
        nullable=False,
        default=MetaTemplateStatus.pending,
    )
    quality_rating: Mapped[str | None] = mapped_column(String(40), nullable=True)
    components: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    raw_payload: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    removed_at_source_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    workspace: Mapped[Workspace] = relationship("Workspace")
    connection: Mapped[WhatsAppConnection] = relationship("WhatsAppConnection")


class WhatsAppOptInRecord(UUIDMixin, Base):
    __tablename__ = "whatsapp_opt_in_records"
    __table_args__ = (
        Index(
            "uq_wa_optin_workspace_phone_channel",
            "workspace_id",
            "phone",
            "channel",
            unique=True,
            postgresql_where=text("phone IS NOT NULL"),
            sqlite_where=text("phone IS NOT NULL"),
        ),
        Index(
            "uq_wa_optin_workspace_bsuid_channel",
            "workspace_id",
            "bsuid",
            "channel",
            unique=True,
            postgresql_where=text("bsuid IS NOT NULL"),
            sqlite_where=text("bsuid IS NOT NULL"),
        ),
        CheckConstraint(
            "phone IS NOT NULL OR bsuid IS NOT NULL OR contact_id IS NOT NULL",
            name="ck_wa_optin_tem_alvo",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("whatsapp_contacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    bsuid: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(30), nullable=False, default="whatsapp")
    status: Mapped[OptInStatus] = mapped_column(
        SAEnum(OptInStatus, name="wa_opt_in_status_enum"),
        nullable=False,
        default=OptInStatus.opted_in,
    )
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    details: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    workspace: Mapped[Workspace] = relationship("Workspace")
    contact: Mapped[WhatsAppContact | None] = relationship("WhatsAppContact")


class WhatsAppSuppressionEntry(UUIDMixin, Base):
    __tablename__ = "whatsapp_suppression_entries"
    __table_args__ = (
        # P0 jurídico da mudança de 2026: com o telefone obrigatório, o cliente
        # que oculta o número pede descadastro e NÃO HÁ ONDE REGISTRAR. Isso é
        # LGPD e política da Meta ao mesmo tempo. A supressão passa a aceitar
        # telefone OU BSUID, cada um com seu índice parcial.
        Index(
            "uq_wa_suppression_workspace_phone",
            "workspace_id",
            "phone",
            unique=True,
            postgresql_where=text("phone IS NOT NULL"),
            sqlite_where=text("phone IS NOT NULL"),
        ),
        Index(
            "uq_wa_suppression_workspace_bsuid",
            "workspace_id",
            "bsuid",
            unique=True,
            postgresql_where=text("bsuid IS NOT NULL"),
            sqlite_where=text("bsuid IS NOT NULL"),
        ),
        CheckConstraint(
            "phone IS NOT NULL OR bsuid IS NOT NULL OR contact_id IS NOT NULL",
            name="ck_wa_suppression_tem_alvo",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("whatsapp_contacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    bsuid: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    reason: Mapped[str] = mapped_column(String(120), nullable=False, default="manual")
    details: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    workspace: Mapped[Workspace] = relationship("Workspace")
    contact: Mapped[WhatsAppContact | None] = relationship("WhatsAppContact")


class WhatsAppAudienceList(UUIDMixin, Base):
    __tablename__ = "whatsapp_audience_lists"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_wa_audience_lists_workspace_name"),
        UniqueConstraint("workspace_id", "id", name="uq_wa_audience_lists_workspace_id"),
        CheckConstraint("status IN ('active','archived')", name="ck_wa_audience_lists_status"),
        CheckConstraint(
            "member_count >= 0 AND valid_count >= 0 AND invalid_count >= 0 AND duplicate_count >= 0",
            name="ck_wa_audience_lists_counts",
        ),
        Index("ix_wa_audience_lists_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="manual")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    invalid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    import_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    workspace: Mapped[Workspace] = relationship("Workspace")
    created_by: Mapped[User | None] = relationship("User")
    members: Mapped[list[WhatsAppAudienceListMember]] = relationship(
        "WhatsAppAudienceListMember",
        back_populates="audience_list",
        cascade="all, delete-orphan",
        order_by="WhatsAppAudienceListMember.created_at",
    )


class WhatsAppAudienceListMember(UUIDMixin, Base):
    __tablename__ = "whatsapp_audience_list_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "audience_list_id"],
            ["whatsapp_audience_lists.workspace_id", "whatsapp_audience_lists.id"],
            name="fk_wa_audience_member_workspace_list",
            ondelete="CASCADE",
        ),
        UniqueConstraint("audience_list_id", "phone", name="uq_wa_audience_member_list_phone"),
        CheckConstraint(
            "status IN ('valid','invalid','suppressed')",
            name="ck_wa_audience_members_status",
        ),
        Index("ix_wa_audience_members_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    audience_list_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("whatsapp_contacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    matter_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("matters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    phone: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="valid")
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    member_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    audience_list: Mapped[WhatsAppAudienceList] = relationship(
        "WhatsAppAudienceList", back_populates="members"
    )
    contact: Mapped[WhatsAppContact | None] = relationship("WhatsAppContact")
    matter: Mapped[Matter | None] = relationship("Matter")


class WhatsAppCampaign(UUIDMixin, Base):
    __tablename__ = "whatsapp_campaigns"
    __table_args__ = (
        CheckConstraint(
            "flow_start_mode IN ('manual','on_delivery','on_reply')",
            name="ck_wa_campaigns_flow_start_mode",
        ),
        Index("ix_wa_campaigns_workspace_status", "workspace_id", "status"),
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_wa_campaigns_workspace_idempotency_key",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    whatsapp_connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("whatsapp_connections.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    follow_up_flow_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("flows.id", ondelete="SET NULL"), nullable=True, index=True
    )
    flow_start_mode: Mapped[str] = mapped_column(String(24), nullable=False, default="manual")
    idempotency_key: Mapped[str | None] = mapped_column(String(180), nullable=True)
    idempotency_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[CampaignStatus] = mapped_column(
        SAEnum(CampaignStatus, name="wa_campaign_status_enum"),
        nullable=False,
        default=CampaignStatus.draft,
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    launched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pause_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    paused_by_policy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    total_recipients: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    eligible_recipients: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocked_recipients: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    read_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    workspace: Mapped[Workspace] = relationship("Workspace")
    connection: Mapped[WhatsAppConnection] = relationship("WhatsAppConnection")
    created_by: Mapped[User | None] = relationship("User")
    follow_up_flow: Mapped[Flow | None] = relationship("Flow")
    audience: Mapped[WhatsAppCampaignAudience | None] = relationship(
        "WhatsAppCampaignAudience",
        back_populates="campaign",
        cascade="all, delete-orphan",
        uselist=False,
    )
    template_snapshot: Mapped[WhatsAppCampaignTemplateSnapshot | None] = relationship(
        "WhatsAppCampaignTemplateSnapshot",
        back_populates="campaign",
        cascade="all, delete-orphan",
        uselist=False,
    )
    recipients: Mapped[list[WhatsAppCampaignRecipient]] = relationship(
        "WhatsAppCampaignRecipient",
        back_populates="campaign",
        cascade="all, delete-orphan",
    )


class WhatsAppCampaignAudience(UUIDMixin, Base):
    __tablename__ = "whatsapp_campaign_audiences"
    __table_args__ = (
        UniqueConstraint("campaign_id", name="uq_wa_campaign_audiences_campaign"),
    )

    campaign_id: Mapped[UUID] = mapped_column(
        ForeignKey("whatsapp_campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filters: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    estimated_recipients: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sample_recipients: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    campaign: Mapped[WhatsAppCampaign] = relationship("WhatsAppCampaign", back_populates="audience")


class WhatsAppCampaignTemplateSnapshot(UUIDMixin, Base):
    __tablename__ = "whatsapp_campaign_template_snapshots"
    __table_args__ = (
        UniqueConstraint("campaign_id", name="uq_wa_campaign_template_snapshot_campaign"),
    )

    campaign_id: Mapped[UUID] = mapped_column(
        ForeignKey("whatsapp_campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_template_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("whatsapp_meta_templates.id", ondelete="SET NULL"), nullable=True, index=True
    )
    template_name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    language: Mapped[str] = mapped_column(String(20), nullable=False, default="pt_BR")
    status: Mapped[MetaTemplateStatus] = mapped_column(
        SAEnum(MetaTemplateStatus, name="wa_campaign_template_status_enum"),
        nullable=False,
        default=MetaTemplateStatus.pending,
    )
    header_parameters: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    body_parameters: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    button_parameters: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    components: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    raw_payload: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    campaign: Mapped[WhatsAppCampaign] = relationship(
        "WhatsAppCampaign",
        back_populates="template_snapshot",
    )
    source_template: Mapped[WhatsAppMetaTemplate | None] = relationship("WhatsAppMetaTemplate")


class WhatsAppCampaignRecipient(UUIDMixin, Base):
    __tablename__ = "whatsapp_campaign_recipients"
    __table_args__ = (
        UniqueConstraint("campaign_id", "phone", name="uq_wa_campaign_recipients_campaign_phone"),
        CheckConstraint(
            "flow_trigger_event IS NULL OR flow_trigger_event IN ('on_delivery','on_reply')",
            name="ck_wa_campaign_recipients_flow_trigger_event",
        ),
        CheckConstraint(
            "(quota_period_start IS NULL) = (quota_reserved_at IS NULL)",
            name="ck_wa_campaign_recipients_quota_reservation_pair",
        ),
        Index("ix_wa_campaign_recipients_campaign_status", "campaign_id", "status"),
        Index("ix_wa_campaign_recipients_wamid", "wamid"),
        Index(
            "ix_wa_campaign_recipients_workspace_quota_period",
            "workspace_id",
            "quota_period_start",
        ),
        Index(
            "ix_wa_campaign_recipients_workspace_template_created",
            "workspace_id",
            "template_id",
            "created_at",
        ),
        Index(
            "ix_wa_campaign_recipients_workspace_contact_template_created",
            "workspace_id",
            "contact_id",
            "template_id",
            "created_at",
        ),
    )

    campaign_id: Mapped[UUID] = mapped_column(
        ForeignKey("whatsapp_campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("whatsapp_contacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    matter_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("matters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Denormalized source template for efficient picker usage aggregation.
    # Historical recipients may legitimately keep this value null.
    template_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("whatsapp_meta_templates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    phone: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    wamid: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    flow_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("flow_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    flow_trigger_event: Mapped[str | None] = mapped_column(String(24), nullable=True)
    flow_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[CampaignRecipientStatus] = mapped_column(
        SAEnum(CampaignRecipientStatus, name="wa_campaign_recipient_status_enum"),
        nullable=False,
        default=CampaignRecipientStatus.queued,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    send_context: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    quota_period_start: Mapped[date | None] = mapped_column(Date(), nullable=True)
    quota_reserved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    campaign: Mapped[WhatsAppCampaign] = relationship(
        "WhatsAppCampaign",
        back_populates="recipients",
    )
    workspace: Mapped[Workspace] = relationship("Workspace")
    contact: Mapped[WhatsAppContact | None] = relationship("WhatsAppContact")
    matter: Mapped[Matter | None] = relationship("Matter")
    flow_run: Mapped[FlowRun | None] = relationship("FlowRun")
    send_job: Mapped[WhatsAppCampaignSendJob | None] = relationship(
        "WhatsAppCampaignSendJob",
        back_populates="recipient",
        cascade="all, delete-orphan",
        uselist=False,
    )
    delivery_events: Mapped[list[WhatsAppCampaignDeliveryEvent]] = relationship(
        "WhatsAppCampaignDeliveryEvent",
        back_populates="recipient",
        cascade="all, delete-orphan",
    )


class WhatsAppCampaignSendJob(UUIDMixin, Base):
    __tablename__ = "whatsapp_campaign_send_jobs"
    __table_args__ = (
        UniqueConstraint("recipient_id", name="uq_wa_campaign_send_jobs_recipient"),
        Index("ix_wa_campaign_send_jobs_workspace_status", "workspace_id", "status"),
    )

    campaign_id: Mapped[UUID] = mapped_column(
        ForeignKey("whatsapp_campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recipient_id: Mapped[UUID] = mapped_column(
        ForeignKey("whatsapp_campaign_recipients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    whatsapp_connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("whatsapp_connections.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[CampaignSendJobStatus] = mapped_column(
        SAEnum(CampaignSendJobStatus, name="wa_campaign_send_job_status_enum"),
        nullable=False,
        default=CampaignSendJobStatus.pending,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    campaign: Mapped[WhatsAppCampaign] = relationship("WhatsAppCampaign")
    recipient: Mapped[WhatsAppCampaignRecipient] = relationship(
        "WhatsAppCampaignRecipient",
        back_populates="send_job",
    )
    workspace: Mapped[Workspace] = relationship("Workspace")
    connection: Mapped[WhatsAppConnection] = relationship("WhatsAppConnection")


class WhatsAppCampaignDeliveryEvent(UUIDMixin, Base):
    __tablename__ = "whatsapp_campaign_delivery_events"
    __table_args__ = (
        Index("ix_wa_campaign_delivery_events_wamid", "wamid"),
        Index("ix_wa_campaign_delivery_events_campaign", "campaign_id"),
        Index(
            "uq_wa_campaign_delivery_events_provider_event_key",
            "provider_event_key",
            unique=True,
            postgresql_where=text("provider_event_key IS NOT NULL"),
            sqlite_where=text("provider_event_key IS NOT NULL"),
        ),
        CheckConstraint(
            "provider_event_key IS NULL OR length(provider_event_key) = 64",
            name="ck_wa_campaign_delivery_event_provider_key",
        ),
    )

    campaign_id: Mapped[UUID] = mapped_column(
        ForeignKey("whatsapp_campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recipient_id: Mapped[UUID] = mapped_column(
        ForeignKey("whatsapp_campaign_recipients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    wamid: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_event_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[CampaignRecipientStatus] = mapped_column(
        SAEnum(CampaignRecipientStatus, name="wa_campaign_delivery_status_enum"),
        nullable=False,
        default=CampaignRecipientStatus.sent,
    )
    payload: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    campaign: Mapped[WhatsAppCampaign] = relationship("WhatsAppCampaign")
    recipient: Mapped[WhatsAppCampaignRecipient] = relationship(
        "WhatsAppCampaignRecipient",
        back_populates="delivery_events",
    )
