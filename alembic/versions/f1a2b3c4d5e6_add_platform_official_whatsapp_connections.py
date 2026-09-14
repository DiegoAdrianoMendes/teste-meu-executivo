"""add platform official whatsapp connections

Revision ID: f1a2b3c4d5e6
Revises: e1f2a3b4c5d6
Create Date: 2026-04-06 12:10:00.000000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "f1a2b3c4d5e6"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


platform_official_whatsapp_role_enum = sa.Enum(
    "notifications",
    "outbound",
    "support",
    name="platform_official_whatsapp_role_enum",
)
platform_official_wa_connection_type_enum = sa.Enum(
    "QR_CODE",
    "OFFICIAL_MANUAL",
    "TECH_PROVIDER",
    name="platform_official_wa_connection_type_enum",
)
platform_official_wa_connection_status_enum = sa.Enum(
    "disconnected",
    "pending",
    "connected",
    "error",
    name="platform_official_wa_connection_status_enum",
)


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    role_enum = platform_official_whatsapp_role_enum
    connection_type_enum = platform_official_wa_connection_type_enum
    status_enum = platform_official_wa_connection_status_enum

    platform_official_whatsapp_role_enum.create(bind, checkfirst=True)
    platform_official_wa_connection_type_enum.create(bind, checkfirst=True)
    platform_official_wa_connection_status_enum.create(bind, checkfirst=True)

    if dialect == "postgresql":
        role_enum = postgresql.ENUM(
            "notifications",
            "outbound",
            "support",
            name="platform_official_whatsapp_role_enum",
            create_type=False,
        )
        connection_type_enum = postgresql.ENUM(
            "QR_CODE",
            "OFFICIAL_MANUAL",
            "TECH_PROVIDER",
            name="platform_official_wa_connection_type_enum",
            create_type=False,
        )
        status_enum = postgresql.ENUM(
            "disconnected",
            "pending",
            "connected",
            "error",
            name="platform_official_wa_connection_status_enum",
            create_type=False,
        )

    op.create_table(
        "platform_official_whatsapp_connections",
        sa.Column("role", role_enum, nullable=False),
        sa.Column("connection_type", connection_type_enum, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("phone_number_id", sa.String(length=50), nullable=True),
        sa.Column("phone_number", sa.String(length=30), nullable=True),
        sa.Column("display_phone_number", sa.String(length=40), nullable=True),
        sa.Column("display_name", sa.String(length=200), nullable=True),
        sa.Column("business_account_id", sa.String(length=50), nullable=True),
        sa.Column("waba_id", sa.String(length=50), nullable=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("business_token_encrypted", sa.Text(), nullable=True),
        sa.Column("registration_pin_encrypted", sa.Text(), nullable=True),
        sa.Column("status", status_enum, nullable=False, server_default="disconnected"),
        sa.Column("webhook_registered", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_error_code", sa.String(length=50), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by_user_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("phone_number_id", name="uq_platform_official_whatsapp_phone_number"),
        sa.UniqueConstraint("role", name="uq_platform_official_whatsapp_role"),
    )
    op.create_index(
        "ix_platform_official_whatsapp_connections_role",
        "platform_official_whatsapp_connections",
        ["role"],
        unique=False,
    )
    op.create_index(
        "ix_platform_official_whatsapp_connections_phone_number_id",
        "platform_official_whatsapp_connections",
        ["phone_number_id"],
        unique=False,
    )
    op.create_index(
        "ix_platform_official_whatsapp_connections_status",
        "platform_official_whatsapp_connections",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_platform_official_whatsapp_connections_updated_by_user_id",
        "platform_official_whatsapp_connections",
        ["updated_by_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_platform_official_whatsapp_connections_updated_by_user_id", table_name="platform_official_whatsapp_connections")
    op.drop_index("ix_platform_official_whatsapp_connections_status", table_name="platform_official_whatsapp_connections")
    op.drop_index("ix_platform_official_whatsapp_connections_phone_number_id", table_name="platform_official_whatsapp_connections")
    op.drop_index("ix_platform_official_whatsapp_connections_role", table_name="platform_official_whatsapp_connections")
    op.drop_table("platform_official_whatsapp_connections")

    bind = op.get_bind()
    platform_official_wa_connection_status_enum.drop(bind, checkfirst=True)
    platform_official_wa_connection_type_enum.drop(bind, checkfirst=True)
    platform_official_whatsapp_role_enum.drop(bind, checkfirst=True)
