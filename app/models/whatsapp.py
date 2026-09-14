"""
Modelos: WhatsApp Integration
Persiste contatos, conversas e mensagens do WhatsApp Business.
"""
import enum
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.matter import Matter
    from app.models.user import User
    from app.models.workspace import Workspace


class ConnectionType(str, enum.Enum):
    QR_CODE = "QR_CODE"
    OFFICIAL_MANUAL = "OFFICIAL_MANUAL"
    TECH_PROVIDER = "TECH_PROVIDER"


class WaMessageDirection(str, enum.Enum):
    inbound = "inbound"    # Mensagem recebida do cliente
    outbound = "outbound"  # Mensagem enviada pelo sistema/advogado


class WaMessageStatus(str, enum.Enum):
    pending = "pending"       # Aguardando envio
    sent = "sent"             # Enviado para a Meta
    delivery_uncertain = "delivery_uncertain"  # Pode ter sido aceito; exige reconciliação
    delivered = "delivered"   # Entregue ao dispositivo
    read = "read"             # Lida pelo destinatário
    failed = "failed"         # Falha no envio


class WaConnectionStatus(str, enum.Enum):
    disconnected = "disconnected"
    pending = "pending"       # Aguardando verificação do número
    connected = "connected"
    error = "error"


class WaQueueStatus(str, enum.Enum):
    ai = "ai"
    human = "human"
    follow_up = "follow_up"
    closed = "closed"


class HumanControlState(str, enum.Enum):
    copilot = "copilot"
    manual_pause = "manual_pause"
    manual_off = "manual_off"


