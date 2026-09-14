"""add KLM Hub operational core and merge migration heads

Revision ID: 7a8b9c0d1e2f
Revises: c7b6a5d4e3f2, 4e5f6a7b8c0
Create Date: 2026-07-12 10:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.base import FlexibleJSON, FlexibleUUID


revision: str = "7a8b9c0d1e2f"
down_revision: Union[str, Sequence[str], None] = (
    "c7b6a5d4e3f2",
    "4e5f6a7b8c0",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


health_subtheme_enum = postgresql.ENUM(
    "medicamento_alto_custo",
    "tratamento",
    "home_care",
    "cirurgia",
    "exame",
    "opme",
    "terapia",
    "outro",
    name="health_subtheme_enum",
    create_type=False,
)
area_status_enum = postgresql.ENUM(
    "active",
    "blueprint",
    "draft",
    "archived",
    name="klm_area_blueprint_status_enum",
    create_type=False,
)
omnichannel_channel_enum = postgresql.ENUM(
    "whatsapp",
    "email",
    "form",
    "instagram",
    "audio",
    "internal",
    name="klm_omnichannel_channel_enum",
    create_type=False,
)
thread_status_enum = postgresql.ENUM(
    "open",
    "waiting_documents",
    "waiting_human",
    "human_active",
    "closed",
    "blocked",
    name="klm_thread_status_enum",
    create_type=False,
)
message_channel_enum = postgresql.ENUM(
    "whatsapp",
    "email",
    "form",
    "instagram",
    "audio",
    "internal",
    name="klm_message_channel_enum",
    create_type=False,
)
message_direction_enum = postgresql.ENUM(
    "inbound",
    "outbound",
    "internal",
    name="klm_message_direction_enum",
    create_type=False,
)
message_author_type_enum = postgresql.ENUM(
    "client",
    "agent",
    "human",
    "system",
    name="klm_message_author_type_enum",
    create_type=False,
)
message_delivery_status_enum = postgresql.ENUM(
    "local_draft",
    "received",
    "queued",
    "sent",
    "delivered",
    "read",
    "failed",
    "blocked",
    name="klm_message_delivery_status_enum",
    create_type=False,
)
follow_up_channel_enum = postgresql.ENUM(
    "whatsapp",
    "email",
    "form",
    "instagram",
    "audio",
    "internal",
    name="klm_follow_up_channel_enum",
    create_type=False,
)
follow_up_status_enum = postgresql.ENUM(
    "scheduled",
    "due",
    "completed",
    "suppressed",
    "cancelled",
    "failed",
    name="klm_follow_up_status_enum",
    create_type=False,
)
technical_severity_enum = postgresql.ENUM(
    "low",
    "medium",
    "high",
    "critical",
    name="klm_technical_severity_enum",
    create_type=False,
)
technical_status_enum = postgresql.ENUM(
    "received",
    "analyzing",
    "patch_ready",
    "validating",
    "resolved",
    "rejected",
    name="klm_technical_status_enum",
    create_type=False,
)
external_intent_status_enum = postgresql.ENUM(
    "draft",
    "awaiting_approval",
    "approved_local",
    "blocked_external",
    "cancelled",
    name="klm_external_intent_status_enum",
    create_type=False,
)

KLM_ENUMS = (
    health_subtheme_enum,
    area_status_enum,
    omnichannel_channel_enum,
    thread_status_enum,
    message_channel_enum,
    message_direction_enum,
    message_author_type_enum,
    message_delivery_status_enum,
    follow_up_channel_enum,
    follow_up_status_enum,
    technical_severity_enum,
    technical_status_enum,
    external_intent_status_enum,
)


def _extend_existing_enums() -> None:
    values = {
        "practice_area_enum": ("saude_suplementar",),
        "kanban_column_id_enum": (
            "novo_lead",
            "triagem_sdr",
            "documentos",
            "reuniao",
            "proposta",
            "contrato",
            "pagamento",
            "ganho",
            "perdido",
        ),
        "micro_status_enum": (
            "reuniao_agendada",
            "reuniao_confirmada",
            "reuniao_realizada",
            "no_show",
            "proposta_em_revisao",
            "contrato_enviado",
            "pagamento_confirmado",
            "perdido_com_motivo",
        ),
    }
    for enum_name, enum_values in values.items():
        for value in enum_values:
            op.execute(f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{value}'")


def upgrade() -> None:
    _extend_existing_enums()

    bind = op.get_bind()
    for enum_obj in KLM_ENUMS:
        enum_obj.create(bind, checkfirst=True)

    op.add_column(
        "matters",
        sa.Column("health_subtheme", health_subtheme_enum, nullable=True),
    )
    op.add_column("matters", sa.Column("health_object_name", sa.String(300), nullable=True))
    op.add_column("matters", sa.Column("health_insurer", sa.String(200), nullable=True))
    op.add_column("matters", sa.Column("health_denial_reason", sa.Text(), nullable=True))
    op.add_column("matters", sa.Column("medical_urgency_hours", sa.Integer(), nullable=True))
    op.add_column(
        "matters",
        sa.Column("economic_value_annual", sa.Numeric(15, 2), nullable=True),
    )
    op.add_column(
        "matters",
        sa.Column("lead_score", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_index("ix_matters_health_subtheme", "matters", ["health_subtheme"])
    op.create_index("ix_matters_health_object_name", "matters", ["health_object_name"])
    op.create_index("ix_matters_lead_score", "matters", ["lead_score"])

    op.create_table(
        "klm_area_blueprints",
        sa.Column(
            "workspace_id",
            FlexibleUUID(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("area_key", sa.String(80), nullable=False),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("status", area_status_enum, server_default="draft", nullable=False),
        sa.Column("version", sa.String(40), server_default="v0.1.0-local", nullable=False),
        sa.Column("readiness", sa.Integer(), server_default="0", nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "blueprint",
            FlexibleJSON(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "validated_by_user_id",
            FlexibleUUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), primary_key=True, nullable=False),
        sa.UniqueConstraint(
            "workspace_id", "area_key", name="uq_klm_area_blueprint_key"
        ),
    )
    op.create_index(
        "ix_klm_area_blueprints_workspace_id", "klm_area_blueprints", ["workspace_id"]
    )
    op.create_index("ix_klm_area_blueprints_status", "klm_area_blueprints", ["status"])

    op.create_table(
        "klm_omnichannel_threads",
        sa.Column(
            "workspace_id",
            FlexibleUUID(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "matter_id",
            FlexibleUUID(),
            sa.ForeignKey("matters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "client_id",
            FlexibleUUID(),
            sa.ForeignKey("clients.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "assigned_human_id",
            FlexibleUUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "primary_channel",
            omnichannel_channel_enum,
            server_default="whatsapp",
            nullable=False,
        ),
        sa.Column("status", thread_status_enum, server_default="open", nullable=False),
        sa.Column("subject", sa.String(220), nullable=True),
        sa.Column("object_name", sa.String(300), nullable=True),
        sa.Column("owner_agent_key", sa.String(100), nullable=True),
        sa.Column("score", sa.Integer(), server_default="0", nullable=False),
        sa.Column("unread_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_message_preview", sa.String(500), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "thread_metadata",
            FlexibleJSON(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), primary_key=True, nullable=False),
    )
    for column in ("workspace_id", "matter_id", "client_id", "assigned_human_id", "status"):
        op.create_index(
            f"ix_klm_omnichannel_threads_{column}",
            "klm_omnichannel_threads",
            [column],
        )

    op.create_table(
        "klm_omnichannel_messages",
        sa.Column(
            "workspace_id",
            FlexibleUUID(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "thread_id",
            FlexibleUUID(),
            sa.ForeignKey("klm_omnichannel_threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "matter_id",
            FlexibleUUID(),
            sa.ForeignKey("matters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "author_user_id",
            FlexibleUUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("channel", message_channel_enum, nullable=False),
        sa.Column("direction", message_direction_enum, nullable=False),
        sa.Column("author_type", message_author_type_enum, nullable=False),
        sa.Column("author_label", sa.String(180), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("external_message_id", sa.String(255), nullable=True),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column(
            "delivery_status",
            message_delivery_status_enum,
            server_default="local_draft",
            nullable=False,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "message_metadata",
            FlexibleJSON(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), primary_key=True, nullable=False),
        sa.UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_klm_message_idempotency"
        ),
    )
    for column in ("workspace_id", "thread_id", "matter_id", "occurred_at"):
        op.create_index(
            f"ix_klm_omnichannel_messages_{column}",
            "klm_omnichannel_messages",
            [column],
        )

    op.create_table(
        "klm_follow_up_tasks",
        sa.Column(
            "workspace_id",
            FlexibleUUID(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "matter_id",
            FlexibleUUID(),
            sa.ForeignKey("matters.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "thread_id",
            FlexibleUUID(),
            sa.ForeignKey("klm_omnichannel_threads.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("channel", follow_up_channel_enum, nullable=False),
        sa.Column(
            "status", follow_up_status_enum, server_default="scheduled", nullable=False
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="8", nullable=False),
        sa.Column(
            "policy_key",
            sa.String(100),
            server_default="lead_never_unanswered",
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "payload",
            FlexibleJSON(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), primary_key=True, nullable=False),
    )
    for column in ("workspace_id", "matter_id", "thread_id", "status", "due_at"):
        op.create_index(
            f"ix_klm_follow_up_tasks_{column}", "klm_follow_up_tasks", [column]
        )

    op.create_table(
        "klm_acquisition_events",
        sa.Column(
            "workspace_id",
            FlexibleUUID(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "matter_id",
            FlexibleUUID(),
            sa.ForeignKey("matters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "client_id",
            FlexibleUUID(),
            sa.ForeignKey("clients.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_name", sa.String(100), nullable=False),
        sa.Column("channel", sa.String(80), nullable=False),
        sa.Column("source", sa.String(120), nullable=True),
        sa.Column("medium", sa.String(120), nullable=True),
        sa.Column("campaign", sa.String(180), nullable=True),
        sa.Column("event_id", sa.String(180), nullable=True),
        sa.Column("dedupe_key", sa.String(180), nullable=False),
        sa.Column(
            "happened_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("value", sa.Numeric(15, 2), nullable=True),
        sa.Column("currency", sa.String(3), server_default="BRL", nullable=False),
        sa.Column(
            "click_ids",
            FlexibleJSON(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "event_metadata",
            FlexibleJSON(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), primary_key=True, nullable=False),
        sa.UniqueConstraint(
            "workspace_id", "dedupe_key", name="uq_klm_acquisition_dedupe"
        ),
    )
    for column in (
        "workspace_id",
        "matter_id",
        "client_id",
        "event_name",
        "channel",
        "source",
        "campaign",
        "happened_at",
    ):
        op.create_index(
            f"ix_klm_acquisition_events_{column}", "klm_acquisition_events", [column]
        )

    op.create_table(
        "klm_technical_requests",
        sa.Column(
            "workspace_id",
            FlexibleUUID(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            FlexibleUUID(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "assigned_to_user_id",
            FlexibleUUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("route", sa.String(300), nullable=True),
        sa.Column(
            "severity", technical_severity_enum, server_default="medium", nullable=False
        ),
        sa.Column(
            "status", technical_status_enum, server_default="received", nullable=False
        ),
        sa.Column(
            "evidence",
            FlexibleJSON(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), primary_key=True, nullable=False),
    )
    for column in ("workspace_id", "created_by_user_id", "severity", "status"):
        op.create_index(
            f"ix_klm_technical_requests_{column}", "klm_technical_requests", [column]
        )

    op.create_table(
        "klm_audit_events",
        sa.Column(
            "workspace_id",
            FlexibleUUID(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            FlexibleUUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actor_type", sa.String(50), nullable=False),
        sa.Column("actor_label", sa.String(180), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("entity_id", FlexibleUUID(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.String(120), nullable=True),
        sa.Column("before_data", FlexibleJSON(), nullable=True),
        sa.Column("after_data", FlexibleJSON(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), primary_key=True, nullable=False),
    )
    for column in (
        "workspace_id",
        "actor_user_id",
        "event_type",
        "entity_type",
        "entity_id",
        "correlation_id",
        "occurred_at",
    ):
        op.create_index(f"ix_klm_audit_events_{column}", "klm_audit_events", [column])

    op.create_table(
        "klm_external_action_intents",
        sa.Column(
            "workspace_id",
            FlexibleUUID(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requested_by_user_id",
            FlexibleUUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "matter_id",
            FlexibleUUID(),
            sa.ForeignKey("matters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action_type", sa.String(120), nullable=False),
        sa.Column("provider", sa.String(100), nullable=True),
        sa.Column("idempotency_key", sa.String(180), nullable=False),
        sa.Column(
            "status",
            external_intent_status_enum,
            server_default="blocked_external",
            nullable=False,
        ),
        sa.Column(
            "payload",
            FlexibleJSON(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "reason",
            sa.Text(),
            server_default="Acoes externas desativadas ate aprovacao do usuario.",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), primary_key=True, nullable=False),
        sa.UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_klm_intent_idempotency"
        ),
    )
    for column in ("workspace_id", "matter_id", "action_type", "status"):
        op.create_index(
            f"ix_klm_external_action_intents_{column}",
            "klm_external_action_intents",
            [column],
        )


def downgrade() -> None:
    tables_and_indexes = (
        (
            "klm_external_action_intents",
            ("workspace_id", "matter_id", "action_type", "status"),
        ),
        (
            "klm_audit_events",
            (
                "workspace_id",
                "actor_user_id",
                "event_type",
                "entity_type",
                "entity_id",
                "correlation_id",
                "occurred_at",
            ),
        ),
        (
            "klm_technical_requests",
            ("workspace_id", "created_by_user_id", "severity", "status"),
        ),
        (
            "klm_acquisition_events",
            (
                "workspace_id",
                "matter_id",
                "client_id",
                "event_name",
                "channel",
                "source",
                "campaign",
                "happened_at",
            ),
        ),
        (
            "klm_follow_up_tasks",
            ("workspace_id", "matter_id", "thread_id", "status", "due_at"),
        ),
        (
            "klm_omnichannel_messages",
            ("workspace_id", "thread_id", "matter_id", "occurred_at"),
        ),
        (
            "klm_omnichannel_threads",
            ("workspace_id", "matter_id", "client_id", "assigned_human_id", "status"),
        ),
        ("klm_area_blueprints", ("workspace_id", "status")),
    )
    for table_name, columns in tables_and_indexes:
        for column in columns:
            op.drop_index(f"ix_{table_name}_{column}", table_name=table_name)
        op.drop_table(table_name)

    op.drop_index("ix_matters_lead_score", table_name="matters")
    op.drop_index("ix_matters_health_object_name", table_name="matters")
    op.drop_index("ix_matters_health_subtheme", table_name="matters")
    for column in (
        "lead_score",
        "economic_value_annual",
        "medical_urgency_hours",
        "health_denial_reason",
        "health_insurer",
        "health_object_name",
        "health_subtheme",
    ):
        op.drop_column("matters", column)

    bind = op.get_bind()
    for enum_obj in reversed(KLM_ENUMS):
        enum_obj.drop(bind, checkfirst=True)

    # Values added to pre-existing PostgreSQL enums stay in place. Removing them
    # requires destructive type recreation and can invalidate historical rows.