class WhatsAppContact(UUIDMixin, Base):
    """
    Contato do WhatsApp — representa um cliente ou pessoa externa.
    Vinculado opcionalmente a um caso (Matter).
    """
    __tablename__ = "whatsapp_contacts"
    __table_args__ = (
        # Parcial a partir de 2026: o telefone deixou de ser obrigatório. O
        # usuário que adota username tem o `wa_id` omitido do webhook quando não
        # há interação recente, e sem o índice parcial dois contatos sem
        # telefone colidiriam entre si.
        Index(
            "uq_whatsapp_contacts_workspace_phone_connection",
            "workspace_id",
            "phone",
            "whatsapp_connection_id",
            unique=True,
            postgresql_where=text("phone IS NOT NULL"),
            sqlite_where=text("phone IS NOT NULL"),
        ),
        # O BSUID é escopado por portfólio de negócio, e a conexão é o que
        # representa esse escopo aqui. O mesmo usuário tem BSUID diferente em
        # portfólios distintos: chave global seria erro de origem.
        Index(
            "uq_whatsapp_contacts_workspace_bsuid_connection",
            "workspace_id",
            "bsuid",
            "whatsapp_connection_id",
            unique=True,
            postgresql_where=text("bsuid IS NOT NULL"),
            sqlite_where=text("bsuid IS NOT NULL"),
        ),
        CheckConstraint(
            "phone IS NOT NULL OR bsuid IS NOT NULL",
            name="ck_whatsapp_contacts_tem_identificador",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    whatsapp_connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("whatsapp_connections.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # Nullable desde 2026: ver o índice parcial acima. O check constraint da
    # tabela garante que ao menos um identificador (telefone ou BSUID) exista,
    # porque contato sem nenhum dos dois não é reconhecível nem endereçável.
    phone: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, index=True)
    # `user_id` no webhook da Meta. Identificador estável dentro do portfólio:
    # permanece quando o usuário troca de username ou oculta o telefone.
    bsuid: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    # `parent_user_id`. Aparece só em cenários específicos; guardado quando vem,
    # nunca tratado como regra.
    parent_bsuid: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # Visível, opcional e editável — guardado para exibição, JAMAIS como chave:
    # o username muda e o vínculo se perderia.
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Vínculo opcional com caso
    matter_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("matters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assigned_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    queue_status: Mapped[WaQueueStatus] = mapped_column(
        SAEnum(WaQueueStatus, name="wa_queue_status_enum"),
        nullable=False,
        default=WaQueueStatus.ai,
        index=True,
    )
    human_control_state: Mapped[HumanControlState] = mapped_column(
        SAEnum(HumanControlState, name="wa_human_control_state_enum"),
        nullable=False,
        default=HumanControlState.copilot,
    )
    active_flow_template_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    active_flow_label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    next_follow_up_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Metadados
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # ── Relacionamentos ──
    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="whatsapp_contacts")
    connection: Mapped["WhatsAppConnection"] = relationship("WhatsAppConnection", back_populates="contacts")
    matter: Mapped[Optional["Matter"]] = relationship("Matter", foreign_keys=[matter_id])
    assigned_user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[assigned_user_id])
    messages: Mapped[List["WhatsAppMessage"]] = relationship(
        "WhatsAppMessage", back_populates="contact",
        order_by="WhatsAppMessage.timestamp", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<WhatsAppContact phone={self.phone} name={self.name}>"


class WhatsAppAutomationRuleDispatch(UUIDMixin, TimestampMixin, Base):
    """Janela persistente de silêncio de uma regra para um contato."""

    __tablename__ = "whatsapp_automation_rule_dispatches"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "contact_id",
            "rule_id",
            name="uq_wa_automation_dispatch_workspace_contact_rule",
        ),
        Index(
            "ix_wa_automation_dispatch_workspace_last",
            "workspace_id",
            "last_dispatched_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[UUID] = mapped_column(
        ForeignKey("whatsapp_contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[str] = mapped_column(String(120), nullable=False)
    last_dispatched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    dispatch_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class WhatsAppMessage(UUIDMixin, Base):
    """
    Mensagem do WhatsApp — entrada ou saída.
    Armazena o wamid (ID da Meta) para rastreamento de status.
    """
    __tablename__ = "whatsapp_messages"

    contact_id: Mapped[UUID] = mapped_column(
        ForeignKey("whatsapp_contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    whatsapp_connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("whatsapp_connections.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # ID da mensagem na Meta (ex: "wamid.HBgLNTUxMTk5...")
    wamid: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, unique=True, index=True)

    direction: Mapped[WaMessageDirection] = mapped_column(
        SAEnum(WaMessageDirection, name="wa_message_direction_enum"), nullable=False
    )
    status: Mapped[WaMessageStatus] = mapped_column(
        SAEnum(WaMessageStatus, name="wa_message_status_enum"),
        nullable=False, default=WaMessageStatus.pending
    )

    # Conteúdo
    body: Mapped[str] = mapped_column(Text, nullable=False)
    media_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    media_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # image, audio, document

    # Transcrições (preenchidas pelo TranscriptionService)
    transcript: Mapped[Optional[str]] = mapped_column(Text, nullable=True)        # áudio transcrito
    image_transcript: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # texto extraído de imagem
    inbound_pipeline: Mapped[dict] = mapped_column(
        FlexibleJSON(),
        nullable=False,
        default=dict,
    )
    message_metadata: Mapped[dict] = mapped_column(
        FlexibleJSON(),
        nullable=False,
        default=dict,
    )
    inbound_lease_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    inbound_lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    inbound_lease_generation: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )

    # Protocolo outbound: uma chave por intenção e checkpoint antes do provider.
    outbound_idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    outbound_payload_digest: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    outbound_request_started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivery_uncertain_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    outbound_error: Mapped[Optional[dict]] = mapped_column(
        FlexibleJSON(), nullable=True
    )

    # Quem enviou (para outbound)
    sent_by_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Timestamps
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Relacionamentos ──
    contact: Mapped["WhatsAppContact"] = relationship("WhatsAppContact", back_populates="messages")
    connection: Mapped["WhatsAppConnection"] = relationship("WhatsAppConnection", back_populates="messages")
    sent_by: Mapped[Optional["User"]] = relationship("User", foreign_keys=[sent_by_id])

    __table_args__ = (
        Index("ix_wa_messages_contact_timestamp", "contact_id", "timestamp"),
        Index("ix_whatsapp_messages_inbound_lease", "inbound_lease_expires_at"),
        Index(
            "ux_whatsapp_messages_outbound_idempotency_key",
            "outbound_idempotency_key",
            unique=True,
        ),
        Index(
            "ix_whatsapp_messages_delivery_reconciliation",
            "status",
            "delivery_uncertain_at",
        ),
    )

    def __repr__(self) -> str:
        return f"<WhatsAppMessage id={self.id} dir={self.direction} status={self.status}>"


class WhatsAppConnection(UUIDMixin, Base):
    """
    Configuração da conexão do número WhatsApp Business por workspace.
    Suporta múltiplos números e diferentes tipos de conexão.
    """
    __tablename__ = "whatsapp_connections"
    __table_args__ = (
        Index(
            "uq_whatsapp_connections_phone_number",
            "phone_number_id",
            unique=True,
        ),
        Index(
            "ix_whatsapp_connections_instance_id",
            "instance_id",
            unique=True,
        ),
        CheckConstraint(
            "transport_mode IN ('official_api', 'coexistence', 'qr_gateway')",
            name="ck_whatsapp_connection_transport_mode",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )

    connection_type: Mapped[ConnectionType] = mapped_column(
        SAEnum(ConnectionType, name="wa_connection_type_enum"),
        nullable=False,
        default=ConnectionType.OFFICIAL_MANUAL,
    )
    transport_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="official_api", server_default="official_api"
    )
    coexistence_sync_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_applicable", server_default="not_applicable"
    )
    coexistence_contacts_sync_request_id: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    coexistence_history_sync_request_id: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    coexistence_sync_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    phone_number_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    phone_number: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    display_phone_number: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    business_account_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    waba_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    access_token_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    business_token_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    instance_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    instance_token_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    registration_pin_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # BYOA (Bring Your Own App): segredo por conexão, substitui a dependência
    # do WHATSAPP_APP_SECRET global para validar a assinatura do webhook e o
    # verify token do handshake, número a número.
    app_secret_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    webhook_verify_token_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    qr_provider: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    qr_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    qr_last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[WaConnectionStatus] = mapped_column(
        SAEnum(WaConnectionStatus, name="wa_connection_status_enum"),
        nullable=False, default=WaConnectionStatus.disconnected
    )
    webhook_registered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_error_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    last_error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    connected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    disconnected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="whatsapp_connections")
    contacts: Mapped[List["WhatsAppContact"]] = relationship("WhatsAppContact", back_populates="connection")
    messages: Mapped[List["WhatsAppMessage"]] = relationship("WhatsAppMessage", back_populates="connection")

    def __repr__(self) -> str:
        identifier = self.display_phone_number or self.phone_number or self.instance_id or self.id
        return f"<WhatsAppConnection identifier={identifier} type={self.connection_type} status={self.status}>"


class WhatsAppConnectionTier(str, enum.Enum):
    """
    Tier de envio Meta por conexão, com escalada automática por qualidade
    e utilização (>=50% em 7 dias): 250 -> 1k -> 10k -> 100k -> ilimitado.
    """
    tier_250 = "tier_250"
    tier_1k = "tier_1k"
    tier_10k = "tier_10k"
    tier_100k = "tier_100k"
    unlimited = "unlimited"


class WhatsAppConnectionTiers(UUIDMixin, Base):
    """
    Estado de tier/rampa de envio por conexão WhatsApp: tier atual, contador
    rolling do dia de destinatários únicos, estágio de rampa de aquecimento
    do número novo e quality rating sincronizado via Business Management API.
    """
    __tablename__ = "whatsapp_connection_tiers"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", name="uq_whatsapp_connection_tiers_connection"
        ),
        CheckConstraint(
            "quality_rating IS NULL OR quality_rating IN ('verde', 'amarelo', 'vermelho')",
            name="ck_whatsapp_connection_tiers_quality_rating",
        ),
        CheckConstraint(
            "daily_unique_recipients >= 0",
            name="ck_whatsapp_connection_tiers_daily_unique_recipients",
        ),
        CheckConstraint(
            "ramp_up_stage >= 0",
            name="ck_whatsapp_connection_tiers_ramp_up_stage",
        ),
        Index(
            "ix_whatsapp_connection_tiers_workspace",
            "workspace_id",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("whatsapp_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tier: Mapped[WhatsAppConnectionTier] = mapped_column(
        SAEnum(WhatsAppConnectionTier, name="wa_connection_tier_enum"),
        nullable=False,
        default=WhatsAppConnectionTier.tier_250,
    )
    daily_unique_recipients: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ramp_up_stage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quality_rating: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    workspace: Mapped["Workspace"] = relationship("Workspace")
    connection: Mapped["WhatsAppConnection"] = relationship("WhatsAppConnection")

    def __repr__(self) -> str:
        return (
            f"<WhatsAppConnectionTiers connection_id={self.connection_id} "
            f"tier={self.tier} quality_rating={self.quality_rating}>"
        )
